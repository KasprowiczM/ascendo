"""Contract tests for ``routes/ai.py``.

Covers:
    * GET  /ai/providers — static metadata response shape.
    * POST /ai/test-connection — happy path with mocked urllib + error path.
    * GET  /ai/config / POST /ai/config — round-trip with redaction.
"""
from __future__ import annotations

import io
import json
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError

import pytest
from fastapi.testclient import TestClient

from ascendo.dashboard import create_app


@pytest.fixture
def app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Build an app + redirect AI config to a tmp file."""
    monkeypatch.setenv("ASCENDO_AI_CONFIG", str(tmp_path / "ai.json"))
    monkeypatch.setenv("ASCENDO_RUNS_DIR", str(tmp_path / "runs"))
    return create_app(adapter=None, runs_dir=tmp_path / "runs")


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c


# ── /ai/providers ────────────────────────────────────────────────────────


def test_providers_endpoint_lists_all_providers(client: TestClient) -> None:
    r = client.get("/ai/providers")
    assert r.status_code == 200
    body = r.json()
    ids = [p["id"] for p in body["providers"]]
    assert ids == ["anthropic", "openai", "google", "openrouter", "ollama", "lm_studio", "litellm"]
    # Local providers + openrouter must request a base_url field.
    assert set(body["providers_with_url"]) == {"ollama", "lm_studio", "litellm", "openrouter"}


# ── /ai/config round-trip ────────────────────────────────────────────────


def test_config_save_and_load_round_trip_with_redaction(client: TestClient) -> None:
    # Initial GET returns empty.
    r0 = client.get("/ai/config")
    assert r0.status_code == 200
    assert r0.json().get("has_api_key") is False

    # Save config with a secret.
    payload = {
        "provider": "anthropic",
        "api_key":  "sk-ant-xxxxxxxxxxxxxxxx",
        "base_url": "",
        "model":    "claude-haiku-4-5",
    }
    r1 = client.post("/ai/config", json=payload)
    assert r1.status_code == 200
    body1 = r1.json()
    assert body1["provider"] == "anthropic"
    assert body1["model"] == "claude-haiku-4-5"
    # api_key MUST be redacted on the response.
    assert body1["api_key"] == "sk-***"
    assert body1["has_api_key"] is True

    # Re-read returns the same redacted shape.
    r2 = client.get("/ai/config")
    body2 = r2.json()
    assert body2["api_key"] == "sk-***"
    assert body2["has_api_key"] is True
    # Sentinel-passthrough: posting the redacted value preserves the key.
    r3 = client.post("/ai/config", json={**payload, "api_key": "sk-***"})
    assert r3.status_code == 200
    assert r3.json()["has_api_key"] is True


# ── /ai/test-connection ──────────────────────────────────────────────────


def _mock_urlopen_returning(payload: dict) -> object:
    """Build a fake urlopen context manager that returns ``payload`` as JSON."""

    class _FakeResp:
        def __init__(self, body: bytes) -> None:
            self._body = io.BytesIO(body)

        def read(self) -> bytes:
            return self._body.read()

        def __enter__(self):
            return self

        def __exit__(self, *exc) -> None:
            return None

    body = json.dumps(payload).encode("utf-8")
    return _FakeResp(body)


def test_test_connection_anthropic_happy_path(client: TestClient) -> None:
    """POST /ai/test-connection with provider=anthropic returns models list."""
    fake_payload = {
        "data": [
            {"id": "claude-haiku-4-5",  "display_name": "Claude Haiku 4.5"},
            {"id": "claude-sonnet-4-7", "display_name": "Claude Sonnet 4.7"},
        ],
    }
    with patch(
        "ascendo.dashboard.routes.ai.urllib_request.urlopen",
        return_value=_mock_urlopen_returning(fake_payload),
    ):
        r = client.post(
            "/ai/test-connection",
            json={"provider": "anthropic", "api_key": "sk-ant-xxx"},
        )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert len(body["models"]) == 2
    assert body["models"][0]["id"] == "claude-haiku-4-5"
    assert body["models"][0]["label"] == "Claude Haiku 4.5"


def test_test_connection_openai_happy_path(client: TestClient) -> None:
    fake_payload = {"data": [{"id": "gpt-4o-mini"}, {"id": "gpt-4o"}]}
    with patch(
        "ascendo.dashboard.routes.ai.urllib_request.urlopen",
        return_value=_mock_urlopen_returning(fake_payload),
    ):
        r = client.post(
            "/ai/test-connection",
            json={"provider": "openai", "api_key": "sk-xxx"},
        )
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert {m["id"] for m in r.json()["models"]} == {"gpt-4o-mini", "gpt-4o"}


def test_test_connection_google_happy_path(client: TestClient) -> None:
    """Gemini's models[] uses name=models/<id> + supportedGenerationMethods."""
    fake_payload = {
        "models": [
            {
                "name": "models/gemini-1.5-pro",
                "displayName": "Gemini 1.5 Pro",
                "supportedGenerationMethods": ["generateContent"],
            },
            {
                "name": "models/embedding-001",
                "displayName": "Embedding 001",
                "supportedGenerationMethods": ["embedContent"],
            },
        ],
    }
    with patch(
        "ascendo.dashboard.routes.ai.urllib_request.urlopen",
        return_value=_mock_urlopen_returning(fake_payload),
    ):
        r = client.post(
            "/ai/test-connection",
            json={"provider": "google", "api_key": "AIzaXxx"},
        )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    # Embedding model is filtered out — only generateContent-capable.
    assert [m["id"] for m in body["models"]] == ["gemini-1.5-pro"]
    assert body["models"][0]["label"] == "Gemini 1.5 Pro"


def test_test_connection_lm_studio_happy_path(client: TestClient) -> None:
    """LM Studio is OpenAI-compatible; same /models response shape."""
    fake_payload = {"data": [{"id": "Llama-3.2-3B-Instruct"}]}
    with patch(
        "ascendo.dashboard.routes.ai.urllib_request.urlopen",
        return_value=_mock_urlopen_returning(fake_payload),
    ):
        r = client.post(
            "/ai/test-connection",
            json={"provider": "lm_studio", "base_url": "http://localhost:1234/v1"},
        )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["models"][0]["id"] == "Llama-3.2-3B-Instruct"


def test_test_connection_network_error_is_caught(client: TestClient) -> None:
    """A urllib URLError must NOT raise 500 — must return ok=false with msg."""
    with patch(
        "ascendo.dashboard.routes.ai.urllib_request.urlopen",
        side_effect=URLError("connection refused"),
    ):
        r = client.post(
            "/ai/test-connection",
            json={"provider": "ollama", "base_url": "http://localhost:11434"},
        )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert "connection refused" in body["error"].lower()


def test_test_connection_unimplemented_provider_returns_friendly_error(
    client: TestClient,
) -> None:
    # litellm is the remaining scaffold-only provider; google + lm_studio
    # were promoted to fully implemented in Sesja 31.
    r = client.post("/ai/test-connection", json={"provider": "litellm", "api_key": "x"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert "not yet implemented" in body["error"].lower()


def test_test_connection_anthropic_without_api_key_returns_error(
    client: TestClient,
) -> None:
    r = client.post("/ai/test-connection", json={"provider": "anthropic"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert "api key" in body["error"].lower()
