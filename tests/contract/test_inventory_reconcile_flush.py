"""Task 15 / Spec 8 -- drift-free reconcile wired into the post-run flush.

A FULL, successful, complete check of an opted-in category evicts the rows a
previous run wrote for packages that are gone; anything uncertain (failed /
partial / filtered / empty / probe-marker scan) never deletes, and rows written
by a live scan (not by a run) are never touched.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from ascendo.dashboard.inventory_db import InventoryDB
from ascendo.orchestrator.run_async import _flush_run_to_inventory_db

_FIXTURE = (Path(__file__).resolve().parent.parent / "fixtures" / "sidecars"
            / "ascendo_v1_apply_winget.json")
CATS = frozenset({"brew", "mas", "npm", "pip", "web"})


def _item(name: str, cat: str, *, current="1.0", target=None, status="up_to_date",
          feed=None, resolved=None, item_id=None) -> dict:
    return {"id": item_id or name, "name": item_id or name, "category": cat,
            "source": {"type": cat, "feed": feed, "url": None},
            "current_version": current, "target_version": target,
            "resolved_version": resolved, "status": status, "exit_code": None,
            "duration_ms": None, "evidence": None, "rollback": None, "messages": []}


def _sidecar(run: Path, phase: str, cat: str, items: list[dict], *, status="success",
             messages: list[dict] | None = None) -> None:
    p = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    p.update(phase=phase, category=cat, items=items, status=status,
             messages=messages or [])
    p["summary"] = {k: sum(1 for i in items if i["status"] == k)
                    for k in ("success", "up_to_date", "failed", "skipped", "planned",
                              "partial", "triggered")}
    p["summary"]["total"] = len(items)
    (run / f"{phase}__{cat}.json").write_text(json.dumps(p), encoding="utf-8")


def _flush(run: Path, db, **kw):
    kw.setdefault("reconcile_categories", CATS)
    kw.setdefault("full_scan", True)
    return _flush_run_to_inventory_db(run, db, **kw)


def _names(db, cat):
    return sorted(r["name"] for r in db.query(category=cat))


@pytest.fixture
def db(tmp_path):
    return InventoryDB(tmp_path / "inv.db")


@pytest.fixture
def run(tmp_path):
    d = tmp_path / "runs" / "rid"
    d.mkdir(parents=True)
    return d


def test_uninstalled_package_disappears_after_a_full_check(db, run, tmp_path):
    old = tmp_path / "runs" / "old"; old.mkdir()
    _sidecar(old, "check", "npm", [_item("keep", "npm"), _item("gone", "npm")])
    _flush(old, db)
    assert _names(db, "npm") == ["gone", "keep"]
    _sidecar(run, "check", "npm", [_item("keep", "npm")])
    _flush(run, db)
    assert _names(db, "npm") == ["keep"]
    meta = db.get_scan_meta("npm")
    assert meta and meta["item_count"] == 1


def test_filtered_run_never_reconciles(db, run, tmp_path):
    old = tmp_path / "runs" / "old"; old.mkdir()
    _sidecar(old, "check", "npm", [_item("keep", "npm"), _item("other", "npm")])
    _flush(old, db, full_scan=False)
    _sidecar(run, "check", "npm", [_item("keep", "npm")])
    _flush(run, db, full_scan=False)
    assert _names(db, "npm") == ["keep", "other"]
    assert db.get_scan_meta("npm") is None


def test_adapter_without_reconcile_categories_is_untouched(db, run, tmp_path):
    old = tmp_path / "runs" / "old"; old.mkdir()
    _sidecar(old, "check", "npm", [_item("keep", "npm"), _item("other", "npm")])
    _flush(old, db, full_scan=False)
    _sidecar(run, "check", "npm", [_item("keep", "npm")])
    _flush_run_to_inventory_db(run, db)                   # Windows / Ubuntu call shape
    assert _names(db, "npm") == ["keep", "other"]
    assert db.get_scan_meta("npm") is None


@pytest.mark.parametrize("kind", ["empty", "failed", "partial", "error-msg", "probe-marker"])
def test_uncertain_scans_never_delete_and_never_mark_complete(db, run, tmp_path, kind):
    old = tmp_path / "runs" / "old"; old.mkdir()
    _sidecar(old, "check", "mas", [_item("Keka", "mas"), _item("1Password", "mas")])
    _flush(old, db, full_scan=False)
    assert db.get_scan_meta("mas") is None
    if kind == "empty":
        _sidecar(run, "check", "mas", [])
    elif kind == "failed":
        _sidecar(run, "check", "mas", [_item("Keka", "mas")], status="failed")
    elif kind == "partial":
        _sidecar(run, "check", "mas", [_item("Keka", "mas")], status="partial")
    elif kind == "error-msg":
        _sidecar(run, "check", "mas", [_item("Keka", "mas")],
                 messages=[{"level": "error", "text": "mas list exploded"}])
    else:   # list probe failed: only a marker item survives
        _sidecar(run, "check", "mas", [_item("mas:list-probe", "mas", current=None,
                                             status="skipped")],
                 messages=[{"level": "warn", "text": "mas list timed out"}])
    _flush(run, db)
    assert {"Keka", "1Password"} <= set(_names(db, "mas"))
    assert db.get_scan_meta("mas") is None


def test_rows_written_by_a_live_scan_are_not_evicted(db, run):
    db.bulk_upsert([{"category": "mas", "name": "UniFi", "installed": "1", "status": "ok",
                     "source_type": "mas"}])                      # live scan: no origin tag
    _sidecar(run, "check", "mas", [_item("Keka", "mas")])
    _flush(run, db)
    assert _names(db, "mas") == ["Keka", "UniFi"]


def test_cask_orphan_is_not_a_seen_app_and_is_evicted(db, run, tmp_path):
    old = tmp_path / "runs" / "old"; old.mkdir()
    _sidecar(old, "check", "brew", [_item("zzz-gone", "brew"), _item("wget", "brew")])
    _flush(old, db, full_scan=False)
    _sidecar(run, "check", "brew",
             [_item("zzz-gone", "brew", status="skipped", target="2.0"),
              _item("wget", "brew")],
             messages=[{"level": "warn", "code": "CASK_ORPHAN",
                        "text": "zzz-gone: skipped -- app Gone.app was removed manually"}])
    _flush(run, db)
    assert _names(db, "brew") == ["wget"]
    assert db.get_scan_meta("brew")["item_count"] == 1


def test_mas_numeric_apply_ids_resolve_to_the_display_name_key(db, run):
    _sidecar(run, "check", "mas",
             [_item("Keka", "mas", current="1.0", target="1.1", status="planned",
                    feed="mas-id:1153157709")])
    _sidecar(run, "apply", "mas",
             [_item("1153157709", "mas", current="1.0", target="1.1", resolved="1.1",
                    status="success")])
    _flush(run, db)
    rows = db.query(category="mas")
    assert [(r["name"], r["installed"], r["status"]) for r in rows] == [
        ("Keka", "1.1", "up_to_date")]            # one row, not Keka + 1153157709


def test_category_outside_the_opt_in_set_is_not_reconciled(db, run, tmp_path):
    old = tmp_path / "runs" / "old"; old.mkdir()
    _sidecar(old, "check", "winget", [_item("a", "winget"), _item("b", "winget")])
    _flush(old, db)
    _sidecar(run, "check", "winget", [_item("a", "winget")])
    _flush(run, db)
    assert _names(db, "winget") == ["a", "b"]


# -- spa_real: completeness, not row count -------------------------------------

class _A:
    def detect_host(self):
        return None

    def package_managers(self, host):
        class _M:
            category = "web"
        return [_M()]


def _stale(n=5):
    return [{"name": f"Helper{i}", "installed": "1", "status": "ok", "source": "web"}
            for i in range(n)]


def _seed(tmp_path, complete):
    from ascendo.dashboard.routes.spa_real import _seed_buckets_from_sidecars
    return _seed_buckets_from_sidecars({"web": _stale()}, _A(), tmp_path / "runs", complete)


def _good_scan(tmp_path):
    runs = tmp_path / "runs" / "good"
    runs.mkdir(parents=True)
    _sidecar(runs, "check", "web", [_item("web:one", "web"), _item("web:two", "web")])
    return runs


def test_trusted_complete_scan_beats_a_larger_stale_bucket(tmp_path):
    _good_scan(tmp_path)
    assert len(_seed(tmp_path, {})["web"]) == 5           # legacy rule: bigger bucket wins
    out = _seed(tmp_path, {"web": 2})                      # scan_meta says 2 -> sidecar matches
    assert sorted(i["name"] for i in out["web"]) == ["web:one", "web:two"]


@pytest.mark.parametrize("kind", ["failed", "partial", "error-msg", "probe-marker", "filtered",
                                  "empty"])
def test_later_untrusted_check_never_shrinks_a_bucket_after_a_good_scan(tmp_path, kind):
    import os
    import time
    _good_scan(tmp_path)
    later = tmp_path / "runs" / "later"
    later.mkdir()
    one = [_item("web:one", "web")]
    if kind == "failed":
        _sidecar(later, "check", "web", one, status="failed")
    elif kind == "partial":
        _sidecar(later, "check", "web", one, status="partial")
    elif kind == "error-msg":
        _sidecar(later, "check", "web", one,
                 messages=[{"level": "error", "text": "boom"}])
    elif kind == "probe-marker":
        _sidecar(later, "check", "web", [_item("web:list-probe", "web", current=None,
                                               status="skipped"), *one, _item("web:two", "web")])
    elif kind == "filtered":
        _sidecar(later, "check", "web", one)               # 1 item vs recorded 2
    else:
        _sidecar(later, "check", "web", [])
    t = time.time() + 5
    for f in later.iterdir():
        os.utime(f, (t, t))
    os.utime(later, (t, t))
    assert len(_seed(tmp_path, {"web": 2})["web"]) == 5    # existing bucket kept
