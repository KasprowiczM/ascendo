"""Typer app root, shared helpers, banner, and ``version`` / ``main``."""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import NoReturn

import typer

from .. import __version__
from ..adapter_factory import AdapterRegistry, NoAdapterAvailableError, select_adapter
from ..models.package import SourceType
from ..models.run import Phase

app = typer.Typer(
    name="ascendo",
    help="Cross-platform update orchestrator.",
    no_args_is_help=False,
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

web_app = typer.Typer(
    name="web",
    help="Start/stop/restart the FastAPI dashboard (web UI).",
    no_args_is_help=True,
)
app.add_typer(web_app, name="web")

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


def _planned(name: str, milestone: str) -> NoReturn:
    typer.secho(f"'{name}' is not yet implemented (planned for {milestone}).", fg=typer.colors.YELLOW, err=True)
    raise typer.Exit(64)


def _sidecars_need_reboot(sidecars) -> bool:
    """True if any sidecar in this run signals a pending reboot.

    Two channels (preserved for cross-platform compatibility):
      1. Top-level `sc.needs_reboot` flag (canonical, M5.4 macOS softwareupdate
         + any future adapter that uses the json_set_needs_reboot bash helper).
      2. Message-text scan for "Reboot required" (legacy Windows convention).
    """
    for sc in sidecars:
        if getattr(sc, "needs_reboot", False):
            return True
        for msg in getattr(sc, "messages", ()):
            text = (getattr(msg, "text", "") or "").lstrip()
            if text.lower().startswith("reboot required"):
                return True
    return False


# ── Banner / first-run guide ──────────────────────────────────────────────

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
    if ctx.invoked_subcommand is not None:
        return
    typer.echo(render_banner())


@app.command()
def version() -> None:
    """Print Ascendo version."""
    typer.echo(f"ascendo {__version__}")


def main() -> None:
    app()
