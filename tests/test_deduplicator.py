import json
from pathlib import Path
from uuid import uuid4

from ascendo.models.sidecar import Sidecar
from ascendo.models.result import ItemStatus
from ascendo.orchestrator.deduplicator import apply_deduplication

def test_deduplicator_ignores_non_preferred(tmp_path: Path):
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


def test_deduplicator_macos_brew_npm(tmp_path: Path):
    """Validates macOS deduplication between brew and npm sources."""
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

