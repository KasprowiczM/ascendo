"""Contract tests for the cross-source deduplication consent surface.

These endpoints make duplicate-uninstall consent an *explicit click*:
``GET /dedup/pending`` reports the recommended fixes for the latest run and
``POST /dedup/apply`` writes the (server-validated) ``DEDUPLICATION_TASKS.json``
and triggers the apply. The destructive artifact is never written implicitly.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from ascendo.dashboard import create_app
from ascendo.models.result import Summary
from ascendo.models.run import Phase, PhaseStatus, RunInfo, Trigger
from ascendo.models.sidecar import Sidecar, SidecarSchema, ToolInfo
from ascendo.orchestrator.sidecar_io import write_sidecar

from .test_dashboard import FakeAdapter


_DEDUP_CONFIG = """
[[app]]
id = "claude"
name = "Claude"
preferred_order = ["npm", "brew"]
[app.sources]
npm = "@anthropic-ai/claude-cli"
brew = "claude"
"""


def _seed_dup_check_run(runs_dir: Path) -> str:
    """Write a CHECK run with claude installed via BOTH npm and brew."""
    run_id = uuid4()
    host = {
        "hostname": "mac", "os": "macos", "os_version": "15.0", "arch": "arm64",
        "user": "t", "is_elevated": False, "elevation_method": "none", "locale": "en-US",
    }
    run = RunInfo(id=run_id, trigger=Trigger.CLI, profile="full", dry_run=False,
                  started_at=datetime.now(timezone.utc))
    now = datetime.now(timezone.utc).isoformat()

    def _sc(category: str, tool: str, item_id: str, name: str) -> Sidecar:
        return Sidecar.model_validate({
            "schema": SidecarSchema.V1_ASCENDO.value,
            "run": run.model_dump(mode="json"),
            "host": host,
            "tool": ToolInfo(name=tool, version="1.0").model_dump(mode="json"),
            "phase": "check",
            "category": category,
            "started_at": now, "finished_at": now,
            "status": PhaseStatus.SUCCESS.value,
            "items": [{
                "id": item_id, "name": name, "category": category,
                "source": {"type": category, "feed": None, "url": None},
                "current_version": "1.0.0", "target_version": "1.0.0",
                "resolved_version": "1.0.0", "status": "up_to_date",
                "exit_code": None, "duration_ms": None, "evidence": None,
                "rollback": None, "messages": [],
            }],
            "summary": Summary(total=1, up_to_date=1, exit_code=0).model_dump(),
        })

    write_sidecar(_sc("npm", "npm", "@anthropic-ai/claude-cli", "claude-cli"), base_dir=runs_dir)
    write_sidecar(_sc("brew", "brew", "claude", "Claude"), base_dir=runs_dir)
    return str(run_id)


@pytest.fixture
def client(tmp_path: Path, monkeypatch) -> TestClient:
    # Isolate config resolution into the temp profile (all per-OS filenames so
    # the test is host-OS-independent).
    monkeypatch.setenv("ASCENDO_HOME", str(tmp_path))
    for fname in ("macos_app_sources.toml", "ubuntu_app_sources.toml", "windows_app_sources.toml"):
        (tmp_path / fname).write_text(_DEDUP_CONFIG)
    monkeypatch.delenv("ASCENDO_DEDUP_AUTO_UNINSTALL", raising=False)
    app = create_app(adapter=FakeAdapter(), runs_dir=tmp_path)
    return TestClient(app)


def test_dedup_pending_lists_duplicates(client: TestClient) -> None:
    runs_dir = Path(client.app.state.runs_dir)
    _seed_dup_check_run(runs_dir)

    resp = client.get("/dedup/pending")
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 1
    fix = body["fixes"][0]
    assert fix["app_id"] == "claude"
    assert fix["best_installed"] == "npm"
    by_src = {s["source"]: s for s in fix["installed"]}
    assert by_src["brew"]["recommended_uninstall"] is True
    assert by_src["npm"]["recommended_uninstall"] is False


def test_dedup_pending_empty_when_no_runs(client: TestClient) -> None:
    resp = client.get("/dedup/pending")
    assert resp.status_code == 200
    assert resp.json()["count"] == 0


def test_dedup_apply_writes_validated_tasks_and_triggers_run(client: TestClient) -> None:
    runs_dir = Path(client.app.state.runs_dir)
    _seed_dup_check_run(runs_dir)

    resp = client.post("/dedup/apply", json={})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    new_run_id = body["run_id"]
    # Server-validated tasks: only the non-best (brew) source is queued.
    assert body["uninstall_tasks"] == {"brew": ["claude"]}

    tasks_file = runs_dir / new_run_id / "DEDUPLICATION_TASKS.json"
    assert tasks_file.is_file()
    assert json.loads(tasks_file.read_text()) == {"brew": ["claude"]}

    # Explicit consent must drop a per-run approval marker beside the tasks
    # file. The Windows winget/npm/pip apply.ps1 executor will ONLY perform an
    # uninstall when this marker (or the ASCENDO_DEDUP_AUTO_UNINSTALL=1 opt-in)
    # is present — so a stray tasks file alone can never auto-uninstall.
    marker = runs_dir / new_run_id / "DEDUPLICATION_APPROVED"
    assert marker.is_file()


def test_dedup_apply_400_when_no_pending(client: TestClient) -> None:
    # No check run at all → nothing to apply.
    resp = client.post("/dedup/apply", json={})
    assert resp.status_code in (400, 409)


def test_dedup_apply_rejects_unknown_app_id(client: TestClient) -> None:
    runs_dir = Path(client.app.state.runs_dir)
    _seed_dup_check_run(runs_dir)
    # Client cannot smuggle an uninstall for an app that isn't a real duplicate.
    resp = client.post("/dedup/apply", json={"app_ids": ["totally-bogus"]})
    assert resp.status_code == 400


def test_dedup_js_card_is_served(client: TestClient) -> None:
    """The self-contained consent card module is served by the dashboard."""
    resp = client.get("/dedup.js")
    assert resp.status_code == 200
    assert "application/javascript" in resp.headers.get("content-type", "")
    assert "ascendoDedup" in resp.text
    assert "/dedup/apply" in resp.text
