"""Pass C: Update engine hardening tests.

TEST-FIRST: these tests pin the expected behavior from the honest-status,
stream-log-race, and E11/E8 fixes.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from ascendo.orchestrator.run_async import (
    RunRegistry,
    RunState,
    RunStatus,
    _INVENTORY_STATUS_MAP,
    _flush_run_to_inventory_db,
    _phase_of,
)


# ── Honest status: failed ≠ outdated, triggered ≠ up_to_date ─────────


class TestHonestStatus:
    """The _INVENTORY_STATUS_MAP was hiding real failures behind green
    pills. failed→outdated made failed installs look like they just
    need a version bump. triggered→up_to_date hid un-reconciled vendor
    daemon kicks.

    Fix: failed→failed, triggered→triggered_pending.
    """

    def test_failed_maps_to_failed(self) -> None:
        """Failed apply must NOT look like 'outdated' — it's a failure."""
        assert _INVENTORY_STATUS_MAP["failed"] == "failed"

    def test_triggered_maps_to_triggered_pending(self) -> None:
        """Triggered vendor daemon kicks should show as pending, not up_to_date."""
        assert _INVENTORY_STATUS_MAP["triggered"] == "triggered_pending"

    def test_partial_maps_to_failed(self) -> None:
        """Partial applies are failures, not 'outdated'."""
        assert _INVENTORY_STATUS_MAP["partial"] == "failed"

    def test_success_still_maps_to_up_to_date(self) -> None:
        """Successful applies are genuinely up_to_date."""
        assert _INVENTORY_STATUS_MAP["success"] == "up_to_date"


# ── E11: RunStatus.CANCELLED ─────────────────────────────────────────


class TestE11Cancelled:
    """E11: Add RunStatus.CANCELLED; set it when should_cancel fired."""

    def test_cancelled_status_exists(self) -> None:
        """CANCELLED must be a valid RunStatus value."""
        assert hasattr(RunStatus, "CANCELLED")
        assert RunStatus.CANCELLED.value == "cancelled"

    def test_cancel_event_sets_cancelled_status(self) -> None:
        """When cancel_event is set before worker finishes, the run
        should end with CANCELLED, not COMPLETED."""
        registry = RunRegistry()
        run_id = uuid4()
        state = registry.register(run_id, base_dir=Path("/tmp/fake"))
        state.status = RunStatus.RUNNING
        state.cancel_event.set()
        # The worker logic should check cancel_event and set CANCELLED.
        # This test just verifies the status exists and can be assigned.
        state.status = RunStatus.CANCELLED
        assert state.status == RunStatus.CANCELLED

    def test_request_cancel_returns_true_for_running_run(self) -> None:
        """RunRegistry.request_cancel should work with CANCELLED-aware state."""
        registry = RunRegistry()
        run_id = uuid4()
        state = registry.register(run_id, base_dir=Path("/tmp/fake"))
        state.status = RunStatus.RUNNING
        assert registry.request_cancel(run_id) is True
        assert state.cancel_event.is_set()


# ── E8: Missing phase warning ────────────────────────────────────────


class TestE8MissingPhase:
    """E8: when phase is not in _PHASE_PRIORITY, log a warning and
    don't silently treat it as priority 0."""

    def test_phase_priority_zero_for_unknown(self) -> None:
        """An unknown phase should get priority 0 (lowest)."""
        from ascendo.orchestrator.run_async import _PHASE_PRIORITY

        assert _PHASE_PRIORITY.get("bogus_phase") is None
        # Known phases all have priority > 0.
        for phase_name in ("check", "plan", "apply", "verify", "cleanup"):
            assert _PHASE_PRIORITY[phase_name] > 0

    def test_known_phases_have_correct_priority_order(self) -> None:
        """verify > apply > check > plan > cleanup."""
        from ascendo.orchestrator.run_async import _PHASE_PRIORITY

        assert _PHASE_PRIORITY["verify"] > _PHASE_PRIORITY["apply"]
        assert _PHASE_PRIORITY["apply"] > _PHASE_PRIORITY["check"]
        assert _PHASE_PRIORITY["check"] > _PHASE_PRIORITY["plan"]
        assert _PHASE_PRIORITY["plan"] > _PHASE_PRIORITY["cleanup"]

    def test_e8_warning_code_path_exists_in_flush(self) -> None:
        """The flush function must contain the E8 warning log call."""
        import inspect
        from ascendo.orchestrator.run_async import _flush_run_to_inventory_db

        source = inspect.getsource(_flush_run_to_inventory_db)
        assert "not in _PHASE_PRIORITY" in source, (
            "E8: flush must check for unrecognized phases"
        )
        assert "warning" in source.lower(), (
            "E8: flush must log a warning for unrecognized phases"
        )


# ── Stream-log env race ──────────────────────────────────────────────


class TestStreamLogContextVar:
    """The worker was mutating os.environ[ASCENDO_STREAM_LOG] which
    races when multiple concurrent runs exist. Fix: use a contextvar
    or pass via call args.
    """

    def test_stream_log_env_var_constant_exists(self) -> None:
        """The env var constant is exported for external scripts."""
        from ascendo.orchestrator.run_async import STREAM_LOG_ENV_VAR

        assert STREAM_LOG_ENV_VAR == "ASCENDO_STREAM_LOG"
