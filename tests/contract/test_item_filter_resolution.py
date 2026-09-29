"""Item-filter routing: per-category mapping, all-excluded, run metadata.

Covers ``_resolve_item_filter`` (per-category identifiers), the runner's
``_filter_for`` (a manager only receives its own category's list) and the
all-excluded rule (an empty list means "nothing to do", never "no filter").
"""
from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

import pytest

from ascendo.dashboard.routes import apps as apps_mod
from ascendo.dashboard.routes.runs import _resolve_item_filter
from ascendo.interfaces.package_manager import IPackageManager
from ascendo.models.host import ElevationMethod, HostInfo, OperatingSystem
from ascendo.models.package import SourceType
from ascendo.models.result import Summary
from ascendo.models.run import Phase, PhaseStatus, RunInfo, Trigger
from ascendo.models.sidecar import Sidecar, SidecarSchema, ToolInfo
from ascendo.orchestrator import run_phases
from ascendo.orchestrator.runner import NEEDS_CHECK, RUN_META_FILENAME
from ascendo.orchestrator.sidecar_io import list_run_sidecars, read_sidecar

from .test_runner import FakeAdapter, FakeManager  # noqa: F401  (shared fakes)


# ── _resolve_item_filter ─────────────────────────────────────────────────────


def _check_sidecar(runs_dir: Path, category: str, items: list[tuple[str, str]]) -> None:
    run_dir = runs_dir / "run-1"
    run_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": "ascendo/v1", "phase": "check", "category": category,
        "items": [{"id": i, "name": n, "status": "up_to_date"} for i, n in items],
    }
    (run_dir / f"check__{category}.json").write_text(json.dumps(payload))


def _resolve(tmp_path: Path, monkeypatch, excluded: set[str], cats: list[str]):
    monkeypatch.setattr(apps_mod, "excluded_keys", lambda: excluded)
    return _resolve_item_filter(None, cats, (Phase.APPLY,), tmp_path)


def test_only_categories_with_an_exclusion_get_an_entry(tmp_path, monkeypatch):
    _check_sidecar(tmp_path, "brew", [("uv", "uv"), ("jq", "jq")])
    _check_sidecar(tmp_path, "npm", [("node", "node")])
    out = _resolve(tmp_path, monkeypatch, {"brew:jq"}, ["brew", "npm"])
    assert out == {"brew": ["uv"]}          # npm: no exclusion -> no filter at all


def test_all_excluded_category_is_an_empty_list_not_missing(tmp_path, monkeypatch):
    _check_sidecar(tmp_path, "brew", [("uv", "uv"), ("jq", "jq")])
    out = _resolve(tmp_path, monkeypatch, {"brew:uv", "brew:jq"}, ["brew"])
    assert out == {"brew": []}


def test_web_filter_carries_slugs_not_prefixed_ids(tmp_path, monkeypatch):
    _check_sidecar(tmp_path, "web", [("web:chrome", "web:chrome"), ("web:brave", "web:brave")])
    out = _resolve(tmp_path, monkeypatch, {"web:web:brave"}, ["web"])
    assert out == {"web": ["chrome"]}
    out = _resolve(tmp_path, monkeypatch, {"web:brave"}, ["web"])   # slug-form key
    assert out == {"web": ["chrome"]}


def test_mas_filter_carries_display_names(tmp_path, monkeypatch):
    _check_sidecar(tmp_path, "mas", [("Keka", "Keka"), ("Copilot", "Copilot")])
    out = _resolve(tmp_path, monkeypatch, {"mas:Copilot"}, ["mas"])
    assert out == {"mas": ["Keka"]}


def test_softwareupdate_filter_carries_labels(tmp_path, monkeypatch):
    lbl = "macOS Sequoia 15.1-24B83"
    _check_sidecar(tmp_path, "softwareupdate", [(lbl, lbl), ("Safari17.4-17.4", "Safari17.4-17.4")])
    out = _resolve(tmp_path, monkeypatch, {"softwareupdate:Safari17.4-17.4"}, ["softwareupdate"])
    assert out == {"softwareupdate": [lbl]}      # every remaining label, not just one


def test_exclusion_survives_a_check_sidecar_that_already_dropped_the_item(tmp_path, monkeypatch):
    """Run 1 filtered `abseil` out, so run 2's latest check sidecar no longer
    lists it. The exclusion is still configured -> apply must STILL receive a
    filter (that omits it), never fall back to unfiltered."""
    _check_sidecar(tmp_path, "brew", [("uv", "uv"), ("jq", "jq")])   # no abseil
    _check_sidecar(tmp_path, "npm", [("node", "node")])
    out = _resolve(tmp_path, monkeypatch, {"brew:abseil"}, ["brew", "npm"])
    assert out == {"brew": ["uv", "jq"]}
    assert "abseil" not in out["brew"]
    assert "npm" not in out          # no exclusion key for npm -> unfiltered


def test_exclusion_is_self_perpetuating_across_runs(tmp_path, monkeypatch):
    """Two consecutive resolutions, the second reading the sidecar the
    first (filtered) run would have produced."""
    _check_sidecar(tmp_path, "mas", [("Keka", "Keka"), ("Copilot", "Copilot")])
    first = _resolve(tmp_path, monkeypatch, {"mas:Copilot"}, ["mas"])
    assert first == {"mas": ["Keka"]}
    # the filtered check of run 1 only lists what passed the filter
    (tmp_path / "run-1" / "check__mas.json").unlink()
    _check_sidecar(tmp_path, "mas", [(n, n) for n in first["mas"]])
    second = _resolve(tmp_path, monkeypatch, {"mas:Copilot"}, ["mas"])
    assert second == first


def test_no_check_data_with_an_exclusion_is_needs_check_not_unfiltered(tmp_path, monkeypatch):
    """Fail-safe: exclusion configured but nothing to build the inclusion list
    from -> the category is marked NEEDS_CHECK (never silently unfiltered)."""
    out = _resolve(tmp_path, monkeypatch, {"brew:jq"}, ["brew"])
    assert out == {"brew": NEEDS_CHECK}


def test_no_check_data_without_exclusion_keys_is_unfiltered(tmp_path, monkeypatch):
    _check_sidecar(tmp_path, "npm", [("node", "node")])
    out = _resolve(tmp_path, monkeypatch, {"brew:jq"}, ["npm", "mas"])
    assert out is None


# ── runner: mapping applied per manager ──────────────────────────────────────


@pytest.fixture
def host() -> HostInfo:
    return HostInfo(
        hostname="testbox", os=OperatingSystem.MACOS, os_version="15.0",
        arch="arm64", user="t", is_elevated=False,
        elevation_method=ElevationMethod.NONE,
    )


@pytest.fixture
def run() -> RunInfo:
    return RunInfo(
        id=UUID("12345678-1234-1234-1234-123456789abd"), trigger=Trigger.CLI,
        profile="full", dry_run=False,
        started_at=datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc),
    )


def test_mapping_is_applied_per_manager(tmp_path, host, run):
    brew = FakeManager(SourceType.BREW)
    mas = FakeManager(SourceType.MAS)
    npm = FakeManager(SourceType.NPM)
    run_phases(
        FakeAdapter([brew, mas, npm]), run, host,
        phases=[Phase.APPLY], base_dir=tmp_path,
        item_filter={"brew": ["uv"], "mas": ["Keka", "1Password"]},
    )
    assert brew.calls == [(Phase.APPLY, ["uv"])]
    assert mas.calls == [(Phase.APPLY, ["Keka", "1Password"])]
    assert npm.calls == [(Phase.APPLY, None)]        # missing key = no filter


def test_flat_filter_is_still_forwarded_to_every_manager(tmp_path, host, run):
    a, b = FakeManager(SourceType.BREW), FakeManager(SourceType.MAS)
    run_phases(FakeAdapter([a, b]), run, host, phases=[Phase.APPLY],
               base_dir=tmp_path, item_filter=["x"])
    assert a.calls == [(Phase.APPLY, ["x"])] and b.calls == [(Phase.APPLY, ["x"])]


def test_all_excluded_category_skips_apply_and_cleanup_never_unfiltered(tmp_path, host, run):
    mas = FakeManager(SourceType.MAS)
    brew = FakeManager(SourceType.BREW)
    report = run_phases(
        FakeAdapter([mas, brew]), run, host,
        phases=[Phase.CHECK, Phase.APPLY, Phase.CLEANUP], base_dir=tmp_path,
        item_filter={"mas": []},
    )
    # check still runs (unfiltered listing); apply/cleanup are NOT run for mas.
    assert [p for p, _ in mas.calls] == [Phase.CHECK]
    assert [p for p, _ in brew.calls] == [Phase.CHECK, Phase.APPLY, Phase.CLEANUP]
    skipped = [s for s in report.sidecars
               if s.category is SourceType.MAS and s.phase in (Phase.APPLY, Phase.CLEANUP)]
    assert len(skipped) == 2
    for sc in skipped:
        assert sc.status is PhaseStatus.SKIPPED
        assert sc.items == []
        assert any(m.code == "ALL_EXCLUDED" for m in sc.messages)
        # persisted like every other sidecar
        assert read_sidecar(tmp_path / str(run.id) / f"{sc.phase.value}__mas.json").status is PhaseStatus.SKIPPED


def test_run_meta_written_with_skips_and_abort(tmp_path, host, run):
    ok = FakeManager(SourceType.BREW)
    down = FakeManager(SourceType.MAS, is_avail=False)
    ex = FakeManager(SourceType.NPM)
    run_phases(
        FakeAdapter([ok, down, ex]), run, host,
        phases=[Phase.CHECK, Phase.APPLY], base_dir=tmp_path,
        item_filter={"npm": []},
    )
    meta = json.loads((tmp_path / str(run.id) / RUN_META_FILENAME).read_text())
    assert meta["aborted_after_phase"] is None
    assert meta["cancelled"] is False
    assert meta["status"] == "success"
    reasons = {tuple(x) for x in meta["skipped_managers"]}
    assert ("mas", "is_available() returned False") in reasons
    assert ("npm", "all items excluded") in reasons


def test_run_meta_records_aborted_after_phase(tmp_path, host, run):
    bad = FakeManager(SourceType.BREW, status=PhaseStatus.FAILED)
    run_phases(FakeAdapter([bad]), run, host, phases=[Phase.CHECK, Phase.APPLY],
               base_dir=tmp_path, stop_on_failure=True)
    meta = json.loads((tmp_path / str(run.id) / RUN_META_FILENAME).read_text())
    assert meta["aborted_after_phase"] == "check"
    assert meta["status"] == "failed"


def test_report_from_a_real_run_surfaces_a_crashed_verify(tmp_path, host, run):
    """End to end: the runner synthesizes a failed verify sidecar for a
    crashed manager and REPORT.md must say so instead of 'all good'."""
    mas = FakeManager(SourceType.MAS, raise_on_phases=(Phase.VERIFY,))
    run_phases(FakeAdapter([mas]), run, host,
               phases=[Phase.CHECK, Phase.APPLY, Phase.VERIFY],
               base_dir=tmp_path, stop_on_failure=False)
    md = (tmp_path / str(run.id) / "REPORT.md").read_text()
    assert "Problems and unverified steps" in md
    assert "Mac App Store" in md
    assert "fake crash on verify" in md
    assert "All updates applied successfully" not in md
    assert "Nothing to update" not in md


def test_run_meta_is_not_mistaken_for_a_sidecar(tmp_path, host, run):
    """run_meta.json lives next to the sidecars; every sidecar reader
    (dashboard GET /runs/{id}, inventory flush, report) must skip it."""
    run_phases(FakeAdapter([FakeManager(SourceType.BREW)]), run, host,
               phases=[Phase.CHECK], base_dir=tmp_path)
    run_dir = tmp_path / str(run.id)
    assert (run_dir / RUN_META_FILENAME).is_file()
    assert [p.name for p in list_run_sidecars(run_dir)] == ["check__brew.json"]


def test_needs_check_skips_mutating_phases_and_runs_the_rest(tmp_path, host, run):
    mas = FakeManager(SourceType.MAS)
    brew = FakeManager(SourceType.BREW)
    report = run_phases(
        FakeAdapter([mas, brew]), run, host,
        phases=[Phase.CHECK, Phase.PLAN, Phase.APPLY, Phase.VERIFY, Phase.CLEANUP],
        base_dir=tmp_path, item_filter={"mas": NEEDS_CHECK},
    )
    # apply/cleanup never reach the mas manager; check/plan/verify run unfiltered
    assert mas.calls == [(Phase.CHECK, None), (Phase.PLAN, None), (Phase.VERIFY, None)]
    assert [p for p, _ in brew.calls] == [
        Phase.CHECK, Phase.PLAN, Phase.APPLY, Phase.VERIFY, Phase.CLEANUP]
    skipped = [s for s in report.sidecars
               if s.category is SourceType.MAS and s.phase in (Phase.APPLY, Phase.CLEANUP)]
    assert len(skipped) == 2
    for sc in skipped:
        assert sc.status is PhaseStatus.SKIPPED and sc.items == []
        msg = next(m for m in sc.messages if m.code == "EXCLUSIONS_NEED_CHECK")
        assert "run check first" in msg.text
        persisted = read_sidecar(tmp_path / str(run.id) / f"{sc.phase.value}__mas.json")
        assert persisted.status is PhaseStatus.SKIPPED
    meta = json.loads((tmp_path / str(run.id) / RUN_META_FILENAME).read_text())
    assert any(c == "mas" and "need a check first" in r for c, r in meta["skipped_managers"])
    md = (tmp_path / str(run.id) / "REPORT.md").read_text()
    assert "Skipped: Mac App Store (exclusions need a check first" in md


def test_excluded_app_is_not_upgraded_when_there_is_no_check_data(tmp_path, monkeypatch, host, run):
    """End to end: real resolver + runner, empty runs dir (no check sidecar),
    exclusion configured -> the apply phase of that category never runs."""
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    monkeypatch.setattr(apps_mod, "excluded_keys", lambda: {"mas:Copilot"})
    flt = _resolve_item_filter(None, ["mas", "brew"], (Phase.APPLY,), runs_dir)
    mas, brew = FakeManager(SourceType.MAS), FakeManager(SourceType.BREW)
    run_phases(FakeAdapter([mas, brew]), run, host, phases=[Phase.APPLY],
               base_dir=tmp_path / "out", item_filter=flt)
    assert mas.calls == []                                   # excluded app safe
    assert brew.calls == [(Phase.APPLY, None)]               # unrelated category unaffected
