"""AI Tools chat endpoints.

Surfaces eight HTTP routes that drive the Sesja 70 AI Tools tab end-to-end:

  GET    /ai/chat/backends                 - list installed + auth state
  GET    /ai/chat/library                  - prompt library, platform-filtered
  POST   /ai/chat/conversations            - create a conversation
  GET    /ai/chat/conversations            - list, optional ?archived=&q=
  GET    /ai/chat/conversations/{cid}      - one conversation + its messages
  DELETE /ai/chat/conversations/{cid}      - delete (cascades messages)
  POST   /ai/chat                          - kick a turn, returns 202 + turn_id
  GET    /ai/chat/stream/{turn_id}         - SSE token / done / action stream
  POST   /ai/chat/cancel/{turn_id}         - request mid-turn cancel
  POST   /ai/chat/action                   - validate + return action proxy plan

The streaming model mirrors the Run Center pattern: post_chat fires a
producer task that pushes Chunk objects onto an asyncio.Queue keyed by
turn_id; the SSE handler drains that queue and emits one ``event: <type>``
line per chunk. Cancel sets the per-turn asyncio.Event so the backend's
stream() coroutine sees the signal between tokens.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict

from ascendo.ai.actions import dispatch_action
from ascendo.ai.backend import BackendResolver, TurnRegistry
from ascendo.ai.context import build_context
from ascendo.ai.persistence import ChatsDB
from ascendo.ai.prompts import filtered_library, system_prompt
from ascendo.ai.streaming import run_turn


router = APIRouter(prefix="/ai/chat", tags=["ai-chat"])


# ============================================================================
# Request models
# ============================================================================


class CreateConversation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str | None = None


class PostChat(BaseModel):
    model_config = ConfigDict(extra="forbid")
    conversation_id: str
    message: str
    template_id: str | None = None
    context_tags: list[str] | None = None
    locale: str = "en"
    backend_override: str | None = None
    bin_overrides: dict[str, str] | None = None
    api_config: dict | None = None


class PostAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action_id: str
    body: dict


# ============================================================================
# Lazy-init helpers (the dashboard factory may not pre-build these).
# ============================================================================


def _chats_db(request: Request) -> ChatsDB:
    db = getattr(request.app.state, "chats_db", None)
    if db is None:
        home = Path(os.environ.get("ASCENDO_HOME") or Path.home() / ".ascendo")
        home.mkdir(parents=True, exist_ok=True)
        db = ChatsDB(home / "chats.db")
        request.app.state.chats_db = db
    return db


def _turn_registry(request: Request) -> TurnRegistry:
    reg = getattr(request.app.state, "turn_registry", None)
    if reg is None:
        reg = TurnRegistry()
        request.app.state.turn_registry = reg
    return reg


def _chat_streams(request: Request) -> dict[str, asyncio.Queue]:
    streams = getattr(request.app.state, "_chat_streams", None)
    if streams is None:
        streams = {}
        request.app.state._chat_streams = streams
    return streams


def _adapter_slug(request: Request) -> str:
    adapter = getattr(request.app.state, "adapter", None)
    if adapter is None:
        return "unknown"
    name = getattr(adapter, "name", None)
    if name:
        return str(name).lower()
    return type(adapter).__name__.lower().replace("adapter", "")


# ============================================================================
# Endpoints
# ============================================================================


@router.get("/backends")
def get_backends(request: Request) -> dict:
    """Report which CLI backends are installed + their bin paths."""
    resolver = BackendResolver(preferred=None, bin_overrides=None, api_config=None)
    return {"backends": resolver.list_status()}


@router.get("/library")
def get_library(request: Request) -> dict:
    """Return the prompt library filtered to entries the host adapter can act on."""
    entries = filtered_library(adapter_name=_adapter_slug(request))
    return {"entries": entries}


@router.post("/conversations", status_code=status.HTTP_201_CREATED)
def post_conversation(body: CreateConversation, request: Request) -> dict:
    db = _chats_db(request)
    cid = db.create_conversation(backend="unset", locale="en")
    if body.title:
        db.update_conversation(cid, title=body.title)
    return {"id": cid}


@router.get("/conversations")
def list_conversations(
    request: Request, archived: bool = False, q: str | None = None,
) -> dict:
    db = _chats_db(request)
    return {"conversations": db.list_conversations(archived=archived, query=q)}


@router.get("/conversations/{cid}")
def get_conversation(cid: str, request: Request) -> dict:
    db = _chats_db(request)
    convs = db.list_conversations(archived=False) + db.list_conversations(archived=True)
    matching = [c for c in convs if c["id"] == cid]
    if not matching:
        raise HTTPException(status_code=404, detail="conversation not found")
    return {"conversation": matching[0], "messages": db.get_messages(cid)}


@router.delete("/conversations/{cid}")
def delete_conversation(cid: str, request: Request) -> dict:
    db = _chats_db(request)
    convs = db.list_conversations(archived=False) + db.list_conversations(archived=True)
    if not any(c["id"] == cid for c in convs):
        raise HTTPException(status_code=404, detail="conversation not found")
    db.delete_conversation(cid)
    return {"ok": True}


@router.post("", status_code=status.HTTP_202_ACCEPTED)
async def post_chat(body: PostChat, request: Request) -> dict:
    """Kick off a chat turn. Returns 202 + stream URL the SPA opens as SSE."""
    db = _chats_db(request)
    reg = _turn_registry(request)
    adapter = getattr(request.app.state, "adapter", None)

    resolver = BackendResolver(
        preferred=body.backend_override,
        bin_overrides=body.bin_overrides,
        api_config=body.api_config,
    )
    backend = resolver.resolve()
    if backend is None:
        raise HTTPException(status_code=503, detail="no backend available")

    runs_dir = Path(
        os.environ.get("ASCENDO_RUNS_DIR")
        or os.environ.get("ASCENDO_HOME", str(Path.home() / ".ascendo"))
    )
    if not runs_dir.name == "runs":
        runs_dir = runs_dir / "runs"

    context_blob = build_context(
        message=body.message,
        template_id=body.template_id,
        locale=body.locale,
        adapter=adapter,
        runs_dir=runs_dir,
        inventory_db=getattr(request.app.state, "inventory_db", None),
        chats_db=db,
        conversation_id=body.conversation_id,
        extra_tags=body.context_tags or [],
    )

    import uuid

    turn_id = uuid.uuid4().hex
    queue: asyncio.Queue = asyncio.Queue()
    streams = _chat_streams(request)
    streams[turn_id] = queue

    async def _producer() -> None:
        try:
            async for chunk in run_turn(
                backend=backend,
                conversation_id=body.conversation_id,
                user_message=body.message,
                system_prompt=system_prompt(body.locale),
                context_blob=context_blob,
                chats_db=db,
                registry=reg,
                template_id=body.template_id,
                context_tags=body.context_tags,
            ):
                await queue.put(chunk)
        finally:
            await queue.put(None)  # sentinel

    asyncio.create_task(_producer())

    return {
        "turn_id": turn_id,
        "stream_url": f"/ai/chat/stream/{turn_id}",
        "cancel_url": f"/ai/chat/cancel/{turn_id}",
    }


@router.get("/stream/{turn_id}")
async def stream_turn(turn_id: str, request: Request):
    streams = _chat_streams(request)
    queue = streams.get(turn_id)
    if queue is None:
        raise HTTPException(status_code=404, detail="unknown turn_id")

    async def _events():
        try:
            while True:
                if await request.is_disconnected():
                    return
                chunk = await queue.get()
                if chunk is None:
                    return
                payload = chunk.model_dump_json(exclude_none=True)
                yield f"event: {chunk.type}\ndata: {payload}\n\n"
        finally:
            streams.pop(turn_id, None)

    return StreamingResponse(_events(), media_type="text/event-stream")


@router.post("/cancel/{turn_id}")
def cancel_turn(turn_id: str, request: Request) -> dict:
    reg = _turn_registry(request)
    state = reg.get(turn_id)
    if state is None:
        raise HTTPException(status_code=404, detail="unknown turn_id")
    state.cancel_event.set()
    return {"ok": True}


@router.post("/action")
def post_action(body: PostAction, request: Request) -> dict:
    """Validate an action proposal + return the proxy plan.

    The SPA fires the underlying endpoint separately so the network flow
    stays visible in the Run Center / dashboard activity log. Task 18
    will optionally wire automatic proxy execution for low-risk actions.
    """
    result = dispatch_action(action_id=body.action_id, body=body.body)
    if not result["ok"]:
        err = result.get("error", "")
        if err == "unknown_action":
            raise HTTPException(status_code=400, detail=err)
        raise HTTPException(status_code=422, detail=err)
    return result
