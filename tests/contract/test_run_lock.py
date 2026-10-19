"""Cross-process run lock (Spec 6): PID + start time, stale release,
re-entrancy keyed on (pid, run_id), mutating phases only."""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from ascendo.orchestrator import run_lock
from ascendo.orchestrator.run_lock import RunLockError

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="run lock is macOS-only")


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


def test_live_foreign_pid_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(run_lock, "_boot_time", lambda: None)
    monkeypatch.setattr(run_lock, "_pid_start_time", lambda pid: None)
    holder = subprocess.Popen(["sleep", "30"])
    try:
        path = _lock(tmp_path)
        path.write_text(f"pid={holder.pid}\nstarted=x\nstarted_unix={time.time()}\nrun_id=other\n")
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
    path.write_text(f"pid=1\nstarted=x\nstarted_unix={time.time()}\nrun_id=other\n")
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
    # fixed ascendo-home path, independent of --runs-dir
    expect = Path.home() / ".ascendo" / "run.lock"
    assert run_lock.default_lock_path(tmp_path / "runs") == expect
    assert run_lock.default_lock_path(tmp_path / "elsewhere" / "runs") == expect


def _hold(tmp_path, started_unix):
    holder = subprocess.Popen(["sleep", "30"])
    path = _lock(tmp_path)
    path.write_text(f"pid={holder.pid}\nstarted=x\nstarted_unix={started_unix}\nrun_id=old\n")
    return holder, path


def test_live_pid_with_pre_boot_started_unix_is_stale_and_stolen(tmp_path, monkeypatch):
    """A lock left by a reboot whose pid was reused by a live process."""
    started = time.time() - 1000
    holder, path = _hold(tmp_path, started)
    try:
        monkeypatch.setattr(run_lock, "_boot_time", lambda: started + 500)      # booted after the lock
        monkeypatch.setattr(run_lock, "_pid_start_time", lambda pid: None)
        assert run_lock.held_by_other(path, run_id="r1") is None
        run_lock.acquire(path, run_id="r1")
        assert f"pid={os.getpid()}" in path.read_text()
        run_lock.release(path, run_id="r1")
    finally:
        holder.kill()
        holder.wait()


def test_live_pid_started_after_the_lock_was_written_is_stale(tmp_path, monkeypatch):
    started = time.time() - 1000
    holder, path = _hold(tmp_path, started)
    try:
        monkeypatch.setattr(run_lock, "_boot_time", lambda: started - 10_000)
        monkeypatch.setattr(run_lock, "_pid_start_time", lambda pid: started + 600)   # pid reused later
        run_lock.acquire(path, run_id="r1")
        run_lock.release(path, run_id="r1")
    finally:
        holder.kill()
        holder.wait()


def test_live_pid_started_before_the_lock_after_boot_is_still_held(tmp_path, monkeypatch):
    started = time.time() - 1000
    holder, path = _hold(tmp_path, started)
    try:
        monkeypatch.setattr(run_lock, "_boot_time", lambda: started - 10_000)
        monkeypatch.setattr(run_lock, "_pid_start_time", lambda pid: started - 5)
        with pytest.raises(RunLockError):
            run_lock.acquire(path, run_id="r1")
    finally:
        holder.kill()
        holder.wait()


def test_real_boot_time_and_pid_start_seams_are_sane():
    boot = run_lock._boot_time()
    assert boot is None or 0 < boot <= time.time()
    start = run_lock._pid_start_time(os.getpid())
    assert start is None or abs(start - time.time()) < 10 * 24 * 3600


def test_steal_does_not_delete_a_lock_replaced_by_another_process(tmp_path):
    """m1: the owner changed between our read and the guarded unlink."""
    path = _lock(tmp_path)
    path.write_text("pid=999999\nstarted=x\nstarted_unix=1\nrun_id=old\n")
    stale_seen = run_lock._read(path)
    # another process steals first and creates ITS lock
    path.write_text(f"pid={os.getppid()}\nstarted=x\nstarted_unix={time.time()}\nrun_id=winner\n")
    with pytest.raises(RunLockError):
        run_lock._steal(path, stale_seen, "loser")
    assert "run_id=winner" in path.read_text()      # the winner's lock survived


def test_lock_is_macos_only_other_platforms_keep_pre_1_1_0_behaviour():
    assert run_lock._SUPPORTED == (sys.platform == "darwin")
