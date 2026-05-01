"""Contract tests for the ascendo/v1 sidecar shape.

Covers:
  - canonical round-trip (dict -> Sidecar -> dict -> Sidecar)
  - JSON-string round-trip via :meth:`Sidecar.model_validate_json`
  - legacy ``ubuntu-aktualizacje/v1`` payload acceptance through
    :func:`parse_sidecar`
  - cross-field validators (summary totals, time ordering)
  - schema-level rejections (unknown schema, extra top-level fields)
  - dry-run support (``planned`` items)
"""

from __future__ import annotations

import copy
import json
from typing import Any

import pytest
from pydantic import ValidationError

from ascendo.models.sidecar import (
    Sidecar,
    SidecarSchema,
    parse_sidecar,
)


def test_round_trip_ascendo_v1(sample_ascendo_v1_apply_winget: dict[str, Any]) -> None:
    """dict -> model -> dict -> model preserves all fields."""
    first = parse_sidecar(sample_ascendo_v1_apply_winget)
    dumped = first.model_dump(by_alias=True, mode="json")
    second = parse_sidecar(dumped)

    assert first == second
    assert second.schema_ is SidecarSchema.V1_ASCENDO
    assert second.summary.total == len(second.items)


def test_round_trip_via_model_validate_json(
    sample_ascendo_v1_apply_winget: dict[str, Any],
) -> None:
    """JSON-string round-trip via the strict entry point still works."""
    raw = json.dumps(sample_ascendo_v1_apply_winget)
    sidecar = Sidecar.model_validate_json(raw)
    again = Sidecar.model_validate_json(sidecar.model_dump_json(by_alias=True))
    assert sidecar == again


def test_legacy_v1_translates_to_ascendo_v1(
    sample_legacy_v1_apt_check: dict[str, Any],
) -> None:
    """Feeding a legacy payload to parse_sidecar produces a valid Sidecar."""
    sidecar = parse_sidecar(sample_legacy_v1_apt_check)
    # After translation the schema is rewritten to canonical ascendo/v1.
    assert sidecar.schema_ is SidecarSchema.V1_ASCENDO
    assert sidecar.phase.value == "check"
    assert sidecar.category.value == "apt"
    # Legacy host string lifted into HostInfo.hostname.
    assert sidecar.host.hostname == "ubuntu-desktop"


def test_legacy_field_mappings_preserved(
    sample_legacy_v1_apt_check: dict[str, Any],
) -> None:
    """Concrete spot-checks of the legacy -> ascendo field rewrites."""
    sidecar = parse_sidecar(sample_legacy_v1_apt_check)

    # summary.ok (12) -> summary.success
    assert sidecar.summary.success == 12
    # summary.warn (2) -> summary.skipped (lossy mapping; documented)
    assert sidecar.summary.skipped == 2
    # summary.err (0) -> summary.failed
    assert sidecar.summary.failed == 0
    # summary.total synthesized from items length, not from legacy ok+warn+err
    assert sidecar.summary.total == len(sidecar.items)

    # exit_code 0 -> status success
    assert sidecar.status.value == "success"

    # ended_at -> finished_at
    assert sidecar.finished_at.isoformat().startswith("2026-04-29T00:00:15")

    # diagnostics[] surfaces as top-level messages[]; codes prefixed.
    assert any("DRIFT-CODE" in m.text for m in sidecar.messages)
    assert any("MIRROR-OK" in m.text for m in sidecar.messages)


def test_summary_items_consistency_validator_rejects_mismatch(
    sample_ascendo_v1_apply_winget: dict[str, Any],
) -> None:
    """Summary.total != len(items) is rejected by the model validator."""
    payload = copy.deepcopy(sample_ascendo_v1_apply_winget)
    payload["summary"]["total"] = 99  # there are only 2 items
    with pytest.raises(ValidationError):
        parse_sidecar(payload)


def test_reverse_time_validator_rejects(
    sample_ascendo_v1_apply_winget: dict[str, Any],
) -> None:
    """finished_at < started_at must be rejected."""
    payload = copy.deepcopy(sample_ascendo_v1_apply_winget)
    # Swap so finished_at precedes started_at.
    payload["started_at"], payload["finished_at"] = (
        payload["finished_at"],
        payload["started_at"],
    )
    with pytest.raises(ValidationError):
        parse_sidecar(payload)


def test_unknown_schema_rejected(
    sample_ascendo_v1_apply_winget: dict[str, Any],
) -> None:
    """Schema 'ascendo/v2' is not in the SidecarSchema enum -> ValidationError."""
    payload = copy.deepcopy(sample_ascendo_v1_apply_winget)
    payload["schema"] = "ascendo/v2"
    with pytest.raises(ValidationError):
        parse_sidecar(payload)


def test_strict_no_extra_fields(
    sample_ascendo_v1_apply_winget: dict[str, Any],
) -> None:
    """An extra top-level field must trip extra='forbid'."""
    payload = copy.deepcopy(sample_ascendo_v1_apply_winget)
    payload["unexpected_top_level"] = {"oops": True}
    with pytest.raises(ValidationError):
        parse_sidecar(payload)


def test_dry_run_planned_status(
    sample_ascendo_v1_check_apt: dict[str, Any],
) -> None:
    """A check phase with status=planned items round-trips cleanly."""
    sidecar = parse_sidecar(sample_ascendo_v1_check_apt)
    assert sidecar.summary.planned == 1
    assert sidecar.items[0].status.value == "planned"
    # Check phases keep status='success' even when items are planned —
    # the run.dry_run flag is the dry-run signal at the run level.
    assert sidecar.status.value == "success"
