"""CodexCliBackend: shells out to `codex` CLI (OpenAI Codex).

Verified against codex-cli 0.128. `codex exec --json` emits:

  {"type":"thread.started","thread_id":"..."}
  {"type":"turn.started"}
  {"type":"item.completed","item":{"id":"...","type":"agent_message","text":"reply"}}
  {"type":"turn.completed","usage":{...}}

The CLI refuses headless mode outside a git repo; --skip-git-repo-check
+ a neutral cwd let it run from the dashboard process. codex doesn't
expose the active model in any event, so model_info() reports the
configured default + reflects -m <model> when the operator sets one
via Settings → AI provider config (future work).
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

DEFAULT_BIN = "codex"


class CodexCliBackend(Backend):
    name = "codex"
    max_input_tokens = 128_000

    def __init__(self, *, bin_name: str = DEFAULT_BIN) -> None:
        self.bin_name = bin_name
        self._cached_path: str | None = None
        self._observed_thread: str | None = None

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
                [self._cached_path or self.bin_name, "exec", "--json",
                 "--skip-git-repo-check", "say ok"],
                capture_output=True, text=True, timeout=15.0, check=False,
                stdin=subprocess.DEVNULL,
                cwd=tempfile.gettempdir(),
                env=sanitized_env(neutral_cwd=tempfile.gettempdir()),
            )
            return res.returncode == 0
        except subprocess.TimeoutExpired:
            return False

    def model_info(self) -> dict[str, str]:
        # codex doesn't expose the active model in --json events; the
        # operator can pin one via Settings → API provider config.
        return {"backend": "Codex CLI", "model": "ChatGPT login"}

    async def stream(
        self,
        *,
        system: str,
        messages: list[Message],
        cancel_event: asyncio.Event,
    ) -> AsyncIterator[Chunk]:
        prompt = _build_prompt(system, messages)
        argv = [
            self._cached_path or self.bin_name, "exec",
            "--json", "--skip-git-repo-check", prompt,
        ]
        neutral_cwd = tempfile.gettempdir()
        tokens_out = 0
        seen_text = False
        # Emit a meta chunk up-front so the SPA badge shows the
        # backend immediately. codex events don't carry a model id
        # so we don't update mid-stream like claude/gemini.
        yield Chunk(type="meta", backend="Codex CLI", model="ChatGPT login")
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
                etype = event.get("type")
                if etype == "thread.started":
                    self._observed_thread = event.get("thread_id")
                    continue
                if etype == "item.completed":
                    item = event.get("item") or {}
                    if item.get("type") == "agent_message":
                        text = item.get("text") or ""
                        if text:
                            tokens_out += max(1, len(text) // 4)
                            seen_text = True
                            yield Chunk(type="token", content=text)
        except SubprocessFailure as e:
            yield Chunk(type="error", error=f"codex failed: {e.stderr_tail[-500:]}")
            yield Chunk(type="done", status="error")
            return
        except SubprocessHang as e:
            yield Chunk(type="error", error=f"codex hang: {e}")
            yield Chunk(type="done", status="error")
            return
        if cancel_event.is_set():
            yield Chunk(type="done", status="cancelled")
        else:
            yield Chunk(type="done", status="success", tokens_out=tokens_out)


def _build_prompt(system: str, messages: list[Message]) -> str:
    """Plain-prose framing — avoids XML tags that some safety
    classifiers flag as prompt-injection attempts (mirrors the
    claude + gemini drivers' Sesja 71c fix)."""
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
