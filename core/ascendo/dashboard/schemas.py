"""Pydantic v2 request/response models for the dashboard REST API.

Schemas here are **wire-format only** — distinct from
:mod:`core.ascendo.models` which holds the canonical domain types. Wire
schemas can evolve faster than domain types without breaking the JSON v1
sidecar contract.

Conventions:

- All wire schemas inherit from a single base with ``extra='forbid'``.
- Camel-case is NOT used — JSON keys match Python attribute names. The SPA
  layer (Layer 1) does the camel-case shim if it wants.
- Datetimes serialize as ISO-8601 (Pydantic v2 default).
"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from ..models.host import HostInfo
from ..models.package import SourceType
from ..models.run import Phase, PhaseStatus
from ..models.sidecar import Sidecar


class _WireBase(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


# ── /version ────────────────────────────────────────────────────────────────


class VersionResponse(_WireBase):
    ascendo: str = Field(description="Ascendo version slug (SemVer 2.0.0).")
    adapter: str | None = Field(
        default=None,
        description="Adapter slug (windows/linux_ubuntu/macos) or None if no adapter installed.",
    )
    adapter_tier: int | None = Field(default=None)


# ── /health ─────────────────────────────────────────────────────────────────


class HealthResponse(_WireBase):
    status: str = Field(description="Overall: 'ok' | 'degraded' | 'error'.")
    adapter: str | None = Field(default=None)
    components: dict[str, str] = Field(
        default_factory=dict,
        description="Per-component status from adapter.health_check().",
    )


# ── /runs ───────────────────────────────────────────────────────────────────


class RunRequest(_WireBase):
    """Body of POST /runs."""

    profile: str = Field(
        default="full",
        description="Profile name. Built-in: 'quick' | 'safe' | 'full'.",
    )
    phases: list[Phase] | None = Field(
        default=None,
        description="Subset of phases to run. None = all 5 in canonical order.",
    )
    categories: list[SourceType] | None = Field(
        default=None,
        description="Filter on source categories. None = all available.",
    )
    dry_run: bool = Field(default=False)
    item_filter: list[str] | None = Field(
        default=None,
        description="Restrict the run to specific package IDs.",
    )
    stop_on_failure: bool = Field(default=True)


class SidecarSummary(_WireBase):
    """Compact view of a Sidecar — for /runs index responses."""

    run_id: UUID
    phase: Phase
    category: SourceType
    status: PhaseStatus
    started_at: datetime
    finished_at: datetime
    total_items: int
    failed_items: int


class RunResponse(_WireBase):
    """Body of POST /runs response and GET /runs/{id}."""

    run_id: UUID
    profile: str
    dry_run: bool
    started_at: datetime
    finished_at: datetime | None
    overall_status: PhaseStatus
    aborted_after_phase: Phase | None
    host: HostInfo
    sidecars: list[Sidecar]
    skipped_managers: list[tuple[str, str]] = Field(
        default_factory=list,
        description="(category, reason) pairs for filtered managers.",
    )

    @classmethod
    def from_orchestrator(cls, report: object) -> RunResponse:
        """Build from a :class:`RunReport` (avoids cross-import in routes)."""
        # Local import to keep schemas leaf-level.
        from ..orchestrator import RunReport  # noqa: PLC0415

        if not isinstance(report, RunReport):
            msg = f"expected RunReport, got {type(report).__name__}"
            raise TypeError(msg)

        return cls(
            run_id=report.run.id,
            profile=report.run.profile,
            dry_run=report.run.dry_run,
            started_at=report.run.started_at,
            finished_at=report.run.finished_at,
            overall_status=report.overall_status,
            aborted_after_phase=report.aborted_after_phase,
            host=report.host,
            sidecars=report.sidecars,
            skipped_managers=report.skipped_managers,
        )


class RunListEntry(_WireBase):
    """One row in the GET /runs index response.

    Includes legacy SPA-compatible fields (``id``, ``started_at``, ``status``,
    …) populated from the first sidecar of each run dir, in addition to the
    canonical ``run_id``/``sidecar_count``/``phases`` triple. The SPA's
    History tab renders rows entirely from these fields, so they MUST be
    present (the values may be ``None`` if no sidecar could be parsed).
    """

    run_id: UUID
    sidecar_count: int
    phases: list[Phase] = Field(
        default_factory=list,
        description="Phases for which a sidecar exists in this run dir.",
    )
    # Legacy SPA aliases / enrichment ─ all optional so existing API
    # consumers that only read run_id/sidecar_count/phases keep working.
    id: UUID | None = Field(
        default=None,
        description="Alias for ``run_id`` so the legacy SPA's row.id keys still resolve.",
    )
    started_at: datetime | None = Field(default=None)
    ended_at: datetime | None = Field(default=None)
    status: PhaseStatus | None = Field(default=None)
    profile: str | None = Field(default=None)
    dry_run: bool | None = Field(default=None)
    source: str | None = Field(
        default=None,
        description="Where the run originated (cli/dashboard/scheduler). Always None for new runs until M3+ tracks it.",
    )
    needs_reboot: bool | None = Field(default=None)
    summary: dict | None = Field(
        default=None,
        description="``{'phases': [...]}`` shape consumed by the History tab; legacy SPA only checks ``.phases.length``.",
    )


class RunListResponse(_WireBase):
    runs: list[RunListEntry]
    total: int
