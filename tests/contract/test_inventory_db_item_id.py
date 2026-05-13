"""Regression tests for Sesja 67's inventory_db v2 schema (item_id PK).

Operator regression on DP5520WMK: the SPA's Categories tab showed
78 msstore packages, but `winget list --source msstore` showed 95.
17 packages whose DisplayName collided across architectures
(MSIX x86 + x64 + arm64 of the same package) were silently dropped
because the old PK was `(category, name)`.

Sesja 67 widens the PK to `(category, name, item_id)`. Multi-arch
packages now each keep their own row. Pre-v2 DBs migrate by dropping
the legacy table (the next live-scan or post-run flush repopulates
within seconds — momentarily flicker, not data loss).
"""
from __future__ import annotations

import sys
import sqlite3
from pathlib import Path

# Force-load from THIS worktree's core/ so the test exercises the
# worktree's code, not the editable install which may point at main.
_WORKTREE_CORE = Path(__file__).resolve().parents[2] / "core"
if _WORKTREE_CORE.is_dir() and str(_WORKTREE_CORE) not in sys.path:
    sys.path.insert(0, str(_WORKTREE_CORE))

import importlib  # noqa: E402

for mod in ("ascendo.dashboard.inventory_db",):
    if mod in sys.modules:
        importlib.reload(sys.modules[mod])

from ascendo.dashboard.inventory_db import InventoryDB  # noqa: E402


def test_v2_schema_has_item_id_in_primary_key(tmp_path: Path) -> None:
    db = InventoryDB(tmp_path / "inv.db")
    with sqlite3.connect(db.path) as conn:
        sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE name='inventory_items'"
        ).fetchone()[0]
        assert "item_id" in sql, "schema must declare item_id column"
        assert "PRIMARY KEY (category, name, item_id)" in sql, (
            "PK must include item_id"
        )


def test_duplicate_names_with_distinct_item_ids_persist_as_separate_rows(
    tmp_path: Path,
) -> None:
    """The exact bug: two MSIX builds of the same package keep both rows."""
    db = InventoryDB(tmp_path / "inv.db")
    n = db.bulk_upsert([
        {"category": "msstore",
         "name": "Microsoft .Net Native Framework Package 1.2",
         "item_id": "MSIX\\Microsoft.NET.Native.Framework.1.2_x64",
         "installed": "1.2.0.0",
         "candidate": "1.2.0.0",
         "status": "ok"},
        {"category": "msstore",
         "name": "Microsoft .Net Native Framework Package 1.2",
         "item_id": "MSIX\\Microsoft.NET.Native.Framework.1.2_x86",
         "installed": "1.2.0.0",
         "candidate": "1.2.0.0",
         "status": "ok"},
    ])
    assert n == 2

    rows = db.query(category="msstore")
    assert len(rows) == 2, f"both architectures must survive; got {rows!r}"
    item_ids = sorted(r["item_id"] for r in rows)
    assert item_ids == [
        "MSIX\\Microsoft.NET.Native.Framework.1.2_x64",
        "MSIX\\Microsoft.NET.Native.Framework.1.2_x86",
    ]


def test_same_item_id_upserts_in_place(tmp_path: Path) -> None:
    """Same (category, name, item_id) on second insert updates the row."""
    db = InventoryDB(tmp_path / "inv.db")
    db.bulk_upsert([
        {"category": "web", "name": "OpenCode", "item_id": "web:opencode",
         "installed": "1.14.40", "candidate": "1.14.41", "status": "outdated"},
    ])
    db.bulk_upsert([
        {"category": "web", "name": "OpenCode", "item_id": "web:opencode",
         "installed": "1.14.41", "candidate": "1.14.41", "status": "ok"},
    ])
    rows = db.query(category="web")
    assert len(rows) == 1
    assert rows[0]["installed"] == "1.14.41"
    assert rows[0]["status"] == "ok"


def test_empty_item_id_treated_as_legacy_anonymous_row(tmp_path: Path) -> None:
    """Rows without item_id behave like pre-v2 (one slot per name)."""
    db = InventoryDB(tmp_path / "inv.db")
    db.bulk_upsert([
        {"category": "npm", "name": "typescript",
         "installed": "5.5.0", "candidate": "5.5.0", "status": "ok"},
        {"category": "npm", "name": "typescript",
         "installed": "5.6.0", "candidate": "5.6.0", "status": "ok"},
    ])
    rows = db.query(category="npm")
    assert len(rows) == 1, "empty item_id collapses, last write wins"
    assert rows[0]["installed"] == "5.6.0"


def test_v1_to_v2_migration_drops_legacy_data(tmp_path: Path) -> None:
    """Pre-v2 DBs are dropped + recreated on first open.

    The dashboard's next live-scan / post-run flush repopulates the
    table within seconds. Operator sees a flicker, never a gap (the
    SPA falls back to live IInventory.list_installed when DB is cold).
    """
    p = tmp_path / "inv.db"
    # Manually create a v1 DB
    with sqlite3.connect(p) as conn:
        conn.execute(
            """
            CREATE TABLE inventory_items (
                category    TEXT NOT NULL,
                name        TEXT NOT NULL,
                installed   TEXT,
                candidate   TEXT,
                status      TEXT,
                source_type TEXT,
                vendor      TEXT,
                metadata    TEXT,
                updated_at  TEXT NOT NULL,
                PRIMARY KEY (category, name)
            )
            """
        )
        conn.execute(
            "INSERT INTO inventory_items VALUES "
            "(?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("msstore", "Legacy Package", "1.0", "1.0", "ok",
             "msstore", None, None, "2026-01-01T00:00:00+00:00"),
        )
        conn.commit()

    # Open via the new code -> triggers v1->v2 migration
    db = InventoryDB(p)
    # Migration drops legacy rows; the SPA's next scan will repopulate.
    assert db.query() == [], (
        "v1->v2 migration must drop legacy data (next scan repopulates)"
    )
    # Schema must now have item_id PK
    with sqlite3.connect(p) as conn:
        sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE name='inventory_items'"
        ).fetchone()[0]
        assert "item_id" in sql
        assert "PRIMARY KEY (category, name, item_id)" in sql


def test_bulk_upsert_accepts_id_field_as_item_id(tmp_path: Path) -> None:
    """Callers may pass ``id`` instead of ``item_id`` (sidecar items use
    ``id``; the live-scan flatten path uses ``item_id``). Both must work.
    """
    db = InventoryDB(tmp_path / "inv.db")
    db.bulk_upsert([
        {"category": "web", "name": "Brave", "id": "web:brave",
         "installed": "1.90.121", "candidate": "1.90.121", "status": "ok"},
    ])
    rows = db.query(category="web")
    assert len(rows) == 1
    assert rows[0]["item_id"] == "web:brave"


def test_query_exposes_item_id(tmp_path: Path) -> None:
    db = InventoryDB(tmp_path / "inv.db")
    db.bulk_upsert([
        {"category": "winget", "name": "VC++ 2008", "item_id": "ARP\\X64\\{...}",
         "installed": "9.0", "candidate": "9.0", "status": "ok"},
    ])
    rows = db.query()
    assert rows[0]["item_id"] == "ARP\\X64\\{...}"
