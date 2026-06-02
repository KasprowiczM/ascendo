"""Async wrapper around :func:`run_phases` with a per-run state registry.

The synchronous ``run_phases`` is fine for short read-only phases (check,
plan, verify, cleanup). The ``apply`` phase, however, can take 10+ minutes
when many packages need upgrading -- blocking an HTTP request for that long
is unacceptable.

This module provides:

- :class:`RunState` -- a single-run lifecycle record (running/completed/failed
  + timestamps + report-when-done).
- :class:`RunRegistry` -- in-memory map ``{run_id: RunState}``. Lives on
  ``app.state.run_registry``. Bounded LRU eviction keeps the map small for
  long-running dashboards.
- :func:`start_run_async` -- kicks off ``run_phases`` in a worker thread,
  returns the ``run_id`` immediately. Subsequent calls to
  ``RunRegistry.get(run_id)`` reflect progress.

**Event emission for SSE.** Sidecars are persisted to disk by
``run_phases`` as each phase completes. The SSE endpoint polls the run
directory + the registry to detect new artefacts; this keeps the
orchestrator core unchanged (no callback/event-bus refactor needed).

**Concurrency model.** One worker thread per run. ``asyncio.to_thread``
keeps the FastAPI event loop responsive while ``run_phases`` is mostly
blocked on subprocess I/O.

**Failure semantics.** A crash inside the worker thread (NOT a sidecar
failure -- those are recorded normally) sets ``state=failed`` with the
exception captured in ``state.error``. The orchestrator's
``_safe_run_phase`` already catches ``ManagerError`` so worker-thread
failures are rare (typically OSError on disk full).
"""
from __future__ import annotations

import asyncio
import logging
import os
import threading
from collections import OrderedDict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import UUID

from ..models.run import Phase, PhaseStatus, RunInfo
from .runner import RunReport, run_phases

# Convention: per-run streaming log, written to by bash apply scripts via
# `_stream_tee` (see adapters/<os>/scripts/<source>/apply.sh). The SSE
# endpoint tails this file and emits ``log_line`` + ``progress`` events
# so the Run Center can show every line of stdout/stderr in real time.
# Re-exported from .stream_log (single source of truth) for back-compat —
# callers that did ``from .run_async import STREAM_LOG_*`` keep working.
from .stream_log import (  # noqa: E402,F401
    STREAM_LOG_ENV_VAR,
    STREAM_LOG_FILENAME,
    stream_log_context,
)

if TYPE_CHECKING:
    from ..interfaces.adapter import IAdapter
    from ..models.host import HostInfo
    from ..models.package import SourceType

_log = logging.getLogger(__name__)


class RunStatus(str, Enum):
    """Lifecycle states for an async run."""

    PENDING = "pending"     # registered, worker not yet started
    RUNNING = "running"     # worker thread is in run_phases
    COMPLETED = "completed"  # run_phases returned a report
    FAILED = "failed"       # worker thread raised (rare -- most failures are inside report.sidecars)
    CANCELLED = "cancelled"  # E11: cooperative cancel via should_cancel fired


@dataclass
class RunState:
    """In-memory record of one async run. Mutated by the worker thread."""

    run_id: UUID
    status: RunStatus = RunStatus.PENDING
    started_at: datetime | None = None
    finished_at: datetime | None = None
    report: RunReport | None = None
    error: str | None = None
    base_dir: Path | None = None
    # Categories + phases this run requested. Used by RunRegistry.conflicts
    # to refuse a concurrent /runs/async on the same (category, apply) pair.
    # Stored as lowercase strings so the conflict check doesn't care whether
    # the caller passed SourceType enums or raw strings.
    categories: tuple[str, ...] = ()
    phases: tuple[str, ...] = ()
    # Internal coordination -- used by the SSE endpoint to wait efficiently.
    _completion_event: threading.Event = field(default_factory=threading.Event, repr=False)
    # Cooperative cancel. /runs/active/stop sets this; run_phases checks it
    # at phase/manager boundaries and aborts the rest of the run.
    cancel_event: threading.Event = field(default_factory=threading.Event, repr=False)
    # Stream-log race fix: each run carries its own log path so concurrent
    # workers don't clobber each other's os.environ. The worker still sets
    # os.environ for subprocess inheritance, but saves/restores using this
    # per-run value instead of relying on a single global.
    stream_log_path: Path | None = None

    # ── Phase 2.2 lifecycle sub-states ──────────────────────────────────────
    # Fine-grained lifecycle the SSE `status` event exposes as a `state` field.
    # "preparing"  — worker has started but run_phases not yet called
    # "running"    — run_phases is executing (implicit while status=RUNNING)
    # "finalizing" — run_phases returned; post-run bookkeeping in progress
    # None         — pending (not yet started)
    lifecycle: str | None = None

    # Refined terminal descriptor. One of:
    #   "completed"               — all sidecars clean (no failures/skips/warnings)
    #   "completed_with_warnings" — done but had skipped/partial/reboot/warn messages
    #   "failed"                  — worker raised (catastrophic) or all sidecars failed
    #   "cancelled"               — cooperative cancel fired
    # None while the run is in progress.
    terminal_state: str | None = None

    # ── Phase 2.3 per-phase tracking (set by on_phase callback in _worker) ─
    # Current phase being executed (Phase.value string) or None.
    current_phase: str | None = None
    # 0-based index of the current phase within the ordered phase list.
    phase_index: int = 0
    # Total number of phases in this run's ordered plan.
    phase_total: int = 0


class RunRegistry:
    """Thread-safe registry of in-flight + recently-completed runs.

    Bounded by ``max_runs`` to avoid unbounded memory growth on long-lived
    dashboards. When the cap is hit, the oldest *completed* runs are
    evicted; running runs are never evicted.
    """

    def __init__(self, *, max_runs: int = 256) -> None:
        self._lock = threading.Lock()
        self._states: OrderedDict[UUID, RunState] = OrderedDict()
        self._max_runs = max_runs

    def register(
        self,
        run_id: UUID,
        *,
        base_dir: Path,
        categories: Iterable[str] | None = None,
        phases: Iterable[str] | None = None,
    ) -> RunState:
        with self._lock:
            state = RunState(
                run_id=run_id,
                base_dir=base_dir,
                categories=tuple(c.lower() for c in (categories or ())),
                phases=tuple(p.lower() for p in (phases or ())),
            )
            self._states[run_id] = state
            self._evict_if_needed_locked()
            return state

    def conflicting_apply(
        self,
        categories: Iterable[str],
        phases: Iterable[str],
    ) -> RunState | None:
        """Return any currently-running RunState whose categories overlap
        AND whose phases include 'apply'. Used by /runs/async to refuse a
        concurrent destructive run on the same source -- winget/brew/etc.
        own their own per-machine lock at the OS level, but firing two
        applies simultaneously still produces user-visible breakage
        (sidecars race, InventoryDB cleared mid-flight, etc.).

        Read-only runs (check/plan/verify) are allowed to overlap; we only
        gate when 'apply' is in the new phases or any running run's phases.
        """
        wanted_cats = {c.lower() for c in (categories or ())}
        wanted_phases = {p.lower() for p in (phases or ())}
        # The destructive flag is on EITHER side: a new check while an
        # apply is running is fine; a new apply while a check is running
        # is also fine (the apply just queues behind the OS lock). We only
        # refuse when BOTH sides include apply on the same category.
        new_is_apply = "apply" in wanted_phases
        if not new_is_apply:
            return None
        with self._lock:
            for st in self._states.values():
                if st.status not in (RunStatus.PENDING, RunStatus.RUNNING):
                    continue
                if "apply" not in st.phases:
                    continue
                if wanted_cats & set(st.categories):
                    return st
                # Wildcard (empty categories = all): collide with anything.
                if not wanted_cats or not st.categories:
                    return st
        return None

    def get(self, run_id: UUID) -> RunState | None:
        with self._lock:
            return self._states.get(run_id)

    def request_cancel(self, run_id: UUID) -> bool:
        """Signal cooperative cancellation for an in-flight run.

        Returns True if a pending/running run was found and its cancel
        flag set; False if there is no such run (already finished /
        unknown id). The worker's ``run_phases`` checks the flag at the
        next phase/manager boundary and stops the rest of the run.
        """
        with self._lock:
            st = self._states.get(run_id)
            if st is None or st.status not in (
                RunStatus.PENDING,
                RunStatus.RUNNING,
            ):
                return False
            st.cancel_event.set()
            return True

    def all_running(self) -> list[UUID]:
        """Snapshot of currently-running run-ids."""
        with self._lock:
            return [
                rid for rid, st in self._states.items()
                if st.status in (RunStatus.PENDING, RunStatus.RUNNING)
            ]

    def _evict_if_needed_locked(self) -> None:
        # Caller already holds self._lock.
        if len(self._states) <= self._max_runs:
            return
        # Evict oldest completed/failed runs first.
        for rid in list(self._states.keys()):
            if len(self._states) <= self._max_runs:
                break
            st = self._states[rid]
            if st.status in (RunStatus.COMPLETED, RunStatus.FAILED):
                del self._states[rid]


# ── Public entry point ──────────────────────────────────────────────────────


# Phase priority for post-run inventory flush. Higher = more authoritative.
# When the same (category, name) appears in multiple sidecars in a single
# run, the row from the highest-priority phase wins. Order rationale:
#   verify (5)  -- re-scans the post-apply state; reflects current reality
#   apply  (4)  -- just touched the package; resolved_version is the truth
#   check  (3)  -- read-only snapshot taken BEFORE apply
#   plan   (2)  -- subset of check (only items that would change)
#   cleanup(1)  -- emits infra/no-op items, not real packages
#
# Bug fixed 2026-05-13: previously sidecars were processed in filename-
# sorted order (apply, check, cleanup, plan, verify), causing the older
# check row's status='planned' to overwrite the apply row's status=
# 'success' -- operator on DP5520WMK saw inventory still listing
# @anthropic-ai/claude-code as "planned 2.1.139 -> 2.1.140" even after
# the apply phase upgraded it to 2.1.140.
_PHASE_PRIORITY: dict[str, int] = {
    "verify": 5,
    "apply": 4,
    "check": 3,
    "plan": 2,
    "cleanup": 1,
}

# Apply / verify emit status strings from a different taxonomy than check.
# Normalise so the inventory_db has a single consistent vocabulary that
# the SPA can paint without per-phase status logic.
_INVENTORY_STATUS_MAP: dict[str, str] = {
    # check/plan vocabulary -- passes through unchanged
    "up_to_date":  "up_to_date",
    "planned":     "planned",
    "missing":     "missing",
    "skipped":     "skipped",
    # apply/verify vocabulary -- HONEST mapping (Phase 0 hardening)
    # Previously: failed→outdated, triggered→up_to_date hid real
    # failures behind green pills. Now: failed stays failed,
    # triggered shows as pending reconciliation.
    "success":     "up_to_date",      # apply succeeded => package is at target
    "failed":      "failed",          # apply failed — operator must see the failure
    "partial":     "failed",          # partial applies are failures too
    "triggered":   "triggered_pending",  # vendor daemon kicked; awaiting reconciliation
}


def _phase_of(sc: Any) -> str:
    """Extract the phase string ('check'/'apply'/...) from a Sidecar."""
    phase = getattr(sc, "phase", None)
    if phase is None:
        return ""
    return phase.value if hasattr(phase, "value") else str(phase)


def _normalize_item_id(item_id_raw: Any, name: str) -> str:
    """Decide whether an ``id`` field is a real disambiguator or merely
    a synthetic prefix of the display name.

    Sesja 67 widened the inventory PK to ``(category, name, item_id)``
    so Windows multi-arch packages (Microsoft VC++ 2008 Redistributable
    × {x86, x64, arm64}) don't collapse. But Ubuntu phase scripts emit
    synthetic ids like ``brew:wget`` / ``apt:upgrade:firefox`` that
    just category-prefix the name — those don't disambiguate, they're
    a namespacing convention. Returning an empty string lets the
    legacy ``(cat, name, "")`` row upsert in place.

    Heuristic (D3/T7 refined):
      - id == name                                → trivially same → ""
      - id ends with ``<sep><name>`` where sep is ``:`` / ``/``
        → always collapse (synthetic prefix convention)
      - id ends with ``<sep><name>`` where sep is ``-`` or ``.``
        → collapse ONLY when the prefix is a known source-category
        slug. This prevents collapsing real dotted hierarchies like
        ``Microsoft.VCRedist.2008.x64.Runtime`` where name=``Runtime``.
    """
    if not item_id_raw or not name:
        return ""
    raw = str(item_id_raw)
    lower_raw = raw.lower()
    lower_name = name.lower()
    if lower_raw == lower_name:
        return ""
    # Colon and slash are unambiguous synthetic-prefix separators.
    # Ubuntu/macOS scripts always use these (brew:wget, apt:upgrade:firefox).
    for sep in (":", "/"):
        if lower_raw.endswith(sep + lower_name):
            return ""
    # Dot and hyphen are ambiguous: "snap-firefox" is synthetic, but
    # "Microsoft.VCRedist.2008.x64.Runtime" is a real dotted hierarchy.
    # Only collapse when the prefix before sep+name is a known source
    # category slug.
    _KNOWN_PREFIXES = frozenset({
        "apt", "brew", "snap", "npm", "pip", "web", "flatpak",
        "winget", "msstore", "drivers", "firmware", "registry_arp",
        "plugin", "mas", "windows_update",
    })
    for sep in ("-", "."):
        suffix = sep + lower_name
        if lower_raw.endswith(suffix):
            prefix = lower_raw[: -len(suffix)]
            # Multi-segment prefix: check the LAST segment too
            # (e.g. "apt:upgrade" → last segment is "upgrade", not known)
            # but the FIRST segment should be a known category.
            first_segment = prefix.split(":")[0].split("/")[0].split(".")[0]
            if first_segment in _KNOWN_PREFIXES:
                return ""
    return raw



def _flush_run_to_inventory_db(run_dir: Path, inventory_db: Any) -> int:
    """Walk the just-finished run's sidecars + bulk-upsert their items.

    Resolves duplicates across phases by phase priority: verify > apply >
    check > plan > cleanup. For apply success items the *installed*
    column is taken from ``resolved_version`` (the post-apply state),
    not ``current_version`` (the pre-apply state). Apply/verify status
    strings are normalised to the check vocabulary so the SPA paints
    consistently regardless of which phase last touched the row.

    Cleanup-phase rows that carry no version info (infrastructure
    actions like ``winget.source-reset``) are filtered out so they
    don't pollute the Apps/Categories view.

    Failures are swallowed (best-effort): a flaky disk should not poison
    the run state. Returns the count of rows flushed (0 on failure or
    when no sidecars exist yet).
    """
    if inventory_db is None or not run_dir.is_dir():
        return 0
    try:
        from .sidecar_io import list_run_sidecars, read_sidecar
    except Exception:  # noqa: BLE001
        return 0

    # Key by (category, name); value carries the row + the priority of
    # the phase that produced it. Higher-priority phases overwrite.
    best: dict[tuple[str, str], tuple[int, dict[str, Any]]] = {}
    # Keys whose underlying bundle is gone — registry entries left over
    # after the user uninstalled the .app. Apply phase emits these as
    # ``skipped`` + zero versions; we delete them from inventory.db so
    # the SPA doesn't keep showing "Cursor (not installed)" forever
    # (Sesja 73).
    uninstalled: set[tuple[str, str, str]] = set()

    for sidecar_path in list_run_sidecars(run_dir):
        try:
            sc = read_sidecar(sidecar_path)
        except Exception:  # noqa: BLE001 -- corrupt sidecars shouldn't block the flush
            continue
        category = (
            sc.category.value if hasattr(sc.category, "value") else str(sc.category)
        )
        phase = _phase_of(sc)
        priority = _PHASE_PRIORITY.get(phase, 0)
        # E8: warn when a sidecar carries an unrecognized phase so the
        # operator knows something unexpected was processed at lowest
        # priority. Don't skip — data is still valuable.
        if phase and phase not in _PHASE_PRIORITY:
            _log.warning(
                "inventory flush: sidecar %s has unrecognized phase %r "
                "(processed at priority 0)",
                sidecar_path.name,
                phase,
            )

        for item in (sc.items or []):
            item_id_raw = getattr(item, "id", None)
            name = getattr(item, "name", None) or item_id_raw
            if not name:
                continue

            current = getattr(item, "current_version", None)
            target = getattr(item, "target_version", None)
            resolved = getattr(item, "resolved_version", None)

            status_obj = getattr(item, "status", None)
            raw_status = (
                status_obj.value if hasattr(status_obj, "value") else str(status_obj)
            ) if status_obj else "unknown"

            # Apply success: installed version is now `resolved_version`
            # (the version we just landed), not the stale `current_version`
            # reading from before the install.
            if phase in ("apply", "verify") and raw_status == "success" and resolved:
                installed = resolved
            else:
                installed = current or resolved

            candidate = target

            # Filter cleanup infra rows: no version info AND came from
            # cleanup phase. Real apps almost always have at least one
            # version present in at least one phase's sidecar.
            if phase == "cleanup" and not installed and not candidate:
                continue

            status = _INVENTORY_STATUS_MAP.get(raw_status, raw_status)

            # Sesja 67: include item_id so packages sharing a DisplayName
            # (different MSIX architectures, VC++ side-by-side installs,
            # ARP entries with identical Name but distinct registry GUIDs)
            # land in separate inventory_items rows instead of silently
            # collapsing. Empty when name == id, falls back to ''.
            #
            # Edge: Ubuntu sidecars emit synthetic ids like
            # ``brew:wget`` or ``apt:upgrade:firefox`` that category-prefix
            # the name — those don't disambiguate two packages, they're
            # just a namespacing convention. Treat id-that-merely-ends-
            # with-name as "no real discriminator" so post-run flush
            # upserts the existing legacy ``(cat, name, "")`` row
            # instead of creating a phantom sibling.
            item_id = _normalize_item_id(item_id_raw, str(name))

            key = (category, str(name), item_id)

            # Uninstalled-from-disk detector: apply phase emits a
            # ``skipped`` row with no version info when the registry
            # entry's bundle is no longer present on disk (Cursor /
            # Opera / Notion / etc. after the user trashes the .app).
            # Drop it from the candidate set AND remember the key so
            # we evict any existing row from inventory.db after the
            # upsert pass. Restricted to the apply phase so that a
            # cleanup-phase no-op for the same key doesn't accidentally
            # delete a legitimate row.
            if (
                phase == "apply"
                and raw_status == "skipped"
                and not installed
                and not candidate
            ):
                uninstalled.add(key)
                # Don't emit this row — let the delete pass clean it up.
                # Pop any earlier (lower-priority) emission of the same
                # key so a stale check__web.json "up_to_date" doesn't
                # win and re-insert the uninstalled app.
                best.pop(key, None)
                continue

            row = {
                "category": category,
                "name": str(name),
                "item_id": item_id,
                "installed": installed,
                "candidate": candidate,
                "status": status,
                "source_type": category,
                "vendor": getattr(item, "vendor", None),
            }
            existing = best.get(key)
            if existing is None or priority >= existing[0]:
                best[key] = (priority, row)

    rows = [r for _, r in best.values()]
    if not rows:
        return 0
    # IMPORTANT: post-run flush is UPSERT-ONLY. The previous implementation
    # called clear_category() before bulk_upsert, but that destroyed live-scan
    # rows that the run's sidecars don't enumerate. Concrete failure:
    #   1. User clicks "Build inventory" → /inventory live-scan classifies
    #      316 apps in /Applications as web (everything not brew/mas/Apple).
    #   2. User runs Quick check → check__web.json has 37 items (only the
    #      registry-driven + auto-classified Tier-A/B apps with vendor
    #      probes).
    #   3. Post-run flush: clear_category("web") nukes 316 rows, then
    #      bulk_upsert writes 37 -- losing 279 visible apps.
    #
    # Apply / verify sidecars are even smaller subsets (only items that
    # got touched), so clearing on those is catastrophic too.
    #
    # The full live-scan paths (POST /inventory/db/refresh, /inventory
    # cold start) still do clear+write per category -- they're the only
    # source authoritative for the FULL set. Post-run sidecars only carry
    # the items each phase saw; we upsert those and leave the rest alone.
    try:
        flushed = inventory_db.bulk_upsert(rows)
    except Exception:  # noqa: BLE001
        _log.exception("inventory_db: post-run flush failed")
        return 0

    # Sesja 73: evict registry entries whose underlying bundle was
    # uninstalled between runs. Apply emits these as skipped+no-versions;
    # without an explicit delete they linger in inventory.db forever and
    # the SPA keeps painting "Cursor (not installed)" on every page load.
    if uninstalled and hasattr(inventory_db, "delete_row"):
        for cat, name, item_id in uninstalled:
            try:
                inventory_db.delete_row(cat, name, item_id)
            except Exception:  # noqa: BLE001
                _log.exception(
                    "inventory_db: failed to evict uninstalled row "
                    "(%s, %s, %s)",
                    cat,
                    name,
                    item_id,
                )

    return flushed


def _report_has_warnings(report: RunReport) -> bool:
    """True if any sidecar carries a soft-warning signal.

    Signals: skipped / partial / triggered items, a needs-reboot flag, or a
    warn/error-level message. Distinguishes a clean ``completed`` run from
    ``completed_with_warnings`` (Phase 2.2). ``planned`` is NOT a warning —
    "N updates available" is normal check output, not a run problem.
    """
    for sc in report.sidecars:
        if sc.needs_reboot:
            return True
        sm = sc.summary
        if sm.skipped or sm.partial or sm.triggered:
            return True
        for msg in (sc.messages or []):
            # MessageLevel is a str-Enum, so comparing to the literal works.
            if msg.level in ("warn", "error"):
                return True
    return False


def _terminal_from_report(report: RunReport | None) -> str:
    """Refine a finished run into a UI-facing terminal descriptor.

    Returns one of ``completed`` / ``completed_with_warnings`` / ``failed``.
    The ``RunStatus`` enum the registry's conflict logic depends on stays
    unchanged (still COMPLETED); this is the honest descriptor the SSE
    ``done`` event + ``/runs/{id}/status`` expose so the dashboard can paint
    a green / amber / red verdict.
    """
    if report is None:
        return "failed"
    overall = report.overall_status
    if overall == PhaseStatus.FAILED:
        return "failed"
    if overall == PhaseStatus.PARTIAL or _report_has_warnings(report):
        return "completed_with_warnings"
    return "completed"


async def start_run_async(
    *,
    registry: RunRegistry,
    adapter: IAdapter,
    run: RunInfo,
    host: HostInfo,
    base_dir: Path,
    phases: Sequence[Phase] | None = None,
    categories: Iterable[SourceType] | None = None,
    stop_on_failure: bool = True,
    item_filter: Iterable[str] | None = None,
    inventory_db: Any = None,
) -> RunState:
    """Register a run + spawn a worker thread to execute it.

    Returns the freshly-created :class:`RunState` (status=PENDING). The
    caller can poll ``registry.get(run_id)`` or subscribe to events via
    the SSE endpoint to track progress.

    The worker thread runs :func:`run_phases` and updates the state
    transitionally:

    1. ``PENDING`` → registry has the run but worker not yet started
    2. ``RUNNING`` → worker thread entered run_phases
    3. ``COMPLETED`` → run_phases returned; state.report set
    4. ``FAILED`` → worker raised; state.error set

    Always returns synchronously -- does not block on the worker.
    """
    # Stringify categories + phases for the registry conflict check.
    # SourceType / Phase are enums; their `.value` is the canonical string.
    cat_strs = [
        c.value if hasattr(c, "value") else str(c)
        for c in (categories or ())
    ]
    phase_strs = [
        p.value if hasattr(p, "value") else str(p)
        for p in (phases or ())
    ]
    state = registry.register(
        run.id,
        base_dir=base_dir,
        categories=cat_strs,
        phases=phase_strs,
    )

    # Pre-create the per-run dir + an empty stream log so the SSE endpoint
    # can start tailing immediately, even before the first manager boots.
    run_dir = base_dir / str(run.id)
    run_dir.mkdir(parents=True, exist_ok=True)
    stream_log_path = run_dir / STREAM_LOG_FILENAME
    try:
        stream_log_path.touch(exist_ok=True)
    except OSError:
        # If we can't touch it (read-only disk?), the SSE endpoint just
        # won't see log_line events -- non-fatal.
        pass

    def _worker() -> None:
        state.status = RunStatus.RUNNING
        state.lifecycle = "preparing"
        state.started_at = datetime.now(timezone.utc)
        # Stream-log race fix (audit P2): bind the per-run path to THIS
        # worker thread via a thread-local instead of mutating the
        # process-global os.environ. Managers run synchronously in this
        # thread and read it via child_env_with_stream_log() when building
        # the child env they pass to Popen(env=...), so two concurrent runs
        # can never inherit each other's log path.
        state.stream_log_path = stream_log_path
        try:
            from .runner import DEFAULT_PHASE_ORDER  # noqa: PLC0415

            def _on_phase(name: str, index: int, total: int) -> None:
                # Phase 2.3: record the live phase on the shared RunState so
                # GET /runs/{id}/status reflects current progress.
                state.current_phase = name
                state.phase_index = index
                state.phase_total = total

            state.lifecycle = "running"
            with stream_log_context(stream_log_path):
                report = run_phases(
                    adapter,
                    run,
                    host,
                    phases=phases or DEFAULT_PHASE_ORDER,
                    categories=categories,
                    base_dir=base_dir,
                    stop_on_failure=stop_on_failure,
                    item_filter=item_filter,
                    should_cancel=lambda: state.cancel_event.is_set(),
                    on_phase=_on_phase,
                )
            state.report = report
            # E11: if cooperative cancel was signalled during run_phases,
            # the run is CANCELLED, not COMPLETED. run_phases may have
            # returned early with partial results.
            # Phase 2.2: set the refined terminal_state BEFORE flipping the
            # registry status, so a /status poller that observes a terminal
            # `status` always sees a populated `terminal_state` too — no race
            # window where status=completed but terminal_state is still None.
            if state.cancel_event.is_set():
                state.terminal_state = "cancelled"
                state.status = RunStatus.CANCELLED
            else:
                state.terminal_state = _terminal_from_report(report)
                state.status = RunStatus.COMPLETED
        except Exception as exc:  # noqa: BLE001
            _log.exception("async run %s worker crashed", run.id)
            state.error = f"{type(exc).__name__}: {exc}"
            state.terminal_state = "failed"
            state.status = RunStatus.FAILED
        finally:
            state.lifecycle = "finalizing"
            # E11: skip inventory flush on cancel — partial sidecars
            # from a cancelled run are unreliable and should not update
            # the inventory DB. Completed/failed runs still flush.
            if state.status != RunStatus.CANCELLED:
                # Best-effort: flush the just-finished run's sidecars into the
                # inventory DB so the next /inventory or /apps/detect call sees
                # the up-to-date installed/candidate columns without a fresh
                # OS-level scan.
                try:
                    _flush_run_to_inventory_db(run_dir, inventory_db)
                except Exception:  # noqa: BLE001
                    _log.exception("post-run inventory flush failed")
            # Also persist per-app version-transition history so the
            # Apps view can render "this app was upgraded N times…".
            # apply phase records the transition; verify backfills the
            # to_version for triggered items once the vendor agent
            # reconciles.
            try:
                from ..dashboard.inventory_db import (  # noqa: PLC0415
                    backfill_triggered_history,
                    flush_apply_history,
                )

                flush_apply_history(run_dir, str(run.id), inventory_db)
                backfill_triggered_history(run_dir, str(run.id), inventory_db)
            except Exception:  # noqa: BLE001
                _log.exception("post-run update_history flush failed")
            state.finished_at = datetime.now(timezone.utc)
            state._completion_event.set()

    # Use asyncio.to_thread so we don't block the FastAPI event loop while
    # *starting* the worker (cheap), but the actual work runs synchronously
    # in a worker thread.
    asyncio.create_task(asyncio.to_thread(_worker))
    return state


__all__ = [
    "STREAM_LOG_ENV_VAR",
    "STREAM_LOG_FILENAME",
    "RunRegistry",
    "RunState",
    "RunStatus",
    "start_run_async",
]
