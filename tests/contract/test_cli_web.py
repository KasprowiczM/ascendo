"""Contract tests for ``ascendo web {start|stop|restart|status|open}``.

Covers the pidfile lifecycle + idempotency + cross-process-check semantics
without actually spawning real uvicorn instances — start/stop/restart are
stubbed via monkeypatch on subprocess.Popen + os.kill so the tests stay
deterministic across CI environments (no port binding races).
"""
from __future__ import annotations

import os
import signal
import socket
import sys
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

# The CLI module registers `app` at import time; we use Typer's runner
# directly instead of subprocess so coverage flows naturally.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "core"))

from ascendo.cli import (  # noqa: E402
    _dashboard_pidfile,
    _pid_alive,
    _port_listening,
    _read_pidfile,
    _write_pidfile,
    app,
)


def _default_port_occupied() -> bool:
    """True when 127.0.0.1:8765 is already bound (e.g. Tauri desktop app)."""
    return _port_listening("127.0.0.1", 8765, timeout=0.3)


@pytest.fixture()
def fake_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Re-point ASCENDO_HOME so each test has an isolated pidfile."""
    monkeypatch.setenv("ASCENDO_HOME", str(tmp_path))
    return tmp_path


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


def test_pidfile_path_honours_ascendo_home(fake_home: Path) -> None:
    """ASCENDO_HOME redirection is the only knob between users."""
    assert _dashboard_pidfile() == fake_home / "dashboard.pid"


def test_pidfile_roundtrip(fake_home: Path) -> None:
    """write_pidfile + read_pidfile must agree on every field."""
    _write_pidfile(12345, "127.0.0.1", 8765)
    pid, meta = _read_pidfile()
    assert pid == 12345
    assert meta["host"] == "127.0.0.1"
    assert meta["port"] == "8765"
    assert "started_at" in meta


def test_read_pidfile_missing(fake_home: Path) -> None:
    """No pidfile yet → returns (None, {})."""
    pid, meta = _read_pidfile()
    assert pid is None
    assert meta == {}


def test_read_pidfile_corrupt(fake_home: Path) -> None:
    """Garbled pidfile → graceful (None, ...) instead of crashing."""
    f = _dashboard_pidfile()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("this is not a pidfile\n", encoding="utf-8")
    pid, _ = _read_pidfile()
    assert pid is None


def test_pid_alive_for_self() -> None:
    """We're definitely alive — our own pid must report alive."""
    assert _pid_alive(os.getpid()) is True


def test_pid_alive_for_garbage() -> None:
    """A pid that almost-certainly doesn't exist returns False."""
    # 2**31 - 1 is far beyond any real pid on any OS.
    assert _pid_alive(2147483646) is False


def test_pid_alive_for_zero() -> None:
    """pid 0 / negative → False (never valid as a target)."""
    assert _pid_alive(0) is False
    assert _pid_alive(-5) is False


def test_port_listening_false_when_nothing_bound() -> None:
    """A random high port we haven't bound returns False quickly."""
    # Pick a deliberately weird port that nothing in this test env binds.
    assert _port_listening("127.0.0.1", 1) is False  # privileged port, almost certainly nothing
    assert _port_listening("127.0.0.1", 59999, timeout=0.2) is False


def test_port_listening_true_when_bound(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bind a transient port, confirm probe sees it."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.listen(1)
    try:
        assert _port_listening("127.0.0.1", port, timeout=0.5) is True
    finally:
        s.close()


@pytest.mark.skipif(
    _default_port_occupied(),
    reason="port 8765 already bound (e.g. Tauri desktop app) — test needs a free port",
)
def test_web_status_reports_stopped_on_clean_state(
    fake_home: Path, runner: CliRunner,
) -> None:
    """No pidfile + nothing on 127.0.0.1:8765 → status says stopped."""
    result = runner.invoke(app, ["web", "status"])
    assert result.exit_code == 0
    assert "stopped" in result.output.lower()


def test_web_status_json_shape(fake_home: Path, runner: CliRunner) -> None:
    """--json emits a stable schema callers can rely on."""
    result = runner.invoke(app, ["web", "status", "--json"])
    assert result.exit_code == 0
    import json
    payload = json.loads(result.output)
    expected_keys = {
        "pidfile_present", "pid", "pid_alive", "host", "port",
        "port_listening", "health_ok", "started_at",
    }
    assert expected_keys.issubset(payload.keys()), (
        f"missing keys: {expected_keys - payload.keys()}"
    )


def test_web_stop_with_no_pidfile_exits_zero(
    fake_home: Path, runner: CliRunner,
) -> None:
    """`ascendo web stop` when nothing is running is a no-op, not an error.

    Operator-friendly: stop should be idempotent — re-running it shouldn't
    fail.
    """
    result = runner.invoke(app, ["web", "stop"])
    assert result.exit_code == 0
    assert "not running" in result.output.lower() or "no pidfile" in result.output.lower()


def test_web_stop_cleans_stale_pidfile(
    fake_home: Path, runner: CliRunner,
) -> None:
    """If pidfile points at a dead pid, stop should remove it."""
    _write_pidfile(2147483646, "127.0.0.1", 8765)  # almost-certainly-dead pid
    assert _dashboard_pidfile().is_file()
    result = runner.invoke(app, ["web", "stop"])
    assert result.exit_code == 0
    assert not _dashboard_pidfile().is_file(), (
        "stale pidfile must be cleared so subsequent `start` doesn't refuse"
    )


@pytest.mark.skipif(
    _default_port_occupied(),
    reason="port 8765 already bound (e.g. Tauri desktop app) — test needs a free port",
)
def test_web_open_refuses_when_not_running(
    fake_home: Path, runner: CliRunner,
) -> None:
    """`web open` without a running dashboard → exit 1 with a clear error.

    Better than launching the browser at a dead URL.
    """
    result = runner.invoke(app, ["web", "open"])
    assert result.exit_code == 1
    assert "not responding" in result.output.lower() or "not running" in result.output.lower()


def test_web_start_help_documents_auto_open_default() -> None:
    """``ascendo web start --help`` must make clear that the browser
    opens automatically (per operator request — discoverable defaults).

    Regression test: pre-fix the default was --no-open, requiring the
    user to remember --open every time. Operator-reported friction.
    """
    runner = CliRunner()
    # Force a wide terminal: typer/rich truncates long option names (e.g.
    # `--no-open` -> `--no-…`) at the CI default width of 80, which would make
    # the substring assertions below spuriously fail.
    result = runner.invoke(app, ["web", "start", "--help"], env={"COLUMNS": "200"})
    assert result.exit_code == 0
    output_lower = result.output.lower()
    assert "open" in output_lower
    # The help text must mention the auto-open default OR the --no-open
    # opt-out path so users headed for SSH sessions can find it.
    assert ("no-open" in output_lower) or ("automatically" in output_lower) or (
        "on by default" in output_lower
    ), (
        f"web start --help must signal auto-open default + --no-open opt-out:\n{result.output}"
    )


def test_web_restart_help_mirrors_start_defaults() -> None:
    """`ascendo web restart` should match `start` defaults so muscle
    memory transfers — same --open auto-fire, same --no-open opt-out."""
    runner = CliRunner()
    # Wide terminal — see test_web_start_help_documents_auto_open_default.
    result = runner.invoke(app, ["web", "restart", "--help"], env={"COLUMNS": "200"})
    assert result.exit_code == 0
    assert "no-open" in result.output.lower()
