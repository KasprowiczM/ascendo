"""Prompt library loader + system prompt builder.

The library lives at `prompts/library.toml` (a sibling data directory next
to this module). EN+PL parity is enforced by the tests in
`tests/contract/test_ai_context_injector.py`.

System prompts are split EN / PL so the LLM gets a native-language frame
rather than relying on the user-locale hint inside the context blob alone.
"""
from __future__ import annotations

from pathlib import Path

try:
    import tomllib  # py3.11+
except ImportError:  # pragma: no cover - py3.10 fallback
    import tomli as tomllib  # type: ignore[no-redef]


LIBRARY_PATH = Path(__file__).resolve().parent / "prompts" / "library.toml"


SYSTEM_PROMPT_EN = """You are Ascendo's AI Tools assistant.
Ascendo is a unified-updates app for macOS, Windows, and Ubuntu. The user is
running it on their own machine and wants help with: diagnosing failed update
runs, recommending exclusions/schedules, setting up the app, and customizing
their web app registry.

You have read-only access to the user's machine state via an injected context
blob (delimited by <ascendo_context> tags). Use that context to ground your
answers - do not make up versions, app names, or run IDs.

When you want to propose an action the user could take, emit it as a fenced
code block with language `ascendo-action` containing a JSON object with these
fields: id, label_en, label_pl, verb, path, body, confirm, risk.
Only use action IDs from this whitelist: run_check, run_plan, run_apply,
run_verify, run_cleanup, install_schedule, remove_schedule, trigger_schedule,
refresh_inventory, add_web_override, edit_skip_list, open_view.

Be concise. Be honest when you don't have enough context."""


SYSTEM_PROMPT_PL = """Jesteś asystentem AI Tools w Ascendo.
Ascendo to ujednolicona aplikacja do aktualizacji dla macOS, Windows i Ubuntu.
Użytkownik uruchamia ją na swoim komputerze i chce pomocy w: diagnozowaniu
nieudanych aktualizacji, rekomendowaniu wykluczeń i harmonogramów, konfiguracji
aplikacji i dostosowaniu rejestru aplikacji web.

Masz dostęp tylko do odczytu do stanu maszyny użytkownika przez wstrzyknięty
blok kontekstu (oznaczony tagami <ascendo_context>). Wykorzystuj ten kontekst
do uzasadnienia odpowiedzi - nie wymyślaj wersji, nazw aplikacji ani ID
uruchomień.

Gdy chcesz zaproponować akcję, emituj ją jako fenced code block z językiem
`ascendo-action` zawierający obiekt JSON z polami: id, label_en, label_pl,
verb, path, body, confirm, risk.
Używaj tylko ID akcji z tej listy: run_check, run_plan, run_apply, run_verify,
run_cleanup, install_schedule, remove_schedule, trigger_schedule,
refresh_inventory, add_web_override, edit_skip_list, open_view.

Bądź zwięzły. Bądź uczciwy, gdy brakuje Ci kontekstu."""


def system_prompt(locale: str) -> str:
    """Return the system prompt in the requested locale (en|pl)."""
    return SYSTEM_PROMPT_PL if locale == "pl" else SYSTEM_PROMPT_EN


def load_library() -> list[dict]:
    """Read every entry from the shipped library TOML."""
    with open(LIBRARY_PATH, "rb") as f:
        data = tomllib.load(f)
    return data.get("entries", [])


def filtered_library(*, adapter_name: str) -> list[dict]:
    """Return library entries applicable to the given adapter.

    Entries without a `platforms` list are universal. Entries with one
    are gated to the adapters they list (e.g. macOS-only Touch ID prompt).
    """
    out: list[dict] = []
    for entry in load_library():
        platforms = entry.get("platforms")
        if platforms is None or adapter_name in platforms:
            out.append(entry)
    return out
