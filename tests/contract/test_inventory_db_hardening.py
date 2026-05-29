"""Pass B: Inventory hardening tests [I9/I2/D4/D8/D11/I5/I8/D3/T7].

TEST-FIRST: these tests are written to fail against the current code,
then the corresponding inventory_db.py + run_async.py changes make them
green.

Each test's docstring cites the finding ID from ASCENDO_ULTRA_REVIEW §5.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from ascendo.dashboard.inventory_db import InventoryDB, is_fresh


# ── I9: Per-category scan-complete watermark ──────────────────────────


class TestI9ScanFreshness:
    """I9: ``bulk_upsert`` never calls ``set_meta`` — after a partial
    post-run flush, ``last_scan_at`` stays old and ``is_fresh()`` can
    serve a partial DB as "fresh."

    Fix: add ``set_scan_complete(category)`` called only by full live-
    scans. ``is_fresh()`` keys on scan-complete, not last-write.
    """

    def test_partial_flush_does_not_advance_scan_freshness(
        self, tmp_path: Path,
    ) -> None:
        """A post-run flush (bulk_upsert only) must NOT make is_fresh()
        return True — the DB still hasn't had a full live-scan."""
        db = InventoryDB(tmp_path / "inv.db")
        # No set_meta, no set_scan_complete — just a bulk_upsert.
        db.bulk_upsert([
            {"category": "brew", "name": "wget", "installed": "1.0"},
        ])
        meta = db.get_meta("fake-adapter")
        assert not is_fresh(meta), (
            "I9: is_fresh must NOT be True after a bare bulk_upsert "
            "without a full-scan watermark"
        )

    def test_set_scan_complete_makes_category_fresh(
        self, tmp_path: Path,
    ) -> None:
        """Calling set_scan_complete after a full live-scan makes
        is_fresh() True for that category."""
        db = InventoryDB(tmp_path / "inv.db")
        db.bulk_upsert([
            {"category": "brew", "name": "wget", "installed": "1.0"},
        ])
        # The new method:
        db.set_scan_complete("brew", item_count=1)
        meta = db.get_scan_meta("brew")
        assert meta is not None
        assert is_fresh(meta), (
            "I9: is_fresh must be True after set_scan_complete"
        )


# ── I2/D4/D8/D11: Reconciliation routine ─────────────────────────────


class TestReconciliation:
    """I2/D4: Upsert-only flush orphans rows for apps uninstalled
    between runs. D8: delete_row exact-PK match orphans legacy
    item_id='' rows. D11: eviction count not surfaced.

    Fix: reconcile(category, seen_keys) — diff DB vs live-scan, remove
    rows not seen.
    """

    def test_reconcile_removes_unseen_rows(self, tmp_path: Path) -> None:
        """I2/D4: rows not in the live-scan set are removed."""
        db = InventoryDB(tmp_path / "inv.db")
        db.bulk_upsert([
            {"category": "brew", "name": "wget", "installed": "1.0"},
            {"category": "brew", "name": "curl", "installed": "1.0"},
            {"category": "brew", "name": "jq", "installed": "1.0"},
        ])
        assert db.count(category="brew") == 3

        # Live-scan saw only wget + curl; jq was uninstalled.
        evicted = db.reconcile("brew", seen_names={"wget", "curl"})
        assert evicted == 1
        names = {r["name"] for r in db.query(category="brew")}
        assert names == {"wget", "curl"}

    def test_reconcile_handles_empty_seen_set(self, tmp_path: Path) -> None:
        """D11: 0-item live-scan should NOT wipe the entire category —
        that's likely a discovery failure, not reality."""
        db = InventoryDB(tmp_path / "inv.db")
        db.bulk_upsert([
            {"category": "brew", "name": "wget", "installed": "1.0"},
            {"category": "brew", "name": "curl", "installed": "1.0"},
        ])
        # Empty seen_names → refuse to reconcile (safety guard).
        evicted = db.reconcile("brew", seen_names=set())
        assert evicted == 0
        assert db.count(category="brew") == 2

    def test_reconcile_returns_evicted_count(self, tmp_path: Path) -> None:
        """D11: eviction count is returned so callers can log/alert."""
        db = InventoryDB(tmp_path / "inv.db")
        db.bulk_upsert([
            {"category": "pip", "name": f"pkg{i}", "installed": "1.0"}
            for i in range(5)
        ])
        evicted = db.reconcile("pip", seen_names={"pkg0", "pkg1"})
        assert evicted == 3
        assert db.count(category="pip") == 2

    def test_reconcile_handles_legacy_empty_item_id(
        self, tmp_path: Path,
    ) -> None:
        """D8: legacy rows with item_id='' must also be eligible for
        reconciliation."""
        db = InventoryDB(tmp_path / "inv.db")
        db.bulk_upsert([
            {"category": "web", "name": "cursor", "item_id": ""},
            {"category": "web", "name": "obsidian", "item_id": ""},
        ])
        evicted = db.reconcile("web", seen_names={"obsidian"})
        assert evicted == 1
        names = {r["name"] for r in db.query(category="web")}
        assert names == {"obsidian"}


# ── I8: PRAGMA user_version + archive ─────────────────────────────────


class TestI8Migration:
    """I8: v1→v2 migration DROP TABLEs with no PRAGMA user_version
    anchor. Future migrations have no version marker.

    Fix: set PRAGMA user_version after migration; archive old table.
    """

    def test_migration_sets_user_version(self, tmp_path: Path) -> None:
        """After construction, user_version should be 2 (current schema)."""
        db = InventoryDB(tmp_path / "inv.db")
        with db._connect() as conn:
            uv = conn.execute("PRAGMA user_version").fetchone()[0]
        assert uv == 2, f"I8: expected user_version=2, got {uv}"

    def test_fresh_db_has_user_version(self, tmp_path: Path) -> None:
        """Even a brand new DB (no v1 migration) has user_version set."""
        db_path = tmp_path / "fresh.db"
        db = InventoryDB(db_path)
        with db._connect() as conn:
            uv = conn.execute("PRAGMA user_version").fetchone()[0]
        assert uv == 2


# ── D3/T7: _normalize_item_id refinement ─────────────────────────────


class TestNormalizeItemId:
    """D3/T7: refine _normalize_item_id to collapse only exact
    prefix+sep+name OR a known synthetic-prefix allowlist. Must NOT
    collapse Windows-style dotted IDs like
    'Microsoft.VCRedist.2008.x64.Runtime' / name='Runtime'.
    """

    @pytest.mark.parametrize(
        "item_id, name, expected",
        [
            # Synthetic prefixes → collapse to ""
            ("brew:wget", "wget", ""),
            ("apt:upgrade:firefox", "firefox", ""),
            ("snap:firefox", "firefox", ""),
            ("npm:typescript", "typescript", ""),
            ("pip:ruff", "ruff", ""),
            ("web:cursor", "cursor", ""),
            # Same as name → collapse
            ("wget", "wget", ""),
            # Real discriminator → keep
            (
                "Microsoft.VCRedist.2008.x64.Runtime",
                "Runtime",
                "Microsoft.VCRedist.2008.x64.Runtime",
            ),
            (
                "Microsoft.VCRedist.2008.x64.Runtime",
                "Microsoft Visual C++ 2008 Redistributable",
                "Microsoft.VCRedist.2008.x64.Runtime",
            ),
            # Different suffix → keep
            ("firefox-bin", "firefox", "firefox-bin"),
            # Empty → collapse
            ("", "wget", ""),
            (None, "wget", ""),
        ],
        ids=[
            "brew-prefix",
            "apt-compound-prefix",
            "snap-prefix",
            "npm-prefix",
            "pip-prefix",
            "web-prefix",
            "same-as-name",
            "dotted-real-discriminator",
            "dotted-completely-different-name",
            "different-suffix",
            "empty-string",
            "none-value",
        ],
    )
    def test_normalize_item_id_cases(
        self, item_id: str | None, name: str, expected: str,
    ) -> None:
        from ascendo.orchestrator.run_async import _normalize_item_id

        result = _normalize_item_id(item_id, name)
        assert result == expected, (
            f"_normalize_item_id({item_id!r}, {name!r}): "
            f"expected {expected!r}, got {result!r}"
        )
