"""Human-readable post-apply report generator.

After every run that included an ``apply`` phase, the orchestrator writes
``<runs_dir>/<run_id>/REPORT.md`` — a short, plain-English summary of what
actually happened. The operator should be able to read it without knowing
about sidecars, handlers, or any internal jargon.

The report has six sections, in order:

1. Header — run id (short), timestamp, host, profile, duration, result
2. Reboot banner (only if any sidecar set ``needs_reboot``)
3. ``What changed`` — apps that were upgraded, grouped by category, with
   ``from → to`` versions
4. ``Already up-to-date`` — counts per category (no per-app list — would
   bloat the report on a typical 200-app run)
5. ``Trigger-only updates`` — Tier-B vendor agents (Keystone, Squirrel,
   builtin) that queued an async update
6. ``Deferred`` — apps that were skipped because they were running, or
   for some other recoverable reason
7. ``Failed`` — apps whose apply errored, with the error message captured
   from the sidecar

The generator is best-effort: any sidecar that fails to parse is silently
skipped, and the function never raises (a failed report should not abort
the run).

This module is intentionally pure-stdlib + Pydantic — no jinja2, no
templating engine. The markdown is small enough to build with f-strings.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from ..models.host import HostInfo
from ..models.result import Item, ItemStatus, Message, MessageLevel
from ..models.run import Phase
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

    # Overall result.
    if n_failed > 0 and n_upgraded > 0:
        result_label = "Mostly succeeded — some apps failed."
    elif n_failed > 0:
        result_label = "Some updates failed."
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
    if headline_bits:
        lines.append("**At a glance:** " + ", ".join(headline_bits) + ".")
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
    if n_deferred > 0:
        lines.append(f"## Deferred ({n_deferred} {_pluralize('app', n_deferred)})")
        lines.append("")
        for cat in _ordered_categories(buckets):
            b = buckets[cat]
            for item in sorted(b.deferred, key=_item_sort_key):
                reason = _deferral_reason(item, b.phase_messages)
                lines.append(f"- **{_display_name(item)}** — {reason}")
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
    else:
        # Always close on a confidence note; no "(none)" cluttering.
        lines.append("## Failed (0 apps)")
        lines.append("")
        lines.append("(none)")
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


def _deferral_reason(item: Item, phase_messages: list[Message]) -> str:
    """Pick the best 1-line reason this item was deferred."""
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
        import re
        m = re.search(r"/([^/]+)\.app", line)
        if m:
            return f"not installed on this machine — remove `{m.group(1)}` from your registry if you don't use it"
        return "not installed on this machine"
    return line


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
