"""Contract tests for :mod:`ascendo.orchestrator.retention`.

Verifies the run-directory retention policy:
  - UUID-only directory filtering (non-UUID dirs are never touched)
  - keep_count keeps the N newest by mtime
  - keep_days keeps dirs modified within the window
  - union semantics when both are specified
  - dry_run never deletes
  - ValueError when neither parameter is given
"""
from __future__ import annotations

import os
import time
import uuid
from pathlib import Path

import pytest

from ascendo.orchestrator.retention import prune_runs


def _make_run_dir(base: Path, name: str | None = None, age_seconds: float = 0) -> Path:
    """Create a synthetic run directory and backdate its mtime."""
    dirname = name or str(uuid.uuid4())
    d = base / dirname
    d.mkdir(parents=True, exist_ok=True)
    (d / "check__brew.json").write_text("{}", encoding="utf-8")
    if age_seconds:
        t = time.time() - age_seconds
        os.utime(d, (t, t))
    return d


class TestPruneRuns:
    def test_raises_when_no_criteria(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="At least one"):
            prune_runs(tmp_path)

    def test_empty_dir(self, tmp_path: Path) -> None:
        assert prune_runs(tmp_path, keep_count=5) == []

    def test_nonexistent_dir(self, tmp_path: Path) -> None:
        missing = tmp_path / "does_not_exist"
        assert prune_runs(missing, keep_count=5) == []

    def test_keep_count_prunes_oldest(self, tmp_path: Path) -> None:
        old = _make_run_dir(tmp_path, age_seconds=300)
        mid = _make_run_dir(tmp_path, age_seconds=200)
        new = _make_run_dir(tmp_path, age_seconds=100)

        pruned = prune_runs(tmp_path, keep_count=2)

        assert len(pruned) == 1
        assert pruned[0].name == old.name
        assert not old.exists()
        assert mid.exists()
        assert new.exists()

    def test_keep_count_equal_to_total_prunes_nothing(self, tmp_path: Path) -> None:
        dirs = [_make_run_dir(tmp_path, age_seconds=i * 100) for i in range(3)]
        pruned = prune_runs(tmp_path, keep_count=3)
        assert pruned == []
        assert all(d.exists() for d in dirs)

    def test_keep_days_prunes_old(self, tmp_path: Path) -> None:
        recent = _make_run_dir(tmp_path, age_seconds=3600)       # 1 hour old
        old = _make_run_dir(tmp_path, age_seconds=10 * 86_400)   # 10 days old

        pruned = prune_runs(tmp_path, keep_days=7)

        assert len(pruned) == 1
        assert pruned[0].name == old.name
        assert not old.exists()
        assert recent.exists()

    def test_union_semantics(self, tmp_path: Path) -> None:
        """A run is kept if it satisfies *either* keep_count OR keep_days."""
        d1 = _make_run_dir(tmp_path, age_seconds=1 * 86_400)     # 1d old (within 7d)
        d2 = _make_run_dir(tmp_path, age_seconds=5 * 86_400)     # 5d old (within 7d)
        d3 = _make_run_dir(tmp_path, age_seconds=20 * 86_400)    # 20d old (outside 7d)
        d4 = _make_run_dir(tmp_path, age_seconds=30 * 86_400)    # 30d old (outside 7d)

        # keep_count=3 keeps d1, d2, d3 (by mtime). keep_days=7 keeps d1, d2.
        # Union: d1, d2, d3 → only d4 pruned.
        pruned = prune_runs(tmp_path, keep_count=3, keep_days=7)

        assert len(pruned) == 1
        assert pruned[0].name == d4.name
        assert d1.exists()
        assert d2.exists()
        assert d3.exists()

    def test_non_uuid_dirs_ignored(self, tmp_path: Path) -> None:
        """Directories whose names are not valid UUIDs are never touched."""
        safe = tmp_path / "not-a-uuid-dir"
        safe.mkdir()
        (safe / "data.txt").write_text("important", encoding="utf-8")

        run = _make_run_dir(tmp_path, age_seconds=500)

        pruned = prune_runs(tmp_path, keep_count=0)

        assert len(pruned) == 1
        assert pruned[0].name == run.name
        assert safe.exists(), "non-UUID directory must not be deleted"

    def test_dry_run_does_not_delete(self, tmp_path: Path) -> None:
        old = _make_run_dir(tmp_path, age_seconds=500)

        pruned = prune_runs(tmp_path, keep_count=0, dry_run=True)

        assert len(pruned) == 1
        assert pruned[0].name == old.name
        assert old.exists(), "dry_run must not delete directories"

    def test_files_in_runs_dir_ignored(self, tmp_path: Path) -> None:
        """Regular files sitting directly in runs_dir are not counted."""
        (tmp_path / "stale.lock").write_text("", encoding="utf-8")
        run = _make_run_dir(tmp_path, age_seconds=100)

        pruned = prune_runs(tmp_path, keep_count=5)

        assert pruned == []
        assert run.exists()
