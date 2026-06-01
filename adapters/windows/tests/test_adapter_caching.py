"""A2/A3: WindowsAdapter must cache its sub-interface singletons.

Audit ASCENDO_ULTRA_REVIEW_2 sec.3/sec.4: ``WindowsAdapter`` previously
constructed a NEW ``WindowsInventory`` / ``WindowsSnapshot`` /
``WindowsScheduler`` / ``WindowsElevation`` on every accessor call. The
in-memory elevation token is per-instance, so a password registered on the
object one route fetched was invisible to a manager that fetched another —
fragile. macOS caches singletons (``adapters/macos/.../adapter.py:107,
192-194``); Windows must match.
"""
from __future__ import annotations

from ascendo_windows.adapter import WindowsAdapter


def test_adapter_caches_sub_interfaces() -> None:
    """Each accessor returns the SAME object across calls (identity)."""
    a = WindowsAdapter()
    assert a.inventory() is a.inventory()
    assert a.snapshot() is a.snapshot()
    assert a.scheduler() is a.scheduler()
    assert a.elevation() is a.elevation()


def test_elevation_state_visible_across_accessor_calls() -> None:
    """A token registered via one ``elevation()`` accessor is visible on the
    next — the whole point of caching the singleton."""
    a = WindowsAdapter()
    a.elevation().register_allowlist(["winget.exe"])
    # A later accessor must see the same allow-list (same object).
    assert "winget.exe" in a.elevation()._allowlist
