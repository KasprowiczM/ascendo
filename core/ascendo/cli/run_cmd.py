"""``ascendo run`` command."""
from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import typer

from ..models.run import PhaseStatus, RunInfo, Trigger
from ._app import (
    _default_runs_dir,
    _resolve_categories,
    _resolve_phases,
    _setup_logging,
    _sidecars_need_reboot,
    app,
)


@app.command()
def run(
    category: str | None = typer.Option(None, "--category", "-c", help="Comma-separated source categories."),
    phase: str | None = typer.Option(None, "--phase", "-p", help="Comma-separated phases or 'all'."),
    profile: str = typer.Option("full", "--profile"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    runs_dir: Path | None = typer.Option(None, "--runs-dir"),
    item_filter: str | None = typer.Option(None, "--items"),
    no_stop_on_failure: bool = typer.Option(False, "--no-stop-on-failure"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Drive the detected OS adapter through the requested phases."""
    from . import AdapterRegistry, NoAdapterAvailableError, run_phases, select_adapter

    _setup_logging(verbose)
    base_dir = runs_dir or _default_runs_dir()
    base_dir.mkdir(parents=True, exist_ok=True)
    phases = _resolve_phases(phase)
    categories = _resolve_categories(category)
    items = [s.strip() for s in item_filter.split(",") if s.strip()] if item_filter else None

    registry = AdapterRegistry()
    registry.discover()
    try:
        adapter = select_adapter(registry=registry)
    except NoAdapterAvailableError as e:
        typer.secho(f"error: {e}", fg=typer.colors.RED, err=True)
        raise typer.Exit(3) from None

    host = adapter.detect_host()
    run_info = RunInfo(
        id=uuid4(), trigger=Trigger.CLI, profile=profile, dry_run=dry_run,
        started_at=datetime.now(UTC), invocation=" ".join(sys.argv),
    )
    typer.echo(f"ascendo run {run_info.id}  adapter={adapter.name}  host={host.hostname}  profile={profile}{' [dry-run]' if dry_run else ''}")

    report = run_phases(
        adapter, run_info, host,
        phases=phases, categories=categories, base_dir=base_dir,
        stop_on_failure=not no_stop_on_failure, item_filter=items,
    )

    for sc in report.sidecars:
        typer.echo(f"  {sc.phase.value:<8s} {sc.category.value:<14s} {sc.status.value:<10s} items={sc.summary.total} failed={sc.summary.failed} success={sc.summary.success}")
    for cat, reason in report.skipped_managers:
        typer.echo(f"  skipped  {cat:<14s} ({reason})")
    if report.aborted_after_phase is not None:
        typer.secho(f"  ! aborted after phase {report.aborted_after_phase.value}", fg=typer.colors.YELLOW)

    overall = report.overall_status
    color = {PhaseStatus.SUCCESS: typer.colors.GREEN, PhaseStatus.SKIPPED: typer.colors.BLUE,
             PhaseStatus.PARTIAL: typer.colors.YELLOW, PhaseStatus.FAILED: typer.colors.RED}[overall]
    typer.secho(f"overall: {overall.value} ({len(report.sidecars)} sidecars, {report.total_items} items)", fg=color, bold=True)

    # Reboot detection: Sidecar v1 dropped the legacy `needs_reboot` field
    # (see core/ascendo/models/legacy.py). The convention used by the
    # PowerShell apply scripts (winget, windows_update) is to emit a
    # phase-level WARN message whose text starts with "Reboot required".
    # We detect that signal and surface it as exit 75.
    needs_reboot = _sidecars_need_reboot(report.sidecars)
    if needs_reboot:
        typer.secho(
            "⚠ system reboot required to complete updates",
            fg=typer.colors.YELLOW,
            err=True,
        )

    # Exit-code precedence:
    #   FAILED  → 2  (always wins; reboot-pending failures still exit 2)
    #   PARTIAL → 1  (reboot-pending partials still exit 1)
    #   SUCCESS → 0, but bumped to 75 if any sidecar requested a reboot
    #   SKIPPED → 0
    exit_code = {
        PhaseStatus.SUCCESS: 0,
        PhaseStatus.SKIPPED: 0,
        PhaseStatus.PARTIAL: 1,
        PhaseStatus.FAILED:  2,
    }[overall]
    if needs_reboot and exit_code == 0:
        exit_code = 75
    raise typer.Exit(exit_code)
