"""Tests for the shared Python process runner (managers/_proc.py)."""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from ascendo.interfaces.package_manager import ManagerError
from ascendo.models.run import Phase, PhaseStatus
from ascendo_macos.managers._proc import run_streaming, salvage_timeout_sidecar
from ascendo_macos.managers.brew import BrewManager

from .test_brew_manager_smoke import (
    ADAPTER_ROOT,
    FIXTURE_SIDECAR,
    _mac_host,
    _run,
)


def _pid_gone(pid: int, seconds: float = 5.0) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.1)
    return False


def test_streams_output_to_log_and_returns_rc(tmp_path: Path) -> None:
    log = tmp_path / "x.log"
    done = run_streaming(["sh", "-c", "echo one; echo two >&2; exit 3"], log, 10)
    assert done.returncode == 3
    assert done.stdout.splitlines() == ["one", "two"]
    assert log.read_text().splitlines() == ["one", "two"]


def test_stdin_is_devnull(tmp_path: Path) -> None:
    done = run_streaming(["cat"], tmp_path / "x.log", 10)
    assert done.returncode == 0 and done.stdout == ""


def test_silent_child_is_killed_on_wall_clock_deadline(tmp_path: Path) -> None:
    """The old per-line readline loop never woke up for a child that prints nothing."""
    started = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        run_streaming(["sleep", "30"], tmp_path / "x.log", 1, kill_grace=1)
    assert time.monotonic() - started < 10


def test_term_ignoring_child_is_killed_after_grace(tmp_path: Path) -> None:
    started = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        run_streaming(["sh", "-c", 'trap "" TERM; sleep 30'], tmp_path / "x.log", 1, kill_grace=1)
    assert time.monotonic() - started < 10


def test_grandchild_dies_with_process_group(tmp_path: Path) -> None:
    pidfile = tmp_path / "gc.pid"
    with pytest.raises(subprocess.TimeoutExpired):
        run_streaming(
            ["sh", "-c", f'sleep 60 & echo $! > "{pidfile}"; wait'],
            tmp_path / "x.log", 1, kill_grace=1,
        )
    assert _pid_gone(int(pidfile.read_text().strip()))


def test_timeout_keeps_partial_output(tmp_path: Path) -> None:
    with pytest.raises(subprocess.TimeoutExpired) as ei:
        run_streaming(["sh", "-c", "echo started; sleep 30"], tmp_path / "x.log", 1, kill_grace=1)
    assert "started" in (ei.value.output or "")
    assert "started" in (tmp_path / "x.log").read_text()


def test_grandchild_holding_pipe_does_not_hang_after_child_exits(tmp_path: Path) -> None:
    """Child exits at once but a background grandchild keeps stdout open (the
    Sesja-68 keepalive pattern): return promptly instead of waiting for EOF."""
    started = time.monotonic()
    done = run_streaming(["sh", "-c", "(sleep 20) & echo hi"], tmp_path / "x.log", 30)
    assert done.returncode == 0 and "hi" in done.stdout
    assert time.monotonic() - started < 10


def test_spawn_failure_raises_oserror(tmp_path: Path) -> None:
    with pytest.raises(OSError):
        run_streaming([str(tmp_path / "does-not-exist")], tmp_path / "x.log", 5)


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
