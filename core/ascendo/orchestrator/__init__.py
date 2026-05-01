"""Orchestrator package — sidecar disk I/O, locking, and recovery.

This subpackage owns everything between the on-disk JSON v1 sidecar
contract (defined in ``core.ascendo.models.sidecar``) and the rest of
the runtime. Layers above this module never read or write sidecar
files directly — they go through the helpers exported here.

Contents:

- :mod:`core.ascendo.orchestrator.sidecar_io`
    Atomic write, locked read, partial-sidecar recovery, and the
    :class:`RecoveryStub` model returned in place of corrupted sidecars
    so the dashboard can render "this run had a corrupted file at X"
    instead of crashing.

Future M2 additions (not yet present):

- ``runner.PhaseRunner`` — invokes adapter scripts with standard flags
  (``--run-id``, ``--json-out``, ``--log``, ``--profile``, ``--config``,
  ``--dry-run``).
- ``lock.AscendoLock`` — process-level run lock, distinct from the
  per-sidecar file lock owned by :mod:`sidecar_io`.
- ``exec.subprocess_safe`` — subprocess wrapper with timeout, encoding,
  and JSON parse.
- ``retry.exponential_backoff`` — retry policy for flaky adapters.
"""

from __future__ import annotations

from .run_async import RunRegistry, RunState, RunStatus, start_run_async
from .runner import DEFAULT_PHASE_ORDER, RunReport, run_phases
from .sidecar_io import (
    RecoveryStub,
    SidecarIOError,
    SidecarLockError,
    SidecarReadError,
    SidecarWriteError,
    list_run_sidecars,
    read_run,
    read_sidecar,
    recover_partial,
    write_sidecar,
)

__all__ = [
    "DEFAULT_PHASE_ORDER",
    "RecoveryStub",
    "RunRegistry",
    "RunReport",
    "RunState",
    "RunStatus",
    "SidecarIOError",
    "SidecarLockError",
    "SidecarReadError",
    "SidecarWriteError",
    "list_run_sidecars",
    "read_run",
    "read_sidecar",
    "recover_partial",
    "run_phases",
    "start_run_async",
    "write_sidecar",
]
