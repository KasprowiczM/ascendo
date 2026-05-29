"""Regression tests for ``_flush_run_to_inventory_db`` phase priority.

Operator observation on DP5520WMK (run a925b9f5, 2026-05-13):
``apply__npm.json`` reported ``@anthropic-ai/claude-code`` as
``status=success, resolved_version=2.1.140`` -- the upgrade had landed
on disk. ``check__npm.json`` from earlier in the same run reported the
same package as ``status=planned, current_version=2.1.139,
target_version=2.1.140`` -- the pre-apply state.

Post-run flush walked sidecars in filename-sorted order (apply, check,
cleanup, plan, verify). The later ``check`` row's ``planned`` status
OVERWROTE the ``apply`` row's ``success`` -- so the inventory.db
ended up showing ``installed=2.1.139, candidate=2.1.140,
status=planned`` instead of ``installed=2.1.140,
status=up_to_date``. Next dashboard navigation: "Apps still has 3
pending updates" -- but they had ALREADY been applied.

Three fixes in ``_flush_run_to_inventory_db``:
  1. Process sidecars in phase priority order (verify > apply > check
     > plan > cleanup). For each ``(category, name)`` tuple, the row
     from the highest-priority phase wins.
  2. For ``apply`` / ``verify`` success items, use ``resolved_version``
     for the ``installed`` column (the post-install reality), not
     ``current_version`` (the stale pre-install snapshot).
  3. Normalize apply / verify status strings to the check vocabulary
     (``success`` -> ``up_to_date``, ``failed`` -> ``failed``, etc.)
     so the SPA paints consistently regardless of phase.

Plus: cleanup-phase items with no version info (e.g.
``pswu.cleanup-superseded``, ``winget.source-reset``) get filtered
out so they don't pollute Apps/Categories.
"""
from __future__ import annotations

import json
from pathlib import Path

from ascendo.dashboard.inventory_db import InventoryDB
from ascendo.orchestrator.run_async import _flush_run_to_inventory_db


_BASE = Path(__file__).resolve().parent.parent / "fixtures" / "sidecars"
_FIXTURE = _BASE / "ascendo_v1_apply_winget.json"


def _write_sidecar(
    run_dir: Path,
    *,
    phase: str,
    category: str,
    items: list[dict],
    status: str = "success",
) -> None:
    """Write a sidecar to ``run_dir/<phase>__<category>.json``.

    Built off the canonical winget apply fixture; overrides phase,
    category, items, and status to match the scenario under test.
    """
    payload = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    payload["phase"] = phase
    payload["category"] = category
    payload["items"] = items
    payload["status"] = status
    payload["summary"] = {
        "total": len(items),
        "success": sum(1 for i in items if i["status"] == "success"),
        "up_to_date": sum(1 for i in items if i["status"] == "up_to_date"),
        "failed": sum(1 for i in items if i["status"] == "failed"),
        "skipped": sum(1 for i in items if i["status"] == "skipped"),
        "planned": sum(1 for i in items if i["status"] == "planned"),
        "partial": sum(1 for i in items if i["status"] == "partial"),
        "triggered": sum(1 for i in items if i["status"] == "triggered"),
    }
    out = run_dir / f"{phase}__{category}.json"
    out.write_text(json.dumps(payload), encoding="utf-8")


def _npm_item(
    name: str,
    *,
    current: str | None,
    target: str | None,
    resolved: str | None = None,
    status: str = "planned",
) -> dict:
    return {
        "id": name,
        "name": name,
        "category": "npm",
        "source": {"type": "npm", "feed": None, "url": None},
        "current_version": current,
        "target_version": target,
        "resolved_version": resolved,
        "status": status,
        "exit_code": None,
        "duration_ms": None,
        "evidence": None,
        "rollback": None,
        "messages": [],
    }


def test_apply_success_overrides_check_planned_for_same_package(
    tmp_path: Path,
) -> None:
    """The exact DP5520WMK 2026-05-13 regression: same package in two
    sidecars of one run; apply's success must win over check's planned.
    """
    db = InventoryDB(tmp_path / "inv.db")
    run = tmp_path / "runs" / "rid-abc"
    run.mkdir(parents=True)

    pkg = "@anthropic-ai/claude-code"
    _write_sidecar(
        run, phase="check", category="npm",
        items=[_npm_item(pkg, current="2.1.139", target="2.1.140", status="planned")],
    )
    _write_sidecar(
        run, phase="apply", category="npm",
        items=[_npm_item(pkg, current="2.1.139", target="2.1.140",
                         resolved="2.1.140", status="success")],
    )

    _flush_run_to_inventory_db(run, db)
    rows = db.query(category="npm")
    assert len(rows) == 1, f"expected one row, got {len(rows)}: {rows}"
    row = rows[0]
    assert row["name"] == pkg
    assert row["installed"] == "2.1.140", (
        "apply.resolved_version should be the inventory installed version"
    )
    assert row["status"] == "up_to_date", (
        "apply 'success' must normalize to 'up_to_date' for inventory"
    )


def test_check_does_not_clobber_prior_apply_success_in_same_run(
    tmp_path: Path,
) -> None:
    """Even if filesystem order returns check sidecar AFTER apply, the
    apply row must win.
    """
    db = InventoryDB(tmp_path / "inv.db")
    run = tmp_path / "runs" / "rid-def"
    run.mkdir(parents=True)

    # Multiple packages, only one with an apply row
    _write_sidecar(
        run, phase="check", category="npm",
        items=[
            _npm_item("pkg-a", current="1.0", target="2.0", status="planned"),
            _npm_item("pkg-b", current="3.0", target="3.0", status="up_to_date"),
        ],
    )
    _write_sidecar(
        run, phase="apply", category="npm",
        items=[
            _npm_item("pkg-a", current="1.0", target="2.0",
                     resolved="2.0", status="success"),
        ],
    )

    _flush_run_to_inventory_db(run, db)
    rows = {r["name"]: r for r in db.query(category="npm")}
    assert rows["pkg-a"]["installed"] == "2.0"
    assert rows["pkg-a"]["status"] == "up_to_date"
    # pkg-b only appeared in check; preserve as up_to_date.
    assert rows["pkg-b"]["installed"] == "3.0"
    assert rows["pkg-b"]["status"] == "up_to_date"


def test_verify_overrides_apply_when_both_present(tmp_path: Path) -> None:
    """``verify`` is the post-apply ground-truth scan; its row should
    win when both apply and verify exist for the same package.
    """
    db = InventoryDB(tmp_path / "inv.db")
    run = tmp_path / "runs" / "rid-ghi"
    run.mkdir(parents=True)

    # Apply reported success; verify discovered the package wasn't
    # actually installed (e.g. installer rolled back silently).
    _write_sidecar(
        run, phase="apply", category="npm",
        items=[_npm_item("flaky", current="1.0", target="2.0",
                         resolved="2.0", status="success")],
    )
    _write_sidecar(
        run, phase="verify", category="npm",
        items=[_npm_item("flaky", current="1.0", target="2.0",
                         status="failed")],
    )

    _flush_run_to_inventory_db(run, db)
    rows = db.query(category="npm")
    assert len(rows) == 1
    # verify.failed normalizes to 'failed' in honest-status inventory taxonomy
    assert rows[0]["status"] == "failed"


def test_apply_failed_maps_to_failed_not_success(tmp_path: Path) -> None:
    """Operator must see "failed" for apps where the install failed;
    they're still on the old version.
    """
    db = InventoryDB(tmp_path / "inv.db")
    run = tmp_path / "runs" / "rid-jkl"
    run.mkdir(parents=True)

    _write_sidecar(
        run, phase="apply", category="npm",
        items=[_npm_item("broken", current="0.1", target="0.2",
                         status="failed")],
    )

    _flush_run_to_inventory_db(run, db)
    rows = db.query(category="npm")
    assert len(rows) == 1
    assert rows[0]["installed"] == "0.1", (
        "Failed apply: installed stays at current_version"
    )
    assert rows[0]["status"] == "failed"


def test_cleanup_phase_infra_items_filtered_out(tmp_path: Path) -> None:
    """``cleanup__winget.json`` items like ``winget.source-reset``
    represent phase actions, not packages. They have no version info
    and must NOT pollute the inventory.
    """
    db = InventoryDB(tmp_path / "inv.db")
    run = tmp_path / "runs" / "rid-mno"
    run.mkdir(parents=True)

    # The exact pattern observed on DP5520WMK
    _write_sidecar(
        run, phase="cleanup", category="winget",
        items=[
            {
                "id": "winget.source-reset",
                "name": "winget source reset",
                "category": "winget",
                "source": {"type": "winget", "feed": None, "url": None},
                "current_version": None,
                "target_version": None,
                "resolved_version": None,
                "status": "success",
                "exit_code": 0,
                "duration_ms": None,
                "evidence": None,
                "rollback": None,
                "messages": [],
            },
        ],
    )

    _flush_run_to_inventory_db(run, db)
    rows = db.query(category="winget")
    assert rows == [], (
        f"Cleanup infra row leaked into inventory: {rows}"
    )


def test_cleanup_with_real_version_info_is_kept(tmp_path: Path) -> None:
    """The cleanup filter is intentionally narrow: it only drops rows
    with NO version info at all. If a future cleanup script emits a
    real package row (e.g. autoremove dependent libraries), keep it.
    """
    db = InventoryDB(tmp_path / "inv.db")
    run = tmp_path / "runs" / "rid-pqr"
    run.mkdir(parents=True)

    _write_sidecar(
        run, phase="cleanup", category="npm",
        items=[_npm_item("zombie-dep", current="0.5", target="0.5",
                         status="success")],
    )

    _flush_run_to_inventory_db(run, db)
    rows = db.query(category="npm")
    assert len(rows) == 1
    assert rows[0]["name"] == "zombie-dep"


def test_status_normalization_apply_success_to_up_to_date(
    tmp_path: Path,
) -> None:
    """Single point-test of the status map: apply 'success' must
    surface as 'up_to_date' so the SPA's category-status pill is
    consistent with check rows.
    """
    db = InventoryDB(tmp_path / "inv.db")
    run = tmp_path / "runs" / "rid-stu"
    run.mkdir(parents=True)
    _write_sidecar(
        run, phase="apply", category="npm",
        items=[_npm_item("normal", current="1.0", target="2.0",
                         resolved="2.0", status="success")],
    )
    _flush_run_to_inventory_db(run, db)
    assert db.query(category="npm")[0]["status"] == "up_to_date"


def test_status_normalization_triggered_to_triggered_pending(tmp_path: Path) -> None:
    """``triggered`` (web Tier-B handler kicked an async updater) is
    now honestly reported as ``triggered_pending`` — the update was
    kicked but not yet reconciled. The SPA should show this as pending.
    """
    db = InventoryDB(tmp_path / "inv.db")
    run = tmp_path / "runs" / "rid-vwx"
    run.mkdir(parents=True)
    _write_sidecar(
        run, phase="apply", category="web",
        items=[_npm_item("notion", current="3.0", target="3.1",
                         status="triggered")],
    )
    _flush_run_to_inventory_db(run, db)
    rows = db.query(category="web")
    assert len(rows) == 1
    assert rows[0]["status"] == "triggered_pending"
