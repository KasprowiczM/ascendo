"""SnapManager - IPackageManager wrapping ``scripts/snap/<phase>.sh``."""
from __future__ import annotations

from typing import ClassVar

from ascendo.models.package import SourceType

from ._base import BashPhaseManager


class SnapManager(BashPhaseManager):
    """Canonical's Snap package manager.

    Bridges to the legacy 5 phase scripts under top-level ``scripts/snap/``.
    """

    CATEGORY: ClassVar[SourceType] = SourceType.SNAP
    SCRIPT_DIR: ClassVar[str] = "snap"
    DISPLAY_NAME: ClassVar[str] = "Snap (Canonical universal packages)"
    AVAILABILITY_BINARIES: ClassVar[tuple[str, ...]] = ("snap",)
