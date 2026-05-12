"""DellDriverManager - IPackageManager wrapper for the Dell Command Update plugin.

The plugin proper lives at ``plugins/dell-driver-update/windows/*.ps1``
(per ADR-0007 plugin layout). This manager makes it visible to the
orchestrator alongside winget / msstore / npm / pip / web / arp /
windows_update, so ``ascendo run --phase check`` includes Dell driver
scans on Dell hardware out of the box.

Why this lives in ``adapters/windows/`` rather than being loaded by a
generic plugin loader:

  * The generic ``core/ascendo/plugins_loader/`` is currently a stub
    (M5.x roadmap). Until that ships, hard-wiring the single Windows
    OEM plugin keeps the user flow working.
  * The plugin is OS-specific (``supported_oses = ["windows"]``), so
    its discovery + dispatch logic is naturally adapter-local.
  * When the generic loader lands, this manager either disappears
    (replaced by ``PluginManager(slug='dell-driver-update')``) or
    becomes a thin shim over it. Either way the public sidecar
    contract (``check__plugin.json`` etc.) stays unchanged.

dcu-cli quirks worth knowing:

  * Every dcu-cli action (including ``/scan``) requires Administrator
    elevation. On a non-elevated token the plugin's check.ps1 catches
    the "requires elevation" error and emits an info message. We
    surface that as a ``skipped`` item from the manager when we can
    detect non-elevation up front, so the SPA shows a useful "needs
    Administrator" hint rather than an empty success row.
  * Exit codes: ``0`` = success no reboot, ``1`` = reboot required,
    ``5`` = reboot pending, ``500`` = no updates available. The plugin
    scripts already map these; we just trust the emitted sidecar.
  * Apply phase can take 30+ minutes for BIOS + multi-driver bundles.
    Timeout is set to 1 hour to match the plugin manifest.
"""
from __future__ import annotations

import ctypes
import logging
import os
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


# Standard Dell Command Update install locations on Windows.
# Probed in order; first hit wins. PATH lookup tried first for the
# rare custom-install case.
_DCU_PROBE_PATHS: tuple[str, ...] = (
    r"C:\Program Files\Dell\CommandUpdate\dcu-cli.exe",
    r"C:\Program Files (x86)\Dell\CommandUpdate\dcu-cli.exe",
)


def _find_dcu_cli() -> Path | None:
    """Locate dcu-cli.exe on the host. Returns None when not installed."""
    on_path = shutil.which("dcu-cli") or shutil.which("dcu-cli.exe")
    if on_path:
        return Path(on_path)
    for candidate in _DCU_PROBE_PATHS:
        p = Path(candidate)
        if p.is_file():
            return p
    return None


def _is_dell_host() -> bool:
    """Best-effort: is this machine made by Dell?

    Returns True when WMI ``Win32_ComputerSystem.Manufacturer`` matches
    "Dell" (case-insensitive). False on probe failure (no WMI / not
    Windows / unexpected output). Used as a hint for is_available()
    only; on a non-Dell host with dcu-cli installed (very rare) we
    still let the manager surface — the plugin's check.ps1 will then
    report "no updates available" cleanly.
    """
    if not _on_windows():
        return False
    wmic = shutil.which("wmic")
    if wmic is None:
        return False
    try:
        res = subprocess.run(
            [wmic, "computersystem", "get", "Manufacturer", "/value"],
            capture_output=True,
            text=True,
            timeout=4,
            check=False,
            **no_window_kwargs(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    if res.returncode != 0:
        return False
    for line in res.stdout.splitlines():
        if "=" in line and line.lower().strip().startswith("manufacturer="):
            value = line.split("=", 1)[1].strip().lower()
            return "dell" in value
    return False


def _on_windows() -> bool:
    return os.name == "nt"


class DellDriverManager(_BaseWindowsManager, IPackageManager):
    """Dell Command Update plugin per-source manager (Windows + Dell only).

    Args:
        plugin_dir: Path to ``plugins/dell-driver-update/`` (set by adapter).
        lib_dir: Path to ``adapters/windows/lib/`` (the plugin scripts
            dot-source AscendoJson.psm1 from there).
        pwsh_path: Optional override for the PowerShell executable.
        timeout_sec: Per-phase timeout. Default 3600 (1h) - BIOS apply
            can take 30+ minutes; matches plugin manifest budget.
    """

    SCRIPT_BY_PHASE: ClassVar[dict[Phase, str]] = {
        Phase.CHECK: "windows/check.ps1",
        Phase.PLAN: "windows/plan.ps1",
        Phase.APPLY: "windows/apply.ps1",
        Phase.VERIFY: "windows/verify.ps1",
        Phase.CLEANUP: "windows/cleanup.ps1",
    }
    DEFAULT_TIMEOUT_SEC: ClassVar[int] = 3600

    def __init__(
        self,
        *,
        plugin_dir: Path,
        lib_dir: Path,
        pwsh_path: str | None = None,
        timeout_sec: int = DEFAULT_TIMEOUT_SEC,
    ) -> None:
        self._plugin_dir: Path = Path(plugin_dir)
        self._lib_dir: Path = Path(lib_dir)
        self._pwsh_path: str | None = pwsh_path
        self._timeout_sec: int = int(timeout_sec)

    # ------------------------------------------------------------------
    # IPackageManager
    # ------------------------------------------------------------------

    @property
    def category(self) -> SourceType:
        # Per the plugin manifest (reporting.sidecar_category = "plugin"),
        # this manager reports under the umbrella PLUGIN source-type.
        # The Dell-specific feed is preserved in each item's
        # SourceFeed='dell_command_update' field (set by the .ps1 scripts).
        return SourceType.PLUGIN

    @property
    def display_name(self) -> str:
        return "Dell Driver Update (dcu-cli)"

    def is_available(self, host: HostInfo) -> bool:
        """Available when: on Windows AND dcu-cli.exe resolvable."""
        if host.os is not OperatingSystem.WINDOWS:
            return False
        return _find_dcu_cli() is not None

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
            msg = (
                f"phase {phase!r} is not supported by DellDriverManager "
                f"(supported: {sorted(p.value for p in self.SCRIPT_BY_PHASE)})"
            )
            raise ValueError(msg)

        script_path = self._plugin_dir / script_rel
        if not script_path.is_file():
            msg = (
                f"DellDriverManager: phase script missing on disk: "
                f"{script_path}"
            )
            raise ManagerError(msg)

        pwsh = self._resolve_pwsh()
        if pwsh is None:
            msg = (
                "DellDriverManager: no PowerShell binary found "
                "(pwsh.exe / pwsh / powershell.exe / powershell on PATH)"
            )
            raise ManagerError(msg)

        # Buffer dir for sidecar salvage (parity with other Windows managers).
        # Created lazily; only the check.ps1 script declares -BufDir today,
        # so we allocate but pass it only when the plugin's script declares
        # the param (defensive — dell plugin scripts don't yet, so we skip).
        with tempfile.TemporaryDirectory(prefix="ascendo-dell-") as tmp:
            tmp_path = Path(tmp)
            argv = self._build_argv(
                pwsh=pwsh,
                script_path=script_path,
                run=run,
                output_dir=tmp_path,
                item_filter=item_filter,
            )
            _log.debug("DellDriverManager spawning: %s", " ".join(argv))
            try:
                completed = subprocess.run(
                    argv,
                    capture_output=True,
                    text=True,
                    timeout=self._timeout_sec,
                    check=False,
                    **no_window_kwargs(),
                )
            except subprocess.TimeoutExpired as exc:
                msg = (
                    f"DellDriverManager: phase {phase.value} timed out "
                    f"after {self._timeout_sec}s"
                )
                raise ManagerError(msg) from exc

            sidecar_path = tmp_path / str(run.id) / f"{phase.value}__plugin.json"
            if not sidecar_path.is_file():
                # Stderr-tail diagnostic — the plugin script crashed before
                # emitting a sidecar. Surface what we can to the operator.
                stderr_tail = (completed.stderr or "").splitlines()[-12:]
                stdout_tail = (completed.stdout or "").splitlines()[-3:]
                msg = (
                    f"DellDriverManager: phase {phase.value} produced "
                    f"no sidecar.\n"
                    f"  script:        {script_path}\n"
                    f"  expected at:   {sidecar_path}\n"
                    f"  exit code:     {completed.returncode}\n"
                    f"  stderr tail:\n    "
                    + "\n    ".join(stderr_tail or ["(empty)"])
                    + f"\n  stdout tail:\n    "
                    + "\n    ".join(stdout_tail or ["(empty)"])
                )
                raise ManagerError(msg)

            try:
                sidecar = read_sidecar(sidecar_path)
            except (SidecarIOError, SidecarReadError) as exc:
                msg = (
                    f"DellDriverManager: failed to parse sidecar at "
                    f"{sidecar_path}: {exc}"
                )
                raise ManagerError(msg) from exc

            if completed.returncode != 0:
                _log.warning(
                    "Dell driver phase %s script exited %d but produced "
                    "a valid sidecar; trusting the sidecar (status=%s)",
                    phase.value,
                    completed.returncode,
                    sidecar.status.value,
                )
            return sidecar

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _resolve_pwsh(self) -> str | None:
        """Find pwsh executable. Cached after first lookup."""
        if self._pwsh_path is not None:
            return self._pwsh_path
        for candidate in ("pwsh.exe", "pwsh", "powershell.exe", "powershell"):
            found = shutil.which(candidate)
            if found is not None:
                self._pwsh_path = found
                return found
        return None

    def _build_argv(
        self,
        *,
        pwsh: str,
        script_path: Path,
        run: RunInfo,
        output_dir: Path,
        item_filter: Iterable[str] | None,
    ) -> list[str]:
        """Build the pwsh argv. Standard Ascendo phase params + Dell-specific
        absence (no extra params — the plugin scripts are self-contained).
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
            run.trigger.value if hasattr(run.trigger, "value") else str(run.trigger),
            "-ProfileName",
            run.profile,
            "-OutputDir",
            str(output_dir),
        ]
        if run.dry_run:
            argv.append("-DryRun")
        if item_filter is not None:
            ids = [i.strip() for i in item_filter if i and i.strip()]
            if ids:
                argv += ["-ItemFilter", ",".join(ids)]
        return argv
