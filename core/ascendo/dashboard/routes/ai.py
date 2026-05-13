"""AI provider configuration + connection-test endpoints.

The Suggestions tab now uses a 3-step wizard:

    1. Pick provider (anthropic / openai / google / openrouter / ollama / lm_studio / litellm)
    2. Enter API key (or local URL for local providers) and click "Test connection".
    3. Pick a model from the live-fetched list and save.

This module backs steps 2 and 3:

    POST /ai/test-connection  — `{provider, api_key?, base_url?}` →
                                `{ok: bool, models?: [{id, label?}], error?: str}`
    GET  /ai/config           — saved settings; `api_key` redacted as ``sk-***``.
    POST /ai/config           — persist `{provider, api_key, base_url, model}`.

Credentials are written to ``~/.config/ascendo/ai.json`` (NOT to the repo).
``$ASCENDO_AI_CONFIG`` overrides the path (tests inject it).

All provider calls use the Python stdlib (``urllib.request``) — no new pip
deps. Each call has a 5s timeout and network/JSON errors are caught and
returned as ``{ok: false, error: "..."}`` rather than raising 500s.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any
import urllib.parse
from urllib import error as urllib_error
from urllib import request as urllib_request

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

_log = logging.getLogger(__name__)
router = APIRouter(tags=["ai"])

_TIMEOUT_SEC = 5.0
_REDACT = "sk-***"

# Providers that need a base_url field exposed in the wizard.
_PROVIDERS_WITH_URL = {"ollama", "lm_studio", "litellm", "openrouter"}

# Default base URLs.
_DEFAULT_BASE_URLS = {
    "ollama":     "http://localhost:11434",
    "lm_studio":  "http://localhost:1234/v1",
    "litellm":    "http://localhost:4000",
    "openrouter": "https://openrouter.ai/api/v1",
    "anthropic":  "https://api.anthropic.com",
    "openai":     "https://api.openai.com/v1",
    "google":     "https://generativelanguage.googleapis.com/v1beta",
}


# ── Config persistence ───────────────────────────────────────────────────


def config_file() -> Path:
    """Path to the AI config JSON.

    Order of precedence: ``$ASCENDO_AI_CONFIG`` > ``~/.config/ascendo/ai.json``.
    """
    override = os.environ.get("ASCENDO_AI_CONFIG")
    if override:
        return Path(override)
    return Path.home() / ".config" / "ascendo" / "ai.json"


def _read_config() -> dict[str, Any]:
    path = config_file()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError) as exc:
        _log.warning("failed to read AI config at %s: %s", path, exc)
        return {}


def _write_config(data: dict[str, Any]) -> Path:
    path = config_file()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(path)
    except OSError as exc:
        _log.exception("failed to persist AI config to %s", path)
        raise HTTPException(
            status_code=500,
            detail=f"could not write AI config: {exc}",
        ) from exc
    return path


# ── Wire schemas ─────────────────────────────────────────────────────────


class AITestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str = Field(..., min_length=1)
    api_key: str | None = None
    base_url: str | None = None


class AIConfigRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str = Field(default="")
    api_key: str = Field(default="")
    base_url: str = Field(default="")
    model: str = Field(default="")


# ── Provider drivers ─────────────────────────────────────────────────────


def _http_get_json(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: float = _TIMEOUT_SEC,
) -> dict[str, Any]:
    """GET a URL and parse JSON. Raises ``urllib.error.URLError`` /
    ``json.JSONDecodeError`` / ``UnicodeDecodeError`` on failure — caller
    catches and translates to ``{ok: false, error}``."""
    req = urllib_request.Request(url, headers=dict(headers or {}))  # noqa: S310
    with urllib_request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        body = resp.read()
    return json.loads(body.decode("utf-8"))


def _provider_anthropic(api_key: str, base_url: str | None) -> dict[str, Any]:
    if not api_key:
        return {"ok": False, "error": "Anthropic requires an API key"}
    base = (base_url or _DEFAULT_BASE_URLS["anthropic"]).rstrip("/")
    url = f"{base}/v1/models"
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "accept": "application/json",
    }
    payload = _http_get_json(url, headers=headers)
    raw_models = payload.get("data") or payload.get("models") or []
    models = [
        {"id": m.get("id"), "label": m.get("display_name") or m.get("id")}
        for m in raw_models
        if isinstance(m, dict) and m.get("id")
    ]
    return {"ok": True, "models": models}


def _provider_openai(api_key: str, base_url: str | None) -> dict[str, Any]:
    if not api_key:
        return {"ok": False, "error": "OpenAI requires an API key"}
    base = (base_url or _DEFAULT_BASE_URLS["openai"]).rstrip("/")
    url = f"{base}/models"
    headers = {"Authorization": f"Bearer {api_key}", "accept": "application/json"}
    payload = _http_get_json(url, headers=headers)
    raw_models = payload.get("data") or []
    models = [
        {"id": m.get("id"), "label": m.get("id")}
        for m in raw_models
        if isinstance(m, dict) and m.get("id")
    ]
    return {"ok": True, "models": models}


def _provider_openrouter(api_key: str, base_url: str | None) -> dict[str, Any]:
    base = (base_url or _DEFAULT_BASE_URLS["openrouter"]).rstrip("/")
    url = f"{base}/models"
    headers: dict[str, str] = {"accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    payload = _http_get_json(url, headers=headers)
    raw_models = payload.get("data") or payload.get("models") or []
    models = [
        {"id": m.get("id"), "label": m.get("name") or m.get("id")}
        for m in raw_models
        if isinstance(m, dict) and m.get("id")
    ]
    return {"ok": True, "models": models}


def _provider_ollama(api_key: str, base_url: str | None) -> dict[str, Any]:
    base = (base_url or _DEFAULT_BASE_URLS["ollama"]).rstrip("/")
    url = f"{base}/api/tags"
    payload = _http_get_json(url, headers={"accept": "application/json"})
    raw_models = payload.get("models") or []
    models = [
        {"id": m.get("name"), "label": m.get("name")}
        for m in raw_models
        if isinstance(m, dict) and m.get("name")
    ]
    return {"ok": True, "models": models}


def _provider_google(api_key: str, base_url: str | None) -> dict[str, Any]:
    if not api_key:
        return {"ok": False, "error": "Google Gemini requires an API key"}
    base = (base_url or _DEFAULT_BASE_URLS["google"]).rstrip("/")
    # API key is a query param, not a header. Filter to chat-capable models
    # (those with generateContent in supportedGenerationMethods).
    url = f"{base}/models?key={urllib.parse.quote(api_key, safe='')}"
    payload = _http_get_json(url, headers={"accept": "application/json"})
    raw_models = payload.get("models") or []
    models: list[dict[str, str]] = []
    for m in raw_models:
        if not isinstance(m, dict):
            continue
        name = m.get("name") or ""
        methods = m.get("supportedGenerationMethods") or []
        if "generateContent" not in methods:
            continue
        # name comes back as "models/gemini-1.5-pro" — strip the prefix.
        ident = name.split("/", 1)[1] if name.startswith("models/") else name
        if not ident:
            continue
        models.append({"id": ident, "label": m.get("displayName") or ident})
    return {"ok": True, "models": models}


def _provider_lm_studio(api_key: str, base_url: str | None) -> dict[str, Any]:
    # LM Studio is OpenAI-compatible. Default port 1234. Auth header is
    # required by some clients but ignored server-side; send a sentinel.
    base = (base_url or _DEFAULT_BASE_URLS["lm_studio"]).rstrip("/")
    url = f"{base}/models"
    headers = {
        "Authorization": f"Bearer {api_key or 'not-needed'}",
        "accept": "application/json",
    }
    payload = _http_get_json(url, headers=headers)
    raw_models = payload.get("data") or []
    models = [
        {"id": m.get("id"), "label": m.get("id")}
        for m in raw_models
        if isinstance(m, dict) and m.get("id")
    ]
    return {"ok": True, "models": models}


def _provider_not_implemented(name: str) -> dict[str, Any]:
    return {
        "ok": False,
        "error": f"Provider '{name}' is not yet implemented; pick another or open an issue.",
    }


# ── Inference helpers (Sesja 67) ─────────────────────────────────────────


def _http_post_json(
    url: str,
    body: dict[str, Any],
    *,
    headers: dict[str, str] | None = None,
    timeout: float = _TIMEOUT_SEC,
) -> dict[str, Any]:
    """POST JSON and parse JSON response.

    Raises ``urllib.error.URLError`` / ``json.JSONDecodeError`` on
    failure — caller catches and returns ``{ok: false, error}``.
    """
    data = json.dumps(body).encode("utf-8")
    h = {"Content-Type": "application/json", "Accept": "application/json"}
    h.update(headers or {})
    req = urllib_request.Request(url, data=data, headers=h, method="POST")  # noqa: S310
    with urllib_request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        raw = resp.read()
    return json.loads(raw.decode("utf-8"))


def call_provider_inference(
    *,
    provider: str,
    model: str,
    api_key: str,
    base_url: str | None,
    system_prompt: str,
    user_prompt: str,
    max_tokens: int = 1200,
    timeout: float = 8.0,
) -> dict[str, Any]:
    """Generate text via the configured provider.

    Returns ``{ok: bool, content?: str, error?: str}``. Every supported
    provider is wired (anthropic / openai / openrouter / ollama / google
    / lm_studio); unknown providers return a not-implemented error.

    Network failures, bad keys, malformed responses all return
    ``{ok: false, error: <human-readable>}`` rather than raising.
    Caller decides whether to surface to the user or silently fall back.
    """
    try:
        if provider == "anthropic":
            base = (base_url or _DEFAULT_BASE_URLS["anthropic"]).rstrip("/")
            payload = _http_post_json(
                f"{base}/v1/messages",
                {
                    "model": model,
                    "max_tokens": max_tokens,
                    "system": system_prompt,
                    "messages": [{"role": "user", "content": user_prompt}],
                },
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                },
                timeout=timeout,
            )
            # Response shape: {"content": [{"type": "text", "text": "..."}]}
            blocks = payload.get("content") or []
            text = "".join(
                b.get("text", "") for b in blocks
                if isinstance(b, dict) and b.get("type") == "text"
            )
            return {"ok": True, "content": text}

        if provider in ("openai", "openrouter", "lm_studio"):
            base = (base_url or _DEFAULT_BASE_URLS[provider]).rstrip("/")
            url = f"{base}/chat/completions"
            headers: dict[str, str] = {}
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"
            elif provider == "lm_studio":
                headers["Authorization"] = "Bearer not-needed"
            payload = _http_post_json(
                url,
                {
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user",   "content": user_prompt},
                    ],
                    "max_tokens": max_tokens,
                    "temperature": 0.4,
                },
                headers=headers,
                timeout=timeout,
            )
            choices = payload.get("choices") or []
            text = ""
            if choices and isinstance(choices[0], dict):
                msg = choices[0].get("message") or {}
                text = msg.get("content") or ""
            return {"ok": True, "content": text}

        if provider == "ollama":
            base = (base_url or _DEFAULT_BASE_URLS["ollama"]).rstrip("/")
            payload = _http_post_json(
                f"{base}/api/chat",
                {
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user",   "content": user_prompt},
                    ],
                    "stream": False,
                    "options": {"temperature": 0.4},
                },
                timeout=timeout,
            )
            msg = payload.get("message") or {}
            return {"ok": True, "content": msg.get("content") or ""}

        if provider == "google":
            if not api_key:
                return {"ok": False, "error": "Google Gemini requires an API key"}
            base = (base_url or _DEFAULT_BASE_URLS["google"]).rstrip("/")
            url = (
                f"{base}/models/{urllib.parse.quote(model)}:generateContent"
                f"?key={urllib.parse.quote(api_key, safe='')}"
            )
            payload = _http_post_json(
                url,
                {
                    "contents": [{"parts": [{"text": user_prompt}]}],
                    "systemInstruction": {"parts": [{"text": system_prompt}]},
                    "generationConfig": {
                        "temperature": 0.4,
                        "maxOutputTokens": max_tokens,
                    },
                },
                timeout=timeout,
            )
            cands = payload.get("candidates") or []
            text = ""
            if cands and isinstance(cands[0], dict):
                parts = (cands[0].get("content") or {}).get("parts") or []
                text = "".join(
                    p.get("text", "") for p in parts if isinstance(p, dict)
                )
            return {"ok": True, "content": text}

        return _provider_not_implemented(provider)
    except urllib_error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", errors="replace")[:200]
        except Exception:  # noqa: BLE001
            pass
        return {"ok": False, "error": f"HTTP {exc.code}: {body or exc.reason}"}
    except urllib_error.URLError as exc:
        return {"ok": False, "error": f"network error: {exc.reason}"}
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        return {"ok": False, "error": f"malformed response: {exc}"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


_PROVIDER_DRIVERS = {
    "anthropic":  _provider_anthropic,
    "openai":     _provider_openai,
    "openrouter": _provider_openrouter,
    "ollama":     _provider_ollama,
    "google":     _provider_google,
    "lm_studio":  _provider_lm_studio,
}


def _test_connection(provider: str, api_key: str, base_url: str | None) -> dict[str, Any]:
    """Drive the provider; catch all network/JSON errors."""
    driver = _PROVIDER_DRIVERS.get(provider)
    if driver is None:
        return _provider_not_implemented(provider)
    try:
        return driver(api_key, base_url)
    except urllib_error.HTTPError as exc:
        # Surface a friendly error including upstream status code.
        body = ""
        try:
            body = exc.read().decode("utf-8", errors="replace")[:200]
        except Exception:  # noqa: BLE001
            body = ""
        msg = f"HTTP {exc.code} from provider"
        if body:
            msg = f"{msg}: {body}"
        return {"ok": False, "error": msg}
    except urllib_error.URLError as exc:
        return {"ok": False, "error": f"Network error: {exc.reason}"}
    except (TimeoutError, OSError) as exc:
        return {"ok": False, "error": f"Network error: {exc}"}
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        return {"ok": False, "error": f"Invalid response from provider: {exc}"}


# ── Endpoints ────────────────────────────────────────────────────────────


@router.get("/ai/providers")
async def ai_providers() -> dict[str, Any]:
    """List supported providers + which need a base_url field."""
    return {
        "providers": [
            {"id": "anthropic",  "label": "Anthropic (Claude)",     "needs_url": False, "implemented": True},
            {"id": "openai",     "label": "OpenAI",                 "needs_url": False, "implemented": True},
            {"id": "google",     "label": "Google Gemini",          "needs_url": False, "implemented": True},
            {"id": "openrouter", "label": "OpenRouter",             "needs_url": True,  "implemented": True},
            {"id": "ollama",     "label": "Ollama (local)",         "needs_url": True,  "implemented": True},
            {"id": "lm_studio",  "label": "LM Studio (local)",      "needs_url": True,  "implemented": True},
            {"id": "litellm",    "label": "LiteLLM (local proxy)",  "needs_url": True,  "implemented": False},
        ],
        "providers_with_url": sorted(_PROVIDERS_WITH_URL),
        "default_base_urls": _DEFAULT_BASE_URLS,
    }


@router.post("/ai/test-connection")
async def ai_test_connection(req: AITestRequest) -> dict[str, Any]:
    """Test the connection to ``provider`` and return a model list.

    Always returns 200 with ``{ok: bool, ...}``; never raises 5xx for
    network failures (so the SPA can render the error inline).
    """
    return _test_connection(req.provider, req.api_key or "", req.base_url)


@router.get("/ai/config")
async def ai_config_get() -> dict[str, Any]:
    """Return the saved AI config with ``api_key`` redacted."""
    cfg = _read_config()
    out = dict(cfg)
    if cfg.get("api_key"):
        out["api_key"] = _REDACT
    out["has_api_key"] = bool(cfg.get("api_key"))
    return out


@router.post("/ai/config")
async def ai_config_post(req: AIConfigRequest) -> dict[str, Any]:
    """Persist the AI config. Empty ``api_key`` keeps the previously saved key."""
    cfg = _read_config()
    new_cfg = {
        "provider": req.provider,
        "base_url": req.base_url,
        "model":    req.model,
    }
    # Preserve the existing key if the SPA sent the redaction sentinel
    # (= user opened settings, didn't retype the key).
    if req.api_key and req.api_key != _REDACT:
        new_cfg["api_key"] = req.api_key
    elif cfg.get("api_key"):
        new_cfg["api_key"] = cfg["api_key"]
    else:
        new_cfg["api_key"] = ""
    path = _write_config(new_cfg)
    out = dict(new_cfg)
    if out.get("api_key"):
        out["api_key"] = _REDACT
    out["has_api_key"] = bool(new_cfg.get("api_key"))
    out["file"] = str(path)
    return out


__all__ = [
    "router",
    "config_file",
]
