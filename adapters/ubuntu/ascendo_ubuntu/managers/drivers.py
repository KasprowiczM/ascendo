"""DriversManager - IPackageManager wrapping ``scripts/drivers/<phase>.sh``.

Covers NVIDIA driver upgrades (via apt) and fwupd firmware updates.
The legacy phase scripts handle the dispatch internally; this manager
just bridges to them.
"""
from __future__ import annotations

from typing import ClassVar

from ascendo.models.host import HostInfo
from ascendo.models.package import SourceType

from ._base import BashPhaseManager


class DriversManager(BashPhaseManager):
    """Drivers + firmware (NVIDIA via apt, firmware via fwupd).

    Bridges to the legacy 5 phase scripts under top-level ``scripts/drivers/``.
    """

    CATEGORY: ClassVar[SourceType] = SourceType.DRIVERS
    SCRIPT_DIR: ClassVar[str] = "drivers"
    DISPLAY_NAME: ClassVar[str] = "Drivers + firmware (NVIDIA, fwupd)"
    # No single binary owns this category — the scripts dispatch to whichever
    # of `ubuntu-drivers`, `apt-mark`, or `fwupdmgr` is present. Override
    # is_available() to gate on Linux only.
    AVAILABILITY_BINARIES: ClassVar[tuple[str, ...]] = ()

    def is_available(self, host: HostInfo) -> bool:
        # Drivers script gracefully degrades when individual tools are
        # missing — it doesn't crash if fwupdmgr or ubuntu-drivers is
        # absent, just skips that subsection. Gate on Linux family only.
        return host.os.value.startswith("linux_")
