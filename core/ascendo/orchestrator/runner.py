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

import logging
from collections.abc import Iterable, Sequence
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from ..interfaces.adapter import IAdapter
from ..interfaces.package_manager import IPackageManager, ManagerError
from ..models.host import HostInfo
from ..models.package import SourceType
from ..models.result import Message, MessageLevel, Summary
from ..models.run import Phase, PhaseStatus, RunInfo
from ..models.sidecar import Sidecar, SidecarSchema, ToolInfo
from .sidecar_io import write_sidecar

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
    item_filter: Iterable[str] | None = None,
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
        item_filter: Optional iterable of package IDs forwarded to every
            manager's ``run_phase`` call.

    Returns:
        A :class:`RunReport` with every produced sidecar.

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

    available_managers = adapter.package_managers(host)
    selected = _select_managers(available_managers, categories, host)

    sidecars: list[Sidecar] = []
    skipped: list[tuple[str, str]] = []
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

    for phase in ordered:
        phase_results: list[Sidecar] = []
        for mgr in selected:
            sidecar = _safe_run_phase(
                mgr,
                phase,
                run,
                host,
                base_dir=base_dir,
                item_filter=item_filter,
            )
            phase_results.append(sidecar)
            sidecars.append(sidecar)

        if stop_on_failure and phase_results and all(
            s.status is PhaseStatus.FAILED for s in phase_results
        ):
            _log.warning(
                "all managers reported failed for phase %s; aborting run",
                phase.value,
            )
            aborted = phase
            break

    return RunReport(
        run=run,
        host=host,
        sidecars=sidecars,
        skipped_managers=skipped,
        aborted_after_phase=aborted,
    )


# ── Internals ───────────────────────────────────────────────────────────────


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
    except OSError:
        # Disk full, permission denied — re-raise; orchestrator can't recover.
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
            "status": PhaseStatus.FAILED.value,
            "items": [],
            "summary": Summary(
                total=0,
                failed=0,
                exit_code=1,
            ).model_dump(),
            "messages": [
                Message(
                    level=MessageLevel.ERROR,
                    text=f"{type(exc).__name__}: {exc}",
                ).model_dump(),
            ],
        }
    )


__all__ = [
    "DEFAULT_PHASE_ORDER",
    "RunReport",
    "run_phases",
]
