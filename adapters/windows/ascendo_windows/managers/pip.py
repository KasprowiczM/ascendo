"""PipManager - IPackageManager for pip / Python global CLIs on Windows.

Mirrors :class:`NpmManager` shape - PowerShell phase scripts under
``adapters/windows/scripts/pip/``, JSON-IPC over a temp dir.

Scope on Windows:
  * Power-user Python tooling installed globally (uv, ruff, black, mypy,
    pytest, poetry, virtualenv, pipx, httpx, isort, ...).
  * Package list is in ``adapters/windows/config/pip_global_clis.txt``.

Why this manager and not winget? Several Python CLIs ship more
frequent releases on PyPI than on winget (e.g. ``ruff``, ``uv``); a
direct ``pip install -U`` honours those upstream cadences without
waiting on a winget manifest bump.

NO sudo / UAC on Windows: pip installs go to the user site-packages
via ``--user`` (or to wherever the active Python interpreter manages
its own prefix). Windows has no equivalent of PEP 668's
externally-managed marker — pip simply writes to the user site by
default and there are no permission gates.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
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

from ._base import _BaseWindowsManager

_log = logging.getLogger(__name__)


class PipManager(_BaseWindowsManager, IPackageManager):
    """pip / Python global-CLI per-source manager (Windows).

    Args:
        scripts_dir: Path to ``adapters/windows/scripts/`` (set by adapter).
        lib_dir: Path to ``adapters/windows/lib/`` (informational only).
        pwsh_path: Optional override for the PowerShell executable.
        timeout_sec: Per-phase timeout. Default 1800 (30 min) - pip
            install can be slow when wheels need to compile.
    """

    SCRIPT_BY_PHASE: ClassVar[dict[Phase, str]] = {
        Phase.CHECK: "pip/check.ps1",
        Phase.PLAN: "pip/plan.ps1",
        Phase.APPLY: "pip/apply.ps1",
        Phase.VERIFY: "pip/verify.ps1",
        Phase.CLEANUP: "pip/cleanup.ps1",
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

    # -- Identity --------------------------------------------------------

    @property
    def category(self) -> SourceType:
        return SourceType.PIP

    @property
    def display_name(self) -> str:
        return "Python global packages (pip + pipx)"

    # -- Availability ----------------------------------------------------

    def is_available(self, host: HostInfo) -> bool:
        """True if Windows + a pip-capable Python is reachable.

        Resolution order:
          1. ``pip3`` on PATH
          2. ``pip`` on PATH
          3. ``py -m pip`` (Python launcher; ships with python.org installers)

        Per the IPackageManager contract, ``is_available`` must complete
        in < 100 ms — we only do PATH probes here, no subprocess spawn.
        """
        if host.os is not OperatingSystem.WINDOWS:
            return False
        if shutil.which("pip3") is not None:
            return True
        if shutil.which("pip") is not None:
            return True
        if shutil.which("py") is not None:
            # py launcher present; assume it has a Python with pip.
            # Verification of `py -m pip` would require a subprocess
            # (against the < 100 ms contract), so accept the launcher's
            # presence as sufficient.
            return True
        return False

    # -- Phase execution -------------------------------------------------

    def run_phase(
        self,
        phase: Phase,
        run: RunInfo,
        host: HostInfo,
        *,
        item_filter: Iterable[str] | None = None,
    ) -> Sidecar:
        """Spawn the PowerShell script for ``phase`` and parse its sidecar."""
        script_rel = self.SCRIPT_BY_PHASE.get(phase)
        if script_rel is None:
            raise ManagerError(
                f"PipManager does not support phase {phase.value!r}; "
                f"supported phases: "
                f"{sorted(p.value for p in self.SCRIPT_BY_PHASE)}"
            )
        script_path = self._scripts_dir / script_rel
        pwsh = self._resolve_pwsh()

        with tempfile.TemporaryDirectory(prefix="ascendo-pip-") as tmp_str:
            output_dir = Path(tmp_str)

            argv = self._build_argv(
                pwsh=pwsh,
                script_path=script_path,
                run=run,
                output_dir=output_dir,
                item_filter=item_filter,
            )

            _log.debug(
                "PipManager.run_phase: phase=%s run_id=%s argv=%r",
                phase.value,
                run.id,
                argv,
            )

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
                raise ManagerError(
                    f"pip {phase.value} script timed out after "
                    f"{self._timeout_sec}s: {script_path}"
                ) from exc
            except OSError as exc:
                raise ManagerError(
                    f"failed to spawn pwsh for pip {phase.value}: {exc}"
                ) from exc

            sidecar_path = (
                output_dir
                / str(run.id)
                / f"{phase.value}__{self.category.value}.json"
            )

            if not sidecar_path.exists():
                raise ManagerError(
                    self._format_missing_sidecar_error(
                        phase=phase,
                        script_path=script_path,
                        sidecar_path=sidecar_path,
                        completed=completed,
                    )
                )

            try:
                sidecar = read_sidecar(sidecar_path)
            except (SidecarReadError, SidecarIOError) as exc:
                raise ManagerError(
                    f"pip {phase.value} script wrote unparseable sidecar "
                    f"at {sidecar_path}: {exc}"
                ) from exc

            if completed.returncode != 0:
                _log.warning(
                    "pip %s script exited %d but produced a valid sidecar; "
                    "trusting the sidecar (status=%s)",
                    phase.value,
                    completed.returncode,
                    sidecar.status.value,
                )

            return sidecar

    # -- Internals -------------------------------------------------------

    def _run_streaming(
        self,
        argv: list[str],
        *,
        log_path: Path,
        timeout: float,
    ) -> subprocess.CompletedProcess[str]:
        """Tee stdout/stderr to ``log_path`` line-by-line."""
        import time as _time

        proc = subprocess.Popen(  # noqa: S603 (argv list, not shell)
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            **no_window_kwargs(),
        )

        captured: list[str] = []
        started = _time.monotonic()
        try:
            with log_path.open("a", encoding="utf-8") as fh:
                for raw_line in iter(proc.stdout.readline, ""):
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
                        pass
        finally:
            proc.stdout.close()
        try:
            return_code = proc.wait(
                timeout=max(1.0, timeout - (_time.monotonic() - started))
            )
        except subprocess.TimeoutExpired:
            proc.kill()
            raise

        return subprocess.CompletedProcess(
            args=argv,
            returncode=return_code,
            stdout="".join(captured),
            stderr="",
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

        PowerShell parameter conventions (identical to WingetManager):

        * Boolean parameters use ``[switch]`` on the script side. The
          caller includes ``-DryRun`` (no value) to enable, omits to
          disable. ``[bool]`` was tried and rejected — string args from
          ``-File`` invocations don't bind to ``[bool]`` reliably.
        * ``-ItemFilter`` (when present) is a single comma-joined string
          rather than a PowerShell array literal. Empty / whitespace-only
          IDs are dropped before joining; the script side splits on ``,``.
          Python package names per PEP 503 cannot contain commas.
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
            "-ProfileName",
            run.profile,
            "-OutputDir",
            str(output_dir),
        ]
        if run.dry_run:
            argv.append("-DryRun")
        if item_filter is not None:
            cleaned = [
                s.strip()
                for s in item_filter
                if s and isinstance(s, str) and s.strip()
            ]
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

        def _tail(s: str | None, limit: int = 800) -> str:
            if not s:
                return "<empty>"
            if len(s) <= limit:
                return s
            return f"...<truncated {len(s) - limit} chars>...{s[-limit:]}"

        return (
            f"pip {phase.value} script produced no sidecar.\n"
            f"  script:        {script_path}\n"
            f"  expected at:   {sidecar_path}\n"
            f"  exit code:     {completed.returncode}\n"
            f"  stderr (tail): {_tail(completed.stderr)}\n"
            f"  stdout (tail): {_tail(completed.stdout)}"
        )

    def _resolve_pwsh(self) -> str:
        """Find ``pwsh.exe`` (preferred) or ``powershell.exe``. Cache result."""
        if self._pwsh_resolved is not None:
            return self._pwsh_resolved

        if self._pwsh_override is not None:
            self._pwsh_resolved = self._pwsh_override
            return self._pwsh_resolved

        for candidate in ("pwsh.exe", "pwsh", "powershell.exe", "powershell"):
            found = shutil.which(candidate)
            if found is not None:
                self._pwsh_resolved = found
                return found

        raise ManagerError(
            "no PowerShell binary on PATH (looked for pwsh.exe, pwsh, "
            "powershell.exe, powershell). Install PowerShell 7+ or pass "
            "pwsh_path= explicitly."
        )
