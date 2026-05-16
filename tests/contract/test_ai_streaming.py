"""Streaming orchestration: combines backend + context + persistence + actions."""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest

from ascendo.ai.backend import Backend, Chunk, Message, TurnRegistry


class FakeBackend(Backend):
    name = "fake"
    bin_name = None

    def __init__(self, tokens: list[str] | None = None, *, error_mid: bool = False) -> None:
        self._tokens = tokens if tokens is not None else ["Hello", " ", "world"]
        self._error_mid = error_mid

    def is_available(self) -> bool:  # pragma: no cover - trivial
        return True

    def is_authenticated(self) -> bool:  # pragma: no cover - trivial
        return True

    def model_info(self) -> dict[str, str]:  # pragma: no cover - trivial
        return {"backend": "fake", "model": "test"}

    async def stream(
        self,
        *,
        system: str,
        messages: list[Message],
        cancel_event: asyncio.Event,
    ) -> AsyncIterator[Chunk]:
        for i, t in enumerate(self._tokens):
            if cancel_event.is_set():
                yield Chunk(type="done", status="cancelled")
                return
            if self._error_mid and i == 1:
                yield Chunk(type="error", error="boom")
                yield Chunk(type="done", status="error")
                return
            yield Chunk(type="token", content=t)
        yield Chunk(type="done", status="success", tokens_out=len(self._tokens))


@pytest.mark.asyncio
async def test_run_turn_streams_chunks_and_persists(tmp_path):
    from ascendo.ai.persistence import ChatsDB
    from ascendo.ai.streaming import run_turn

    chats = ChatsDB(tmp_path / "chats.db")
    cid = chats.create_conversation(backend="fake", locale="en")
    registry = TurnRegistry()
    chunks: list[Chunk] = []
    async for c in run_turn(
        backend=FakeBackend(),
        conversation_id=cid,
        user_message="hi",
        system_prompt="you are helpful",
        context_blob="<ascendo_context>...</ascendo_context>",
        chats_db=chats,
        registry=registry,
    ):
        chunks.append(c)

    assert any(c.type == "token" for c in chunks)
    assert chunks[-1].type == "done"
    msgs = chats.get_messages(cid)
    assert len(msgs) == 2  # user + assistant
    assert msgs[0]["role"] == "user"
    assert msgs[0]["content"] == "hi"
    assert msgs[1]["role"] == "assistant"
    assert "Hello world" in msgs[1]["content"]


@pytest.mark.asyncio
async def test_run_turn_strips_action_fences_from_persisted_text(tmp_path):
    from ascendo.ai.persistence import ChatsDB
    from ascendo.ai.streaming import run_turn

    chats = ChatsDB(tmp_path / "chats.db")
    cid = chats.create_conversation(backend="fake", locale="en")
    tokens = [
        "Run the check phase:\n",
        '```ascendo-action\n{"id":"run_check","verb":"POST","path":"/runs/async",'
        '"body":{"categories":["winget"],"phases":["check"]}}\n```',
        "\nThat's it.",
    ]
    async for _ in run_turn(
        backend=FakeBackend(tokens=tokens),
        conversation_id=cid,
        user_message="check winget",
        system_prompt="sys",
        context_blob="ctx",
        chats_db=chats,
        registry=TurnRegistry(),
    ):
        pass

    msgs = chats.get_messages(cid)
    assistant = msgs[1]
    # The fenced action block was stripped from the persisted prose.
    assert "ascendo-action" not in assistant["content"]
    # The detected action lives on the dedicated column.
    import json

    actions = json.loads(assistant["actions"])
    assert any(a.get("id") == "run_check" for a in actions)


@pytest.mark.asyncio
async def test_run_turn_handles_backend_error(tmp_path):
    from ascendo.ai.backend import TurnStatus
    from ascendo.ai.persistence import ChatsDB
    from ascendo.ai.streaming import run_turn

    chats = ChatsDB(tmp_path / "chats.db")
    cid = chats.create_conversation(backend="fake", locale="en")
    registry = TurnRegistry()
    chunks: list[Chunk] = []
    async for c in run_turn(
        backend=FakeBackend(error_mid=True),
        conversation_id=cid,
        user_message="oops",
        system_prompt="sys",
        context_blob="ctx",
        chats_db=chats,
        registry=registry,
    ):
        chunks.append(c)

    assert any(c.type == "error" for c in chunks)
    assert chunks[-1].type == "done"
    # Registry surfaces the failure for /ai/chat/status callers.
    states = list(registry._states.values())  # noqa: SLF001 - test inspection
    assert states[-1].status == TurnStatus.FAILED


@pytest.mark.asyncio
async def test_run_turn_registers_turn_in_registry(tmp_path):
    from ascendo.ai.backend import TurnStatus
    from ascendo.ai.persistence import ChatsDB
    from ascendo.ai.streaming import run_turn

    chats = ChatsDB(tmp_path / "chats.db")
    cid = chats.create_conversation(backend="fake", locale="en")
    registry = TurnRegistry()
    async for _ in run_turn(
        backend=FakeBackend(),
        conversation_id=cid,
        user_message="hi",
        system_prompt="sys",
        context_blob="ctx",
        chats_db=chats,
        registry=registry,
    ):
        pass
    states = list(registry._states.values())  # noqa: SLF001
    assert len(states) == 1
    assert states[0].conversation_id == cid
    assert states[0].status == TurnStatus.COMPLETED
    assert states[0].started_at is not None
    assert states[0].ended_at is not None


@pytest.mark.asyncio
async def test_run_turn_emits_action_proposal_during_streaming(tmp_path):
    """Action fences detected mid-stream emit action_proposal chunks BEFORE done.

    Pre-Task-20 the parser only ran post-hoc so the SPA couldn't render
    chips until the full message landed. Task 20 makes the chips appear
    incrementally as soon as the closing ``` arrives.
    """
    from ascendo.ai.persistence import ChatsDB
    from ascendo.ai.streaming import run_turn

    chats = ChatsDB(tmp_path / "chats.db")
    cid = chats.create_conversation(backend="fake", locale="en")
    tokens = [
        "Run check:\n",
        '```ascendo-action\n{"id":"run_check","verb":"POST","path":"/runs/async",'
        '"body":{"categories":["winget"],"phases":["check"]}}\n```',
        "\nNext step is plan.",
    ]
    seen: list[Chunk] = []
    async for c in run_turn(
        backend=FakeBackend(tokens=tokens),
        conversation_id=cid,
        user_message="check",
        system_prompt="sys",
        context_blob="ctx",
        chats_db=chats,
        registry=TurnRegistry(),
    ):
        seen.append(c)

    types = [c.type for c in seen]
    assert "action_proposal" in types
    # The proposal must arrive BEFORE the terminal done event.
    proposal_idx = types.index("action_proposal")
    done_idx = types.index("done")
    assert proposal_idx < done_idx
    proposal = seen[proposal_idx]
    assert proposal.action is not None
    assert proposal.action["id"] == "run_check"


@pytest.mark.asyncio
async def test_run_turn_does_not_double_emit_actions(tmp_path):
    """Subsequent tokens after a closed fence must not re-emit the same action."""
    from ascendo.ai.persistence import ChatsDB
    from ascendo.ai.streaming import run_turn

    chats = ChatsDB(tmp_path / "chats.db")
    cid = chats.create_conversation(backend="fake", locale="en")
    tokens = [
        "Two actions:\n",
        '```ascendo-action\n{"id":"run_check","body":{"categories":["w"],"phases":["check"]}}\n```',
        "\nand\n",
        '```ascendo-action\n{"id":"run_plan","body":{"categories":["w"],"phases":["plan"]}}\n```',
        "\ndone.",
    ]
    seen: list[Chunk] = []
    async for c in run_turn(
        backend=FakeBackend(tokens=tokens),
        conversation_id=cid,
        user_message="show me",
        system_prompt="sys",
        context_blob="ctx",
        chats_db=chats,
        registry=TurnRegistry(),
    ):
        seen.append(c)

    proposals = [c for c in seen if c.type == "action_proposal"]
    ids = [p.action["id"] for p in proposals if p.action]
    assert ids == ["run_check", "run_plan"]


# ── C.4: scoped read-only auto-fire ──────────────────────────────────────────

class _ProbeThenAnswerBackend(Backend):
    """Pass 1: propose a web_probe action. Pass 2 (after a tool_result is
    in history): emit a final answer with no action."""

    name = "fake"
    bin_name = None

    def is_available(self) -> bool:
        return True

    def is_authenticated(self) -> bool:
        return True

    def model_info(self) -> dict[str, str]:
        return {"backend": "fake", "model": "test"}

    async def stream(self, *, system, messages, cancel_event):
        has_tool = any(getattr(m, "role", "") == "tool_result" for m in messages)
        if not has_tool:
            fence = (
                '```ascendo-action\n'
                '{"id":"web_probe","verb":"POST","path":"/web/probe-entry",'
                '"body":{"slug":"x"}}\n```'
            )
            yield Chunk(type="token", content="probing " + fence)
            yield Chunk(type="done", status="success", tokens_out=3)
        else:
            yield Chunk(type="token", content="done, it works")
            yield Chunk(type="done", status="success", tokens_out=3)


@pytest.mark.asyncio
async def test_auto_fire_runs_readonly_action_and_reprompts(tmp_path):
    from ascendo.ai.persistence import ChatsDB
    from ascendo.ai.streaming import run_turn

    chats = ChatsDB(tmp_path / "c.db")
    cid = chats.create_conversation(backend="fake", locale="en")
    calls: list[dict] = []

    def runner(action: dict) -> dict:
        calls.append(action)
        return {"ok": True, "resolved_version": "1.2.3"}

    chunks = []
    async for c in run_turn(
        backend=_ProbeThenAnswerBackend(),
        conversation_id=cid,
        user_message="cover my app",
        system_prompt="sys",
        context_blob="ctx",
        chats_db=chats,
        registry=TurnRegistry(),
        auto_action_runner=runner,
    ):
        chunks.append(c)

    # The probe was auto-fired exactly once, then the model re-prompted
    # and finished (bounded; not 3x).
    assert len(calls) == 1
    assert calls[0]["id"] == "web_probe"
    results = [c for c in chunks if c.type == "action_result"]
    assert len(results) == 1
    assert results[0].action["result"]["resolved_version"] == "1.2.3"


@pytest.mark.asyncio
async def test_mutating_action_is_not_auto_fired(tmp_path):
    from ascendo.ai.persistence import ChatsDB
    from ascendo.ai.streaming import run_turn

    chats = ChatsDB(tmp_path / "c.db")
    cid = chats.create_conversation(backend="fake", locale="en")
    fence = (
        '```ascendo-action\n{"id":"run_apply","verb":"POST",'
        '"path":"/runs/async","body":{}}\n```'
    )
    calls = []
    async for _ in run_turn(
        backend=FakeBackend(tokens=["go " + fence]),
        conversation_id=cid,
        user_message="apply everything",
        system_prompt="s",
        context_blob="c",
        chats_db=chats,
        registry=TurnRegistry(),
        auto_action_runner=lambda a: calls.append(a) or {"ok": True},
    ):
        pass
    # run_apply is mutating => never auto-fired; stays propose-only.
    assert calls == []
