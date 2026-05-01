"""Top-level `Sidecar` model — the JSON v1 contract.

A sidecar is emitted to disk by a native script (Layer 6) at the end of
each phase. The Python adapter (Layer 5) reads it and validates it
against this model. The orchestrator (Layer 4) operates on the result.

Schema name: ``ascendo/v1`` (canonical) or ``ubuntu-aktualizacje/v1``
(legacy, accepted for backward compatibility — see ADR-0003).

**Required vs. optional.** The `Required` rows of the table in ADR-0003
are mirrored here as fields with no default. Optional fields have
defaults (None or empty list/dict).
"""

from __future__ import annotations

import json
from datetime import datetime
from enum import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from .host import HostInfo
from .package import SourceType
from .result import Item, Message, Summary
from .run import Phase, PhaseStatus, RunInfo


class SidecarSchema(str, Enum):
    """Recognized sidecar schema identifiers.

    The reader accepts both during the migration period. New emitters
    write only `V1_ASCENDO`. See ADR-0003 for the deprecation timeline.
    """

    V1_ASCENDO = "ascendo/v1"
    V1_LEGACY_UBUNTU = "ubuntu-aktualizacje/v1"


# Tool name (e.g. 'apt', 'winget', 'brew', 'softwareupdate', 'dcu-cli').
ToolName = Annotated[str, StringConstraints(min_length=1, max_length=64, strip_whitespace=True)]


class ToolInfo(BaseModel):
    """Identity of the tool that performed this phase.

    Distinct from `HostInfo.os` because one OS may host multiple tools
    (Linux can have apt + snap + flatpak + brew). Sidecars are 1:1 with
    a tool execution.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: ToolName = Field(
        description="Lowercase tool slug. Also used as `category` in items[].",
    )
    version: str = Field(
        description="Tool's self-reported version (e.g. 'apt 2.7.14', 'winget v1.28.240').",
    )
    binary_path: str | None = Field(
        default=None,
        description=(
            "Absolute path to the tool's binary (resolved at run time). "
            "Optional — useful for audit when multiple installs of the same "
            "tool exist on PATH."
        ),
    )


class Sidecar(BaseModel):
    """One emitted phase result. The unit of persistence in `runs/<run-id>/`.

    Naming: stored on disk as ``<run-id>/<phase>__<category>.json``.
    Example: ``2026-05-01-r-1a2b/apply__winget.json``.

    Frozen — sidecars are immutable historical records.
    """

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        # Pydantic v2: serialize datetimes as ISO-8601 strings to match what
        # the Bash + PowerShell emitters produce.
        ser_json_timedelta="iso8601",
        validate_assignment=True,
    )

    # ── Schema identity ──────────────────────────────────────────────
    schema_: SidecarSchema = Field(
        alias="schema",
        description="Schema identifier. Reader accepts both v1 variants.",
    )

    # ── Required top-level blocks ────────────────────────────────────
    run: RunInfo
    host: HostInfo
    tool: ToolInfo
    phase: Phase
    category: SourceType = Field(
        description=(
            "Source category this sidecar describes. e.g. 'apt', 'winget'. "
            "Mirrors `tool.name` in the common case but can differ when a "
            "single tool drives multiple categories (e.g. brew → formula + cask)."
        ),
    )

    started_at: datetime
    finished_at: datetime
    status: PhaseStatus

    items: list[Item] = Field(
        default_factory=list,
        description="Per-package operations attempted in this phase.",
    )
    summary: Summary

    # ── Optional ─────────────────────────────────────────────────────
    messages: list[Message] = Field(
        default_factory=list,
        description="Phase-level log lines not associated with a specific item.",
    )

    # Schema-version tag (legacy alias). The default literal here lets the
    # legacy emitters (Ubuntu_Aktualizacje pre-rename) round-trip cleanly:
    # they emit `"schema": "ubuntu-aktualizacje/v1"` and we still parse it.
    @model_validator(mode="after")
    def _check_schema_recognized(self) -> "Sidecar":
        # Pydantic Enum validator already enforces the value; this is a
        # belt-and-suspenders check to surface a friendlier error if a
        # future schema slips through.
        if self.schema_ not in (SidecarSchema.V1_ASCENDO, SidecarSchema.V1_LEGACY_UBUNTU):
            msg = f"Unsupported sidecar schema: {self.schema_!r}"
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _check_finished_after_started(self) -> "Sidecar":
        if self.finished_at < self.started_at:
            msg = (
                f"finished_at ({self.finished_at}) must be >= started_at "
                f"({self.started_at})"
            )
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _check_summary_consistent(self) -> "Sidecar":
        """Summary counts must match items[] cardinality."""
        if self.summary.total != len(self.items):
            msg = (
                f"summary.total={self.summary.total} != len(items)={len(self.items)}"
            )
            raise ValueError(msg)
        return self

    def is_legacy_schema(self) -> bool:
        """True if this sidecar was emitted by a pre-rename codebase."""
        return self.schema_ is SidecarSchema.V1_LEGACY_UBUNTU


# ── Convenience reader (for tests + ad-hoc inspection) ────────────────
# The full reader lives in core.ascendo.orchestrator.sidecar_reader and adds
# file-system handling, locking, and error recovery. This minimal helper is
# safe for test fixtures.

def parse_sidecar(payload: str | bytes | dict) -> Sidecar:  # noqa: D401 (imperative)
    """Parse a JSON string / bytes / pre-loaded dict into a Sidecar model.

    Accepts both schema variants:
      - ``ascendo/v1`` — passed straight to :meth:`Sidecar.model_validate`.
      - ``ubuntu-aktualizacje/v1`` — translated via
        :mod:`ascendo.models.legacy` first, then validated as ascendo/v1.

    The legacy translation is applied unconditionally when the schema
    string matches; this is the public entry point for sidecar parsing
    and is the only place that handles both shapes.
    :meth:`Sidecar.model_validate_json` (used internally elsewhere)
    deliberately stays strict — only the canonical shape is accepted.

    Raises:
        pydantic.ValidationError: if the payload (after optional legacy
            translation) doesn't match the v1 schema.
        json.JSONDecodeError: if ``payload`` is bytes/str that aren't
            valid JSON.
    """
    # Local import to keep module import graph acyclic.
    from .legacy import is_legacy_v1, translate_legacy_v1

    if isinstance(payload, dict):
        data: dict = payload
    else:
        data = json.loads(payload)

    if is_legacy_v1(data):
        data = translate_legacy_v1(data)

    return Sidecar.model_validate(data)


# Type alias for v1 reader's accept-both-schemas contract — exported for
# clarity at use-sites (e.g. core.dashboard).
SidecarSchemaLiteral = Literal["ascendo/v1", "ubuntu-aktualizacje/v1"]
