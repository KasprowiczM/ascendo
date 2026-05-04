"""MacOSAdapter - implements IAdapter for macOS."""
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

from .inventory import MacOSInventory
from .managers.brew import BrewManager
from .managers.elevation import MacElevation
from .managers.mas import MasManager

_log = logging.getLogger(__name__)


def _resolve_resource_dir(env_var: str, repo_relative: str) -> Path:
    """Resolve a bundled-resource directory.

    Priority:
      1. ``$<env_var>`` if set (test fixture override or custom install path).
      2. ``adapters/macos/<repo_relative>`` resolved from the module's source
         location (the editable-install case).

    Returns a :class:`Path` even if the directory does not yet exist;
    callers (e.g. ``health_check``) report missing resources by checking
    ``is_dir()`` themselves.
    """
    override = os.environ.get(env_var)
    if override:
        return Path(override)
    return Path(__file__).resolve().parent.parent / repo_relative


class MacOSAdapter(IAdapter):
    """Tier 1 adapter for macOS (M5.3 scope — PACKAGE_MANAGEMENT | ELEVATION | INVENTORY).

    Capabilities declared:
        PACKAGE_MANAGEMENT — Homebrew formulae + casks via BrewManager;
                             Mac App Store via MasManager (sudo mas upgrade,
                             CVE-2025-43411 rule enforced).
        ELEVATION          — sudo password cache via MacElevation; exposed as
                             POST /elevation/auth on the dashboard so the SPA
                             can prompt once and forward sudo to mas/brew.
        INVENTORY          — installed-application enumeration via MacOSInventory
                             (uses ``system_profiler SPApplicationsDataType -json``
                             + ``mdfind`` fallback); populates the dashboard
                             Categories tab with the installed-apps list.

    MacElevation and MacOSInventory are singletons per adapter instance
    (cached in ``self._cached_elevation`` / ``self._cached_inventory``) so
    a single dashboard password prompt covers all managers and inventory
    reads reuse the same object within a process lifetime.

    Remaining accessors (snapshot, scheduler, source) return None
    and are reserved for M5.4-M5.5:
        M5.4 — snapshot (Time Machine read-only) + softwareupdate manager
        M5.5 — scheduler (launchd)
    """

    SCRIPTS_DIR: ClassVar[Path] = _resolve_resource_dir(
        "ASCENDO_MACOS_SCRIPTS_DIR", "scripts"
    )
    LIB_DIR: ClassVar[Path] = _resolve_resource_dir(
        "ASCENDO_MACOS_LIB_DIR", "lib"
    )

    def __init__(self) -> None:
        self._cached_host: HostInfo | None = None
        self._cached_elevation: MacElevation | None = None
        self._cached_inventory: MacOSInventory | None = None

    # ── Identity ──────────────────────────────────────────────────────────

    @property
    def name(self) -> str:
        return "macos"

    @property
    def display_name(self) -> str:
        return "macOS"

    @property
    def tier(self) -> int:
        return 1

    @property
    def capabilities(self) -> AdapterCapability:
        return (
            AdapterCapability.PACKAGE_MANAGEMENT
            | AdapterCapability.ELEVATION
            | AdapterCapability.INVENTORY
        )

    # ── Sub-interface accessors ───────────────────────────────────────────

    def package_managers(self, host: HostInfo) -> list[IPackageManager]:
        return [
            BrewManager(scripts_dir=self.SCRIPTS_DIR, lib_dir=self.LIB_DIR),
            MasManager(scripts_dir=self.SCRIPTS_DIR, lib_dir=self.LIB_DIR, elevation=self.elevation()),
        ]

    def inventory(self) -> IInventory | None:  # type: ignore[override]
        """Returns a cached MacOSInventory singleton (M5.3)."""
        if self._cached_inventory is None:
            self._cached_inventory = MacOSInventory(
                scripts_dir=self.SCRIPTS_DIR, lib_dir=self.LIB_DIR
            )
        return self._cached_inventory

    def snapshot(self) -> ISnapshot | None:
        """Not implemented yet (planned for M5.4 — Time Machine read-only ISnapshot). Returns None."""
        return None  # M5.4 (Time Machine read-only)

    def scheduler(self) -> IScheduler | None:
        """Not implemented yet (planned for M5.5 — launchd IScheduler). Returns None."""
        return None  # M5.5 (launchd)

    def source(self) -> ISource | None:
        """Not implemented in M5.1. Returns None."""
        return None

    def elevation(self) -> IElevation | None:
        """Returns a cached MacElevation singleton (M5.2)."""
        if self._cached_elevation is None:
            self._cached_elevation = MacElevation()
        return self._cached_elevation

    # ── Lifecycle ─────────────────────────────────────────────────────────

    def detect_host(self) -> HostInfo:
        """Build a HostInfo snapshot of the current macOS host.

        Caches the result — repeated calls within a process return the same
        instance (HostInfo is frozen/immutable so this is safe).
        """
        if self._cached_host is not None:
            return self._cached_host

        os_family = self._detect_os()
        os_version = self._detect_os_version(os_family)
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
        """Adapter self-test. Returns component→status_string for ``ascendo doctor``.

        Components checked (7 total):
            brew, jq, mas, system_profiler, bash, ascendo_lib, ascendo_scripts
        """
        out: dict[str, str] = {}
        out["brew"] = self._brew_status()
        out["jq"] = self._jq_status()
        out["mas"] = self._mas_status()
        out["system_profiler"] = self._system_profiler_status()
        out["bash"] = self._bash_status()
        out["ascendo_lib"] = self._lib_status()
        out["ascendo_scripts"] = self._scripts_status()
        return out

    # ── Private: OS detection ─────────────────────────────────────────────

    def _detect_os(self) -> OperatingSystem:
        if platform.system() == "Darwin":
            return OperatingSystem.MACOS
        # Not Darwin — return best-guess so detect_host doesn't crash off-Darwin
        sys_name = platform.system()
        if sys_name == "Windows":
            return OperatingSystem.WINDOWS
        if sys_name == "Linux":
            return OperatingSystem.LINUX_OTHER
        return OperatingSystem.UNKNOWN

    def _detect_os_version(self, os_family: OperatingSystem) -> str:
        if os_family is OperatingSystem.MACOS:
            try:
                res = subprocess.run(
                    ["sw_vers", "-productVersion"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    check=False,
                )
                v = (res.stdout or "").strip()
                return v if v else "unknown"
            except (OSError, subprocess.TimeoutExpired):
                return "unknown"
        # Off-Darwin fallback
        plat = platform.platform()
        return plat if plat else "unknown"

    def _detect_arch(self) -> str:
        raw = platform.machine() or "unknown"
        return raw.lower()

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
        """True if running as root (geteuid() == 0). Windows-safe via AttributeError guard."""
        try:
            return os.geteuid() == 0  # type: ignore[attr-defined]
        except AttributeError:
            # Windows: os.geteuid does not exist
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

    def _brew_status(self) -> str:
        path = shutil.which("brew")
        if path is None:
            return "unavailable: brew not on PATH (install from https://brew.sh)"
        try:
            res = subprocess.run(
                [path, "--version"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return f"error: {exc}"
        if res.returncode != 0:
            return f"error: brew --version exited {res.returncode}"
        lines = (res.stdout or "").strip().splitlines()
        v = lines[0] if lines else ""
        return f"ok: {v}" if v else "ok"

    def _jq_status(self) -> str:
        path = shutil.which("jq")
        if path is None:
            return "unavailable: jq not on PATH (install: brew install jq)"
        try:
            res = subprocess.run(
                [path, "--version"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return f"error: {exc}"
        if res.returncode != 0:
            return f"error: jq --version exited {res.returncode}"
        v = (res.stdout or "").strip()
        return f"ok: {v}" if v else "ok"

    def _mas_status(self) -> str:
        path = shutil.which("mas")
        if path is None:
            return "unavailable: mas not on PATH (install: brew install mas)"
        try:
            res = subprocess.run(
                [path, "version"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return f"error: {exc}"
        if res.returncode != 0:
            return f"error: mas version exited {res.returncode}"
        v = (res.stdout or "").strip()
        if not v:
            return "ok"
        try:
            major = int(v.split(".")[0])
            if major < 4:
                return f"degraded: mas {v} found, need >=4 (brew upgrade mas)"
            return f"ok: {v}"
        except (ValueError, IndexError):
            return f"ok: {v}"

    def _system_profiler_status(self) -> str:
        path = shutil.which("system_profiler") or "/usr/sbin/system_profiler"
        if not Path(path).is_file():
            return "unavailable: system_profiler not found (macOS-only built-in)"
        try:
            res = subprocess.run(
                [path, "-listDataTypes"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return f"error: {exc}"
        if res.returncode != 0:
            return f"error: system_profiler -listDataTypes exited {res.returncode}"
        if "SPApplicationsDataType" not in (res.stdout or ""):
            return "degraded: SPApplicationsDataType not advertised"
        return "ok"

    def _bash_status(self) -> str:
        # Prefer system bash; shutil.which("bash") finds it on macOS PATH
        path = shutil.which("bash") or "/bin/bash"
        if not Path(path).exists():
            return "unavailable: no bash binary found on PATH or at /bin/bash"
        try:
            res = subprocess.run(
                [path, "--version"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
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
        modules = (
            list(self.LIB_DIR.glob("*.sh"))
            + list(self.LIB_DIR.glob("*.py"))
        )
        if not modules:
            return (
                f"degraded: {self.LIB_DIR} exists but contains no .sh/.py modules"
            )
        return f"ok: {len(modules)} module(s)"

    def _scripts_status(self) -> str:
        if not self.SCRIPTS_DIR.is_dir():
            return f"unavailable: {self.SCRIPTS_DIR} does not exist"
        brew_dir = self.SCRIPTS_DIR / "brew"
        if not brew_dir.is_dir():
            return (
                f"degraded: {brew_dir} missing (brew scripts not installed)"
            )
        return "ok"
