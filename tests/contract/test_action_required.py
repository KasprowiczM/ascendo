"""Contract tests for the Phase-A 'Action required' guarantee.

Every non-silent web app (skipped / triggered / failed) must surface in
``collect_action_required`` and in the REPORT.md ``## ⚠ Action required``
section — promoted out of the generic Deferred bucket so nothing is ever
silently missing.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from ascendo.dashboard import create_app
from ascendo.models.host import ElevationMethod, HostInfo, OperatingSystem
from ascendo.models.package import ItemSource, SourceType
from ascendo.models.result import Item, ItemStatus, Message, MessageLevel, Summary
from ascendo.models.run import Phase, PhaseStatus, RunInfo, Trigger
from ascendo.models.sidecar import Sidecar, SidecarSchema, ToolInfo
from ascendo.orchestrator.report import (
    collect_action_required,
    generate_apply_report,
)
from ascendo.orchestrator.sidecar_io import write_sidecar

_RUN_ID = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")


def _host() -> HostInfo:
    return HostInfo(
        hostname="testbox", os=OperatingSystem.MACOS, os_version="14.5",
        arch="arm64", user="t", is_elevated=False,
        elevation_method=ElevationMethod.NONE,
    )


def _item(item_id: str, status: ItemStatus, cur=None, tgt=None) -> Item:
    return Item(
        id=item_id, name=item_id, category=SourceType.WEB,
        source=ItemSource(type=SourceType.WEB),
        current_version=cur, target_version=tgt, status=status,
    )


def _web_apply(run_dir: Path, items: list[Item], msgs: list[Message]) -> None:
    started = datetime(2026, 5, 16, 12, 0, tzinfo=timezone.utc)
    payload = {
        "schema": SidecarSchema.V1_ASCENDO.value,
        "run": RunInfo(
            id=_RUN_ID, trigger=Trigger.CLI, profile="full", dry_run=False,
            started_at=started,
        ).model_dump(mode="json"),
        "host": _host().model_dump(mode="json"),
        "tool": ToolInfo(name="web", version="1.0").model_dump(mode="json"),
        "phase": Phase.APPLY.value,
        "category": SourceType.WEB.value,
        "started_at": started.isoformat(),
        "finished_at": (started + timedelta(seconds=30)).isoformat(),
        "status": PhaseStatus.PARTIAL.value,
        "items": [it.model_dump(mode="json") for it in items],
        "summary": Summary(
            total=len(items),
            success=sum(1 for i in items if i.status is ItemStatus.SUCCESS),
            up_to_date=0,
            failed=sum(1 for i in items if i.status is ItemStatus.FAILED),
            skipped=sum(1 for i in items if i.status is ItemStatus.SKIPPED),
            triggered=sum(1 for i in items if i.status is ItemStatus.TRIGGERED),
            exit_code=1,
        ).model_dump(),
        "needs_reboot": False,
        "messages": [m.model_dump() for m in msgs],
    }
    write_sidecar(Sidecar.model_validate(payload), base_dir=run_dir.parent)


@pytest.fixture
def run_dir(tmp_path: Path) -> Path:
    rd = tmp_path / str(_RUN_ID)
    rd.mkdir(parents=True, exist_ok=True)
    return rd


def _msg(text: str) -> Message:
    return Message(level=MessageLevel.INFO, text=text)


def test_collect_action_required_parses_token_messages(run_dir: Path) -> None:
    _web_apply(
        run_dir,
        items=[
            _item("web:obsidian", ItemStatus.SUCCESS, "1.0", "1.1"),
            _item("web:keepassxc", ItemStatus.SUCCESS, "2.0", "2.1"),
            _item("web:claude", ItemStatus.SKIPPED, "1.0", "2.0"),
            _item("web:vscode", ItemStatus.SKIPPED, "1.1", "1.2"),
            _item("web:perplexity", ItemStatus.SKIPPED),
        ],
        msgs=[
            _msg("claude: skipped in safe mode — open the app | "
                 "ASCENDO-ACTION-REQUIRED reason=self_update cur=1.0 cand=2.0"),
            _msg("vscode: skipped in safe mode — open the app | "
                 "ASCENDO-ACTION-REQUIRED reason=no_silent_path cur=1.1 cand=1.2"),
            _msg("perplexity: skipped in safe mode | "
                 "ASCENDO-ACTION-REQUIRED reason=probe_broken cur=? cand=?"),
        ],
    )
    items = collect_action_required(run_dir)
    by_slug = {i.slug: i for i in items}
    assert set(by_slug) == {"claude", "vscode", "perplexity"}
    assert by_slug["claude"].reason == "self_update"
    assert by_slug["claude"].current == "1.0"
    assert by_slug["claude"].candidate == "2.0"
    assert by_slug["vscode"].reason == "no_silent_path"
    assert by_slug["perplexity"].reason == "probe_broken"
    for it in items:
        assert it.category == "web"
        assert it.reason_text  # human-readable, non-empty
        assert it.name


def test_report_renders_action_required_first(run_dir: Path) -> None:
    _web_apply(
        run_dir,
        items=[
            _item("web:obsidian", ItemStatus.SUCCESS, "1.0", "1.1"),
            _item("web:claude", ItemStatus.SKIPPED, "1.0", "2.0"),
        ],
        msgs=[
            _msg("claude: skipped | "
                 "ASCENDO-ACTION-REQUIRED reason=self_update cur=1.0 cand=2.0"),
        ],
    )
    md = generate_apply_report(run_dir)
    assert md is not None
    assert "## ⚠ Action required" in md
    # The Action-required section must appear before "## What changed".
    assert md.index("## ⚠ Action required") < md.index("## What changed")
    # The promoted app must NOT also be buried in a generic Deferred row.
    if "## Deferred" in md:
        deferred_block = md[md.index("## Deferred"):]
        assert "claude" not in deferred_block.lower()


def test_every_non_silent_web_app_is_surfaced(run_dir: Path) -> None:
    """The guarantee: nothing that isn't silently updated is missing."""
    non_silent = {
        "web:aa": ItemStatus.SKIPPED,
        "web:bb": ItemStatus.TRIGGERED,
        "web:cc": ItemStatus.FAILED,
        "web:dd": ItemStatus.SKIPPED,
    }
    items = [
        _item("web:silent1", ItemStatus.SUCCESS, "1", "2"),
        _item("web:silent2", ItemStatus.UP_TO_DATE, "9", "9"),
    ] + [_item(k, v, "1", "2") for k, v in non_silent.items()]
    msgs = [
        _msg(f"{k.split(':')[1]}: skipped | "
             f"ASCENDO-ACTION-REQUIRED reason=no_silent_path cur=1 cand=2")
        for k in non_silent
    ]
    _web_apply(run_dir, items=items, msgs=msgs)
    surfaced = {i.slug for i in collect_action_required(run_dir)}
    expected = {k.split(":")[1] for k in non_silent}
    assert expected.issubset(surfaced), (
        f"silently missing: {expected - surfaced}"
    )
    assert "silent1" not in surfaced and "silent2" not in surfaced


def test_action_required_endpoint(tmp_path: Path) -> None:
    rd = tmp_path / str(_RUN_ID)
    rd.mkdir(parents=True, exist_ok=True)
    # write_sidecar(base_dir=tmp_path) lands it under tmp_path/<run_id>/
    items = [
        _item("web:obsidian", ItemStatus.SUCCESS, "1", "2"),
        _item("web:claude", ItemStatus.SKIPPED, "1.0", "2.0"),
    ]
    # _web_apply writes via base_dir=run_dir.parent (== tmp_path == runs_dir)
    _web_apply(rd, items, [
        _msg("claude: skipped | "
             "ASCENDO-ACTION-REQUIRED reason=self_update cur=1.0 cand=2.0"),
    ])

    app = create_app(runs_dir=tmp_path)
    app.state.adapter = None
    with TestClient(app) as client:
        r = client.get(f"/runs/{_RUN_ID}/action-required")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["run_id"] == str(_RUN_ID)
        assert body["count"] == 1
        assert body["items"][0]["slug"] == "claude"
        assert body["items"][0]["reason"] == "self_update"
        assert body["items"][0]["reason_text"]
        # Unknown run -> 404.
        r404 = client.get("/runs/00000000-0000-0000-0000-000000000000/action-required")
        assert r404.status_code == 404
