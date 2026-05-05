"""Per-item results, summary, and free-form messages.

`Item` is the central record: one entry per package operation attempted
in this phase. `Summary` aggregates them. `Message` carries free-form
human-readable lines (warnings, hints) that didn't map to a specific
item.
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, NonNegativeInt, StringConstraints

from .package import (
    ItemEvidence,
    ItemRollback,
    ItemSource,
    PackageId,
    SourceType,
    VersionStr,
)


class ItemStatus(str, Enum):
    """Outcome of a single item operation.

    `up_to_date` is distinct from `success`: success means we did
    something successfully; up_to_date means there was nothing to do.
    The dashboard renders these differently.
    """

    SUCCESS = "success"
    UP_TO_DATE = "up_to_date"
    FAILED = "failed"
    SKIPPED = "skipped"
    PLANNED = "planned"   # dry-run mode: would have done X
    PARTIAL = "partial"   # multi-step item where some steps succeeded
    MISSING = "missing"   # not installed yet — apply would install it


class MessageLevel(str, Enum):
    """Severity for free-form messages."""

    DEBUG = "debug"
    INFO = "info"
    WARN = "warn"
    ERROR = "error"


class Message(BaseModel):
    """A single human-readable log-style message.

    Distinct from Python `logging` records — this is what gets persisted
    in the sidecar for dashboard display, audit, and stakeholder reports.
    Native scripts emit these alongside `items[]`.
    """

    model_config = ConfigDict(extra="forbid")

    level: MessageLevel
    text: Annotated[str, StringConstraints(min_length=1, max_length=4096)]
    timestamp: str | None = Field(
        default=None,
        description="ISO-8601 timestamp at message emission. Optional.",
    )


class Item(BaseModel):
    """One package operation attempted (or skipped, or planned) in this phase.

    The same `id` may appear once per phase per run. `current_version` is
    pre-apply state; `resolved_version` is post-apply truth; `target_version`
    is what the planner intended.
    """

    model_config = ConfigDict(extra="forbid")

    id: PackageId = Field(description="Package identifier within its source.")
    name: str = Field(description="Display name.")
    category: SourceType = Field(
        description="Source category (mirrors Package.category).",
    )
    source: ItemSource = Field(
        description="Specific source/feed instance.",
    )

    # Version triplet
    current_version: VersionStr | None = Field(
        default=None,
        description="Version installed before this phase (None if not installed).",
    )
    target_version: VersionStr | None = Field(
        default=None,
        description="Version the planner intended to install.",
    )
    resolved_version: VersionStr | None = Field(
        default=None,
        description=(
            "Version actually installed after apply (or what `verify` proved). "
            "Distinct from target_version when the source ships a different "
            "build than advertised."
        ),
    )

    # Outcome
    status: ItemStatus
    exit_code: int | None = Field(
        default=None,
        description="Underlying tool's exit code. e.g. winget's -1978335190.",
    )
    duration_ms: NonNegativeInt | None = Field(
        default=None,
        description="Wall-clock duration of this item operation, in milliseconds.",
    )

    # Evidence + rollback
    evidence: ItemEvidence | None = Field(
        default=None,
        description=(
            "Local-version evidence. Set by adapters that need unknown-version "
            "suppression (PowerShell-style)."
        ),
    )
    rollback: ItemRollback | None = Field(
        default=None,
        description=(
            "Rollback method for a successful apply. None on check/plan/verify; "
            "expected to be set on successful apply."
        ),
    )

    # Free-form
    messages: list[Message] = Field(
        default_factory=list,
        description="Per-item log lines from the native tool.",
    )

    # Optional plugin/source-specific extension fields are NOT permitted at
    # this level — `extra='forbid'`. Plugins that need extra fields should
    # store them in `evidence.note` or a separate `meta` JSON in their own
    # plugin storage area, NOT in the standard sidecar shape.


class Summary(BaseModel):
    """Aggregate counts + headline status for a single phase sidecar.

    Sums of `items[]` outcomes, plus the phase-level exit code. The
    orchestrator's overall run status is computed by combining all phase
    summaries — see core.orchestrator.
    """

    model_config = ConfigDict(extra="forbid")

    total: NonNegativeInt = Field(description="Total items in this phase.")
    success: NonNegativeInt = 0
    up_to_date: NonNegativeInt = 0
    failed: NonNegativeInt = 0
    skipped: NonNegativeInt = 0
    planned: NonNegativeInt = 0
    partial: NonNegativeInt = 0
    duration_ms: NonNegativeInt | None = Field(
        default=None,
        description="Total wall-clock duration for this phase, in milliseconds.",
    )
    exit_code: int = Field(
        default=0,
        description=(
            "Phase-level exit code. 0 on full success or up-to-date; "
            "non-zero on any failure (orchestrator inspects the items[] "
            "for granular reasons)."
        ),
    )

    def is_clean(self) -> bool:
        """True if every item succeeded, was up-to-date, or was deliberately skipped."""
        return self.failed == 0 and self.partial == 0
