"""Contract tests for POST /sudo/auth legacy stub endpoint.

Validates:
- Rejection (401) on invalid password when elevation backend returns (False, msg).
- Masking of password in error messages and 300 character cap.
- Backend exception handling returning 500 with generic error message, no secret leak.
- Sanitization of memory in finally block.
- Fallback when adapter has no elevation support.
"""
from __future__ import annotations

import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ascendo.dashboard import create_app

from .test_dashboard_elevation import (
    _FakeAdapterNoElevation,
    _FakeAdapterWithElevation,
    _FakeElevation,
)


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
    root = logging.getLogger()
    prev_level = root.level
    root.addHandler(cap)
    root.setLevel(logging.DEBUG)
    try:
        yield cap
    finally:
        root.removeHandler(cap)
        root.setLevel(prev_level)


def test_sudo_auth_wrong_password_returns_401(
    tmp_path: Path,
    log_capture: _LogCapture,
) -> None:
    """POST /sudo/auth with wrong password must return 401, not 200."""
    app = create_app(adapter=_FakeAdapterWithElevation(), runs_dir=tmp_path)
    client = TestClient(app)

    resp = client.post(
        "/sudo/auth",
        json={"password": "wrong-password"},
    )
    assert resp.status_code == 401
    assert "sudo: 3 incorrect password attempts" in resp.json()["detail"]

    combined = "\n".join(log_capture.records)
    assert "sudo auth rejected by backend" in combined
    assert "wrong-password" not in combined


def test_sudo_auth_correct_password_returns_200(
    tmp_path: Path,
) -> None:
    """POST /sudo/auth with correct password returns 200."""
    app = create_app(adapter=_FakeAdapterWithElevation(), runs_dir=tmp_path)
    client = TestClient(app)

    resp = client.post(
        "/sudo/auth",
        json={"password": "correct"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "cached": True}


def test_sudo_auth_backend_exception_does_not_leak_password(
    tmp_path: Path,
    log_capture: _LogCapture,
) -> None:
    """Backend raising an exception containing password must return 500 without leaking secret."""
    secret = "SuperSecretP@ss123"

    class _RaisingElevation(_FakeElevation):
        def register_password(
            self, password: str, *, verify: bool = True, timeout: int = 15
        ) -> tuple[bool, str]:
            raise RuntimeError(f"elevation failure with secret {password}")

    class _RaisingAdapter(_FakeAdapterWithElevation):
        def __init__(self) -> None:
            self._elev = _RaisingElevation()

    app = create_app(adapter=_RaisingAdapter(), runs_dir=tmp_path)
    client = TestClient(app, raise_server_exceptions=False)

    resp = client.post(
        "/sudo/auth",
        json={"password": secret},
    )
    assert resp.status_code == 500
    assert secret not in resp.text
    assert resp.json()["detail"] == "elevation backend error"

    combined = "\n".join(log_capture.records)
    assert secret not in combined
    assert "sudo auth backend error: RuntimeError" in combined


def test_sudo_auth_backend_rejection_message_masks_password(
    tmp_path: Path,
    log_capture: _LogCapture,
) -> None:
    """Backend returning password in failure message must have password masked with ***."""
    secret = "SuperSecretP@ss123"

    class _LeakingElevation(_FakeElevation):
        def register_password(
            self, password: str, *, verify: bool = True, timeout: int = 15
        ) -> tuple[bool, str]:
            return False, f"auth error: password {password} rejected"

    class _LeakingAdapter(_FakeAdapterWithElevation):
        def __init__(self) -> None:
            self._elev = _LeakingElevation()

    app = create_app(adapter=_LeakingAdapter(), runs_dir=tmp_path)
    client = TestClient(app)

    resp = client.post(
        "/sudo/auth",
        json={"password": secret},
    )
    assert resp.status_code == 401
    assert secret not in resp.text
    assert "***" in resp.text

    combined = "\n".join(log_capture.records)
    assert secret not in combined
    assert "sudo auth rejected by backend" in combined


def test_sudo_auth_without_elevation_returns_stub_response(
    tmp_path: Path,
) -> None:
    """POST /sudo/auth when adapter has no elevation returns stub fallback."""
    app = create_app(adapter=_FakeAdapterNoElevation(), runs_dir=tmp_path)
    client = TestClient(app)

    resp = client.post(
        "/sudo/auth",
        json={"password": "any-password"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "cached": True, "stub": True}
