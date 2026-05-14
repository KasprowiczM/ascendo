"""GeminiCliBackend: shells out to `gemini` CLI (gemini-cli, Google).

Verified against gemini CLI 0.42 (2026-05). Event shape:

  {"type":"init", "model":"auto-gemini-3", ...}
  {"type":"message", "role":"user", ...}            # echo of our prompt
  {"type":"message", "role":"assistant",
       "content":"Hi there, user.", "delta":true}   # text we want
  {"type":"result", "status":"success", "stats":{...}}

The CLI refuses headless mode in untrusted directories. We bypass the
prompt with --skip-trust (per official docs) since we always launch
from a neutral cwd.
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
)

DEFAULT_BIN = "gemini"


class GeminiCliBackend(Backend):
    name = "gemini"
    max_input_tokens = 1_000_000

    def __init__(self, *, bin_name: str = DEFAULT_BIN) -> None:
        self.bin_name = bin_name
        self._cached_path: str | None = None
        self._observed_model: str | None = None

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
                 "--output-format", "stream-json", "--skip-trust"],
                capture_output=True, text=True, timeout=15.0, check=False,
                cwd=tempfile.gettempdir(),
                env={**os.environ},
            )
            return res.returncode == 0
        except subprocess.TimeoutExpired:
            return False

    def model_info(self) -> dict[str, str]:
        return {
            "backend": "Gemini CLI",
            "model": self._observed_model or "gemini (Google login)",
        }

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
            "--skip-trust",
        ]
        # Run gemini in a neutral cwd so it doesn't refuse with a trust
        # prompt in interactive mode AND doesn't auto-load the Ascendo
        # repo as a workspace context.
        neutral_cwd = tempfile.gettempdir()
        tokens_out = 0
        seen_text = False
        try:
            async for line in run_streaming(
                argv, cancel_event=cancel_event, cwd=neutral_cwd,
            ):
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    # Some lines are non-JSON warnings (e.g. "Ripgrep is
                    # not available. Falling back to GrepTool."). Skip.
                    continue
                etype = event.get("type")
                if etype == "init":
                    m = event.get("model")
                    if m:
                        self._observed_model = m
                        yield Chunk(
                            type="meta", backend="Gemini CLI", model=m,
                        )
                    continue
                if etype == "message" and event.get("role") == "assistant":
                    text = event.get("content") or ""
                    if text:
                        tokens_out += max(1, len(text) // 4)
                        seen_text = True
                        yield Chunk(type="token", content=text)
                elif etype == "result" and not seen_text:
                    # If the CLI ever returns a single-shot result with
                    # no message event, surface the prose so the user
                    # still sees something.
                    text = event.get("text") or event.get("result") or ""
                    if text:
                        tokens_out += max(1, len(text) // 4)
                        yield Chunk(type="token", content=text)
        except SubprocessFailure as e:
            yield Chunk(type="error", error=f"gemini failed: {e.stderr_tail[-500:]}")
            yield Chunk(type="done", status="error")
            return
        except SubprocessHang as e:
            yield Chunk(type="error", error=f"gemini hang: {e}")
            yield Chunk(type="done", status="error")
            return

        if cancel_event.is_set():
            yield Chunk(type="done", status="cancelled")
        else:
            yield Chunk(type="done", status="success", tokens_out=tokens_out)


def _build_prompt(system: str, messages: list[Message]) -> str:
    """Plain-prose framing — avoids XML tags that some safety
    classifiers flag as prompt-injection attempts (same fix as the
    claude driver in Sesja 71c)."""
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
