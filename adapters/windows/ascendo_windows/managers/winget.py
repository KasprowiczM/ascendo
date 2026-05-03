"""WingetManager - IPackageManager for the winget package source.

Spawns the PowerShell scripts in ``../../scripts/`` which use the lib modules
in ``../../lib/`` to emit JSON v1 sidecars. This module is a thin orchestrator
that:

1. Resolves the right script path for the requested phase.
2. Spawns ``pwsh.exe`` (preferred) or ``powershell.exe`` (fallback).
3. Reads the emitted sidecar via :mod:`ascendo.orchestrator.sidecar_io`.
4. Returns the parsed :class:`Sidecar`.

The actual winget logic lives in PowerShell where its quirks were tuned
(see ``adapters/windows/lib/Winget-Parser.ps1`` and friends).

The Python <-> PowerShell IPC contract is:

* Python invokes ``pwsh -File <script.ps1> -RunId ... -Trigger ... -Profile ...
  -DryRun ... -OutputDir ... [-ItemFilter id1,id2,...]``.
* The PowerShell script writes its sidecar to
  ``<OutputDir>/<RunId>/<phase>__winget.json`` and exits.
* Python re-reads the sidecar via :func:`read_sidecar`.

If the script crashes before emitting a sidecar, :class:`ManagerError` is
raised. Per-item failures are NOT manager errors - they are reported back
in the returned sidecar with ``status=failed`` items.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterable
from pathlib import Path
from typing import ClassVar

from ascendo.interfaces.package_manager import IPackageManager, ManagerError
from ascendo.models.host import HostInfo, OperatingSystem
from ascendo.models.package import SourceType
from ascendo.models.run import Phase, RunInfo
from ascendo.models.sidecar import Sidecar
from ascendo.orchestrator.sidecar_io import (
    SidecarIOError,
    SidecarReadError,
    read_sidecar,
)
from ascendo.utils.proc import no_window_kwargs

_log = logging.getLogger(__name__)


class WingetManager(IPackageManager):
    """winget per-source manager.

    Args:
        scripts_dir: Path to ``adapters/windows/scripts/`` (set by adapter).
        lib_dir: Path to ``adapters/windows/lib/`` (set by adapter). Currently
            informational only - the PowerShell scripts dot-source from there
            relative to themselves; we keep the reference for future health
            checks and for log breadcrumbs.
        pwsh_path: Optional override for the PowerShell executable. If
            ``None``, resolves at run time (``pwsh.exe`` then
            ``powershell.exe``).
        timeout_sec: Per-phase timeout. Default ``1800`` (30 min).
    """

    # ── PowerShell script lookup ─────────────────────────────────────
    # The script lives under ``scripts_dir / winget / <phase>.ps1`` to
    # match the layout documented in ``adapters/windows/README.md``.
    SCRIPT_BY_PHASE: ClassVar[dict[Phase, str]] = {
        Phase.CHECK: "winget/check.ps1",
        Phase.PLAN: "winget/plan.ps1",
        Phase.APPLY: "winget/apply.ps1",
        Phase.VERIFY: "winget/verify.ps1",
        Phase.CLEANUP: "winget/cleanup.ps1",
    }

    DEFAULT_TIMEOUT_SEC: ClassVar[int] = 1800

    def __init__(
        self,
        *,
        scripts_dir: Path,
        lib_dir: Path,
        pwsh_path: str | None = None,
        timeout_sec: int = DEFAULT_TIMEOUT_SEC,
    ) -> None:
        self._scripts_dir: Path = Path(scripts_dir)
        self._lib_dir: Path = Path(lib_dir)
        self._pwsh_override: str | None = pwsh_path
        self._pwsh_resolved: str | None = None
        self._timeout_sec: int = timeout_sec

    # ── Identity ─────────────────────────────────────────────────────

    @property
    def category(self) -> SourceType:
        return SourceType.WINGET

    @property
    def display_name(self) -> str:
        return "Windows Package Manager (winget)"

    # ── Availability ─────────────────────────────────────────────────

    def is_available(self, host: HostInfo) -> bool:
        """True if Windows + a winget binary is on PATH.

        Note: we only consult ``host.os`` and ``shutil.which``. We do NOT
        spawn PowerShell here - per the IPackageManager contract,
        ``is_available`` must complete in < 100 ms.
        """
        if host.os is not OperatingSystem.WINDOWS:
            return False
        # ``shutil.which`` will pick up either form on Windows. On a
        # non-Windows dev host (where we can still construct a manager
        # for testing), this returns None and we report unavailable.
        if shutil.which("winget") is not None:
            return True
        return shutil.which("winget.exe") is not None

    # ── Phase execution ──────────────────────────────────────────────

    def run_phase(
        self,
        phase: Phase,
        run: RunInfo,
        host: HostInfo,
        *,
        item_filter: Iterable[str] | None = None,
    ) -> Sidecar:
        """Spawn the PowerShell script for ``phase`` and parse its sidecar.

        See module docstring for the full IPC contract.

        Args:
            phase: The phase to run. Must be a key in :attr:`SCRIPT_BY_PHASE`
                or :class:`ManagerError` is raised.
            run: Run-level metadata.
            host: Host info (used only for downstream context; the actual
                availability check should already have run).
            item_filter: Optional iterable of package IDs. When set, the
                script restricts its operation to these IDs.

        Returns:
            The parsed :class:`Sidecar` emitted by the script. Per-item
            failures live in ``sidecar.items[]`` - they are NOT raised.

        Raises:
            ManagerError: pwsh unavailable, script unsupported for this
                phase, script crashed before emitting a sidecar, sidecar
                file missing on success exit, or sidecar parse failure.
        """
        script_rel = self.SCRIPT_BY_PHASE.get(phase)
        if script_rel is None:
            msg = (
                f"WingetManager does not yet support phase {phase.value!r}; "
                f"supported phases: "
                f"{sorted(p.value for p in self.SCRIPT_BY_PHASE)}"
            )
            raise ManagerError(msg)

        script_path = self._scripts_dir / script_rel
        pwsh = self._resolve_pwsh()

        # Use an isolated tempdir as ``OutputDir``. M3.5+ will let the
        # orchestrator inject its own runs root so sidecars land alongside
        # the rest of the run; for the M3 MVP each manager owns its own
        # short-lived directory and the orchestrator copies / re-reads as
        # needed.
        with tempfile.TemporaryDirectory(prefix="ascendo-winget-") as tmp_str:
            output_dir = Path(tmp_str)

            argv = self._build_argv(
                pwsh=pwsh,
                script_path=script_path,
                run=run,
                output_dir=output_dir,
                item_filter=item_filter,
            )

            _log.debug(
                "WingetManager.run_phase: phase=%s run_id=%s argv=%r",
                phase.value,
                run.id,
                argv,
            )

            # Live-stream stdout/stderr to a sibling .log file so the
            # SSE endpoint can tail it and surface real-time progress
            # (download bars, "Successfully installed" lines, etc) in
            # the SPA's raw event log. Without this, the user sees
            # nothing until the whole phase script finishes.
            log_path = (
                output_dir
                / str(run.id)
                / f"{phase.value}__{self.category.value}.log"
            )
            log_path.parent.mkdir(parents=True, exist_ok=True)

            try:
                completed = self._run_streaming(
                    argv,
                    log_path=log_path,
                    timeout=self._timeout_sec,
                )
            except subprocess.TimeoutExpired as exc:
                msg = (
                    f"winget {phase.value} script timed out after "
                    f"{self._timeout_sec}s: {script_path}"
                )
                raise ManagerError(msg) from exc
            except OSError as exc:
                # FileNotFoundError if pwsh disappeared between resolve and
                # spawn, PermissionError on hardened hosts, etc.
                msg = (
                    f"failed to spawn pwsh for winget {phase.value}: "
                    f"{exc}"
                )
                raise ManagerError(msg) from exc

            sidecar_path = (
                output_dir
                / str(run.id)
                / f"{phase.value}__{self.category.value}.json"
            )

            if not sidecar_path.exists():
                # No sidecar produced - this is always a manager error,
                # whether the script exited 0 or non-zero. A clean script
                # MUST emit one on every code path.
                msg = self._format_missing_sidecar_error(
                    phase=phase,
                    script_path=script_path,
                    sidecar_path=sidecar_path,
                    completed=completed,
                )
                raise ManagerError(msg)

            try:
                sidecar = read_sidecar(sidecar_path)
            except (SidecarReadError, SidecarIOError) as exc:
                msg = (
                    f"winget {phase.value} script wrote unparseable sidecar "
                    f"at {sidecar_path}: {exc}"
                )
                raise ManagerError(msg) from exc

            if completed.returncode != 0:
                _log.warning(
                    "winget %s script exited %d but produced a valid "
                    "sidecar; trusting the sidecar (status=%s)",
                    phase.value,
                    completed.returncode,
                    sidecar.status.value,
                )

            return sidecar

    # ── Internals ────────────────────────────────────────────────────

    def _run_streaming(
        self,
        argv: list[str],
        *,
        log_path: Path,
        timeout: float,
    ) -> subprocess.CompletedProcess:
        """Spawn the script with Popen + tee stdout/stderr to ``log_path``
        line-by-line, returning a faux ``CompletedProcess`` with the same
        shape ``run_phase`` expects.

        Why streaming rather than ``subprocess.run``: winget's ``upgrade``
        emits real-time progress (download bars, "Successfully installed"
        lines, exit messages) on stdout. With ``run`` everything buffers
        until the script exits and the user stares at a frozen progress
        bar. With streaming, every line lands in ``<run-id>/<phase>__<source>.log``
        as it happens, the SSE endpoint tails that file, and the SPA's
        raw event log fills up live.
        """
        import time as _time

        # Open the log file in line-buffered text mode. We append (not
        # truncate) so two phases that share a log path don't lose the
        # earlier one — though by convention each (phase, source) gets
        # its own file.
        proc = subprocess.Popen(  # noqa: S603 (argv list, not shell)
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,  # merge stderr → stdout for one tail
            text=True,
            bufsize=1,  # line-buffered
            **no_window_kwargs(),
        )

        captured: list[str] = []
        started = _time.monotonic()
        try:
            with log_path.open("a", encoding="utf-8") as fh:
                # Iterating proc.stdout yields decoded lines because text=True.
                for raw_line in iter(proc.stdout.readline, ""):
                    # Per-line timeout safety: if we're past the budget,
                    # kill the process. Doing this between lines avoids
                    # the OS-level timeout machinery (which would buffer
                    # output until the timeout fires).
                    if _time.monotonic() - started > timeout:
                        proc.kill()
                        raise subprocess.TimeoutExpired(argv, timeout)
                    if not raw_line:
                        break
                    captured.append(raw_line)
                    try:
                        fh.write(raw_line)
                        fh.flush()
                    except OSError:
                        # Best-effort log: don't kill the run if disk is
                        # transiently full / permission-denied.
                        pass
        finally:
            proc.stdout.close()
        # Wait for the process to fully exit (it may still be flushing).
        try:
            return_code = proc.wait(timeout=max(1.0, timeout - (_time.monotonic() - started)))
        except subprocess.TimeoutExpired:
            proc.kill()
            raise

        return subprocess.CompletedProcess(
            args=argv,
            returncode=return_code,
            stdout="".join(captured),
            stderr="",  # merged into stdout above
        )

    def _build_argv(
        self,
        *,
        pwsh: str,
        script_path: Path,
        run: RunInfo,
        output_dir: Path,
        item_filter: Iterable[str] | None,
    ) -> list[str]:
        """Build the pwsh argv list for the script invocation.

        PowerShell parameter conventions:

        * Boolean parameters are encoded by **presence/absence**: the
          script declares ``[switch] $DryRun`` and we include
          ``-DryRun`` in argv only when ``run.dry_run`` is True. We
          tried passing ``"True"``/``"False"``/``"1"``/``"0"`` to a
          ``[bool]`` parameter — all rejected by PowerShell's
          ``[System.Convert]::ToBoolean`` fallback for ``-File`` mode
          when the value comes through as System.String (which happens
          for every Win32 subprocess argv). ``[switch]`` sidesteps the
          conversion entirely.
        * String parameters are passed as separate argv tokens; pwsh does
          not need them quoted - subprocess hands the kernel the argv list
          as-is and CreateProcess composes the command line via
          ``CommandLineToArgvW`` rules.
        * ``-ItemFilter`` (when present) is a single comma-joined string
          rather than a PowerShell array literal. Empty / whitespace-only
          IDs are dropped before joining; the script side splits on ``,``.
          Comma is forbidden inside a winget package ID, so this is safe.
        """
        argv: list[str] = [
            pwsh,
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script_path),
            "-RunId",
            str(run.id),
            "-Trigger",
            run.trigger.value,
            "-Profile",
            run.profile,
            "-OutputDir",
            str(output_dir),
        ]
        # ``-DryRun`` is a [switch] parameter on the script side (see
        # adapters/windows/scripts/winget/check.ps1). PowerShell's [bool]
        # parameter binder rejects every string value we tried passing
        # through -File mode ("$true", "$false", "1", "0", "True", "False")
        # because [System.Convert]::ToBoolean(string) is the only fallback
        # used and it has surprising semantics from Win32 subprocess argv.
        # The canonical PowerShell pattern: include the switch token to
        # enable, omit it to disable.
        if run.dry_run:
            argv.append("-DryRun")
        if item_filter is not None:
            cleaned = [s.strip() for s in item_filter if s and s.strip()]
            if cleaned:
                argv.extend(["-ItemFilter", ",".join(cleaned)])
        return argv

    def _format_missing_sidecar_error(
        self,
        *,
        phase: Phase,
        script_path: Path,
        sidecar_path: Path,
        completed: subprocess.CompletedProcess[str],
    ) -> str:
        """Compose a debugging-friendly error for the missing-sidecar case."""
        # Cap captured stderr / stdout to avoid exploding logs when a
        # script chunders thousands of lines before crashing.
        def _tail(s: str | None, limit: int = 800) -> str:
            if not s:
                return "<empty>"
            if len(s) <= limit:
                return s
            return f"...<truncated {len(s) - limit} chars>...{s[-limit:]}"

        return (
            f"winget {phase.value} script produced no sidecar.\n"
            f"  script:        {script_path}\n"
            f"  expected at:   {sidecar_path}\n"
            f"  exit code:     {completed.returncode}\n"
            f"  stderr (tail): {_tail(completed.stderr)}\n"
            f"  stdout (tail): {_tail(completed.stdout)}"
        )

    def _resolve_pwsh(self) -> str:
        """Find ``pwsh.exe`` (preferred) or ``powershell.exe``. Cache result.

        On Linux / WSL where a real ``pwsh`` is on PATH, we will happily
        return that path - useful for cross-OS unit testing. The actual
        runtime expectation is Windows; the IPackageManager contract says
        ``is_available`` must reject non-Windows hosts before we get here.
        """
        if self._pwsh_resolved is not None:
            return self._pwsh_resolved

        if self._pwsh_override is not None:
            self._pwsh_resolved = self._pwsh_override
            return self._pwsh_resolved

        # ``pwsh`` (PowerShell 7+) preferred; ``pwsh.exe`` is the Windows
        # form, ``pwsh`` the cross-platform form. shutil.which handles
        # both transparently on Windows.
        for candidate in ("pwsh.exe", "pwsh", "powershell.exe", "powershell"):
            found = shutil.which(candidate)
            if found is not None:
                self._pwsh_resolved = found
                return found

        msg = (
            "no PowerShell binary on PATH (looked for pwsh.exe, pwsh, "
            "powershell.exe, powershell). Install PowerShell 7+ or pass "
            "pwsh_path= explicitly."
        )
        raise ManagerError(msg)
