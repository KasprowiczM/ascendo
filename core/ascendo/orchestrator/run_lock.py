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
* a lock is STALE (stolen) when its owner is dead, when it was written before
  the machine booted (a lock left behind by the very reboot a deferred
  ``softwareupdate -R`` can trigger, whose pid may have been reused), or when
  the live pid's process started AFTER the lock was written (pid reuse). An
  unparseable lock file is stolen only once it is older than
  ``STALE_AFTER_SECONDS``;
* stealing is serialised with an ``flock`` on ``run.lock.guard`` and re-checks
  the owner inside the guard, so two racing processes can never delete each
  other's freshly created lock.

The lock file is ``~/.ascendo/run.lock`` regardless of ``--runs-dir`` (CLI,
dashboard and scheduled runs must exclude each other); ``ASCENDO_RUN_LOCK_FILE``
overrides the location (tests).
"""
from __future__ import annotations

import errno
import os
import subprocess
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


def default_lock_path(runs_dir: Path | None = None) -> Path:
    """``$ASCENDO_RUN_LOCK_FILE`` or ``~/.ascendo/run.lock`` (``runs_dir`` is
    accepted for call-site compatibility and deliberately ignored)."""
    del runs_dir
    override = os.environ.get(LOCK_ENV_VAR)
    if override:
        return Path(override)
    return Path.home() / ".ascendo" / LOCK_FILENAME


# Process-start comparisons tolerate this much clock/rounding skew (seconds).
_START_TOLERANCE = 5.0


def _boot_time() -> float | None:
    """Epoch seconds of the last boot (read-only), or ``None`` if unknown.
    Test seam."""
    try:
        if sys.platform == "darwin":
            out = subprocess.run(
                ["sysctl", "-n", "kern.boottime"], capture_output=True, text=True,
                timeout=5, check=False).stdout
            marker = "sec ="
            if marker in out:
                return float(out.split(marker, 1)[1].split(",", 1)[0].strip())
        else:
            with open("/proc/stat", encoding="utf-8") as fh:
                for line in fh:
                    if line.startswith("btime "):
                        return float(line.split()[1])
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    return None


def _pid_start_time(pid: int) -> float | None:
    """Epoch start time of ``pid`` via ``ps -o lstart=`` (read-only), or
    ``None``. Test seam."""
    try:
        env = dict(os.environ, LC_ALL="C", LANG="C")
        out = subprocess.run(
            ["ps", "-o", "lstart=", "-p", str(pid)], capture_output=True, text=True,
            timeout=5, check=False, env=env).stdout.strip()
        if out:
            return time.mktime(time.strptime(" ".join(out.split()), "%a %b %d %H:%M:%S %Y"))
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    return None


def _started_unix(payload: dict) -> float | None:
    try:
        value = float(payload.get("started_unix", ""))
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _is_stale(payload: dict) -> bool:
    """True when the lock's owner can no longer be running that run."""
    pid = payload.get("pid")
    if not pid or not _pid_alive(pid):
        return bool(pid)            # dead owner is stale; no owner is handled by the caller
    started = _started_unix(payload)
    if started is None:
        return False
    boot = _boot_time()
    if boot is not None and started < boot:
        return True                 # written before the last boot: the pid is a stranger
    pid_start = _pid_start_time(pid)
    return pid_start is not None and pid_start > started + _START_TOLERANCE


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
    if not pid or not _pid_alive(pid) or _is_stale(payload):
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
    if pid == os.getpid() and payload.get("run_id") == run_id and not _is_stale(payload):
        return path
    if pid and not _is_stale(payload):
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
    _steal(path, payload, run_id)
    return path


def _steal(path: Path, seen: dict, run_id: str) -> None:
    """Replace a stale lock. Serialised by an flock guard; the owner is
    re-read inside the guard so a lock another process just (re)created is
    never deleted."""
    import fcntl  # noqa: PLC0415 -- POSIX only; the whole feature is gated on win32

    guard = path.with_name(path.name + ".guard")
    fd = os.open(str(guard), os.O_CREAT | os.O_WRONLY, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        current = _read(path)
        if current != seen:
            # Someone else already replaced it: re-evaluate from scratch.
            if _try_create(path, run_id):
                return
            raise RunLockError(f"another Ascendo run is in progress (lock at {path})")
        path.unlink(missing_ok=True)
        if not _try_create(path, run_id):
            raise RunLockError(f"run lock file exists at {path}")
    finally:
        os.close(fd)        # releases the flock


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
