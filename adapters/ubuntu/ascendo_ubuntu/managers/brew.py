"""BrewManager (Linux) - IPackageManager wrapping ``scripts/brew/<phase>.sh``.

Linuxbrew lives at ``/home/linuxbrew/.linuxbrew/bin/brew`` by default.
The legacy bash scripts already handle this prefix; we just need to
detect availability when ``brew`` isn't on the user's PATH.
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import ClassVar

from ascendo.models.host import HostInfo
from ascendo.models.package import SourceType

from ._base import BashPhaseManager


_LINUXBREW_PREFIX = Path("/home/linuxbrew/.linuxbrew/bin/brew")


class BrewManager(BashPhaseManager):
    """Homebrew on Linux (Linuxbrew).

    Bridges to the legacy 5 phase scripts under top-level ``scripts/brew/``.
    """

    CATEGORY: ClassVar[SourceType] = SourceType.BREW
    SCRIPT_DIR: ClassVar[str] = "brew"
    DISPLAY_NAME: ClassVar[str] = "Homebrew (Linuxbrew)"
    AVAILABILITY_BINARIES: ClassVar[tuple[str, ...]] = ("brew",)

    def is_available(self, host: HostInfo) -> bool:
        if not host.os.value.startswith("linux_"):
            return False
        # PATH lookup first, then the canonical Linuxbrew prefix.
        if shutil.which("brew") is not None:
            return True
        return _LINUXBREW_PREFIX.is_file()
