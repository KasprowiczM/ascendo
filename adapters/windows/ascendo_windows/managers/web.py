"""WebManager - sixth IPackageManager for Windows.

Covers third-party apps installed outside winget / msstore — apps with
their own update channels (Brave, Obsidian, Notion, OBS Studio) or no
machine-readable channel at all (Discord, Slack, Zoom — handler=builtin).

Mirrors :class:`NpmManager` shape: PowerShell phase scripts under
``adapters/windows/scripts/web/``, JSON-IPC over a temp dir.

Scope on Windows (v1):
  * Curated TOML registry at ``adapters/windows/config/web_apps.toml``.
  * 3 handlers: github_release / release_feed / builtin. (No
    sparkle/keystone/squirrel/omaha/msupdate/docker — those are macOS-
    specific update channels.)
  * Ownership detection via HKLM/HKCU Uninstall registry — apps with
    no matching ``windows_uninstall_key`` produce no sidecar item.
  * Apply is Tier-B trigger-only in v1: opens the vendor's release/
    download page, emits ``triggered`` status. We do not yet download
    + run .exe installers from Ascendo (needs Authenticode +
    UAC + per-installer flag handling).
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


class WebManager(_BaseWindowsManager, IPackageManager):
    """Web (third-party app) per-source manager.

    Args:
        scripts_dir: Path to ``adapters/windows/scripts/`` (set by adapter).
        lib_dir: Path to ``adapters/windows/lib/`` (informational + used
            by the PS scripts at dot-source time).
        config_dir: Path to ``adapters/windows/config/`` (containing
            ``web_apps.toml``). Defaults to ``<scripts_dir>/../config``.
        pwsh_path: Optional override for the PowerShell executable.
        timeout_sec: Per-phase timeout. Default 600 (10 min) — network
            probes (GitHub API, vendor feeds) should be fast but we leave
            slack for retries.
    """

    SCRIPT_BY_PHASE: ClassVar[dict[Phase, str]] = {
        Phase.CHECK: "web/check.ps1",
        Phase.PLAN: "web/plan.ps1",
        Phase.APPLY: "web/apply.ps1",
        Phase.VERIFY: "web/verify.ps1",
        Phase.CLEANUP: "web/cleanup.ps1",
    }
    DEFAULT_TIMEOUT_SEC: ClassVar[int] = 600

    def __init__(
        self,
        *,
        scripts_dir: Path,
        lib_dir: Path,
        config_dir: Path | None = None,
        pwsh_path: str | None = None,
        timeout_sec: int = DEFAULT_TIMEOUT_SEC,
    ) -> None:
        self._scripts_dir: Path = Path(scripts_dir)
        self._lib_dir: Path = Path(lib_dir)
        self._config_dir: Path = (
            Path(config_dir) if config_dir is not None
            else self._scripts_dir.parent / "config"
        )
        self._pwsh_override: str | None = pwsh_path
        self._pwsh_resolved: str | None = None
        self._timeout_sec: int = timeout_sec

    # ── Identity ─────────────────────────────────────────────────────

    @property
    def category(self) -> SourceType:
        return SourceType.WEB

    @property
    def display_name(self) -> str:
        return "Web installers (Brave, Obsidian, Notion, OBS, etc.)"

    # ── Availability ─────────────────────────────────────────────────

    def is_available(self, host: HostInfo) -> bool:
        """True on Windows when the registry TOML parses.

        We DO NOT spawn pwsh here (per ``IPackageManager.is_available``
        contract: must complete in < 100 ms). Instead we try to parse the
        shipped registry via the Python schema and treat success as
        availability. Apps with handler=builtin require no external probe
        so the manager is "available" even if all the upstream Tier-A
        feeds are offline.
        """
        if host.os is not OperatingSystem.WINDOWS:
            return False
        try:
            from ascendo_windows.web_registry import WebRegistryV2
        except ImportError:
            return False
        shipped = self._config_dir / "web_apps.toml"
        if not shipped.is_file():
            return False
        try:
            WebRegistryV2.load(shipped, None)
        except Exception:  # noqa: BLE001 — health_check surfaces details
            return False
        return True

    # ── Phase execution ──────────────────────────────────────────────

    def run_phase(
        self,
        phase: Phase,
        run: RunInfo,
        host: HostInfo,
        *,
        item_filter: Iterable[str] | None = None,
    ) -> Sidecar:
        script_rel = self.SCRIPT_BY_PHASE.get(phase)
        if script_rel is None:
            raise ManagerError(
                f"WebManager does not support phase {phase.value!r}; "
                f"supported phases: "
                f"{sorted(p.value for p in self.SCRIPT_BY_PHASE)}"
            )
        script_path = self._scripts_dir / script_rel
        pwsh = self._resolve_pwsh()

        with tempfile.TemporaryDirectory(prefix="ascendo-web-") as tmp_str:
            output_dir = Path(tmp_str)
            argv = self._build_argv(
                pwsh=pwsh,
                script_path=script_path,
                run=run,
                output_dir=output_dir,
                item_filter=item_filter,
            )
            _log.debug(
                "WebManager.run_phase: phase=%s run_id=%s argv=%r",
                phase.value, run.id, argv,
            )

            log_path = (
                output_dir / str(run.id)
                / f"{phase.value}__{self.category.value}.log"
            )
            log_path.parent.mkdir(parents=True, exist_ok=True)

            try:
                completed = self._run_streaming(
                    argv, log_path=log_path, timeout=self._timeout_sec,
                )
            except subprocess.TimeoutExpired as exc:
                raise ManagerError(
                    f"web {phase.value} script timed out after "
                    f"{self._timeout_sec}s: {script_path}"
                ) from exc
            except OSError as exc:
                raise ManagerError(
                    f"failed to spawn pwsh for web {phase.value}: {exc}"
                ) from exc

            sidecar_path = (
                output_dir / str(run.id)
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
                    f"web {phase.value} script wrote unparseable sidecar "
                    f"at {sidecar_path}: {exc}"
                ) from exc

            if completed.returncode != 0:
                _log.warning(
                    "web %s script exited %d but produced a valid sidecar; "
                    "trusting the sidecar (status=%s)",
                    phase.value, completed.returncode, sidecar.status.value,
                )
            return sidecar

    # ── Internals ────────────────────────────────────────────────────

    def _run_streaming(
        self,
        argv: list[str],
        *,
        log_path: Path,
        timeout: float,
    ) -> subprocess.CompletedProcess[str]:
        """Tee stdout/stderr to ``log_path`` line-by-line (same as winget)."""
        import time as _time

        proc = subprocess.Popen(  # noqa: S603
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
            rc = proc.wait(
                timeout=max(1.0, timeout - (_time.monotonic() - started))
            )
        except subprocess.TimeoutExpired:
            proc.kill()
            raise
        return subprocess.CompletedProcess(
            args=argv, returncode=rc, stdout="".join(captured), stderr="",
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
        """Build the pwsh argv. Adds ``-ConfigDir <config>`` so the script
        finds web_apps.toml even when the Windows adapter is installed
        outside its source tree (PyInstaller bundle / .msi).
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
            "-ConfigDir",
            str(self._config_dir),
        ]
        if run.dry_run:
            argv.append("-DryRun")
        if item_filter is not None:
            cleaned = [
                s.strip() for s in item_filter
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
        def _tail(s: str | None, limit: int = 800) -> str:
            if not s:
                return "<empty>"
            if len(s) <= limit:
                return s
            return f"...<truncated {len(s) - limit} chars>...{s[-limit:]}"
        return (
            f"web {phase.value} script produced no sidecar.\n"
            f"  script:        {script_path}\n"
            f"  expected at:   {sidecar_path}\n"
            f"  exit code:     {completed.returncode}\n"
            f"  stderr (tail): {_tail(completed.stderr)}\n"
            f"  stdout (tail): {_tail(completed.stdout)}"
        )

    def _resolve_pwsh(self) -> str:
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
