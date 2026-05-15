"""OpencodeBackend: shells out to `opencode` (open-source, multi-provider).

Verified against opencode 1.14.50. `opencode run --format json` emits:

  {"type":"step_start","timestamp":...,"sessionID":"...","part":{...}}
  {"type":"text","timestamp":...,"part":{"text":"Hello there friend",...}}

Other event types we may see across versions: tool, finish. We pull
``part.text`` from text events as the user-visible token stream.

opencode doesn't expose the active model name in its JSON events
(it's user-configured via `opencode auth` + `opencode models`), so
model_info() reports a generic "user-configured" string.
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import tempfile
from collections.abc import AsyncIterator

from ..backend import Backend, Chunk, Message
from ._base import (
    SubprocessFailure,
    SubprocessHang,
    discover_binary,
    run_streaming,
    sanitized_env,
)

DEFAULT_BIN = "opencode"


class OpencodeBackend(Backend):
    name = "opencode"
    max_input_tokens = 32_000  # depends on user-configured provider; conservative

    def __init__(self, *, bin_name: str = DEFAULT_BIN) -> None:
        self.bin_name = bin_name
        self._cached_path: str | None = None

    def is_available(self) -> bool:
        path = discover_binary(self.bin_name)
        if path is None:
            return False
        self._cached_path = path
        return True

    def is_authenticated(self) -> bool:
        if not self.is_available():
            return False
        try:
            res = subprocess.run(
                [self._cached_path or self.bin_name, "run",
                 "--pure", "--format", "json", "say ok"],
                capture_output=True, text=True, timeout=15.0, check=False,
                stdin=subprocess.DEVNULL,
                cwd=tempfile.gettempdir(),
                env=sanitized_env(neutral_cwd=tempfile.gettempdir()),
            )
            return res.returncode == 0
        except subprocess.TimeoutExpired:
            return False

    def model_info(self) -> dict[str, str]:
        return {"backend": "opencode CLI", "model": "user-configured"}

    async def stream(
        self,
        *,
        system: str,
        messages: list[Message],
        cancel_event: asyncio.Event,
    ) -> AsyncIterator[Chunk]:
        prompt = _build_prompt(system, messages)
        argv = [
            self._cached_path or self.bin_name, "run",
            # --pure: disable agent tools/plugins so opencode acts as a
            # plain "ask the model, get a reply" channel. Without --pure,
            # opencode 1.14 tries to spawn $SHELL for tool calls and
            # deadlocks against our subprocess pipes (Sesja 71e finding).
            "--pure",
            "--format", "json", prompt,
        ]
        neutral_cwd = tempfile.gettempdir()
        tokens_out = 0
        # opencode doesn't expose the active model in JSON events, so
        # surface a fixed identifier badge up-front.
        yield Chunk(type="meta", backend="opencode CLI", model="user-configured")
        try:
            async for line in run_streaming(
                argv, cancel_event=cancel_event, cwd=neutral_cwd,
            ):
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if event.get("type") == "text":
                    part = event.get("part") or {}
                    text = part.get("text") or event.get("text") or ""
                    if text:
                        tokens_out += max(1, len(text) // 4)
                        yield Chunk(type="token", content=text)
        except SubprocessFailure as e:
            yield Chunk(type="error", error=f"opencode failed: {e.stderr_tail[-500:]}")
            yield Chunk(type="done", status="error")
            return
        except SubprocessHang as e:
            yield Chunk(type="error", error=f"opencode hang: {e}")
            yield Chunk(type="done", status="error")
            return
        if cancel_event.is_set():
            yield Chunk(type="done", status="cancelled")
        else:
            yield Chunk(type="done", status="success", tokens_out=tokens_out)


def _build_prompt(system: str, messages: list[Message]) -> str:
    """Plain-prose framing — avoids XML tags that some safety
    classifiers flag as prompt-injection attempts (mirrors the
    claude + gemini + codex drivers' Sesja 71c/71d fix)."""
    parts: list[str] = []
    if system:
        parts.append(
            "## Persona and instructions (from the host application)\n\n" + system
        )
    if messages:
        parts.append("## Conversation so far\n")
        history = messages[:-1] if messages and messages[-1].role == "user" else messages
        new_user_msg = messages[-1] if messages and messages[-1].role == "user" else None
        for m in history:
            label = {"user": "User", "assistant": "You (previously)"}.get(m.role, m.role.capitalize())
            parts.append(f"{label}:\n{m.content}\n")
        if new_user_msg:
            parts.append("## New user message\n\n" + new_user_msg.content)
    return "\n".join(parts)
