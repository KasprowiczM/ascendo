"""AptManager - IPackageManager wrapping ``scripts/apt/<phase>.sh``."""
from __future__ import annotations

from typing import ClassVar

from ascendo.models.package import SourceType

from ._base import BashPhaseManager


class AptManager(BashPhaseManager):
    """Advanced Package Tool (system packages on Debian/Ubuntu).

    Bridges to the legacy 5 phase scripts under top-level ``scripts/apt/``.
    """

    CATEGORY: ClassVar[SourceType] = SourceType.APT
    SCRIPT_DIR: ClassVar[str] = "apt"
    DISPLAY_NAME: ClassVar[str] = "Advanced Package Tool (apt)"
    AVAILABILITY_BINARIES: ClassVar[tuple[str, ...]] = ("apt-get", "apt")
