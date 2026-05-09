"""FlatpakManager - IPackageManager wrapping ``scripts/flatpak/<phase>.sh``."""
from __future__ import annotations

from typing import ClassVar

from ascendo.models.package import SourceType

from ._base import BashPhaseManager


class FlatpakManager(BashPhaseManager):
    """Flatpak (Linux universal packages).

    Bridges to the legacy 5 phase scripts under top-level ``scripts/flatpak/``.
    """

    CATEGORY: ClassVar[SourceType] = SourceType.FLATPAK
    SCRIPT_DIR: ClassVar[str] = "flatpak"
    DISPLAY_NAME: ClassVar[str] = "Flatpak"
    AVAILABILITY_BINARIES: ClassVar[tuple[str, ...]] = ("flatpak",)
