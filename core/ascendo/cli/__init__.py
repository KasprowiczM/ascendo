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
from ..interfaces.scheduler import ScheduleSpec, SchedulerError
from ..interfaces.snapshot import SnapshotError
from ..models.package import SourceType
from ..models.run import Phase, PhaseStatus, RunInfo, Trigger
from ..orchestrator import run_phases

app = typer.Typer(
    name="ascendo",
    help="Cross-platform update orchestrator.",
    no_args_is_help=False,  # we render our own banner via the callback below
)
runs_app = typer.Typer(
    name="runs",
    help="Inspect previous run sidecars on disk.",
    no_args_is_help=True,
)
app.add_typer(runs_app, name="runs")

snapshot_app = typer.Typer(
    name="snapshot",
    help="Manage system snapshots (VSS on Windows, timeshift on Linux).",
    no_args_is_help=True,
)
app.add_typer(snapshot_app, name="snapshot")

schedule_app = typer.Typer(
    name="schedule",
    help="Manage scheduled runs (Task Scheduler on Windows, systemd timer on Linux).",
    no_args_is_help=True,
)
app.add_typer(schedule_app, name="schedule")

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


# ── Banner / first-run guide ──────────────────────────────────────────────
# Bare `ascendo` (no subcommand) renders a translated banner with a
# subcommand table and example flows. `ascendo --help` is unchanged
# (Typer's auto-generated help). Locale resolution order:
#   1. ASCENDO_LOCALE env var
#   2. ~/.config/ascendo/locale.txt
#   3. default 'en'

_BANNER_EN = {
    "title": "Ascendo — Unified updates. Every app. One click.",
    "tagline": "Cross-platform update orchestrator (Windows / Linux / macOS).",
    "quickstart_header": "Quick start",
    "quickstart_lines": [
        ("ascendo doctor",                  "5-component health snapshot of your system"),
        ("ascendo run --phase check",       "find available updates (read-only, ~15 s)"),
        ("ascendo run --phase apply",       "apply updates (gated, idempotent)"),
    ],
    "commands_header": "Commands",
    "commands": [
        ("version",         "Print Ascendo version."),
        ("doctor",          "Adapter self-test: detect OS, check tool health."),
        ("run",             "Drive the OS adapter through one or more phases."),
        ("runs list",       "List runs on disk, newest first."),
        ("runs show <id>",  "Show every phase + per-category status for a run."),
        ("runs json <id>",  "Emit consolidated ascendo/run/v1 JSON (pipe to jq)."),
        ("dashboard",       "Launch the FastAPI + SPA dashboard on 127.0.0.1."),
        ("snapshot",        "Manage system snapshots (VSS / Time Machine / timeshift)."),
        ("schedule",        "Manage scheduled runs (Task Scheduler / launchd / systemd)."),
    ],
    "examples_header": "Examples",
    "examples": [
        "ascendo run --category winget --phase check    # one source, one phase",
        "ascendo run --phases check,plan --profile safe # safe profile, dry-survey",
        "ascendo dashboard --background                 # detached web UI",
        "ascendo runs json <id> --pretty | jq .summary  # scripting hook",
    ],
    "more_help": "Run `ascendo <command> --help` for details on any command.",
    "docs_link":  "Docs: https://github.com/KasprowiczM/ascendo",
}

_BANNER_PL = {
    "title": "Ascendo — Zunifikowane aktualizacje. Każda aplikacja. Jednym kliknięciem.",
    "tagline": "Wieloplatformowy orchestrator aktualizacji (Windows / Linux / macOS).",
    "quickstart_header": "Szybki start",
    "quickstart_lines": [
        ("ascendo doctor",                  "kontrola zdrowia systemu (5 komponentów)"),
        ("ascendo run --phase check",       "wyszukaj dostępne aktualizacje (tylko-do-odczytu, ~15 s)"),
        ("ascendo run --phase apply",       "zastosuj aktualizacje (z potwierdzeniem, idempotentne)"),
    ],
    "commands_header": "Komendy",
    "commands": [
        ("version",         "Wyświetl wersję Ascendo."),
        ("doctor",          "Self-test adaptera: wykryj OS, sprawdź narzędzia."),
        ("run",             "Uruchom adapter OS przez jedną lub więcej faz."),
        ("runs list",       "Lista uruchomień na dysku, najnowsze pierwsze."),
        ("runs show <id>",  "Szczegóły faz i kategorii pojedynczego uruchomienia."),
        ("runs json <id>",  "Skonsolidowany JSON ascendo/run/v1 (do jq)."),
        ("dashboard",       "Uruchom dashboard FastAPI + SPA na 127.0.0.1."),
        ("snapshot",        "Zarządzaj snapshotami (VSS / Time Machine / timeshift)."),
        ("schedule",        "Zarządzaj zaplanowanymi uruchomieniami (Task Scheduler / launchd / systemd)."),
    ],
    "examples_header": "Przykłady",
    "examples": [
        "ascendo run --category winget --phase check    # jedno źródło, jedna faza",
        "ascendo run --phases check,plan --profile safe # profil 'safe', tylko podgląd",
        "ascendo dashboard --background                 # web UI w tle",
        "ascendo runs json <id> --pretty | jq .summary  # skrypty / automatyzacja",
    ],
    "more_help": "Uruchom `ascendo <komenda> --help` po szczegóły.",
    "docs_link":  "Dokumentacja: https://github.com/KasprowiczM/ascendo",
}


def _resolve_locale() -> str:
    """Pick the locale for the banner. Returns 'pl' or 'en'."""
    env_locale = os.environ.get("ASCENDO_LOCALE", "").strip().lower()
    if env_locale:
        return "pl" if env_locale.startswith("pl") else "en"
    cfg = Path.home() / ".config" / "ascendo" / "locale.txt"
    if cfg.is_file():
        try:
            stored = cfg.read_text(encoding="utf-8").strip().lower()
            if stored:
                return "pl" if stored.startswith("pl") else "en"
        except OSError:
            pass
    return "en"


def render_banner(locale: str | None = None) -> str:
    """Build the bare-`ascendo` banner string. Pure function — no I/O.

    Public so the contract test can import + assert on its content
    without driving a Typer CliRunner. ``locale=None`` resolves via
    :func:`_resolve_locale`; pass ``"en"`` / ``"pl"`` to force.
    """
    loc = locale or _resolve_locale()
    b = _BANNER_PL if loc == "pl" else _BANNER_EN

    lines: list[str] = []
    lines.append("")
    lines.append(b["title"])
    lines.append(b["tagline"])
    lines.append("")
    lines.append(b["quickstart_header"] + ":")
    for cmd, desc in b["quickstart_lines"]:
        lines.append(f"  {cmd:<32s} {desc}")
    lines.append("")
    lines.append(b["commands_header"] + ":")
    for cmd, desc in b["commands"]:
        lines.append(f"  {cmd:<18s} {desc}")
    lines.append("")
    lines.append(b["examples_header"] + ":")
    for ex in b["examples"]:
        lines.append(f"  {ex}")
    lines.append("")
    lines.append(b["more_help"])
    lines.append(b["docs_link"])
    lines.append("")
    return "\n".join(lines)


@app.callback(invoke_without_command=True)
def _main_callback(ctx: typer.Context) -> None:
    """Render the banner when no subcommand was invoked."""
    # If a subcommand was selected, do nothing — let Typer dispatch normally.
    if ctx.invoked_subcommand is not None:
        return
    # Plain `ascendo` with no args → print the banner and exit 0.
    typer.echo(render_banner())


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


def _sidecars_need_reboot(sidecars) -> bool:
    """True if any sidecar in this run signals a pending reboot.

    Two channels (preserved for cross-platform compatibility):
      1. Top-level `sc.needs_reboot` flag (canonical, M5.4 macOS softwareupdate
         + any future adapter that uses the json_set_needs_reboot bash helper).
      2. Message-text scan for "Reboot required" (legacy Windows convention).
    """
    for sc in sidecars:
        # Channel 1: top-level flag (new, canonical)
        if getattr(sc, "needs_reboot", False):
            return True
        # Channel 2: message-text scan (legacy Windows)
        for msg in getattr(sc, "messages", ()):
            text = (getattr(msg, "text", "") or "").lstrip()
            if text.lower().startswith("reboot required"):
                return True
    return False


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


# ── snapshot subcommands ──────────────────────────────────────────────────
# These delegate to the adapter's ISnapshot implementation. The interface
# (core/ascendo/interfaces/snapshot.py) deliberately exposes only
# create / list / get — actual restore is documented as a user-only
# operation that lives in the dashboard. The CLI's `restore` subcommand
# is kept as a clear placeholder with exit 64 so users know it is not
# yet implemented at the interface level.


def _resolve_adapter_for_capability():
    """Discover and select an adapter, mapping registry errors to exit codes.

    Returns the selected adapter and its detected host. Exits 3 if no
    adapter is available on this host (mirrors the pattern used by
    :func:`run` and :func:`doctor`).
    """
    registry = AdapterRegistry()
    registry.discover()
    try:
        adapter = select_adapter(registry=registry)
    except NoAdapterAvailableError as e:
        typer.secho(f"error: {e}", fg=typer.colors.RED, err=True)
        raise typer.Exit(3) from None
    host = adapter.detect_host()
    return adapter, host


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
    effective_label = label or f"ascendo {datetime.now(timezone.utc).isoformat(timespec='seconds')}"
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


# ── schedule subcommands ──────────────────────────────────────────────────


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


def main() -> None:
    app()


if __name__ == "__main__":
    main()


__all__ = ["app", "main"]
