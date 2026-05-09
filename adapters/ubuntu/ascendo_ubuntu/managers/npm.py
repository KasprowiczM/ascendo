"""NpmManager - IPackageManager wrapping ``scripts/npm/<phase>.sh``."""
from __future__ import annotations

from typing import ClassVar

from ascendo.models.package import SourceType

from ._base import BashPhaseManager


class NpmManager(BashPhaseManager):
    """npm globals (Node + global CLIs).

    Bridges to the legacy 5 phase scripts under top-level ``scripts/npm/``.
    """

    CATEGORY: ClassVar[SourceType] = SourceType.NPM
    SCRIPT_DIR: ClassVar[str] = "npm"
    DISPLAY_NAME: ClassVar[str] = "npm globals (Node toolchain)"
    AVAILABILITY_BINARIES: ClassVar[tuple[str, ...]] = ("npm",)
