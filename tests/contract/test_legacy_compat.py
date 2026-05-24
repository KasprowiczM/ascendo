"""Tests for the legacy ascendo/v1 -> ascendo/v1 translator.

These tests don't go through :class:`Sidecar` for the unit cases —
they exercise :func:`translate_legacy_v1` directly, then a couple of
integration tests parse the output through :func:`parse_sidecar` to
confirm the produced dict satisfies the Pydantic model.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest

from ascendo.models.legacy import (
    is_legacy_v1,
    translate_legacy_v1,
)
from ascendo.models.sidecar import parse_sidecar


def test_is_legacy_v1_detects_schema_string(
    sample_legacy_v1_apt_check: dict[str, Any],
) -> None:
    """The detector matches the legacy schema literal."""
    assert is_legacy_v1(sample_legacy_v1_apt_check)


def test_is_legacy_v1_rejects_modern(
    sample_ascendo_v1_apply_winget: dict[str, Any],
) -> None:
    """A modern ascendo/v1 payload is not flagged as legacy."""
    assert not is_legacy_v1(sample_ascendo_v1_apply_winget)
    # Non-dicts should also return False, not raise.
    assert not is_legacy_v1(None)  # type: ignore[arg-type]
    assert not is_legacy_v1("ascendo/v1")  # type: ignore[arg-type]


def test_translate_synthesizes_run_id_stably(
    sample_legacy_v1_apt_check: dict[str, Any],
) -> None:
    """uuid5 over (host, started_at) is deterministic — same input, same id."""
    a = translate_legacy_v1(copy.deepcopy(sample_legacy_v1_apt_check))
    b = translate_legacy_v1(copy.deepcopy(sample_legacy_v1_apt_check))
    assert a["run"]["id"] == b["run"]["id"]

    # And changing started_at changes the id.
    mutated = copy.deepcopy(sample_legacy_v1_apt_check)
    mutated["started_at"] = "2099-01-01T00:00:00Z"
    c = translate_legacy_v1(mutated)
    assert c["run"]["id"] != a["run"]["id"]


def test_translate_maps_exit_code_zero_to_success(
    sample_legacy_v1_apt_check: dict[str, Any],
) -> None:
    """exit_code = 0 -> status='success'."""
    payload = copy.deepcopy(sample_legacy_v1_apt_check)
    payload["exit_code"] = 0
    translated = translate_legacy_v1(payload)
    assert translated["status"] == "success"


def test_translate_maps_nonzero_exit_code_to_failed(
    sample_legacy_v1_npm_apply: dict[str, Any],
) -> None:
    """exit_code >= 2 -> status='failed' (1 is legacy 'warn' which is success-with-advisories;
    75 is 'already-running' which maps to skipped). See docs/agents/contract.md."""
    payload = copy.deepcopy(sample_legacy_v1_npm_apply)
    payload["exit_code"] = 20  # apply-fail-known
    translated = translate_legacy_v1(payload)
    assert translated["status"] == "failed"

    payload["exit_code"] = 1  # legacy "warn" — sidecar items[] are clean, advisories only
    translated = translate_legacy_v1(payload)
    assert translated["status"] == "success"

    payload["exit_code"] = 75  # already-running
    translated = translate_legacy_v1(payload)
    assert translated["status"] == "skipped"


def test_translate_handles_empty_items(
    sample_legacy_v1_apt_check: dict[str, Any],
) -> None:
    """A legacy payload with items=[] still translates and validates as ascendo/v1."""
    payload = copy.deepcopy(sample_legacy_v1_apt_check)
    payload["items"] = []
    payload["summary"] = {"ok": 0, "warn": 0, "err": 0}
    payload["diagnostics"] = []

    translated = translate_legacy_v1(payload)
    assert translated["items"] == []
    assert translated["summary"]["total"] == 0

    # Round-trip through Pydantic — a no-items legacy payload is valid.
    sidecar = parse_sidecar(payload)
    assert sidecar.summary.total == 0
    assert sidecar.items == []


def test_translate_handles_diagnostics_to_messages(
    sample_legacy_v1_apt_check: dict[str, Any],
) -> None:
    """Each legacy diagnostic surfaces as a top-level Message with code prefix."""
    translated = translate_legacy_v1(sample_legacy_v1_apt_check)
    msgs = translated["messages"]
    assert len(msgs) == 2

    levels = {m["level"] for m in msgs}
    assert levels == {"warn", "info"}

    # Code prefix preserved in text so operators don't lose information.
    texts = [m["text"] for m in msgs]
    assert any(t.startswith("[DRIFT-CODE] ") for t in texts)
    assert any(t.startswith("[MIRROR-OK] ") for t in texts)


@pytest.mark.parametrize(
    ("legacy_result", "ascendo_status"),
    [
        ("ok", "success"),
        ("warn", "skipped"),
        ("skipped", "skipped"),
        ("failed", "failed"),
        ("noop", "up_to_date"),
    ],
)
def test_translate_item_result_to_status_mapping(
    sample_legacy_v1_apt_check: dict[str, Any],
    legacy_result: str,
    ascendo_status: str,
) -> None:
    """Item-level result -> status mapping table matches the documented spec."""
    payload = copy.deepcopy(sample_legacy_v1_apt_check)
    payload["items"] = [
        {
            "id": "x",
            "action": "check-update",
            "from": None,
            "to": None,
            "result": legacy_result,
        }
    ]
    payload["summary"] = {"ok": 0, "warn": 0, "err": 0}
    payload["diagnostics"] = []

    translated = translate_legacy_v1(payload)
    assert translated["items"][0]["status"] == ascendo_status


def test_translate_npm_apply_round_trips_through_parse_sidecar(
    sample_legacy_v1_npm_apply: dict[str, Any],
) -> None:
    """Full integration check: legacy npm apply -> ascendo Sidecar instance.

    Note: fixture uses exit_code=1 (legacy 'warn' = success-with-advisories);
    per-item failures surface in items[] regardless of overall sidecar status.
    """
    sidecar = parse_sidecar(sample_legacy_v1_npm_apply)
    assert sidecar.tool.name == "npm"
    assert sidecar.category.value == "npm"
    assert sidecar.phase.value == "apply"
    # exit_code=1 = warn = success at the run level; the failing item is still
    # surfaced via items[] for the orchestrator's per-item view.
    assert sidecar.status.value == "success"

    # Per-item details surfaced as info-level message under the failed item.
    failed = next(i for i in sidecar.items if i.status.value == "failed")
    assert any("EACCES" in m.text for m in failed.messages)
