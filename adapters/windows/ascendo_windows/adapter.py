"""WindowsAdapter - implements IAdapter for Windows.

M3 MVP: only IPackageManager (winget) is wired. Other capabilities
(inventory, snapshots, scheduling, source verification, elevation)
return ``None`` / raise :class:`NotImplementedError` until future
milestones.

Host detection prefers the stdlib (``platform``, ``socket``, ``getpass``,
``locale``) over WMI / pywin32 - this keeps the adapter importable in
unit tests on Linux without pywin32 installed. The ``IsUserAnAdmin``
check is wrapped in a try / except to match.
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

from .managers.winget import WingetManager

_log = logging.getLogger(__name__)


class WindowsAdapter(IAdapter):
    """Tier 1 adapter for Windows 10/11."""

    # Resolved relative to ``ascendo_windows/adapter.py`` -> parent ->
    # parent (``adapters/windows/``) -> scripts/ or lib/.
    SCRIPTS_DIR: ClassVar[Path] = (
        Path(__file__).resolve().parent.parent / "scripts"
    )
    LIB_DIR: ClassVar[Path] = (
        Path(__file__).resolve().parent.parent / "lib"
    )

    def __init__(self) -> None:
        self._cached_host: HostInfo | None = None

    # ── Identity ─────────────────────────────────────────────────────

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
        # M3 MVP - only package management is wired.
        return AdapterCapability.PACKAGE_MANAGEMENT

    # ── Sub-interface accessors ──────────────────────────────────────

    def package_managers(self, host: HostInfo) -> list[IPackageManager]:
        return [
            WingetManager(scripts_dir=self.SCRIPTS_DIR, lib_dir=self.LIB_DIR),
        ]

    def inventory(self) -> IInventory:
        msg = "Inventory not yet implemented (planned for M3.6)"
        raise NotImplementedError(msg)

    def snapshot(self) -> ISnapshot | None:
        return None  # VSS integration deferred

    def scheduler(self) -> IScheduler | None:
        return None  # Task Scheduler integration deferred

    def source(self) -> ISource | None:
        return None  # source verification deferred

    def elevation(self) -> IElevation | None:
        return None  # explicit elevation interface deferred (M3 MVP runs as user)

    # ── Lifecycle ────────────────────────────────────────────────────

    def detect_host(self) -> HostInfo:
        """Build a :class:`HostInfo` snapshot of the current host.

        The result is cached on the instance - the IAdapter contract
        explicitly permits returning the same instance on repeated calls.
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
        """Adapter self-test - returns a dict of ``component -> status``.

        Components inspected:

        * ``winget``       - is the binary on PATH?
        * ``pwsh``         - is PowerShell 7 on PATH? Also PS 5.1 fallback.
        * ``ascendo_lib``  - do the expected lib modules exist on disk?
        * ``ascendo_scripts`` - does the scripts directory exist?
        """
        out: dict[str, str] = {}

        # winget
        winget_path = shutil.which("winget") or shutil.which("winget.exe")
        if winget_path is None:
            out["winget"] = "unavailable: not on PATH"
        else:
            out["winget"] = self._winget_status(winget_path)

        # PowerShell
        out["pwsh"] = self._pwsh_status()

        # lib
        out["ascendo_lib"] = self._lib_status()

        # scripts
        out["ascendo_scripts"] = self._scripts_status()

        return out

    # ── Host detection helpers ───────────────────────────────────────

    def _detect_os(self) -> OperatingSystem:
        # ``platform.system()`` returns 'Windows', 'Linux', 'Darwin', etc.
        sys_name = platform.system()
        if sys_name == "Windows":
            return OperatingSystem.WINDOWS
        if sys_name == "Darwin":
            return OperatingSystem.MACOS
        if sys_name == "Linux":
            # The Windows adapter shouldn't normally be detect_host'd on
            # Linux, but for unit tests we still produce a sane value.
            return OperatingSystem.LINUX_OTHER
        return OperatingSystem.UNKNOWN

    def _detect_os_version(self, os_family: OperatingSystem) -> str:
        if os_family is OperatingSystem.WINDOWS:
            # platform.version() on Windows returns e.g. '10.0.26200'.
            # platform.release() gives '11' on Windows 11. Combine for a
            # human-friendly string that matches the legacy script output
            # in ``Aktualizacje-W11-Dell5520`` ("11 Pro 26200").
            release = platform.release() or "?"
            version = platform.version() or "?"
            edition = self._detect_windows_edition()
            if edition:
                return f"{release} {edition} {version}"
            return f"{release} {version}"
        # Linux / macOS / unknown - fall back to platform.platform() which
        # is reliably non-empty.
        plat = platform.platform()
        return plat if plat else "unknown"

    def _detect_windows_edition(self) -> str | None:
        """Best-effort Windows edition (e.g. 'Pro', 'Enterprise').

        Uses ``wmic`` only when running under Windows; on other hosts we
        skip silently. ``wmic`` is deprecated in newer Windows builds, so
        a None return is a perfectly normal outcome - we degrade to
        ``release + version``.
        """
        if platform.system() != "Windows":
            return None
        wmic = shutil.which("wmic")
        if wmic is None:
            return None
        try:
            res = subprocess.run(  # noqa: S603 (argv list, not shell)
                [wmic, "os", "get", "Caption", "/value"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        if res.returncode != 0:
            return None
        # Output: 'Caption=Microsoft Windows 11 Pro\r\n'
        for line in res.stdout.splitlines():
            line = line.strip()
            if line.lower().startswith("caption="):
                caption = line.split("=", 1)[1].strip()
                # Strip the 'Microsoft Windows 11' / 'Microsoft Windows 10'
                # prefix; the remainder is the edition.
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
        # ``platform.machine()`` returns 'AMD64' on Win64, 'ARM64' on
        # Win-on-ARM. Normalise to lower-case x86_64 / arm64 so sidecars
        # match what Linux + macOS adapters emit.
        raw = platform.machine() or "unknown"
        normalized = raw.lower()
        if normalized == "amd64":
            return "x86_64"
        return normalized

    def _detect_hostname(self) -> str:
        # ``socket.gethostname`` is portable and matches $env:COMPUTERNAME
        # in the common case.
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
        """Detect Administrator on Windows; False elsewhere.

        Wrapped in try / except so that imports succeed on non-Windows
        hosts where ``ctypes.windll`` is unavailable.
        """
        if platform.system() != "Windows":
            return False
        try:
            import ctypes  # local import: only needed on Windows
        except ImportError:  # pragma: no cover - ctypes is stdlib
            return False
        try:
            # Returns 1 if the current process is elevated, 0 otherwise.
            return bool(ctypes.windll.shell32.IsUserAnAdmin())  # type: ignore[attr-defined]
        except (AttributeError, OSError):
            return False

    def _detect_locale(self) -> str | None:
        # ``locale.getlocale()`` returns ('en_US', 'UTF-8') or (None, None)
        # depending on the shell. We translate underscore -> hyphen to
        # produce BCP-47 style 'en-US'.
        try:
            tag, _enc = locale.getlocale()
        except (ValueError, locale.Error):
            tag = None
        if not tag:
            return None
        return tag.replace("_", "-")

    # ── Health-check helpers ─────────────────────────────────────────

    def _winget_status(self, winget_path: str) -> str:
        try:
            res = subprocess.run(  # noqa: S603 (argv list, not shell)
                [winget_path, "--version"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return f"error: {exc}"
        if res.returncode != 0:
            return f"error: winget --version exited {res.returncode}"
        version = (res.stdout or res.stderr or "").strip()
        return f"ok: {version}" if version else "ok"

    def _pwsh_status(self) -> str:
        # Prefer PowerShell 7. Fall back to Windows PowerShell 5.1 and
        # mark it 'degraded' - the lib modules support both, but 7 is the
        # tested path.
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
                res = subprocess.run(  # noqa: S603 (argv list, not shell)
                    [found, "-NoProfile", "-Command", "$PSVersionTable.PSVersion.ToString()"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False,
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
        # The exact module set evolves; verify the directory is non-empty
        # of .ps1 / .psm1 files. We do not pin specific filenames here -
        # health_check is a smoke test, not a contract test.
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
        return "ok"
