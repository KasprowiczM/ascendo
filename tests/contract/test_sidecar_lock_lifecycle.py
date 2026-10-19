"""Sidecar ``.lock`` lifecycle (POSIX): a released lock leaves nothing behind.

Before 1.1.1 every write (and every read) left ``<sidecar>.lock`` next to the
sidecar forever, so ``ascendo doctor`` reported hundreds of "stale" locks that
were never held. Writers now unlink the lock while still holding it (with an
inode re-check on acquire so the unlink race cannot produce two holders), and
readers never create one. Windows keeps its previous behaviour.
"""

from __future__ import annotations

import fcntl
import os
import threading
from pathlib import Path
from typing import Any

import pytest

from ascendo.models.sidecar import parse_sidecar
from ascendo.orchestrator import sidecar_io
from ascendo.orchestrator.sidecar_io import (
    detect_stale_locks,
    prune_stale_locks,
    read_sidecar,
    write_sidecar,
)

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX lock lifecycle")


def _locks(root: Path) -> list[Path]:
    return sorted(root.rglob("*.lock"))


def test_write_leaves_no_lock_file(sample_ascendo_v1_apply_winget: dict[str, Any], tmp_path: Path) -> None:
    path = write_sidecar(parse_sidecar(sample_ascendo_v1_apply_winget), base_dir=tmp_path)
    assert path.exists()
    assert _locks(tmp_path) == []


def test_read_never_creates_a_lock_file(sample_ascendo_v1_apply_winget: dict[str, Any], tmp_path: Path) -> None:
    path = write_sidecar(parse_sidecar(sample_ascendo_v1_apply_winget), base_dir=tmp_path)
    read_sidecar(path)
    assert _locks(tmp_path) == []


def test_concurrent_writers_still_serialize_and_leave_no_lock(
    sample_ascendo_v1_apply_winget: dict[str, Any], tmp_path: Path
) -> None:
    sc = parse_sidecar(sample_ascendo_v1_apply_winget)
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            for _ in range(10):
                write_sidecar(sc, base_dir=tmp_path)
                read_sidecar(sidecar_io._sidecar_path(tmp_path, sc))
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert _locks(tmp_path) == []


def test_holder_survives_concurrent_unlink_race(tmp_path: Path) -> None:
    """A waiter that opened the old (unlinked) lock inode must not count as a holder."""
    target = tmp_path / "x.json"
    entered: list[str] = []
    inside = threading.Lock()
    overlap: list[bool] = []

    def worker(name: str) -> None:
        for _ in range(25):
            with sidecar_io._locked(target, exclusive=True):
                if not inside.acquire(blocking=False):
                    overlap.append(True)
                    continue
                entered.append(name)
                inside.release()

    threads = [threading.Thread(target=worker, args=(f"w{i}",)) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert overlap == []
    assert len(entered) == 150
    assert _locks(tmp_path) == []


def test_prune_removes_unheld_leftovers_and_keeps_held_locks(tmp_path: Path) -> None:
    run = tmp_path / "run"
    run.mkdir()
    leftover = run / "check__brew.json.lock"
    leftover.touch()
    held = run / "apply__mas.json.lock"
    fd = os.open(held, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        old = leftover.stat().st_mtime - 3600
        os.utime(leftover, (old, old))
        os.utime(held, (old, old))
        removed = prune_stale_locks(run)
        assert removed == [leftover]
        assert not leftover.exists()
        assert held.exists()
    finally:
        os.close(fd)


def test_detect_stale_locks_ignores_unheld_leftovers(tmp_path: Path) -> None:
    """Only a lock someone still HOLDS past the threshold is a real problem."""
    run = tmp_path / "run"
    run.mkdir()
    leftover = run / "check__brew.json.lock"
    leftover.touch()
    old = leftover.stat().st_mtime - 3600
    os.utime(leftover, (old, old))
    assert detect_stale_locks(run, max_age_seconds=60) == []
    held = run / "apply__mas.json.lock"
    fd = os.open(held, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        os.utime(held, (old, old))
        assert detect_stale_locks(run, max_age_seconds=60) == [held]
    finally:
        os.close(fd)
