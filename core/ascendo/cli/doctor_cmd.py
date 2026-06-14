"""``ascendo doctor`` and ``ascendo build-inventory`` commands."""
from __future__ import annotations

import logging
import os
from pathlib import Path

import typer

from ..adapter_factory import AdapterRegistry, NoAdapterAvailableError, select_adapter
from ._app import (
    _default_runs_dir,
    _setup_logging,
    app,
)

_log = logging.getLogger("ascendo")


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
    from ..orchestrator.sidecar_io import detect_stale_locks

    typer.echo(f"capabilities: {adapter.capabilities}")
    health = adapter.health_check()

    # P12: detect stale sidecar locks (mtime/PID)
    runs_dir = _default_runs_dir()
    stale_locks = []
    if runs_dir.is_dir():
        for run_sub_dir in runs_dir.iterdir():
            if run_sub_dir.is_dir():
                stale_locks.extend(detect_stale_locks(run_sub_dir))

    if stale_locks:
        health["Orchestrator"] = f"degraded (found {len(stale_locks)} stale sidecar .lock files)"

    bad = 0
    for component, status in sorted(health.items()):
        ok = status.startswith("ok") or status.startswith("degraded")
        color = typer.colors.GREEN if ok else typer.colors.RED
        typer.secho(f"  {component:<20s} {status}", fg=color)
        if not ok:
            bad += 1

    if stale_locks:
        typer.secho(
            "\nWarning: Stale sidecar locks detected. These may prevent future sidecar writes.",
            fg=typer.colors.YELLOW
        )
        for lock in stale_locks:
            typer.echo(f"  rm {lock}")

    raise typer.Exit(0 if bad == 0 else 1)


@app.command("build-inventory")
def build_inventory(
    verbose: bool = typer.Option(False, "--verbose", "-v"),
    no_db: bool = typer.Option(
        False,
        "--no-db",
        help="Skip the inventory.db flush; only print the in-memory bucket summary.",
    ),
) -> None:
    """Scan installed packages and (re)build the inventory cache + DB.

    Same effect as clicking "Build inventory" in the dashboard Overview:
    runs the adapter's IInventory enumerator, classifies each entry,
    overlays the latest check sidecars, and persists into the SQLite
    inventory DB at ``~/.ascendo/inventory.db`` so the SPA picks it up
    immediately. Idempotent — safe to re-run.
    """
    _setup_logging(verbose)
    registry = AdapterRegistry()
    registry.discover()
    try:
        adapter = select_adapter(registry=registry)
    except NoAdapterAvailableError as e:
        typer.secho(f"error: {e}", fg=typer.colors.RED, err=True)
        raise typer.Exit(3) from None

    # Reuse the dashboard's live-scan helpers — single source of truth.
    # Import late so non-dashboard CLI commands aren't blocked when
    # FastAPI / pydantic-settings aren't installed (e.g. CLI-only profile).
    try:
        from ..dashboard.routes.spa_real import (
            InventoryCache,
            _build_buckets_live,
            _flatten_buckets_for_db,
            _replace_buckets_in_db,
        )
    except ImportError as exc:
        typer.secho(
            f"error: dashboard module unavailable ({exc}); "
            "install with `pip install 'ascendo[dashboard]'`.",
            fg=typer.colors.RED, err=True,
        )
        raise typer.Exit(70) from None

    cache = InventoryCache()
    runs_dir = _default_runs_dir()
    typer.echo(f"scanning {adapter.name} adapter inventory…")
    try:
        buckets = _build_buckets_live(adapter, cache, runs_dir)
    except Exception as exc:
        typer.secho(f"error: inventory scan failed: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from None

    total = sum(len(v) for v in buckets.values())
    typer.echo("")
    for cat in sorted(buckets.keys()):
        items = buckets[cat]
        outdated = sum(1 for it in items if it.get("status") in ("outdated", "planned"))
        line = f"  {cat:<16s} {len(items):>5d} item(s)"
        if outdated:
            line += f"   ({outdated} outdated)"
        typer.echo(line)
    typer.echo("")
    typer.secho(f"scanned {total} package(s) across {len(buckets)} source(s).", fg=typer.colors.GREEN)

    if no_db:
        typer.echo("--no-db: skipping inventory.db flush.")
        raise typer.Exit(0)

    try:
        from ..dashboard.inventory_db import InventoryDB
    except ImportError as exc:
        typer.secho(
            f"warning: inventory_db module unavailable ({exc}); skipping flush.",
            fg=typer.colors.YELLOW, err=True,
        )
        raise typer.Exit(0) from None

    db_path = Path(os.environ.get("ASCENDO_INVENTORY_DB") or (Path.home() / ".ascendo" / "inventory.db"))
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db = InventoryDB(db_path)
    rows = _flatten_buckets_for_db(buckets)
    n_written = _replace_buckets_in_db(db, buckets, rows)
    try:
        db.set_meta(adapter.name, item_count=n_written)
    except Exception:
        _log.exception("inventory_db.set_meta failed")
    typer.secho(f"wrote {n_written} row(s) to {db_path}", fg=typer.colors.GREEN)
    raise typer.Exit(0)
