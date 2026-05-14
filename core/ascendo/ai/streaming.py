"""Streaming orchestration: wires Backend + Context + Persistence + TurnRegistry.

run_turn() is the single entry point for one chat turn. It:
  1. Registers a TurnState in the registry for /ai/chat/cancel and /status.
  2. Persists the user message to ChatsDB.
  3. Loads full conversation history.
  4. Drives backend.stream() with the framed system prompt + context blob.
  5. Yields chunks as they arrive (relayed to SSE by the route handler).
  6. After the stream finishes, parses action proposals from the buffered
     text and persists the cleaned assistant message.

Action fences are stripped from the persisted prose so the SPA renders
clean markdown; the parsed action dicts live on the dedicated `actions`
column. This mirrors Task 13's parse_actions contract.
"""
from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from time import time

from .actions import parse_actions
from .backend import Backend, Chunk, Message, TurnRegistry, TurnState, TurnStatus


async def run_turn(
    *,
    backend: Backend,
    conversation_id: str,
    user_message: str,
    system_prompt: str,
    context_blob: str,
    chats_db,
    registry: TurnRegistry,
    template_id: str | None = None,
    context_tags: list[str] | None = None,
    state: TurnState | None = None,
) -> AsyncIterator[Chunk]:
    """Execute one chat turn end-to-end, yielding chunks for SSE relay.

    If ``state`` is supplied the caller has already registered it in the
    registry (so its turn_id matches what was returned to the SPA + can
    be addressed by /ai/chat/cancel/{turn_id}). Otherwise a fresh state
    is generated and registered.
    """
    if state is None:
        turn_id = uuid.uuid4().hex
        state = TurnState(
            turn_id=turn_id,
            conversation_id=conversation_id,
            backend_name=backend.name,
            status=TurnStatus.RUNNING,
            started_at=time(),
        )
        registry.register(state)
    else:
        state.status = TurnStatus.RUNNING
        if state.started_at is None:
            state.started_at = time()

    chats_db.append_message(
        conversation_id=conversation_id,
        role="user",
        content=user_message,
        template_id=template_id,
        context_tags=context_tags,
    )

    history = [
        Message(role=m["role"], content=m["content"])
        for m in chats_db.get_messages(conversation_id)
        if m["role"] in ("user", "assistant", "system", "tool_result")
    ]

    full_system = f"{system_prompt}\n\n{context_blob}"

    buffer: list[str] = []
    tokens_in_estimate = (
        len(full_system) + sum(len(m.content) for m in history)
    ) // 4
    tokens_out_total = 0
    error: str | None = None
    final_status: str = "success"
    # Count of action proposals already emitted as SSE events so we don't
    # re-emit the same fence on every subsequent token (Task 20).
    emitted_actions = 0

    try:
        async for chunk in backend.stream(
            system=full_system,
            messages=history,
            cancel_event=state.cancel_event,
        ):
            if chunk.type == "token":
                if chunk.content:
                    buffer.append(chunk.content)
                yield chunk
                # Incremental action detection: re-parse the running buffer
                # and emit any newly-completed fence as an action_proposal
                # chunk so the SPA renders chips DURING streaming, not only
                # after `done`. parse_actions is a closed-fence regex, so
                # half-typed fences don't fire prematurely.
                actions_so_far, _ = parse_actions("".join(buffer))
                while emitted_actions < len(actions_so_far):
                    proposal = actions_so_far[emitted_actions]
                    emitted_actions += 1
                    yield Chunk(type="action_proposal", action=proposal)
            elif chunk.type == "done":
                final_status = chunk.status or "success"
                tokens_out_total = chunk.tokens_out or (len("".join(buffer)) // 4)
                yield chunk
                break
            elif chunk.type == "error":
                error = chunk.error
                final_status = "error"
                yield chunk
            elif chunk.type in ("action_proposal", "context_trimmed", "meta"):
                yield chunk
    except Exception as exc:  # noqa: BLE001 - boundary handler
        error = str(exc)
        final_status = "error"
        yield Chunk(type="error", error=error)
        yield Chunk(type="done", status="error")

    assistant_text = "".join(buffer)
    actions, cleaned = parse_actions(assistant_text)

    chats_db.append_message(
        conversation_id=conversation_id,
        role="assistant",
        content=cleaned,
        actions=actions or None,
        tokens_in=tokens_in_estimate,
        tokens_out=tokens_out_total,
    )

    if final_status == "cancelled":
        state.status = TurnStatus.CANCELLED
    elif final_status == "success":
        state.status = TurnStatus.COMPLETED
    else:
        state.status = TurnStatus.FAILED
    state.error = error
    state.ended_at = time()
    state.tokens_in = tokens_in_estimate
    state.tokens_out = tokens_out_total
