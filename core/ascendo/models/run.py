"""Run-level metadata — `run.*` block of the JSON v1 sidecar.

A run is a single end-to-end invocation of Ascendo (CLI, scheduled job,
or dashboard click). One run produces one or more sidecars (one per
phase × category). All sidecars from the same run share the same
`run.id`.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints


class Phase(str, Enum):
    """The 5-phase contract every category script implements.

    See `docs/agents/contract.md` (Linux pre-merge) and ADR-0003 for
    the contract's normative definition.

    - **check**: read-only inventory of what's available. Side-effect-free.
    - **plan**: enumerate exactly what would change. Side-effect-free.
    - **apply**: execute changes. The only mutating phase.
    - **verify**: post-apply validation. Side-effect-free.
    - **cleanup**: bounded post-run housekeeping (cache prune, log rotate).
    """

    CHECK = "check"
    PLAN = "plan"
    APPLY = "apply"
    VERIFY = "verify"
    CLEANUP = "cleanup"


class PhaseStatus(str, Enum):
    """Outcome of a phase.

    `partial` is the deliberate middle ground: some items succeeded, some
    failed, the phase as a whole did not abort. The orchestrator uses
    this to decide whether to continue to the next phase or short-circuit.
    """

    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"
    SKIPPED = "skipped"


class Trigger(str, Enum):
    """What initiated this run.

    Drives audit + telemetry. Scheduled jobs are distinguished from CLI
    runs because they need different default behaviors (silent mode,
    automatic snapshot, no interactive confirmations).
    """

    CLI = "cli"
    SCHEDULER = "scheduler"
    DASHBOARD = "dashboard"
    PLUGIN = "plugin"
    UNKNOWN = "unknown"


# Profile names are short identifiers users can override in config.
# Built-ins: 'quick', 'safe', 'full'. Custom profiles also allowed.
ProfileName = Annotated[
    str,
    StringConstraints(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_\-]+$"),
]


class RunInfo(BaseModel):
    """Run-level identity + invocation metadata. Frozen."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
    )

    id: UUID = Field(
        description="UUID v4 generated at run start. Stable across all phases.",
    )
    trigger: Trigger = Field(
        description="What initiated this run.",
    )
    profile: ProfileName = Field(
        description="Profile name. Built-in: 'quick' | 'safe' | 'full'.",
    )
    dry_run: bool = Field(
        default=False,
        description=(
            "True if no mutations should be performed. Apply phase still "
            "runs but emits a sidecar with 'planned' items only."
        ),
    )
    started_at: datetime = Field(
        description="ISO-8601 timestamp at run start (UTC).",
    )
    finished_at: datetime | None = Field(
        default=None,
        description=(
            "ISO-8601 timestamp at run end (UTC). None until the orchestrator "
            "writes the final summary."
        ),
    )
    invocation: str | None = Field(
        default=None,
        description=(
            "Original command-line string for human-readable audit. "
            "Optional; CLI captures this, scheduler may not."
        ),
    )
