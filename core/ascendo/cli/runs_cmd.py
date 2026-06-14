"""``ascendo runs`` subcommands: list, show, json, report."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import typer

from ..models.run import PhaseStatus
from ._app import (
    _default_runs_dir,
    _sidecars_need_reboot,
    runs_app,
)


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


@runs_app.command("json")
def runs_json(
    run_id: str = typer.Argument(..., help="Run id (the directory name under runs_dir)."),
    runs_dir: Path | None = typer.Option(None, "--runs-dir"),
    pretty: bool = typer.Option(False, "--pretty", help="Pretty-print the JSON (indent=2)."),
) -> None:
    """Emit a consolidated run report as a single JSON blob (pipe to jq).

    Schema: ``ascendo/run/v1``. Carries the per-sidecar payloads plus a
    top-level rollup (overall_status, started/finished, duration_seconds,
    needs_reboot, summary). Useful for scripting on top of `ascendo runs`.
    """
    from ..orchestrator.sidecar_io import read_run

    base_dir = runs_dir or _default_runs_dir()
    run_dir = base_dir / run_id
    if not run_dir.is_dir():
        typer.secho(f"error: run not found: {run_dir}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)

    sidecars = [sc for sc in read_run(run_dir) if hasattr(sc, "status")]
    overall = _run_overall_status(sidecars)
    started = min((sc.started_at for sc in sidecars if sc.started_at), default=None)
    finished = max((sc.finished_at for sc in sidecars if sc.finished_at), default=None)
    duration = (finished - started).total_seconds() if started and finished else None

    payload = {
        "schema": "ascendo/run/v1",
        "run_id": run_id,
        "started_at":  started.isoformat()  if started  else None,
        "finished_at": finished.isoformat() if finished else None,
        "duration_seconds": duration,
        "overall_status": overall.value,
        "needs_reboot": _sidecars_need_reboot(sidecars),
        "sidecars": [sc.model_dump(mode="json", by_alias=True) for sc in sidecars],
        "summary": {
            "phases":        len(sidecars),
            "items_total":   sum(sc.summary.total for sc in sidecars),
            "items_failed":  sum(sc.summary.failed for sc in sidecars),
            "items_success": sum(sc.summary.success for sc in sidecars),
        },
    }

    import json as _json
    typer.echo(_json.dumps(payload, indent=2 if pretty else None, default=str))


@runs_app.command("report")
def runs_report(
    run_id: str = typer.Argument(..., help="Run id (the directory name under runs_dir)."),
    runs_dir: Path | None = typer.Option(None, "--runs-dir"),
    open_in_viewer: bool = typer.Option(
        False, "--open",
        help="Open the report in the default markdown viewer (macOS: `open`).",
    ),
    regenerate: bool = typer.Option(
        False, "--regenerate",
        help="Force re-render the report from sidecars even if REPORT.md already exists.",
    ),
) -> None:
    """Print the human-readable post-apply report for a run.

    The report is normally generated automatically at the end of any run
    that included an ``apply`` phase. This command prints the on-disk
    ``REPORT.md`` to stdout (or regenerates it on demand from sidecars).
    """
    from ..orchestrator.report import REPORT_FILENAME, generate_apply_report

    base_dir = runs_dir or _default_runs_dir()
    run_dir = base_dir / run_id
    if not run_dir.is_dir():
        typer.secho(f"error: run not found: {run_dir}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)

    report_path = run_dir / REPORT_FILENAME
    markdown: str | None = None
    if regenerate or not report_path.is_file():
        markdown = generate_apply_report(run_dir)
        if markdown is None:
            typer.secho(
                "no apply phase in this run — nothing to report.",
                fg=typer.colors.YELLOW, err=True,
            )
            raise typer.Exit(0)
    else:
        try:
            markdown = report_path.read_text(encoding="utf-8")
        except OSError as exc:
            typer.secho(f"error: could not read {report_path}: {exc}", fg=typer.colors.RED, err=True)
            raise typer.Exit(1) from None

    typer.echo(markdown)

    if open_in_viewer:
        opener: list[str] | None = None
        if sys.platform == "darwin":
            opener = ["open", str(report_path)]
        elif sys.platform.startswith("linux"):
            opener = ["xdg-open", str(report_path)]
        elif sys.platform == "win32":
            opener = ["cmd", "/c", "start", "", str(report_path)]
        if opener is not None:
            try:
                subprocess.run(opener, check=False)
            except OSError as exc:
                typer.secho(
                    f"warning: could not open viewer: {exc}",
                    fg=typer.colors.YELLOW, err=True,
                )


@runs_app.command("prune")
def runs_prune(
    keep_count: int | None = typer.Option(None, "--keep-count", "-n", help="Keep at most N runs (newest first by mtime)."),
    keep_days: int | None = typer.Option(None, "--keep-days", "-d", help="Keep runs from the last N days."),
    runs_dir: Path | None = typer.Option(None, "--runs-dir"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would be pruned without deleting."),
) -> None:
    """Remove old run directories according to a retention policy.

    If neither --keep-count nor --keep-days is specified, defaults to
    --keep-count 50. When both are given a run is kept if it satisfies
    *either* criterion (union).
    """
    from ..orchestrator.retention import prune_runs

    if keep_count is None and keep_days is None:
        keep_count = 50

    base_dir = runs_dir or _default_runs_dir()
    if not base_dir.is_dir():
        typer.secho(f"no runs directory: {base_dir}", fg=typer.colors.YELLOW)
        raise typer.Exit(0)

    pruned = prune_runs(
        base_dir,
        keep_count=keep_count,
        keep_days=keep_days,
        dry_run=dry_run,
    )

    if not pruned:
        typer.secho("nothing to prune.", fg=typer.colors.GREEN)
        raise typer.Exit(0)

    verb = "would prune" if dry_run else "pruned"
    for p in pruned:
        typer.echo(f"  {verb}: {p.name}")
    color = typer.colors.YELLOW if dry_run else typer.colors.GREEN
    typer.secho(f"{verb} {len(pruned)} run(s).", fg=color)

