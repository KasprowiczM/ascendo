import json
from pathlib import Path
from uuid import uuid4

from ascendo.models.sidecar import Sidecar
from ascendo.models.result import ItemStatus
from ascendo.orchestrator.deduplicator import apply_deduplication, compute_dedup_fixes


def _win_dup_sidecars(run_id):
    """Two CHECK sidecars (winget + npm) that both install logical 'Claude',
    where 'web' is the absolute preferred but only npm+winget are installed."""
    base = {
        "schema": "ascendo/v1",
        "run": {"id": str(run_id), "trigger": "cli", "profile": "full", "dry_run": False, "started_at": "2026-05-29T00:00:00Z", "finished_at": None, "invocation": None},
        "host": {"hostname": "mock", "os": "windows", "os_version": "10", "arch": "x64", "user": "mock", "is_elevated": False, "elevation_method": "none", "locale": "en-US"},
        "tool": {"name": "winget", "version": "1.0", "binary_path": None},
        "phase": "check",
        "started_at": "2026-05-29T00:00:00Z",
        "finished_at": "2026-05-29T00:00:00Z",
        "status": "success",
        "summary": {"total": 1, "success": 0, "up_to_date": 0, "failed": 0, "skipped": 0, "planned": 1, "partial": 0, "triggered": 0, "duration_ms": 10, "exit_code": 0},
        "needs_reboot": False,
        "messages": [],
    }
    winget = Sidecar.model_validate({
        **base, "category": "winget",
        "items": [{
            "id": "Anthropic.Claude", "name": "Claude", "category": "winget",
            "source": {"type": "winget", "feed": "winget", "url": None},
            "current_version": "1.0.0", "target_version": "1.1.0", "resolved_version": "1.1.0",
            "status": "planned",
            "exit_code": None, "duration_ms": None, "evidence": None, "rollback": None, "messages": []}]})
    npm = Sidecar.model_validate({
        **base, "tool": {"name": "npm", "version": "1.0", "binary_path": None}, "category": "npm",
        "items": [{
            "id": "@anthropic-ai/claude-cli", "name": "claude-cli", "category": "npm",
            "source": {"type": "npm", "feed": None, "url": None},
            "current_version": "1.0.0", "target_version": "1.2.0", "resolved_version": "1.2.0",
            "status": "planned",
            "exit_code": None, "duration_ms": None, "evidence": None, "rollback": None, "messages": []}]})
    return [winget, npm]


def test_compute_dedup_fixes_is_pure_and_serializable(tmp_path: Path):
    """compute_dedup_fixes returns JSON-safe structured duplicates and must NOT
    mutate the sidecars or write any file."""
    config_file = tmp_path / "win_app_sources.toml"
    config_file.write_text("""
[[app]]
id = "claude"
name = "Claude"
preferred_order = ["web", "npm", "winget"]

[app.sources]
winget = "Anthropic.Claude"
npm = "@anthropic-ai/claude-cli"
web = "claude"
    """)
    run_id = uuid4()
    sidecars = _win_dup_sidecars(run_id)

    fixes = compute_dedup_fixes(sidecars, config_file)

    # Pure: no mutation
    assert sidecars[0].items[0].status == ItemStatus.PLANNED
    assert sidecars[0].items[0].action != "uninstall"
    # No file written next to the config
    assert not (tmp_path / str(run_id)).exists()

    assert len(fixes) == 1
    fix = fixes[0]
    assert fix["app_id"] == "claude"
    assert fix["app_name"] == "Claude"
    assert fix["preferred"] == "web"          # absolute preferred (not installed)
    assert fix["best_installed"] == "npm"     # best installed source kept
    # JSON-serializable (no live objects leaked)
    json.dumps(fix)
    by_src = {s["source"]: s for s in fix["installed"]}
    assert by_src["npm"]["recommended_uninstall"] is False
    assert by_src["winget"]["recommended_uninstall"] is True
    assert by_src["winget"]["id"] == "Anthropic.Claude"


def test_compute_dedup_fixes_empty_when_no_duplicates(tmp_path: Path):
    config_file = tmp_path / "win_app_sources.toml"
    config_file.write_text("""
[[app]]
id = "claude"
name = "Claude"
preferred_order = ["npm", "winget"]

[app.sources]
npm = "@anthropic-ai/claude-cli"
winget = "Anthropic.Claude"
    """)
    run_id = uuid4()
    sidecars = _win_dup_sidecars(run_id)
    # Drop winget item so only the preferred npm is installed → no duplicate.
    sidecars = [sidecars[1]]
    assert compute_dedup_fixes(sidecars, config_file) == []

def test_deduplicator_ignores_non_preferred(tmp_path: Path, monkeypatch):
    # Auto-uninstall is fail-safe by default (only with a TTY or explicit
    # opt-in). Tests run non-interactively, so opt in to exercise the
    # destructive-queue path deterministically.
    monkeypatch.setenv("ASCENDO_DEDUP_AUTO_UNINSTALL", "1")
    # Setup mock config
    config_dir = tmp_path.parent / "adapters" / "windows" / "config"
    config_dir.mkdir(parents=True)
    
    config_file = config_dir / "app_sources.toml"
    config_file.write_text("""
[[app]]
id = "claude"
name = "Claude"
preferred_order = ["web", "npm", "winget"]

[app.sources]
winget = "Anthropic.Claude"
npm = "@anthropic-ai/claude-cli"
web = "claude"
    """)

    run_id = uuid4()
    run_dir = tmp_path / str(run_id)
    run_dir.mkdir(parents=True)

    # Mock sidecars
    base_sidecar = {
        "schema": "ascendo/v1",
        "run": {"id": str(run_id), "trigger": "cli", "profile": "full", "dry_run": False, "started_at": "2026-05-29T00:00:00Z", "finished_at": None, "invocation": None},
        "host": {"hostname": "mock", "os": "windows", "os_version": "10", "arch": "x64", "user": "mock", "is_elevated": False, "elevation_method": "none", "locale": "en-US"},
        "tool": {"name": "winget", "version": "1.0", "binary_path": None},
        "phase": "check",
        "started_at": "2026-05-29T00:00:00Z",
        "finished_at": "2026-05-29T00:00:00Z",
        "status": "success",
        "summary": {"total": 1, "success": 0, "up_to_date": 0, "failed": 0, "skipped": 0, "planned": 1, "partial": 0, "triggered": 0, "duration_ms": 10, "exit_code": 0},
        "needs_reboot": False,
        "messages": []
    }

    winget_sidecar = Sidecar.model_validate({
        **base_sidecar,
        "category": "winget",
        "items": [{
            "id": "Anthropic.Claude",
            "name": "Claude",
            "category": "winget",
            "source": {"type": "winget", "feed": "winget", "url": None},
            "current_version": "1.0.0",
            "target_version": "1.1.0",
            "resolved_version": "1.1.0",
            "status": "planned",
            "exit_code": None, "duration_ms": None, "evidence": None, "rollback": None, "messages": []
        }]
    })

    npm_sidecar = Sidecar.model_validate({
        **base_sidecar,
        "tool": {"name": "npm", "version": "1.0", "binary_path": None},
        "category": "npm",
        "items": [{
            "id": "@anthropic-ai/claude-cli",
            "name": "claude-cli",
            "category": "npm",
            "source": {"type": "npm", "feed": None, "url": None},
            "current_version": "1.0.0",
            "target_version": "1.2.0",
            "resolved_version": "1.2.0",
            "status": "planned",
            "exit_code": None, "duration_ms": None, "evidence": None, "rollback": None, "messages": []
        }]
    })

    sidecars = [winget_sidecar, npm_sidecar]
    
    # Run deduplication
    apply_deduplication(sidecars, run_id, tmp_path, config_file)

    assert sidecars[0].items[0].status == ItemStatus.PLANNED
    assert sidecars[0].items[0].action == "uninstall"

    assert sidecars[1].items[0].status == ItemStatus.PLANNED

    # Check report generation
    report = (run_dir / "DEDUPLICATION_REPORT.md").read_text()
    assert "Claude" in report
    assert "Recommended Source**: `web`" in report
    assert "winget uninstall --id Anthropic.Claude" in report


def test_deduplicator_macos_brew_npm(tmp_path: Path, monkeypatch):
    """Validates macOS deduplication between brew and npm sources."""
    # Opt in to the destructive-queue path (fail-safe off by default).
    monkeypatch.setenv("ASCENDO_DEDUP_AUTO_UNINSTALL", "1")
    config_file = tmp_path / "macos_app_sources.toml"
    config_file.write_text("""
[[app]]
id = "claude"
name = "Claude"
preferred_order = ["npm", "brew"]

[app.sources]
npm = "@anthropic-ai/claude-cli"
brew = "claude"
    """)

    run_id = uuid4()
    run_dir = tmp_path / str(run_id)
    run_dir.mkdir(parents=True)

    base_sidecar = {
        "schema": "ascendo/v1",
        "run": {"id": str(run_id), "trigger": "cli", "profile": "full", "dry_run": False, "started_at": "2026-05-29T00:00:00Z", "finished_at": None, "invocation": None},
        "host": {"hostname": "mock-mac", "os": "macos", "os_version": "15.0", "arch": "arm64", "user": "mock", "is_elevated": False, "elevation_method": "none", "locale": "en-US"},
        "tool": {"name": "brew", "version": "5.0", "binary_path": None},
        "phase": "check",
        "started_at": "2026-05-29T00:00:00Z",
        "finished_at": "2026-05-29T00:00:00Z",
        "status": "success",
        "summary": {"total": 1, "success": 0, "up_to_date": 0, "failed": 0, "skipped": 0, "planned": 1, "partial": 0, "triggered": 0, "duration_ms": 10, "exit_code": 0},
        "needs_reboot": False,
        "messages": []
    }

    brew_sidecar = Sidecar.model_validate({
        **base_sidecar,
        "category": "brew",
        "items": [{
            "id": "claude",
            "name": "Claude",
            "category": "brew",
            "source": {"type": "brew", "feed": None, "url": None},
            "current_version": "1.0.0",
            "target_version": "1.1.0",
            "resolved_version": "1.1.0",
            "status": "planned",
            "exit_code": None, "duration_ms": None, "evidence": None, "rollback": None, "messages": []
        }]
    })

    npm_sidecar = Sidecar.model_validate({
        **base_sidecar,
        "tool": {"name": "npm", "version": "10.9", "binary_path": None},
        "category": "npm",
        "items": [{
            "id": "@anthropic-ai/claude-cli",
            "name": "claude-cli",
            "category": "npm",
            "source": {"type": "npm", "feed": None, "url": None},
            "current_version": "1.0.0",
            "target_version": "1.2.0",
            "resolved_version": "1.2.0",
            "status": "planned",
            "exit_code": None, "duration_ms": None, "evidence": None, "rollback": None, "messages": []
        }]
    })

    sidecars = [brew_sidecar, npm_sidecar]

    # Run deduplication with macOS config
    apply_deduplication(sidecars, run_id, tmp_path, config_file)

    # brew should be marked for uninstall (npm is preferred)
    assert sidecars[0].items[0].status == ItemStatus.PLANNED
    assert sidecars[0].items[0].action == "uninstall"

    # npm should remain as-is (preferred source)
    assert sidecars[1].items[0].status == ItemStatus.PLANNED

    # Check report generation
    report = (run_dir / "DEDUPLICATION_REPORT.md").read_text()
    assert "Claude" in report
    assert "Recommended Source**: `npm`" in report
    assert "brew uninstall claude" in report


def test_report_only_does_not_mutate_check_sidecar(tmp_path: Path, monkeypatch):
    """Fail-safe report-only mode (non-TTY, no opt-in) surfaces duplicates in
    DEDUPLICATION_REPORT.md but must NOT mutate the read-only CHECK sidecar or
    queue any destructive uninstall task."""
    monkeypatch.delenv("ASCENDO_DEDUP_AUTO_UNINSTALL", raising=False)
    monkeypatch.setattr("sys.stdout.isatty", lambda: False)

    config_file = tmp_path / "win_app_sources.toml"
    config_file.write_text("""
[[app]]
id = "claude"
name = "Claude"
preferred_order = ["web", "npm", "winget"]

[app.sources]
winget = "Anthropic.Claude"
npm = "@anthropic-ai/claude-cli"
web = "claude"
    """)

    run_id = uuid4()
    run_dir = tmp_path / str(run_id)
    run_dir.mkdir(parents=True)

    base_sidecar = {
        "schema": "ascendo/v1",
        "run": {"id": str(run_id), "trigger": "cli", "profile": "full", "dry_run": False, "started_at": "2026-05-29T00:00:00Z", "finished_at": None, "invocation": None},
        "host": {"hostname": "mock", "os": "windows", "os_version": "10", "arch": "x64", "user": "mock", "is_elevated": False, "elevation_method": "none", "locale": "en-US"},
        "tool": {"name": "winget", "version": "1.0", "binary_path": None},
        "phase": "check",
        "started_at": "2026-05-29T00:00:00Z",
        "finished_at": "2026-05-29T00:00:00Z",
        "status": "success",
        "summary": {"total": 1, "success": 0, "up_to_date": 0, "failed": 0, "skipped": 0, "planned": 1, "partial": 0, "triggered": 0, "duration_ms": 10, "exit_code": 0},
        "needs_reboot": False,
        "messages": []
    }

    winget_sidecar = Sidecar.model_validate({
        **base_sidecar,
        "category": "winget",
        "items": [{
            "id": "Anthropic.Claude", "name": "Claude", "category": "winget",
            "source": {"type": "winget", "feed": "winget", "url": None},
            "current_version": "1.0.0", "target_version": "1.1.0", "resolved_version": "1.1.0",
            "status": "planned",
            "exit_code": None, "duration_ms": None, "evidence": None, "rollback": None, "messages": []
        }]
    })
    npm_sidecar = Sidecar.model_validate({
        **base_sidecar,
        "tool": {"name": "npm", "version": "1.0", "binary_path": None},
        "category": "npm",
        "items": [{
            "id": "@anthropic-ai/claude-cli", "name": "claude-cli", "category": "npm",
            "source": {"type": "npm", "feed": None, "url": None},
            "current_version": "1.0.0", "target_version": "1.2.0", "resolved_version": "1.2.0",
            "status": "planned",
            "exit_code": None, "duration_ms": None, "evidence": None, "rollback": None, "messages": []
        }]
    })

    sidecars = [winget_sidecar, npm_sidecar]
    apply_deduplication(sidecars, run_id, tmp_path, config_file)

    # Report written, but no destructive tasks queued.
    assert (run_dir / "DEDUPLICATION_REPORT.md").exists()
    assert not (run_dir / "DEDUPLICATION_TASKS.json").exists()

    # The non-preferred (winget) CHECK item is left exactly as check found it:
    # NOT flipped to a planned uninstall, status/target untouched.
    winget_item = sidecars[0].items[0]
    assert winget_item.status == ItemStatus.PLANNED
    assert winget_item.action != "uninstall"
    assert winget_item.target_version == "1.1.0"

