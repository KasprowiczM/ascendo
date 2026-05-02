"""ArpManager — IPackageManager for MSI / classic Win32 installs surfaced
through the Add/Remove Programs registry tree.

Many Windows apps (especially older or enterprise ones) are not in
winget's catalog and not in the Microsoft Store; they live as registry
entries under ``HKLM\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*``
(and the ``WOW6432Node`` mirror, plus the ``HKCU`` per-user variant).

The PowerShell scripts under ``adapters/windows/scripts/arp/`` enumerate
those keys, filter out anything already managed by winget (so we don't
double-count), and emit the standard ``ascendo/v1`` sidecar with
``category="registry_arp"``. The check phase is read-only; apply uses
``UninstallString`` (or ``msiexec /x {ProductCode}``) to remove a package
when explicitly requested via ``-ItemFilter``.

Like :class:`MSStoreManager`, this class re-uses :class:`WingetManager`'s
spawn / IPC / error-format machinery and only overrides identity and
script paths.
"""
from __future__ import annotations

from typing import ClassVar

from ascendo.models.host import HostInfo, OperatingSystem
from ascendo.models.package import SourceType
from ascendo.models.run import Phase

from .winget import WingetManager


class ArpManager(WingetManager):
    """Add/Remove Programs (registry) per-source manager."""

    SCRIPT_BY_PHASE: ClassVar[dict[Phase, str]] = {
        Phase.CHECK:   "arp/check.ps1",
        Phase.PLAN:    "arp/plan.ps1",
        Phase.APPLY:   "arp/apply.ps1",
        Phase.VERIFY:  "arp/verify.ps1",
        Phase.CLEANUP: "arp/cleanup.ps1",
    }

    @property
    def category(self) -> SourceType:
        return SourceType.REGISTRY_ARP

    @property
    def display_name(self) -> str:
        return "Add/Remove Programs (registry)"

    def is_available(self, host: HostInfo) -> bool:
        """ARP scanning works on any Windows host — no external tool needed.

        Unlike :class:`WingetManager`, we don't require ``winget`` on PATH.
        Pure registry reads via :class:`Microsoft.Win32.Registry` (which
        is available in any PowerShell on Windows).
        """
        return host.os is OperatingSystem.WINDOWS
