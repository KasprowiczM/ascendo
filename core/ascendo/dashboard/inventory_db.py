"""Persistent SQLite cache for inventory rows.

Replaces the previous "scan-on-every-page-load" model with a small SQLite
DB at ``~/.ascendo/inventory.db``. The DB is updated:

* whenever ``POST /inventory/refresh`` or ``POST /inventory/db/refresh``
  is called, OR
* automatically after every async run finishes (the worker thread walks
  freshly-written sidecars and bulk-upserts their items so the next
  navigation reflects what just changed).

The DB serves as the single source of truth for both the **Categories**
tab (``GET /inventory*``) AND the **Apps** tab (``GET /apps/detect``),
finally giving them parity (the user-facing bug being fixed in the same
commit was: brew shows 143 formulae in Categories but 1 row in Apps).

Schema is migrated idempotently on every open. All times are ISO 8601
UTC. Each method opens its own connection so the DB is safe across the
multi-thread worker model used by uvicorn.
"""
from __future__ import annotations

import json as _json
import logging
import sqlite3
import threading
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)


_SCHEMA_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS inventory_items (
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
    """,
    "CREATE INDEX IF NOT EXISTS ix_inventory_status ON inventory_items(status)",
    "CREATE INDEX IF NOT EXISTS ix_inventory_category ON inventory_items(category)",
    """
    CREATE TABLE IF NOT EXISTS inventory_meta (
        adapter      TEXT PRIMARY KEY,
        last_scan_at TEXT,
        item_count   INTEGER
    )
    """,
)


def _utcnow() -> str:
    """Return a stable UTC ISO8601 timestamp (no microseconds)."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    """Convert a DB row to the SPA-friendly dict shape.

    The ``metadata`` column stores opaque JSON; we decode it lazily so
    callers that don't need it pay nothing. Decode failure falls back
    to an empty dict (the row is still useful).
    """
    out: dict[str, Any] = {
        "category": row["category"],
        "name": row["name"],
        "installed": row["installed"],
        "candidate": row["candidate"],
        "status": row["status"],
        "source_type": row["source_type"],
        "vendor": row["vendor"],
        "updated_at": row["updated_at"],
    }
    raw = row["metadata"]
    if raw:
        try:
            out["metadata"] = _json.loads(raw)
        except (_json.JSONDecodeError, TypeError):
            out["metadata"] = {}
    else:
        out["metadata"] = {}
    return out


class InventoryDB:
    """Tiny SQLite-backed inventory cache.

    Thread-safety: each method opens its own connection inside its
    scope. SQLite supports concurrent readers + a single writer; the
    bulk_upsert path wraps the whole batch in one transaction so even
    a 400-item brew dump only takes a single fsync.
    """

    def __init__(self, path: Path) -> None:
        self._path: Path = path
        # Migration is idempotent and cheap (CREATE IF NOT EXISTS); we
        # run it once at construction so the rest of the API can assume
        # the schema exists.
        self._init_lock = threading.Lock()
        self._migrate()

    # ── connection helpers ──────────────────────────────────────────────

    def _connect(self) -> sqlite3.Connection:
        # ``check_same_thread=False`` is safe because each method opens
        # and closes its own connection — we never share a connection
        # across threads.
        self._path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(
            self._path,
            timeout=10.0,
            check_same_thread=False,
            isolation_level="DEFERRED",
        )
        conn.row_factory = sqlite3.Row
        # WAL gives the dashboard reader/writer concurrency without
        # the writer blocking the readers; harmless if the underlying
        # FS doesn't support it (SQLite silently falls back).
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
        except sqlite3.DatabaseError:  # pragma: no cover — exotic FS
            pass
        return conn

    def _migrate(self) -> None:
        with self._init_lock:
            with self._connect() as conn:
                for stmt in _SCHEMA_STATEMENTS:
                    conn.execute(stmt)
                conn.commit()

    # ── path ────────────────────────────────────────────────────────────

    @property
    def path(self) -> Path:
        return self._path

    # ── single upsert ───────────────────────────────────────────────────

    def upsert(
        self,
        category: str,
        name: str,
        *,
        installed: str | None = None,
        candidate: str | None = None,
        status: str | None = None,
        source_type: str | None = None,
        vendor: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Insert-or-update a single inventory row.

        Status defaults to ``"unknown"`` when neither ``installed`` nor
        ``candidate`` is supplied; callers that have already classified
        should pass ``status`` explicitly.
        """
        if not category or not name:
            return  # silently ignore malformed rows; never raise on bad input

        meta_blob = _json.dumps(metadata, sort_keys=True) if metadata else None
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO inventory_items
                    (category, name, installed, candidate, status,
                     source_type, vendor, metadata, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(category, name) DO UPDATE SET
                    installed   = excluded.installed,
                    candidate   = excluded.candidate,
                    status      = excluded.status,
                    source_type = excluded.source_type,
                    vendor      = excluded.vendor,
                    metadata    = excluded.metadata,
                    updated_at  = excluded.updated_at
                """,
                (
                    category,
                    name,
                    installed,
                    candidate,
                    status or "unknown",
                    source_type,
                    vendor,
                    meta_blob,
                    _utcnow(),
                ),
            )
            conn.commit()

    # ── bulk upsert ─────────────────────────────────────────────────────

    def bulk_upsert(self, rows: Iterable[dict[str, Any]]) -> int:
        """Upsert many rows inside one transaction.

        Each row dict requires ``category`` + ``name``; remaining fields
        are optional. Rows missing the two keys are silently skipped.
        Returns the number of rows actually written.
        """
        materialised: list[tuple[Any, ...]] = []
        ts = _utcnow()
        for row in rows:
            category = row.get("category")
            name = row.get("name")
            if not category or not name:
                continue
            metadata = row.get("metadata")
            meta_blob: str | None
            if metadata is None:
                meta_blob = None
            elif isinstance(metadata, str):
                # Already serialised (caller may have come from JSON);
                # accept verbatim.
                meta_blob = metadata
            else:
                try:
                    meta_blob = _json.dumps(metadata, sort_keys=True)
                except (TypeError, ValueError):
                    meta_blob = None
            materialised.append(
                (
                    str(category),
                    str(name),
                    row.get("installed"),
                    row.get("candidate"),
                    row.get("status") or "unknown",
                    row.get("source_type"),
                    row.get("vendor"),
                    meta_blob,
                    ts,
                ),
            )
        if not materialised:
            return 0
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT INTO inventory_items
                    (category, name, installed, candidate, status,
                     source_type, vendor, metadata, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(category, name) DO UPDATE SET
                    installed   = excluded.installed,
                    candidate   = excluded.candidate,
                    status      = excluded.status,
                    source_type = excluded.source_type,
                    vendor      = excluded.vendor,
                    metadata    = excluded.metadata,
                    updated_at  = excluded.updated_at
                """,
                materialised,
            )
            conn.commit()
        return len(materialised)

    # ── queries ─────────────────────────────────────────────────────────

    def query(
        self,
        *,
        category: str | None = None,
        status: str | None = None,
        search: str | None = None,
    ) -> list[dict[str, Any]]:
        """Read rows back, optionally filtered.

        ``search`` is a case-insensitive ``LIKE %term%`` over the
        ``name`` column.  Missing rows return ``[]`` — never raise on
        an empty DB.
        """
        clauses: list[str] = []
        params: list[Any] = []
        if category:
            clauses.append("category = ?")
            params.append(category)
        if status:
            clauses.append("status = ?")
            params.append(status)
        if search:
            clauses.append("LOWER(name) LIKE ?")
            params.append(f"%{search.lower()}%")
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = (
            "SELECT category, name, installed, candidate, status,"
            " source_type, vendor, metadata, updated_at"
            f" FROM inventory_items{where}"
            " ORDER BY category, name"
        )
        with self._connect() as conn:
            cursor = conn.execute(sql, tuple(params))
            return [_row_to_dict(r) for r in cursor.fetchall()]

    def categories(self) -> list[str]:
        """Distinct category list (sorted)."""
        with self._connect() as conn:
            cursor = conn.execute(
                "SELECT DISTINCT category FROM inventory_items ORDER BY category",
            )
            return [r[0] for r in cursor.fetchall()]

    def count(self, *, category: str | None = None) -> int:
        """Row count, optionally restricted to one category."""
        with self._connect() as conn:
            if category:
                cursor = conn.execute(
                    "SELECT COUNT(*) FROM inventory_items WHERE category = ?",
                    (category,),
                )
            else:
                cursor = conn.execute("SELECT COUNT(*) FROM inventory_items")
            row = cursor.fetchone()
            return int(row[0]) if row else 0

    # ── meta (last-scan bookkeeping) ────────────────────────────────────

    def get_meta(self, adapter: str) -> dict[str, Any] | None:
        """Return ``{adapter, last_scan_at, item_count}`` or ``None``."""
        with self._connect() as conn:
            cursor = conn.execute(
                "SELECT adapter, last_scan_at, item_count FROM inventory_meta"
                " WHERE adapter = ?",
                (adapter,),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            return {
                "adapter": row["adapter"],
                "last_scan_at": row["last_scan_at"],
                "item_count": row["item_count"],
            }

    def set_meta(
        self,
        adapter: str,
        *,
        last_scan_at: str | None = None,
        item_count: int = 0,
    ) -> None:
        """Persist scan timestamp + item count for ``adapter``."""
        ts = last_scan_at or _utcnow()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO inventory_meta (adapter, last_scan_at, item_count)
                VALUES (?, ?, ?)
                ON CONFLICT(adapter) DO UPDATE SET
                    last_scan_at = excluded.last_scan_at,
                    item_count   = excluded.item_count
                """,
                (adapter, ts, item_count),
            )
            conn.commit()

    # ── deletion ────────────────────────────────────────────────────────

    def clear_category(self, category: str) -> int:
        """Remove every row for ``category``. Returns rows deleted."""
        if not category:
            return 0
        with self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM inventory_items WHERE category = ?",
                (category,),
            )
            deleted = cursor.rowcount or 0
            conn.commit()
            return deleted

    def clear_all(self) -> None:
        """Wipe both tables (useful for tests + ``Reset cache`` UX)."""
        with self._connect() as conn:
            conn.execute("DELETE FROM inventory_items")
            conn.execute("DELETE FROM inventory_meta")
            conn.commit()

    # ── lifecycle ───────────────────────────────────────────────────────

    def close(self) -> None:
        """No-op: each method opens + closes its own connection. Kept so
        callers (FastAPI lifespan teardown) can call ``.close()`` without
        blowing up."""


# ── DB-freshness helpers ─────────────────────────────────────────────────


_DEFAULT_FRESH_SECONDS = 24 * 3600


def is_fresh(meta: dict[str, Any] | None, *, max_age_seconds: int = _DEFAULT_FRESH_SECONDS) -> bool:
    """Return True iff ``meta['last_scan_at']`` is within ``max_age_seconds``."""
    if not meta:
        return False
    raw = meta.get("last_scan_at")
    if not raw:
        return False
    try:
        # ``fromisoformat`` accepts "+00:00" but not "Z"; normalise.
        ts = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return False
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - ts).total_seconds()
    return age <= max_age_seconds


__all__ = [
    "InventoryDB",
    "is_fresh",
]
