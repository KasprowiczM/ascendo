"""Cross-process run lock shared by the CLI, the dashboard and scheduled runs.

A mutating run (``apply`` / ``cleanup``) holds ``<ascendo_home>/run.lock``
for its whole duration so two processes can never upgrade the same machine
at once. Read-only phases (check / plan / verify) never take the lock.

Port of macOS_updates ``lib/python/run_lock.py`` with Ascendo differences:

* re-entrancy is keyed on ``(pid, run_id)``, not the pid alone -- dashboard
  worker threads share one pid (in-process overlap is already refused by the
  run registry);
* liveness is probed with ``os.kill(pid, 0)`` which is only safe on POSIX
  (on Windows it would *terminate* the process), so the feature is a no-op on
  ``win32`` -- Windows and Ubuntu behaviour is unchanged;
* a lock whose owner is dead is stolen; an unparseable lock file is stolen
  only once it is older than ``STALE_AFTER_SECONDS``.

The lock file lives next to the runs directory (``~/.ascendo/run.lock``);
``ASCENDO_RUN_LOCK_FILE`` overrides the location (tests).
"""
from __future__ import annotations

import errno
import os
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

LOCK_ENV_VAR = "ASCENDO_RUN_LOCK_FILE"
LOCK_FILENAME = "run.lock"
STALE_AFTER_SECONDS = 6 * 3600
_SUPPORTED = sys.platform != "win32"


class RunLockError(RuntimeError):
    """Another Ascendo run holds the lock (CLI exit 11, HTTP 409)."""


def default_lock_path(runs_dir: Path) -> Path:
    """``$ASCENDO_RUN_LOCK_FILE`` or ``<runs_dir>/../run.lock``."""
    override = os.environ.get(LOCK_ENV_VAR)
    if override:
        return Path(override)
    return Path(runs_dir).parent / LOCK_FILENAME


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError as exc:
        return exc.errno == errno.EPERM
    return True


def _read(path: Path) -> dict:
    data: dict = {}
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            key, sep, value = line.partition("=")
            if not sep:
                continue
            if key == "pid":
                try:
                    data["pid"] = int(value)
                except ValueError:
                    data["pid"] = None
            else:
                data[key] = value
    except OSError:
        return {}
    return data


def _try_create(path: Path, run_id: str) -> bool:
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    try:
        fd = os.open(str(path), flags, 0o600)
    except FileExistsError:
        return False
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(
            f"pid={os.getpid()}\n"
            f"started={time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}\n"
            f"started_unix={time.time()}\n"
            f"run_id={run_id}\n"
        )
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return True


def held_by_other(path: Path, *, run_id: str = "") -> dict | None:
    """Owner payload when a *live foreign* run holds the lock, else ``None``.

    Read-only peek (never creates/steals) so a front end can answer 409
    immediately instead of failing later inside a worker thread."""
    if not _SUPPORTED:
        return None
    payload = _read(Path(path))
    pid = payload.get("pid")
    if not pid or not _pid_alive(pid):
        return None
    if pid == os.getpid() and payload.get("run_id") == run_id:
        return None
    return payload


def acquire(path: Path, *, run_id: str) -> Path:
    """Take the lock or raise :class:`RunLockError`. Re-entrant per ``(pid, run_id)``."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if _try_create(path, run_id):
        return path
    payload = _read(path)
    pid = payload.get("pid")
    if pid == os.getpid() and payload.get("run_id") == run_id:
        return path
    if pid and _pid_alive(pid):
        raise RunLockError(
            f"another Ascendo run is in progress (pid {pid}, run {payload.get('run_id', '?')}, "
            f"since {payload.get('started', '?')}); wait for it to finish"
        )
    if not pid:
        # No readable owner: only steal it once it is old.
        try:
            age = time.time() - path.stat().st_mtime
        except OSError:
            age = STALE_AFTER_SECONDS + 1
        if age <= STALE_AFTER_SECONDS:
            raise RunLockError(f"run lock file exists at {path} with no readable owner")
    path.unlink(missing_ok=True)
    if not _try_create(path, run_id):
        raise RunLockError(f"run lock file exists at {path}")
    return path


def release(path: Path, *, run_id: str) -> None:
    """Remove the lock if (and only if) this process/run owns it."""
    path = Path(path)
    payload = _read(path)
    if not payload:
        return
    if payload.get("pid") == os.getpid() and payload.get("run_id") in (run_id, None):
        path.unlink(missing_ok=True)


@contextmanager
def run_lock(path: Path, run_id: str, *, enabled: bool = True) -> Iterator[None]:
    """Hold the run lock for the ``with`` body; no-op when disabled/unsupported."""
    if not enabled or not _SUPPORTED:
        yield
        return
    acquire(path, run_id=run_id)
    try:
        yield
    finally:
        release(path, run_id=run_id)


__all__ = [
    "LOCK_ENV_VAR",
    "RunLockError",
    "acquire",
    "default_lock_path",
    "held_by_other",
    "release",
    "run_lock",
]
