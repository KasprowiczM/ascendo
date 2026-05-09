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
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
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
    # Per-app update history: every apply/verify run that produced a real
    # version transition records one row here. Powers the "show this app's
    # update history" UX in the Apps view (e.g. "Docker: 4.71->4.72 last
    # Tuesday, 4.70->4.71 a week before").
    """
    CREATE TABLE IF NOT EXISTS update_history (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        category      TEXT NOT NULL,
        name          TEXT NOT NULL,
        from_version  TEXT,
        to_version    TEXT NOT NULL,
        status        TEXT NOT NULL,
        run_id        TEXT NOT NULL,
        applied_at    TEXT NOT NULL,
        handler       TEXT,
        notes         TEXT,
        UNIQUE (category, name, run_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_update_history_app"
    " ON update_history(category, name, applied_at DESC)",
    "CREATE INDEX IF NOT EXISTS ix_update_history_run"
    " ON update_history(run_id)",
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

    @contextmanager
    def _connect(self) -> "Iterator[sqlite3.Connection]":
        """Open a SQLite connection and ALWAYS close it on context exit.

        Critical: Python's built-in ``with conn:`` only commits/rolls back
        the transaction — it does NOT close the connection. Every call
        that did ``with self._connect() as conn:`` was leaking one file
        descriptor per invocation. With the dashboard's SSE polling +
        per-request inventory queries, that's tens of leaked fds per
        second; the user's box hit ``[Errno 24] Too many open files``
        within ~7 minutes. This context-manager wrapper guarantees
        ``conn.close()`` runs on EVERY exit path (commit, rollback, or
        exception). Sesja 49 fix.
        """
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
        try:
            yield conn
            # Caller used `with self._connect() as conn:` so any pending
            # transaction was committed by the inner sqlite3 ctxmgr. We
            # also commit explicitly here in case the caller did not use
            # the inner ctxmgr (defensive).
            try:
                conn.commit()
            except sqlite3.DatabaseError:
                pass
        except BaseException:
            try:
                conn.rollback()
            except sqlite3.DatabaseError:
                pass
            raise
        finally:
            try:
                conn.close()
            except sqlite3.DatabaseError:  # pragma: no cover
                pass

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

    # ── update history ──────────────────────────────────────────────────

    # Items with these statuses didn't actually change versions and so
    # should never produce update_history rows.
    _NO_TRANSITION_STATUSES: frozenset[str] = frozenset({
        "skipped", "up_to_date", "planned", "unknown",
    })

    def record_update_history(
        self,
        *,
        category: str,
        name: str,
        from_version: str | None,
        to_version: str,
        status: str,
        run_id: str,
        applied_at: str | None = None,
        handler: str | None = None,
        notes: str | None = None,
    ) -> bool:
        """Insert (or replace) one update_history row.

        Idempotent on ``(category, name, run_id)`` — running the same
        apply twice with the same run_id won't double up history.
        Returns True iff a row was written, False on malformed input.
        """
        if not category or not name or not run_id or status is None:
            return False
        ts = applied_at or _utcnow()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO update_history
                    (category, name, from_version, to_version,
                     status, run_id, applied_at, handler, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(category, name, run_id) DO UPDATE SET
                    from_version = excluded.from_version,
                    to_version   = excluded.to_version,
                    status       = excluded.status,
                    applied_at   = excluded.applied_at,
                    handler      = excluded.handler,
                    notes        = excluded.notes
                """,
                (
                    str(category), str(name),
                    from_version, str(to_version),
                    str(status), str(run_id), ts,
                    handler, notes,
                ),
            )
            conn.commit()
            return True

    def get_update_history(
        self,
        category: str,
        name: str,
        *,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Return up to ``limit`` history rows for one app, newest first."""
        if not category or not name:
            return []
        limit = max(1, min(int(limit), 500))
        with self._connect() as conn:
            cursor = conn.execute(
                """
                SELECT category, name, from_version, to_version,
                       status, run_id, applied_at, handler, notes
                FROM update_history
                WHERE category = ? AND name = ?
                ORDER BY applied_at DESC, id DESC
                LIMIT ?
                """,
                (category, name, limit),
            )
            return [
                {
                    "category": r["category"],
                    "name": r["name"],
                    "from_version": r["from_version"],
                    "to_version": r["to_version"],
                    "status": r["status"],
                    "run_id": r["run_id"],
                    "applied_at": r["applied_at"],
                    "handler": r["handler"],
                    "notes": r["notes"],
                }
                for r in cursor.fetchall()
            ]

    def update_history_to_version(
        self,
        *,
        category: str,
        name: str,
        run_id: str,
        to_version: str,
    ) -> bool:
        """Backfill the to_version on an existing history row.

        Used by the verify-phase post-pass so that ``triggered`` items
        whose vendor agent later reconciled get a real to_version
        instead of the empty string we wrote at apply time.
        """
        if not category or not name or not run_id or not to_version:
            return False
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE update_history
                SET to_version = ?
                WHERE category = ? AND name = ? AND run_id = ?
                """,
                (str(to_version), category, name, run_id),
            )
            conn.commit()
            return (cursor.rowcount or 0) > 0

    def history_count(
        self,
        *,
        category: str | None = None,
        name: str | None = None,
    ) -> int:
        """Row count over update_history, optionally filtered."""
        clauses: list[str] = []
        params: list[Any] = []
        if category:
            clauses.append("category = ?")
            params.append(category)
        if name:
            clauses.append("name = ?")
            params.append(name)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        with self._connect() as conn:
            cursor = conn.execute(
                f"SELECT COUNT(*) FROM update_history{where}",
                tuple(params),
            )
            row = cursor.fetchone()
            return int(row[0]) if row else 0

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


# ── apply-phase history flush ────────────────────────────────────────────


# Statuses where the item DID change version state (success +
# failed both record a transition for the audit trail; triggered
# logs the kick-off and verify-phase backfills the to_version).
_HISTORY_RECORD_STATUSES: frozenset[str] = frozenset({
    "success", "failed", "triggered", "missing",
})


def _coerce_str(value: Any) -> str | None:
    """Best-effort: stringify scalar values, return None for empties."""
    if value is None:
        return None
    if hasattr(value, "value"):  # Enum
        value = value.value
    text = str(value).strip()
    return text or None


def _extract_handler(item: Any) -> str | None:
    """Pull a sub-classifier (sparkle / release_feed / etc.) off the item.

    The web-handler hint lives in different places depending on the
    Pydantic Item model vs the raw sidecar dict, so probe a few.
    """
    for attr in ("handler", "method"):
        val = getattr(item, attr, None)
        if val is not None:
            return _coerce_str(val)
    metadata = getattr(item, "metadata", None)
    if isinstance(metadata, dict):
        for key in ("handler", "method", "updater"):
            if key in metadata and metadata[key]:
                return _coerce_str(metadata[key])
    return None


def flush_apply_history(
    run_dir: Path,
    run_id: str,
    db: "InventoryDB | None",
) -> int:
    """Walk a finished run's apply sidecars and record version transitions.

    Per spec: skip items where status in {skipped, up_to_date, planned};
    record one row per item with status in {success, failed, triggered,
    missing}. For triggered, to_version is left empty — the verify pass
    backfills it via :func:`backfill_triggered_history`.

    Best-effort: failures are logged + swallowed. Returns the count of
    rows written.
    """
    if db is None or not run_id or not run_dir.is_dir():
        return 0
    try:
        from ..orchestrator.sidecar_io import list_run_sidecars, read_sidecar
    except Exception:  # noqa: BLE001
        return 0
    written = 0
    applied_at = _utcnow()
    for sidecar_path in list_run_sidecars(run_dir):
        try:
            sc = read_sidecar(sidecar_path)
        except Exception:  # noqa: BLE001
            continue
        # Only apply phase produces transitions worth recording.
        phase_obj = getattr(sc, "phase", None)
        phase = phase_obj.value if hasattr(phase_obj, "value") else str(phase_obj)
        if phase != "apply":
            continue
        category_obj = getattr(sc, "category", None)
        category = (
            category_obj.value if hasattr(category_obj, "value") else str(category_obj)
        )
        if not category:
            continue
        for item in (getattr(sc, "items", None) or []):
            status_obj = getattr(item, "status", None)
            status = (
                status_obj.value if hasattr(status_obj, "value") else str(status_obj)
            )
            if not status or status not in _HISTORY_RECORD_STATUSES:
                continue
            name = (
                _coerce_str(getattr(item, "name", None))
                or _coerce_str(getattr(item, "id", None))
            )
            if not name:
                continue
            current = _coerce_str(getattr(item, "current_version", None))
            target = (
                _coerce_str(getattr(item, "resolved_version", None))
                or _coerce_str(getattr(item, "target_version", None))
            )
            if status == "triggered":
                # Apply kicked off the vendor agent; the new version
                # isn't observable until verify catches up.
                to_version = ""
            elif status == "failed":
                # Record the attempt with the intended target so the
                # operator sees what was supposed to happen + can debug.
                to_version = target or current or ""
            elif status == "missing":
                # Initial install: from is None, to is the just-installed.
                current = None
                to_version = target or ""
            else:  # success
                to_version = target or current or ""
            if to_version is None:
                to_version = ""
            try:
                ok = db.record_update_history(
                    category=category,
                    name=name,
                    from_version=current,
                    to_version=to_version,
                    status=status,
                    run_id=str(run_id),
                    applied_at=applied_at,
                    handler=_extract_handler(item),
                )
                if ok:
                    written += 1
            except Exception:  # noqa: BLE001
                _log.exception(
                    "update_history: record failed for %s:%s", category, name,
                )
    return written


def backfill_triggered_history(
    run_dir: Path,
    run_id: str,
    db: "InventoryDB | None",
) -> int:
    """After verify, fill in to_version for any triggered rows that
    landed with an empty to_version.

    The verify phase re-reads installed CFBundleShortVersionString /
    package version. If it now reports a non-empty resolved_version OR
    a current_version newer than what apply saw, that's the post-
    reconciliation truth — copy it into the matching update_history row.
    Returns rows touched.
    """
    if db is None or not run_id or not run_dir.is_dir():
        return 0
    try:
        from ..orchestrator.sidecar_io import list_run_sidecars, read_sidecar
    except Exception:  # noqa: BLE001
        return 0
    touched = 0
    for sidecar_path in list_run_sidecars(run_dir):
        try:
            sc = read_sidecar(sidecar_path)
        except Exception:  # noqa: BLE001
            continue
        phase_obj = getattr(sc, "phase", None)
        phase = phase_obj.value if hasattr(phase_obj, "value") else str(phase_obj)
        if phase != "verify":
            continue
        category_obj = getattr(sc, "category", None)
        category = (
            category_obj.value if hasattr(category_obj, "value") else str(category_obj)
        )
        if not category:
            continue
        for item in (getattr(sc, "items", None) or []):
            name = (
                _coerce_str(getattr(item, "name", None))
                or _coerce_str(getattr(item, "id", None))
            )
            if not name:
                continue
            new_version = (
                _coerce_str(getattr(item, "resolved_version", None))
                or _coerce_str(getattr(item, "current_version", None))
            )
            if not new_version:
                continue
            try:
                if db.update_history_to_version(
                    category=category,
                    name=name,
                    run_id=str(run_id),
                    to_version=new_version,
                ):
                    touched += 1
            except Exception:  # noqa: BLE001
                _log.exception(
                    "update_history: backfill failed for %s:%s", category, name,
                )
    return touched


__all__ = [
    "InventoryDB",
    "is_fresh",
    "flush_apply_history",
    "backfill_triggered_history",
]
