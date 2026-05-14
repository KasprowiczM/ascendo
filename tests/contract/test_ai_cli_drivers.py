"""Per-driver smoke tests for the 4 CLI backends + ApiKeyBackend + resolver."""
from __future__ import annotations
import asyncio
import os
from pathlib import Path
import pytest

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "ai_cli"


@pytest.fixture(autouse=True)
def _path_with_fakes(monkeypatch):
    monkeypatch.setenv("PATH", str(FIXTURES) + os.pathsep + os.environ.get("PATH", ""))


# ========================= ClaudeCodeBackend =========================

def test_claude_code_name():
    from ascendo.ai.drivers.claude_code import ClaudeCodeBackend
    b = ClaudeCodeBackend(bin_name="fake-claude")
    assert b.name == "claude"


def test_claude_code_is_available_when_binary_present():
    from ascendo.ai.drivers.claude_code import ClaudeCodeBackend
    b = ClaudeCodeBackend(bin_name="fake-claude")
    assert b.is_available() is True


def test_claude_code_is_available_false_when_missing(monkeypatch):
    monkeypatch.setenv("PATH", "/nonexistent")
    from ascendo.ai.drivers.claude_code import ClaudeCodeBackend
    b = ClaudeCodeBackend(bin_name="fake-claude")
    assert b.is_available() is False


def test_claude_code_is_authenticated_when_probe_succeeds(monkeypatch):
    monkeypatch.setenv("FIXTURE_CASE", "auth_probe_ok")
    from ascendo.ai.drivers.claude_code import ClaudeCodeBackend
    b = ClaudeCodeBackend(bin_name="fake-claude")
    assert b.is_authenticated() is True


def test_claude_code_is_authenticated_false_when_probe_fails(monkeypatch):
    monkeypatch.setenv("FIXTURE_CASE", "auth_expired")
    from ascendo.ai.drivers.claude_code import ClaudeCodeBackend
    b = ClaudeCodeBackend(bin_name="fake-claude")
    assert b.is_authenticated() is False


@pytest.mark.asyncio
async def test_claude_code_stream_yields_tokens(monkeypatch):
    monkeypatch.setenv("FIXTURE_CASE", "success")
    from ascendo.ai.drivers.claude_code import ClaudeCodeBackend
    from ascendo.ai.backend import Message
    b = ClaudeCodeBackend(bin_name="fake-claude")
    cancel = asyncio.Event()
    chunks = []
    async for c in b.stream(
        system="You are helpful.",
        messages=[Message(role="user", content="hi")],
        cancel_event=cancel,
    ):
        chunks.append(c)
    assert any(c.type == "token" for c in chunks)
    assert chunks[-1].type == "done"
    assert chunks[-1].status == "success"


# ========================= GeminiCliBackend =========================

def test_gemini_cli_name():
    from ascendo.ai.drivers.gemini_cli import GeminiCliBackend
    b = GeminiCliBackend(bin_name="fake-gemini")
    assert b.name == "gemini"


def test_gemini_cli_is_available_when_binary_present():
    from ascendo.ai.drivers.gemini_cli import GeminiCliBackend
    b = GeminiCliBackend(bin_name="fake-gemini")
    assert b.is_available() is True


def test_gemini_cli_is_authenticated_when_probe_ok(monkeypatch):
    monkeypatch.setenv("FIXTURE_CASE", "auth_probe_ok")
    from ascendo.ai.drivers.gemini_cli import GeminiCliBackend
    b = GeminiCliBackend(bin_name="fake-gemini")
    assert b.is_authenticated() is True


@pytest.mark.asyncio
async def test_gemini_cli_stream_yields_text(monkeypatch):
    monkeypatch.setenv("FIXTURE_CASE", "success")
    from ascendo.ai.drivers.gemini_cli import GeminiCliBackend
    from ascendo.ai.backend import Message
    b = GeminiCliBackend(bin_name="fake-gemini")
    cancel = asyncio.Event()
    chunks = []
    async for c in b.stream(
        system="You are helpful.",
        messages=[Message(role="user", content="hi")],
        cancel_event=cancel,
    ):
        chunks.append(c)
    assert any(c.type == "token" for c in chunks)
    assert chunks[-1].type == "done"


# ========================= CodexCliBackend =========================

def test_codex_cli_name():
    from ascendo.ai.drivers.codex_cli import CodexCliBackend
    b = CodexCliBackend(bin_name="fake-codex")
    assert b.name == "codex"


def test_codex_cli_is_available():
    from ascendo.ai.drivers.codex_cli import CodexCliBackend
    b = CodexCliBackend(bin_name="fake-codex")
    assert b.is_available() is True


def test_codex_cli_authenticated(monkeypatch):
    monkeypatch.setenv("FIXTURE_CASE", "auth_probe_ok")
    from ascendo.ai.drivers.codex_cli import CodexCliBackend
    b = CodexCliBackend(bin_name="fake-codex")
    assert b.is_authenticated() is True


@pytest.mark.asyncio
async def test_codex_cli_streams_text_events(monkeypatch):
    monkeypatch.setenv("FIXTURE_CASE", "success")
    from ascendo.ai.drivers.codex_cli import CodexCliBackend
    from ascendo.ai.backend import Message
    b = CodexCliBackend(bin_name="fake-codex")
    cancel = asyncio.Event()
    chunks = []
    async for c in b.stream(
        system="", messages=[Message(role="user", content="hi")],
        cancel_event=cancel,
    ):
        chunks.append(c)
    assert any(c.type == "token" for c in chunks)
    assert chunks[-1].type == "done"


# ========================= OpencodeBackend =========================

def test_opencode_name():
    from ascendo.ai.drivers.opencode import OpencodeBackend
    b = OpencodeBackend(bin_name="fake-opencode")
    assert b.name == "opencode"


def test_opencode_is_available():
    from ascendo.ai.drivers.opencode import OpencodeBackend
    b = OpencodeBackend(bin_name="fake-opencode")
    assert b.is_available() is True


def test_opencode_is_authenticated(monkeypatch):
    monkeypatch.setenv("FIXTURE_CASE", "auth_probe_ok")
    from ascendo.ai.drivers.opencode import OpencodeBackend
    b = OpencodeBackend(bin_name="fake-opencode")
    assert b.is_authenticated() is True


@pytest.mark.asyncio
async def test_opencode_streams_text(monkeypatch):
    monkeypatch.setenv("FIXTURE_CASE", "success")
    from ascendo.ai.drivers.opencode import OpencodeBackend
    from ascendo.ai.backend import Message
    b = OpencodeBackend(bin_name="fake-opencode")
    cancel = asyncio.Event()
    chunks = []
    async for c in b.stream(
        system="", messages=[Message(role="user", content="hi")],
        cancel_event=cancel,
    ):
        chunks.append(c)
    assert any(c.type == "token" for c in chunks)
    assert chunks[-1].type == "done"


# ========================= ApiKeyBackend =========================

def test_api_key_backend_name():
    from ascendo.ai.drivers.api_key import ApiKeyBackend
    b = ApiKeyBackend(provider="anthropic", api_key="sk-test", model="claude-sonnet-4-7")
    assert b.name == "api:anthropic"


def test_api_key_backend_is_available_when_key_set():
    from ascendo.ai.drivers.api_key import ApiKeyBackend
    b = ApiKeyBackend(provider="anthropic", api_key="sk-test", model="claude-sonnet-4-7")
    assert b.is_available() is True


def test_api_key_backend_is_unavailable_when_key_missing():
    from ascendo.ai.drivers.api_key import ApiKeyBackend
    b = ApiKeyBackend(provider="anthropic", api_key="", model="claude-sonnet-4-7")
    assert b.is_available() is False


def test_api_key_backend_local_provider_ok_without_key():
    from ascendo.ai.drivers.api_key import ApiKeyBackend
    b = ApiKeyBackend(provider="ollama", api_key="", model="llama3.1")
    assert b.is_available() is True


@pytest.mark.asyncio
async def test_api_key_backend_simulates_streaming(monkeypatch):
    from ascendo.ai.drivers import api_key as mod
    from ascendo.ai.backend import Message

    def fake_call(*, provider, api_key, model, system, prompt, **kw):
        return "Hello world from API"

    monkeypatch.setattr(mod, "call_provider_inference", fake_call)
    b = mod.ApiKeyBackend(provider="anthropic", api_key="sk-test", model="claude-sonnet-4-7")
    cancel = asyncio.Event()
    chunks = []
    async for c in b.stream(
        system="", messages=[Message(role="user", content="hi")],
        cancel_event=cancel,
    ):
        chunks.append(c)
    full = "".join(c.content or "" for c in chunks if c.type == "token")
    assert "Hello" in full
    assert chunks[-1].type == "done"


# ========================= BackendResolver =========================

def test_resolver_prefers_user_choice(monkeypatch):
    monkeypatch.setenv("FIXTURE_CASE", "auth_probe_ok")
    from ascendo.ai.backend import BackendResolver
    r = BackendResolver(
        preferred="gemini",
        bin_overrides={"claude": "fake-claude", "gemini": "fake-gemini",
                       "codex": "fake-codex", "opencode": "fake-opencode"},
        api_config=None,
    )
    b = r.resolve()
    assert b is not None and b.name == "gemini"


def test_resolver_falls_through_to_first_available(monkeypatch):
    monkeypatch.setenv("FIXTURE_CASE", "auth_probe_ok")
    from ascendo.ai.backend import BackendResolver
    r = BackendResolver(
        preferred=None,
        bin_overrides={"claude": "fake-claude", "gemini": "fake-gemini",
                       "codex": "fake-codex", "opencode": "fake-opencode"},
        api_config=None,
    )
    b = r.resolve()
    assert b is not None and b.name == "claude"  # first in fixed order


def test_resolver_uses_api_when_no_cli(monkeypatch):
    monkeypatch.setenv("PATH", "/nonexistent")
    from ascendo.ai.backend import BackendResolver
    r = BackendResolver(
        preferred=None, bin_overrides={},
        api_config={"provider": "anthropic", "api_key": "sk-test", "model": "claude-sonnet-4-7"},
    )
    b = r.resolve()
    assert b is not None and b.name.startswith("api:")


def test_resolver_returns_none_when_nothing(monkeypatch):
    monkeypatch.setenv("PATH", "/nonexistent")
    from ascendo.ai.backend import BackendResolver
    r = BackendResolver(preferred=None, bin_overrides={}, api_config=None)
    assert r.resolve() is None


def test_resolver_list_status(monkeypatch):
    from ascendo.ai.backend import BackendResolver
    r = BackendResolver(
        preferred=None,
        bin_overrides={"claude": "fake-claude", "gemini": "fake-gemini",
                       "codex": "fake-codex", "opencode": "fake-opencode"},
        api_config={"provider": "anthropic", "api_key": "sk-test", "model": "claude-sonnet-4-7"},
    )
    statuses = r.list_status()
    names = [s["name"] for s in statuses]
    assert "claude" in names
    assert "gemini" in names
    assert "codex" in names
    assert "opencode" in names
    assert "api:anthropic" in names
