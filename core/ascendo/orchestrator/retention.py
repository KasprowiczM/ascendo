"""Run-directory retention policy."""
from __future__ import annotations

import logging
import shutil
import time
from pathlib import Path
from uuid import UUID

_log = logging.getLogger(__name__)


def _is_uuid(name: str) -> bool:
    """Return True if *name* parses as a valid UUID (any version)."""
    try:
        UUID(name)
        return True
    except ValueError:
        return False


def prune_runs(
    runs_dir: Path,
    *,
    keep_count: int | None = None,
    keep_days: int | None = None,
    dry_run: bool = False,
) -> list[Path]:
    """Remove old run directories based on retention policy.

    Args:
        runs_dir: The base runs directory (~/.ascendo/runs).
        keep_count: Keep at most this many runs (by mtime, newest first).
        keep_days: Keep runs from the last N days.
        dry_run: If True, return paths that *would* be pruned but don't delete.

    Returns:
        List of pruned (or would-be-pruned) directories.

    At least one of keep_count or keep_days must be specified.  If both are
    given, a run is kept if it satisfies *either* criterion (union).
    """
    if keep_count is None and keep_days is None:
        raise ValueError("At least one of keep_count or keep_days must be specified.")

    if not runs_dir.is_dir():
        return []

    # Collect UUID-named subdirectories with their mtime.
    entries: list[tuple[Path, float]] = []
    for child in runs_dir.iterdir():
        if not child.is_dir():
            continue
        if not _is_uuid(child.name):
            continue
        try:
            mtime = child.stat().st_mtime
        except OSError:
            continue
        entries.append((child, mtime))

    # Sort newest-first by mtime.
    entries.sort(key=lambda e: e[1], reverse=True)

    now = time.time()
    keep_indices: set[int] = set()

    if keep_count is not None:
        for i in range(min(keep_count, len(entries))):
            keep_indices.add(i)

    if keep_days is not None:
        cutoff = now - (keep_days * 86_400)
        for i, (_path, mtime) in enumerate(entries):
            if mtime >= cutoff:
                keep_indices.add(i)

    pruned: list[Path] = []
    for i, (path, _mtime) in enumerate(entries):
        if i in keep_indices:
            continue
        if dry_run:
            _log.info("would prune: %s", path)
        else:
            _log.info("pruning: %s", path)
            shutil.rmtree(path, ignore_errors=True)
        pruned.append(path)

    return pruned
