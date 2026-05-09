"""PipManager - IPackageManager wrapping ``scripts/pip/<phase>.sh``."""
from __future__ import annotations

from typing import ClassVar

from ascendo.models.package import SourceType

from ._base import BashPhaseManager


class PipManager(BashPhaseManager):
    """Python global packages (pip + pipx).

    Bridges to the legacy 5 phase scripts under top-level ``scripts/pip/``.
    """

    CATEGORY: ClassVar[SourceType] = SourceType.PIP
    SCRIPT_DIR: ClassVar[str] = "pip"
    DISPLAY_NAME: ClassVar[str] = "Python global packages (pip + pipx)"
    AVAILABILITY_BINARIES: ClassVar[tuple[str, ...]] = ("pip3", "pip", "pipx")
