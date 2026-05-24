"""UbuntuAdapter - implements IAdapter for Ubuntu / Debian.

Bridges core to the legacy bash phase scripts at top-level
``scripts/<cat>/<phase>.sh`` (in the repo root, NOT under
``adapters/ubuntu/scripts/``). Those scripts emit the legacy
``ascendo/v1`` sidecar schema; ``parse_sidecar()`` in
``ascendo.models.sidecar`` automatically translates to ``ascendo/v1``.

This adapter is intentionally a thin scaffold. Scheduler / Snapshot /
Elevation / Source are deferred to a later session.
"""
from __future__ import annotations

import getpass
import locale
import logging
import os
import platform
import shutil
import socket
import subprocess
from pathlib import Path
from typing import ClassVar

from ascendo.interfaces import (
    AdapterCapability,
    IAdapter,
    IElevation,
    IInventory,
    IPackageManager,
    IScheduler,
    ISnapshot,
    ISource,
)
from ascendo.models.host import ElevationMethod, HostInfo, OperatingSystem

from .inventory import UbuntuInventory
from .snapshot import TimeshiftSnapshot
from .managers.apt import AptManager
from .managers.brew import BrewManager
from .managers.drivers import DriversManager
from .managers.elevation import LinuxElevation
from .managers.flatpak import FlatpakManager
from .managers.npm import NpmManager
from .managers.pip import PipManager
from .managers.scheduler import SystemdScheduler
from .managers.snap import SnapManager
from .managers.web import WebManager

_log = logging.getLogger(__name__)


def _resolve_repo_root(env_var: str = "ASCENDO_UBUNTU_REPO_ROOT") -> Path:
    """Resolve the repo root that hosts the legacy ``scripts/`` and ``lib/``.

    Priority:
      1. ``$ASCENDO_UBUNTU_REPO_ROOT`` if set (explicit override).
      2. The repo root inferred from the module path:
         ``<repo>/adapters/ubuntu/ascendo_ubuntu/adapter.py``
         → walk up 3 levels to ``<repo>``.
    """
    override = os.environ.get(env_var)
    if override:
        return Path(override)
    return Path(__file__).resolve().parent.parent.parent.parent


class UbuntuAdapter(IAdapter):
    """Tier 1 adapter for Ubuntu / Debian (and via fallback all linux_* flavours).

    Capabilities:
        PACKAGE_MANAGEMENT — 7 managers in canonical order:
            apt → snap → brew → npm → pip → flatpak → drivers
        INVENTORY          — UbuntuInventory placeholder

    Sub-interface accessors snapshot / scheduler / elevation / source all
    return ``None`` in this scaffold.

    The adapter resolves the legacy bash scripts to the repo root's top-level
    ``scripts/`` directory (NOT ``adapters/ubuntu/scripts/``); same for
    ``lib/``. Override via ``$ASCENDO_UBUNTU_REPO_ROOT`` for tests / custom
    install layouts.
    """

    REPO_ROOT: ClassVar[Path] = _resolve_repo_root()
    SCRIPTS_DIR: ClassVar[Path] = REPO_ROOT / "scripts"
    LIB_DIR: ClassVar[Path] = REPO_ROOT / "lib"
    # Adapter-local script tree (used by the inventory enumerator, which
    # ships its own list.sh under adapters/ubuntu/scripts/inventory/).
    # Per-manager scripts still live at top-level scripts/ for now —
    # the migration into adapters/ubuntu/scripts/ is incremental.
    ADAPTER_SCRIPTS_DIR: ClassVar[Path] = (
        Path(__file__).resolve().parent.parent / "scripts"
    )
    ADAPTER_LIB_DIR: ClassVar[Path] = (
        Path(__file__).resolve().parent.parent / "lib"
    )

    def __init__(self) -> None:
        self._cached_host: HostInfo | None = None
        self._cached_inventory: UbuntuInventory | None = None
        self._cached_snapshot: TimeshiftSnapshot | None = None
        self._cached_elevation: LinuxElevation | None = None
        self._cached_scheduler: SystemdScheduler | None = None

    # ── Identity ──────────────────────────────────────────────────────────

    @property
    def name(self) -> str:
        return "ubuntu"

    @property
    def display_name(self) -> str:
        return "Ubuntu / Debian"

    @property
    def tier(self) -> int:
        return 1

    @property
    def capabilities(self) -> AdapterCapability:
        return (
            AdapterCapability.PACKAGE_MANAGEMENT
            | AdapterCapability.INVENTORY
            | AdapterCapability.SNAPSHOTS
            | AdapterCapability.ELEVATION
            | AdapterCapability.SCHEDULING
        )

    # ── Sub-interface accessors ───────────────────────────────────────────

    def package_managers(self, host: HostInfo) -> list[IPackageManager]:
        """Return the 7 package managers in canonical order.

        Order matches legacy ``update-all.sh`` and ``lib/orchestrator.sh``:
        apt → snap → brew → npm → pip → flatpak → drivers. Drivers runs
        last because the NVIDIA / fwupd flow may require a reboot.

        Each manager is wired with the cached :class:`LinuxElevation`
        singleton; when an apply phase runs AND a sudo password has been
        registered (via the dashboard's ``/elevation/auth`` endpoint),
        the manager's ``_build_env(phase=APPLY)`` injects
        ``SUDO_ASKPASS`` + ``_ASCENDO_SUDO_PW`` so the legacy bash
        ``apply.sh`` scripts authenticate non-interactively.
        """
        elev = self.elevation()
        kwargs = {
            "scripts_dir": self.SCRIPTS_DIR,
            "lib_dir": self.LIB_DIR,
            "repo_root": self.REPO_ROOT,
            "elevation": elev,
        }
        web_kwargs = {
            "scripts_dir": self.ADAPTER_SCRIPTS_DIR,
            "lib_dir": self.ADAPTER_LIB_DIR,
            "repo_root": self.REPO_ROOT,
            "elevation": elev,
        }
        return [
            AptManager(**kwargs),
            SnapManager(**kwargs),
            BrewManager(**kwargs),
            NpmManager(**kwargs),
            PipManager(**kwargs),
            FlatpakManager(**kwargs),
            WebManager(**web_kwargs),
            DriversManager(**kwargs),
        ]

    def inventory(self) -> IInventory | None:  # type: ignore[override]
        """Returns a cached :class:`UbuntuInventory` singleton.

        Drives ``adapters/ubuntu/scripts/inventory/list.sh`` which
        enumerates installed packages across apt, snap, flatpak, brew,
        npm and pip.
        """
        if self._cached_inventory is None:
            self._cached_inventory = UbuntuInventory(
                scripts_dir=self.ADAPTER_SCRIPTS_DIR,
                lib_dir=self.ADAPTER_LIB_DIR,
            )
        return self._cached_inventory

    def snapshot(self) -> ISnapshot | None:
        """Returns a cached :class:`TimeshiftSnapshot` singleton.

        The instance is constructed lazily on first call. It is returned
        unconditionally — callers should check ``is_available(host)``
        before invoking ``create()`` / ``list()``. (Returning the
        instance even when timeshift is not installed lets callers
        surface a friendly "snapshots unavailable" message instead of
        ``AttributeError`` on ``None``.)
        """
        if self._cached_snapshot is None:
            self._cached_snapshot = TimeshiftSnapshot()
        return self._cached_snapshot

    def scheduler(self) -> IScheduler | None:
        """Returns a cached :class:`SystemdScheduler` singleton.

        Drives ``adapters/ubuntu/scripts/scheduler/scheduler.sh`` over
        JSON-IPC. Per-user systemd timers — no root required.
        """
        if self._cached_scheduler is None:
            self._cached_scheduler = SystemdScheduler(
                scripts_dir=self.ADAPTER_SCRIPTS_DIR,
                lib_dir=self.ADAPTER_LIB_DIR,
            )
        return self._cached_scheduler

    def source(self) -> ISource | None:
        """Not implemented; reserved for M6 (cross-cutting source verification)."""
        return None

    def elevation(self) -> IElevation | None:
        """Returns a cached :class:`LinuxElevation` singleton.

        The instance is constructed lazily on first call with the
        adapter's static askpass helper at
        ``adapters/ubuntu/lib/askpass_helper.sh``. The dashboard's
        ``/elevation/auth`` endpoint cooperates via the duck-typed
        ``register_password`` / ``has_password_registered`` /
        ``invalidate`` methods (see
        ``core/ascendo/dashboard/routes/elevation.py``).
        """
        if self._cached_elevation is None:
            self._cached_elevation = LinuxElevation(
                askpass_helper=self.ADAPTER_LIB_DIR / "askpass_helper.sh",
            )
        return self._cached_elevation

    # ── Lifecycle ─────────────────────────────────────────────────────────

    def detect_host(self) -> HostInfo:
        """Build a HostInfo snapshot of the current Linux host.

        Cached — repeated calls within a process return the same instance.
        """
        if self._cached_host is not None:
            return self._cached_host

        os_family = self._detect_os()
        os_version = self._detect_os_version()
        arch = self._detect_arch()
        hostname = self._detect_hostname()
        user = self._detect_user()
        is_elevated = self._detect_is_elevated()
        elevation_method = (
            ElevationMethod.SUDO if is_elevated else ElevationMethod.NONE
        )
        bcp47 = self._detect_locale()

        self._cached_host = HostInfo(
            hostname=hostname,
            os=os_family,
            os_version=os_version,
            arch=arch,
            user=user,
            is_elevated=is_elevated,
            elevation_method=elevation_method,
            locale=bcp47,
        )
        return self._cached_host

    def health_check(self) -> dict[str, str]:
        """Adapter self-test for ``ascendo doctor``.

        Components checked (12 total):
            apt, snap, brew, npm, pip, flatpak, fwupd, timeshift, sudo,
            bash, ascendo_lib, ascendo_scripts
        """
        out: dict[str, str] = {}
        out["apt"] = self._tool_status("apt", "apt-get", flag="--version")
        out["snap"] = self._tool_status("snap", flag="version")
        out["brew"] = self._tool_status("brew", flag="--version")
        out["npm"] = self._tool_status("npm", flag="--version")
        out["pip"] = self._pip_status()
        out["flatpak"] = self._tool_status("flatpak", flag="--version")
        out["fwupd"] = self._tool_status("fwupdmgr", flag="--version")
        out["timeshift"] = self._timeshift_status()
        out["sudo"] = self._sudo_status()
        out["systemctl"] = self._systemctl_status()
        out["curl"] = self._tool_status("curl", flag="--version")
        out["bash"] = self._bash_status()
        out["ascendo_lib"] = self._lib_status()
        out["ascendo_scripts"] = self._scripts_status()
        return out

    # ── Private: OS detection ─────────────────────────────────────────────

    def _detect_os(self) -> OperatingSystem:
        if platform.system() != "Linux":
            sys_name = platform.system()
            if sys_name == "Windows":
                return OperatingSystem.WINDOWS
            if sys_name == "Darwin":
                return OperatingSystem.MACOS
            return OperatingSystem.UNKNOWN
        # Read /etc/os-release for ID
        release = self._read_os_release()
        ident = (release.get("ID") or "").strip().lower()
        if ident == "ubuntu":
            return OperatingSystem.LINUX_UBUNTU
        if ident == "debian":
            return OperatingSystem.LINUX_DEBIAN
        # ID_LIKE ancestor matching
        id_like = (release.get("ID_LIKE") or "").strip().lower()
        for token in id_like.split():
            if token == "ubuntu":
                return OperatingSystem.LINUX_UBUNTU
            if token == "debian":
                return OperatingSystem.LINUX_DEBIAN
        return OperatingSystem.LINUX_OTHER

    def _read_os_release(self) -> dict[str, str]:
        path = Path("/etc/os-release")
        if not path.is_file():
            return {}
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError:
            return {}
        out: dict[str, str] = {}
        for line in raw.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            out[k.strip()] = v.strip().strip("\"'")
        return out

    def _detect_os_version(self) -> str:
        release = self._read_os_release()
        v = (release.get("VERSION_ID") or "").strip()
        if v:
            return v
        # Fallback to platform.platform()
        plat = platform.platform()
        return plat if plat else "unknown"

    def _detect_arch(self) -> str:
        return (platform.machine() or "unknown").lower()

    def _detect_hostname(self) -> str:
        try:
            return socket.gethostname() or "unknown"
        except OSError:
            return "unknown"

    def _detect_user(self) -> str:
        try:
            return getpass.getuser()
        except (OSError, KeyError):
            return os.environ.get("USER") or os.environ.get("USERNAME") or "unknown"

    def _detect_is_elevated(self) -> bool:
        try:
            return os.geteuid() == 0  # type: ignore[attr-defined]
        except AttributeError:
            return False

    def _detect_locale(self) -> str | None:
        try:
            tag, _enc = locale.getlocale()
        except (ValueError, locale.Error):
            tag = None
        if not tag:
            return None
        return tag.replace("_", "-")

    # ── Private: health check helpers ────────────────────────────────────

    def _tool_status(
        self,
        name: str,
        *fallbacks: str,
        flag: str = "--version",
    ) -> str:
        """Resolve a CLI on PATH and probe its version flag.

        Args:
            name: Primary binary name (e.g. ``"apt"``).
            *fallbacks: Alternate binary names to try if the primary is
                missing (e.g. ``"apt-get"`` for ``apt``).
            flag: The flag that prints version. Default ``"--version"``;
                some tools (``snap``) prefer a subcommand like ``"version"``.
        """
        candidates = (name, *fallbacks)
        path: str | None = None
        chosen: str | None = None
        for cand in candidates:
            p = shutil.which(cand)
            if p is not None:
                path = p
                chosen = cand
                break
        if path is None:
            tried = "/".join(candidates)
            return f"unavailable: {tried} not on PATH"
        # Build version probe argv. ``snap version`` is a subcommand;
        # ``apt-get --version`` is a flag.
        argv = [path] + (flag.split() if flag else [])
        try:
            res = subprocess.run(
                argv, capture_output=True, text=True, timeout=10, check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return f"error: {exc}"
        if res.returncode != 0 and not (res.stdout or res.stderr):
            return f"error: {chosen} {flag} exited {res.returncode}"
        first = ((res.stdout or res.stderr) or "").strip().splitlines()
        v = first[0] if first else ""
        return f"ok: {v}" if v else "ok"

    def _pip_status(self) -> str:
        path = shutil.which("pip3") or shutil.which("pip")
        if path is None:
            return "unavailable: pip not on PATH (install: apt install python3-pip)"
        try:
            res = subprocess.run(
                [path, "--version"],
                capture_output=True, text=True, timeout=5, check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return f"error: {exc}"
        if res.returncode != 0:
            return f"error: pip --version exited {res.returncode}"
        line = (res.stdout or "").strip().splitlines()
        v = line[0] if line else ""
        return f"ok: {v}" if v else "ok"

    def _systemctl_status(self) -> str:
        """Probe systemctl (used by SystemdScheduler for per-user timers).

        Optional component: missing => degraded (not error) because the
        scheduler is a nice-to-have, not a hard requirement for apply.
        """
        path = shutil.which("systemctl")
        if path is None:
            return (
                "degraded: systemctl not on PATH "
                "(scheduler unavailable; install: apt install systemd)"
            )
        try:
            res = subprocess.run(
                [path, "--version"],
                capture_output=True, text=True, timeout=5, check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return f"error: {exc}"
        out = (res.stdout or res.stderr or "").strip().splitlines()
        v = out[0] if out else "systemctl"
        return f"ok: {v}"

    def _sudo_status(self) -> str:
        """Probe sudo + the static askpass helper.

        Three states:
          - ok: sudo on PATH AND askpass_helper.sh exists + executable
          - degraded: sudo present but askpass helper missing/non-exec
                      (apply phases will fall back to TTY prompts)
          - unavailable: sudo not installed (elevation impossible)
        """
        path = shutil.which("sudo")
        if path is None:
            return (
                "unavailable: sudo not on PATH "
                "(install: apt install sudo)"
            )
        helper = self.ADAPTER_LIB_DIR / "askpass_helper.sh"
        if not helper.is_file():
            return (
                f"degraded: sudo at {path} but "
                f"askpass helper missing at {helper} "
                f"(apply phases will require TTY prompt)"
            )
        try:
            mode = helper.stat().st_mode
        except OSError as exc:
            return f"error: cannot stat askpass helper: {exc}"
        if not (mode & 0o111):
            return (
                f"degraded: askpass helper at {helper} is not executable "
                f"(chmod +x to fix)"
            )
        try:
            res = subprocess.run(
                [path, "--version"],
                capture_output=True, text=True, timeout=5, check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return f"error: {exc}"
        out = (res.stdout or res.stderr or "").strip().splitlines()
        v = out[0] if out else "sudo"
        return f"ok: {v} + askpass helper"

    def _timeshift_status(self) -> str:
        """Probe timeshift CLI.

        Treated as an optional component: missing => warn (not error)
        because snapshots are a nice-to-have, not a hard requirement.
        """
        path = shutil.which("timeshift")
        if path is None:
            return (
                "degraded: timeshift not installed "
                "(snapshots unavailable; install: sudo apt install timeshift)"
            )
        try:
            res = subprocess.run(
                [path, "--version"],
                capture_output=True, text=True, timeout=5, check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return f"error: {exc}"
        out = (res.stdout or res.stderr or "").strip().splitlines()
        v = out[0] if out else ""
        return f"ok: {v}" if v else "ok"

    def _systemctl_status(self) -> str:
        path = shutil.which("systemctl")
        if path is None:
            return "warn: not available (systemctl not on PATH)"
        try:
            res = subprocess.run(
                [path, "--user", "is-system-running"],
                capture_output=True, text=True, timeout=5, check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return f"warn: {exc}"
        # is-system-running returns non-zero on degraded/initializing/etc.,
        # but systemctl itself is functional. Treat any successful spawn
        # as ok.
        line = (res.stdout or res.stderr or "").strip().splitlines()
        v = line[0] if line else ""
        return f"ok: {v}" if v else "ok"

    def _bash_status(self) -> str:
        path = shutil.which("bash") or "/bin/bash"
        if not Path(path).exists():
            return "unavailable: no bash binary found on PATH or at /bin/bash"
        try:
            res = subprocess.run(
                [path, "--version"],
                capture_output=True, text=True, timeout=5, check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return f"error: {exc}"
        if res.returncode != 0:
            return f"error: bash --version exited {res.returncode}"
        lines = (res.stdout or "").splitlines()
        v = lines[0] if lines else ""
        return f"ok: {v}" if v else "ok"

    def _lib_status(self) -> str:
        if not self.LIB_DIR.is_dir():
            return f"unavailable: {self.LIB_DIR} does not exist"
        modules = list(self.LIB_DIR.glob("*.sh")) + list(self.LIB_DIR.glob("*.py"))
        if not modules:
            return f"degraded: {self.LIB_DIR} exists but contains no .sh/.py modules"
        return f"ok: {len(modules)} module(s)"

    def _scripts_status(self) -> str:
        if not self.SCRIPTS_DIR.is_dir():
            return f"unavailable: {self.SCRIPTS_DIR} does not exist"
        apt_dir = self.SCRIPTS_DIR / "apt"
        if not apt_dir.is_dir():
            return f"degraded: {apt_dir} missing (apt scripts not installed)"
        return "ok"
