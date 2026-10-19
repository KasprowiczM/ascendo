"""Human-readable post-apply report generator.

After every run that included an ``apply`` phase, the orchestrator writes
``<runs_dir>/<run_id>/REPORT.md`` — a short, plain-English summary of what
actually happened. The operator should be able to read it without knowing
about sidecars, handlers, or any internal jargon.

The report has these sections, in order:

1. Header — run id (short), timestamp, host, profile, duration, result
2. Reboot banner (only if any sidecar set ``needs_reboot``)
3. ``Problems and unverified steps`` — every phase sidecar of the run
   (check/plan/apply/verify/cleanup) that is ``failed``/``partial``, plus
   categories whose post-apply state is *unknown* (verify missing/failed)
4. ``Still pending after this run`` — verify items that are not at the
   target version
5. ``Action required`` — items (any category) the user must act on
6. ``What changed`` — apps that were upgraded, grouped by category, with
   ``from → to`` versions
7. ``Already up-to-date`` — counts per category whose ``check`` sidecar
   succeeded (no per-app list — would bloat the report)
   ``Updated outside Ascendo`` — items ``check`` offered (planned) that a vendor
   updater / the App Store updated before ``apply`` ran (apply found them
   ``up_to_date``); shown right after the up-to-date counts
8. ``Trigger-only updates`` — Tier-B vendor agents (Keystone, Squirrel,
   builtin) that queued an async update
9. ``Deferred`` — apps that were skipped because they were running, or
   for some other recoverable reason
10. ``Manufacturer warnings`` — apps whose vendor feed is *older* than the
    installed version (``ASCENDO-STATUS code=feed_stale``): a warning, never
    "pending", "failed" or action-required
11. ``Failed`` — apps whose apply errored, with the error message captured
    from the sidecar
12. ``Run notes`` — run stopped early / cancelled / managers skipped

Honesty rule: the report never claims "all updates applied successfully"
or "Failed (0 apps)" when any phase sidecar of the run failed or when the
post-apply state could not be verified. Unknown is not zero.

The generator is best-effort: any sidecar that fails to parse is silently
skipped, and the function never raises (a failed report should not abort
the run).

This module is intentionally pure-stdlib + Pydantic — no jinja2, no
templating engine. The markdown is small enough to build with f-strings.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from ..models.host import HostInfo
from ..models.package import SourceType
from ..models.result import Item, ItemStatus, Message, MessageLevel
from ..models.run import Phase, PhaseStatus
from ..models.sidecar import Sidecar
from .sidecar_io import list_run_sidecars, read_sidecar

_log = logging.getLogger(__name__)

REPORT_FILENAME = "REPORT.md"


# ── Display names ────────────────────────────────────────────────────────────
# Operator-friendly category labels. Falls back to the SourceType.value
# verbatim for any category not in the map.
_CATEGORY_LABELS: dict[str, str] = {
    "brew": "Homebrew",
    "brew_formula": "Homebrew formulae",
    "brew_cask": "Homebrew casks",
    "mas": "Mac App Store",
    "mac_app_store": "Mac App Store",
    "npm": "Node.js (npm)",
    "pip": "Python (pip)",
    "pipx": "Python (pipx)",
    "softwareupdate": "macOS system updates",
    "web": "Web apps (AppImage / GitHub releases / Sparkle)",
    "winget": "winget",
    "msstore": "Microsoft Store",
    "windows_update": "Windows Update",
    "registry_arp": "Windows installers",
    "appx": "Windows AppX",
    "apt": "APT (Ubuntu)",
    "snap": "Snap",
    "flatpak": "Flatpak",
    "plugin": "Plugin",
    "system": "macOS system apps",
    "inventory": "Inventory",
}


# Apply-phase order to render in the report. Categories not in this list
# fall to the end in alphabetical order.
_CATEGORY_DISPLAY_ORDER: tuple[str, ...] = (
    "softwareupdate",
    "windows_update",
    "brew",
    "brew_formula",
    "brew_cask",
    "mas",
    "mac_app_store",
    "winget",
    "msstore",
    "registry_arp",
    "apt",
    "snap",
    "flatpak",
    "npm",
    "pip",
    "pipx",
    "web",
)


def _category_label(category: str) -> str:
    return _CATEGORY_LABELS.get(category, category)


def _category_sort_key(category: str) -> tuple[int, str]:
    try:
        return (_CATEGORY_DISPLAY_ORDER.index(category), "")
    except ValueError:
        return (len(_CATEGORY_DISPLAY_ORDER), category)


# ── Buckets ──────────────────────────────────────────────────────────────────


@dataclass
class _CategoryBuckets:
    """Per-category collected items by outcome."""

    upgraded: list[Item] = field(default_factory=list)
    up_to_date: list[Item] = field(default_factory=list)
    triggered: list[Item] = field(default_factory=list)
    deferred: list[Item] = field(default_factory=list)
    failed: list[Item] = field(default_factory=list)
    # Phase-level messages (info on deferrals etc.) — keyed by item id where
    # we can extract that prefix; otherwise listed as freeform notes.
    phase_messages: list[Message] = field(default_factory=list)

    @property
    def total_known(self) -> int:
        return (
            len(self.upgraded)
            + len(self.up_to_date)
            + len(self.triggered)
            + len(self.deferred)
            + len(self.failed)
        )


# ── Action required (Phase A: the no-app-silently-missing guarantee) ─────────

# Stable marker stamped by phase scripts (any category) on every item the
# user must act on. Parsed deterministically (never fuzzy).
#
#   token   = ASCENDO-ACTION-REQUIRED reason=<r> cur=<c> cand=<d>
#   message = "<item id>: <human text> | <token>"     (a sidecar message,
#             phase-level or item-level)
#
# * <r>: short machine code without spaces (see ``_REASON_TEXT``).
# * <c>/<d>: versions without spaces; "?" = unknown.
# * The item id is the text before the first ": " of the message (ids must
#   not contain ": ").
# Bash scripts build the token with ``ascendo_action_required_token``
# (adapters/macos/lib/ascendo_json.sh). ``web`` messages keep their
# historical ``<slug>: ...`` shape, which this parser also accepts.
ACTION_RE = re.compile(
    r"ASCENDO-ACTION-REQUIRED reason=(\S+) cur=(\S+) cand=(\S+)"
)

_REASON_TEXT = {
    "self_update": "Updates itself in the background — open it once to apply.",
    "no_silent_path": "No silent install path — open it and use Help → Check for Updates.",
    "probe_broken": "Ascendo can't detect a version — fix via AI Tools or update manually.",
    "failed": "Update failed — open the app and update manually.",
    "app_in_use": "The app was running during the update — quit it and re-run apply.",
    "major_upgrade": (
        "A major macOS upgrade is available. Ascendo never installs it "
        "automatically — upgrade from System Settings → Software Update."
    ),
    "ipad_pending": (
        "iPad app with a pending update — open the App Store → Updates and "
        "update it there (mas cannot)."
    ),
    "rosetta_missing": (
        "An Intel-only app needs Rosetta, which is not installed — run "
        "`softwareupdate --install-rosetta` in Terminal (or set "
        "ASCENDO_INSTALL_ROSETTA=1 for a full-profile run)."
    ),
    "xcode_license": (
        "The Xcode license has not been accepted — run "
        "`sudo xcodebuild -license accept` in Terminal, then re-run."
    ),
    "cask_orphan": (
        "The cask's app is gone from disk — nothing to upgrade. Remove the "
        "stale cask with `brew uninstall --cask --force <name>`."
    ),
    "cask_needs_admin": (
        "This cask needs an administrator password to upgrade — run "
        "`brew upgrade --cask <name>` in Terminal."
    ),
    "needs_restart": (
        "Needs a restart / the app is running — quit it (or restart) and re-run apply."
    ),
    "feed_stale": (
        "The vendor's update feed is older than the installed version, so the "
        "version could not be verified — nothing was changed. Check the app's "
        "own updater if you expect a newer release."
    ),
    "behind": (
        "A newer version is published but could not be installed silently — "
        "open the app and use its own updater."
    ),
    "rollout_hold": (
        "The vendor is rolling this release out in stages and has not offered it "
        "to this Mac yet — the app's own updater will pick it up; nothing to do."
    ),
    "error_soft": (
        "The vendor download or its verification failed, so nothing was replaced "
        "(the previous app is untouched) — re-run apply later or update from the app."
    ),
    "download_error": (
        "The vendor download or its verification failed, so nothing was replaced "
        "(the previous app is untouched) — re-run apply later or update from the app."
    ),
    "launch_failed": (
        "Ascendo could not start the app to let its own updater run — open it "
        "yourself and use its update menu."
    ),
    "launched": (
        "Ascendo started the app hidden so its own updater could run; the update "
        "is not confirmed until the next check."
    ),
    "unverified": (
        "The update was triggered long ago but the installed version has never "
        "changed — open the app and check for updates yourself."
    ),
    "identity_unverifiable": (
        "Ascendo could not verify the app's signed identity (bundle id / Team ID), "
        "so it refused to replace it — update it from the app itself."
    ),
    "app_facts_unavailable": (
        "Ascendo could not read the app's details from Homebrew, so it cannot tell "
        "whether the app is running and did not upgrade it — re-run apply or run "
        "`brew upgrade --cask <name>` yourself."
    ),
    "unknown_version": (
        "The installed and the published version cannot be compared, so the app "
        "is not treated as up to date — check it manually."
    ),
    "sudo_unavailable": (
        "Root access was not available (no cached sudo and no askpass) — this "
        "system step is deferred. Authenticate interactively and re-run."
    ),
    "cli_duplicate": (
        "A second copy of this CLI sits next to the working native install. "
        "Remove the one you do not use: `npm uninstall -g --prefix <prefix> "
        "<package>` for an npm copy, `brew uninstall <formula>` for a Homebrew "
        "copy (Ascendo never removes Homebrew copies; npm copies only with "
        "ASCENDO_REMOVE_CLI_DUPLICATES=1)."
    ),
}
_OPEN_HINT_TEXT = {
    "major_upgrade": "Open System Settings → Software Update",
    "ipad_pending": "Open App Store → Updates",
    "xcode_license": "Run: sudo xcodebuild -license accept",
    "cask_orphan": "Run: brew uninstall --cask --force {slug}",
    "cask_needs_admin": "Run: brew upgrade --cask {slug}",
    "needs_restart": "Quit {name} and re-run",
    "app_in_use": "Quit {name} and re-run",
    "cli_duplicate": "Remove the duplicate copy (see the remedy in the run's npm messages)",
}
_DEFAULT_REASON_TEXT = "Open the app and check for updates manually."

# Status vocabulary (macOS_updates internet_status) carried as
# ``| ASCENDO-STATUS code=<code>`` in a message text (and in ``Message.code``).
# Only the codes below are *understood*; anything else (unknown code, empty or
# malformed token) is treated as "no code" so the generic diagnostic stays.
STATUS_RE = re.compile(r"ASCENDO-STATUS code=(\S+)")

STATUS_CODES = frozenset({
    "current_verified", "current_vendor", "current_cask_only", "updated",
    "update_available", "behind", "needs_restart", "rollout_hold", "feed_stale",
    "unverified", "launched", "error_soft",
})
# A ``triggered`` item carrying one of these is fine (listed by name under
# Trigger-only updates) and is NOT promoted to Action required.
# A fresh `unverified` (the version has not changed *yet*) is the same: only
# a STALE one -- unchanged longer than ASCENDO_WEB_STALE_DAYS -- is promoted,
# and verify marks that one itself with an ACTION-REQUIRED token (Spec G).
_BENIGN_TRIGGERED = frozenset({
    "launched", "updated", "unverified", "current_verified", "current_vendor",
    "current_cask_only", "rollout_hold",
})
# Informational outcomes: a skipped item with one of these needs no action.
_INFORMATIONAL_SKIPPED = frozenset({"rollout_hold"})
# The only codes that promote a triggered/skipped item to Action required.
_ATTENTION_CODES = frozenset({"behind", "needs_restart"})

_STALE_VERSIONS_RE = re.compile(
    r"vendor feed (\S+) (?:is older than|<) installed (\S+)"
)
_STATUS_TOKEN_RE = re.compile(r"\s*\|?\s*ASCENDO-STATUS code=\S*")
_SOFT_DEFERRAL_CATEGORIES = frozenset({"softwareupdate", "mas"})


@dataclass(frozen=True)
class ActionItem:
    """One item the user must act on (was not silently updated).

    ``category`` is the source category (``web``, ``softwareupdate``, ``mas``,
    ``brew`` ...). ``slug`` is the item identifier as the phase script
    reported it (the registry slug for web apps).
    """

    category: str
    name: str
    slug: str
    current: str
    candidate: str
    reason: str
    reason_text: str
    open_hint: str


def _slug_of(item: Item) -> str:
    raw = (item.id or item.name or "").strip()
    return raw.split(":", 1)[1] if ":" in raw else raw


def _pretty(slug: str) -> str:
    return slug.replace("-", " ").replace("_", " ").title()


def _status_reason(status: ItemStatus) -> str:
    if status is ItemStatus.TRIGGERED:
        return "self_update"
    if status in (ItemStatus.FAILED, ItemStatus.PARTIAL):
        return "failed"
    if status is ItemStatus.PLANNED:
        return "no_silent_path"
    return "probe_broken"


def _item_key(category: str, item: Item) -> str:
    """The identifier an ``ActionItem.slug`` uses for ``item``.

    Web ids are ``web:<slug>`` -> ``<slug>``; every other category keeps
    the raw id (labels/names may legitimately contain ``:``).
    """
    if category == "web":
        return _slug_of(item)
    return (item.id or item.name or "").strip()


def _action_display_name(category: str, slug: str) -> str:
    return _pretty(slug) if category == "web" else slug


def _action_open_hint(category: str, reason: str, slug: str, name: str) -> str:
    template = _OPEN_HINT_TEXT.get(reason)
    if template:
        return template.format(slug=slug, name=name)
    if category == "web":
        return f"Open {name}"
    return ""


def _make_action(
    category: str, slug: str, reason: str, cur: str, cand: str,
) -> ActionItem:
    name = _action_display_name(category, slug)
    text = _REASON_TEXT.get(reason, _DEFAULT_REASON_TEXT)
    if "<name>" in text:
        text = text.replace("<name>", slug)
    return ActionItem(
        category=category,
        name=name,
        slug=slug,
        current="" if cur == "?" else cur,
        candidate="" if cand == "?" else cand,
        reason=reason,
        reason_text=text,
        open_hint=_action_open_hint(category, reason, slug, name),
    )


def _token_messages(sc: Sidecar) -> list[str]:
    """Every message text of a sidecar (phase-level AND item-level)."""
    texts = [(m.text or "").strip() for m in sc.messages]
    for it in sc.items:
        texts.extend((m.text or "").strip() for m in it.messages)
    return texts


def _status_code_of(text: str, code: str | None) -> str | None:
    """The recognised status code of one message: the ``ASCENDO-STATUS`` token
    in its text, else the machine ``code`` field. ``None`` for anything not in
    :data:`STATUS_CODES` (unrecognised / malformed => the generic diagnostic stays)."""
    m = STATUS_RE.search(text or "")
    cand = m.group(1) if m else (code or "")
    return cand if cand in STATUS_CODES else None


def _status_codes(sc: Sidecar) -> dict[str, str]:
    """``{item slug: status code}`` for one sidecar (item-level messages win
    over the phase-level ones keyed ``"<slug>: ..."``)."""
    out: dict[str, str] = {}
    for m in sc.messages:
        code = _status_code_of(m.text or "", m.code)
        if code:
            out[_slug_from_token_message(m.text or "")] = code
    for it in sc.items:
        for m in it.messages:
            code = _status_code_of(m.text or "", m.code)
            if code:
                out[_item_key(sc.category.value, it)] = code
    return out


@dataclass(frozen=True)
class ManufacturerWarning:
    """An app whose vendor feed is older than the installed version."""

    category: str
    slug: str
    name: str
    installed: str
    vendor: str


def _manufacturer_warnings(apply_sidecars: list[Sidecar]) -> list[ManufacturerWarning]:
    """Skipped apply items carrying ``code=feed_stale`` WITH both versions.

    A stale feed without parseable versions is not a presentable warning, so it
    returns nothing for that item and its generic diagnostic stays. Hard
    failures (status failed/partial) are never downgraded to a warning."""
    out: list[ManufacturerWarning] = []
    for sc in apply_sidecars:
        cat = sc.category.value
        codes = _status_codes(sc)
        for it in sc.items:
            if it.status is not ItemStatus.SKIPPED:
                continue
            key = _item_key(cat, it)
            if codes.get(key) != "feed_stale":
                continue
            installed = (it.current_version or "").strip()
            if not installed:
                continue
            vendor = ""
            texts = [(m.text or "") for m in it.messages]
            texts += [(m.text or "") for m in sc.messages
                      if _slug_from_token_message(m.text or "") == key]
            for t in texts:
                mv = _STALE_VERSIONS_RE.search(t)
                if mv:
                    vendor = mv.group(1)
                    break
            if not vendor:
                continue
            out.append(ManufacturerWarning(cat, key, _action_display_name(cat, key),
                                           installed, vendor))
    return sorted(out, key=lambda w: (w.name.lower(), w.category))


# Codes that mark a system step deferred (not failed): lack of privileges
# (SUDO_UNAVAILABLE / SUDO_UNAVAILABLE_UNATTENDED) or the restart-capable
# step held back because an earlier apply failed (SYSTEM_UPDATE_DEFERRED).
_SOFT_DEFERRAL_CODES: tuple[str, ...] = (
    "SUDO_UNAVAILABLE", "SUDO_UNAVAILABLE_UNATTENDED", "SYSTEM_UPDATE_DEFERRED",
)


def _is_soft_deferral(sc: Sidecar, it: Item) -> bool:
    """A system step deferred for lack of privileges or a restart (F3).

    Counts: a skipped item whose messages carry ``SUDO_UNAVAILABLE`` (any
    category), or a skipped softwareupdate / mas item carrying an
    ``ASCENDO-ACTION-REQUIRED reason=needs_restart`` token. Policy skips (not
    Recommended, major-upgrade block) and ``ipad_pending`` are NOT deferrals
    (the latter is already an Action-required item)."""
    if it.status is not ItemStatus.SKIPPED:
        return False
    key = _item_key(sc.category.value, it)
    texts = [(m.code or "", m.text or "") for m in it.messages]
    texts += [(m.code or "", m.text or "") for m in sc.messages
              if _slug_from_token_message(m.text or "") in (key, it.name, it.id)]
    for code, text in texts:
        if code in _SOFT_DEFERRAL_CODES or any(c in text for c in _SOFT_DEFERRAL_CODES):
            return True
        am = ACTION_RE.search(text)
        if am and am.group(1) == "needs_restart" \
                and sc.category.value in _SOFT_DEFERRAL_CATEGORIES:
            return True
    return False


def _soft_deferrals(apply_sidecars: list[Sidecar]) -> list[tuple[str, Item]]:
    return [(sc.category.value, it) for sc in apply_sidecars for it in sc.items
            if _is_soft_deferral(sc, it)]


def _slug_from_token_message(text: str) -> str:
    """Item id of a token message: text before the first ``": "`` of the
    part preceding the token (falls back to the first ``:``)."""
    head = re.split(r"ASCENDO-(?:ACTION-REQUIRED|STATUS)", text, maxsplit=1)[0].rstrip(" |")
    if ": " in head:
        head = head.split(": ", 1)[0]
    else:
        head = head.split(":", 1)[0]
    return head.strip() or "?"


# Reasons a CHECK-phase token may raise on its own (nothing in apply/verify can
# know them: e.g. an Intel-only app that cannot launch without Rosetta).
_CHECK_ACTION_REASONS = frozenset({"rosetta_missing"})


def _action_items(
    apply_sidecars: list[Sidecar], verify_sidecars: list[Sidecar] | None = None,
    check_sidecars: list[Sidecar] | None = None,
) -> list[ActionItem]:
    """Every item the user must act on, deduped by (category, slug).

    Pass 1 (any category): deterministic ``ASCENDO-ACTION-REQUIRED`` token
    messages. Pass 2 (web only): any non-silent web item without a token still
    surfaces with a status-derived reason -- EXCEPT the ones the status
    vocabulary explains as fine: a stale vendor feed (Manufacturer warning),
    a triggered item whose code is launched / updated / current_* /
    rollout_hold, and an informational skip (rollout_hold). Items with no code,
    an unrecognised code or a malformed token keep surfacing as before.
    Pass 3: ``ASCENDO-ACTION-REQUIRED`` tokens of the VERIFY phase (a stale
    ``unverified`` item, a ``behind`` one); a fresh unverified is not promoted.
    """
    by_key: dict[tuple[str, str], ActionItem] = {}
    stale_keys = {(w.category, w.slug) for w in _manufacturer_warnings(apply_sidecars)}

    # Pass 1 — precise, from the deterministic token messages.
    for sc in apply_sidecars:
        category = sc.category.value
        for text in _token_messages(sc):
            m = ACTION_RE.search(text)
            if not m:
                continue
            reason, cur, cand = m.group(1), m.group(2), m.group(3)
            slug = _slug_from_token_message(text)
            by_key[(category, slug)] = _make_action(category, slug, reason, cur, cand)

    # Pass 2 — guarantee: any non-silent web item with no token still
    # surfaces (status-derived reason). Never silently missing.
    non_silent = {
        ItemStatus.SKIPPED, ItemStatus.TRIGGERED, ItemStatus.FAILED,
        ItemStatus.PARTIAL, ItemStatus.MISSING, ItemStatus.PLANNED,
    }
    for sc in apply_sidecars:
        if sc.category is not SourceType.WEB:
            continue
        codes = _status_codes(sc)
        for it in sc.items:
            if it.status not in non_silent:
                continue
            slug = _slug_of(it)
            if ("web", slug) in by_key or ("web", slug) in stale_keys:
                continue
            code = codes.get(slug)
            if it.status is ItemStatus.TRIGGERED and code in _BENIGN_TRIGGERED:
                continue
            if it.status is ItemStatus.SKIPPED and code in _INFORMATIONAL_SKIPPED:
                continue
            item_text = " ".join(
                (m.text or "") for m in it.messages
            ).lower()
            if code in _ATTENTION_CODES:
                reason = code
            elif "in_use" in item_text or "in use" in item_text or "running" in item_text:
                reason = "app_in_use"
            else:
                reason = _status_reason(it.status)
            by_key[("web", slug)] = _make_action(
                "web", slug, reason,
                (it.current_version or "").strip() or "?",
                (it.target_version or it.resolved_version or "").strip() or "?",
            )

    # Pass 3 -- tokens the verify phase stamped (stale unverified, behind).
    for sc in verify_sidecars or []:
        category = sc.category.value
        for text in _token_messages(sc):
            m = ACTION_RE.search(text)
            if not m:
                continue
            slug = _slug_from_token_message(text)
            if (category, slug) in by_key:
                continue
            by_key[(category, slug)] = _make_action(
                category, slug, m.group(1), m.group(2), m.group(3))

    # Pass 4 -- narrow allowlist of CHECK-phase tokens; never overrides the
    # precise apply/verify rows (dedupe by (category, slug)).
    for sc in check_sidecars or []:
        category = sc.category.value
        for text in _token_messages(sc):
            m = ACTION_RE.search(text)
            if not m or m.group(1) not in _CHECK_ACTION_REASONS:
                continue
            slug = _slug_from_token_message(text)
            if (category, slug) in by_key:
                continue
            by_key[(category, slug)] = _make_action(
                category, slug, m.group(1), m.group(2), m.group(3))

    return sorted(by_key.values(), key=lambda a: (a.name.lower(), a.category))


def collect_action_required(run_dir: Path) -> list[ActionItem]:
    """Public: the consolidated 'you must act on these' list for a run.

    Reads the run's apply sidecars and returns every item (web app,
    macOS update, iPad app, cask ...) the user must act on — see the
    ``ASCENDO-ACTION-REQUIRED`` token contract at :data:`ACTION_RE`; web
    apps that were not silently updated are always included. Never raises —
    returns ``[]`` on any error or when there is no apply phase.
    """
    try:
        if not run_dir.is_dir():
            return []
        apply_sidecars: list[Sidecar] = []
        verify_sidecars: list[Sidecar] = []
        check_sidecars: list[Sidecar] = []
        for path in list_run_sidecars(run_dir):
            try:
                sc = read_sidecar(path)
            except Exception:  # noqa: BLE001
                continue
            if sc.phase is Phase.APPLY:
                apply_sidecars.append(sc)
            elif sc.phase is Phase.VERIFY:
                verify_sidecars.append(sc)
            elif sc.phase is Phase.CHECK:
                check_sidecars.append(sc)
        return _action_items(apply_sidecars, verify_sidecars, check_sidecars)
    except Exception:  # noqa: BLE001
        _log.exception("collect_action_required failed for %s", run_dir)
        return []


# ── Public API ───────────────────────────────────────────────────────────────


def generate_apply_report(run_dir: Path, host: HostInfo | None = None) -> str | None:
    """Build a human-readable markdown report for the given run directory.

    Walks every ``<phase>__<category>.json`` sidecar in ``run_dir``,
    classifies items, and renders ``REPORT.md``. Writes the result to
    ``run_dir/REPORT.md`` and returns the markdown string.

    Returns ``None`` when there is no apply phase in the run — the report
    is meant to summarise what was applied; check-only / verify-only runs
    don't generate one.

    Never raises. On unexpected error, logs a warning and returns
    ``None`` without writing the file.

    Args:
        run_dir: Path to a ``<base_dir>/<run-id>/`` directory.
        host: Optional host snapshot for the header. If omitted, the
            report extracts host info from the first sidecar it finds.

    Returns:
        The generated markdown string, or ``None`` if nothing was
        produced.
    """
    try:
        return _generate_apply_report_unsafe(run_dir, host=host)
    except Exception:  # noqa: BLE001
        _log.exception("apply report generation failed for %s", run_dir)
        return None


def _generate_apply_report_unsafe(
    run_dir: Path, *, host: HostInfo | None,
) -> str | None:
    """Inner generator. May raise — wrapped by :func:`generate_apply_report`."""
    if not run_dir.is_dir():
        return None

    sidecars: list[Sidecar] = []
    for path in list_run_sidecars(run_dir):
        try:
            sidecars.append(read_sidecar(path))
        except Exception as exc:  # noqa: BLE001 — corrupt sidecars must not abort the report
            _log.debug("skipping unreadable sidecar %s: %s", path, exc)
            continue

    apply_sidecars = [s for s in sidecars if s.phase is Phase.APPLY]
    if not apply_sidecars:
        # Nothing to summarise — check-only or verify-only run.
        return None

    check_sidecars = [s for s in sidecars if s.phase is Phase.CHECK]
    verify_sidecars = [s for s in sidecars if s.phase is Phase.VERIFY]
    run_meta = _read_run_meta(run_dir)

    # Host info: prefer caller-supplied; fall back to the first sidecar's.
    resolved_host = host or apply_sidecars[0].host

    # Run-level metadata from the first apply sidecar (every sidecar in
    # the same run carries the same run block, but we only need one).
    run_info = apply_sidecars[0].run

    # Build a lookup of (category, item_id) -> target_version from check
    # sidecars. Apply sidecars often leave target_version blank (the script
    # has the running install but the candidate version was already
    # determined at check time), so we backfill from the check phase to
    # show "X.Y.Z → A.B.C" instead of just "X.Y.Z".
    check_targets: dict[tuple[str, str], str] = {}
    for sc in check_sidecars:
        cat = sc.category.value
        for it in sc.items:
            key = (cat, it.id)
            target = it.target_version or it.resolved_version
            if target:
                check_targets[key] = target

    # Per-category buckets from apply sidecars.
    buckets: dict[str, _CategoryBuckets] = {}
    for sc in apply_sidecars:
        cat = sc.category.value
        b = buckets.setdefault(cat, _CategoryBuckets())
        for item in sc.items:
            _classify_item(item, b)
        # Phase-level messages: keep info / warn / error so we can surface
        # deferral reasons and handler errors.
        b.phase_messages.extend(sc.messages)

    # Pull "already up-to-date" counts from check sidecars too — apply
    # sometimes only emits items it actually touched (e.g. brew/mas with
    # 0-outdated runs return total=0 in apply). The check phase is the
    # authoritative inventory baseline.
    check_counts: dict[str, int] = {}
    for sc in check_sidecars:
        cat = sc.category.value
        # A category is only counted "already up-to-date" when its check
        # actually succeeded. A failed/partial check is a problem (listed
        # below), never "0 outdated".
        if sc.status is not PhaseStatus.SUCCESS:
            continue
        # up_to_date count from check is the most accurate "X apps are
        # already current" figure; fall back to total when absent.
        c = sc.summary.up_to_date or sc.summary.total
        check_counts[cat] = c

    # Reboot detection — top-level flag on any sidecar.
    needs_reboot = any(getattr(s, "needs_reboot", False) for s in sidecars)

    # Aggregate counts.
    n_upgraded = sum(len(b.upgraded) for b in buckets.values())
    n_up_to_date = sum(check_counts.values()) or sum(
        len(b.up_to_date) for b in buckets.values()
    )
    n_triggered = sum(len(b.triggered) for b in buckets.values())
    n_deferred = sum(len(b.deferred) for b in buckets.values())
    n_failed = sum(len(b.failed) for b in buckets.values())

    # The consolidated "you must act on these" list (any category).
    action_items = _action_items(apply_sidecars, verify_sidecars, check_sidecars)
    action_keys = {(a.category, a.slug) for a in action_items}
    # Manufacturer warnings (stale vendor feed) and soft system deferrals are
    # warnings: counted, listed by name, never "failed" or "pending". A
    # cancelled run is already a Problem; it gets no extra deferral warning.
    mfr_warnings = _manufacturer_warnings(apply_sidecars)
    mfr_keys = {(w.category, w.slug) for w in mfr_warnings}
    status_by_cat = {sc.category.value: _status_codes(sc) for sc in apply_sidecars}
    soft_deferrals = (
        [] if _read_run_meta(run_dir).get("cancelled") else _soft_deferrals(apply_sidecars)
    )

    # Honesty (Spec 1): problems from EVERY phase sidecar + unknown state.
    problems, warnings = _collect_problems(sidecars)
    still_pending = _collect_still_pending(verify_sidecars)
    unverified = _unverified_categories(
        apply_sidecars, verify_sidecars, run_meta,
    )
    run_problems = _run_meta_problems(run_meta)
    # Categories already named in a Problems line are not repeated as
    # "could not be verified".
    problem_cats = {(p.category, p.phase) for p in problems}
    unverified_only = [
        c for c in unverified if (c, Phase.VERIFY.value) not in problem_cats
    ]
    hard_problem_count = (
        len(problems) + len(unverified_only) + len(run_problems)
    )
    issue_count = n_failed + hard_problem_count + len(still_pending)

    # Overall result. Precedence: problems beat every "success" claim.
    if issue_count:
        if hard_problem_count == 0 and not still_pending:
            # Only failed apply items (already listed under Failed).
            result_label = (
                "Mostly succeeded — some apps failed."
                if n_upgraded > 0 else "Some updates failed."
            )
        else:
            result_label = (
                f"Finished with problems — {issue_count} "
                f"{_pluralize('step', issue_count)} failed or could not be verified."
            )
    elif warnings:
        result_label = (
            f"Updates applied, but {len(warnings)} "
            f"{_pluralize('cleanup step', len(warnings))} reported problems."
            if n_upgraded > 0 else
            f"Nothing to update — but {len(warnings)} "
            f"{_pluralize('cleanup step', len(warnings))} reported problems."
        )
    elif mfr_warnings or soft_deferrals:
        bits: list[str] = []
        if mfr_warnings:
            bits.append(
                f"{len(mfr_warnings)} manufacturer "
                f"{_pluralize('feed', len(mfr_warnings))} could not confirm the "
                "installed version"
            )
        if soft_deferrals:
            bits.append(
                f"{len(soft_deferrals)} system {_pluralize('step', len(soft_deferrals))} "
                f"{'was' if len(soft_deferrals) == 1 else 'were'} deferred"
            )
        lead = "Updates applied, but " if n_upgraded > 0 else "Nothing to update — but "
        result_label = lead + " and ".join(bits) + "."
    elif n_upgraded > 0:
        result_label = "All updates applied successfully."
    else:
        result_label = "Nothing to update — everything was already current."

    # Duration — earliest started_at to latest finished_at across all sidecars.
    started = min((s.started_at for s in sidecars), default=None)
    finished = max((s.finished_at for s in sidecars), default=None)
    duration_str = _format_duration(started, finished)

    # ── Build the markdown ──────────────────────────────────────────────
    lines: list[str] = []
    short_id = str(run_info.id)[:8]
    timestamp = (started or datetime.now()).strftime("%Y-%m-%d %H:%M")
    lines.append(f"# Ascendo update — {timestamp} ({resolved_host.hostname})")
    lines.append("")
    lines.append(f"**Result:** {result_label}")
    lines.append(f"**Duration:** {duration_str}.")
    profile = run_info.profile or "full"
    lines.append(f"**Profile:** `{profile}`  ·  **Run id:** `{short_id}`")
    lines.append("")

    # Reboot banner (prominent, near top).
    if needs_reboot:
        lines.append("> **A system reboot is required** to finish applying these updates.")
        lines.append("")

    # ── At-a-glance counts ───────────────────────────────────────────────
    headline_bits: list[str] = []
    if n_upgraded:
        headline_bits.append(f"{n_upgraded} upgraded")
    if n_up_to_date:
        headline_bits.append(f"{n_up_to_date} already up-to-date")
    if n_triggered:
        headline_bits.append(f"{n_triggered} triggered")
    if n_deferred:
        headline_bits.append(f"{n_deferred} deferred")
    if n_failed:
        headline_bits.append(f"{n_failed} failed")
    if hard_problem_count:
        headline_bits.append(
            f"{hard_problem_count} {_pluralize('step', hard_problem_count)} "
            "failed/unverified"
        )
    if still_pending:
        headline_bits.append(f"{len(still_pending)} still pending")
    if warnings:
        headline_bits.append(
            f"{len(warnings)} cleanup {_pluralize('warning', len(warnings))}"
        )
    if mfr_warnings:
        headline_bits.append(
            f"{len(mfr_warnings)} manufacturer {_pluralize('warning', len(mfr_warnings))}"
        )
    if soft_deferrals:
        headline_bits.append(
            f"{len(soft_deferrals)} system {_pluralize('step', len(soft_deferrals))} deferred"
        )
    if action_items:
        _all_web = all(a.category == "web" for a in action_items)
        headline_bits.append(
            f"{len(action_items)} need you to open them"
            if _all_web else f"{len(action_items)} need your attention"
        )
    if headline_bits:
        lines.append("**At a glance:** " + ", ".join(headline_bits) + ".")
        lines.append("")

    # ── Problems and unverified steps (Spec 1: never hide a failure) ─────
    problem_lines: list[str] = []
    for p in problems:
        problem_lines.append(
            f"- **{_category_label(p.category)}** — {p.phase}: {p.reason}"
        )
    for cat in unverified_only:
        problem_lines.append(
            f"- **{_category_label(cat)}** — verify: the state after apply "
            "could not be verified (unknown, not confirmed up-to-date)"
        )
    for text in run_problems:
        problem_lines.append(f"- {text}")
    if problem_lines:
        lines.append(
            f"## ⚠ Problems and unverified steps ({len(problem_lines)})"
        )
        lines.append("")
        lines.extend(problem_lines)
        lines.append("")
    if warnings:
        lines.append(f"## Warnings ({len(warnings)})")
        lines.append("")
        for p in warnings:
            lines.append(
                f"- **{_category_label(p.category)}** — {p.phase}: {p.reason}"
            )
        lines.append("")

    # ── Still pending after this run (verify says: not at target) ────────
    if still_pending:
        lines.append(f"## Still pending after this run ({len(still_pending)})")
        lines.append("")
        for cat, item in still_pending:
            cur = (item.current_version or "").strip()
            tgt = (item.target_version or item.resolved_version or "").strip()
            ver = ""
            if cur or tgt:
                ver = f" (`{cur or '?'}` → `{tgt or '?'}`)"
            lines.append(
                f"- **{_display_name(item)}**{ver} — "
                f"{_category_label(cat)}: verify did not confirm the update"
            )
        lines.append("")

    # ── Action required (promoted out of generic Deferred) ───────────────
    if action_items:
        only_web = all(a.category == "web" for a in action_items)
        noun = "app" if only_web else "item"
        n_act = len(action_items)
        verb = "need" if n_act != 1 else "needs"
        lines.append(
            f"## ⚠ Action required — {n_act} {_pluralize(noun, n_act)} "
            + (f"{verb} you to open them" if only_web else f"{verb} your attention")
        )
        lines.append("")
        for a in action_items:
            ver = ""
            if a.current or a.candidate:
                ver = f" (`{a.current or '?'}` → `{a.candidate or '?'}`)"
            cat = "" if a.category == "web" else f" _{_category_label(a.category)}_:"
            lines.append(f"- **{a.name}**{ver} —{cat} {a.reason_text}")
        lines.append("")
        if any(a.category == "web" for a in action_items):
            lines.append(
                "Run Ascendo → AI Tools → \"Make Ascendo cover an app it's "
                "missing\" to automate any of these."
            )
            lines.append("")

    # ── What changed ────────────────────────────────────────────────────
    if n_upgraded > 0:
        lines.append(f"## What changed ({n_upgraded} {_pluralize('app', n_upgraded)} upgraded)")
        lines.append("")
        for cat in _ordered_categories(buckets):
            b = buckets[cat]
            if not b.upgraded:
                continue
            label = _category_label(cat)
            lines.append(f"### {label} ({len(b.upgraded)} {_pluralize('upgrade', len(b.upgraded))})")
            for item in sorted(b.upgraded, key=_item_sort_key):
                fallback_target = check_targets.get((cat, item.id))
                lines.append(_render_upgrade(item, fallback_target=fallback_target))
            lines.append("")

    # ── Already up-to-date ───────────────────────────────────────────────
    if n_up_to_date > 0:
        lines.append(f"## Already up-to-date ({n_up_to_date} {_pluralize('app', n_up_to_date)} checked)")
        lines.append("")
        # Use check counts where available; otherwise apply's up_to_date list.
        per_cat: list[tuple[str, int]] = []
        for cat in _ordered_categories_for_counts(check_counts, buckets):
            count = check_counts.get(cat) or len(buckets.get(cat, _CategoryBuckets()).up_to_date)
            if count <= 0:
                continue
            per_cat.append((cat, count))
        for cat, count in per_cat:
            label = _category_label(cat)
            lines.append(f"- {label}: {count} {_pluralize('package', count)}")
        lines.append("")

    # ── Updated outside Ascendo ──────────────────────────────────────────
    outside = _updated_outside(check_sidecars, apply_sidecars)
    if outside:
        lines.append(f"## Updated outside Ascendo ({len(outside)})")
        lines.append("")
        lines.append(
            "A vendor updater or the App Store updated these between check and "
            "apply — Ascendo did not touch them."
        )
        for cat, name, cur, tgt in outside:
            lines.append(f"- {name} ({_category_label(cat)}): {cur} → {tgt}")
        lines.append("")

    # ── Trigger-only updates ─────────────────────────────────────────────
    if n_triggered > 0:
        lines.append(
            f"## Trigger-only updates ({n_triggered} {_pluralize('app', n_triggered)} will self-update)"
        )
        lines.append("")
        for cat in _ordered_categories(buckets):
            b = buckets[cat]
            for item in sorted(b.triggered, key=_item_sort_key):
                lines.append(f"- **{_display_name(item)}** — vendor update agent triggered")
        lines.append("")

    # ── Deferred ─────────────────────────────────────────────────────────
    # Exclude web apps already promoted into the Action-required section
    # above so they are never double-listed / buried here.
    deferred_rows: list[tuple[str, Item]] = []
    for cat in _ordered_categories(buckets):
        b = buckets[cat]
        for item in sorted(b.deferred, key=_item_sort_key):
            if (cat, _item_key(cat, item)) in action_keys | mfr_keys:
                continue
            deferred_rows.append((cat, item))
    if deferred_rows:
        lines.append(
            f"## Deferred ({len(deferred_rows)} "
            f"{_pluralize('app', len(deferred_rows))})"
        )
        lines.append("")
        for cat, item in deferred_rows:
            reason = _deferral_reason(
                item, buckets[cat].phase_messages,
                code=status_by_cat.get(cat, {}).get(_item_key(cat, item)),
            )
            lines.append(f"- **{_display_name(item)}** — {reason}")
        lines.append("")

    # ── Manufacturer warnings (stale vendor feed) ────────────────────────
    if mfr_warnings:
        lines.append(f"## Manufacturer warnings ({len(mfr_warnings)})")
        lines.append("")
        for w in mfr_warnings:
            lines.append(
                f"- **{w.name}** — vendor feed `{w.vendor}` is older than the "
                f"installed `{w.installed}` ({_category_label(w.category)}). "
                f"{_REASON_TEXT['feed_stale']}"
            )
        lines.append("")

    # ── Failed ───────────────────────────────────────────────────────────
    if n_failed > 0:
        lines.append(f"## Failed ({n_failed} {_pluralize('app', n_failed)})")
        lines.append("")
        for cat in _ordered_categories(buckets):
            b = buckets[cat]
            for item in sorted(b.failed, key=_item_sort_key):
                error = _failure_reason(item, b.phase_messages)
                lines.append(f"- **{_display_name(item)}** — {error}")
        lines.append("")
    elif not (problem_lines or warnings or still_pending):
        # Only claim "Failed (0)" when NOTHING in the run failed or is
        # unverified (unknown is not zero).
        lines.append("## Failed (0 apps)")
        lines.append("")
        lines.append("(none)")
        lines.append("")

    # ── Run notes (from run_meta.json) ───────────────────────────────────
    notes = _run_notes(run_meta)
    if notes:
        lines.append("## Run notes")
        lines.append("")
        lines.extend(f"- {n}" for n in notes)
        lines.append("")

    markdown = "\n".join(lines).rstrip() + "\n"

    # Best-effort write — non-fatal on disk failure.
    try:
        report_path = run_dir / REPORT_FILENAME
        report_path.write_text(markdown, encoding="utf-8")
    except OSError:
        _log.exception("could not write %s", run_dir / REPORT_FILENAME)

    return markdown


# ── Internals ────────────────────────────────────────────────────────────────


# ── Updated outside Ascendo (Spec 9, sidecar-only) ───────────────────────────

# Alias groups (macOS_updates run_summary APP_ALIASES): one app, many names.
_APP_ALIAS_GROUPS = (
    ("google chrome", "chrome"), ("microsoft word", "word"),
    ("microsoft excel", "excel"), ("microsoft powerpoint", "powerpoint"),
    ("microsoft outlook", "outlook"), ("microsoft onenote", "onenote"),
    ("zoom.us", "zoom"), ("visual studio code", "vs code", "vscode"),
    ("brave browser", "brave"), ("docker", "docker desktop"),
    ("chatgpt", "codex"), ("comet", "comet (perplexity browser)"),
)


def _app_key(name: str) -> str:
    """normalize_app_key + alias group: lower-case, strip ``.app``/prefix, _-/- -> space."""
    n = name.strip().lower()
    if ":" in n:
        n = n.split(":", 1)[1]
    if n.endswith(".app"):
        n = n[:-4]
    n = " ".join(n.replace("_", " ").replace("-", " ").split())
    for group in _APP_ALIAS_GROUPS:
        norm = {" ".join(g.replace("-", " ").split()) for g in group}
        if n in norm:
            return sorted(norm)[0]
    return n


def _updated_outside(
    check_sidecars: list[Sidecar], apply_sidecars: list[Sidecar],
) -> list[tuple[str, str, str, str]]:
    """(category, display name, from, to) for items check offered that apply found current.

    Skipped when the same app (alias-deduped) was upgraded/triggered by Ascendo
    in this run, so a name is never counted twice.
    """
    offered: dict[tuple[str, str], Item] = {}
    for sc in check_sidecars:
        for it in sc.items:
            if it.status is ItemStatus.PLANNED and it.current_version and it.target_version:
                offered[(sc.category.value, _app_key(it.id))] = it
    ours: set[tuple[str, str]] = set()
    already: dict[tuple[str, str], Item] = {}
    for sc in apply_sidecars:
        for it in sc.items:
            key = (sc.category.value, _app_key(it.id))
            if it.status in (ItemStatus.SUCCESS, ItemStatus.TRIGGERED):
                ours.add(key)
            elif it.status is ItemStatus.UP_TO_DATE:
                already[key] = it
    out: list[tuple[str, str, str, str]] = []
    for key, chk in offered.items():
        if key in already and key not in ours:
            out.append((key[0], _display_name(chk), chk.current_version or "", chk.target_version or ""))
    return sorted(out)


def _classify_item(item: Item, bucket: _CategoryBuckets) -> None:
    """Sort one item into the right bucket on the apply sidecar."""
    status = item.status
    if status is ItemStatus.SUCCESS:
        # An item with current_version != target_version is a real upgrade;
        # current_version == target_version (rare but possible — e.g. npm
        # reinstalling the same version) gets reported as already current.
        if item.current_version and item.target_version and item.current_version == item.target_version:
            bucket.up_to_date.append(item)
        else:
            bucket.upgraded.append(item)
    elif status is ItemStatus.UP_TO_DATE:
        bucket.up_to_date.append(item)
    elif status is ItemStatus.TRIGGERED:
        bucket.triggered.append(item)
    elif status is ItemStatus.SKIPPED:
        bucket.deferred.append(item)
    elif status is ItemStatus.FAILED:
        bucket.failed.append(item)
    elif status is ItemStatus.PARTIAL:
        # Treat partial as failure for reporting clarity — the operator
        # needs to know it didn't fully land.
        bucket.failed.append(item)
    elif status is ItemStatus.PLANNED:
        # Apply-phase planned items only happen on dry-run. Treat as
        # "would have upgraded" — group with deferred so the operator
        # sees them.
        bucket.deferred.append(item)
    elif status is ItemStatus.MISSING:
        bucket.deferred.append(item)


def _display_name(item: Item) -> str:
    """Operator-facing name for one item.

    Strips category prefixes (e.g. ``web:chrome`` → ``Chrome``) when the
    item's id is more readable than its name.
    """
    name = (item.name or item.id or "").strip()
    # If the name is just ``<cat>:<slug>`` strip the prefix and titlecase
    # the slug for readability.
    if ":" in name:
        slug = name.split(":", 1)[1]
        return slug.replace("-", " ").replace("_", " ").title()
    return name


def _item_sort_key(item: Item) -> str:
    return _display_name(item).lower()


def _render_upgrade(item: Item, *, fallback_target: str | None = None) -> str:
    """Render one upgrade row for the ``What changed`` section."""
    name = _display_name(item)
    cur = (item.current_version or "").strip()
    tgt = (item.resolved_version or item.target_version or fallback_target or "").strip()
    if cur and tgt and cur != tgt:
        return f"- **{name}** `{cur}` → `{tgt}`"
    if tgt:
        return f"- **{name}** → `{tgt}`"
    if cur:
        return f"- **{name}** (current `{cur}`)"
    return f"- **{name}**"


def _deferral_reason(
    item: Item, phase_messages: list[Message], code: str | None = None,
) -> str:
    """Pick the best 1-line reason this item was deferred.

    A recognised status ``code`` (rollout_hold, ...) gets its human text; an
    unrecognised or missing one keeps the message-derived reason (the generic
    diagnostic is never replaced by a guess).
    """
    if code in _REASON_TEXT and code not in ("feed_stale",):
        return _REASON_TEXT[code]
    # First, look at item-level messages.
    for msg in item.messages:
        text = (msg.text or "").strip()
        if text:
            return _normalise_reason(text)
    # Then, scan phase-level messages for one that mentions this item.
    candidates = _name_match_candidates(item)
    for msg in phase_messages:
        text = (msg.text or "").strip()
        if not text:
            continue
        haystack = text.lower()
        if any(c in haystack for c in candidates):
            return _normalise_reason(text)
    if item.exit_code:
        return f"skipped (exit {item.exit_code})"
    return "skipped"


def _failure_reason(item: Item, phase_messages: list[Message]) -> str:
    """Pick the best 1-line reason this item failed."""
    # Prefer error-level messages.
    for msg in item.messages:
        if msg.level is MessageLevel.ERROR and msg.text:
            return _normalise_reason(msg.text)
    candidates = _name_match_candidates(item)
    for msg in phase_messages:
        if msg.level is not MessageLevel.ERROR or not msg.text:
            continue
        haystack = msg.text.lower()
        if any(c in haystack for c in candidates):
            return _normalise_reason(msg.text)
    # Fall back to any item message.
    for msg in item.messages:
        if msg.text:
            return _normalise_reason(msg.text)
    if item.exit_code:
        return f"failed (exit {item.exit_code})"
    return "failed"


def _name_match_candidates(item: Item) -> list[str]:
    """Lowercased name fragments to match against phase-level messages.

    Sidecar emitters often key phase messages by the bare slug (``chrome:``)
    while item.name carries the category prefix (``web:chrome``). Generate
    both forms so either matches.
    """
    out: set[str] = set()
    for raw in (item.name, item.id):
        if not raw:
            continue
        s = raw.lower().strip()
        if not s:
            continue
        out.add(s)
        if ":" in s:
            slug = s.split(":", 1)[1].strip()
            if slug:
                out.add(slug)
    return [c for c in out if c]


def _normalise_reason(text: str) -> str:
    """Trim a sidecar message into a one-line, human-friendly reason."""
    # Take the first non-empty line.
    line = next((ln.strip() for ln in text.splitlines() if ln.strip()), text.strip())
    # The machine token is for the Action-required parser, not for humans.
    line = re.sub(r"\s*\|?\s*" + ACTION_RE.pattern, "", line).strip() or line
    line = _STATUS_TOKEN_RE.sub("", line).strip() or line
    low_line = line.lower()
    if "sudo_unavailable" in low_line:
        return _REASON_TEXT["sudo_unavailable"]
    low = line.lower()
    if "unknown arg:" in low:
        return "internal argument error (please report)"
    if "no sidecar" in low:
        return "script crashed before reporting"
    # Strip our own ``<id>:`` prefix when the message is keyed by item id.
    if ":" in line:
        head, rest = line.split(":", 1)
        if " " not in head and rest.strip():
            line = rest.strip()
    # Friendly substitutions for the most common deferral codes.
    replacements = {
        "deferred_app_in_use": "was running during the update — re-run apply to upgrade it",
        "deferred_running": "was running during the update — re-run apply to upgrade it",
        "skipped_running": "was running during the update — re-run apply to upgrade it",
    }
    if line in replacements:
        return replacements[line]
    # Heuristic shortening for long parenthetical reasons. We surface
    # the head of the message (before the " (...)" parenthesis) so the
    # report stays readable.
    #
    #   Before: "deferred_app_in_use (quit Notion and re-run apply to upgrade 1.2 → 1.3)"
    #   After:  "was running during the update — quit it and re-run apply to upgrade 1.2 → 1.3"
    #
    #   Before: "not_installed (bundle 'com.x.y' not found on disk; registry expected '/Applications/Foo.app' — remove from registry if you don't use this app)"
    #   After:  "not installed on this machine — remove `Foo` from your registry if you don't use it"
    if line.startswith("deferred_app_in_use") and "(" in line:
        tail = line.split("(", 1)[1].rstrip(")").strip()
        # "quit Foo and re-run apply to upgrade 1 → 2" → keep useful tail
        if tail.lower().startswith("quit "):
            return f"was running during the update — {tail}"
        return f"was running during the update — {tail}"
    if line.startswith("not_installed"):
        # Extract the app name from "registry expected '/Applications/Foo.app'"
        m = re.search(r"/([^/]+)\.app", line)
        if m:
            return f"not installed on this machine — remove `{m.group(1)}` from your registry if you don't use it"
        return "not installed on this machine"
    # Pip→brew handoff messages. The pip apply phase deliberately skips
    # packages that brew also owns (uv, pip, setuptools, wheel) so we
    # don't double-manage them — but the raw message is verbose and
    # looks like a failure. Surface a cleaner one-liner.
    #
    # Original message: "skipped pip upgrade of 'uv' — brew also owns
    # this formula; upgrade flows through brew (preserves /opt/homebrew/bin/uv)"
    if "brew also owns" in line.lower() or "managed by brew" in line.lower():
        m = re.search(r"'([^']+)'", line)
        name = m.group(1) if m else "package"
        return f"managed by Homebrew (run `brew upgrade {name}` to bump)"
    if "no record" in line.lower() and ("brew" in line.lower() or "self-upgrade" in line.lower()):
        return "managed by Homebrew (run `brew upgrade python` to bump pip)"
    return line


# ── Honesty helpers (Spec 1) ─────────────────────────────────────────────────

_REASON_MAX = 240


@dataclass(frozen=True)
class _Problem:
    """A failed/partial phase sidecar that must be surfaced by name."""

    category: str
    phase: str
    reason: str


def _first_line(text: str, limit: int = _REASON_MAX) -> str:
    line = next((ln.strip() for ln in text.splitlines() if ln.strip()), text.strip())
    if len(line) > limit:
        line = line[: limit - 1].rstrip() + "…"
    return line


def _sidecar_reason(sc: Sidecar) -> str:
    """One human line explaining why this sidecar is failed/partial."""
    msgs = [m for m in sc.messages if (m.text or "").strip()]
    chosen: Message | None = None
    for level in (MessageLevel.ERROR, MessageLevel.WARN):
        chosen = next((m for m in msgs if m.level is level), None)
        if chosen is not None:
            break
    if chosen is None and msgs:
        chosen = msgs[0]
    bad = [
        it for it in sc.items
        if it.status in (ItemStatus.FAILED, ItemStatus.PARTIAL)
    ]
    parts: list[str] = []
    if chosen is not None:
        parts.append(_normalise_reason(_first_line(chosen.text)))
    if bad:
        names = ", ".join(_display_name(it) for it in bad[:5])
        more = f" (+{len(bad) - 5} more)" if len(bad) > 5 else ""
        parts.append(f"{len(bad)} {_pluralize('item', len(bad))} failed: {names}{more}")
    if not parts:
        parts.append(f"reported status {sc.status.value}")
    return _first_line("; ".join(parts), _REASON_MAX * 2)


def _collect_problems(
    sidecars: list[Sidecar],
) -> tuple[list[_Problem], list[_Problem]]:
    """``(problems, warnings)`` from EVERY phase sidecar of the run.

    A sidecar whose failure is already itemised elsewhere is not listed
    twice: failed *apply* items live under ``## Failed`` and failed
    *verify* items under ``## Still pending``. Manager-level failures
    (no items / crashed script) and check/plan failures are always listed.
    Cleanup failures are warnings, not headline failures.
    """
    problems: list[_Problem] = []
    warnings: list[_Problem] = []
    bad_statuses = (ItemStatus.FAILED, ItemStatus.PARTIAL)
    for sc in sidecars:
        if sc.status not in (PhaseStatus.FAILED, PhaseStatus.PARTIAL):
            continue
        has_bad_items = any(it.status in bad_statuses for it in sc.items)
        if sc.phase is Phase.APPLY and has_bad_items:
            continue
        if sc.phase is Phase.VERIFY and (
            has_bad_items
            or any(it.status is ItemStatus.PLANNED for it in sc.items)
        ):
            continue
        entry = _Problem(sc.category.value, sc.phase.value, _sidecar_reason(sc))
        (warnings if sc.phase is Phase.CLEANUP else problems).append(entry)
    key = lambda p: (_category_sort_key(p.category), p.phase)  # noqa: E731
    return sorted(problems, key=key), sorted(warnings, key=key)


def _collect_still_pending(
    verify_sidecars: list[Sidecar],
) -> list[tuple[str, Item]]:
    """Verify items that are not confirmed at the target version."""
    out: list[tuple[str, Item]] = []
    for sc in verify_sidecars:
        for it in sc.items:
            if it.status in (
                ItemStatus.FAILED, ItemStatus.PLANNED, ItemStatus.PARTIAL,
            ):
                out.append((sc.category.value, it))
    out.sort(key=lambda t: (_category_sort_key(t[0]), _item_sort_key(t[1])))
    return out


def _unverified_categories(
    apply_sidecars: list[Sidecar],
    verify_sidecars: list[Sidecar],
    run_meta: dict,
) -> list[str]:
    """Categories that changed state in apply but whose result is unknown.

    Unknown = the verify sidecar is missing (the run ended before verify:
    verify ran for other categories, or the run was aborted/cancelled) or
    is a manager-level failure with no items. A run that simply did not
    request the verify phase has nothing to report here.
    """
    verify_by_cat = {sc.category.value: sc for sc in verify_sidecars}
    run_cut_short = bool(
        verify_sidecars
        or run_meta.get("aborted_after_phase")
        or run_meta.get("cancelled")
    )
    out: list[str] = []
    for sc in apply_sidecars:
        cat = sc.category.value
        if not any(
            it.status in (ItemStatus.SUCCESS, ItemStatus.PARTIAL) for it in sc.items
        ):
            continue
        v = verify_by_cat.get(cat)
        if v is None:
            if run_cut_short:
                out.append(cat)
        elif v.status is PhaseStatus.FAILED and not v.items:
            out.append(cat)
    return sorted(set(out), key=_category_sort_key)


def _read_run_meta(run_dir: Path) -> dict:
    """``<run_dir>/run_meta.json`` written by the orchestrator (or ``{}``)."""
    try:
        data = json.loads((run_dir / "run_meta.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _run_meta_problems(run_meta: dict) -> list[str]:
    out: list[str] = []
    phase = run_meta.get("aborted_after_phase")
    if run_meta.get("cancelled"):
        out.append(
            "**Run cancelled** — it was stopped before it finished"
            + (f" (at phase {phase})" if phase else "")
            + "; later steps did not run."
        )
    elif phase:
        out.append(
            f"**Run stopped after phase {phase}** — every step of that phase "
            "failed, so the later phases did not run."
        )
    return out


def _run_notes(run_meta: dict) -> list[str]:
    out: list[str] = []
    for entry in run_meta.get("skipped_managers") or []:
        try:
            cat, reason = entry[0], entry[1]
        except (IndexError, TypeError):
            continue
        # "not in categories filter" is the user's own selection, not news.
        if reason == "not in categories filter":
            continue
        out.append(f"Skipped: {_category_label(str(cat))} ({reason})")
    return out


def _ordered_categories(buckets: dict[str, _CategoryBuckets]) -> list[str]:
    return sorted(buckets.keys(), key=_category_sort_key)


def _ordered_categories_for_counts(
    check_counts: dict[str, int], buckets: dict[str, _CategoryBuckets],
) -> list[str]:
    cats = set(check_counts) | set(buckets)
    return sorted(cats, key=_category_sort_key)


def _format_duration(start: datetime | None, end: datetime | None) -> str:
    if start is None or end is None or end < start:
        return "unknown"
    seconds = int((end - start).total_seconds())
    if seconds < 60:
        return f"{seconds} {_pluralize('second', seconds)}"
    minutes, secs = divmod(seconds, 60)
    if minutes < 60:
        if secs == 0:
            return f"{minutes} {_pluralize('minute', minutes)}"
        return f"{minutes} {_pluralize('minute', minutes)} {secs} {_pluralize('second', secs)}"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} {_pluralize('hour', hours)} {minutes} {_pluralize('minute', minutes)}"


def _pluralize(word: str, n: int) -> str:
    return word if n == 1 else word + "s"


__all__ = [
    "REPORT_FILENAME",
    "generate_apply_report",
]
