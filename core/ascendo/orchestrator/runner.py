"""Phase orchestrator — drives an adapter through the 5-phase contract.

Glue layer that ties together:

- :class:`IAdapter` (Layer 5) — provides per-host :class:`IPackageManager` instances
- :class:`IPackageManager` (Layer 5) — runs one phase × one source, returns a Sidecar
- :class:`Sidecar` (Layer 4 model) — the unit of structured output
- :func:`write_sidecar` (Layer 4 I/O) — atomic on-disk persistence

A "run" is one invocation of :func:`run_phases`. It iterates the requested
phases in 5-phase contract order (check → plan → apply → verify → cleanup),
and within each phase iterates the available package managers. Each
``(phase, manager)`` pair produces one :class:`Sidecar`, which is both
persisted to ``<base_dir>/<run-id>/<phase>__<category>.json`` and accumulated
in the returned :class:`RunReport`.

**Failure handling.** A :class:`ManagerError` from one manager does NOT abort
the run — the failure is recorded as a synthetic ``status=failed`` sidecar
and the orchestrator continues with the next manager / next phase. This
matches the design intent of the JSON v1 contract (per-item failures live
in ``items[]``; manager-level failures are logged + isolated).

**Phase short-circuit.** If a phase ends with ``status=failed`` for ALL
managers, subsequent phases are skipped (apply on a failed plan would be
unsafe). Configurable via ``stop_on_failure``.

This module is intentionally **thin** — no CLI parsing, no config loading,
no scheduling. Those live in ``core.ascendo.cli`` and
``core.ascendo.scheduler``. Run :func:`run_phases` directly from tests, from
a Typer CLI command, or from a FastAPI endpoint.

See ADR-0003 (sidecar contract), ADR-0004 (Python core + native scripts),
ADR-0005 (six-layer architecture).
"""
from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterable, Mapping, Sequence
from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from ..interfaces.adapter import IAdapter
from ..interfaces.package_manager import IPackageManager, ManagerError
from ..models.host import HostInfo
from ..models.package import ItemSource, SourceType
from ..models.result import Item, ItemStatus, Message, MessageLevel, Summary
from ..models.run import Phase, PhaseStatus, RunInfo
from ..models.sidecar import Sidecar, SidecarSchema, ToolInfo
from .run_lock import default_lock_path, run_lock
from .run_logger import attach_run_log
from .sidecar_io import SidecarWriteError, write_sidecar

_log = logging.getLogger(__name__)

# Canonical phase order. Subset is allowed (e.g. ['check', 'plan']) — the
# orchestrator preserves this ordering regardless of the input order so
# downstream code never sees apply before check.
DEFAULT_PHASE_ORDER: tuple[Phase, ...] = (
    Phase.CHECK,
    Phase.PLAN,
    Phase.APPLY,
    Phase.VERIFY,
    Phase.CLEANUP,
)


class RunReport(BaseModel):
    """Aggregate result of a single :func:`run_phases` invocation.

    Frozen — created once at the end of the run, never mutated thereafter.
    The dashboard / CLI consume this for summary rendering; downstream
    persistence (run history database) reads the embedded sidecars.
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    run: RunInfo = Field(description="Run-level metadata.")
    host: HostInfo = Field(description="Host snapshot at run start.")
    sidecars: list[Sidecar] = Field(
        default_factory=list,
        description=(
            "All sidecars produced this run, in execution order. One entry "
            "per (phase, manager) pair that actually ran."
        ),
    )
    skipped_managers: list[tuple[str, str]] = Field(
        default_factory=list,
        description=(
            "Managers skipped before any phase ran, as ``(category, reason)`` "
            "tuples. Typical reason: ``is_available()`` returned False."
        ),
    )
    aborted_after_phase: Phase | None = Field(
        default=None,
        description=(
            "If the run short-circuited (``stop_on_failure=True``), the last "
            "phase that ran before abort. None if all requested phases ran."
        ),
    )

    @property
    def overall_status(self) -> PhaseStatus:
        """Aggregate status across all sidecars.

        - ``failed`` if any sidecar's status is ``failed``.
        - ``partial`` if some failed and some succeeded.
        - ``skipped`` if every sidecar is ``skipped`` and at least one exists.
        - ``success`` otherwise (including the no-sidecars case).
        """
        if not self.sidecars:
            return PhaseStatus.SUCCESS
        statuses = {s.status for s in self.sidecars}
        if PhaseStatus.FAILED in statuses and PhaseStatus.SUCCESS in statuses:
            return PhaseStatus.PARTIAL
        if PhaseStatus.FAILED in statuses:
            return PhaseStatus.FAILED
        if statuses == {PhaseStatus.SKIPPED}:
            return PhaseStatus.SKIPPED
        return PhaseStatus.SUCCESS

    @property
    def total_items(self) -> int:
        """Sum of items across every sidecar."""
        return sum(len(s.items) for s in self.sidecars)

    def by_category(self, category: SourceType) -> list[Sidecar]:
        """Return only sidecars produced by managers of the given category."""
        return [s for s in self.sidecars if s.category is category]

    def by_phase(self, phase: Phase) -> list[Sidecar]:
        """Return only sidecars produced for the given phase."""
        return [s for s in self.sidecars if s.phase is phase]


# ── Public entry point ──────────────────────────────────────────────────────


def run_phases(
    adapter: IAdapter,
    run: RunInfo,
    host: HostInfo,
    *,
    phases: Sequence[Phase] = DEFAULT_PHASE_ORDER,
    categories: Iterable[SourceType] | None = None,
    base_dir: Path,
    stop_on_failure: bool = True,
    item_filter: Iterable[str] | Mapping[str, Iterable[str]] | None = None,
    should_cancel: Callable[[], bool] | None = None,
    on_phase: Callable[[str, int, int], None] | None = None,
    pre_final_apply: Callable[[], None] | None = None,
) -> RunReport:
    """Run the requested phases for every available package manager.

    Args:
        adapter: The OS adapter (``WindowsAdapter`` / ``UbuntuAdapter`` / etc.).
        run: Run-level metadata. ``run.id`` becomes the sidecar directory name.
        host: Host snapshot. Captured by the caller via ``adapter.detect_host()``.
        phases: Subset of phases to run. Reordered to canonical
            (``check → plan → apply → verify → cleanup``) regardless of input.
        categories: Optional filter on which package managers to include.
            ``None`` means all managers exposed by the adapter for this host.
        base_dir: Root directory under which ``<run-id>/`` will be created.
        stop_on_failure: If True (default), the run short-circuits when a phase
            ends with all-failed status. ``RunReport.aborted_after_phase`` is set.
        item_filter: Optional package identifiers. Either a flat iterable
            (forwarded to every manager's ``run_phase``) or a mapping
            ``{category: [identifiers]}`` (each manager only receives the
            identifiers its own category matches on; a missing key means
            "no filter" for that manager). An **empty list** for a
            category means "everything is excluded": the runner skips that
            manager's ``apply``/``cleanup`` with a synthesized ``skipped``
            sidecar (message code ``ALL_EXCLUDED``) instead of running it
            unfiltered. The :data:`NEEDS_CHECK` marker (exclusions exist
            but there is no check data to build the list from) skips the
            mutating phases the same way (code ``EXCLUSIONS_NEED_CHECK``)
            while the non-mutating phases run.

        pre_final_apply: Called right before the ``apply`` of a
            ``defer_until_end`` manager (macOS ``softwareupdate -R``, which may
            restart the machine) so the caller can persist the report /
            inventory first. Not called when that step is skipped.

    Returns:
        A :class:`RunReport` with every produced sidecar.

    Raises:
        RunLockError: a mutating run (apply/cleanup, not dry-run) started while
            another Ascendo process holds the cross-process run lock (POSIX).

    Notes:
        Per-manager :class:`ManagerError` is swallowed and recorded as a
        synthetic ``failed`` sidecar; it never propagates out of this function.
        Truly catastrophic errors (disk full, etc.) DO propagate as
        :class:`OSError` from the I/O layer.
    """
    ordered = [p for p in DEFAULT_PHASE_ORDER if p in set(phases)]
    if not ordered:
        msg = f"phases must be a non-empty subset of {DEFAULT_PHASE_ORDER}"
        raise ValueError(msg)

    # M5.7.6: attach a per-run file handler under <base_dir>/<run.id>/run.log
    # and prune to ASCENDO_RUN_LOG_KEEP (default 30) on exit. Mirrors the
    # Ascendo `logs/update_all_<ts>.log` rotation. Cheap (~1 MiB
    # per run worst-case); large operator wins for offline post-mortem.
    mutating = bool(set(ordered) & _MUTATING_PHASES) and not run.dry_run
    scope_factory = getattr(adapter, "run_scope", None)
    scope = (
        scope_factory(run, host, phases=tuple(ordered))
        if scope_factory is not None else nullcontext()
    )
    with attach_run_log(str(run.id), base_dir), \
            run_lock(default_lock_path(base_dir), str(run.id), enabled=mutating), \
            scope:
        return _run_phases_inner(
            adapter=adapter, run=run, host=host, ordered=ordered,
            categories=categories, base_dir=base_dir,
            stop_on_failure=stop_on_failure, item_filter=item_filter,
            should_cancel=should_cancel, on_phase=on_phase,
            pre_final_apply=pre_final_apply,
        )


def _run_phases_inner(
    adapter,
    run,
    host,
    *,
    ordered,
    categories,
    base_dir,
    stop_on_failure,
    item_filter,
    should_cancel,
    on_phase=None,
    pre_final_apply=None,
):
    available_managers = adapter.package_managers(host)
    selected = _select_managers(available_managers, categories, host)
    # Restart-capable managers: their APPLY runs after everything else.
    deferred = [m for m in selected if getattr(m, "defer_until_end", False)]

    sidecars: list[Sidecar] = []
    skipped: list[tuple[str, str]] = []
    excluded_skips: list[tuple[str, str]] = []
    aborted: Phase | None = None

    # Identify managers we filtered out for `is_available() == False`.
    for mgr in available_managers:
        if mgr in selected:
            continue
        skipped.append((mgr.category.value, _skip_reason(mgr, categories, host)))
        _log.info(
            "skipped manager %s (%s)",
            mgr.category.value,
            skipped[-1][1],
        )

    def _cancelled() -> bool:
        try:
            return should_cancel is not None and bool(should_cancel())
        except Exception:  # noqa: BLE001 — a flaky predicate must never crash a run
            return False

    def _run_one(mgr, phase):
        """One (phase, manager) slot: exclusion handling + the real call."""
        mgr_filter = _filter_for(item_filter, mgr)
        if isinstance(mgr_filter, _NeedsCheck):
            if phase in _MUTATING_PHASES:
                # Exclusions exist but we cannot build the inclusion list
                # yet: fail SAFE (skip), never run unfiltered.
                sidecar = _skip_sidecar(
                    mgr, phase, run, host, base_dir=base_dir,
                    code="EXCLUSIONS_NEED_CHECK",
                    text=(
                        f"EXCLUSIONS_NEED_CHECK: {mgr.category.value} has excluded "
                        f"items but no check data yet - {phase.value} skipped; run "
                        "check first so excluded items can be honoured"
                    ),
                )
                excluded_skips.append((
                    mgr.category.value,
                    "exclusions need a check first (run check so excluded items can be honoured)",
                ))
                return sidecar
            mgr_filter = None   # non-mutating phases run as before
        if (
            mgr_filter is not None
            and not mgr_filter
            and phase in _MUTATING_PHASES
        ):
            # Every item of this category is excluded: nothing to do.
            # Never fall through to "no filter" (that would update the
            # excluded apps).
            sidecar = _skip_sidecar(
                mgr, phase, run, host, base_dir=base_dir,
                code="ALL_EXCLUDED",
                text=(
                    f"ALL_EXCLUDED: every {mgr.category.value} item is excluded "
                    f"from updates - {phase.value} skipped"
                ),
            )
            excluded_skips.append((mgr.category.value, "all items excluded"))
        else:
            sidecar = _safe_run_phase(
                mgr,
                phase,
                run,
                host,
                base_dir=base_dir,
                item_filter=mgr_filter,
            )
        return sidecar

    cancelled = False
    apply_reached = False
    for idx, phase in enumerate(ordered):
        # Cooperative cancel is checked at phase + manager boundaries (NOT
        # mid-subprocess): a Stop click takes effect before the next
        # phase/manager starts, so an in-flight install is never killed
        # half-done. For a long `full` run (5 phases × N managers) this
        # stops all remaining work within one manager-phase.
        if _cancelled():
            _log.warning("run %s cancelled before phase %s", run.id, phase.value)
            aborted = phase
            cancelled = True
            break
        # Phase 2.3: announce phase entry so the async worker can record the
        # live phase on RunState (the SSE generator also derives durable
        # `phase` events from on-disk sidecars for post-hoc replay).
        if on_phase is not None:
            try:
                on_phase(phase.value, idx, len(ordered))
            except Exception:  # noqa: BLE001
                _log.exception("on_phase callback raised for phase %s", phase.value)
        phase_results: list[Sidecar] = []
        for mgr in selected:
            if _cancelled():
                _log.warning(
                    "run %s cancelled during phase %s (before %s)",
                    run.id, phase.value, mgr.category.value,
                )
                aborted = phase
                cancelled = True
                break
            if phase is Phase.APPLY and mgr in deferred:
                continue   # runs after every other step (restart-safe)
            sidecar = _run_one(mgr, phase)
            phase_results.append(sidecar)
            sidecars.append(sidecar)

        if cancelled:
            break

        if phase is Phase.APPLY:
            apply_reached = True

        if phase == Phase.CHECK:
            try:
                from .deduplicator import apply_deduplication  # noqa: PLC0415
                apply_deduplication(phase_results, run.id, base_dir)
            except Exception:  # noqa: BLE001
                _log.exception("Deduplication pass failed for run %s", run.id)

        if stop_on_failure and phase_results and all(
            s.status is PhaseStatus.FAILED for s in phase_results
        ):
            _log.warning(
                "all managers reported failed for phase %s; aborting run",
                phase.value,
            )
            aborted = phase
            break

    def _finalize(rep, *, cancelled):
        """run_meta.json + REPORT.md (idempotent; safe to call twice)."""
        # Durable run-level metadata for REPORT.md ("Run stopped after phase X",
        # "Skipped: <category> (<reason>)"). Best-effort, never fatal.
        _write_run_meta(
            base_dir / str(run.id),
            report=rep,
            cancelled=cancelled,
            extra_skipped=_dedupe_pairs(excluded_skips),
        )
        # Best-effort: write a human-readable REPORT.md when an apply phase
        # ran. Non-fatal -- generator swallows its own errors.
        if Phase.APPLY in set(ordered):
            try:
                from .report import generate_apply_report  # noqa: PLC0415

                generate_apply_report(base_dir / str(run.id), host=host)
            except Exception:  # noqa: BLE001
                _log.exception("post-run report generation failed for %s", run.id)

    # -- restart-capable (deferred) APPLY ------------------------------------
    # Only a HARD failure of an earlier mutating step may stop the restart:
    # a half-applied brew/mas/npm/web transaction followed by a reboot is the
    # one outcome to avoid. pip and soft deferrals never block.
    if deferred and apply_reached and not cancelled and not _cancelled():
        blocking = sorted({
            sc.category.value for sc in sidecars
            if sc.phase is Phase.APPLY
            and sc.category.value in _BLOCKING_CATEGORIES
            and sc.status in (PhaseStatus.FAILED, PhaseStatus.PARTIAL)
        })
        if blocking:
            cats = ", ".join(blocking)
            for mgr in deferred:
                sidecars.append(_skip_sidecar(
                    mgr, Phase.APPLY, run, host, base_dir=base_dir,
                    code="SYSTEM_UPDATE_DEFERRED",
                    text=(
                        f"macOS update deferred: {cats} apply failed; "
                        "nothing was restarted - fix and re-run."
                    ),
                    deferred_item=True,
                ))
        else:
            # Persist the report / inventory BEFORE the step that may reboot.
            interim = RunReport(
                run=run, host=host, sidecars=list(sidecars),
                skipped_managers=skipped, aborted_after_phase=aborted,
            )
            try:
                _finalize(interim, cancelled=False)
            except Exception:  # noqa: BLE001 - bookkeeping must not block the run
                _log.exception("interim _finalize raised for run %s", run.id)
            if pre_final_apply is not None:
                try:
                    pre_final_apply()
                except Exception:  # noqa: BLE001 - bookkeeping must not block the run
                    _log.exception("pre_final_apply hook raised for run %s", run.id)
            for mgr in deferred:
                sidecars.append(_run_one(mgr, Phase.APPLY))

    report = RunReport(
        run=run,
        host=host,
        sidecars=sidecars,
        skipped_managers=skipped,
        aborted_after_phase=aborted,
    )

    _finalize(report, cancelled=cancelled)

    return report


# ── Internals ───────────────────────────────────────────────────────────────

# Phases that mutate the system (an all-excluded category must not run them).
_MUTATING_PHASES: frozenset[Phase] = frozenset({Phase.APPLY, Phase.CLEANUP})

# An apply that fails hard in one of these categories postpones the
# restart-capable step (macOS_updates matrix: App Store, CLI/npm, brew and the
# "internet" apps block; pip / soft problems never do).
_BLOCKING_CATEGORIES: frozenset[str] = frozenset({"brew", "mas", "npm", "web"})

RUN_META_FILENAME = "run_meta.json"


class _NeedsCheck:
    """Marker: a category has exclusions but no check data to honour them.

    Used as a mapping value in ``run_phases(item_filter=...)``. The runner
    skips that manager's mutating phases (fail-safe: never upgrade an app
    the user excluded just because we could not build an inclusion list)
    and runs the non-mutating phases unfiltered, so a check can produce the
    data the next run needs.
    """

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "NEEDS_CHECK"


NEEDS_CHECK = _NeedsCheck()


def _filter_for(
    item_filter: Iterable[str] | Mapping[str, Iterable[str] | _NeedsCheck] | None,
    mgr: IPackageManager,
) -> list[str] | _NeedsCheck | None:
    """Resolve the filter list ONE manager should receive.

    * ``None`` -> ``None`` (no filter).
    * mapping -> ``mapping[mgr.category.value]`` (missing key -> ``None``;
      :data:`NEEDS_CHECK` is passed through).
    * flat iterable -> that list for every manager (legacy behaviour).
    ``[]`` is preserved (it means "everything excluded", see
    :func:`run_phases`); it is never coerced to ``None``.
    """
    if item_filter is None:
        return None
    if isinstance(item_filter, Mapping):
        entry = item_filter.get(mgr.category.value)
        if entry is None:
            return None
        if isinstance(entry, _NeedsCheck):
            return entry
        return [str(x) for x in entry]
    return [str(x) for x in item_filter]


def _dedupe_pairs(pairs: list[tuple[str, str]]) -> list[tuple[str, str]]:
    seen: set[tuple[str, str]] = set()
    out: list[tuple[str, str]] = []
    for p in pairs:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def _write_run_meta(
    run_dir: Path,
    *,
    report: RunReport,
    cancelled: bool,
    extra_skipped: list[tuple[str, str]],
) -> None:
    """Write ``<run_dir>/run_meta.json`` (best effort; never raises)."""
    try:
        run_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "aborted_after_phase": (
                report.aborted_after_phase.value
                if report.aborted_after_phase is not None else None
            ),
            "cancelled": bool(cancelled),
            "skipped_managers": [
                [cat, reason]
                for cat, reason in list(report.skipped_managers) + list(extra_skipped)
            ],
            "status": report.overall_status.value,
        }
        (run_dir / RUN_META_FILENAME).write_text(
            json.dumps(payload, indent=2) + "\n", encoding="utf-8",
        )
    except Exception:
        _log.exception("could not write %s", run_dir / RUN_META_FILENAME)


def _skip_sidecar(
    mgr: IPackageManager,
    phase: Phase,
    run: RunInfo,
    host: HostInfo,
    *,
    base_dir: Path,
    code: str,
    text: str,
    deferred_item: bool = False,
) -> Sidecar:
    """Persist a ``skipped`` sidecar carrying one coded message.

    Item-less by default. ``deferred_item=True`` adds one ``skipped`` item
    carrying the same coded message so the REPORT.md "Deferred" section and the
    run's warning count see the soft deferral (F3).
    """
    message = Message(level=MessageLevel.INFO, text=text, code=code)
    sidecar = _synthesize_sidecar(
        mgr, phase, run, host,
        status=PhaseStatus.SKIPPED,
        message=message,
        exit_code=0,
    )
    if deferred_item:
        item = Item(
            id=f"{mgr.category.value}:system-update",
            name="macOS system update" if mgr.category is SourceType.SOFTWAREUPDATE
            else mgr.display_name,
            category=mgr.category,
            source=ItemSource(type=mgr.category),
            status=ItemStatus.SKIPPED,
            exit_code=0,
            messages=[message],
        )
        sidecar = sidecar.model_copy(update={
            "items": [item],
            "summary": Summary(total=1, skipped=1, exit_code=0),
        })
    write_sidecar(sidecar, base_dir=base_dir)
    return sidecar


def _select_managers(
    managers: Sequence[IPackageManager],
    categories: Iterable[SourceType] | None,
    host: HostInfo,
) -> list[IPackageManager]:
    """Filter managers by availability + optional category whitelist."""
    cat_set = set(categories) if categories is not None else None
    selected: list[IPackageManager] = []
    for mgr in managers:
        if cat_set is not None and mgr.category not in cat_set:
            continue
        try:
            if not mgr.is_available(host):
                continue
        except Exception:  # noqa: BLE001
            # is_available shouldn't raise, but if it does we treat the
            # manager as unavailable rather than aborting the run.
            _log.exception("is_available raised for %s; treating as unavailable", mgr.category.value)
            continue
        selected.append(mgr)
    return selected


def _skip_reason(
    mgr: IPackageManager,
    categories: Iterable[SourceType] | None,
    host: HostInfo,
) -> str:
    """Human-readable reason a manager was filtered out (best effort)."""
    if categories is not None and mgr.category not in set(categories):
        return "not in categories filter"
    try:
        if not mgr.is_available(host):
            return "is_available() returned False"
    except Exception as exc:  # noqa: BLE001
        return f"is_available() raised: {exc!r}"
    return "filtered (unknown reason)"


def _safe_run_phase(
    mgr: IPackageManager,
    phase: Phase,
    run: RunInfo,
    host: HostInfo,
    *,
    base_dir: Path,
    item_filter: Iterable[str] | None,
) -> Sidecar:
    """Run one (phase, manager); persist the sidecar; never raise.

    On :class:`ManagerError`, synthesize a ``status=failed`` sidecar that
    carries the error in :attr:`Sidecar.messages`. The synthesized sidecar
    is still persisted via :func:`write_sidecar` so the run history shows
    every attempted slot.
    """
    try:
        sidecar = mgr.run_phase(phase, run, host, item_filter=item_filter)
    except ManagerError as exc:
        _log.error(
            "manager %s failed at phase %s: %s",
            mgr.category.value,
            phase.value,
            exc,
        )
        sidecar = _synthesize_failure_sidecar(mgr, phase, run, host, exc)

    try:
        write_sidecar(sidecar, base_dir=base_dir)
    except SidecarWriteError:
        # E5: write_sidecar raises SidecarWriteError (RuntimeError
        # subclass), NOT OSError.  Re-raise — disk full / permission
        # denied means the orchestrator can't persist any evidence.
        _log.exception("failed to persist sidecar for %s/%s", mgr.category.value, phase.value)
        raise

    return sidecar


def _synthesize_failure_sidecar(
    mgr: IPackageManager,
    phase: Phase,
    run: RunInfo,
    host: HostInfo,
    exc: ManagerError,
) -> Sidecar:
    """Build a minimal valid sidecar describing a manager-level crash."""
    return _synthesize_sidecar(
        mgr, phase, run, host,
        status=PhaseStatus.FAILED,
        message=Message(
            level=MessageLevel.ERROR,
            text=f"{type(exc).__name__}: {exc}",
        ),
        exit_code=1,
    )


def _synthesize_sidecar(
    mgr: IPackageManager,
    phase: Phase,
    run: RunInfo,
    host: HostInfo,
    *,
    status: PhaseStatus,
    message: Message,
    exit_code: int,
) -> Sidecar:
    """Build a minimal valid item-less sidecar carrying one message."""
    now = datetime.now(timezone.utc)
    return Sidecar.model_validate(
        {
            "schema": SidecarSchema.V1_ASCENDO.value,
            "run": run.model_dump(mode="json"),
            "host": host.model_dump(mode="json"),
            "tool": ToolInfo(
                name=mgr.category.value,
                version="unknown",
            ).model_dump(mode="json"),
            "phase": phase.value,
            "category": mgr.category.value,
            "started_at": now.isoformat(),
            "finished_at": now.isoformat(),
            "status": status.value,
            "items": [],
            "summary": Summary(
                total=0,
                failed=0,
                exit_code=exit_code,
            ).model_dump(),
            "messages": [message.model_dump()],
        }
    )


__all__ = [
    "DEFAULT_PHASE_ORDER",
    "NEEDS_CHECK",
    "RUN_META_FILENAME",
    "RunReport",
    "run_phases",
]
