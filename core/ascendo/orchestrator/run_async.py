"""Async wrapper around :func:`run_phases` with a per-run state registry.

The synchronous ``run_phases`` is fine for short read-only phases (check,
plan, verify, cleanup). The ``apply`` phase, however, can take 10+ minutes
when many packages need upgrading — blocking an HTTP request for that long
is unacceptable.

This module provides:

- :class:`RunState` — a single-run lifecycle record (running/completed/failed
  + timestamps + report-when-done).
- :class:`RunRegistry` — in-memory map ``{run_id: RunState}``. Lives on
  ``app.state.run_registry``. Bounded LRU eviction keeps the map small for
  long-running dashboards.
- :func:`start_run_async` — kicks off ``run_phases`` in a worker thread,
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
failure — those are recorded normally) sets ``state=failed`` with the
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
from typing import TYPE_CHECKING
from uuid import UUID

from ..models.run import Phase, RunInfo
from .runner import RunReport, run_phases

# Convention: per-run streaming log, written to by bash apply scripts via
# `_stream_tee` (see adapters/<os>/scripts/<source>/apply.sh). The SSE
# endpoint tails this file and emits ``log_line`` + ``progress`` events
# so the Run Center can show every line of stdout/stderr in real time.
STREAM_LOG_FILENAME = "_stream.log"
STREAM_LOG_ENV_VAR = "ASCENDO_STREAM_LOG"

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
    FAILED = "failed"       # worker thread raised (rare — most failures are inside report.sidecars)


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
    # Internal coordination — used by the SSE endpoint to wait efficiently.
    _completion_event: threading.Event = field(default_factory=threading.Event, repr=False)


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

    def register(self, run_id: UUID, *, base_dir: Path) -> RunState:
        with self._lock:
            state = RunState(run_id=run_id, base_dir=base_dir)
            self._states[run_id] = state
            self._evict_if_needed_locked()
            return state

    def get(self, run_id: UUID) -> RunState | None:
        with self._lock:
            return self._states.get(run_id)

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

    Always returns synchronously — does not block on the worker.
    """
    state = registry.register(run.id, base_dir=base_dir)

    # Pre-create the per-run dir + an empty stream log so the SSE endpoint
    # can start tailing immediately, even before the first manager boots.
    run_dir = base_dir / str(run.id)
    run_dir.mkdir(parents=True, exist_ok=True)
    stream_log_path = run_dir / STREAM_LOG_FILENAME
    try:
        stream_log_path.touch(exist_ok=True)
    except OSError:
        # If we can't touch it (read-only disk?), the SSE endpoint just
        # won't see log_line events — non-fatal.
        pass

    def _worker() -> None:
        state.status = RunStatus.RUNNING
        state.started_at = datetime.now(timezone.utc)
        # Publish the stream-log path to subprocesses spawned by managers.
        # Threads share os.environ, so setting it before run_phases lets
        # any bash script (`apply.sh` etc.) tee its output through
        # `_stream_tee` if it cooperates. Restored in `finally`.
        prior_env = os.environ.get(STREAM_LOG_ENV_VAR)
        os.environ[STREAM_LOG_ENV_VAR] = str(stream_log_path)
        try:
            from .runner import DEFAULT_PHASE_ORDER  # noqa: PLC0415

            report = run_phases(
                adapter,
                run,
                host,
                phases=phases or DEFAULT_PHASE_ORDER,
                categories=categories,
                base_dir=base_dir,
                stop_on_failure=stop_on_failure,
                item_filter=item_filter,
            )
            state.report = report
            state.status = RunStatus.COMPLETED
        except Exception as exc:  # noqa: BLE001
            _log.exception("async run %s worker crashed", run.id)
            state.error = f"{type(exc).__name__}: {exc}"
            state.status = RunStatus.FAILED
        finally:
            if prior_env is None:
                os.environ.pop(STREAM_LOG_ENV_VAR, None)
            else:
                os.environ[STREAM_LOG_ENV_VAR] = prior_env
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
