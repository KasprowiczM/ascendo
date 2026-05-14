"""Backend ABC + Chunk model + TurnRegistry + BackendResolver for AI chat.

Mirrors M2.10's RunRegistry pattern (core/ascendo/orchestrator/run_async.py).
"""
from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from collections import OrderedDict
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict


class Message(BaseModel):
    """One conversation turn (user or assistant)."""
    model_config = ConfigDict(extra="forbid")

    role: Literal["user", "assistant", "system", "tool_result"]
    content: str


class Chunk(BaseModel):
    """One streaming chunk produced by a backend during a turn."""
    model_config = ConfigDict(extra="forbid")

    type: Literal["token", "action_proposal", "context_trimmed", "done", "error"]
    content: str | None = None
    action: dict | None = None
    status: Literal["success", "cancelled", "error"] | None = None
    error: str | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None


class Backend(ABC):
    """One AI backend: a CLI driver, an API-key driver, or a future remote service."""

    name: str = ""
    bin_name: str | None = None
    max_input_tokens: int = 12_000

    @abstractmethod
    def is_available(self) -> bool:
        """Cheap check: binary on PATH + version probe. No network."""

    @abstractmethod
    def is_authenticated(self) -> bool:
        """3-second probe call to verify auth works. May be network-y."""

    @abstractmethod
    async def stream(
        self,
        *,
        system: str,
        messages: list[Message],
        cancel_event: asyncio.Event,
    ) -> AsyncIterator[Chunk]:
        """Stream chunks. Closes when message complete or cancel_event set."""

    @abstractmethod
    def model_info(self) -> dict[str, str]:
        """Backend-specific model name + provider for the SPA footer pill."""


class TurnStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class TurnState:
    """One in-flight chat turn."""
    turn_id: str
    conversation_id: str
    backend_name: str
    status: TurnStatus = TurnStatus.PENDING
    error: str | None = None
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    started_at: float | None = None
    ended_at: float | None = None
    tokens_in: int = 0
    tokens_out: int = 0


class TurnRegistry:
    """Thread-safe, bounded registry of in-flight turns.

    Mirror of M2.10's RunRegistry (core/ascendo/orchestrator/run_async.py).
    Evicts completed turns first when full; never evicts running turns.
    """

    def __init__(self, max_runs: int = 256) -> None:
        self._states: OrderedDict[str, TurnState] = OrderedDict()
        self._max_runs = max_runs

    def register(self, state: TurnState) -> None:
        if len(self._states) >= self._max_runs:
            self._evict_one()
        self._states[state.turn_id] = state

    def get(self, turn_id: str) -> TurnState | None:
        return self._states.get(turn_id)

    def remove(self, turn_id: str) -> None:
        self._states.pop(turn_id, None)

    def _evict_one(self) -> None:
        """Evict oldest completed/failed/cancelled turn; never running ones."""
        for tid, state in list(self._states.items()):
            if state.status in (TurnStatus.COMPLETED, TurnStatus.FAILED, TurnStatus.CANCELLED):
                del self._states[tid]
                return
        # All running: don't evict; let it grow past cap.


# ============================================================================
# BackendResolver
# ============================================================================

DEFAULT_CLI_ORDER = ("claude", "gemini", "codex", "opencode")


class BackendResolver:
    """Resolves the first available backend.

    Order:
    1. User preference (preferred= arg, typically from settings)
    2. First installed CLI in DEFAULT_CLI_ORDER
    3. ApiKeyBackend if api_config provided
    4. None
    """

    def __init__(
        self,
        *,
        preferred: str | None,
        bin_overrides: dict[str, str] | None = None,
        api_config: dict | None = None,
    ) -> None:
        self.preferred = preferred
        self.bin_overrides = bin_overrides or {}
        self.api_config = api_config

    def _build(self, name: str) -> Backend | None:
        if name == "claude":
            from .drivers.claude_code import ClaudeCodeBackend
            return ClaudeCodeBackend(bin_name=self.bin_overrides.get("claude", "claude"))
        if name == "gemini":
            from .drivers.gemini_cli import GeminiCliBackend
            return GeminiCliBackend(bin_name=self.bin_overrides.get("gemini", "gemini"))
        if name == "codex":
            from .drivers.codex_cli import CodexCliBackend
            return CodexCliBackend(bin_name=self.bin_overrides.get("codex", "codex"))
        if name == "opencode":
            from .drivers.opencode import OpencodeBackend
            return OpencodeBackend(bin_name=self.bin_overrides.get("opencode", "opencode"))
        if name.startswith("api:") and self.api_config:
            from .drivers.api_key import ApiKeyBackend
            return ApiKeyBackend(**self.api_config)
        return None

    def resolve(self) -> Backend | None:
        candidates: list[str] = []
        if self.preferred:
            candidates.append(self.preferred)
        candidates.extend(DEFAULT_CLI_ORDER)
        if self.api_config:
            candidates.append(f"api:{self.api_config.get('provider', '')}")

        seen: set[str] = set()
        for name in candidates:
            if name in seen:
                continue
            seen.add(name)
            try:
                b = self._build(name)
            except Exception:
                continue
            if b and b.is_available():
                return b
        return None

    def list_status(self) -> list[dict[str, str]]:
        out: list[dict[str, str]] = []
        for name in DEFAULT_CLI_ORDER:
            try:
                b = self._build(name)
            except Exception:
                continue
            if b is None:
                continue
            available = b.is_available()
            out.append({
                "name": name,
                "available": "true" if available else "false",
                "binary": b.bin_name or "",
            })
        if self.api_config:
            try:
                b = self._build(f"api:{self.api_config.get('provider')}")
                if b is not None:
                    out.append({
                        "name": b.name,
                        "available": "true" if b.is_available() else "false",
                        "binary": "",
                    })
            except Exception:
                pass
        return out
