"""Regression test: auth endpoints never leak secrets into log output.

The dashboard promises passwords and API keys are never logged.
This test fires real requests and asserts the custom redaction filter
keeps secrets out of captured log records.
"""
from __future__ import annotations

import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ascendo.dashboard import create_app


class _LogCapture(logging.Handler):
    """In-memory handler that records formatted messages."""

    def __init__(self) -> None:
        super().__init__()
        self.records: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(self.format(record))


@pytest.fixture
def log_capture() -> _LogCapture:
    cap = _LogCapture()
    cap.setLevel(logging.DEBUG)
    root = logging.getLogger("ascendo")
    root.addHandler(cap)
    root.setLevel(logging.DEBUG)
    yield cap
    root.removeHandler(cap)


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    from .test_dashboard import FakeAdapter

    app = create_app(adapter=FakeAdapter(), runs_dir=tmp_path)
    return TestClient(app)


def test_elevation_auth_password_not_logged(
    tmp_path: Path,
    log_capture: _LogCapture,
) -> None:
    """POST /elevation/auth with a password must not expose it in logs."""
    from .test_dashboard_elevation import _FakeAdapterWithElevation

    app = create_app(adapter=_FakeAdapterWithElevation(), runs_dir=tmp_path)
    client = TestClient(app)

    # Verify log_capture is active and receiving records
    logging.getLogger("ascendo").info("elevation auth test starting")

    resp = client.post(
        "/elevation/auth",
        json={"password": "SuperSecretP@ss123"},
    )
    assert resp.status_code == 401

    combined = "\n".join(log_capture.records)
    assert "elevation auth test starting" in combined
    assert "SuperSecretP@ss123" not in combined


def test_sudo_auth_stub_password_not_logged(
    client: TestClient,
    log_capture: _LogCapture,
) -> None:
    """POST /sudo/auth (legacy stub path) must not expose password."""
    resp = client.post(
        "/sudo/auth",
        json={"password": "AnotherSecret456!"},
    )
    assert resp.status_code in (200, 400, 401, 503)

    combined = "\n".join(log_capture.records)
    assert "AnotherSecret456!" not in combined


def test_ai_config_api_key_not_logged(
    client: TestClient,
    log_capture: _LogCapture,
) -> None:
    """POST /ai/config with an API key must redact it in logs."""
    resp = client.post(
        "/ai/config",
        json={"backend": "openai", "api_key": "sk-proj-test12345abcdef"},
    )
    assert resp.status_code in (200, 422, 500)

    combined = "\n".join(log_capture.records)
    assert "sk-proj-test12345abcdef" not in combined


@pytest.mark.parametrize(
    ("path", "payload", "secret"),
    [
        # /elevation/auth
        ("/elevation/auth", {"password": ["SuperSecretP@ss123"]}, "SuperSecretP@ss123"),
        ("/elevation/auth", {"password": 987654}, "987654"),
        ("/elevation/auth", {"password": {"val": "SuperSecretP@ss123"}}, "SuperSecretP@ss123"),
        # /sudo/auth
        ("/sudo/auth", {"password": ["AnotherSecret456!"]}, "AnotherSecret456!"),
        ("/sudo/auth", {"password": 123456}, "123456"),
        ("/sudo/auth", {"password": {"val": "AnotherSecret456!"}}, "AnotherSecret456!"),
        # /ai/config
        ("/ai/config", {"provider": "openai", "api_key": ["sk-proj-test12345abcdef"]}, "sk-proj-test12345abcdef"),
        ("/ai/config", {"api_key": 99999}, "99999"),
        ("/ai/config", {"api_key": {"val": "sk-secret"}}, "sk-secret"),
        # /ai/test-connection
        ("/ai/test-connection", {"provider": "openai", "api_key": ["sk-proj-test12345abcdef"]}, "sk-proj-test12345abcdef"),
        ("/ai/test-connection", {"provider": "openai", "api_key": 99999}, "99999"),
    ],
)
def test_validation_error_does_not_leak_secret_in_response(
    client: TestClient,
    path: str,
    payload: dict,
    secret: str,
) -> None:
    resp = client.post(path, json=payload)
    assert resp.status_code == 422
    assert secret not in resp.text

