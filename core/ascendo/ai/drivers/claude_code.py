"""ClaudeCodeBackend: shells out to `claude` CLI from Claude Code."""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
from collections.abc import AsyncIterator

from ..backend import Backend, Chunk, Message
from ._base import (
    SubprocessFailure,
    SubprocessHang,
    discover_binary,
    run_streaming,
)

DEFAULT_BIN = "claude"


class ClaudeCodeBackend(Backend):
    name = "claude"
    max_input_tokens = 200_000

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
                [self._cached_path or self.bin_name, "-p", "say ok",
                 "--output-format", "stream-json"],
                capture_output=True, text=True, timeout=10.0, check=False,
                env={**os.environ},
            )
            return res.returncode == 0
        except subprocess.TimeoutExpired:
            return False

    def model_info(self) -> dict[str, str]:
        return {"backend": "claude", "model": "claude-sonnet (subscription)"}

    async def stream(
        self,
        *,
        system: str,
        messages: list[Message],
        cancel_event: asyncio.Event,
    ) -> AsyncIterator[Chunk]:
        prompt = _build_prompt(system, messages)
        argv = [
            self._cached_path or self.bin_name, "-p", prompt,
            "--output-format", "stream-json",
        ]
        tokens_out = 0
        try:
            async for line in run_streaming(argv, cancel_event=cancel_event):
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if event.get("type") == "content_block_delta":
                    text = (event.get("delta") or {}).get("text") or ""
                    if text:
                        tokens_out += max(1, len(text) // 4)
                        yield Chunk(type="token", content=text)
        except SubprocessFailure as e:
            yield Chunk(type="error", error=f"claude subprocess failed: {e.stderr_tail[-500:]}")
            yield Chunk(type="done", status="error")
            return
        except SubprocessHang as e:
            yield Chunk(type="error", error=f"claude hang: {e}")
            yield Chunk(type="done", status="error")
            return

        if cancel_event.is_set():
            yield Chunk(type="done", status="cancelled")
        else:
            yield Chunk(type="done", status="success", tokens_out=tokens_out)


def _build_prompt(system: str, messages: list[Message]) -> str:
    """Join the system prompt + transcript into a single prompt string.

    CLIs are stateless; we re-pass everything each turn.
    """
    parts: list[str] = []
    if system:
        parts.append(f"<system>\n{system}\n</system>")
    if messages:
        parts.append("<conversation>")
        for m in messages:
            parts.append(f"{m.role.upper()}: {m.content}")
        parts.append("</conversation>")
    return "\n".join(parts)
