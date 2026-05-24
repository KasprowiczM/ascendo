"""run_logger edge-case coverage (M5.7.6, 2026-05-24).

Stress tests beyond the basic smoke in the ad-hoc validation script:
  * symlink dirs inside base_dir are not destructively resolved
  * concurrent attach_run_log invocations in the same base_dir
  * keep=0 is a defensive no-op (never prune everything)
  * non-run-id-shaped dirs (operator notes, backups) are preserved
  * log_path file exists + contains attach/detach lines even on
    a yield-only block that emits no user log records
"""
from __future__ import annotations

import logging
import os
import tempfile
import threading
import time
from pathlib import Path

import pytest

from ascendo.orchestrator.run_logger import (
    attach_run_log,
    prune_run_dirs,
)


def _make_run_dir(base: Path, idx: int, *, age_sec: float = 3600.0) -> Path:
    d = base / f"00000000-0000-0000-0000-{idx:012x}"
    d.mkdir()
    t = time.time() - age_sec
    os.utime(d, (t, t))
    return d


def test_symlink_inside_base_dir_is_not_resolved_destructively(tmp_path: Path) -> None:
    """A symlink whose name matches the run-id shape must not cause
    rmtree to walk into and destroy the target."""
    target = tmp_path / "real-run"
    target.mkdir()
    (target / "critical.txt").write_text("must survive")

    sym = tmp_path / "00000000-0000-0000-0000-aaaaaaaaaaaa"
    sym.symlink_to(target)

    for i in range(11):
        _make_run_dir(tmp_path, i)

    with attach_run_log("00000000-0000-0000-0000-fffffffffffe", tmp_path, keep=5):
        logging.getLogger("ascendo").info("symlink test")

    # Even if the symlink itself was treated as a run dir and removed,
    # the real target must be untouched.
    assert target.is_dir(), "rmtree followed a symlink and destroyed the target"
    assert (target / "critical.txt").read_text() == "must survive"


def test_concurrent_attach_in_same_base_dir(tmp_path: Path) -> None:
    """Two simultaneous run_phases calls in the same base_dir must not
    corrupt each other's handlers or files."""
    errors: list[BaseException] = []

    def worker(i: int) -> None:
        try:
            with attach_run_log(f"00000000-0000-0000-0000-{i:012x}", tmp_path, keep=20):
                logging.getLogger("ascendo").info(f"worker {i}")
                time.sleep(0.05)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    ts = [threading.Thread(target=worker, args=(i,)) for i in range(5)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()

    assert not errors, f"concurrent attach raised: {errors}"
    # All 5 run dirs + their run.log files exist.
    for i in range(5):
        d = tmp_path / f"00000000-0000-0000-0000-{i:012x}"
        assert d.is_dir()
        log_file = d / "run.log"
        assert log_file.exists() and log_file.read_text(), (
            f"run.log empty/missing for worker {i}"
        )


def test_pruner_keep_zero_is_defensive_noop(tmp_path: Path) -> None:
    """keep=0 must not wipe the directory — it's a defensive guard
    against `--keep 0` typos that would otherwise nuke history."""
    for i in range(3):
        _make_run_dir(tmp_path, i)
    removed = prune_run_dirs(tmp_path, keep=0)
    assert removed == 0
    assert len(list(tmp_path.iterdir())) == 3


def test_pruner_preserves_non_run_id_directories(tmp_path: Path) -> None:
    """Operator-owned dirs (notes / backups / arbitrary names) must
    survive pruning regardless of mtime."""
    (tmp_path / "operator-notes").mkdir()
    (tmp_path / "old-backup-2024-01").mkdir()
    for i in range(50):
        _make_run_dir(tmp_path, i)

    removed = prune_run_dirs(tmp_path, keep=10)

    assert removed == 40, f"expected 40 pruned (50-10), got {removed}"
    assert (tmp_path / "operator-notes").is_dir()
    assert (tmp_path / "old-backup-2024-01").is_dir()


def test_empty_run_block_still_creates_populated_log(tmp_path: Path) -> None:
    """A run_phases call that emits no user-level logging.info should
    still leave attach/detach markers in run.log for post-mortem."""
    with attach_run_log("00000000-0000-0000-0000-fffffffffffe", tmp_path, keep=10) as log:
        # Intentionally no log records emitted by user code.
        pass

    assert log.exists()
    body = log.read_text()
    assert "run-log attached" in body
    assert "run-log detaching" in body


@pytest.mark.parametrize(
    "name,expected",
    [
        ("01de9a77-41a3-4867-9494-dd1155fa0ab0", True),    # UUID4 (current)
        ("00000000-0000-0000-0000-000000000000", True),    # zero UUID
        ("20260518T230000Z-abc123", True),                  # legacy timestamp
        ("not-a-run", False),
        ("operator-notes", False),
        ("01de9a77-41a3-4867-9494-dd1155fa0ab0-extra", False),
        ("", False),
    ],
    ids=["uuid4", "uuid-zero", "legacy", "plain", "operator", "uuid-with-suffix", "empty"],
)
def test_run_dir_name_recognition(name: str, expected: bool) -> None:
    from ascendo.orchestrator.run_logger import _looks_like_run_dir
    assert _looks_like_run_dir(name) is expected
