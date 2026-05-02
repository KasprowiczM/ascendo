"""Tests for the real adapter-backed handlers in :mod:`spa_real`.

Covers B1 (categories + inventory + cache), B2 (health), B3 (active run +
SSE) from the windows-end-to-end design. The matching stubs in
``spa_stubs.py`` have been deleted; these tests assert that the new
handlers ship the expected shapes and respect the cache TTL.

Reuses the FakeAdapter pattern from :mod:`test_dashboard` but adds a
fake :class:`IInventory` so we can exercise the inventory pipeline
without spawning real winget.
"""
from __future__ import annotations

import time
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from ascendo.dashboard import create_app
from ascendo.dashboard.routes.spa_real import (
    InventoryCache,
    _classify,
    _version_gt,
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
from ascendo.orchestrator.run_async import RunRegistry, RunStatus


# ── Fakes ─────────────────────────────────────────────────────────────────


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

    def run_phase(self, *args, **kwargs):  # pragma: no cover — unused here
        raise NotImplementedError


class _FakeInventory(IInventory):
    """In-memory inventory fake. Records ``list_installed`` call count."""

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
    def __init__(
        self,
        *,
        managers: list[IPackageManager] | None = None,
        inventory: IInventory | None = None,
        health: dict[str, str] | None = None,
    ) -> None:
        self._managers = managers if managers is not None else [
            _FakeMgr(SourceType.WINGET, "winget"),
        ]
        self._inventory = inventory
        self._health = health if health is not None else {"winget": "ok"}

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
        return list(self._managers)

    def inventory(self) -> IInventory:
        if self._inventory is None:
            raise NotImplementedError
        return self._inventory

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
            os=OperatingSystem.WINDOWS,
            os_version="11",
            arch="x86_64",
            user="tester",
            is_elevated=False,
            elevation_method=ElevationMethod.NONE,
        )

    def health_check(self) -> dict[str, str]:
        return dict(self._health)


def _pkg(id_: str, source: SourceType, *, name: str | None = None) -> Package:
    return Package(id=id_, name=name or id_, category=source)


# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
def fake_packages() -> list[Package]:
    """5 packages across 3 sources for bucketing tests."""
    return [
        _pkg("Microsoft.PowerShell", SourceType.WINGET),
        _pkg("Mozilla.Firefox", SourceType.WINGET),
        _pkg("nodejs", SourceType.NPM),
        _pkg("typescript", SourceType.NPM),
        _pkg("requests", SourceType.PIP),
    ]


@pytest.fixture
def adapter_with_inv(fake_packages: list[Package]) -> _FakeAdapter:
    return _FakeAdapter(
        managers=[
            _FakeMgr(SourceType.WINGET, "winget"),
            _FakeMgr(SourceType.NPM, "npm"),
        ],
        inventory=_FakeInventory(fake_packages),
        health={"winget": "ok: 1.7.0", "pwsh": "error: not found"},
    )


@pytest.fixture
def client(adapter_with_inv: _FakeAdapter, tmp_path: Path) -> TestClient:
    app = create_app(adapter=adapter_with_inv, runs_dir=tmp_path)
    return TestClient(app)


# ── /categories ───────────────────────────────────────────────────────────


def test_categories_real(client: TestClient) -> None:
    """B1: /categories surfaces all adapter package managers."""
    r = client.get("/categories")
    assert r.status_code == 200
    body = r.json()
    ids = {c["id"] for c in body["categories"]}
    assert ids == {"winget", "npm"}
    assert all("display_name" in c for c in body["categories"])
    assert all(c["available"] is True for c in body["categories"])


# ── /inventory ────────────────────────────────────────────────────────────


def test_inventory_buckets_by_source(client: TestClient) -> None:
    """B1: /inventory groups items by their SourceType key."""
    r = client.get("/inventory")
    assert r.status_code == 200
    cats = r.json()["categories"]
    assert set(cats.keys()) == {"winget", "npm", "pip"}
    assert len(cats["winget"]) == 2
    assert len(cats["npm"]) == 2
    assert len(cats["pip"]) == 1
    # Item shape:
    item = cats["winget"][0]
    assert {"name", "installed", "candidate", "source", "status"}.issubset(item)
    assert item["source"] == "winget"


def test_inventory_summary_totals(client: TestClient) -> None:
    """B1: /inventory/summary totals add up per category and overall."""
    r = client.get("/inventory/summary")
    assert r.status_code == 200
    body = r.json()
    assert body["totals"]["total"] == 5
    # No version info on the fake packages -> all classified ok.
    assert body["totals"]["ok"] == 5
    assert body["totals"]["outdated"] == 0
    assert body["totals"]["missing"] == 0
    # Categories add up.
    assert body["categories"]["winget"]["total"] == 2
    assert body["categories"]["npm"]["total"] == 2
    assert body["categories"]["pip"]["total"] == 1


def test_inventory_category_projection(client: TestClient) -> None:
    """B1: /inventory/{category} returns just that bucket."""
    r = client.get("/inventory/npm")
    assert r.status_code == 200
    body = r.json()
    assert body["category"] == "npm"
    assert len(body["items"]) == 2
    assert all(it["source"] == "npm" for it in body["items"])


# ── Caching ───────────────────────────────────────────────────────────────


def test_inventory_cache_hit(
    client: TestClient,
    adapter_with_inv: _FakeAdapter,
) -> None:
    """B1: hitting /inventory twice within the TTL only loads once."""
    inv = adapter_with_inv._inventory
    assert isinstance(inv, _FakeInventory)
    inv.call_count = 0

    r1 = client.get("/inventory")
    r2 = client.get("/inventory")
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert inv.call_count == 1, f"loader was called {inv.call_count}x; expected 1"


def test_inventory_cache_invalidate(
    client: TestClient,
    adapter_with_inv: _FakeAdapter,
) -> None:
    """B1: POST /inventory/refresh forces the next call to reload."""
    inv = adapter_with_inv._inventory
    assert isinstance(inv, _FakeInventory)
    inv.call_count = 0

    client.get("/inventory")  # populate cache
    refresh = client.post("/inventory/refresh")
    assert refresh.status_code == 200
    assert refresh.json()["ok"] is True
    # ``refreshed`` should list the categories that were cached.
    assert set(refresh.json()["refreshed"]) == {"winget", "npm", "pip"}

    client.get("/inventory")  # forces reload
    assert inv.call_count == 2


# ── /health/check + /health/run ───────────────────────────────────────────


def test_health_check_real(client: TestClient) -> None:
    """B2: /health/check derives score from adapter.health_check()."""
    r = client.get("/health/check")
    assert r.status_code == 200
    body = r.json()
    # 1 component is "error: not found" → 1 bad → 100 - 20 = 80.
    assert body["score"] == 80
    assert body["status"] == "degraded"
    assert body["failed"] == ["pwsh"]
    assert body["components"] == {
        "winget": "ok: 1.7.0",
        "pwsh": "error: not found",
    }
    assert body["checked_at"]  # ISO timestamp


def test_health_run_real(client: TestClient) -> None:
    """B2: POST /health/run wraps the same snapshot in {ok, snapshot}."""
    r = client.post("/health/run")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["snapshot"]["score"] == 80


def test_health_no_adapter_returns_503(tmp_path: Path) -> None:
    """B2: missing adapter -> 503 with a clear message."""
    app = create_app(runs_dir=tmp_path)
    app.state.adapter = None
    client = TestClient(app)
    r = client.get("/health/check")
    assert r.status_code == 503


# ── /runs/active + stop + stream ──────────────────────────────────────────


def test_runs_active_no_run(client: TestClient) -> None:
    """B3: empty registry -> active is None."""
    r = client.get("/runs/active")
    assert r.status_code == 200
    assert r.json() == {"active": None}


def test_runs_active_with_running_run(
    client: TestClient,
    tmp_path: Path,
) -> None:
    """B3: an in-flight registered run is reported as active."""
    registry: RunRegistry = client.app.state.run_registry
    run_id = uuid4()
    state = registry.register(run_id, base_dir=tmp_path)
    state.status = RunStatus.RUNNING
    state.started_at = datetime.now(timezone.utc)

    r = client.get("/runs/active")
    assert r.status_code == 200
    body = r.json()
    assert body["active"]["run_id"] == str(run_id)
    assert body["active"]["status"] == "running"
    assert body["active"]["started_at"]


def test_runs_active_stop_no_run(client: TestClient) -> None:
    """B3: stop with no active run reports the gap honestly."""
    r = client.post("/runs/active/stop")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert "no active run" in body["reason"]


def test_runs_active_stop_running_run(
    client: TestClient,
    tmp_path: Path,
) -> None:
    """B3: stop with an active run still reports cancel-not-supported."""
    registry: RunRegistry = client.app.state.run_registry
    run_id = uuid4()
    state = registry.register(run_id, base_dir=tmp_path)
    state.status = RunStatus.RUNNING

    r = client.post("/runs/active/stop")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert "cancel" in body["reason"].lower()


def test_sse_emits_status_event_idle(client: TestClient) -> None:
    """B3: stream with no active run emits idle status and closes."""
    r = client.get("/runs/active/stream")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    assert b"event: status" in r.content
    # Body parses as "idle" payload.
    assert b"idle" in r.content


def test_sse_emits_status_event_with_active_run(
    client: TestClient,
    tmp_path: Path,
) -> None:
    """B3: stream with an in-flight run emits status + done events.

    We register a RUNNING run, kick off the stream, and after a short
    delay flip the run to COMPLETED via a background thread so the SSE
    generator's poll loop emits ``done`` and exits.
    """
    import threading

    registry: RunRegistry = client.app.state.run_registry
    run_id = uuid4()
    state = registry.register(run_id, base_dir=tmp_path)
    state.status = RunStatus.RUNNING
    state.started_at = datetime.now(timezone.utc)

    def _complete_after_delay() -> None:
        time.sleep(0.6)  # > 1 SSE poll tick (500 ms)
        state.status = RunStatus.COMPLETED
        state.finished_at = datetime.now(timezone.utc)

    threading.Thread(target=_complete_after_delay, daemon=True).start()

    deadline = time.monotonic() + 5.0
    body = b""
    with client.stream("GET", "/runs/active/stream") as resp:
        assert resp.status_code == 200
        for chunk in resp.iter_bytes():
            body += chunk
            if b"event: done" in body or time.monotonic() > deadline:
                break

    assert b"event: status" in body
    assert b"event: done" in body
    assert str(run_id).encode() in body


# ── Version comparator ────────────────────────────────────────────────────


def test_version_gt() -> None:
    """B1: _version_gt is *strict* — equal versions and downgrades are False."""
    # Strict newer.
    assert _version_gt("1.2.3", "1.2.2")
    assert _version_gt("0.40.0", "0.1.9")
    # Strict older.
    assert not _version_gt("1.2.2", "1.2.3")
    # Etap 12 npm regression case: candidate is older than installed
    # (e.g. @google/gemini-cli `latest` dist-tag points at 0.1.9 while
    # 0.40.0 is installed). Must NOT flag as outdated.
    assert not _version_gt("0.1.9", "0.40.0")
    # Equal versions are not "greater".
    assert not _version_gt("1.0.0", "1.0.0")
    # None / empty inputs.
    assert not _version_gt(None, "1.0.0")
    assert not _version_gt("1.0.0", None)


def test_classify_uses_strict_newer() -> None:
    """B1: _classify returns ``outdated`` only on strict-newer candidate."""
    assert _classify("1.0.0", "1.0.1") == "outdated"
    assert _classify("1.0.1", "1.0.0") == "ok"  # downgrade is not outdated
    assert _classify("1.0.0", "1.0.0") == "ok"
    assert _classify(None, None) == "ok"
    assert _classify(None, "1.0.0") == "missing"


# ── Cache unit ────────────────────────────────────────────────────────────


def test_inventory_cache_ttl_expiry() -> None:
    """B1: InventoryCache reloads after TTL expires."""
    cache = InventoryCache(ttl_seconds=0.05)
    calls = {"n": 0}

    def loader():
        calls["n"] += 1
        return [_pkg("a", SourceType.WINGET)]

    cache.get(loader)
    cache.get(loader)
    assert calls["n"] == 1
    time.sleep(0.1)
    cache.get(loader)
    assert calls["n"] == 2


# ── Stubs catalog ─────────────────────────────────────────────────────────


def test_stubs_inventory_table_marked_served() -> None:
    """The :data:`spa_stubs._SPA_FETCH_INVENTORY` catalog must mark the
    endpoints we just promoted as ``"served"`` rather than ``"stub-..."``.
    Keeps the catalog an honest ledger of what's still stubbed.
    """
    from ascendo.dashboard.routes.spa_stubs import _SPA_FETCH_INVENTORY

    served_now = (
        "GET /categories",
        "GET /inventory",
        "GET /inventory/summary",
        "GET /inventory/{cat}",
        "GET /health/check",
        "POST /health/run",
        "POST /inventory/refresh",
        "GET /runs/active",
        "GET /runs/active/stream",
        "POST /runs/active/stop",
    )
    for key in served_now:
        assert _SPA_FETCH_INVENTORY[key] == "served", (
            f"{key} should be 'served' but is {_SPA_FETCH_INVENTORY[key]!r}"
        )


# ── Inventory fallback when adapter has no inventory() ────────────────────


def test_inventory_no_inventory_returns_empty_buckets(tmp_path: Path) -> None:
    """An adapter whose inventory() raises NotImplementedError must yield
    an empty bucket map, not a 500. The dashboard MUST keep rendering on
    a clean install.
    """
    adapter = _FakeAdapter(inventory=None)
    app = create_app(adapter=adapter, runs_dir=tmp_path)
    client = TestClient(app)
    r = client.get("/inventory")
    assert r.status_code == 200
    assert r.json() == {"categories": {}}
