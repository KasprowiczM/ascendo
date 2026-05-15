"""Contract tests for :class:`InventoryDB` + the DB-first inventory routes.

The DB is the persistent backbone behind both ``GET /inventory`` (the
Categories tab) and ``GET /apps/detect`` (the Apps tab). These tests
exercise:

* the pure-DB API (schema migration, upserts, queries, meta, clear),
* the fallback-then-populate flow when ``/inventory`` is called and
  the DB is empty,
* the parity guarantee — Apps + Categories show identical row counts.
"""
from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ascendo.dashboard import create_app
from ascendo.dashboard.inventory_db import InventoryDB, is_fresh
from ascendo.interfaces.adapter import AdapterCapability, IAdapter
from ascendo.interfaces.elevation import IElevation
from ascendo.interfaces.inventory import IInventory
from ascendo.interfaces.package_manager import IPackageManager
from ascendo.interfaces.scheduler import IScheduler
from ascendo.interfaces.snapshot import ISnapshot
from ascendo.interfaces.source import ISource
from ascendo.models.host import ElevationMethod, HostInfo, OperatingSystem
from ascendo.models.package import Package, SourceType


# ── Pure-DB API ──────────────────────────────────────────────────────────


def test_db_schema_creates_tables_idempotently(tmp_path: Path) -> None:
    """Constructing InventoryDB twice over the same path must not fail."""
    db_path = tmp_path / "inv.db"
    InventoryDB(db_path)
    InventoryDB(db_path)  # second open re-runs migrations idempotently
    assert db_path.is_file()


def test_db_bulk_upsert_and_query(tmp_path: Path) -> None:
    """bulk_upsert + query round-trips with all expected columns."""
    db = InventoryDB(tmp_path / "inv.db")
    db.bulk_upsert(
        [
            {
                "category": "brew",
                "name": "wget",
                "installed": "1.21.4",
                "candidate": "1.21.5",
                "status": "outdated",
                "source_type": "brew",
                "vendor": None,
                "metadata": {"tap": "homebrew/core"},
            },
            {
                "category": "npm",
                "name": "typescript",
                "installed": "5.4.0",
                "candidate": None,
                "status": "ok",
            },
        ],
    )
    rows = db.query()
    assert len(rows) == 2
    by_name = {r["name"]: r for r in rows}
    assert by_name["wget"]["candidate"] == "1.21.5"
    assert by_name["wget"]["metadata"] == {"tap": "homebrew/core"}
    assert by_name["typescript"]["status"] == "ok"


def test_db_meta_read_write(tmp_path: Path) -> None:
    """get_meta returns None on first call; set_meta then round-trips."""
    db = InventoryDB(tmp_path / "inv.db")
    assert db.get_meta("macos") is None
    db.set_meta("macos", item_count=143)
    meta = db.get_meta("macos")
    assert meta is not None
    assert meta["adapter"] == "macos"
    assert meta["item_count"] == 143
    assert meta["last_scan_at"]  # ISO timestamp


def test_db_query_filters(tmp_path: Path) -> None:
    """Category, status, search filters narrow correctly."""
    db = InventoryDB(tmp_path / "inv.db")
    db.bulk_upsert(
        [
            {"category": "brew", "name": "wget", "status": "outdated"},
            {"category": "brew", "name": "curl", "status": "ok"},
            {"category": "npm", "name": "typescript", "status": "ok"},
        ],
    )
    assert {r["name"] for r in db.query(category="brew")} == {"wget", "curl"}
    assert {r["name"] for r in db.query(status="outdated")} == {"wget"}
    assert {r["name"] for r in db.query(search="WG")} == {"wget"}
    assert db.query(category="bogus") == []


def test_db_clear_category_scoped(tmp_path: Path) -> None:
    """clear_category removes only its rows; other categories survive."""
    db = InventoryDB(tmp_path / "inv.db")
    db.bulk_upsert(
        [
            {"category": "brew", "name": "wget"},
            {"category": "brew", "name": "curl"},
            {"category": "npm", "name": "typescript"},
        ],
    )
    deleted = db.clear_category("brew")
    assert deleted == 2
    rows = db.query()
    assert len(rows) == 1
    assert rows[0]["name"] == "typescript"


def test_db_empty_db_query_returns_empty_list(tmp_path: Path) -> None:
    """Empty DB never raises; just returns []."""
    db = InventoryDB(tmp_path / "inv.db")
    assert db.query() == []
    assert db.query(category="brew") == []
    assert db.count() == 0
    assert db.categories() == []


def test_db_upsert_replaces_row(tmp_path: Path) -> None:
    """Re-upserting the same (category, name) overwrites prior columns."""
    db = InventoryDB(tmp_path / "inv.db")
    db.upsert("brew", "wget", installed="1.0", candidate="1.1", status="outdated")
    db.upsert("brew", "wget", installed="1.1", candidate=None, status="ok")
    rows = db.query()
    assert len(rows) == 1
    assert rows[0]["installed"] == "1.1"
    assert rows[0]["candidate"] is None
    assert rows[0]["status"] == "ok"


def test_bulk_upsert_after_clear_category_removes_stale_rows(
    tmp_path: Path,
) -> None:
    """After live-scan repopulates a category with fewer items, stale rows from
    the previous scan must NOT survive in the DB.

    Regression test for the Sesja-34 hygiene bug: ``InventoryDB.bulk_upsert``
    is upsert-only, so when a manager's tracked-set shrinks (e.g. pip drops a
    package), the SQLite inventory keeps the orphan entry. The fix is to
    ``clear_category(cat)`` before ``bulk_upsert`` for each category that
    we have authoritative fresh data for.
    """
    db = InventoryDB(tmp_path / "inv.db")
    # Initial scan: pip has 2 items.
    db.bulk_upsert(
        [
            {"category": "pip", "name": "ruff", "installed": "0.1.0"},
            {"category": "pip", "name": "obsolete-tool", "installed": "9.9"},
        ],
    )
    assert db.count(category="pip") == 2

    # Live-rescan emits only 1 item (obsolete-tool was uninstalled). The
    # caller must clear-then-bulk-upsert so the orphan row is gone.
    db.clear_category("pip")
    db.bulk_upsert([{"category": "pip", "name": "ruff", "installed": "0.2.0"}])

    rows = db.query(category="pip")
    assert len(rows) == 1
    assert rows[0]["name"] == "ruff"
    assert rows[0]["installed"] == "0.2.0"
    # Other categories are untouched.
    assert db.count(category="brew") == 0


def test_db_bulk_upsert_skips_malformed_rows(tmp_path: Path) -> None:
    """Rows missing category or name are silently skipped."""
    db = InventoryDB(tmp_path / "inv.db")
    written = db.bulk_upsert(
        [
            {"category": "brew", "name": "wget"},
            {"name": "no-category"},   # skipped
            {"category": "brew"},      # skipped
            {},                        # skipped
        ],
    )
    assert written == 1
    assert db.count() == 1


def test_is_fresh_window() -> None:
    """is_fresh treats meta within max_age_seconds as fresh."""
    now = datetime.now(timezone.utc)
    assert is_fresh({"last_scan_at": now.isoformat()})
    one_hour_ago = (now - timedelta(hours=1)).isoformat()
    assert is_fresh({"last_scan_at": one_hour_ago})
    two_days_ago = (now - timedelta(days=2)).isoformat()
    assert not is_fresh({"last_scan_at": two_days_ago})
    # malformed / missing values
    assert not is_fresh(None)
    assert not is_fresh({})
    assert not is_fresh({"last_scan_at": "not-a-timestamp"})


# ── DB-first inventory route integration ─────────────────────────────────


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
        self.call_count: int = 0

    def list_installed(
        self,
        host: HostInfo,
        *,
        categories: Iterable[str] | None = None,
    ) -> list[Package]:
        self.call_count += 1
        return list(self._packages)

    def emit_sidecar(self, run, host, packages):  # pragma: no cover
        raise NotImplementedError


class _FakeAdapter(IAdapter):
    def __init__(self, packages: list[Package]) -> None:
        self._inv = _FakeInventory(packages)
        self._packages = packages

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


def _pkg(name: str) -> Package:
    return Package(id=name, name=name, category=SourceType.BREW)


@pytest.fixture
def db_client(tmp_path: Path) -> tuple[TestClient, _FakeAdapter, Path]:
    packages = [_pkg("wget"), _pkg("curl"), _pkg("jq")]
    adapter = _FakeAdapter(packages)
    db_path = tmp_path / "inventory.db"
    app = create_app(adapter=adapter, runs_dir=tmp_path, inventory_db_path=db_path)
    return TestClient(app), adapter, db_path


def test_inventory_populates_db_on_first_scan(
    db_client: tuple[TestClient, _FakeAdapter, Path],
) -> None:
    """First /inventory call: empty DB -> live scan -> DB populated."""
    client, adapter, db_path = db_client
    # DB exists but is empty.
    db = InventoryDB(db_path)
    assert db.count() == 0

    r = client.get("/inventory")
    assert r.status_code == 200
    body = r.json()
    assert "brew" in body["categories"]
    assert len(body["categories"]["brew"]) == 3

    # DB now populated; meta records the scan.
    db = InventoryDB(db_path)
    assert db.count() == 3
    assert db.get_meta("fake") is not None


def test_inventory_serves_from_db_when_fresh(
    db_client: tuple[TestClient, _FakeAdapter, Path],
) -> None:
    """When DB has fresh data, the loader is not invoked again."""
    client, adapter, _ = db_client
    adapter._inv.call_count = 0

    client.get("/inventory")  # first call -> live scan, DB populated
    assert adapter._inv.call_count == 1

    client.get("/inventory")  # second call -> served from DB
    assert adapter._inv.call_count == 1, "second call must hit the DB"


def test_inventory_db_refresh_endpoint_repopulates(
    db_client: tuple[TestClient, _FakeAdapter, Path],
) -> None:
    """POST /inventory/db/refresh forces a rescan + repopulate."""
    client, adapter, db_path = db_client
    client.get("/inventory")  # populate
    initial_count = InventoryDB(db_path).count()
    assert initial_count == 3

    r = client.post("/inventory/db/refresh")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["item_count"] == 3
    assert "brew" in body["categories"]
    assert body["refreshed_at"]


def test_apps_and_categories_see_same_data(
    db_client: tuple[TestClient, _FakeAdapter, Path],
) -> None:
    """Apps tab and Categories tab read the SAME row count.

    The Sesja-32 bug: brew showed 143 in Categories but 1 in Apps. Both
    should share the DB-resolved buckets so the row counts always match.
    """
    client, _, _ = db_client

    cats = client.get("/inventory").json()["categories"]
    apps = client.get("/apps/detect").json()["apps"]

    cat_brew = {it["name"] for it in cats.get("brew", [])}
    apps_brew = {a["name"] for a in apps if a["category"] == "brew"}
    assert cat_brew == apps_brew, (
        f"Apps/Categories disagree: cats={sorted(cat_brew)} apps={sorted(apps_brew)}"
    )


def test_inventory_db_refresh_drops_stale_rows_for_shrunk_manifest(
    db_client: tuple[TestClient, _FakeAdapter, Path],
) -> None:
    """When the live-scan manifest shrinks, ``/inventory/db/refresh`` must
    not leave orphan rows in the DB.

    Regression test for the Sesja-34 hygiene bug: ``InventoryDB.bulk_upsert``
    is upsert-only; without an explicit ``clear_category`` step, removed
    items linger forever. This test:

    1. Populates the DB with 3 brew rows.
    2. Mutates the fake inventory to drop one item.
    3. Forces a DB refresh.
    4. Asserts the dropped item is gone from the DB.
    """
    client, adapter, db_path = db_client

    # Initial scan: 3 items.
    client.get("/inventory")
    db = InventoryDB(db_path)
    assert db.count(category="brew") == 3

    # Mutate the fake adapter so the next live scan returns only 2 items.
    adapter._inv._packages = [_pkg("wget"), _pkg("curl")]  # noqa: SLF001

    # Force a DB refresh so the live-scan path fires.
    r = client.post("/inventory/db/refresh")
    assert r.status_code == 200
    assert r.json()["item_count"] == 2

    # The orphan ``jq`` row must be gone — not lingering as upsert exhaust.
    db = InventoryDB(db_path)
    rows = db.query(category="brew")
    names = {r["name"] for r in rows}
    assert names == {"wget", "curl"}, (
        f"Stale row leaked through DB refresh: {sorted(names)}"
    )


def test_post_run_flush_is_upsert_only(tmp_path: Path) -> None:
    """Regression: ``_flush_run_to_inventory_db`` must NOT clear rows
    outside the sidecar's enumeration set.

    Original bug (Sesja 34) wanted: "clear stale rows when a manifest
    shrinks". Sesja 45 over-corrected by adding ``clear_category(cat)``
    before bulk_upsert in the post-run flush.

    The over-correction was catastrophic: check__web.json reports the
    registry-driven subset (~37 apps with vendor probes), while the
    full inventory live-scan has ~316 web-classified bundles. After a
    check run the flush cleared all 316 and wrote back only 37 — the
    operator saw "Web: 37 apps" instead of the truth.

    The right model: post-run flush is **UPSERT-ONLY**. The explicit
    live-scan paths (POST /inventory/db/refresh, /inventory cold
    start in spa_real._replace_buckets_in_db) keep their clear+write
    semantics because they ARE authoritative for the full set. Apply
    sidecars only carry items that got touched; clearing on those
    was always wrong.

    This test asserts:
      1. Pre-existing rows for items NOT in the sidecar are preserved.
      2. Rows that ARE in the sidecar get their installed/candidate
         updated.
    """
    import json
    from ascendo.orchestrator.run_async import _flush_run_to_inventory_db

    db_path = tmp_path / "inv.db"
    db = InventoryDB(db_path)

    # Seed: 3 brew rows from a prior live-scan ("inventory enumeration"
    # outside the manifest). jq is the row NOT in the upcoming sidecar
    # — it must SURVIVE the flush (live-scan said it's installed; the
    # check sidecar's narrower view shouldn't nuke it).
    db.bulk_upsert(
        [
            {"category": "brew", "name": "wget", "installed": "0.9",
             "candidate": "0.9", "status": "up_to_date"},
            {"category": "brew", "name": "curl", "installed": "1.0",
             "candidate": "1.0", "status": "up_to_date"},
            {"category": "brew", "name": "jq", "installed": "1.0",
             "candidate": "1.0", "status": "up_to_date"},
        ],
    )
    assert db.count(category="brew") == 3

    # Build a check__brew.json with only 2 items — wget upgraded to 1.0,
    # curl unchanged, jq absent from sidecar.
    fixture = (
        Path(__file__).resolve().parent.parent
        / "fixtures" / "sidecars" / "ascendo_v1_apply_winget.json"
    )
    payload = json.loads(fixture.read_text(encoding="utf-8"))
    payload["category"] = "brew"
    payload["phase"] = "check"
    payload["items"] = [
        {**(payload["items"][0]), "id": "brew:wget", "name": "wget",
         "category": "brew", "source": {"type": "brew"},
         "current_version": "1.0", "target_version": "1.0",
         "status": "up_to_date"},
        {**(payload["items"][0]), "id": "brew:curl", "name": "curl",
         "category": "brew", "source": {"type": "brew"},
         "current_version": "1.0", "target_version": "1.0",
         "status": "up_to_date"},
    ]
    payload["summary"] = {
        "total": 2, "success": 0, "up_to_date": 2,
        "failed": 0, "skipped": 0, "planned": 0, "partial": 0, "triggered": 0,
    }
    payload["status"] = "success"

    run_dir = tmp_path / "runs" / "abc-123"
    run_dir.mkdir(parents=True)
    (run_dir / "check__brew.json").write_text(
        json.dumps(payload), encoding="utf-8",
    )

    flushed = _flush_run_to_inventory_db(run_dir, db)
    assert flushed == 2

    rows = db.query(category="brew")
    names = {r["name"] for r in rows}
    # All 3 must remain: wget + curl from sidecar, jq preserved from
    # earlier live-scan (not in this sidecar — must not be wiped).
    assert names == {"wget", "curl", "jq"}, (
        f"Post-run flush WRONGLY cleared rows it shouldn't own: {sorted(names)}"
    )
    # wget's row should now reflect the sidecar's upgraded version (1.0).
    wget = next(r for r in rows if r["name"] == "wget")
    assert wget["installed"] == "1.0", (
        f"Upsert did not refresh wget.installed: {wget}"
    )



def test_delete_row_removes_single_row_by_pk(tmp_path: Path) -> None:
    """Sesja 73: ``delete_row(category, name, item_id)`` evicts one row.

    Used by the post-run flush when an apply sidecar reports a registry
    entry whose underlying bundle was uninstalled (Cursor / Opera /
    Notion). Must NOT delete sibling rows in the same category.
    """
    db_path = tmp_path / "inv.db"
    db = InventoryDB(db_path)
    db.bulk_upsert(
        [
            {"category": "web", "name": "web:cursor", "item_id": "",
             "installed": None, "candidate": None, "status": "skipped"},
            {"category": "web", "name": "web:obsidian", "item_id": "",
             "installed": "1.5", "candidate": "1.5", "status": "up_to_date"},
        ],
    )
    assert db.count(category="web") == 2

    deleted = db.delete_row("web", "web:cursor", "")
    assert deleted == 1
    rows = db.query(category="web")
    names = {r["name"] for r in rows}
    assert names == {"web:obsidian"}, (
        f"delete_row should have evicted only cursor; got {sorted(names)}"
    )

    # No-op when row already gone.
    deleted = db.delete_row("web", "web:cursor", "")
    assert deleted == 0


def test_post_run_flush_evicts_uninstalled_rows(tmp_path: Path) -> None:
    """Sesja 73: apply phase emits ``status=skipped`` with no version
    for registry entries whose bundle is missing on disk (operator
    uninstalled Cursor but kept it in ``web_apps.toml``). The post-run
    flush MUST delete the matching inventory row so the SPA stops
    showing "Cursor (not installed)" forever.
    """
    import json
    from ascendo.orchestrator.run_async import _flush_run_to_inventory_db

    db_path = tmp_path / "inv.db"
    db = InventoryDB(db_path)
    # Seed: cursor was in inventory from an earlier live-scan, obsidian
    # is a regular installed app.
    db.bulk_upsert(
        [
            {"category": "web", "name": "web:cursor", "item_id": "",
             "installed": "0.5.0", "candidate": "0.5.0", "status": "up_to_date"},
            {"category": "web", "name": "web:obsidian", "item_id": "",
             "installed": "1.5.0", "candidate": "1.5.0", "status": "up_to_date"},
        ],
    )
    assert db.count(category="web") == 2

    # Build an apply__web.json that says "cursor is gone" (status=skipped,
    # no current/target version — the exact shape apply.sh emits for the
    # not_installed_or_path_mismatch branch).
    fixture = (
        Path(__file__).resolve().parent.parent
        / "fixtures" / "sidecars" / "ascendo_v1_apply_winget.json"
    )
    payload = json.loads(fixture.read_text(encoding="utf-8"))
    payload["category"] = "web"
    payload["phase"] = "apply"
    payload["items"] = [
        {
            **payload["items"][0],
            "id": "web:cursor",
            "name": "web:cursor",
            "category": "web",
            "source": {"type": "web"},
            "current_version": None,
            "target_version": None,
            "resolved_version": None,
            "status": "skipped",
        },
        {
            **payload["items"][0],
            "id": "web:obsidian",
            "name": "web:obsidian",
            "category": "web",
            "source": {"type": "web"},
            "current_version": "1.5.0",
            "target_version": "1.5.0",
            "status": "up_to_date",
        },
    ]
    payload["summary"] = {
        "total": 2, "success": 0, "up_to_date": 1,
        "failed": 0, "skipped": 1, "planned": 0, "partial": 0, "triggered": 0,
    }
    payload["status"] = "success"

    run_dir = tmp_path / "runs" / "evict-test"
    run_dir.mkdir(parents=True)
    (run_dir / "apply__web.json").write_text(json.dumps(payload), encoding="utf-8")

    _flush_run_to_inventory_db(run_dir, db)

    rows = db.query(category="web")
    names = {r["name"] for r in rows}
    assert "web:cursor" not in names, (
        "Post-run flush should have evicted the uninstalled cursor row; "
        f"DB still has {sorted(names)}"
    )
    assert "web:obsidian" in names, "Sibling installed row must survive eviction"


def test_inventory_db_does_not_leak_file_descriptors(tmp_path: Path) -> None:
    """Regression: every InventoryDB call must close its sqlite3 connection.

    Background: Pythons builtin ``with conn:`` only commits/rollbacks the
    transaction; it does NOT close the connection. The original
    ``_connect()`` returned a raw connection and every caller did
    ``with self._connect() as conn:`` — leaking one fd per call. Under
    the dashboards SSE polling load + inventory queries, the user hit
    ``[Errno 24] Too many open files`` within 7 minutes (~256 fd limit
    on macOS).

    This test does 200 mixed get_meta / set_meta / query / count calls
    and confirms the SQLite file is openable afterwards (i.e. we have
    not run out of file descriptors and have not held a writer lock).
    """
    import resource
    import sqlite3

    db_path = tmp_path / "inv.db"
    db = InventoryDB(db_path)

    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    # Force a tighter limit so a real leak surfaces fast even on hosts
    # whose ulimit is huge (Linux defaults can be 1M+).
    target_soft = min(128, soft)
    try:
        resource.setrlimit(resource.RLIMIT_NOFILE, (target_soft, hard))
    except (ValueError, OSError):
        pytest.skip("cannot lower RLIMIT_NOFILE in this environment")
    try:
        for i in range(200):
            db.set_meta("test", last_scan_at="2026-05-09T00:00:00Z", item_count=i)
            db.get_meta("test")
            db.count(category="brew")
            db.bulk_upsert([
                {"category": "brew", "name": f"pkg{i}", "installed": "1.0"},
            ])
            db.query(category="brew")
        # If fds were leaking, opening a fresh connection here would
        # throw OperationalError("unable to open database file").
        cn = sqlite3.connect(db_path)
        cn.execute("SELECT 1").fetchone()
        cn.close()
    finally:
        try:
            resource.setrlimit(resource.RLIMIT_NOFILE, (soft, hard))
        except (ValueError, OSError):
            pass

