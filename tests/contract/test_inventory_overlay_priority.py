"""Regression tests for the build-inventory overlay pipeline.

Operator observation on DP5520WMK (2026-05-13, after Sesja 59 fixes):
After applying updates + clicking "Build Inventory" in the dashboard,
the Categories tab showed:
  - winget: 83 items, 0 with installed version
  - msstore: 78 items, 0 with installed version
  - npm: 0 items (!)
  - pip: 0 items (!)
  - web: 0 items
  - plugin: 0 items
  - windows_update: 0 items
  - registry_arp: 46 items, 0 with installed version

Three distinct bugs in the inventory pipeline:

1. ``_latest_check_overlay`` picked the most-recent sidecar by mtime
   with a verify > apply > check tie-break. Verify sidecars for npm/
   pip/web are typically EMPTY (verify dedupes after success; if no
   items were applied for that category, verify has items=[]). The
   function returned an empty overlay -> Categories showed 0 items
   even though check sidecars next to verify had 15 / 11 / N items.

2. ``_seed_buckets_from_sidecars`` only replaced a bucket when the
   sidecar had MORE items than IInventory found. For msstore/winget,
   ``Get-WingetInstalled`` returns ~85/~84 items but ~95/~80 sidecar
   items dedupe to 78 / lower (via id() in
   ``_items_from_check_sidecar``). 78 < 85 so no replacement; the
   versionless IInventory items stayed.

3. ``_build_buckets_live`` did not call ``_enrich_items`` on the
   buckets. Even when the bucket WAS replaced from sidecars, the CLI
   path bypassed the per-item version overlay that the SPA's
   GET /inventory route uses. Result: build-inventory flushed
   versionless rows into the DB.

The fix touches three layers:

* ``_latest_check_overlay`` splits candidates into ``check`` vs
  ``apply/verify`` lists. Check provides the FULL base; apply/verify
  overlay per-item ``installed`` for items that were just applied.
  Empty sidecars in either tier are skipped.

* ``_build_buckets_live`` runs ``_enrich_items`` on every bucket
  after ``_seed_buckets_from_sidecars`` so per-item version overlay
  applies to IInventory-derived rows too.

These tests pin all three behaviors.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from ascendo.dashboard.routes.spa_real import (
    InventoryCache,
    _build_buckets_live,
    _latest_check_overlay,
)


_FIXTURE = (
    Path(__file__).resolve().parent.parent
    / "fixtures" / "sidecars" / "ascendo_v1_apply_winget.json"
)


def _write_sidecar(
    run_dir: Path,
    *,
    phase: str,
    category: str,
    items: list[dict],
) -> Path:
    payload = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    payload["phase"] = phase
    payload["category"] = category
    payload["items"] = items
    payload["status"] = "success"
    payload["summary"] = {
        "total": len(items), "success": 0, "up_to_date": 0,
        "failed": 0, "skipped": 0, "planned": 0, "partial": 0, "triggered": 0,
    }
    out = run_dir / f"{phase}__{category}.json"
    out.write_text(json.dumps(payload), encoding="utf-8")
    return out


def _item(name: str, *, current: str | None, target: str | None,
          resolved: str | None = None, status: str = "up_to_date") -> dict:
    return {
        "id": name, "name": name, "category": "npm",
        "source": {"type": "npm", "feed": None, "url": None},
        "current_version": current, "target_version": target,
        "resolved_version": resolved, "status": status,
        "exit_code": None, "duration_ms": None, "evidence": None,
        "rollback": None, "messages": [],
    }


def test_empty_verify_does_not_hide_full_check(tmp_path: Path) -> None:
    """Bug 1: empty verify mustn't return zero overlay when check has data.

    Exact DP5520WMK 2026-05-13 reproduction: a single run dir contains
    check with 15 items + verify with 0 items + apply with 15 items.
    ``_latest_check_overlay`` previously picked verify (latest mtime,
    highest priority on tie) and returned ``{}``.
    """
    run_dir = tmp_path / "runs" / "rid-empty-verify"
    run_dir.mkdir(parents=True)

    # Write in mtime order: check, apply, verify (so verify has newest mtime)
    _write_sidecar(run_dir, phase="check", category="npm",
                   items=[_item("pkg-a", current="1.0", target="1.0"),
                          _item("pkg-b", current="2.0", target="2.0")])
    _write_sidecar(run_dir, phase="apply", category="npm", items=[])
    _write_sidecar(run_dir, phase="verify", category="npm", items=[])

    overlay = _latest_check_overlay(tmp_path / "runs", "npm")
    assert "pkg-a" in overlay, (
        f"empty verify hid the full check sidecar; overlay keys: {list(overlay.keys())}"
    )
    assert "pkg-b" in overlay


def test_apply_success_resolved_version_overlays_check(tmp_path: Path) -> None:
    """Bug 1 partner: when apply has success items, those overlay onto
    the check base (so post-apply versions are visible in inventory).
    """
    run_dir = tmp_path / "runs" / "rid-apply-overlay"
    run_dir.mkdir(parents=True)

    # check sees 2 packages pre-apply
    _write_sidecar(run_dir, phase="check", category="npm", items=[
        _item("pkg-up", current="1.0", target="2.0", status="planned"),
        _item("pkg-ok", current="3.0", target="3.0", status="up_to_date"),
    ])
    # apply succeeds on pkg-up; pkg-ok wasn't touched
    _write_sidecar(run_dir, phase="apply", category="npm", items=[
        _item("pkg-up", current="1.0", target="2.0", resolved="2.0", status="success"),
    ])

    overlay = _latest_check_overlay(tmp_path / "runs", "npm")
    assert overlay["pkg-up"]["installed"] == "2.0", (
        "apply success resolved_version must overlay onto check row"
    )
    assert overlay["pkg-up"]["status"] == "up_to_date"
    # pkg-ok: untouched by apply, stays at check's reading.
    assert overlay["pkg-ok"]["installed"] == "3.0"


def test_failed_apply_does_not_overlay_installed(tmp_path: Path) -> None:
    """If apply FAILED for an item, the check row's installed value
    (pre-apply state) must remain visible -- not get overwritten by
    a meaningless resolved_version.
    """
    run_dir = tmp_path / "runs" / "rid-apply-fail"
    run_dir.mkdir(parents=True)
    _write_sidecar(run_dir, phase="check", category="npm",
                   items=[_item("broken", current="0.1", target="0.2", status="planned")])
    _write_sidecar(run_dir, phase="apply", category="npm",
                   items=[_item("broken", current="0.1", target="0.2", status="failed")])
    overlay = _latest_check_overlay(tmp_path / "runs", "npm")
    assert overlay["broken"]["installed"] == "0.1"


class _FakeWindowsAdapter:
    """Minimal IAdapter stub for the build_buckets_live integration test.

    Returns one IInventory item per category (matching real Windows
    behaviour where ``winget list`` reports apps as msstore/winget but
    without versions). Forces the overlay pass to fill the versions in.
    """

    name = "fake-windows"

    def detect_host(self):
        from ascendo.models.host import HostInfo, OperatingSystem, ElevationMethod
        return HostInfo(
            hostname="test", os=OperatingSystem.WINDOWS,
            os_version="11", arch="x86_64", user="t",
            is_elevated=True, elevation_method=ElevationMethod.UAC,
            locale="en-US",
        )

    def inventory(self):
        from ascendo.interfaces.inventory import IInventory
        from ascendo.models.package import Package, SourceType

        class _Inv(IInventory):
            def list_installed(self, host, *, categories=None):
                return [
                    Package(
                        id="fake-msstore-app",
                        name="WhatsApp",
                        category=SourceType.MSSTORE,
                        installed_version=None,  # IInventory has no version
                        available_version=None,
                        vendor=None,
                    ),
                ]

            def emit_sidecar(self, host, run_id, trigger, profile,
                             output_dir, *, categories=None, dry_run=False):
                raise NotImplementedError  # unused in this test path
        return _Inv()

    def package_managers(self, host):
        # Return a fake msstore manager so _adapter_category_ids
        # surfaces 'msstore' as a category.
        from ascendo.interfaces.package_manager import IPackageManager
        from ascendo.models.package import SourceType

        class _FM:
            category = SourceType.MSSTORE
        return [_FM()]


def test_build_buckets_live_runs_enrichment_pass(tmp_path: Path) -> None:
    """Bug 3 (the CLI bug): build-inventory must apply per-item version
    overlay even when IInventory rows have no versions.

    Setup mirrors DP5520WMK: IInventory returns 1 msstore item with
    versions=None; the check sidecar nearby has the same item WITH
    versions. After ``_build_buckets_live`` the bucket row must have
    versions populated.
    """
    run_dir = tmp_path / "runs" / "rid-enrich"
    run_dir.mkdir(parents=True)
    _write_sidecar(
        run_dir, phase="check", category="msstore",
        items=[{
            "id": "MSIX\\WhatsAppDesktop", "name": "WhatsApp",
            "category": "msstore",
            "source": {"type": "msstore", "feed": None, "url": None},
            "current_version": "2.2616.100.0",
            "target_version": "2.2616.100.0",
            "resolved_version": None,
            "status": "up_to_date",
            "exit_code": None, "duration_ms": None,
            "evidence": None, "rollback": None, "messages": [],
        }],
    )

    buckets = _build_buckets_live(
        _FakeWindowsAdapter(), InventoryCache(), tmp_path / "runs"
    )
    assert "msstore" in buckets, f"msstore bucket missing: {list(buckets.keys())}"
    rows = buckets["msstore"]
    # IInventory returned 1 row with installed=None.
    # _enrich_items must overlay the check sidecar's version onto it.
    whatsapp = next((r for r in rows if r["name"] == "WhatsApp"), None)
    assert whatsapp is not None, f"WhatsApp not in msstore bucket: {[r['name'] for r in rows]}"
    assert whatsapp["installed"] == "2.2616.100.0", (
        "build-inventory must overlay check-sidecar version onto IInventory rows; "
        f"got: {whatsapp}"
    )
    assert whatsapp["candidate"] == "2.2616.100.0"


def test_check_overlay_empty_when_no_sidecars(tmp_path: Path) -> None:
    """Degenerate case: a fresh install with no run dirs. Function must
    return ``{}`` without raising.
    """
    runs_dir = tmp_path / "no-runs-yet"
    runs_dir.mkdir()
    overlay = _latest_check_overlay(runs_dir, "npm")
    assert overlay == {}


def test_check_overlay_handles_apply_only_run(tmp_path: Path) -> None:
    """If a run dir has only apply sidecars (operator ran ``ascendo run
    --phase apply --category npm`` without check first), the apply
    sidecar becomes the base.
    """
    run_dir = tmp_path / "runs" / "rid-apply-only"
    run_dir.mkdir(parents=True)
    _write_sidecar(run_dir, phase="apply", category="npm",
                   items=[_item("solo", current="1.0", target="2.0",
                                resolved="2.0", status="success")])
    overlay = _latest_check_overlay(tmp_path / "runs", "npm")
    assert "solo" in overlay
    assert overlay["solo"]["installed"] == "2.0"
