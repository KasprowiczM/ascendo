"""Smoke tests for :meth:`WindowsAdapter.health_check`.

Mock-based; deliberately does NOT spawn pwsh, winget, npm, pip, or open
real SQLite. Verifies that ``ascendo doctor`` on Windows surfaces the
expanded component set (npm, pip, inventory_db) added for parity with
the macOS adapter's 12-component health rollup.

Pattern mirrors test_inventory_smoke.py / test_msstore_arp_smoke.py —
monkey-patches ``shutil.which`` + ``subprocess.run`` to control which
binaries appear "installed" on the test host.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from ascendo_windows.adapter import WindowsAdapter


# ── Component presence ─────────────────────────────────────────────────


def test_health_check_includes_npm_component() -> None:
    """npm probe must be reported (parity with macOS NpmManager health
    component)."""
    result = WindowsAdapter().health_check()
    assert "npm" in result


def test_health_check_includes_pip_component() -> None:
    """pip probe must be reported (parity with macOS PipManager health
    component)."""
    result = WindowsAdapter().health_check()
    assert "pip" in result


def test_health_check_includes_inventory_db_component() -> None:
    """inventory_db cache probe must be reported."""
    result = WindowsAdapter().health_check()
    assert "inventory_db" in result


def test_health_check_has_at_least_eight_components() -> None:
    """The Windows health rollup should report at least 8 components
    after this expansion (was 5 pre-fix: winget / pswindowsupdate / pwsh
    / ascendo_lib / ascendo_scripts). Targets parity with macOS's 12."""
    result = WindowsAdapter().health_check()
    assert len(result) >= 8, (
        f"expected >= 8 components, got {len(result)}: {sorted(result)}"
    )


# ── npm probe behaviour ─────────────────────────────────────────────────


def test_health_check_npm_degraded_when_missing() -> None:
    """When npm + npm.cmd are absent from PATH, the probe must report
    ``unavailable`` (a graceful "not installed" signal — npm globals is
    optional infrastructure)."""

    original_which = __import__("shutil").which

    def _fake_which(name: str) -> str | None:
        if name in ("npm", "npm.cmd"):
            return None
        return original_which(name)

    with patch("ascendo_windows.adapter.shutil.which", side_effect=_fake_which):
        result = WindowsAdapter().health_check()

    npm_status = result["npm"]
    assert npm_status.startswith("unavailable"), (
        f"expected npm status to start with 'unavailable' when missing, "
        f"got: {npm_status!r}"
    )


# ── inventory_db probe behaviour ────────────────────────────────────────


def test_health_check_inventory_db_degraded_when_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the SQLite cache file doesn't exist, the probe must report
    ``unavailable`` with an actionable hint to run ``ascendo build-inventory``.
    Honours ``$ASCENDO_INVENTORY_DB`` env override (same path the dashboard
    + CLI use)."""
    missing_db = tmp_path / "nonexistent" / "inventory.db"
    assert not missing_db.exists()  # sanity

    monkeypatch.setenv("ASCENDO_INVENTORY_DB", str(missing_db))

    result = WindowsAdapter().health_check()
    inv_status = result["inventory_db"]
    assert inv_status.startswith("unavailable"), (
        f"expected inventory_db status to start with 'unavailable' when "
        f"db file missing, got: {inv_status!r}"
    )
    assert "build-inventory" in inv_status, (
        f"expected hint to mention 'build-inventory', got: {inv_status!r}"
    )
