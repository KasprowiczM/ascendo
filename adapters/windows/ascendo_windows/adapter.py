"""WindowsAdapter - implements IAdapter for Windows."""
from __future__ import annotations

import getpass
import locale
import logging
import os
import platform
import shutil
import socket
import sqlite3
import subprocess
import sys
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
from ascendo.utils.proc import no_window_kwargs

from .inventory import WindowsInventory
from .managers.arp import ArpManager
from .managers.dell import DellDriverManager, _find_dcu_cli
from .managers.elevation import WindowsElevation
from .managers.msstore import MSStoreManager
from .managers.npm import NpmManager
from .managers.pip import PipManager
from .managers.scheduler import WindowsScheduler
from .managers.snapshot import WindowsSnapshot
from .managers.web import WebManager
from .managers.winget import WingetManager
from .managers.windows_update import WindowsUpdateManager

_log = logging.getLogger(__name__)


def _resolve_resource_dir(env_var: str, repo_relative: str) -> Path:
    """Resolve a bundled-resource directory.

    Priority:
      1. ``$<env_var>`` if set (PyInstaller bundle, MSI install path,
         or test fixture override).
      2. ``adapters/windows/<repo_relative>`` resolved from the
         module's source location (the editable-install case).

    Returns a :class:`Path` even if the directory does not yet exist;
    callers (e.g. ``health_check``) report missing resources by checking
    ``is_dir()`` themselves.
    """
    override = os.environ.get(env_var)
    if override:
        return Path(override)
    return Path(__file__).resolve().parent.parent / repo_relative


class WindowsAdapter(IAdapter):
    """Tier 1 adapter for Windows 10/11."""

    SCRIPTS_DIR: ClassVar[Path] = _resolve_resource_dir(
        "ASCENDO_WIN_SCRIPTS_DIR", "scripts"
    )
    LIB_DIR: ClassVar[Path] = _resolve_resource_dir(
        "ASCENDO_WIN_LIB_DIR", "lib"
    )
    # Plugin root for the Dell DCU plugin. Resolves to
    # ``<repo-root>/plugins/dell-driver-update/`` in dev installs and
    # ``$ProgramFiles\Ascendo\plugins\dell-driver-update\`` after MSI/
    # NSIS install (the latter is preserved via the
    # ``ASCENDO_WIN_DELL_PLUGIN_DIR`` env var; see
    # ``_resolve_resource_dir`` callers).
    DELL_PLUGIN_DIR: ClassVar[Path] = _resolve_resource_dir(
        "ASCENDO_WIN_DELL_PLUGIN_DIR",
        "../../plugins/dell-driver-update",
    )

    def __init__(self) -> None:
        self._cached_host: HostInfo | None = None

    @property
    def name(self) -> str:
        return "windows"

    @property
    def display_name(self) -> str:
        return "Windows"

    @property
    def tier(self) -> int:
        return 1

    @property
    def capabilities(self) -> AdapterCapability:
        return (
            AdapterCapability.PACKAGE_MANAGEMENT
            | AdapterCapability.INVENTORY
            | AdapterCapability.SNAPSHOTS
            | AdapterCapability.SCHEDULING
            | AdapterCapability.ELEVATION
        )

    def package_managers(self, host: HostInfo) -> list[IPackageManager]:
        config_dir = self.SCRIPTS_DIR.parent / "config"
        return [
            WingetManager(scripts_dir=self.SCRIPTS_DIR, lib_dir=self.LIB_DIR),
            MSStoreManager(scripts_dir=self.SCRIPTS_DIR, lib_dir=self.LIB_DIR),
            NpmManager(scripts_dir=self.SCRIPTS_DIR, lib_dir=self.LIB_DIR),
            PipManager(scripts_dir=self.SCRIPTS_DIR, lib_dir=self.LIB_DIR),
            WebManager(
                scripts_dir=self.SCRIPTS_DIR,
                lib_dir=self.LIB_DIR,
                config_dir=config_dir,
            ),
            DellDriverManager(
                plugin_dir=self.DELL_PLUGIN_DIR,
                lib_dir=self.LIB_DIR,
            ),
            ArpManager(scripts_dir=self.SCRIPTS_DIR, lib_dir=self.LIB_DIR),
            WindowsUpdateManager(scripts_dir=self.SCRIPTS_DIR, lib_dir=self.LIB_DIR),
        ]

    def inventory(self) -> IInventory:
        return WindowsInventory(
            scripts_dir=self.SCRIPTS_DIR,
            lib_dir=self.LIB_DIR,
        )

    def snapshot(self) -> ISnapshot | None:
        return WindowsSnapshot(scripts_dir=self.SCRIPTS_DIR, lib_dir=self.LIB_DIR)

    def scheduler(self) -> IScheduler | None:
        return WindowsScheduler(scripts_dir=self.SCRIPTS_DIR, lib_dir=self.LIB_DIR)

    def source(self) -> ISource | None:
        return None

    def elevation(self) -> IElevation | None:
        return WindowsElevation()

    def service_manager(self):
        """Windows AscendoDashboard service manager (A5 decoupling).

        Lets the dashboard's /service routes resolve the manager off the
        active adapter instead of importing ``ascendo_windows`` in core.
        """
        from .managers.service import WindowsServiceManager, default_repo_root

        return WindowsServiceManager(repo_root=default_repo_root())

    def detect_host(self) -> HostInfo:
        if self._cached_host is not None:
            return self._cached_host

        os_family = self._detect_os()
        os_version = self._detect_os_version(os_family)
        arch = self._detect_arch()
        hostname = self._detect_hostname()
        user = self._detect_user()
        is_elevated = self._detect_is_elevated()
        elevation_method = (
            ElevationMethod.UAC if is_elevated and os_family is OperatingSystem.WINDOWS
            else ElevationMethod.NONE
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
        """Adapter self-test. Returns component->status_string for ``ascendo doctor``.

        Components checked (10 total):
            winget, pswindowsupdate, npm, pip, dcu, pwsh, ascendo_lib,
            ascendo_scripts, web_registry, inventory_db
        """
        out: dict[str, str] = {}

        winget_path = shutil.which("winget") or shutil.which("winget.exe")
        if winget_path is None:
            out["winget"] = "unavailable: not on PATH"
        else:
            out["winget"] = self._winget_status(winget_path)

        out["pswindowsupdate"] = self._pswindowsupdate_status()
        out["npm"] = self._npm_status()
        out["pip"] = self._pip_status()
        out["dcu"] = self._dcu_status()
        out["pwsh"] = self._pwsh_status()
        out["ascendo_lib"] = self._lib_status()
        out["ascendo_scripts"] = self._scripts_status()
        out["web_registry"] = self._web_registry_status()
        out["inventory_db"] = self._inventory_db_status()

        return out

    def _detect_os(self) -> OperatingSystem:
        sys_name = platform.system()
        if sys_name == "Windows":
            return OperatingSystem.WINDOWS
        if sys_name == "Darwin":
            return OperatingSystem.MACOS
        if sys_name == "Linux":
            return OperatingSystem.LINUX_OTHER
        return OperatingSystem.UNKNOWN

    def _detect_os_version(self, os_family: OperatingSystem) -> str:
        if os_family is OperatingSystem.WINDOWS:
            release = platform.release() or "?"
            version = platform.version() or "?"
            edition = self._detect_windows_edition()
            if edition:
                return f"{release} {edition} {version}"
            return f"{release} {version}"
        plat = platform.platform()
        return plat if plat else "unknown"

    def _detect_windows_edition(self) -> str | None:
        if platform.system() != "Windows":
            return None
        wmic = shutil.which("wmic")
        if wmic is None:
            return None
        try:
            res = subprocess.run(
                [wmic, "os", "get", "Caption", "/value"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
                **no_window_kwargs(),
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        if res.returncode != 0:
            return None
        for line in res.stdout.splitlines():
            line = line.strip()
            if line.lower().startswith("caption="):
                caption = line.split("=", 1)[1].strip()
                for prefix in (
                    "Microsoft Windows 11 ",
                    "Microsoft Windows 10 ",
                    "Microsoft Windows ",
                ):
                    if caption.startswith(prefix):
                        return caption[len(prefix):].strip() or None
                return caption or None
        return None

    def _detect_arch(self) -> str:
        raw = platform.machine() or "unknown"
        normalized = raw.lower()
        if normalized == "amd64":
            return "x86_64"
        return normalized

    def _detect_hostname(self) -> str:
        try:
            return socket.gethostname() or "unknown"
        except OSError:
            return "unknown"

    def _detect_user(self) -> str:
        try:
            return getpass.getuser()
        except (OSError, KeyError):
            return os.environ.get("USERNAME") or os.environ.get("USER") or "unknown"

    def _detect_is_elevated(self) -> bool:
        if platform.system() != "Windows":
            return False
        try:
            import ctypes
        except ImportError:
            return False
        try:
            return bool(ctypes.windll.shell32.IsUserAnAdmin())  # type: ignore[attr-defined]
        except (AttributeError, OSError):
            return False

    def _detect_locale(self) -> str | None:
        try:
            tag, _enc = locale.getlocale()
        except (ValueError, locale.Error):
            tag = None
        if not tag:
            return None
        return tag.replace("_", "-")

    def _winget_status(self, winget_path: str) -> str:
        try:
            res = subprocess.run(
                [winget_path, "--version"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
                **no_window_kwargs(),
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return f"error: {exc}"
        if res.returncode != 0:
            return f"error: winget --version exited {res.returncode}"
        version = (res.stdout or res.stderr or "").strip()
        return f"ok: {version}" if version else "ok"

    def _pswindowsupdate_status(self) -> str:
        if platform.system() != "Windows":
            return "unavailable: non-Windows host"

        pwsh = None
        for candidate in ("pwsh.exe", "pwsh", "powershell.exe", "powershell"):
            pwsh = shutil.which(candidate)
            if pwsh is not None:
                break
        if pwsh is None:
            return "unavailable: no PowerShell binary on PATH"

        try:
            res = subprocess.run(
                [
                    pwsh,
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    "[bool](Get-Module -ListAvailable -Name PSWindowsUpdate)",
                ],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
                **no_window_kwargs(),
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return f"error: probe failed: {exc}"

        if res.returncode != 0:
            return f"error: probe exited {res.returncode}"

        out = (res.stdout or "").strip().lower()
        if out == "true":
            return "ok: module installed"
        return (
            "unavailable: PSWindowsUpdate module not installed "
            "(install via: Install-Module PSWindowsUpdate -Scope CurrentUser -Force)"
        )

    def _npm_status(self) -> str:
        """Probe npm via PATH; report version on success.

        npm is required for the npm-globals package manager (parity with
        macOS's NpmManager). On Windows, npm typically ships as
        ``npm.cmd`` (wrapping ``node.exe``); ``shutil.which`` finds both.
        """
        npm_path = shutil.which("npm") or shutil.which("npm.cmd")
        if npm_path is None:
            return "unavailable: not on PATH (install Node.js to enable npm-globals updates)"
        try:
            res = subprocess.run(
                [npm_path, "--version"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
                **no_window_kwargs(),
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return f"error: {exc}"
        if res.returncode != 0:
            return f"error: npm --version exited {res.returncode}"
        version = (res.stdout or res.stderr or "").strip().splitlines()
        v = version[0] if version else ""
        return f"ok: npm {v}" if v else "ok"

    def _pip_status(self) -> str:
        """Probe pip via the Windows py launcher first, then python -m pip, then pip.

        On Windows the canonical way to invoke the user's preferred Python
        is the ``py`` launcher (ships with python.org installers). If it's
        on PATH that's the most reliable indicator. Otherwise fall back
        to ``python -m pip`` (matches the venv shim a manual install uses)
        and finally bare ``pip``.
        """
        attempts: tuple[tuple[list[str], str], ...] = (
            (["py", "-m", "pip", "--version"], "py launcher"),
            (["python", "-m", "pip", "--version"], "python -m pip"),
            (["pip", "--version"], "pip"),
        )
        for argv, via in attempts:
            exe = shutil.which(argv[0])
            if exe is None:
                continue
            try:
                res = subprocess.run(
                    [exe, *argv[1:]],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    check=False,
                    **no_window_kwargs(),
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                return f"error: {via} probe failed: {exc}"
            if res.returncode != 0:
                continue
            line = (res.stdout or "").strip().splitlines()
            v = line[0] if line else ""
            return f"ok: {v} ({via})" if v else f"ok ({via})"
        return "unavailable: no python+pip on PATH"

    def _dcu_status(self) -> str:
        """Probe Dell Command Update (dcu-cli.exe).

        Resolution order:
          1. PATH (Get-Command equivalent via shutil.which)
          2. ``C:\\Program Files\\Dell\\CommandUpdate\\dcu-cli.exe``
          3. ``C:\\Program Files (x86)\\Dell\\CommandUpdate\\dcu-cli.exe``

        Reports ``unavailable: not installed`` on non-Dell hosts so the
        ``ascendo doctor`` row is informative rather than alarming.
        Doesn't actually invoke dcu-cli (it requires elevation even for
        ``/version``, and admin-prompts in a health check are
        operator-hostile).
        """
        if platform.system() != "Windows":
            return "unavailable: non-Windows host"
        dcu = _find_dcu_cli()
        if dcu is None:
            return (
                "unavailable: not installed (Dell Command Update missing; "
                "install via Microsoft Store or winget install Dell.CommandUpdate)"
            )
        return f"ok: {dcu} (requires Administrator to invoke)"

    def _web_registry_status(self) -> str:
        """Validate the web_apps.toml registry is parseable + count apps.

        Loads via the Pydantic schema (web_registry.WebRegistryV2) so a
        malformed sub-table or unknown handler surfaces as an explicit
        ``error: validation failed at <loc>: <msg>`` rather than silently
        passing the raw-TOML parse check.

        Absent file is still reported as ``unavailable`` (informational —
        e.g. minimal PyInstaller bundles may strip resources).
        """
        registry = self.SCRIPTS_DIR.parent / "config" / "web_apps.toml"
        if not registry.is_file():
            return (
                "unavailable: web_apps.toml not shipped yet "
                "(WebManager Tier-B baseline)"
            )
        try:
            from .web_registry import WebRegistryV2
        except ImportError as exc:
            return f"error: web_registry import failed: {exc}"
        try:
            reg = WebRegistryV2.load(registry, None)
        except Exception as exc:    # noqa: BLE001
            return f"error: registry validation failed: {exc}"
        active = reg.active_apps()
        if not active:
            return "degraded: registry is empty"
        return f"ok: {len(active)} apps registered"

    def _inventory_db_status(self) -> str:
        """Probe the SQLite inventory cache; report row count if present.

        Honours ``$ASCENDO_INVENTORY_DB`` (same env var the dashboard +
        ``ascendo build-inventory`` CLI use); otherwise falls back to
        ``~/.ascendo/inventory.db``. Informational only: absence means
        the user hasn't run a check yet, which is not a failure.
        """
        override = os.environ.get("ASCENDO_INVENTORY_DB")
        db_path = Path(override) if override else Path.home() / ".ascendo" / "inventory.db"
        if not db_path.is_file():
            return "unavailable: run `ascendo build-inventory` to populate"
        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        except sqlite3.Error as exc:
            return f"degraded: cannot open {db_path}: {exc}"
        try:
            cur = conn.execute("SELECT COUNT(*) FROM inventory_items")
            row = cur.fetchone()
            count = int(row[0]) if row else 0
        except sqlite3.Error as exc:
            return f"degraded: cannot query inventory_items: {exc}"
        finally:
            try:
                conn.close()
            except sqlite3.Error:
                pass
        return f"ok: {count} rows cached"

    def _pwsh_status(self) -> str:
        for candidate, label, degraded in (
            ("pwsh.exe", "pwsh", False),
            ("pwsh", "pwsh", False),
            ("powershell.exe", "powershell", True),
            ("powershell", "powershell", True),
        ):
            found = shutil.which(candidate)
            if found is None:
                continue
            try:
                res = subprocess.run(
                    [found, "-NoProfile", "-Command", "$PSVersionTable.PSVersion.ToString()"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False,
                    **no_window_kwargs(),
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                return f"error: {label} probe failed: {exc}"
            if res.returncode != 0:
                continue
            version = (res.stdout or "").strip().splitlines()
            v = version[-1] if version else ""
            prefix = "degraded" if degraded else "ok"
            suffix = f": {v}" if v else ""
            note = " (PowerShell 5.1 - tested but PowerShell 7 preferred)" if degraded else ""
            return f"{prefix}{suffix}{note}"
        return "unavailable: no pwsh.exe / powershell.exe on PATH"

    def _lib_status(self) -> str:
        if not self.LIB_DIR.is_dir():
            return f"unavailable: {self.LIB_DIR} does not exist"
        candidates = [
            *self.LIB_DIR.glob("*.ps1"),
            *self.LIB_DIR.glob("*.psm1"),
        ]
        if not candidates:
            return f"degraded: {self.LIB_DIR} exists but contains no .ps1/.psm1 modules"
        return f"ok: {len(candidates)} module(s)"

    def _scripts_status(self) -> str:
        if not self.SCRIPTS_DIR.is_dir():
            return f"unavailable: {self.SCRIPTS_DIR} does not exist"
        winget_dir = self.SCRIPTS_DIR / "winget"
        if not winget_dir.is_dir():
            return f"degraded: {winget_dir} missing (winget scripts not installed)"
        inventory_script = self.SCRIPTS_DIR / "inventory" / "list.ps1"
        if not inventory_script.is_file():
            return (
                f"degraded: {inventory_script} missing "
                f"(inventory capability unavailable)"
            )
        return "ok"
