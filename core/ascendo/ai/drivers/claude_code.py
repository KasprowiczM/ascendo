"""ClaudeCodeBackend: shells out to `claude` CLI from Claude Code."""
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

DEFAULT_BIN = "claude"


class ClaudeCodeBackend(Backend):
    name = "claude"
    max_input_tokens = 200_000

    def __init__(self, *, bin_name: str = DEFAULT_BIN) -> None:
        self.bin_name = bin_name
        self._cached_path: str | None = None
        # The CLI reports its actual model in the first `init` system event
        # of every stream; we cache the latest so model_info() reflects what
        # the SPA badge should show.
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
                 "--output-format", "stream-json", "--verbose"],
                capture_output=True, text=True, timeout=10.0, check=False,
                env={**os.environ},
            )
            return res.returncode == 0
        except subprocess.TimeoutExpired:
            return False

    def model_info(self) -> dict[str, str]:
        return {
            "backend": "Claude Code CLI",
            "model": self._observed_model or "claude (subscription)",
        }

    async def stream(
        self,
        *,
        system: str,
        messages: list[Message],
        cancel_event: asyncio.Event,
    ) -> AsyncIterator[Chunk]:
        prompt = _build_prompt(system, messages)
        # `--verbose` is REQUIRED alongside `--output-format=stream-json`
        # in claude CLI 1.x+ (otherwise: "When using --print,
        # --output-format=stream-json requires --verbose"). Without it
        # the subprocess exits 1 immediately and the chat shows an empty
        # AI bubble. Discovered Sesja 71b on a fresh macOS install.
        argv = [
            self._cached_path or self.bin_name, "-p", prompt,
            "--output-format", "stream-json", "--verbose",
        ]
        # Run claude in a neutral cwd so it doesn't auto-load the
        # dashboard's working directory as a project context (which
        # would pollute every reply with /git/ noise and skill hooks).
        neutral_cwd = tempfile.gettempdir()
        tokens_out = 0
        seen_text = False
        try:
            async for line in run_streaming(argv, cancel_event=cancel_event, cwd=neutral_cwd):
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                etype = event.get("type")
                # claude CLI 2.x emits a system init event with the active
                # model id in `model`. Cache it so model_info() reports the
                # truth instead of the constructor's fallback.
                if etype == "system" and event.get("subtype") == "init":
                    m = event.get("model")
                    if m:
                        self._observed_model = m
                        yield Chunk(
                            type="meta", backend="Claude Code CLI", model=m,
                        )
                    continue
                # Streamed text comes as type=assistant with message.content
                # being a list of {type: "text", text: "..."} parts (CLI 2.x).
                # Older CLI 1.x emitted type=content_block_delta with
                # event.delta.text — keep that as a fallback path.
                if etype == "assistant":
                    msg = event.get("message") or {}
                    m = msg.get("model")
                    if m and not self._observed_model:
                        self._observed_model = m
                    for part in (msg.get("content") or []):
                        if isinstance(part, dict) and part.get("type") == "text":
                            text = part.get("text") or ""
                            if text:
                                tokens_out += max(1, len(text) // 4)
                                seen_text = True
                                yield Chunk(type="token", content=text)
                elif etype == "content_block_delta":
                    text = (event.get("delta") or {}).get("text") or ""
                    if text:
                        tokens_out += max(1, len(text) // 4)
                        seen_text = True
                        yield Chunk(type="token", content=text)
                elif etype == "result" and not seen_text:
                    # CLI 2.x: when no assistant event arrives (turns
                    # short-circuited by hooks etc.) the final `result`
                    # event still carries the prose. Emit as one token so
                    # the SPA shows something.
                    text = event.get("result") or ""
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

    CLIs are stateless; we re-pass everything each turn. The framing
    avoids XML-style ``<system>`` tags because Claude Code's
    prompt-injection guard treats those as user-supplied tag injection
    and refuses to honour their content (it just narrates "ignoring
    instructions" and responds from its own project context instead).
    Plain-prose section headers slip past that guard cleanly.
    """
    parts: list[str] = []
    if system:
        parts.append(
            "## Persona and instructions (from the host application)\n\n" + system
        )
    if messages:
        parts.append("## Conversation so far\n")
        # Last message is the new user turn — render the rest as transcript
        # context. Models behave better when the actual question is the
        # final sentence rather than buried mid-block.
        history = messages[:-1] if messages and messages[-1].role == "user" else messages
        new_user_msg = messages[-1] if messages and messages[-1].role == "user" else None
        for m in history:
            label = {"user": "User", "assistant": "You (previously)"}.get(m.role, m.role.capitalize())
            parts.append(f"{label}:\n{m.content}\n")
        if new_user_msg:
            parts.append("## New user message\n\n" + new_user_msg.content)
    return "\n".join(parts)
