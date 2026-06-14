"""``ascendo schedule`` subcommands: install, remove, list, trigger."""
from __future__ import annotations

import typer

from ..interfaces.scheduler import SchedulerError, ScheduleSpec
from ._app import (
    _resolve_adapter_for_capability,
    _setup_logging,
    schedule_app,
)


@schedule_app.command("install")
def schedule_install(
    calendar: str = typer.Option(
        ..., "--calendar",
        help="Schedule expression in backend-native syntax "
             "(systemd OnCalendar / schtasks / launchd cron-like).",
    ),
    profile: str = typer.Option("safe", "--profile", help="Profile to run on schedule."),
    name: str = typer.Option(
        "ascendo-default", "--name",
        help="Schedule slug (lowercase, dashes). Used by the backend as the entry id.",
    ),
    description: str | None = typer.Option(None, "--description"),
    no_drivers: bool = typer.Option(False, "--no-drivers", help="(reserved for adapter use)"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Install or update a scheduled run entry. Idempotent."""
    _setup_logging(verbose)
    adapter, host = _resolve_adapter_for_capability()
    sched_mgr = adapter.scheduler()
    if sched_mgr is None:
        typer.secho(
            f"error: scheduler not supported on this adapter ({adapter.name}).",
            fg=typer.colors.RED, err=True,
        )
        raise typer.Exit(10)
    if not sched_mgr.is_available(host):
        typer.secho(
            f"error: scheduler backend '{sched_mgr.backend}' is not operational on this host.",
            fg=typer.colors.RED, err=True,
        )
        raise typer.Exit(10)
    # `no_drivers` is captured in the description so the underlying script
    # can pick it up if/when it grows that knob; the public ScheduleSpec
    # surface intentionally stays narrow.
    desc = description or ""
    if no_drivers and "no-drivers" not in desc:
        desc = (desc + " [no-drivers]").strip()
    try:
        spec = ScheduleSpec(
            name=name,
            expression=calendar,
            profile=profile,
            enabled=True,
            description=desc or None,
        )
        sched_mgr.install(host, spec)
    except SchedulerError as e:
        typer.secho(f"error: {e}", fg=typer.colors.RED, err=True)
        raise typer.Exit(20) from None
    typer.secho(
        f"schedule installed: {spec.name} (backend={sched_mgr.backend})",
        fg=typer.colors.GREEN,
    )
    typer.echo(f"  expression: {spec.expression}")
    typer.echo(f"  profile:    {spec.profile}")
    if spec.description:
        typer.echo(f"  description: {spec.description}")


@schedule_app.command("remove")
def schedule_remove(
    name: str = typer.Option(
        "ascendo-default", "--name", help="Schedule slug to remove.",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Uninstall a scheduled run entry. No-op if it does not exist."""
    _setup_logging(verbose)
    adapter, host = _resolve_adapter_for_capability()
    sched_mgr = adapter.scheduler()
    if sched_mgr is None:
        typer.secho(
            f"error: scheduler not supported on this adapter ({adapter.name}).",
            fg=typer.colors.RED, err=True,
        )
        raise typer.Exit(10)
    try:
        sched_mgr.uninstall(host, name)
    except SchedulerError as e:
        typer.secho(f"error: {e}", fg=typer.colors.RED, err=True)
        raise typer.Exit(20) from None
    typer.secho(f"schedule removed: {name}", fg=typer.colors.GREEN)


@schedule_app.command("list")
def schedule_list(
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """List Ascendo's scheduled run entries."""
    _setup_logging(verbose)
    adapter, host = _resolve_adapter_for_capability()
    sched_mgr = adapter.scheduler()
    if sched_mgr is None:
        typer.secho(
            f"error: scheduler not supported on this adapter ({adapter.name}).",
            fg=typer.colors.RED, err=True,
        )
        raise typer.Exit(10)
    try:
        items = sched_mgr.list(host)
    except SchedulerError as e:
        typer.secho(f"error: {e}", fg=typer.colors.RED, err=True)
        raise typer.Exit(20) from None
    if not items:
        typer.secho("no schedule entries.", fg=typer.colors.YELLOW)
        return
    typer.secho(
        f"{'NAME':<32s} {'PROFILE':<10s} {'ENABLED':<8s} {'EXPRESSION'}",
        bold=True,
    )
    for s in items:
        typer.echo(
            f"{s.name:<32s} {s.profile:<10s} {('yes' if s.enabled else 'no'):<8s} {s.expression}"
        )


@schedule_app.command("trigger")
def schedule_trigger(
    name: str = typer.Option(
        "ascendo-default", "--name", help="Schedule slug to trigger now.",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Run the named schedule entry immediately."""
    _setup_logging(verbose)
    adapter, host = _resolve_adapter_for_capability()
    sched_mgr = adapter.scheduler()
    if sched_mgr is None:
        typer.secho(
            f"error: scheduler not supported on this adapter ({adapter.name}).",
            fg=typer.colors.RED, err=True,
        )
        raise typer.Exit(10)
    try:
        sched_mgr.trigger(host, name)
    except SchedulerError as e:
        typer.secho(f"error: {e}", fg=typer.colors.RED, err=True)
        raise typer.Exit(20) from None
    typer.secho(f"schedule triggered: {name}", fg=typer.colors.GREEN)

