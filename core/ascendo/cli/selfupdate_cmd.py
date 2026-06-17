"""``ascendo self-update`` — update Ascendo itself.

Distinct from ``ascendo run`` (which updates the *apps Ascendo manages*).
Shares the exact same engine as the dashboard endpoints + the SPA
startup auto-check (:mod:`ascendo.selfupdate`), so behaviour is identical
across CLI, web, and desktop on macOS / Linux / Windows.

    ascendo self-update            # check, then upgrade if newer (prompts)
    ascendo self-update --check    # only report status (exit 0/1)
    ascendo self-update --yes      # upgrade without the confirmation prompt
"""
from __future__ import annotations

import time

import typer

from ._app import app


@app.command(name="self-update")
def self_update(
    check_only: bool = typer.Option(False, "--check", "-c", help="Only report whether an update is available."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Apply without confirmation."),
) -> None:
    """Check for a newer Ascendo and (optionally) upgrade in place."""
    from ..selfupdate import check_for_updates, detect_install, get_job, start_update
    from ..selfupdate.apply import UpdateNotSupported

    info = detect_install()
    report = check_for_updates(info)

    cur = report.get("current_core")
    latest = report.get("latest_core")

    if not report.get("ok"):
        typer.secho(
            f"Could not check for updates: {report.get('error')}",
            fg=typer.colors.YELLOW, err=True,
        )
        typer.echo(f"Installed: ascendo {cur}")
        raise typer.Exit(0 if check_only else 1)

    if not report.get("update_available"):
        typer.secho(f"Ascendo is up to date (v{cur}, channel: {report.get('channel')}).", fg=typer.colors.GREEN)
        raise typer.Exit(0)

    # An update exists.
    if report.get("core_update_available"):
        typer.secho(f"Update available: ascendo {cur} → {latest}", fg=typer.colors.CYAN)
    if report.get("shell_update_available"):
        typer.secho(
            f"Desktop shell update available: {report.get('current_shell')} → {report.get('latest_shell')}",
            fg=typer.colors.CYAN,
        )
    if report.get("notes_url"):
        typer.echo(f"Release notes: {report['notes_url']}")

    if check_only:
        raise typer.Exit(0)

    if not report.get("can_self_update"):
        art = report.get("shell_artifact") or {}
        url = art.get("dmg_url") or art.get("msi_url") or art.get("appimage_url")
        typer.secho(
            "This install can't upgrade itself in-app (not a git checkout).",
            fg=typer.colors.YELLOW, err=True,
        )
        if url:
            typer.echo(f"Download the latest installer: {url}")
        raise typer.Exit(2)

    if not yes:
        if not typer.confirm(f"Upgrade Ascendo to {latest} now?", default=True):
            typer.echo("Cancelled.")
            raise typer.Exit(0)

    try:
        job = start_update(info)
    except UpdateNotSupported as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(2) from None

    # Stream the job log to the terminal until it finishes.
    printed = 0
    while True:
        live = get_job(job.id)
        if live is None:
            break
        while printed < len(live.log):
            typer.echo(live.log[printed])
            printed += 1
        if live.state in {"success", "error"}:
            break
        time.sleep(0.3)

    final = get_job(job.id)
    if final and final.state == "success":
        typer.secho(
            f"Updated: {final.version_before} → {final.version_after or latest}. "
            "Restart the dashboard / desktop app to load the new version.",
            fg=typer.colors.GREEN,
        )
        raise typer.Exit(0)
    typer.secho(
        f"Update failed: {final.error if final else 'unknown error'}",
        fg=typer.colors.RED, err=True,
    )
    raise typer.Exit(1)
