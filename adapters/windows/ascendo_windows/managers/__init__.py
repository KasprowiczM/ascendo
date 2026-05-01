"""Per-source package manager implementations for Windows."""
from __future__ import annotations

from .winget import WingetManager

__all__ = ["WingetManager"]
