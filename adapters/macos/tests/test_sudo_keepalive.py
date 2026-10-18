"""One sudo keep-alive per interactive apply run (Spec 7b).

``MacOSAdapter.run_scope`` starts a daemon thread that refreshes the sudo
timestamp with ``sudo -n -v`` (never ``sudo -v``: that would prompt) and stops
it on EVERY exit path. A fake ``sudo`` on PATH counts calls; the real one is
unreachable (the suite tripwire fakes it too).
"""
from __future__ import annotations

import os
import stat
import threading
import time
from datetime import datetime, timezone
from uuid import UUID

import pytest

from ascendo.models.run import Phase, RunInfo, Trigger
from ascendo_macos.adapter import MacOSAdapter
from ascendo_macos.sudo_keepalive import SudoKeepAlive


def _run(trigger=Trigger.CLI, dry=False) -> RunInfo:
    return RunInfo(id=UUID("12345678-1234-1234-1234-123456789abc"), trigger=trigger,
                   profile="full", dry_run=dry,
                   started_at=datetime(2026, 5, 1, tzinfo=timezone.utc))


@pytest.fixture
def fake_sudo(tmp_path, monkeypatch):
    log = tmp_path / "sudo_calls.log"
    rc = tmp_path / "sudo_rc"
    rc.write_text("0")
    p = tmp_path / "fakebin" / "sudo"
    p.parent.mkdir()
    p.write_text(f'#!/bin/sh\nprintf "%s\\n" "$*" >> "{log}"\nexit "$(cat {rc})"\n')
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{p.parent}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("ASCENDO_SUDO_KEEPALIVE_INTERVAL", "0.05")
    monkeypatch.delenv("SUDO_ASKPASS", raising=False)

    def calls():
        return log.read_text().splitlines() if log.exists() else []

    calls.set_rc = lambda v: rc.write_text(str(v))
    return calls


def _wait(pred, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.02)
    return False


def test_keepalive_refreshes_with_sudo_n_v_only_and_stops_on_exit(fake_sudo):
    adapter = MacOSAdapter()
    with adapter.run_scope(_run(), adapter.detect_host(), phases=(Phase.CHECK, Phase.APPLY)):
        assert _wait(lambda: len(fake_sudo()) >= 3)
    n = len(fake_sudo())
    assert set(fake_sudo()) == {"-n -v"}            # never a prompting `sudo -v`
    time.sleep(0.3)
    assert len(fake_sudo()) == n                    # thread is dead after __exit__
    assert not [t for t in threading.enumerate() if t.name == "ascendo-sudo-keepalive"]


def test_keepalive_stops_on_exception(fake_sudo):
    adapter = MacOSAdapter()
    with pytest.raises(RuntimeError):
        with adapter.run_scope(_run(), adapter.detect_host(), phases=(Phase.APPLY,)):
            _wait(lambda: fake_sudo())
            raise RuntimeError("cancelled/crashed")
    time.sleep(0.2)
    n = len(fake_sudo())
    time.sleep(0.3)
    assert len(fake_sudo()) == n


def test_keepalive_ignores_failures_until_first_success_then_stops_on_failure(tmp_path, monkeypatch):
    """rc sequence 1,1,0,0,1: alive through the 2 'not primed yet' failures and the
    successes; exits only on the failure AFTER a success. Never `sudo -v`."""
    log = tmp_path / "seq.log"
    cnt = tmp_path / "cnt"
    cnt.write_text("0")
    p = tmp_path / "fakebin" / "sudo"
    p.parent.mkdir()
    p.write_text(
        "#!/bin/sh\n"
        f'printf "%s\\n" "$*" >> "{log}"\n'
        f'n=$(cat "{cnt}"); n=$((n+1)); echo $n > "{cnt}"\n'
        'case $n in 1|2) exit 1;; 3|4) exit 0;; *) exit 1;; esac\n')
    p.chmod(0o755)
    monkeypatch.setenv("PATH", f"{p.parent}{os.pathsep}{os.environ['PATH']}")
    ka = SudoKeepAlive(interval=0.1)
    ka.start()
    try:
        thread = ka._thread
        assert _wait(lambda: int(cnt.read_text()) >= 2)
        assert thread.is_alive()                          # 2 failures before any success: still alive
        assert _wait(lambda: int(cnt.read_text()) >= 4)
        assert _wait(lambda: not thread.is_alive())       # 5th call fails after successes -> exit
        assert int(cnt.read_text()) == 5
        assert set(log.read_text().splitlines()) == {"-n -v"}

    finally:
        ka.stop()


@pytest.mark.parametrize("kwargs,phases", [
    ({"trigger": Trigger.SCHEDULER}, (Phase.APPLY,)),     # unattended
    ({"dry": True}, (Phase.APPLY,)),                       # dry run
    ({}, (Phase.CHECK, Phase.PLAN, Phase.VERIFY)),         # read-only
])
def test_keepalive_not_started(fake_sudo, kwargs, phases):
    adapter = MacOSAdapter()
    with adapter.run_scope(_run(**kwargs), adapter.detect_host(), phases=phases):
        time.sleep(0.3)
    assert fake_sudo() == []


def test_keepalive_not_started_when_askpass_wired(fake_sudo, tmp_path, monkeypatch):
    ask = tmp_path / "ask.sh"
    ask.write_text("#!/bin/sh\necho pw\n")
    ask.chmod(0o755)
    monkeypatch.setenv("SUDO_ASKPASS", str(ask))
    adapter = MacOSAdapter()
    with adapter.run_scope(_run(), adapter.detect_host(), phases=(Phase.APPLY,)):
        time.sleep(0.3)
    assert fake_sudo() == []


def test_keepalive_not_started_when_dashboard_password_registered(fake_sudo):
    adapter = MacOSAdapter()
    adapter.elevation().has_password_registered = lambda: True   # type: ignore[method-assign]
    with adapter.run_scope(_run(), adapter.detect_host(), phases=(Phase.APPLY,)):
        time.sleep(0.3)
    assert fake_sudo() == []


def test_missing_sudo_binary_never_raises(monkeypatch, tmp_path):
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setenv("ASCENDO_SUDO_KEEPALIVE_INTERVAL", "0.05")
    with SudoKeepAlive(interval=0.05):
        time.sleep(0.2)


def test_default_interval_is_50_seconds(monkeypatch):
    monkeypatch.delenv("ASCENDO_SUDO_KEEPALIVE_INTERVAL", raising=False)
    assert SudoKeepAlive().interval == 50.0
