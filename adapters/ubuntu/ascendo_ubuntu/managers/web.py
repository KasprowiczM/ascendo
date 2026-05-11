"""WebManager — IPackageManager for AppImages + GitHub releases on Linux.

Bridges to the adapter-local 5-phase scripts under
``adapters/ubuntu/scripts/web/``. Unlike apt/snap/brew/etc. (which point
at the legacy top-level ``scripts/<cat>/`` tree), the web scripts ship
inside the adapter package because there is no legacy bash equivalent.

Handlers (registry-driven):
    Tier-A: github_release, release_feed, appimage
    Tier-B: builtin (trigger-only)
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import ClassVar

from ascendo.models.host import HostInfo
from ascendo.models.package import SourceType

from ._base import BashPhaseManager


class WebManager(BashPhaseManager):
    """Web app updater for Linux.

    Args mirror BashPhaseManager but `scripts_dir` MUST point at the
    adapter-local script tree (``adapters/ubuntu/scripts/``), not the
    legacy top-level ``scripts/`` — there are no top-level web scripts.
    """

    CATEGORY: ClassVar[SourceType] = SourceType.WEB
    SCRIPT_DIR: ClassVar[str] = "web"
    DISPLAY_NAME: ClassVar[str] = "Web apps (AppImage + GitHub releases)"
    AVAILABILITY_BINARIES: ClassVar[tuple[str, ...]] = ("curl",)

    def is_available(self, host: HostInfo) -> bool:
        """True iff Linux + curl on PATH + shipped registry parses."""
        if not super().is_available(host):
            return False
        # Probe the shipped registry to make sure it loads cleanly.
        shipped = self._scripts_dir.parent / "config" / "web_apps.toml"
        if not shipped.is_file():
            return False
        try:
            from ascendo_ubuntu.web_registry import WebRegistry
            user = Path("~/.config/ascendo/web_apps.toml").expanduser()
            WebRegistry.load(shipped, user if user.exists() else None)
        except Exception:  # noqa: BLE001
            return False
        # Make sure handlers + discovery script are present.
        for h in ("github_release.sh", "release_feed.sh",
                  "appimage.sh", "builtin.sh"):
            if not (self._lib_dir / "handlers" / h).is_file():
                return False
        if not (self._lib_dir / "web_discovery.sh").is_file():
            return False
        return shutil.which("curl") is not None
