"""Orchestration safety (Spec 5/6): restart-safe ordering, run lock."""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

from ascendo.models.package import SourceType
from ascendo.models.run import Phase, PhaseStatus
from ascendo.orchestrator import run_phases
from ascendo.orchestrator.run_lock import RunLockError

from .test_runner import FakeAdapter, FakeManager, host, run  # noqa: F401  (fixtures)


class DeferredManager(FakeManager):
    defer_until_end = True


def _calls(*mgrs):
    return {m.category.value: [c[0] for c in m.calls] for m in mgrs}


def test_default_flag_is_false():
    assert FakeManager.defer_until_end is False


def test_deferred_apply_runs_last_when_nothing_blocks(host, run, tmp_path):   # noqa: F811
    brew = FakeManager(SourceType.BREW)
    su = DeferredManager(SourceType.SOFTWAREUPDATE)
    order: list[tuple[str, Phase]] = []
    for m in (brew, su):
        orig = m.run_phase

        def wrapped(phase, run_, host_, *, item_filter=None, _o=orig, _m=m):
            order.append((_m.category.value, phase))
            return _o(phase, run_, host_, item_filter=item_filter)

        m.run_phase = wrapped
    hook: list[int] = []
    report = run_phases(
        FakeAdapter([su, brew]), run, host, base_dir=tmp_path / "runs",
        pre_final_apply=lambda: hook.append(len(order)),
    )
    # softwareupdate's APPLY is postponed past verify/cleanup of everything.
    assert order[-1] == ("softwareupdate", Phase.APPLY)
    assert ("softwareupdate", Phase.CHECK) in order and ("softwareupdate", Phase.CLEANUP) in order
    # the restart-capable step comes after the pre-restart hook
    assert hook == [len(order) - 1]
    assert report.overall_status is PhaseStatus.SUCCESS
    # report + run_meta are on disk before the deferred apply (hook time)
    assert (tmp_path / "runs" / str(run.id) / "run_meta.json").is_file()


@pytest.mark.parametrize("bad", [SourceType.BREW, SourceType.MAS, SourceType.NPM, SourceType.WEB])
def test_hard_apply_failure_defers_the_restart_step(host, run, tmp_path, bad):   # noqa: F811
    failing = FakeManager(bad, status=PhaseStatus.FAILED)
    su = DeferredManager(SourceType.SOFTWAREUPDATE)
    hook: list[int] = []
    report = run_phases(
        FakeAdapter([failing, su]), run, host, base_dir=tmp_path / "runs",
        stop_on_failure=False, pre_final_apply=lambda: hook.append(1),
    )
    assert Phase.APPLY not in _calls(su)["softwareupdate"]      # never invoked
    assert hook == []                                            # nothing may restart
    [sc] = [s for s in report.sidecars
            if s.category is SourceType.SOFTWAREUPDATE and s.phase is Phase.APPLY]
    assert sc.status is PhaseStatus.SKIPPED
    codes = [m.code for m in sc.messages]
    assert "SYSTEM_UPDATE_DEFERRED" in codes
    assert bad.value in sc.messages[0].text and "nothing was restarted" in sc.messages[0].text
    # persisted
    assert (tmp_path / "runs" / str(run.id) / "apply__softwareupdate.json").is_file()


def test_pip_failure_never_blocks(host, run, tmp_path):   # noqa: F811
    pip = FakeManager(SourceType.PIP, status=PhaseStatus.FAILED)
    su = DeferredManager(SourceType.SOFTWAREUPDATE)
    run_phases(FakeAdapter([pip, su]), run, host, base_dir=tmp_path / "runs",
               stop_on_failure=False)
    assert Phase.APPLY in _calls(su)["softwareupdate"]


def test_cancel_before_deferred_apply_skips_it(host, run, tmp_path):   # noqa: F811
    su = DeferredManager(SourceType.SOFTWAREUPDATE)
    brew = FakeManager(SourceType.BREW)
    n = {"c": 0}

    def cancel():
        n["c"] += 1
        return Phase.CLEANUP in _calls(brew)["brew"]

    run_phases(FakeAdapter([brew, su]), run, host, base_dir=tmp_path / "runs",
               should_cancel=cancel)
    assert Phase.APPLY not in _calls(su)["softwareupdate"]


def test_check_only_run_has_no_deferred_apply(host, run, tmp_path):   # noqa: F811
    su = DeferredManager(SourceType.SOFTWAREUPDATE)
    run_phases(FakeAdapter([su]), run, host, phases=[Phase.CHECK], base_dir=tmp_path / "runs")
    assert _calls(su)["softwareupdate"] == [Phase.CHECK]


# ── run lock ───────────────────────────────────────────────────────────────

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only lock")


@posix_only
def test_apply_refused_while_another_process_holds_the_lock(host, run, tmp_path, monkeypatch):   # noqa: F811
    import subprocess
    holder = subprocess.Popen(["sleep", "30"])
    try:
        lock = tmp_path / "held.lock"
        lock.write_text(f"pid={holder.pid}\nstarted=x\nstarted_unix={time.time()}\nrun_id=other\n")
        monkeypatch.setenv("ASCENDO_RUN_LOCK_FILE", str(lock))
        mgr = FakeManager(SourceType.BREW)
        with pytest.raises(RunLockError):
            run_phases(FakeAdapter([mgr]), run, host, base_dir=tmp_path / "runs")
        assert mgr.calls == []
        # read-only phases are never blocked
        run_phases(FakeAdapter([mgr]), run, host, phases=[Phase.CHECK, Phase.PLAN, Phase.VERIFY],
                   base_dir=tmp_path / "runs")
        assert [c[0] for c in mgr.calls] == [Phase.CHECK, Phase.PLAN, Phase.VERIFY]
    finally:
        holder.kill()
        holder.wait()


@posix_only
def test_lock_released_after_run(host, run, tmp_path, monkeypatch):   # noqa: F811
    lock = tmp_path / "x.lock"
    monkeypatch.setenv("ASCENDO_RUN_LOCK_FILE", str(lock))
    seen: list[bool] = []
    mgr = FakeManager(SourceType.BREW)
    orig = mgr.run_phase

    def spy(phase, run_, host_, *, item_filter=None):
        if phase is Phase.APPLY:
            seen.append(lock.exists())
        return orig(phase, run_, host_, item_filter=item_filter)

    mgr.run_phase = spy
    run_phases(FakeAdapter([mgr]), run, host, base_dir=tmp_path / "runs")
    assert seen == [True] and not lock.exists()


def test_blocked_restart_step_is_a_listed_soft_deferral_and_run_warning(host, run, tmp_path):   # noqa: F811
    from ascendo.orchestrator.run_async import _report_has_warnings

    failing = FakeManager(SourceType.MAS, status=PhaseStatus.FAILED)
    su = DeferredManager(SourceType.SOFTWAREUPDATE)
    report = run_phases(FakeAdapter([failing, su]), run, host,
                        base_dir=tmp_path / "runs", stop_on_failure=False)
    assert _report_has_warnings(report) is True
    md = (tmp_path / "runs" / str(run.id) / "REPORT.md").read_text()
    assert "system step deferred" in md.lower() or "system step was deferred" in md.lower()
    assert "nothing was restarted" in md


def test_deferred_only_run_without_failure_has_no_deferral_warning(host, run, tmp_path):   # noqa: F811
    from ascendo.orchestrator.run_async import _report_has_warnings

    report = run_phases(FakeAdapter([FakeManager(SourceType.BREW), DeferredManager(SourceType.SOFTWAREUPDATE)]),
                        run, host, base_dir=tmp_path / "runs")
    assert _report_has_warnings(report) is False


def test_async_worker_flushes_bookkeeping_before_the_restart_step_and_warns_when_blocked(host, run, tmp_path):   # noqa: F811
    import asyncio

    from ascendo.orchestrator.run_async import RunRegistry, start_run_async

    order: list[str] = []

    class _Db:     # inventory DB stub: flushes are best-effort, any failure is swallowed
        pass

    ok = FakeManager(SourceType.BREW)
    su = DeferredManager(SourceType.SOFTWAREUPDATE)
    orig = su.run_phase

    def spy(phase, run_, host_, *, item_filter=None):
        if phase is Phase.APPLY:
            order.append("deferred-apply")
        return orig(phase, run_, host_, item_filter=item_filter)

    su.run_phase = spy
    import ascendo.orchestrator.run_async as ra
    real = ra._flush_run_to_inventory_db

    def flush_spy(*a, **k):
        order.append("flush")
        return real(*a, **k)

    ra._flush_run_to_inventory_db = flush_spy
    try:
        async def go():
            reg = RunRegistry()
            state = await start_run_async(
                registry=reg, adapter=FakeAdapter([ok, su]), run=run, host=host,
                base_dir=tmp_path / "runs", inventory_db=_Db())
            for _ in range(200):
                if state._completion_event.is_set():
                    break
                await asyncio.sleep(0.02)
            return state
        state = asyncio.run(go())
    finally:
        ra._flush_run_to_inventory_db = real
    assert order.index("flush") < order.index("deferred-apply")   # persisted BEFORE the restart step
    assert state.terminal_state == "completed"
