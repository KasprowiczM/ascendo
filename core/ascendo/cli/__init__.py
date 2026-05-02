"""Typer-based CLI: `ascendo <command>`."""
from __future__ import annotations

import logging
import os
import subprocess
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
runs_app = typer.Typer(
    name="runs",
    help="Inspect previous run sidecars on disk.",
    no_args_is_help=True,
)
app.add_typer(runs_app, name="runs")
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
    background: bool = typer.Option(
        False,
        "--background",
        "-b",
        help="Spawn uvicorn in a detached child process and return immediately.",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Launch the FastAPI dashboard backend on 127.0.0.1 (loopback only by default).

    With ``--background`` the dashboard is spawned as a detached child
    process and the CLI returns immediately. Stdout/stderr are silenced
    so the parent terminal does not stay tethered to the server. Use
    this when scripting (e.g. ``ascendo dashboard --background &&
    open http://127.0.0.1:8765/``).
    """
    _setup_logging(verbose)

    if background:
        # Re-invoke ourselves in the foreground mode of this same command,
        # detached from the parent process group / session so the terminal
        # is free to exit. Cross-platform via stdlib only.
        argv = [
            sys.executable, "-m", "ascendo", "dashboard",
            "--host", host, "--port", str(port),
        ]
        if runs_dir is not None:
            argv += ["--runs-dir", str(runs_dir)]
        if verbose:
            argv += ["--verbose"]
        popen_kwargs: dict[str, object] = {
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "stdin":  subprocess.DEVNULL,
        }
        if sys.platform == "win32":
            # CREATE_NEW_PROCESS_GROUP keeps the child alive past the
            # parent's exit; DETACHED_PROCESS detaches from the console.
            popen_kwargs["creationflags"] = (
                subprocess.CREATE_NEW_PROCESS_GROUP
                | getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
            )
        else:
            popen_kwargs["start_new_session"] = True
        proc = subprocess.Popen(argv, **popen_kwargs)
        typer.secho(
            f"ascendo dashboard started in background (pid={proc.pid}) on http://{host}:{port}/",
            fg=typer.colors.GREEN,
        )
        return

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


# ── runs subcommands: list + show ─────────────────────────────────────────
# These read sidecars directly from disk and print a Typer-styled table,
# mirroring what the dashboard's GET /runs and GET /runs/{id} surface.
def _iter_run_dirs(base_dir: Path):
    """Yield every run-id directory under ``base_dir`` sorted newest-first.

    Run-id directories are anything containing at least one ``.json``
    sidecar, regardless of name (UUID or timestamp). Newest-first means
    sorted by mtime descending.
    """
    if not base_dir.is_dir():
        return
    candidates = []
    for child in base_dir.iterdir():
        if not child.is_dir():
            continue
        if not any(p.suffix == ".json" for p in child.iterdir() if p.is_file()):
            continue
        candidates.append(child)
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    yield from candidates


def _run_overall_status(sidecars) -> PhaseStatus:
    """Aggregate per-sidecar phase status into a single overall status.

    Same precedence as :class:`RunReport.overall_status`: any FAILED
    wins; else any PARTIAL; else any SUCCESS; else SKIPPED.
    """
    statuses = [sc.status for sc in sidecars if hasattr(sc, "status")]
    if not statuses:
        return PhaseStatus.SKIPPED
    if PhaseStatus.FAILED in statuses:
        return PhaseStatus.FAILED
    if PhaseStatus.PARTIAL in statuses:
        return PhaseStatus.PARTIAL
    if PhaseStatus.SUCCESS in statuses:
        return PhaseStatus.SUCCESS
    return PhaseStatus.SKIPPED


def _status_color(status: PhaseStatus) -> str:
    return {
        PhaseStatus.SUCCESS: typer.colors.GREEN,
        PhaseStatus.SKIPPED: typer.colors.BLUE,
        PhaseStatus.PARTIAL: typer.colors.YELLOW,
        PhaseStatus.FAILED:  typer.colors.RED,
    }.get(status, typer.colors.WHITE)


@runs_app.command("list")
def runs_list(
    runs_dir: Path | None = typer.Option(None, "--runs-dir"),
    limit: int = typer.Option(20, "--limit", "-n", help="How many runs to show (newest first)."),
    status: str | None = typer.Option(
        None, "--status", "-s",
        help="Filter by overall status: success | partial | failed | skipped.",
    ),
) -> None:
    """List runs on disk, newest first."""
    from ..orchestrator.sidecar_io import read_run

    base_dir = runs_dir or _default_runs_dir()
    if not base_dir.is_dir():
        typer.secho(f"no runs (dir does not exist): {base_dir}", fg=typer.colors.YELLOW)
        raise typer.Exit(0)

    want_status: PhaseStatus | None = None
    if status:
        try:
            want_status = PhaseStatus(status.lower())
        except ValueError:
            valid = ", ".join(p.value for p in PhaseStatus)
            typer.secho(f"error: unknown status '{status}'. valid: {valid}", fg=typer.colors.RED, err=True)
            raise typer.Exit(2) from None

    typer.secho(
        f"{'RUN-ID':<40s} {'STARTED':<20s} {'STATUS':<10s} {'PHASES':>6s} {'ITEMS':>6s}",
        bold=True,
    )
    shown = 0
    for run_dir in _iter_run_dirs(base_dir):
        if shown >= limit:
            break
        sidecars = [sc for sc in read_run(run_dir) if hasattr(sc, "status")]
        if not sidecars:
            continue
        overall = _run_overall_status(sidecars)
        if want_status is not None and overall != want_status:
            continue
        started = min((sc.started_at for sc in sidecars if sc.started_at), default=None)
        started_str = started.strftime("%Y-%m-%d %H:%M:%S") if started else "—"
        items = sum(sc.summary.total for sc in sidecars)
        typer.secho(
            f"{run_dir.name:<40s} {started_str:<20s} ",
            nl=False,
        )
        typer.secho(f"{overall.value:<10s} ", fg=_status_color(overall), nl=False)
        typer.echo(f"{len(sidecars):>6d} {items:>6d}")
        shown += 1

    if shown == 0:
        typer.secho("no matching runs.", fg=typer.colors.YELLOW)


@runs_app.command("show")
def runs_show(
    run_id: str = typer.Argument(..., help="Run id (the directory name under runs_dir)."),
    runs_dir: Path | None = typer.Option(None, "--runs-dir"),
) -> None:
    """Show every phase + per-category status for one run."""
    from ..orchestrator.sidecar_io import read_run

    base_dir = runs_dir or _default_runs_dir()
    run_dir = base_dir / run_id
    if not run_dir.is_dir():
        typer.secho(f"error: run not found: {run_dir}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)

    parsed = read_run(run_dir)
    sidecars = [sc for sc in parsed if hasattr(sc, "status")]
    if not sidecars:
        typer.secho("error: no readable sidecars in this run.", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)

    overall = _run_overall_status(sidecars)
    started = min((sc.started_at for sc in sidecars if sc.started_at), default=None)
    finished = max((sc.finished_at for sc in sidecars if sc.finished_at), default=None)
    duration = ((finished - started).total_seconds() if started and finished else None)
    total_items = sum(sc.summary.total for sc in sidecars)
    failed_items = sum(sc.summary.failed for sc in sidecars)

    typer.secho(f"run-id:    {run_id}", bold=True)
    typer.echo(f"started:   {started.isoformat() if started else '—'}")
    typer.echo(f"finished:  {finished.isoformat() if finished else '—'}")
    typer.echo(f"duration:  {duration:.1f}s" if duration else "duration:  —")
    typer.secho(f"overall:   {overall.value}", fg=_status_color(overall), bold=True)
    typer.echo(f"sidecars:  {len(sidecars)}  items: {total_items}  failed: {failed_items}")
    typer.echo("")
    typer.secho(
        f"  {'PHASE':<8s} {'CATEGORY':<14s} {'STATUS':<10s} {'ITEMS':>6s} {'FAILED':>7s}",
        bold=True,
    )
    for sc in sidecars:
        typer.secho(
            f"  {sc.phase.value:<8s} {sc.category.value:<14s} ",
            nl=False,
        )
        typer.secho(f"{sc.status.value:<10s} ", fg=_status_color(sc.status), nl=False)
        typer.echo(f"{sc.summary.total:>6d} {sc.summary.failed:>7d}")

    raise typer.Exit({
        PhaseStatus.SUCCESS: 0, PhaseStatus.SKIPPED: 0,
        PhaseStatus.PARTIAL: 1, PhaseStatus.FAILED: 2,
    }[overall])


def main() -> None:
    app()


if __name__ == "__main__":
    main()


__all__ = ["app", "main"]
