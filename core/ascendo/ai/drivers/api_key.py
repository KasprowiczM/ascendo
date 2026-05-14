"""ApiKeyBackend: wraps the Sesja 67 call_provider_inference() one-shot path.

Simulates streaming by chunking the one-shot response into ~16-char pieces.
This is degraded UX vs. real CLI streaming, but it works for users who
have an API key but no CLI installed.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from ..backend import Backend, Chunk, Message

# Re-export the existing call_provider_inference so tests can monkeypatch.
try:
    from ascendo.dashboard.routes.ai import call_provider_inference
except ImportError:  # pragma: no cover
    def call_provider_inference(**kwargs):
        raise NotImplementedError("dashboard.routes.ai not importable")


class ApiKeyBackend(Backend):
    def __init__(
        self,
        *,
        provider: str,
        api_key: str,
        model: str,
        base_url: str | None = None,
    ) -> None:
        self.name = f"api:{provider}"
        self.bin_name = None
        self.provider = provider
        self.api_key = api_key
        self.model = model
        self.base_url = base_url
        self.max_input_tokens = 128_000

    def is_available(self) -> bool:
        return bool(self.api_key) or self.provider in ("ollama", "lm_studio")

    def is_authenticated(self) -> bool:
        # No probe; the actual call will surface auth issues as failure.
        return self.is_available()

    def model_info(self) -> dict[str, str]:
        return {"backend": self.name, "model": self.model}

    async def stream(
        self,
        *,
        system: str,
        messages: list[Message],
        cancel_event: asyncio.Event,
    ) -> AsyncIterator[Chunk]:
        prompt = "\n".join(f"{m.role.upper()}: {m.content}" for m in messages)
        try:
            text = await asyncio.to_thread(
                call_provider_inference,
                provider=self.provider,
                api_key=self.api_key,
                model=self.model,
                system=system,
                prompt=prompt,
                base_url=self.base_url,
            )
        except Exception as e:
            yield Chunk(type="error", error=f"api call failed: {e}")
            yield Chunk(type="done", status="error")
            return
        # Chunk the one-shot text to simulate streaming.
        tokens_out = max(1, len(text) // 4)
        for i in range(0, len(text), 16):
            if cancel_event.is_set():
                yield Chunk(type="done", status="cancelled")
                return
            yield Chunk(type="token", content=text[i:i + 16])
            await asyncio.sleep(0)  # let cancel_event interrupt
        yield Chunk(type="done", status="success", tokens_out=tokens_out)
