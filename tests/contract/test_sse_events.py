"""Tests for Task 2.3 — phase + attention SSE events + enriched done.

Verifies the ``GET /runs/{id}/events`` stream:
  - emits first-class ``event: phase`` frames (durable — derived from the
    on-disk sidecars so a client that connects post-hoc still replays them),
  - emits a ``done`` frame carrying ``state`` / ``counts`` / ``needs_reboot``,
  - emits a terminal ``done`` for a CANCELLED run and CLOSES the stream
    (the pre-2.3 generator only treated COMPLETED/FAILED as terminal, so a
    cancelled run looped forever).

The fake-adapter machinery is reused from the sibling lifecycle test.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from fastapi.testclient import TestClient

from ascendo.dashboard import create_app

from .test_run_async_lifecycle import (
    _make_adapter,
    _make_sidecar,
    _skipped_item,
    _WarnMgr,
)


def _collect_sse(client: TestClient, run_id: str, timeout: float = 8.0) -> list[dict]:
    """Stream SSE frames until ``event: done`` (or ``timeout``), then parse.

    Reads in a daemon thread guarded by a wall-clock deadline so a regression
    where ``done`` never fires (e.g. CANCELLED not treated as terminal) fails
    fast instead of hanging the suite.
    """
    raw = bytearray()
    finished = threading.Event()

    def _read() -> None:
        try:
            with client.stream("GET", f"/runs/{run_id}/events") as resp:
                for chunk in resp.iter_bytes():
                    raw.extend(chunk)
                    if b"event: done" in bytes(raw):
                        break
        finally:
            finished.set()

    th = threading.Thread(target=_read, daemon=True)
    th.start()
    finished.wait(timeout)

    frames: list[dict] = []
    for block in bytes(raw).decode("utf-8", "replace").split("\n\n"):
        ev = None
        data: object = None
        for line in block.splitlines():
            if line.startswith("event:"):
                ev = line[len("event:"):].strip()
            elif line.startswith("data:"):
                payload = line[len("data:"):].strip()
                try:
                    data = json.loads(payload)
                except ValueError:
                    data = payload
        if ev:
            frames.append({"event": ev, "data": data})
    return frames


def test_sse_emits_phase_events_and_enriched_done(tmp_path: Path) -> None:
    """A 2-phase run emits per-phase ``phase`` events + a ``done`` frame whose
    payload carries ``counts`` (updated/deferred/warned/failed), ``state``,
    and ``needs_reboot``.
    """
    app = create_app(adapter=_make_adapter(_WarnMgr()), runs_dir=tmp_path)
    client = TestClient(app)
    r = client.post("/runs/async", json={"phases": ["check", "plan"]})
    assert r.status_code == 202
    run_id = r.json()["run_id"]

    frames = _collect_sse(client, run_id)

    phase_frames = [f for f in frames if f["event"] == "phase"]
    assert phase_frames, f"no phase events in stream: {[f['event'] for f in frames]}"
    first = phase_frames[0]["data"]
    assert isinstance(first, dict)
    assert "phase" in first and "total" in first, f"phase frame missing keys: {first!r}"
    # Two phases requested → both announced.
    seen_phases = {f["data"]["phase"] for f in phase_frames}
    assert {"check", "plan"} <= seen_phases, f"expected check+plan, got {seen_phases}"

    done = [f for f in frames if f["event"] == "done"]
    assert done, "no done event"
    d = done[-1]["data"]
    assert isinstance(d, dict)
    assert isinstance(d.get("counts"), dict), f"done.counts not a dict: {d!r}"
    for k in ("updated", "deferred", "warned", "failed"):
        assert k in d["counts"], f"done.counts missing {k}: {d['counts']!r}"
    # _WarnMgr emits one skipped item per phase → deferred should count them.
    assert d["counts"]["deferred"] >= 1, f"expected deferred>=1: {d['counts']!r}"
    assert "state" in d, "done missing refined terminal state"
    assert d["state"] == "completed_with_warnings", f"unexpected state: {d!r}"
    assert "needs_reboot" in d, "done missing needs_reboot"
    assert d["needs_reboot"] is False


def test_sse_cancelled_run_emits_done_and_closes(tmp_path: Path) -> None:
    """A CANCELLED run still emits a terminal ``done`` (state='cancelled') and
    closes the stream — pre-2.3 the generator looped forever on CANCELLED.

    POST /runs/async blocks until the worker finishes under TestClient, so the
    cancel is driven from a side thread (mirrors test_run_async_lifecycle).
    """
    from ascendo.interfaces.package_manager import IPackageManager
    from ascendo.models.host import HostInfo
    from ascendo.models.package import SourceType
    from ascendo.models.sidecar import Sidecar

    started = threading.Event()
    release = threading.Event()

    class _BlockingMgr(IPackageManager):
        @property
        def category(self) -> SourceType:
            return SourceType.WINGET

        @property
        def display_name(self) -> str:
            return "fake-blocking"

        def is_available(self, host: HostInfo) -> bool:
            return True

        def run_phase(self, phase, run, host, *, item_filter=None) -> Sidecar:
            started.set()
            release.wait(timeout=8.0)
            return _make_sidecar(run, host, phase=phase)

    app = create_app(adapter=_make_adapter(_BlockingMgr()), runs_dir=tmp_path)
    client = TestClient(app)

    def _canceller() -> None:
        if not started.wait(timeout=5.0):
            return
        reg = app.state.run_registry
        for rid in reg.all_running():
            st = reg.get(rid)
            if st is not None:
                st.cancel_event.set()
        release.set()

    t = threading.Thread(target=_canceller, daemon=True)
    t.start()
    r = client.post("/runs/async", json={"phases": ["check"]})  # blocks until done
    run_id = r.json()["run_id"]
    t.join(timeout=2.0)

    # Run is cancelled + finished now; a post-hoc SSE connection must still
    # reach a terminal done frame and close (not hang).
    frames = _collect_sse(client, run_id, timeout=6.0)
    done = [f for f in frames if f["event"] == "done"]
    assert done, "CANCELLED run never emitted a done event (stream hung)"
    assert done[-1]["data"].get("state") == "cancelled", (
        f"expected done.state='cancelled', got {done[-1]['data']!r}"
    )
