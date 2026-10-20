"""Contract tests for B4 runner hardening.

1. Interim _finalize crash does not block deferred softwareupdate apply.
2. Async worker crash writes minimal run_meta.json.
3. _write_run_meta catches any Exception, not just OSError.
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import patch

import ascendo.orchestrator.runner as runner_module
from ascendo.models.package import SourceType
from ascendo.models.run import Phase, PhaseStatus
from ascendo.orchestrator import run_phases
from ascendo.orchestrator.run_async import RunRegistry, RunStatus, start_run_async
from ascendo.orchestrator.runner import RunReport, _write_run_meta

from .test_runner import FakeAdapter, FakeManager, host, run  # noqa: F401 (fixtures)
from .test_runner_safety import DeferredManager


def test_interim_finalize_crash_does_not_block_deferred_apply(host, run, tmp_path, monkeypatch):  # noqa: F811
    """B4: if report generation / metadata write raises during interim _finalize,
    the deferred softwareupdate apply must still run."""
    brew = FakeManager(SourceType.BREW)
    su = DeferredManager(SourceType.SOFTWAREUPDATE)

    calls = []
    real_write_run_meta = runner_module._write_run_meta

    def crashing_write_run_meta(*args, **kwargs):
        calls.append(args)
        if len(calls) == 1:
            raise RuntimeError("simulated interim finalize crash")
        return real_write_run_meta(*args, **kwargs)

    monkeypatch.setattr(runner_module, "_write_run_meta", crashing_write_run_meta)

    report = run_phases(
        FakeAdapter([brew, su]),
        run,
        host,
        base_dir=tmp_path / "runs",
    )

    su_apply = [
        sc for sc in report.sidecars
        if sc.category == SourceType.SOFTWAREUPDATE and sc.phase == Phase.APPLY
    ]
    assert len(su_apply) == 1
    assert su_apply[0].status == PhaseStatus.SUCCESS


async def test_async_worker_crash_writes_failed_run_meta(host, run, tmp_path):  # noqa: F811
    """B4: when async worker crashes unexpectedly, it writes a minimal run_meta.json."""
    class CrashingManager(FakeManager):
        def run_phase(self, phase, run_, host_, *, item_filter=None):
            raise RuntimeError("fatal worker crash")

    adapter = FakeAdapter([CrashingManager(SourceType.BREW)])
    registry = RunRegistry()

    state = await start_run_async(
        registry=registry,
        adapter=adapter,
        run=run,
        host=host,
        base_dir=tmp_path / "runs",
    )

    # Wait for completion
    for _ in range(100):
        if state._completion_event.is_set():
            break
        await asyncio.sleep(0.05)

    assert state.status == RunStatus.FAILED
    assert "fatal worker crash" in (state.error or "")

    run_meta_path = tmp_path / "runs" / str(run.id) / "run_meta.json"
    assert run_meta_path.is_file(), "run_meta.json was not created on worker crash"
    data = json.loads(run_meta_path.read_text(encoding="utf-8"))
    assert data["status"] == "failed"
    assert "fatal worker crash" in data.get("error", "")


def test_write_run_meta_catches_all_exceptions(tmp_path, run, host):  # noqa: F811
    """B4: _write_run_meta catches Exception, not just OSError."""
    report = RunReport(run=run, host=host, sidecars=[])
    # An object that causes TypeError during json.dumps
    with patch("ascendo.orchestrator.runner._log.exception") as mock_log:
        _write_run_meta(
            tmp_path / "runs" / str(run.id),
            report=report,
            cancelled=False,
            extra_skipped=[("cat", object())],  # type: ignore[list-item]
        )
        assert mock_log.called
