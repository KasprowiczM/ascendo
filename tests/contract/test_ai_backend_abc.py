"""Contract tests for the AI Backend ABC + Chunk + TurnRegistry."""
from __future__ import annotations
import asyncio
import pytest
from pydantic import ValidationError

from ascendo.ai.backend import Backend, Chunk, TurnRegistry, TurnState, TurnStatus


def test_chunk_token_type_accepts_content():
    c = Chunk(type="token", content="hello")
    assert c.type == "token"
    assert c.content == "hello"


def test_chunk_done_type_accepts_status():
    c = Chunk(type="done", status="success")
    assert c.status == "success"


def test_chunk_action_proposal_type_accepts_action():
    c = Chunk(type="action_proposal", action={"id": "run_check", "label_en": "Run check"})
    assert c.action["id"] == "run_check"


def test_chunk_error_type_accepts_error_field():
    c = Chunk(type="error", error="oops")
    assert c.error == "oops"


def test_chunk_invalid_type_rejected():
    with pytest.raises(ValidationError):
        Chunk(type="not_a_real_type")


def test_chunk_rejects_extra_fields():
    with pytest.raises(ValidationError):
        Chunk(type="token", content="hi", bogus="extra")


def test_backend_is_abstract():
    with pytest.raises(TypeError):
        Backend()  # type: ignore[abstract]


def test_turn_registry_register_and_get():
    reg = TurnRegistry(max_runs=10)
    state = TurnState(turn_id="abc", conversation_id="conv1", backend_name="fake")
    reg.register(state)
    assert reg.get("abc") is state
    assert reg.get("missing") is None


def test_turn_registry_evicts_completed_when_full():
    reg = TurnRegistry(max_runs=2)
    s1 = TurnState(turn_id="t1", conversation_id="c", backend_name="fake")
    s1.status = TurnStatus.COMPLETED
    s2 = TurnState(turn_id="t2", conversation_id="c", backend_name="fake")
    s3 = TurnState(turn_id="t3", conversation_id="c", backend_name="fake")
    reg.register(s1)
    reg.register(s2)
    reg.register(s3)  # forces eviction; s1 (completed) goes first
    assert reg.get("t1") is None
    assert reg.get("t2") is s2
    assert reg.get("t3") is s3


def test_turn_registry_never_evicts_running():
    reg = TurnRegistry(max_runs=2)
    s1 = TurnState(turn_id="t1", conversation_id="c", backend_name="fake")
    s1.status = TurnStatus.RUNNING
    s2 = TurnState(turn_id="t2", conversation_id="c", backend_name="fake")
    s2.status = TurnStatus.RUNNING
    reg.register(s1)
    reg.register(s2)
    s3 = TurnState(turn_id="t3", conversation_id="c", backend_name="fake")
    reg.register(s3)
    # No completed runs to evict; all three present (registry grew past cap).
    assert reg.get("t1") is s1
    assert reg.get("t2") is s2
    assert reg.get("t3") is s3


def test_turn_registry_remove():
    reg = TurnRegistry()
    s = TurnState(turn_id="t1", conversation_id="c", backend_name="fake")
    reg.register(s)
    reg.remove("t1")
    assert reg.get("t1") is None
