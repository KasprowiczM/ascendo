"""Cross-process run lock (Spec 6): PID + start time, stale release,
re-entrancy keyed on (pid, run_id), mutating phases only."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from ascendo.orchestrator import run_lock
from ascendo.orchestrator.run_lock import RunLockError

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only lock")


def _lock(tmp_path: Path) -> Path:
    return tmp_path / "run.lock"


def test_acquire_release_and_mode(tmp_path):
    path = _lock(tmp_path)
    got = run_lock.acquire(path, run_id="r1")
    assert got == path and path.is_file()
    assert path.stat().st_mode & 0o777 == 0o600
    text = path.read_text()
    assert f"pid={os.getpid()}" in text and "run_id=r1" in text and "started_unix=" in text
    run_lock.release(path, run_id="r1")
    assert not path.exists()


def test_reentrant_same_pid_same_run_id(tmp_path):
    path = _lock(tmp_path)
    run_lock.acquire(path, run_id="r1")
    run_lock.acquire(path, run_id="r1")          # re-entry: no error
    run_lock.release(path, run_id="r1")
    assert not path.exists()


def test_same_pid_different_run_id_is_refused(tmp_path):
    path = _lock(tmp_path)
    run_lock.acquire(path, run_id="r1")
    with pytest.raises(RunLockError):
        run_lock.acquire(path, run_id="r2")
    run_lock.release(path, run_id="r1")


def test_live_foreign_pid_refused(tmp_path):
    holder = subprocess.Popen(["sleep", "30"])
    try:
        path = _lock(tmp_path)
        path.write_text(f"pid={holder.pid}\nstarted=x\nstarted_unix=0\nrun_id=other\n")
        with pytest.raises(RunLockError) as ei:
            run_lock.acquire(path, run_id="r1")
        assert str(holder.pid) in str(ei.value)
    finally:
        holder.kill()
        holder.wait()


def test_dead_pid_is_stolen(tmp_path):
    path = _lock(tmp_path)
    path.write_text("pid=999999\nstarted=x\nstarted_unix=0\nrun_id=old\n")
    run_lock.acquire(path, run_id="r1")
    assert f"pid={os.getpid()}" in path.read_text()
    run_lock.release(path, run_id="r1")


def test_release_does_not_remove_foreign_lock(tmp_path):
    path = _lock(tmp_path)
    path.write_text("pid=1\nstarted=x\nstarted_unix=0\nrun_id=other\n")
    run_lock.release(path, run_id="r1")
    assert path.exists()


def test_context_manager_disabled_is_noop(tmp_path):
    path = _lock(tmp_path)
    with run_lock.run_lock(path, "r1", enabled=False):
        assert not path.exists()


def test_context_manager_releases_on_exception(tmp_path):
    path = _lock(tmp_path)
    with pytest.raises(ValueError):
        with run_lock.run_lock(path, "r1", enabled=True):
            assert path.exists()
            raise ValueError("boom")
    assert not path.exists()


def test_unparseable_lock_file_fresh_is_refused_old_is_stolen(tmp_path):
    path = _lock(tmp_path)
    path.write_text("garbage")
    with pytest.raises(RunLockError):
        run_lock.acquire(path, run_id="r1")
    old = path.stat().st_mtime - 7 * 3600
    os.utime(path, (old, old))
    run_lock.acquire(path, run_id="r1")
    run_lock.release(path, run_id="r1")


def test_default_lock_path_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("ASCENDO_RUN_LOCK_FILE", str(tmp_path / "x.lock"))
    assert run_lock.default_lock_path(tmp_path / "runs") == tmp_path / "x.lock"
    monkeypatch.delenv("ASCENDO_RUN_LOCK_FILE")
    assert run_lock.default_lock_path(tmp_path / "runs") == tmp_path / "run.lock"
