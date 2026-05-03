"""ascendo-macos — Tier 1 adapter for macOS.

M5.1 capability: PACKAGE_MANAGEMENT (Homebrew). Other capabilities
(inventory, snapshots, scheduler, elevation, source) ship in M5.2-M5.5.
"""
from __future__ import annotations

from .adapter import MacOSAdapter

__all__ = ["MacOSAdapter"]
