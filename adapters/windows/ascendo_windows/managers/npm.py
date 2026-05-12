"""NpmManager - IPackageManager for npm globals on Windows.

Mirrors :class:`WingetManager` shape - PowerShell phase scripts under
``adapters/windows/scripts/npm/``, JSON-IPC over a temp dir.

Scope on Windows:
  * ``npm`` and ``pnpm`` (npm-installed-globally).
  * Anthropic / OpenAI / Google CLI tools shipped via npm
    (claude-code, codex-cli, gemini-cli, qwen-code, opencode-cli) -
    package list is in ``adapters/windows/config/npm_global_clis.txt``.

Why this manager and not winget? npm package distribution moves on
PyPI/registry cadences that winget doesn't track. On Windows, npm is
distributed as ``npm.cmd`` (a CMD shim wrapping ``node.exe``) when Node
is installed via the Node.js installer or via winget. We don't manage
node itself here (that's handled by winget); we manage what npm
installs globally.

NO sudo / UAC on Windows: npm globals install to the user-owned
``%APPDATA%\\npm`` prefix by default, which is writable without
elevation.
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


class NpmManager(_BaseWindowsManager, IPackageManager):
    """npm global packages per-source manager (Windows).

    Args:
        scripts_dir: Path to ``adapters/windows/scripts/`` (set by adapter).
        lib_dir: Path to ``adapters/windows/lib/`` (informational only).
        pwsh_path: Optional override for the PowerShell executable. If
            ``None``, resolves at run time (``pwsh.exe`` then
            ``powershell.exe``).
        timeout_sec: Per-phase timeout. Default 1800 (30 min) - npm
            install of large CLIs (Claude Code, Codex) can be slow.
    """

    SCRIPT_BY_PHASE: ClassVar[dict[Phase, str]] = {
        Phase.CHECK: "npm/check.ps1",
        Phase.PLAN: "npm/plan.ps1",
        Phase.APPLY: "npm/apply.ps1",
        Phase.VERIFY: "npm/verify.ps1",
        Phase.CLEANUP: "npm/cleanup.ps1",
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
        return SourceType.NPM

    @property
    def display_name(self) -> str:
        return "npm global packages"

    # -- Availability ----------------------------------------------------

    def is_available(self, host: HostInfo) -> bool:
        """True if Windows + npm (or npm.cmd) is on PATH.

        Note: we only consult ``host.os`` and ``shutil.which``. We do NOT
        spawn PowerShell here - per the IPackageManager contract,
        ``is_available`` must complete in < 100 ms.
        """
        if host.os is not OperatingSystem.WINDOWS:
            return False
        # On Windows the Node installer puts a CMD shim at
        # ``%ProgramFiles%\nodejs\npm.cmd``; ``shutil.which`` picks up
        # both ``npm`` and ``npm.cmd``. The bare ``npm`` form sometimes
        # resolves on PATH too (e.g. via Git Bash or nvm-windows shims).
        if shutil.which("npm") is not None:
            return True
        return shutil.which("npm.cmd") is not None

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
                f"NpmManager does not support phase {phase.value!r}; "
                f"supported phases: "
                f"{sorted(p.value for p in self.SCRIPT_BY_PHASE)}"
            )
        script_path = self._scripts_dir / script_rel
        pwsh = self._resolve_pwsh()

        with tempfile.TemporaryDirectory(prefix="ascendo-npm-") as tmp_str:
            output_dir = Path(tmp_str)

            argv = self._build_argv(
                pwsh=pwsh,
                script_path=script_path,
                run=run,
                output_dir=output_dir,
                item_filter=item_filter,
            )

            _log.debug(
                "NpmManager.run_phase: phase=%s run_id=%s argv=%r",
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
                    f"npm {phase.value} script timed out after "
                    f"{self._timeout_sec}s: {script_path}"
                ) from exc
            except OSError as exc:
                raise ManagerError(
                    f"failed to spawn pwsh for npm {phase.value}: {exc}"
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
                    f"npm {phase.value} script wrote unparseable sidecar "
                    f"at {sidecar_path}: {exc}"
                ) from exc

            if completed.returncode != 0:
                _log.warning(
                    "npm %s script exited %d but produced a valid sidecar; "
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
          npm package names cannot contain commas, so this is safe.
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
            f"npm {phase.value} script produced no sidecar.\n"
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
