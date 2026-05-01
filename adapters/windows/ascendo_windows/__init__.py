"""Ascendo Windows adapter (Tier 1).

Implements the IAdapter interface for Windows. Driven by native PowerShell
scripts in ../scripts/ which emit JSON v1 sidecars; this module parses them
back into Python models.

M3 MVP scope: winget read-only check phase. plan/apply/verify/cleanup,
inventory, snapshots (VSS), scheduling (Task Scheduler), elevation (UAC)
will land in subsequent milestones.
"""
from __future__ import annotations

from .adapter import WindowsAdapter
from .managers.winget import WingetManager

__all__ = ["WindowsAdapter", "WingetManager"]
