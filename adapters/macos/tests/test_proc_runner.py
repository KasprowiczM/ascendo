"""Tests for the shared Python process runner (managers/_proc.py)."""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from ascendo.interfaces.package_manager import ManagerError
from ascendo.models.run import Phase, PhaseStatus
from ascendo_macos.managers import _proc
from ascendo_macos.managers._proc import (
    handle_phase_timeout,
    run_streaming,
    salvage_timeout_sidecar,
)
from ascendo_macos.managers.brew import BrewManager

from .test_brew_manager_smoke import (
    ADAPTER_ROOT,
    FIXTURE_SIDECAR,
    _mac_host,
    _run,
)


@pytest.fixture(params=["1", "0"], ids=["isolated", "shared"])
def mode(request, monkeypatch) -> str:
    """Run the test in both spawn modes (ASCENDO_PROC_ISOLATE seam)."""
    monkeypatch.setenv("ASCENDO_PROC_ISOLATE", request.param)
    return request.param


def _pid_gone(pid: int, seconds: float = 5.0) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.1)
    return False


def test_streams_output_to_log_and_returns_rc(tmp_path: Path, mode: str) -> None:
    log = tmp_path / "x.log"
    done = run_streaming(["sh", "-c", "echo one; echo two >&2; exit 3"], log, 10)
    assert done.returncode == 3
    assert done.stdout.splitlines() == ["one", "two"]
    assert log.read_text().splitlines() == ["one", "two"]


def test_stdin_is_devnull(tmp_path: Path, mode: str) -> None:
    done = run_streaming(["cat"], tmp_path / "x.log", 10)
    assert done.returncode == 0 and done.stdout == ""


def test_silent_child_is_killed_on_wall_clock_deadline(tmp_path: Path, mode: str) -> None:
    """The old per-line readline loop never woke up for a child that prints nothing."""
    started = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        run_streaming(["sleep", "30"], tmp_path / "x.log", 1, kill_grace=1)
    assert time.monotonic() - started < 10


def test_term_ignoring_child_is_killed_after_grace(tmp_path: Path, mode: str) -> None:
    started = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        run_streaming(["sh", "-c", 'trap "" TERM; sleep 30'], tmp_path / "x.log", 1, kill_grace=1)
    assert time.monotonic() - started < 10


def test_grandchild_dies_with_process_group(tmp_path: Path, mode: str) -> None:
    pidfile = tmp_path / "gc.pid"
    with pytest.raises(subprocess.TimeoutExpired):
        run_streaming(
            ["sh", "-c", f'sleep 60 & echo $! > "{pidfile}"; wait'],
            tmp_path / "x.log", 1, kill_grace=1,
        )
    assert _pid_gone(int(pidfile.read_text().strip()))


def test_timeout_keeps_partial_output(tmp_path: Path, mode: str) -> None:
    with pytest.raises(subprocess.TimeoutExpired) as ei:
        run_streaming(["sh", "-c", "echo started; sleep 30"], tmp_path / "x.log", 1, kill_grace=1)
    assert "started" in (ei.value.output or "")
    assert "started" in (tmp_path / "x.log").read_text()


def test_grandchild_holding_pipe_does_not_hang_after_child_exits(tmp_path: Path, mode: str) -> None:
    """Child exits at once but a background grandchild keeps stdout open (the
    Sesja-68 keepalive pattern): return promptly instead of waiting for EOF."""
    started = time.monotonic()
    done = run_streaming(["sh", "-c", "(sleep 20) & echo hi"], tmp_path / "x.log", 30)
    assert done.returncode == 0 and "hi" in done.stdout
    assert time.monotonic() - started < 10


def test_spawn_failure_raises_oserror(tmp_path: Path) -> None:
    with pytest.raises(OSError):
        run_streaming([str(tmp_path / "does-not-exist")], tmp_path / "x.log", 5)


def test_shared_mode_tree_kill_reaps_grandchild_that_outlives_exiting_leader(
    tmp_path: Path, monkeypatch,
) -> None:
    """Shared mode has no process group to killpg: the descendant snapshot must
    still reap a TERM-ignoring grandchild whose parent exits on TERM."""
    monkeypatch.setenv("ASCENDO_PROC_ISOLATE", "0")
    pidfile = tmp_path / "gc.pid"
    with pytest.raises(subprocess.TimeoutExpired):
        run_streaming(
            ["sh", "-c", f'(trap "" TERM; sleep 60) & echo $! > "{pidfile}"; sleep 60'],
            tmp_path / "x.log", 1, kill_grace=1,
        )
    assert _pid_gone(int(pidfile.read_text().strip()))


# -- spawn-mode selection --------------------------------------------------

def _pgid_report(tmp_path: Path) -> tuple[int, int]:
    done = run_streaming(["sh", "-c", 'echo "$$ $(ps -o pgid= -p $$)"'], tmp_path / "x.log", 10)
    pid, pgid = done.stdout.split()
    return int(pid), int(pgid)


def test_isolated_mode_child_leads_its_own_group(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ASCENDO_PROC_ISOLATE", "1")
    pid, pgid = _pgid_report(tmp_path)
    assert pgid == pid != os.getpgid(0)


def test_shared_mode_child_stays_in_parent_group(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ASCENDO_PROC_ISOLATE", "0")
    pid, pgid = _pgid_report(tmp_path)
    assert pgid == os.getpgid(0) != pid


def test_auto_detect_isolates_only_without_controlling_terminal(monkeypatch) -> None:
    monkeypatch.delenv("ASCENDO_PROC_ISOLATE", raising=False)
    real_open = os.open

    def no_tty(path, *a, **k):
        if path == "/dev/tty":
            raise OSError("no controlling terminal")
        return real_open(path, *a, **k)

    def has_tty(path, *a, **k):
        if path == "/dev/tty":
            return real_open(os.devnull, os.O_RDONLY)
        return real_open(path, *a, **k)

    monkeypatch.setattr(_proc.os, "open", no_tty)
    assert _proc._isolate_child() is True
    monkeypatch.setattr(_proc.os, "open", has_tty)
    assert _proc._isolate_child() is False
    monkeypatch.setenv("ASCENDO_PROC_ISOLATE", "1")
    assert _proc._isolate_child() is True


# -- abnormal exit: no orphans ---------------------------------------------

class _Boom(BaseException):
    """Stands in for KeyboardInterrupt / any BaseException."""


@pytest.fixture
def spawned(monkeypatch) -> list:
    """Record every Popen created through subprocess (for liveness assertions)."""
    made: list = []
    real = subprocess.Popen

    class Recording(real):  # type: ignore[misc, valid-type]
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            made.append(self)

    monkeypatch.setattr(subprocess, "Popen", Recording)
    return made


def test_child_is_killed_when_runner_raises_baseexception(
    tmp_path: Path, mode: str, spawned: list, monkeypatch,
) -> None:
    class _Clock:
        @staticmethod
        def monotonic() -> float:
            raise _Boom()

    monkeypatch.setattr(_proc, "time", _Clock)
    with pytest.raises(_Boom):
        run_streaming(["sleep", "60"], tmp_path / "x.log", 30, kill_grace=1)
    child = spawned[0]
    assert child.poll() is not None, "child left running after abnormal exit"


def test_sigint_to_parent_does_not_orphan_child_or_grandchild(tmp_path: Path, mode: str) -> None:
    pidfile, gcfile = tmp_path / "child.pid", tmp_path / "gc.pid"
    driver = tmp_path / "driver.py"
    driver.write_text(
        "import signal, sys\n"
        "from pathlib import Path\n"
        "signal.signal(signal.SIGINT, signal.default_int_handler)\n"
        "from ascendo_macos.managers._proc import run_streaming\n"
        f"script = 'sleep 60 & echo $! > \"{gcfile}\"; echo $$ > \"{pidfile}\"; wait'\n"
        f"run_streaming(['sh', '-c', script], Path(r'{tmp_path / 'x.log'}'), 60, kill_grace=1)\n",
        encoding="utf-8",
    )
    env = {**os.environ, "ASCENDO_PROC_ISOLATE": mode}
    parent = subprocess.Popen([sys.executable, str(driver)], env=env,
                              stderr=subprocess.PIPE, text=True)
    try:
        end = time.monotonic() + 15
        while time.monotonic() < end and not (pidfile.exists() and gcfile.exists()
                                               and pidfile.read_text().strip()
                                               and gcfile.read_text().strip()):
            time.sleep(0.1)
        child, grandchild = int(pidfile.read_text()), int(gcfile.read_text())
        parent.send_signal(2)  # SIGINT to the parent ONLY
        parent.wait(timeout=20)
        assert parent.returncode != 0  # KeyboardInterrupt propagated
        assert _pid_gone(child), "child orphaned by SIGINT"
        assert _pid_gone(grandchild), "grandchild orphaned by SIGINT"
    finally:
        if parent.poll() is None:
            parent.kill()


def test_log_open_failure_spawns_no_child(tmp_path: Path, spawned: list) -> None:
    marker = tmp_path / "ran"
    with pytest.raises(OSError):
        run_streaming(["sh", "-c", f'touch "{marker}"; sleep 30'],
                      tmp_path / "missing-dir" / "x.log", 5)
    assert spawned == [] and not marker.exists()


def test_stdout_closed_and_reader_stopped_when_grandchild_holds_pipe(
    tmp_path: Path, mode: str, spawned: list,
) -> None:
    done = run_streaming(["sh", "-c", "(sleep 20) & echo hi"], tmp_path / "x.log", 30)
    assert "hi" in done.stdout
    assert spawned[0].stdout.closed
    assert not [t for t in threading.enumerate() if t.name == "ascendo-proc-reader"]


def test_output_with_carriage_returns_and_utf8(tmp_path: Path) -> None:
    done = run_streaming(
        ["sh", "-c", r"printf 'a\rb\r\nzażółć\ntail'"], tmp_path / "x.log", 10,
    )
    assert done.stdout.splitlines() == ["a", "b", "zażółć", "tail"]


# -- manager-level: timeout salvage --------------------------------------

def _fake_brew_scripts(tmp_path: Path, body: str) -> Path:
    scripts = tmp_path / "scripts"
    (scripts / "brew").mkdir(parents=True)
    script = scripts / "brew" / "check.sh"
    script.write_text(
        "#!/usr/bin/env bash\n"
        'while [ $# -gt 0 ]; do case "$1" in\n'
        '  --output-dir) OUT="$2"; shift 2;; --run-id) RID="$2"; shift 2;; *) shift;; esac; done\n'
        + body,
        encoding="utf-8",
    )
    return scripts


def test_manager_timeout_salvages_partial_sidecar_as_failed(tmp_path: Path) -> None:
    scripts = _fake_brew_scripts(
        tmp_path,
        f'mkdir -p "$OUT/$RID"; cp "{FIXTURE_SIDECAR}" "$OUT/$RID/check__brew.json"\nsleep 60\n',
    )
    mgr = BrewManager(scripts_dir=scripts, lib_dir=ADAPTER_ROOT / "lib", timeout_sec=1)
    started = time.monotonic()
    sc = mgr.run_phase(Phase.CHECK, _run(), _mac_host())
    assert time.monotonic() - started < 15
    assert sc.status is PhaseStatus.FAILED
    assert sc.summary.exit_code == 124
    assert any(m.code == "PHASE_TIMEOUT" and "timed out" in m.text for m in sc.messages)


def test_manager_timeout_without_sidecar_raises_manager_error(tmp_path: Path) -> None:
    scripts = _fake_brew_scripts(tmp_path, "sleep 60\n")
    mgr = BrewManager(scripts_dir=scripts, lib_dir=ADAPTER_ROOT / "lib", timeout_sec=1)
    with pytest.raises(ManagerError, match="timed out"):
        mgr.run_phase(Phase.CHECK, _run(), _mac_host())


def test_salvage_returns_none_for_missing_or_garbage_sidecar(tmp_path: Path) -> None:
    assert salvage_timeout_sidecar(tmp_path / "nope.json", "t") is None
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert salvage_timeout_sidecar(bad, "t") is None


def test_handle_phase_timeout_helper(tmp_path: Path) -> None:
    exc = subprocess.TimeoutExpired(["x"], 1)
    run = _run()
    with pytest.raises(ManagerError, match="boom") as ei:
        handle_phase_timeout(output_dir=tmp_path, run_id=run.id, phase=Phase.CHECK,
                             category="brew", message="boom", exc=exc)
    assert ei.value.__cause__ is exc
    target = tmp_path / str(run.id) / "check__brew.json"
    target.parent.mkdir(parents=True)
    target.write_bytes(FIXTURE_SIDECAR.read_bytes())
    sc = handle_phase_timeout(output_dir=tmp_path, run_id=run.id, phase=Phase.CHECK,
                              category="brew", message="boom", exc=exc)
    assert sc.status is PhaseStatus.FAILED
