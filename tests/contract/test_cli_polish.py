"""Contract tests for the CLI polish landed in feat(cli) — A1+A2+A3.

Covers:
  A1 — `ascendo snapshot` and `ascendo schedule` are real Typer subcommand
       groups (not exit-64 placeholders). Help text lists the expected
       subcommands.
  A2 — `ascendo run` exits 75 (not 0) when overall status is success but
       any sidecar reports a "Reboot required" phase-level message,
       with a yellow warning printed to stderr.
  A3 — `ascendo runs json <id>` emits a single JSON blob with the
       ``ascendo/run/v1`` schema, suitable for piping to ``jq``.

Path-resolution caveat:
    The repo is checked out as a git worktree under .claude/worktrees/<id>.
    The user's editable install of `ascendo` typically points at the
    *primary* checkout, NOT the worktree, so a naive ``import ascendo.cli``
    runs the unmodified primary copy. We force the worktree's ``core/``
    onto ``sys.path`` ahead of any cached module so the changes under
    test are the ones exercised. This is a no-op when pytest is run with
    PYTHONPATH already pointing at the worktree.
"""
from __future__ import annotations

import sys
from pathlib import Path

# ── Worktree path bootstrap ───────────────────────────────────────────────
# Resolve the worktree root from this file's location and insert its
# ``core/`` onto ``sys.path[0]`` so `import ascendo.*` resolves to the
# code we are about to test (instead of any pre-existing editable install
# of the primary checkout).
_WORKTREE_ROOT = Path(__file__).resolve().parents[2]
_WORKTREE_CORE = _WORKTREE_ROOT / "core"
if str(_WORKTREE_CORE) not in sys.path:
    sys.path.insert(0, str(_WORKTREE_CORE))

# Drop any already-imported `ascendo.*` modules so we re-import from the
# worktree path on the first reference below. Safe: nothing in the
# contract suite has an active reference to ascendo.cli at this point.
for _name in [k for k in list(sys.modules) if k == "ascendo" or k.startswith("ascendo.")]:
    if "ascendo.cli" in _name:
        del sys.modules[_name]

import json
from datetime import datetime, timezone
from typing import Any
from unittest.mock import patch
from uuid import UUID

import pytest
from typer.testing import CliRunner

from ascendo.cli import app
from ascendo.models.host import ElevationMethod, HostInfo, OperatingSystem
from ascendo.models.package import SourceType
from ascendo.models.result import Message, MessageLevel, Summary
from ascendo.models.run import Phase, PhaseStatus, RunInfo, Trigger
from ascendo.models.sidecar import Sidecar, SidecarSchema, ToolInfo
from ascendo.orchestrator import RunReport
from ascendo.orchestrator.sidecar_io import write_sidecar


runner = CliRunner()


# ── Helpers ───────────────────────────────────────────────────────────────


def _make_host() -> HostInfo:
    return HostInfo(
        hostname="testbox",
        os=OperatingSystem.WINDOWS,
        os_version="11 Pro 26200",
        arch="x86_64",
        user="tester",
        is_elevated=False,
        elevation_method=ElevationMethod.NONE,
    )


def _make_run() -> RunInfo:
    return RunInfo(
        id=UUID("12345678-1234-1234-1234-123456789abc"),
        trigger=Trigger.CLI,
        profile="full",
        dry_run=False,
        started_at=datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc),
    )


def _make_sidecar(
    *,
    phase: Phase = Phase.APPLY,
    category: SourceType = SourceType.WINGET,
    status: PhaseStatus = PhaseStatus.SUCCESS,
    messages: list[Message] | None = None,
) -> Sidecar:
    """Build a minimal-but-valid Sidecar with optional phase-level messages."""
    now = datetime(2026, 5, 1, 12, 0, 5, tzinfo=timezone.utc)
    payload: dict[str, Any] = {
        "schema": SidecarSchema.V1_ASCENDO.value,
        "run": _make_run().model_dump(mode="json"),
        "host": _make_host().model_dump(mode="json"),
        "tool": ToolInfo(name=category.value, version="1.0").model_dump(mode="json"),
        "phase": phase.value,
        "category": category.value,
        "started_at": now.isoformat(),
        "finished_at": now.isoformat(),
        "status": status.value,
        "items": [],
        "summary": Summary(total=0, exit_code=0).model_dump(),
    }
    if messages is not None:
        payload["messages"] = [m.model_dump() for m in messages]
    return Sidecar.model_validate(payload)


# ── A1 — snapshot/schedule subcommand groups ─────────────────────────────


def test_snapshot_help_lists_subcommands() -> None:
    """`ascendo snapshot --help` must succeed and list create/list/restore."""
    result = runner.invoke(app, ["snapshot", "--help"])
    assert result.exit_code == 0, result.stdout
    out = result.stdout
    assert "create" in out
    assert "list" in out
    assert "restore" in out


def test_schedule_help_lists_subcommands() -> None:
    """`ascendo schedule --help` must succeed and list install/remove/list/trigger."""
    result = runner.invoke(app, ["schedule", "--help"])
    assert result.exit_code == 0, result.stdout
    out = result.stdout
    assert "install" in out
    assert "remove" in out
    assert "list" in out
    assert "trigger" in out


# ── A3 — `runs json` subcommand ───────────────────────────────────────────


def test_runs_json_not_found_exits_1(tmp_path: Path) -> None:
    """A bogus run id must exit 1 with a stderr 'not found' message."""
    result = runner.invoke(
        app,
        ["runs", "json", "nonexistent-run-id", "--runs-dir", str(tmp_path)],
    )
    assert result.exit_code == 1
    # The error line is written to stderr (typer.secho ... err=True).
    assert "not found" in result.stderr.lower()


def test_runs_json_synthetic_emits_v1_schema(tmp_path: Path) -> None:
    """Write a synthetic sidecar to disk, ask `runs json <id>`, parse the
    stdout as JSON, and assert the rollup shape."""
    # 1. Build a sidecar that carries a single planned item so the summary
    #    has total=1. Re-use the on-disk fixture to skip the schema dance.
    sample = json.loads(
        (Path(__file__).parent.parent / "fixtures" / "sidecars"
         / "ascendo_v1_check_apt.json").read_text(encoding="utf-8")
    )
    sidecar = Sidecar.model_validate(sample)
    write_sidecar(sidecar, base_dir=tmp_path)
    run_id = str(sidecar.run.id)

    # 2. Invoke the CLI.
    result = runner.invoke(
        app,
        ["runs", "json", run_id, "--runs-dir", str(tmp_path), "--pretty"],
    )
    assert result.exit_code == 0, (result.stdout, result.stderr)

    # 3. The stdout must be valid JSON in our run schema.
    payload = json.loads(result.stdout)
    assert payload["schema"] == "ascendo/run/v1"
    assert payload["run_id"] == run_id
    assert payload["overall_status"] == PhaseStatus.SUCCESS.value
    assert payload["needs_reboot"] is False
    assert payload["summary"]["phases"] >= 1
    assert payload["summary"]["items_total"] >= 1
    assert isinstance(payload["sidecars"], list)
    assert payload["sidecars"][0]["schema"] in (
        SidecarSchema.V1_ASCENDO.value,
        SidecarSchema.V1_LEGACY_UBUNTU.value,
    )


# ── A2 — exit 75 + stderr line on needs_reboot ────────────────────────────


def test_run_exit_75_on_reboot(tmp_path: Path) -> None:
    """Patch the orchestrator so `run_phases` returns a fake report with a
    sidecar carrying a 'Reboot required' WARN message. The run command
    must exit 75 and print a stderr warning."""
    reboot_msg = Message(
        level=MessageLevel.WARN,
        text="Reboot required to complete one or more upgrades.",
    )
    sidecar = _make_sidecar(messages=[reboot_msg])
    fake_report = RunReport(
        run=_make_run(),
        host=_make_host(),
        sidecars=[sidecar],
        skipped_managers=[],
        aborted_after_phase=None,
    )

    # Build a minimal stub adapter object that satisfies the run command's
    # access pattern (it only reads `name` and `detect_host()`).
    class _StubAdapter:
        name = "stub"
        display_name = "Stub"
        tier = 1

        def detect_host(self) -> HostInfo:
            return _make_host()

    stub_adapter = _StubAdapter()

    # Patch the three CLI seams: AdapterRegistry / select_adapter / run_phases.
    # `select_adapter` returns our stub; `run_phases` returns the fake report.
    with patch("ascendo.cli.AdapterRegistry") as mock_registry, \
         patch("ascendo.cli.select_adapter", return_value=stub_adapter), \
         patch("ascendo.cli.run_phases", return_value=fake_report):
        mock_registry.return_value.discover.return_value = None

        result = runner.invoke(
            app,
            ["run", "--profile", "full", "--runs-dir", str(tmp_path)],
        )

    assert result.exit_code == 75, (result.exit_code, result.stdout, result.stderr)
    assert "reboot required" in result.stderr.lower()
