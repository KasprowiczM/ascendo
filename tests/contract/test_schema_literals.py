"""I1/I3: Schema literal distinctness + legacy warn→skipped mapping tests.

I1: The legacy schema literal "ubuntu-aktualizacje/v1" MUST stay distinct
    from canonical "ascendo/v1". Collapsing them caused a production outage.

I3: The legacy "warn" status maps to "skipped" (lossy but documented).
    This mapping must be pinned by a test.
"""
from __future__ import annotations


def test_i1_legacy_schema_distinct_from_canonical() -> None:
    """I1: Legacy schema literal must never equal canonical."""
    from ascendo.models.sidecar import SidecarSchema

    legacy = SidecarSchema.V1_LEGACY_UBUNTU.value
    canonical = SidecarSchema.V1_ASCENDO.value
    assert legacy != canonical, (
        f"I1 REGRESSION: legacy schema '{legacy}' must be distinct "
        f"from canonical '{canonical}' — collapsing caused a production outage"
    )
    assert legacy == "ubuntu-aktualizacje/v1"
    assert canonical == "ascendo/v1"


def test_i1_sidecar_schema_literal_includes_both() -> None:
    """The SidecarSchemaLiteral type must accept both schema strings."""
    from ascendo.models.sidecar import SidecarSchemaLiteral

    import typing
    args = typing.get_args(SidecarSchemaLiteral)
    assert "ascendo/v1" in args
    assert "ubuntu-aktualizacje/v1" in args


def test_i1_legacy_module_schema_constant() -> None:
    """The legacy module's _LEGACY_SCHEMA must be the pre-rebrand string."""
    from ascendo.models.legacy import _LEGACY_SCHEMA

    assert _LEGACY_SCHEMA == "ubuntu-aktualizacje/v1", (
        "I1: _LEGACY_SCHEMA must be the historical pre-rebrand string"
    )


def test_i3_legacy_warn_maps_to_skipped() -> None:
    """I3: The legacy 'warn' item result maps to 'skipped' in the
    ascendo vocabulary. This is lossy (warn ≠ skipped semantically)
    but is the documented choice — changing it would break existing
    inventory rows parsed from ubuntu-aktualizacje/v1 sidecars.
    """
    from ascendo.models.legacy import _RESULT_TO_STATUS

    assert _RESULT_TO_STATUS["warn"] == "skipped", (
        "I3: legacy 'warn' must map to 'skipped' — changing this "
        "alters how historic sidecars are interpreted"
    )


def test_i3_legacy_summary_warn_maps_to_skipped_count() -> None:
    """I3: When translating a legacy summary block, the 'warn' count
    becomes the 'skipped' count."""
    from ascendo.models.legacy import translate_legacy_v1

    # Minimal legacy v1 sidecar with a warn count.
    legacy_payload = {
        "schema": "ubuntu-aktualizacje/v1",
        "host": "testhost",
        "kind": "check",
        "category": "apt",
        "started_at": "2026-01-01T00:00:00+00:00",
        "ended_at": "2026-01-01T00:01:00+00:00",
        "exit_code": 0,
        "items": [],
        "summary": {
            "ok": 3,
            "err": 1,
            "warn": 1,
        },
    }
    translated = translate_legacy_v1(legacy_payload)
    summary = translated["summary"]
    assert summary["skipped"] == 1, (
        "I3: legacy summary.warn must become summary.skipped"
    )
