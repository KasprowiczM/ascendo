"""SSE disconnect handling for /ai/chat (Task 19).

The Sesja 70 chat surface returns a turn_id to the SPA + exposes two ways
to stop a turn mid-stream:

  POST /ai/chat/cancel/{turn_id}   - explicit user-clicked cancel
  GET  /ai/chat/stream/{turn_id}   - implicit (client closes the tab)

Both paths must reach the same TurnState.cancel_event, otherwise the
backend keeps streaming tokens nobody sees + the operator burns CLI/API
quota. Before Task 19 the turn_id returned to the SPA didn't match the
one run_turn() registered internally, so cancel always 404'd. Task 19
threads the same state object through so both endpoints resolve.
"""
from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "ai_cli"


class _FakeAdapter:
    name = "fake"

    @property
    def capabilities(self):
        return "PACKAGE_MANAGEMENT|INVENTORY"

    def health_check(self):
        return {"components": []}


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(FIXTURES) + os.pathsep + os.environ.get("PATH", ""))
    monkeypatch.setenv("ASCENDO_HOME", str(tmp_path / ".ascendo"))
    monkeypatch.setenv("ASCENDO_RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setenv("FIXTURE_CASE", "success")
    sys.modules.pop("ascendo.dashboard.app", None)
    from ascendo.dashboard.app import create_app

    return create_app(adapter=_FakeAdapter(), runs_dir=tmp_path / "runs")


def _install_fake_backend(monkeypatch, tokens, *, delay: float = 0.0):
    """Patch BackendResolver.resolve() so post_chat picks our Python fake.

    Uses monkeypatch so the original method is restored at test teardown.
    Otherwise the patched method leaks into subsequent tests (e.g.
    test_ai_cli_drivers) that exercise the real resolver behaviour.
    """
    from ascendo.ai.backend import Backend, BackendResolver, Chunk

    class SlowFake(Backend):
        name = "slow-fake"
        bin_name = None

        def is_available(self) -> bool: return True
        def is_authenticated(self) -> bool: return True
        def model_info(self) -> dict[str, str]:
            return {"backend": "slow-fake", "model": "test"}

        async def stream(
            self, *, system, messages, cancel_event,
        ) -> AsyncIterator[Chunk]:
            for tok in tokens:
                if cancel_event.is_set():
                    yield Chunk(type="done", status="cancelled")
                    return
                if delay:
                    await asyncio.sleep(delay)
                yield Chunk(type="token", content=tok)
            yield Chunk(type="done", status="success", tokens_out=len(tokens))

    monkeypatch.setattr(BackendResolver, "resolve", lambda self: SlowFake())


def test_post_chat_then_cancel_round_trip_resolves(app, monkeypatch):
    """The turn_id the SPA gets back must be the one /cancel knows about."""
    _install_fake_backend(monkeypatch, ["A", "B", "C", "D"], delay=0.05)
    c = TestClient(app)
    conv = c.post("/ai/chat/conversations", json={}).json()
    r = c.post("/ai/chat", json={
        "conversation_id": conv["id"],
        "message": "hi",
        "locale": "en",
    })
    assert r.status_code == 202
    turn_id = r.json()["turn_id"]
    # Before Task 19 this would 404 because run_turn() generated its own
    # internal turn_id while post_chat returned a different one.
    cancel = c.post(f"/ai/chat/cancel/{turn_id}")
    assert cancel.status_code == 200
    assert cancel.json()["ok"] is True


def test_stream_disconnect_sets_cancel_event(app, monkeypatch):
    """Manually drive the SSE generator with a Request.is_disconnected mock."""
    _install_fake_backend(monkeypatch, ["A"], delay=0.0)
    c = TestClient(app)
    conv = c.post("/ai/chat/conversations", json={}).json()
    r = c.post("/ai/chat", json={
        "conversation_id": conv["id"],
        "message": "go",
        "locale": "en",
    }).json()
    turn_id = r["turn_id"]

    # Look up the state in the registry to inspect cancel_event later.
    reg = app.state.turn_registry
    state = reg.get(turn_id)
    assert state is not None, "post_chat must register the turn_id"
    assert state.cancel_event.is_set() is False

    # Drive the SSE generator manually with a fake Request whose
    # is_disconnected returns True on first poll.
    streams = app.state._chat_streams
    assert turn_id in streams

    fake_app = type("FakeApp", (), {"state": app.state})()

    class FakeReq:
        def __init__(self, fake_app_instance):
            self.app = fake_app_instance
        async def is_disconnected(self):
            return True

    from ascendo.dashboard.routes.chat import stream_turn

    async def _drive():
        resp = await stream_turn(turn_id=turn_id, request=FakeReq(fake_app))  # type: ignore[arg-type]
        # StreamingResponse exposes the generator via body_iterator;
        # consuming it runs the disconnect check + sets cancel_event.
        async for _chunk in resp.body_iterator:
            break

    asyncio.run(_drive())
    assert state.cancel_event.is_set(), (
        "disconnect must signal the backend stream to stop"
    )


def test_stream_cancel_endpoint_sets_cancel_event(app, monkeypatch):
    """POST /ai/chat/cancel/{turn_id} flips the same event the stream uses."""
    _install_fake_backend(monkeypatch, ["A"], delay=0.0)
    c = TestClient(app)
    conv = c.post("/ai/chat/conversations", json={}).json()
    r = c.post("/ai/chat", json={
        "conversation_id": conv["id"],
        "message": "stop me",
        "locale": "en",
    }).json()
    turn_id = r["turn_id"]
    state = app.state.turn_registry.get(turn_id)
    assert state is not None
    assert not state.cancel_event.is_set()

    c.post(f"/ai/chat/cancel/{turn_id}")
    assert state.cancel_event.is_set()
