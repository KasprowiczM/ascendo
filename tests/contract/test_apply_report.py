"""Contract tests for the human-readable apply report.

The report is auto-generated at the end of any run that includes an
``apply`` phase. These tests build sidecars on disk via the same
:func:`write_sidecar` path the orchestrator uses, then drive the
generator + CLI + dashboard endpoint and assert on the rendered output.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from ascendo.models.host import ElevationMethod, HostInfo, OperatingSystem
from ascendo.models.package import ItemSource, SourceType
from ascendo.models.result import Item, ItemStatus, Message, MessageLevel, Summary
from ascendo.models.run import Phase, PhaseStatus, RunInfo, Trigger
from ascendo.models.sidecar import Sidecar, SidecarSchema, ToolInfo
from ascendo.orchestrator.report import REPORT_FILENAME, generate_apply_report
from ascendo.orchestrator.sidecar_io import write_sidecar
from ._report_fixtures import (
    _RUN_ID,
    _build_item,
    _build_sidecar,
    _host,
    _run,
    _write,
    run_dir,
)


# ── Tests: generator ────────────────────────────────────────────────────────


def test_generate_apply_report_renders_basic_run(run_dir: Path) -> None:
    """Single-category apply run with one upgrade renders cleanly."""
    started = datetime(2026, 5, 8, 22, 47, tzinfo=timezone.utc)
    items = [
        _build_item(
            item_id="glib", name="glib",
            category=SourceType.BREW,
            status=ItemStatus.SUCCESS,
            current="2.88.0", target="2.88.1", resolved="2.88.1",
        ),
    ]
    _write(run_dir, _build_sidecar(
        phase=Phase.APPLY, category=SourceType.BREW, items=items, started=started,
    ))

    md = generate_apply_report(run_dir)

    assert md is not None
    assert "# Ascendo update" in md
    assert "testbox" in md
    assert "1 app upgraded" in md
    assert "Homebrew" in md
    assert "glib" in md
    assert "2.88.0" in md and "2.88.1" in md
    # File was actually written.
    assert (run_dir / REPORT_FILENAME).is_file()


def test_generate_apply_report_handles_failed_items(run_dir: Path) -> None:
    """Failed items appear in a Failed section with the error message."""
    started = datetime(2026, 5, 8, 22, 47, tzinfo=timezone.utc)
    items = [
        _build_item(
            item_id="web:obsidian", name="web:obsidian",
            category=SourceType.WEB,
            status=ItemStatus.FAILED,
            current="1.12.7",
            messages=[Message(level=MessageLevel.ERROR, text="handler exit 26: download timed out")],
        ),
    ]
    _write(run_dir, _build_sidecar(
        phase=Phase.APPLY, category=SourceType.WEB, items=items, started=started,
    ))

    md = generate_apply_report(run_dir)

    assert md is not None
    assert "## Failed (1 app)" in md
    assert "Obsidian" in md
    assert "handler exit 26" in md or "download timed out" in md


def test_generate_apply_report_surfaces_needs_reboot(run_dir: Path) -> None:
    """The reboot banner is rendered prominently near the top."""
    started = datetime(2026, 5, 8, 22, 47, tzinfo=timezone.utc)
    items = [
        _build_item(
            item_id="macOS-15.1", name="macOS 15.1",
            category=SourceType.SOFTWAREUPDATE,
            status=ItemStatus.SUCCESS,
            current="14.5", target="15.1", resolved="15.1",
        ),
    ]
    _write(run_dir, _build_sidecar(
        phase=Phase.APPLY, category=SourceType.SOFTWAREUPDATE,
        items=items, started=started, needs_reboot=True,
    ))

    md = generate_apply_report(run_dir)

    assert md is not None
    # Banner before the "What changed" section.
    banner_idx = md.find("system reboot is required")
    changed_idx = md.find("## What changed")
    assert banner_idx >= 0
    assert changed_idx > banner_idx, "reboot banner must come before What changed"


def test_generate_apply_report_groups_categories(run_dir: Path) -> None:
    """Multi-category run produces sections in canonical order."""
    started = datetime(2026, 5, 8, 22, 47, tzinfo=timezone.utc)
    _write(run_dir, _build_sidecar(
        phase=Phase.APPLY, category=SourceType.WEB,
        items=[_build_item(item_id="web:docker", name="web:docker",
                           category=SourceType.WEB, status=ItemStatus.SUCCESS,
                           current="4.71.0", target="4.72.0", resolved="4.72.0")],
        started=started,
    ))
    _write(run_dir, _build_sidecar(
        phase=Phase.APPLY, category=SourceType.BREW,
        items=[_build_item(item_id="glib", name="glib",
                           category=SourceType.BREW, status=ItemStatus.SUCCESS,
                           current="2.88.0", target="2.88.1", resolved="2.88.1")],
        started=started,
    ))
    _write(run_dir, _build_sidecar(
        phase=Phase.APPLY, category=SourceType.SOFTWAREUPDATE,
        items=[_build_item(item_id="kb-1", name="security update",
                           category=SourceType.SOFTWAREUPDATE, status=ItemStatus.SUCCESS,
                           current="-", target="2026.05.01", resolved="2026.05.01")],
        started=started,
    ))

    md = generate_apply_report(run_dir)

    assert md is not None
    su_idx = md.find("macOS system updates")
    brew_idx = md.find("### Homebrew")
    web_idx = md.find("Web apps")
    assert 0 <= su_idx < brew_idx < web_idx


def test_generate_apply_report_omits_check_only_runs(run_dir: Path) -> None:
    """Runs with no apply phase return None and write no file."""
    started = datetime(2026, 5, 8, 22, 47, tzinfo=timezone.utc)
    _write(run_dir, _build_sidecar(
        phase=Phase.CHECK, category=SourceType.BREW,
        items=[_build_item(item_id="glib", name="glib",
                           category=SourceType.BREW, status=ItemStatus.UP_TO_DATE,
                           current="2.88.0", target="2.88.0")],
        started=started,
    ))

    md = generate_apply_report(run_dir)

    assert md is None
    assert not (run_dir / REPORT_FILENAME).exists()


def test_generate_apply_report_counts_check_baseline(run_dir: Path) -> None:
    """Already-up-to-date counts come from the check phase, not apply."""
    started = datetime(2026, 5, 8, 22, 47, tzinfo=timezone.utc)
    # Apply: nothing happened (0 items emitted — common when nothing outdated).
    _write(run_dir, _build_sidecar(
        phase=Phase.APPLY, category=SourceType.BREW,
        items=[], started=started,
    ))
    # Check: 152 packages, all up-to-date.
    items_ud = [
        _build_item(
            item_id=f"pkg-{i}", name=f"pkg-{i}",
            category=SourceType.BREW, status=ItemStatus.UP_TO_DATE,
            current="1.0", target="1.0",
        )
        for i in range(152)
    ]
    _write(run_dir, _build_sidecar(
        phase=Phase.CHECK, category=SourceType.BREW,
        items=items_ud, started=started,
    ))

    md = generate_apply_report(run_dir)
    assert md is not None
    assert "152 packages" in md
    assert "Already up-to-date" in md


def test_generate_apply_report_triggered_section(run_dir: Path) -> None:
    """Tier-B 'triggered' items get their own section."""
    started = datetime(2026, 5, 8, 22, 47, tzinfo=timezone.utc)
    items = [
        _build_item(
            item_id="web:gdrive", name="web:gdrive",
            category=SourceType.WEB, status=ItemStatus.TRIGGERED,
            current="124.0",
        ),
    ]
    _write(run_dir, _build_sidecar(
        phase=Phase.APPLY, category=SourceType.WEB, items=items, started=started,
    ))

    md = generate_apply_report(run_dir)

    assert md is not None
    assert "Trigger-only updates" in md
    assert "Gdrive" in md  # display name canonicalisation


def test_generate_apply_report_deferred_with_reason(run_dir: Path) -> None:
    """Phase A: a web app deferred because it was in use is promoted into
    the consolidated Action-required section (not buried in Deferred),
    still with a friendly 'was running' reason."""
    started = datetime(2026, 5, 8, 22, 47, tzinfo=timezone.utc)
    items = [
        _build_item(
            item_id="web:trezor-suite", name="web:trezor-suite",
            category=SourceType.WEB, status=ItemStatus.SKIPPED,
            current="26.4.2",
            messages=[Message(level=MessageLevel.INFO, text="deferred_app_in_use")],
        ),
    ]
    _write(run_dir, _build_sidecar(
        phase=Phase.APPLY, category=SourceType.WEB, items=items, started=started,
    ))

    md = generate_apply_report(run_dir)

    assert md is not None
    assert "## ⚠ Action required" in md
    assert "Trezor Suite" in md
    # Friendly 'was running' reason still surfaces (now in Action required).
    assert "running" in md.lower()
    # And it is NOT also buried in a generic Deferred section.
    if "## Deferred" in md:
        assert "Trezor Suite" not in md[md.index("## Deferred"):]


def test_generate_apply_report_swallows_exceptions(tmp_path: Path) -> None:
    """A non-existent run dir returns None without raising."""
    md = generate_apply_report(tmp_path / "does-not-exist")
    assert md is None


# ── Tests: dashboard endpoint ───────────────────────────────────────────────


def _make_test_client(runs_dir: Path) -> TestClient:
    """Build a dashboard TestClient with no adapter — only /runs/{id}/report needed."""
    from ascendo.dashboard import create_app

    app = create_app(runs_dir=runs_dir)
    app.state.adapter = None
    return TestClient(app)


def test_runs_report_endpoint_returns_markdown(tmp_path: Path) -> None:
    run_dir = tmp_path / str(_RUN_ID)
    run_dir.mkdir()
    started = datetime(2026, 5, 8, 22, 47, tzinfo=timezone.utc)
    items = [
        _build_item(
            item_id="glib", name="glib",
            category=SourceType.BREW, status=ItemStatus.SUCCESS,
            current="2.88.0", target="2.88.1", resolved="2.88.1",
        ),
    ]
    _write(run_dir, _build_sidecar(
        phase=Phase.APPLY, category=SourceType.BREW, items=items, started=started,
    ))

    client = _make_test_client(tmp_path)
    r = client.get(f"/runs/{_RUN_ID}/report")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/markdown")
    assert "# Ascendo update" in r.text
    assert "glib" in r.text


def test_runs_report_endpoint_404_for_check_only_run(tmp_path: Path) -> None:
    run_dir = tmp_path / str(_RUN_ID)
    run_dir.mkdir()
    started = datetime(2026, 5, 8, 22, 47, tzinfo=timezone.utc)
    _write(run_dir, _build_sidecar(
        phase=Phase.CHECK, category=SourceType.BREW,
        items=[_build_item(item_id="glib", name="glib",
                           category=SourceType.BREW, status=ItemStatus.UP_TO_DATE,
                           current="1.0", target="1.0")],
        started=started,
    ))
    client = _make_test_client(tmp_path)
    r = client.get(f"/runs/{_RUN_ID}/report")
    assert r.status_code == 404


def test_runs_report_endpoint_404_for_unknown_run(tmp_path: Path) -> None:
    client = _make_test_client(tmp_path)
    r = client.get(f"/runs/{uuid4()}/report")
    assert r.status_code == 404


# ── Updated outside Ascendo (Task 17, Spec 9 sidecar-only) ───────────────────


def _outside_run(run_dir: Path, *, check_id, apply_id, apply_status, apply_cur="2.0", apply_tgt="2.0"):
    started = datetime(2026, 5, 8, 22, 47, tzinfo=timezone.utc)
    _write(run_dir, _build_sidecar(
        phase=Phase.CHECK, category=SourceType.WEB, started=started,
        items=[_build_item(item_id=check_id, category=SourceType.WEB,
                           status=ItemStatus.PLANNED, current="1.0", target="2.0")]))
    _write(run_dir, _build_sidecar(
        phase=Phase.APPLY, category=SourceType.WEB, started=started,
        items=[_build_item(item_id=apply_id, category=SourceType.WEB, status=apply_status,
                           current=apply_cur, target=apply_tgt)]))
    return generate_apply_report(run_dir)


def test_report_lists_app_updated_outside_ascendo(run_dir: Path) -> None:
    md = _outside_run(run_dir, check_id="web:slack", apply_id="web:slack",
                      apply_status=ItemStatus.UP_TO_DATE)
    assert "## Updated outside Ascendo (1)" in md
    assert "Slack" in md and "1.0 → 2.0" in md


def test_report_outside_section_absent_when_ascendo_upgraded_it(run_dir: Path) -> None:
    md = _outside_run(run_dir, check_id="web:slack", apply_id="web:slack",
                      apply_status=ItemStatus.SUCCESS)
    assert "Updated outside Ascendo" not in md


def test_report_outside_section_alias_of_our_upgrade_is_not_outside(run_dir: Path) -> None:
    # check offered "web:google-chrome"; Ascendo upgraded the alias "web:chrome".
    md = _outside_run(run_dir, check_id="web:google-chrome", apply_id="web:chrome",
                      apply_status=ItemStatus.SUCCESS)
    assert "Updated outside Ascendo" not in md


def test_report_outside_section_matches_aliases_on_up_to_date_side(run_dir: Path) -> None:
    md = _outside_run(run_dir, check_id="web:google-chrome", apply_id="web:chrome",
                      apply_status=ItemStatus.UP_TO_DATE)
    assert "## Updated outside Ascendo (1)" in md


# ── Check-phase allowlisted tokens (Task 17 fix, Ruling 49) ─────────────────


def _rosetta_check(run_dir: Path, token_reason: str = "rosetta_missing"):
    started = datetime(2026, 5, 8, 22, 47, tzinfo=timezone.utc)
    _write(run_dir, _build_sidecar(
        phase=Phase.CHECK, category=SourceType.WEB, started=started,
        items=[_build_item(item_id="web:oldapp", category=SourceType.WEB,
                           status=ItemStatus.UP_TO_DATE, current="1.0", target="1.0")],
        messages=[Message(level=MessageLevel.WARN, text=(
            "oldapp: Rosetta is not installed - this Intel-only app will not launch | "
            f"ASCENDO-ACTION-REQUIRED reason={token_reason} cur=1.0 cand=?"))]))
    _write(run_dir, _build_sidecar(
        phase=Phase.APPLY, category=SourceType.WEB, started=started,
        items=[_build_item(item_id="web:other", category=SourceType.WEB,
                           status=ItemStatus.UP_TO_DATE, current="2.0", target="2.0")]))


def test_check_rosetta_missing_reaches_collector_and_report(run_dir: Path) -> None:
    from ascendo.orchestrator.report import collect_action_required
    _rosetta_check(run_dir)
    acts = collect_action_required(run_dir)
    assert [(a.category, a.slug, a.reason) for a in acts] == [("web", "oldapp", "rosetta_missing")]
    md = generate_apply_report(run_dir)
    assert "Action required" in md and "Rosetta" in md


def test_check_tokens_outside_allowlist_are_ignored(run_dir: Path) -> None:
    from ascendo.orchestrator.report import collect_action_required
    _rosetta_check(run_dir, token_reason="self_update")
    assert collect_action_required(run_dir) == []
