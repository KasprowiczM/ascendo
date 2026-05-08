"""Contract tests for per-app update_history.

Covers:
  * the pure-DB API on :class:`InventoryDB` (record / get / backfill /
    count, idempotent on (category, name, run_id));
  * :func:`flush_apply_history` walking apply sidecars and recording
    only items that actually changed version state;
  * :func:`backfill_triggered_history` filling in to_version after the
    verify phase observes the vendor agent's reconciled install;
  * the ``GET /apps/{category}/{name}/history`` endpoint.

These exercise the user-visible "show this app's update history" UX
("Docker: 4.71->4.72 last Tuesday, 4.70->4.71 a week before").
"""
from __future__ import annotations

import copy
import json
from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from ascendo.dashboard import create_app
from ascendo.dashboard.inventory_db import (
    InventoryDB,
    backfill_triggered_history,
    flush_apply_history,
)
from ascendo.interfaces.adapter import AdapterCapability, IAdapter
from ascendo.interfaces.elevation import IElevation
from ascendo.interfaces.inventory import IInventory
from ascendo.interfaces.package_manager import IPackageManager
from ascendo.interfaces.scheduler import IScheduler
from ascendo.interfaces.snapshot import ISnapshot
from ascendo.interfaces.source import ISource
from ascendo.models.host import ElevationMethod, HostInfo, OperatingSystem
from ascendo.models.package import Package, SourceType
from ascendo.models.sidecar import parse_sidecar
from ascendo.orchestrator.sidecar_io import write_sidecar


# ── Pure-DB API ──────────────────────────────────────────────────────────


def test_record_update_history_round_trips(tmp_path: Path) -> None:
    """record then get returns the same row shape."""
    db = InventoryDB(tmp_path / "inv.db")
    ok = db.record_update_history(
        category="web",
        name="web:docker",
        from_version="4.71.0",
        to_version="4.72.0",
        status="success",
        run_id="run-A",
        handler="sparkle",
    )
    assert ok is True
    rows = db.get_update_history("web", "web:docker")
    assert len(rows) == 1
    r = rows[0]
    assert r["from_version"] == "4.71.0"
    assert r["to_version"] == "4.72.0"
    assert r["status"] == "success"
    assert r["run_id"] == "run-A"
    assert r["handler"] == "sparkle"
    assert r["applied_at"]  # auto-stamped


def test_record_update_history_idempotent_per_run_id(tmp_path: Path) -> None:
    """Re-recording (cat, name, run_id) replaces but never duplicates."""
    db = InventoryDB(tmp_path / "inv.db")
    db.record_update_history(
        category="brew", name="ruff",
        from_version="0.5.0", to_version="0.6.0",
        status="success", run_id="run-B",
    )
    db.record_update_history(
        category="brew", name="ruff",
        from_version="0.5.0", to_version="0.6.1",  # different to_version
        status="success", run_id="run-B",
    )
    rows = db.get_update_history("brew", "ruff")
    assert len(rows) == 1
    assert rows[0]["to_version"] == "0.6.1"


def test_get_update_history_orders_newest_first(tmp_path: Path) -> None:
    """The endpoint promises chronological newest-first ordering."""
    db = InventoryDB(tmp_path / "inv.db")
    base = datetime(2026, 5, 1, tzinfo=timezone.utc)
    for i, run in enumerate(["r1", "r2", "r3"]):
        db.record_update_history(
            category="brew", name="curl",
            from_version=f"7.{i}", to_version=f"7.{i + 1}",
            status="success", run_id=run,
            applied_at=(base + timedelta(days=i)).isoformat(),
        )
    rows = db.get_update_history("brew", "curl")
    versions = [r["to_version"] for r in rows]
    assert versions == ["7.3", "7.2", "7.1"]


def test_get_update_history_respects_limit(tmp_path: Path) -> None:
    """`limit=N` caps the response to N entries."""
    db = InventoryDB(tmp_path / "inv.db")
    for i in range(7):
        db.record_update_history(
            category="brew", name="jq",
            from_version=f"1.{i}", to_version=f"1.{i + 1}",
            status="success", run_id=f"r{i}",
            applied_at=f"2026-05-0{i + 1}T10:00:00+00:00",
        )
    assert len(db.get_update_history("brew", "jq", limit=3)) == 3
    assert len(db.get_update_history("brew", "jq", limit=20)) == 7


def test_update_history_to_version_backfills(tmp_path: Path) -> None:
    """Verify-phase backfill flips an empty to_version into a real one."""
    db = InventoryDB(tmp_path / "inv.db")
    db.record_update_history(
        category="web", name="web:keystone-app",
        from_version="100.0", to_version="",
        status="triggered", run_id="run-T",
    )
    touched = db.update_history_to_version(
        category="web", name="web:keystone-app",
        run_id="run-T", to_version="100.1",
    )
    assert touched is True
    row = db.get_update_history("web", "web:keystone-app")[0]
    assert row["to_version"] == "100.1"
    assert row["status"] == "triggered"  # status preserved


def test_record_update_history_rejects_malformed(tmp_path: Path) -> None:
    """Empty cat / name / run_id -> False, no row written."""
    db = InventoryDB(tmp_path / "inv.db")
    assert db.record_update_history(
        category="", name="x", from_version=None,
        to_version="1.0", status="success", run_id="r1",
    ) is False
    assert db.record_update_history(
        category="brew", name="", from_version=None,
        to_version="1.0", status="success", run_id="r1",
    ) is False
    assert db.history_count() == 0


# ── flush_apply_history (sidecar-walker) ─────────────────────────────────


def _load_fixture() -> dict[str, Any]:
    here = Path(__file__).resolve().parent.parent / "fixtures" / "sidecars"
    return json.loads(
        (here / "ascendo_v1_apply_winget.json").read_text(encoding="utf-8"),
    )


def _new_run_id() -> str:
    return str(uuid4())


def _retag_payload(
    payload: dict[str, Any],
    *,
    run_id: str,
    phase: str | None = None,
    items: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Deep-copy + retag the canonical fixture for a custom test scenario."""
    p = copy.deepcopy(payload)
    p["run"]["id"] = run_id
    if phase is not None:
        p["phase"] = phase
    if items is not None:
        p["items"] = items
        # Recompute the summary buckets so Pydantic validators don't trip.
        # ``missing`` items have to be folded into another bucket because
        # the canonical Summary schema doesn't have a `missing` field.
        # The Sidecar.model_validator just checks that
        # total == sum(buckets), so we count ``missing`` toward planned
        # for fixture purposes — the per-item statuses themselves are
        # what flush_apply_history reads.
        success = sum(1 for it in items if it.get("status") == "success")
        up_to_date = sum(1 for it in items if it.get("status") == "up_to_date")
        failed = sum(1 for it in items if it.get("status") == "failed")
        skipped = sum(1 for it in items if it.get("status") == "skipped")
        planned = sum(1 for it in items if it.get("status") == "planned")
        partial = sum(1 for it in items if it.get("status") == "partial")
        triggered = sum(1 for it in items if it.get("status") == "triggered")
        missing = sum(1 for it in items if it.get("status") == "missing")
        p["summary"] = {
            "total": len(items),
            "success": success,
            "up_to_date": up_to_date,
            "failed": failed,
            "skipped": skipped,
            "planned": planned + missing,  # roll missing into planned
            "partial": partial,
            "triggered": triggered,
            "duration_ms": 1000,
            "exit_code": 0,
        }
    return p


def test_flush_apply_history_records_successful_upgrades(tmp_path: Path) -> None:
    """Apply sidecar with 2 success items -> 2 history rows."""
    db = InventoryDB(tmp_path / "inv.db")
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    rid = _new_run_id()
    sc = parse_sidecar(_retag_payload(_load_fixture(), run_id=rid))
    write_sidecar(sc, base_dir=runs_dir)

    written = flush_apply_history(runs_dir / rid, rid, db)
    assert written == 2
    pwsh = db.get_update_history("winget", "PowerShell")
    fox = db.get_update_history("winget", "Firefox")
    assert pwsh and pwsh[0]["from_version"] == "7.5.0"
    assert pwsh[0]["to_version"] == "7.6.0"
    assert fox and fox[0]["to_version"] == "129.0"


def test_flush_apply_history_skips_uptodate_and_planned(tmp_path: Path) -> None:
    """Items with status=up_to_date / planned / skipped never produce rows."""
    db = InventoryDB(tmp_path / "inv.db")
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    rid = _new_run_id()
    base = _load_fixture()
    items = [
        {
            **base["items"][0],
            "status": "up_to_date",
            "current_version": "7.6.0",
            "target_version": "7.6.0",
            "resolved_version": "7.6.0",
        },
        {
            **base["items"][1],
            "status": "planned",
            "current_version": "128.0",
            "target_version": "129.0",
            "resolved_version": None,
        },
    ]
    sc = parse_sidecar(_retag_payload(base, run_id=rid, items=items))
    write_sidecar(sc, base_dir=runs_dir)

    written = flush_apply_history(runs_dir / rid, rid, db)
    assert written == 0
    assert db.history_count() == 0


def test_flush_apply_history_records_triggered_with_empty_to_version(
    tmp_path: Path,
) -> None:
    """Tier-B `triggered` items get to_version="" pending verify backfill."""
    db = InventoryDB(tmp_path / "inv.db")
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    rid = _new_run_id()
    base = _load_fixture()
    items = [
        {
            **base["items"][0],
            "id": "web:keystone-app",
            "name": "web:keystone-app",
            "status": "triggered",
            "current_version": "100.0",
            "target_version": None,
            "resolved_version": None,
        },
    ]
    sc = parse_sidecar(_retag_payload(base, run_id=rid, items=items))
    write_sidecar(sc, base_dir=runs_dir)

    written = flush_apply_history(runs_dir / rid, rid, db)
    assert written == 1
    rows = db.get_update_history("winget", "web:keystone-app")
    assert rows[0]["from_version"] == "100.0"
    assert rows[0]["to_version"] == ""
    assert rows[0]["status"] == "triggered"


def test_flush_apply_history_idempotent_per_run_id(tmp_path: Path) -> None:
    """Running flush twice with the same run_id doesn't duplicate."""
    db = InventoryDB(tmp_path / "inv.db")
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    rid = _new_run_id()
    sc = parse_sidecar(_retag_payload(_load_fixture(), run_id=rid))
    write_sidecar(sc, base_dir=runs_dir)

    flush_apply_history(runs_dir / rid, rid, db)
    flush_apply_history(runs_dir / rid, rid, db)  # second pass, same run
    # 2 items in the fixture, so 2 rows total — never 4.
    assert db.history_count() == 2


def test_flush_apply_history_skips_non_apply_phases(tmp_path: Path) -> None:
    """check / plan / verify / cleanup sidecars are ignored by the flush.

    Apply is the only phase that ACTUALLY transitions a package from one
    version to another; everything else is read-only or planning.
    """
    db = InventoryDB(tmp_path / "inv.db")
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    rid = _new_run_id()
    payload = _retag_payload(_load_fixture(), run_id=rid, phase="check")
    sc = parse_sidecar(payload)
    write_sidecar(sc, base_dir=runs_dir)

    written = flush_apply_history(runs_dir / rid, rid, db)
    assert written == 0


def test_backfill_triggered_history_fills_to_version(tmp_path: Path) -> None:
    """Verify-phase sidecar with a real resolved_version backfills the row."""
    db = InventoryDB(tmp_path / "inv.db")
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    rid = _new_run_id()
    base = _load_fixture()
    # Apply phase: emit a triggered item with empty to_version.
    apply_items = [
        {
            **base["items"][0],
            "id": "web:keystone-app",
            "name": "web:keystone-app",
            "status": "triggered",
            "current_version": "100.0",
            "target_version": None,
            "resolved_version": None,
        },
    ]
    apply_sc = parse_sidecar(
        _retag_payload(base, run_id=rid, items=apply_items),
    )
    write_sidecar(apply_sc, base_dir=runs_dir)
    flush_apply_history(runs_dir / rid, rid, db)
    assert db.get_update_history("winget", "web:keystone-app")[0]["to_version"] == ""

    # Verify phase: the vendor agent has caught up; resolved_version=100.1.
    verify_items = [
        {
            **base["items"][0],
            "id": "web:keystone-app",
            "name": "web:keystone-app",
            "status": "success",
            "current_version": "100.1",
            "target_version": "100.1",
            "resolved_version": "100.1",
        },
    ]
    verify_payload = _retag_payload(
        base, run_id=rid, phase="verify", items=verify_items,
    )
    verify_sc = parse_sidecar(verify_payload)
    write_sidecar(verify_sc, base_dir=runs_dir)

    touched = backfill_triggered_history(runs_dir / rid, rid, db)
    assert touched == 1
    row = db.get_update_history("winget", "web:keystone-app")[0]
    assert row["to_version"] == "100.1"


# ── /apps/{category}/{name}/history endpoint ─────────────────────────────


class _FakeMgr(IPackageManager):
    def __init__(self, source: SourceType, name: str) -> None:
        self._source = source
        self._name = name

    @property
    def category(self) -> SourceType:
        return self._source

    @property
    def display_name(self) -> str:
        return self._name

    def is_available(self, host: HostInfo) -> bool:
        return True

    def run_phase(self, *args, **kwargs):  # pragma: no cover
        raise NotImplementedError


class _FakeInventory(IInventory):
    def __init__(self, packages: list[Package]) -> None:
        self._packages = packages

    def list_installed(
        self,
        host: HostInfo,
        *,
        categories: Iterable[str] | None = None,
    ) -> list[Package]:
        return list(self._packages)

    def emit_sidecar(self, run, host, packages):  # pragma: no cover
        raise NotImplementedError


class _FakeAdapter(IAdapter):
    def __init__(self) -> None:
        self._inv = _FakeInventory([])

    @property
    def name(self) -> str:
        return "fake"

    @property
    def display_name(self) -> str:
        return "Fake"

    @property
    def tier(self) -> int:
        return 1

    @property
    def capabilities(self) -> AdapterCapability:
        return AdapterCapability.PACKAGE_MANAGEMENT | AdapterCapability.INVENTORY

    def package_managers(self, host: HostInfo) -> list[IPackageManager]:
        return [_FakeMgr(SourceType.BREW, "brew")]

    def inventory(self) -> IInventory:
        return self._inv

    def snapshot(self) -> ISnapshot | None:
        return None

    def scheduler(self) -> IScheduler | None:
        return None

    def source(self) -> ISource | None:
        return None

    def elevation(self) -> IElevation | None:
        return None

    def detect_host(self) -> HostInfo:
        return HostInfo(
            hostname="testhost",
            os=OperatingSystem.MACOS,
            os_version="14",
            arch="arm64",
            user="tester",
            is_elevated=False,
            elevation_method=ElevationMethod.NONE,
        )

    def health_check(self) -> dict[str, str]:
        return {"brew": "ok"}


@pytest.fixture
def history_client(tmp_path: Path) -> tuple[TestClient, Path]:
    db_path = tmp_path / "inventory.db"
    app = create_app(
        adapter=_FakeAdapter(),
        runs_dir=tmp_path,
        inventory_db_path=db_path,
    )
    return TestClient(app), db_path


def test_apps_history_endpoint_returns_chronological(
    history_client: tuple[TestClient, Path],
) -> None:
    """GET /apps/{cat}/{name}/history returns rows newest-first."""
    client, db_path = history_client
    db = InventoryDB(db_path)
    base = datetime(2026, 5, 1, tzinfo=timezone.utc)
    for i, run in enumerate(["r1", "r2", "r3"]):
        db.record_update_history(
            category="web", name="web:docker",
            from_version=f"4.{70 + i}.0", to_version=f"4.{71 + i}.0",
            status="success", run_id=run,
            applied_at=(base + timedelta(days=i)).isoformat(),
            handler="sparkle",
        )
    r = client.get("/apps/web/web:docker/history")
    assert r.status_code == 200
    body = r.json()
    assert body["category"] == "web"
    assert body["name"] == "web:docker"
    versions = [h["to"] for h in body["history"]]
    assert versions == ["4.73.0", "4.72.0", "4.71.0"]
    assert body["history"][0]["handler"] == "sparkle"


def test_apps_history_endpoint_limit_param(
    history_client: tuple[TestClient, Path],
) -> None:
    """`?limit=2` caps the response."""
    client, db_path = history_client
    db = InventoryDB(db_path)
    for i in range(5):
        db.record_update_history(
            category="brew", name="curl",
            from_version=f"7.{i}", to_version=f"7.{i + 1}",
            status="success", run_id=f"r{i}",
            applied_at=f"2026-05-0{i + 1}T10:00:00+00:00",
        )
    r = client.get("/apps/brew/curl/history?limit=2")
    assert r.status_code == 200
    assert len(r.json()["history"]) == 2


def test_apps_history_endpoint_unknown_app_returns_empty(
    history_client: tuple[TestClient, Path],
) -> None:
    """Empty history for an unknown app: 200 with []."""
    client, _ = history_client
    r = client.get("/apps/brew/never-installed/history")
    assert r.status_code == 200
    assert r.json()["history"] == []
