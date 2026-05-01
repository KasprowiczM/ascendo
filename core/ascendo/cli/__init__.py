"""Typer-based CLI: `ascendo <command>`."""
from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import NoReturn
from uuid import uuid4

import typer

from .. import __version__
from ..adapter_factory import AdapterRegistry, NoAdapterAvailableError, select_adapter
from ..models.package import SourceType
from ..models.run import Phase, PhaseStatus, RunInfo, Trigger
from ..orchestrator import run_phases

app = typer.Typer(
    name="ascendo",
    help="Cross-platform update orchestrator.",
    no_args_is_help=True,
)
_log = logging.getLogger("ascendo")


def _default_runs_dir() -> Path:
    override = os.environ.get("ASCENDO_RUNS_DIR")
    return Path(override) if override else Path.home() / ".ascendo" / "runs"


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def _resolve_phases(arg: str | None) -> list[Phase]:
    if arg is None or arg.lower() == "all":
        return [Phase.CHECK, Phase.PLAN, Phase.APPLY, Phase.VERIFY, Phase.CLEANUP]
    out: list[Phase] = []
    for n in (s.strip().lower() for s in arg.split(",") if s.strip()):
        try:
            out.append(Phase(n))
        except ValueError:
            valid = ", ".join(p.value for p in Phase)
            typer.secho(f"error: unknown phase '{n}'. valid: {valid}", fg=typer.colors.RED, err=True)
            raise typer.Exit(2) from None
    return out


def _resolve_categories(arg: str | None) -> list[SourceType] | None:
    if arg is None:
        return None
    out: list[SourceType] = []
    for n in (s.strip().lower() for s in arg.split(",") if s.strip()):
        try:
            out.append(SourceType(n))
        except ValueError:
            valid = ", ".join(s.value for s in SourceType)
            typer.secho(f"error: unknown category '{n}'. valid: {valid}", fg=typer.colors.RED, err=True)
            raise typer.Exit(2) from None
    return out


@app.command()
def version() -> None:
    """Print Ascendo version."""
    typer.echo(f"ascendo {__version__}")


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
        started_at=datetime.now(timezone.utc), invocation=" ".join(sys.argv),
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
    raise typer.Exit({PhaseStatus.SUCCESS: 0, PhaseStatus.SKIPPED: 0, PhaseStatus.PARTIAL: 1, PhaseStatus.FAILED: 2}[overall])


@app.command()
def doctor(verbose: bool = typer.Option(False, "--verbose", "-v")) -> None:
    """Adapter self-test."""
    _setup_logging(verbose)
    registry = AdapterRegistry()
    registry.discover()
    try:
        adapter = select_adapter(registry=registry)
    except NoAdapterAvailableError as e:
        typer.secho(f"error: {e}", fg=typer.colors.RED, err=True)
        raise typer.Exit(3) from None
    typer.echo(f"adapter: {adapter.name} ({adapter.display_name}) tier={adapter.tier}")
    typer.echo(f"capabilities: {adapter.capabilities}")
    health = adapter.health_check()
    bad = 0
    for component, status in sorted(health.items()):
        ok = status.startswith("ok") or status.startswith("degraded")
        color = typer.colors.GREEN if ok else typer.colors.RED
        typer.secho(f"  {component:<20s} {status}", fg=color)
        if not ok:
            bad += 1
    raise typer.Exit(0 if bad == 0 else 1)


def _planned(name: str, milestone: str) -> NoReturn:
    typer.secho(f"'{name}' is not yet implemented (planned for {milestone}).", fg=typer.colors.YELLOW, err=True)
    raise typer.Exit(64)


@app.command()
def schedule() -> None:
    """[planned: M3.13] Manage periodic runs."""
    _planned("ascendo schedule", "M3.13 (Task Scheduler)")


@app.command()
def snapshot() -> None:
    """[planned: M3.12] Snapshots."""
    _planned("ascendo snapshot", "M3.12 (VSS / timeshift)")


@app.command()
def dashboard(
    host: str = typer.Option("127.0.0.1", "--host", help="Bind address. Default: 127.0.0.1 (loopback only)."),
    port: int = typer.Option(8765, "--port", help="TCP port."),
    runs_dir: Path | None = typer.Option(None, "--runs-dir"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Launch the FastAPI dashboard backend on 127.0.0.1 (loopback only by default)."""
    _setup_logging(verbose)
    try:
        import uvicorn
    except ImportError:
        typer.secho(
            "error: uvicorn not installed. Run: pip install 'ascendo[dashboard]' or pip install uvicorn",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(70) from None  # EX_SOFTWARE

    from ..dashboard import create_app

    base_dir = runs_dir or _default_runs_dir()
    app_instance = create_app(runs_dir=base_dir)
    typer.secho(
        f"ascendo dashboard listening on http://{host}:{port}/  (runs_dir={base_dir})",
        fg=typer.colors.GREEN,
    )
    uvicorn.run(app_instance, host=host, port=port, log_level="info" if verbose else "warning")


def main() -> None:
    app()


if __name__ == "__main__":
    main()


__all__ = ["app", "main"]
