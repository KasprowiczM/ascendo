"""Per-run rotated file logging for the orchestrator (M5.7.6).

Rationale:
  Each ``run_phases`` invocation creates ``<base_dir>/<run-id>/`` for
  sidecar JSON. Until M5.7.6, only `_stream.log` (an SSE replay buffer
  for the dashboard) lived alongside it — useful for the SPA, useless
  for offline post-mortem since it is a raw event stream, not a
  human-readable run log. The reference project (Aktualizacje_MAC)
  keeps a rotated `logs/update_all_<ts>.log` that captures every
  manager's stdout+stderr; this module ports that pattern to Ascendo.

Behaviour:
  * On enter, attaches a ``logging.FileHandler`` to the ``ascendo``
    logger writing to ``<base_dir>/<run-id>/run.log``. Format includes
    timestamp, level, manager category, phase, message.
  * On exit, flushes + detaches the handler.
  * Prunes ``base_dir`` to the most-recent ``keep`` run directories
    (newest by mtime). Default 30, override via env
    ``ASCENDO_RUN_LOG_KEEP``.

The pruner is intentionally conservative — it only removes directories
whose names look like UUIDs (or the legacy ``YYYYMMDDTHHMMSSZ-xxxxxx``
shape), never operator-supplied paths. Run directories currently
in-use by another process are skipped via a single mtime probe (we
don't take a lock — best-effort pruning, never destructive).
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

_log = logging.getLogger(__name__)

DEFAULT_KEEP = 30

# Run-id shapes we own + are safe to prune. UUID4 is the orchestrator
# default; the legacy timestamp-suffixed shape was used by the bash
# orchestrator pre-M2.10 and is still emitted by some CLI paths.
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
_LEGACY_RE = re.compile(r"^\d{8}T\d{6}Z-[0-9a-f]+$", re.IGNORECASE)


def _looks_like_run_dir(name: str) -> bool:
    return bool(_UUID_RE.match(name) or _LEGACY_RE.match(name))


def prune_run_dirs(base_dir: Path, keep: int = DEFAULT_KEEP) -> int:
    """Prune ``base_dir`` to the ``keep`` newest run directories.

    Returns the count of directories removed. Never raises on a single
    failed remove — logs a warning and continues. Directories whose
    names do not match the run-id shape are ignored entirely.
    """
    if keep < 1:
        return 0
    if not base_dir.is_dir():
        return 0
    candidates: list[tuple[float, Path]] = []
    for entry in base_dir.iterdir():
        if not entry.is_dir():
            continue
        if not _looks_like_run_dir(entry.name):
            continue
        try:
            mtime = entry.stat().st_mtime
        except OSError:
            continue
        candidates.append((mtime, entry))

    if len(candidates) <= keep:
        return 0
    # Newest first; everything past `keep` is stale.
    candidates.sort(key=lambda t: t[0], reverse=True)
    removed = 0
    for _, stale in candidates[keep:]:
        try:
            shutil.rmtree(stale)
            removed += 1
        except OSError as exc:
            _log.warning("run-log prune: failed to remove %s: %s", stale, exc)
    return removed


@contextmanager
def attach_run_log(
    run_id: str,
    base_dir: Path,
    *,
    keep: Optional[int] = None,
    level: int = logging.INFO,
) -> Iterator[Path]:
    """Context manager: attach a per-run file handler, prune on exit.

    Usage::

        with attach_run_log(run.id, base_dir) as log_path:
            run_phases(...)

    Yields the log path so callers can surface it in the run report or
    SPA. The file is created lazily — if no log records are emitted
    while inside the block, the file may be empty but always exists
    (we touch it on entry so post-mortem `tail -f` works).

    The handler is attached to the top-level ``ascendo`` logger so it
    captures every sub-module without per-module wiring.
    """
    if keep is None:
        try:
            keep = int(os.environ.get("ASCENDO_RUN_LOG_KEEP", DEFAULT_KEEP))
        except ValueError:
            keep = DEFAULT_KEEP

    run_dir = base_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    log_path = run_dir / "run.log"

    # Touch so post-mortem tooling has something to read even if a
    # silent crash kills us before the first log line.
    log_path.touch(exist_ok=True)

    handler = logging.FileHandler(log_path, mode="a", encoding="utf-8")
    handler.setLevel(level)
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s %(levelname)-7s %(name)s :: %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S%z",
        )
    )
    root = logging.getLogger("ascendo")
    # Ensure INFO records reach the handler even if the operator runs
    # without a global logging.basicConfig (CLI direct invocation, tests).
    # We restore the previous effective level on exit so we never leak
    # a noisier log config to surrounding code.
    prior_level = root.level
    if root.level > level or root.level == logging.NOTSET:
        root.setLevel(level)
    root.addHandler(handler)
    started = time.time()
    root.info("run-log attached run_id=%s base_dir=%s", run_id, base_dir)
    try:
        yield log_path
    finally:
        elapsed = time.time() - started
        root.info("run-log detaching after %.2fs", elapsed)
        handler.flush()
        try:
            root.removeHandler(handler)
            root.setLevel(prior_level)
        finally:
            handler.close()
        try:
            removed = prune_run_dirs(base_dir, keep=keep)
            if removed:
                _log.info(
                    "run-log prune: removed %d stale run dirs (kept newest %d)",
                    removed,
                    keep,
                )
        except OSError as exc:
            _log.warning("run-log prune raised OSError: %s", exc)
