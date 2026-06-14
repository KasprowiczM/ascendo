"""``ascendo snapshot`` subcommands: create, list, restore."""
from __future__ import annotations

from datetime import UTC, datetime

import typer

from ..interfaces.snapshot import SnapshotError
from ._app import (
    _resolve_adapter_for_capability,
    _setup_logging,
    snapshot_app,
)


@snapshot_app.command("create")
def snapshot_create(
    description: str | None = typer.Option(
        None, "--description", "-d",
        help="Free-form description stored with the snapshot.",
    ),
    label: str | None = typer.Option(
        None, "--label",
        help="Short label. Default: 'ascendo <iso-timestamp>'.",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Create a new system snapshot. Prints the new snapshot id."""
    _setup_logging(verbose)
    adapter, host = _resolve_adapter_for_capability()
    snap_mgr = adapter.snapshot()
    if snap_mgr is None:
        typer.secho(
            f"error: snapshot not supported on this adapter ({adapter.name}).",
            fg=typer.colors.RED, err=True,
        )
        raise typer.Exit(10)
    if not snap_mgr.is_available(host):
        typer.secho(
            f"error: snapshot backend '{snap_mgr.backend}' is not operational on this host.",
            fg=typer.colors.RED, err=True,
        )
        raise typer.Exit(10)
    effective_label = label or f"ascendo {datetime.now(UTC).isoformat(timespec='seconds')}"
    try:
        info = snap_mgr.create(host, label=effective_label, notes=description)
    except SnapshotError as e:
        typer.secho(f"error: {e}", fg=typer.colors.RED, err=True)
        raise typer.Exit(20) from None
    typer.secho(f"snapshot created: {info.id}", fg=typer.colors.GREEN)
    typer.echo(f"  backend:    {info.backend}")
    typer.echo(f"  created_at: {info.created_at.isoformat()}")
    if info.label:
        typer.echo(f"  label:      {info.label}")
    if info.notes:
        typer.echo(f"  notes:      {info.notes}")


@snapshot_app.command("list")
def snapshot_list(
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """List system snapshots known to the adapter."""
    _setup_logging(verbose)
    adapter, host = _resolve_adapter_for_capability()
    snap_mgr = adapter.snapshot()
    if snap_mgr is None:
        typer.secho(
            f"error: snapshot not supported on this adapter ({adapter.name}).",
            fg=typer.colors.RED, err=True,
        )
        raise typer.Exit(10)
    try:
        items = snap_mgr.list(host)
    except SnapshotError as e:
        typer.secho(f"error: {e}", fg=typer.colors.RED, err=True)
        raise typer.Exit(20) from None
    if not items:
        typer.secho("no snapshots.", fg=typer.colors.YELLOW)
        return
    typer.secho(
        f"{'ID':<40s} {'CREATED':<25s} {'LABEL'}",
        bold=True,
    )
    for s in items:
        label = s.label or "—"
        typer.echo(f"{s.id:<40s} {s.created_at.isoformat():<25s} {label}")


@snapshot_app.command("restore")
def snapshot_restore(
    snapshot_id: str = typer.Argument(..., help="Snapshot id (from `ascendo snapshot list`)."),
    confirm: str = typer.Option(
        "", "--confirm",
        help="Must be the literal string 'RESTORE' to proceed.",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Restore a snapshot.

    The :class:`ISnapshot` interface intentionally does not expose a
    ``restore`` method — restore is a deliberate user-only operation
    that requires extra UI confirmation and lives in the dashboard.
    The CLI carries this subcommand as a clear placeholder so users
    know where to reach when interface coverage lands.
    """
    _setup_logging(verbose)
    if confirm != "RESTORE":
        typer.secho(
            "error: refusing to restore without --confirm RESTORE",
            fg=typer.colors.RED, err=True,
        )
        raise typer.Exit(2)
    adapter, _host = _resolve_adapter_for_capability()
    snap_mgr = adapter.snapshot()
    if snap_mgr is None:
        typer.secho(
            f"error: snapshot not supported on this adapter ({adapter.name}).",
            fg=typer.colors.RED, err=True,
        )
        raise typer.Exit(10)
    typer.secho(
        f"'ascendo snapshot restore {snapshot_id}' is not yet implemented at the "
        f"interface level (ISnapshot exposes create/list/get only). "
        f"Use the dashboard's snapshot tab for restore on this adapter "
        f"({adapter.name}, backend={snap_mgr.backend}).",
        fg=typer.colors.YELLOW, err=True,
    )
    raise typer.Exit(64)
