"""Ubuntu cross-source deduplication behaviour (P1 v1.0-beta).

Ubuntu ships a real ``adapters/ubuntu/config/ubuntu_app_sources.toml`` (claude /
docker / vscode / spotify) but has **no uninstall executor** — only the Windows
``apply.ps1`` scripts consume ``DEDUPLICATION_TASKS.json``. So on Ubuntu the
deduplicator is **report-only by design**:

* the fail-safe default (non-TTY, no opt-in) writes ``DEDUPLICATION_REPORT.md``
  only, never mutates the read-only CHECK sidecars, and never queues a
  destructive uninstall task;
* the queue path is reachable *only* behind the explicit
  ``ASCENDO_DEDUP_AUTO_UNINSTALL=1`` opt-in (mirroring the Windows env gate) —
  never an implicit default. Even when queued, no Ubuntu executor consumes the
  tasks file, so the queue is inert until a future apt/snap/flatpak uninstall
  step lands.

These tests drive the deduplicator with the **actual shipped Ubuntu config** so
a config drift (e.g. a new app, a reordered ``preferred_order``) is caught here.
"""
from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from ascendo.models.result import ItemStatus
from ascendo.models.sidecar import Sidecar
from ascendo.orchestrator.deduplicator import apply_deduplication, compute_dedup_fixes

_UBUNTU_CONFIG = (
    Path(__file__).resolve().parents[1] / "config" / "ubuntu_app_sources.toml"
)


def _check_sidecar(category: str, tool: str, item_id: str, run_id) -> Sidecar:
    """A single-item CHECK sidecar for ``category`` reporting ``item_id`` as
    installed (current_version set) and outdated (planned upgrade)."""
    return Sidecar.model_validate({
        "schema": "ascendo/v1",
        "run": {
            "id": str(run_id), "trigger": "cli", "profile": "full",
            "dry_run": False, "started_at": "2026-05-29T00:00:00Z",
            "finished_at": None, "invocation": None,
        },
        "host": {
            "hostname": "mk-uP5520", "os": "linux_ubuntu", "os_version": "24.04",
            "arch": "x64", "user": "mk", "is_elevated": False,
            "elevation_method": "none", "locale": "en-US",
        },
        "tool": {"name": tool, "version": "1.0", "binary_path": None},
        "category": category,
        "phase": "check",
        "started_at": "2026-05-29T00:00:00Z",
        "finished_at": "2026-05-29T00:00:00Z",
        "status": "success",
        "summary": {
            "total": 1, "success": 0, "up_to_date": 0, "failed": 0,
            "skipped": 0, "planned": 1, "partial": 0, "triggered": 0,
            "duration_ms": 10, "exit_code": 0,
        },
        "needs_reboot": False,
        "messages": [],
        "items": [{
            "id": item_id, "name": item_id, "category": category,
            "source": {"type": category, "feed": None, "url": None},
            "current_version": "1.0.0", "target_version": "1.1.0",
            "resolved_version": "1.1.0", "status": "planned",
            "exit_code": None, "duration_ms": None, "evidence": None,
            "rollback": None, "messages": [],
        }],
    })


def _docker_dup_sidecars(run_id):
    """Docker installed via BOTH apt (preferred) and snap (non-preferred),
    per the shipped ubuntu_app_sources.toml docker entry."""
    return [
        _check_sidecar("apt", "apt", "docker-ce", run_id),
        _check_sidecar("snap", "snap", "docker", run_id),
    ]


def test_ubuntu_config_ships_expected_apps():
    """Guard the shipped config so the dedup tests below stay meaningful."""
    from ascendo.models.deduplication import AppSourcesRegistry

    registry = AppSourcesRegistry.load(_UBUNTU_CONFIG)
    ids = {app.id for app in registry.apps}
    assert {"claude", "docker", "vscode", "spotify"} <= ids


def test_compute_dedup_fixes_uses_real_ubuntu_config():
    """Read-only consent surface (GET /dedup/pending) sees the snap copy as the
    recommended-uninstall and keeps the preferred apt copy. No mutation/IO."""
    run_id = uuid4()
    sidecars = _docker_dup_sidecars(run_id)

    fixes = compute_dedup_fixes(sidecars, _UBUNTU_CONFIG)

    assert len(fixes) == 1
    fix = fixes[0]
    assert fix["app_id"] == "docker"
    assert fix["preferred"] == "apt"
    by_src = {s["source"]: s for s in fix["installed"]}
    assert by_src["apt"]["recommended_uninstall"] is False
    assert by_src["snap"]["recommended_uninstall"] is True
    # Pure: read-only CHECK sidecars are untouched.
    assert sidecars[1].items[0].status == ItemStatus.PLANNED
    assert sidecars[1].items[0].action != "uninstall"


def test_ubuntu_dedup_is_report_only_by_default(tmp_path: Path, monkeypatch):
    """Fail-safe default (non-TTY, no opt-in): a Quick check that finds a
    cross-source duplicate writes the REPORT only — it does NOT mutate the
    read-only CHECK sidecars and does NOT queue DEDUPLICATION_TASKS.json. This
    is the v1 Ubuntu contract: report-only, no uninstall executor."""
    monkeypatch.delenv("ASCENDO_DEDUP_AUTO_UNINSTALL", raising=False)
    monkeypatch.setattr("sys.stdout.isatty", lambda: False)

    run_id = uuid4()
    run_dir = tmp_path / str(run_id)
    run_dir.mkdir(parents=True)
    sidecars = _docker_dup_sidecars(run_id)

    apply_deduplication(sidecars, run_id, tmp_path, _UBUNTU_CONFIG)

    # Report written; no destructive task queue (no Ubuntu executor exists).
    report = (run_dir / "DEDUPLICATION_REPORT.md").read_text()
    assert "Docker" in report
    assert "Recommended Source**: `apt`" in report
    assert "sudo snap remove docker" in report
    assert not (run_dir / "DEDUPLICATION_TASKS.json").exists()

    # The non-preferred (snap) CHECK item is left exactly as check found it.
    snap_item = sidecars[1].items[0]
    assert snap_item.status == ItemStatus.PLANNED
    assert snap_item.action != "uninstall"
    assert snap_item.target_version == "1.1.0"


def test_ubuntu_dedup_queues_only_under_explicit_optin(tmp_path: Path, monkeypatch):
    """The destructive queue path is reachable ONLY behind the explicit
    ASCENDO_DEDUP_AUTO_UNINSTALL=1 opt-in (mirroring the Windows env gate),
    never an implicit non-TTY default. Note: no Ubuntu executor consumes the
    tasks file yet, so the queue is inert — but the gate must still hold."""
    monkeypatch.setenv("ASCENDO_DEDUP_AUTO_UNINSTALL", "1")

    run_id = uuid4()
    run_dir = tmp_path / str(run_id)
    run_dir.mkdir(parents=True)
    sidecars = _docker_dup_sidecars(run_id)

    apply_deduplication(sidecars, run_id, tmp_path, _UBUNTU_CONFIG)

    # Under the opt-in, the non-preferred snap copy is queued for uninstall.
    tasks_path = run_dir / "DEDUPLICATION_TASKS.json"
    assert tasks_path.exists()
    import json
    tasks = json.loads(tasks_path.read_text())
    assert tasks.get("snap") == ["docker"]

    snap_item = sidecars[1].items[0]
    assert snap_item.status == ItemStatus.PLANNED
    assert snap_item.action == "uninstall"
    # The preferred apt copy is kept untouched.
    assert sidecars[0].items[0].action != "uninstall"
