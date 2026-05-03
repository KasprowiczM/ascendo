"""WindowsUpdateManager - IPackageManager for the Windows Update source.

Mirrors :class:`WingetManager` but drives PSWindowsUpdate instead of winget.
Spawns the PowerShell scripts in ``adapters/windows/scripts/windows_update/``
which use ``adapters/windows/lib/AscendoPSWindowsUpdate.psm1`` to enumerate
and install pending Windows OS updates (KBs).

The Python <-> PowerShell IPC contract is identical to WingetManager:

* Python invokes ``pwsh -File <script.ps1> -RunId ... -Trigger ... -Profile ...
  [-DryRun] -OutputDir ... [-ItemFilter id1,id2,...]``.
* The PowerShell script writes its sidecar to
  ``<OutputDir>/<RunId>/<phase>__windows_update.json`` and exits.
* Python re-reads the sidecar via :func:`read_sidecar`.

Availability check: in addition to the host being Windows, we shell out to
``pwsh`` once to ask whether the PSWindowsUpdate module is installed
(``[bool](Get-Module -ListAvailable PSWindowsUpdate)``). The check is
cached on the instance.
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


class WindowsUpdateManager(IPackageManager):
    """Windows Update per-source manager (PSWindowsUpdate).

    Args:
        scripts_dir: Path to ``adapters/windows/scripts/`` (set by adapter).
        lib_dir: Path to ``adapters/windows/lib/`` (informational; the
            PowerShell scripts dot-source from there relative to themselves).
        pwsh_path: Optional override for the PowerShell executable. If
            ``None``, resolves at run time (``pwsh.exe`` then
            ``powershell.exe``).
        timeout_sec: Per-phase timeout. Default ``3600`` (60 min): Windows
            Update installs can be slower than typical winget upgrades on
            large feature updates.
    """

    SCRIPT_BY_PHASE: ClassVar[dict[Phase, str]] = {
        Phase.CHECK: "windows_update/check.ps1",
        Phase.PLAN: "windows_update/plan.ps1",
        Phase.APPLY: "windows_update/apply.ps1",
        Phase.VERIFY: "windows_update/verify.ps1",
        Phase.CLEANUP: "windows_update/cleanup.ps1",
    }

    DEFAULT_TIMEOUT_SEC: ClassVar[int] = 3600

    # PowerShell one-liner used by is_available() to test PSWindowsUpdate
    # presence. Emits "True\n" or "False\n" to stdout.
    _PSWU_PROBE_CMD: ClassVar[str] = (
        "[bool](Get-Module -ListAvailable -Name PSWindowsUpdate)"
    )
    _PSWU_PROBE_TIMEOUT_SEC: ClassVar[int] = 10

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
        # Cache the PSWindowsUpdate availability probe result so the
        # subprocess only spawns once per manager instance.
        self._pswu_available: bool | None = None

    # ── Identity ─────────────────────────────────────────────────────

    @property
    def category(self) -> SourceType:
        return SourceType.WINDOWS_UPDATE

    @property
    def display_name(self) -> str:
        return "Windows Update (PSWindowsUpdate)"

    # ── Availability ─────────────────────────────────────────────────

    def is_available(self, host: HostInfo) -> bool:
        """True if Windows + PSWindowsUpdate module is installed.

        Note: this implementation breaks the strict <100ms guideline of
        IPackageManager.is_available because we shell out to pwsh once to
        probe the module list. The result is cached per-instance, so
        subsequent calls are free. We accept the one-time cost because there
        is no ``shutil.which``-equivalent for PowerShell modules.
        """
        if host.os is not OperatingSystem.WINDOWS:
            return False

        if self._pswu_available is not None:
            return self._pswu_available

        try:
            pwsh = self._resolve_pwsh()
        except ManagerError:
            self._pswu_available = False
            return False

        try:
            res = subprocess.run(  # noqa: S603 (argv list, not shell)
                [pwsh, "-NoProfile", "-NonInteractive", "-Command", self._PSWU_PROBE_CMD],
                capture_output=True,
                text=True,
                timeout=self._PSWU_PROBE_TIMEOUT_SEC,
                check=False,
                **no_window_kwargs(),
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            _log.debug("WindowsUpdateManager: PSWindowsUpdate probe failed: %s", exc)
            self._pswu_available = False
            return False

        if res.returncode != 0:
            self._pswu_available = False
            return False

        out = (res.stdout or "").strip().lower()
        self._pswu_available = out == "true"
        return self._pswu_available

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

        Mirrors :meth:`WingetManager.run_phase` exactly; see that method's
        docstring for the full IPC contract.
        """
        script_rel = self.SCRIPT_BY_PHASE.get(phase)
        if script_rel is None:
            msg = (
                f"WindowsUpdateManager does not yet support phase {phase.value!r}; "
                f"supported phases: "
                f"{sorted(p.value for p in self.SCRIPT_BY_PHASE)}"
            )
            raise ManagerError(msg)

        script_path = self._scripts_dir / script_rel
        pwsh = self._resolve_pwsh()

        with tempfile.TemporaryDirectory(prefix="ascendo-wu-") as tmp_str:
            output_dir = Path(tmp_str)

            argv = self._build_argv(
                pwsh=pwsh,
                script_path=script_path,
                run=run,
                output_dir=output_dir,
                item_filter=item_filter,
            )

            _log.debug(
                "WindowsUpdateManager.run_phase: phase=%s run_id=%s argv=%r",
                phase.value,
                run.id,
                argv,
            )

            try:
                completed = subprocess.run(  # noqa: S603 (argv list, not shell)
                    argv,
                    capture_output=True,
                    text=True,
                    timeout=self._timeout_sec,
                    check=False,
                    **no_window_kwargs(),
                )
            except subprocess.TimeoutExpired as exc:
                msg = (
                    f"windows_update {phase.value} script timed out after "
                    f"{self._timeout_sec}s: {script_path}"
                )
                raise ManagerError(msg) from exc
            except OSError as exc:
                msg = (
                    f"failed to spawn pwsh for windows_update {phase.value}: "
                    f"{exc}"
                )
                raise ManagerError(msg) from exc

            sidecar_path = (
                output_dir
                / str(run.id)
                / f"{phase.value}__{self.category.value}.json"
            )

            if not sidecar_path.exists():
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
                    f"windows_update {phase.value} script wrote unparseable "
                    f"sidecar at {sidecar_path}: {exc}"
                )
                raise ManagerError(msg) from exc

            if completed.returncode != 0:
                _log.warning(
                    "windows_update %s script exited %d but produced a valid "
                    "sidecar; trusting the sidecar (status=%s)",
                    phase.value,
                    completed.returncode,
                    sidecar.status.value,
                )

            return sidecar

    # ── Internals ────────────────────────────────────────────────────

    def _build_argv(
        self,
        *,
        pwsh: str,
        script_path: Path,
        run: RunInfo,
        output_dir: Path,
        item_filter: Iterable[str] | None,
    ) -> list[str]:
        """Build the pwsh argv list. Mirrors WingetManager._build_argv.

        ``-DryRun`` is emitted as a [switch] token (presence/absence) per
        the same reasoning documented in WingetManager.
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

        def _tail(s: str | None, limit: int = 800) -> str:
            if not s:
                return "<empty>"
            if len(s) <= limit:
                return s
            return f"...<truncated {len(s) - limit} chars>...{s[-limit:]}"

        return (
            f"windows_update {phase.value} script produced no sidecar.\n"
            f"  script:        {script_path}\n"
            f"  expected at:   {sidecar_path}\n"
            f"  exit code:     {completed.returncode}\n"
            f"  stderr (tail): {_tail(completed.stderr)}\n"
            f"  stdout (tail): {_tail(completed.stdout)}"
        )

    def _resolve_pwsh(self) -> str:
        """Find ``pwsh.exe`` (preferred) or ``powershell.exe``. Cache result.

        Mirrors :meth:`WingetManager._resolve_pwsh`.
        """
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

        msg = (
            "no PowerShell binary on PATH (looked for pwsh.exe, pwsh, "
            "powershell.exe, powershell). Install PowerShell 7+ or pass "
            "pwsh_path= explicitly."
        )
        raise ManagerError(msg)
