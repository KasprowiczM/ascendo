"""Sidecar disk I/O — atomic write, locked read, partial recovery.

This is the only module in core that touches the filesystem on behalf
of sidecar JSONs. Everything above (the run reconciler, the dashboard
backend, the SQLite persister) reads and writes sidecars exclusively
through the public helpers defined here.

Three properties matter, in order:

1. **Atomicity.** A reader must never observe a half-written file.
   Achieved by writing to a same-directory tempfile and renaming via
   :func:`os.replace`. Same-directory keeps it on the same filesystem,
   which is required for ``rename(2)`` atomicity guarantees.
2. **Concurrency.** Two writers must serialize, and a reader concurrent
   with a writer must either see the previous valid version or wait.
   Implemented via a sibling ``.lock`` file with :func:`fcntl.flock`
   on POSIX and :func:`msvcrt.locking` on Windows. See
   :func:`_locked` for the cross-OS contract.
3. **Best-effort recovery.** When (1) and (2) fail anyway — process
   killed mid-write, OS crash, disk full at flush time — readers must
   still produce *something* the dashboard can display. See
   :func:`recover_partial` and :class:`RecoveryStub`.

Filename convention:

    ``<base_dir>/<run-id>/<phase>__<category>.json``

Phase + category are joined by double underscore. Category is sanitized
for filesystem-safe characters (``/`` -> ``_``).

Cross-OS notes:

- POSIX ``fcntl.flock`` is advisory; only processes that also call
  ``flock`` cooperate. That's fine — we own all writers.
- Windows ``msvcrt.locking`` is *mandatory*: a non-locking process
  reading the file at the wrong moment can fail the read. We cope by
  retrying reads with exponential backoff (max ~500 ms total).
- ``msvcrt`` has no shared lock. We treat shared-lock requests on
  Windows as a no-op: callers tolerate the very brief race window
  via the read retry loop.
"""

from __future__ import annotations

import json
import logging
import os
import random
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..models.sidecar import Sidecar, parse_sidecar

# Platform-specific locking primitives — imported once at module load
# rather than inside the lock context manager so import errors surface
# at module-import time, not at first lock attempt.
if os.name == "posix":
    import fcntl  # type: ignore[import-not-found]
elif os.name == "nt":
    import msvcrt  # type: ignore[import-not-found]


__all__ = [
    "RecoveryStub",
    "SidecarIOError",
    "SidecarLockError",
    "SidecarReadError",
    "SidecarWriteError",
    "detect_stale_locks",
    "list_run_sidecars",
    "read_run",
    "read_sidecar",
    "recover_partial",
    "write_sidecar",
]


_LOG = logging.getLogger(__name__)
# Module load fingerprint — useful when investigating mount-cache issues
# during development. Not part of the public API.
_MODULE_REVISION = "m2.4-r1"

# Lock retry policy — applies to:
#   - exclusive lock acquisition on POSIX (LOCK_EX|LOCK_NB) and Windows
#     (msvcrt LK_NBLCK)
#   - shared lock acquisition on POSIX (LOCK_SH|LOCK_NB)
#
# Schedule: capped exponential backoff with jitter via the simple
# doubling sequence below. Sum ~6.5 s total — generous because
# under heavy contention the per-write window can briefly exceed 1 s
# when fsync hits a slow disk. Real workloads see at most one
# concurrent writer per (run-id, phase, category) tuple, so this
# budget is effectively never exhausted; it only matters for tests
# and adversarial scenarios.
_LOCK_BACKOFF_MS: tuple[int, ...] = (
    25, 50, 100, 200, 400, 600, 800, 1000, 1200, 1500, 1750,
)

# Read retry policy — applies on Windows when a concurrent writer briefly
# holds an exclusive byte-range lock. Schedule: 25, 50, 100, 150, 200 ms
# (5 attempts; sum ~525 ms).
_READ_BACKOFF_MS: tuple[int, ...] = (25, 50, 100, 150, 200)


# ── Exceptions ────────────────────────────────────────────────────────


class SidecarIOError(RuntimeError):
    """Base class for sidecar I/O failures.

    Anything raised by this module that isn't a programmer error
    (TypeError, ValueError on bad arguments) is a subclass of this.
    Callers can ``except SidecarIOError`` to catch all I/O paths in
    one place.
    """


class SidecarReadError(SidecarIOError):
    """Sidecar file existed but couldn't be parsed.

    Distinct from :class:`FileNotFoundError`: the file was readable
    bytes-wise, but those bytes weren't a valid v1 sidecar (truncated
    JSON, schema mismatch, missing required field).
    """


class SidecarWriteError(SidecarIOError):
    """Sidecar write failed at the OS level.

    Disk full, permission denied, parent directory not writable, etc.
    Atomicity is preserved: on this exception, the destination path
    either holds the prior valid sidecar or doesn't exist.
    """


class SidecarLockError(SidecarIOError):
    """Couldn't acquire the lock after retry budget exhausted.

    Retry budget is :data:`_LOCK_BACKOFF_MS`. Hitting this usually
    indicates a stuck writer holding the lock (process crashed without
    releasing). Operator action: inspect the ``.lock`` sibling file's
    age and remove it manually if its writer is gone.
    """


# ── RecoveryStub ──────────────────────────────────────────────────────


class RecoveryStub(BaseModel):
    """Placeholder returned by :func:`read_run` for unreadable sidecars.

    The dashboard renders these in the run detail view as a corruption
    notice with the originating path, so users can tell which phase had
    the problem instead of seeing the whole run as missing.

    This is *not* a Sidecar — it deliberately doesn't satisfy the
    Sidecar shape. Callers must check ``isinstance(x, RecoveryStub)``
    when iterating the result of :func:`read_run`.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    path: Path = Field(
        description="Filesystem path of the sidecar that couldn't be read.",
    )
    error: str = Field(
        description="Short human-readable reason. e.g. 'truncated JSON', 'parse error'.",
    )
    partial: dict[str, object] | None = Field(
        default=None,
        description=(
            "Best-effort top-level dict from the partial JSON, if "
            ":func:`recover_partial` could salvage one. None if even "
            "the loose recovery failed."
        ),
    )


# ── Filename convention ───────────────────────────────────────────────


def _sanitize_category(category: str) -> str:
    r"""Replace path-separator characters in a category name.

    The category lands directly in a filename. Real categories (winget,
    apt, brew_formula) never contain ``/`` or ``\``, but plugins can
    declare arbitrary :class:`SourceType` values; we defend against
    a malicious or careless plugin escaping the run directory.
    """
    return category.replace("/", "_").replace("\\", "_")


def _sidecar_path(base_dir: Path, sidecar: Sidecar) -> Path:
    """Compute the canonical on-disk path for a sidecar.

    Layout: ``<base_dir>/<run-id>/<phase>__<category>.json``.
    """
    run_id = str(sidecar.run.id)
    phase = sidecar.phase.value
    category = _sanitize_category(sidecar.category.value)
    return base_dir / run_id / f"{phase}__{category}.json"


def _lock_path_for(target: Path) -> Path:
    """Return the sibling ``.lock`` path for a given sidecar target.

    The lock lives next to the target so a single ``rm -rf <run-id>/``
    cleans both. We use ``<target>.lock`` rather than a single
    per-directory lock because two writers targeting different files
    in the same run dir don't actually conflict.
    """
    return target.with_name(target.name + ".lock")


# ── Cross-OS locking ──────────────────────────────────────────────────


def _flock_posix(fd: int, *, exclusive: bool) -> None:
    """POSIX advisory lock with retry budget.

    Raises :class:`SidecarLockError` if the lock can't be acquired
    after :data:`_LOCK_BACKOFF_MS`.
    """
    flag = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
    flag |= fcntl.LOCK_NB
    last_err: BaseException | None = None
    # Try once, then up to len(_LOCK_BACKOFF_MS) more times. Jitter the
    # sleep by +/-25% to break thundering-herd lockstep when many writers
    # contend the same lock and would otherwise retry in sync.
    attempts = 1 + len(_LOCK_BACKOFF_MS)
    for attempt in range(attempts):
        try:
            fcntl.flock(fd, flag)
            return
        except BlockingIOError as exc:
            last_err = exc
            if attempt < len(_LOCK_BACKOFF_MS):
                base_ms = _LOCK_BACKOFF_MS[attempt]
                jitter = random.uniform(-0.25, 0.25) * base_ms
                time.sleep(max(1.0, base_ms + jitter) / 1000.0)
    raise SidecarLockError(
        f"could not acquire {'exclusive' if exclusive else 'shared'} lock "
        f"after {attempts} attempts"
    ) from last_err


def _flock_windows(fd: int, *, exclusive: bool) -> None:
    """Windows mandatory byte-range lock.

    ``msvcrt.locking`` only offers exclusive locking. Shared-lock
    requests degrade to a no-op; the read retry loop in
    :func:`_read_bytes_with_retry` covers the very small race window.
    """
    if not exclusive:
        # No shared-lock primitive on Windows. Cooperative-only readers
        # cope via retry. Documented in module docstring.
        return
    last_err: BaseException | None = None
    attempts = 1 + len(_LOCK_BACKOFF_MS)
    for attempt in range(attempts):
        try:
            # Lock 1 byte at offset 0. The file is empty (a stub lock
            # file), so locking byte 0 establishes ownership.
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            return
        except OSError as exc:
            last_err = exc
            if attempt < len(_LOCK_BACKOFF_MS):
                base_ms = _LOCK_BACKOFF_MS[attempt]
                jitter = random.uniform(-0.25, 0.25) * base_ms
                time.sleep(max(1.0, base_ms + jitter) / 1000.0)
    raise SidecarLockError(
        f"could not acquire exclusive lock after {attempts} attempts"
    ) from last_err


def _funlock_windows(fd: int) -> None:
    """Release a Windows byte-range lock, swallowing benign errors.

    If the lock wasn't held (e.g. shared-lock no-op path) ``locking``
    raises ``OSError``; that's fine.
    """
    try:
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    except OSError:
        pass


@contextmanager
def _locked(path: Path, *, exclusive: bool) -> Iterator[None]:
    """Acquire a lock on ``path`` (the sidecar target) for the duration.

    Internally locks a sibling ``.lock`` file rather than the data file
    itself. This avoids any chance of holding a lock on a file that's
    about to be replaced by :func:`os.replace`.

    Args:
        path: Path to the *data* file (sidecar). The ``.lock`` sibling
            is created next to it.
        exclusive: True for writers, False for readers. On Windows,
            shared locks degrade to a no-op (see module docstring).

    Yields:
        None. The lock is held while the ``with`` body executes.

    Raises:
        SidecarLockError: lock couldn't be acquired within the retry
            budget.
        SidecarIOError: the ``.lock`` file couldn't be opened (parent
            directory missing, permission denied, etc.).
    """
    lock_path = _lock_path_for(path)
    # Ensure parent dir exists so writers can create the lock file
    # before the data file. Readers should never get here without an
    # existing parent (caller would have hit FileNotFoundError already).
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    # Open with O_RDWR|O_CREAT — the lock file may not exist yet.
    try:
        fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o644)
    except OSError as exc:
        raise SidecarIOError(f"could not open lock file {lock_path}: {exc}") from exc

    try:
        if os.name == "posix":
            _flock_posix(fd, exclusive=exclusive)
        elif os.name == "nt":
            _flock_windows(fd, exclusive=exclusive)
        else:  # pragma: no cover — defensive
            raise SidecarIOError(f"unsupported OS for locking: {os.name}")

        try:
            yield
        finally:
            if os.name == "nt":
                _funlock_windows(fd)
            # POSIX flock is auto-released on close(); no explicit
            # unlock needed.
    finally:
        try:
            os.close(fd)
        except OSError:
            pass


# ── Atomic write ──────────────────────────────────────────────────────


def _atomic_write_json(path: Path, payload: str) -> None:
    """Write ``payload`` to ``path`` atomically.

    Strategy: write to a tempfile in the same directory, fsync, then
    :func:`os.replace`. Same directory keeps the rename on the same
    filesystem, which is the precondition ``rename(2)`` requires for
    atomicity.

    On POSIX the destination receives mode ``0o644``. On Windows the
    chmod is a no-op (umask-style permissions don't apply).

    Args:
        path: Destination path. Parent dir is created if missing.
        payload: Pre-serialized JSON string.

    Raises:
        SidecarWriteError: any OS error during write, fsync, or rename.
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    tmp_path: Path | None = None
    try:
        # delete=False because we want to control the rename ourselves.
        # dir=path.parent ensures same-filesystem rename.
        tf = tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=str(path.parent),
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        )
        tmp_path = Path(tf.name)
        try:
            tf.write(payload)
            tf.flush()
            os.fsync(tf.fileno())
        finally:
            tf.close()

        if os.name == "posix":
            try:
                os.chmod(tmp_path, 0o644)
            except OSError:
                # Non-fatal — some filesystems (NFS, FAT) reject chmod.
                _LOG.debug("chmod 0o644 failed on %s; continuing", tmp_path)

        os.replace(str(tmp_path), str(path))
        tmp_path = None  # ownership transferred to destination
    except OSError as exc:
        raise SidecarWriteError(f"failed to write {path}: {exc}") from exc
    finally:
        # If we still own the tempfile (rename didn't happen), clean up.
        if tmp_path is not None and tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                _LOG.warning("could not unlink leftover tempfile %s", tmp_path)


# ── Read with retry (Windows race tolerance) ──────────────────────────


def _read_bytes_with_retry(path: Path) -> bytes:
    """Read ``path`` with brief retries on transient sharing violations.

    Windows can fail an open with sharing violations when a concurrent
    writer is mid-rename. Retry with exponential backoff up to ~500 ms
    total before giving up. POSIX doesn't have this problem but the
    same code path is harmless there.
    """
    last_err: BaseException | None = None
    attempts = 1 + len(_READ_BACKOFF_MS)
    for attempt in range(attempts):
        try:
            return path.read_bytes()
        except (PermissionError, OSError) as exc:
            # Windows raises PermissionError for sharing violations;
            # POSIX shouldn't reach this path under normal operation.
            last_err = exc
            if attempt < len(_READ_BACKOFF_MS):
                time.sleep(_READ_BACKOFF_MS[attempt] / 1000.0)
    raise SidecarReadError(f"could not read {path}: {last_err}") from last_err


# ── Public API: write ─────────────────────────────────────────────────


def write_sidecar(sidecar: Sidecar, *, base_dir: Path) -> Path:
    """Atomically write ``sidecar`` to its canonical path.

    The file is serialized as UTF-8 JSON with the schema-by-alias key
    (``"schema": "ascendo/v1"``) emitted under the alias name, matching
    the on-disk contract. An exclusive lock on the sibling ``.lock``
    file is held for the entire serialize+rename sequence.

    Args:
        sidecar: A validated :class:`Sidecar` instance.
        base_dir: Root directory under which ``<run-id>/`` subdirs live.
            Created if missing.

    Returns:
        The :class:`Path` written.

    Raises:
        SidecarLockError: another writer is holding the lock.
        SidecarWriteError: disk write failed.
    """
    target = _sidecar_path(base_dir, sidecar)
    # Pydantic v2: by_alias=True so ``schema_`` serializes as ``schema``.
    payload = sidecar.model_dump_json(by_alias=True, indent=2)

    with _locked(target, exclusive=True):
        _atomic_write_json(target, payload)

    return target


# ── Public API: read ──────────────────────────────────────────────────


def read_sidecar(path: Path) -> Sidecar:
    """Read and validate a sidecar from disk.

    Acquires a shared lock (POSIX) or no-op + retry-on-failure
    (Windows; see module docstring). Raises :class:`SidecarReadError`
    on any parse failure.

    Args:
        path: Absolute path to the sidecar JSON file.

    Returns:
        A validated :class:`Sidecar`.

    Raises:
        FileNotFoundError: ``path`` does not exist.
        SidecarReadError: bytes were read but couldn't be parsed.
        SidecarLockError: shared lock acquisition failed (POSIX only).
    """
    if not path.exists():
        raise FileNotFoundError(path)

    with _locked(path, exclusive=False):
        raw = _read_bytes_with_retry(path)

    try:
        return parse_sidecar(raw)
    except (ValidationError, ValueError, json.JSONDecodeError, KeyError) as exc:
        # KeyError is raised by translate_legacy_v1 when a payload tagged
        # as legacy is missing a required legacy field (e.g. ``kind`` or
        # ``host``). Treat it as a parse failure so it routes through the
        # same channel manager code already handles (SidecarReadError) —
        # never leak a raw KeyError up to the orchestrator, where only
        # ManagerError is caught and a stray exception kills the whole
        # async run silently.
        raise SidecarReadError(f"could not parse {path}: {exc!r}") from exc


def list_run_sidecars(run_dir: Path) -> list[Path]:
    """List sidecar JSON files in a run directory, sorted.

    Skips:
      - Hidden files (``.foo``) — these include the lock files we
        create with ``.<name>.tmp`` prefix during atomic write.
      - ``.lock`` sibling files (sidecar.json.lock).
      - ``.partial`` files (reserved for future explicit partials).
      - Directories.

    Args:
        run_dir: Path to a ``<base_dir>/<run-id>/`` directory.

    Returns:
        Sorted list of absolute paths.
    """
    if not run_dir.is_dir():
        return []

    out: list[Path] = []
    for entry in run_dir.iterdir():
        if not entry.is_file():
            continue
        name = entry.name
        if name.startswith("."):
            continue
        if name.endswith(".lock") or name.endswith(".partial"):
            continue
        if not name.endswith(".json"):
            continue
        out.append(entry)
    out.sort(key=lambda p: p.name)
    return out


def read_run(run_dir: Path) -> list[Sidecar | RecoveryStub]:
    """Read every sidecar in a run directory.

    Per-file errors are logged but never abort the run read — the
    failed sidecar is replaced in the result list with a
    :class:`RecoveryStub` so the caller can render a partial run.

    Args:
        run_dir: Path to a ``<base_dir>/<run-id>/`` directory.

    Returns:
        List of :class:`Sidecar` or :class:`RecoveryStub`, in filename
        order.
    """
    out: list[Sidecar | RecoveryStub] = []
    for path in list_run_sidecars(run_dir):
        try:
            out.append(read_sidecar(path))
        except (SidecarReadError, SidecarLockError, SidecarIOError) as exc:
            _LOG.warning("sidecar read failed for %s: %s", path, exc)
            stub = recover_partial(path)
            if stub is not None:
                # recover_partial returns a Sidecar on success; if it
                # got that far, append it instead of a stub.
                out.append(stub)
            else:
                out.append(
                    RecoveryStub(
                        path=path,
                        error=str(exc),
                        partial=None,
                    )
                )
    return out


# ── Public API: partial recovery ──────────────────────────────────────


def _try_parse_full(raw: bytes) -> Sidecar | None:
    """Attempt full parse; return None on failure (no exception)."""
    try:
        return parse_sidecar(raw)
    except (ValidationError, ValueError, json.JSONDecodeError):
        return None


def _try_truncate_to_valid_json(raw: bytes) -> dict[str, object] | None:
    """Try discarding trailing bytes to find the longest valid JSON prefix.

    Cheap implementation: scan backwards for the last ``}`` and try
    parsing each candidate. Bounded to last 4 KiB to avoid pathological
    cost on large sidecars.
    """
    if not raw:
        return None
    # Walk backwards from end, capping the search window.
    text = raw.decode("utf-8", errors="replace")
    n = len(text)
    floor = max(0, n - 4096)
    # Try progressively shorter prefixes ending at each '}' we find.
    i = n
    while i > floor:
        i = text.rfind("}", floor, i)
        if i < 0:
            break
        candidate = text[: i + 1]
        try:
            obj = json.loads(candidate)
        except json.JSONDecodeError:
            # Step past this '}' to keep looking.
            continue
        if isinstance(obj, dict):
            return obj
        # Non-dict valid JSON (an array, number, etc.) isn't useful.
        return None
    return None


# Required top-level keys for a stub sidecar to be useful as evidence
# of what was attempted. From ADR-0003. We use the *alias* name (schema
# rather than schema_) because that's what's on disk.
_STUB_REQUIRED_KEYS: frozenset[str] = frozenset(
    {"schema", "run", "host", "tool", "phase", "category", "started_at"}
)


def recover_partial(path: Path) -> Sidecar | None:
    """Best-effort recovery of a partially-written sidecar.

    Strategy (in order):

    1. Try a full parse — maybe the sidecar is fine and the caller's
       earlier read just hit a transient lock race.
    2. Try truncating trailing bytes back to the last well-formed
       JSON object. Useful when the writer crashed mid-flush.
    3. If the recovered dict has the seven required top-level fields,
       synthesize a minimal valid sidecar stamped FAILED with a single
       ERROR-level message pointing at the original path.

    This function never raises. Returns None if all strategies fail.

    Args:
        path: The (suspect) sidecar file path.

    Returns:
        A best-effort :class:`Sidecar` or None.
    """
    try:
        raw = path.read_bytes()
    except OSError as exc:
        _LOG.warning("recover_partial: cannot read %s: %s", path, exc)
        return None

    # Strategy 1: full parse.
    full = _try_parse_full(raw)
    if full is not None:
        return full

    # Strategy 2: truncate to last valid JSON object.
    obj = _try_truncate_to_valid_json(raw)
    if obj is None:
        return None

    # Strategy 2b: maybe the truncated dict is itself a complete sidecar.
    full = _try_parse_full(json.dumps(obj).encode("utf-8"))
    if full is not None:
        return full

    # Strategy 3: synthesize a minimal stub from the partial.
    if not _STUB_REQUIRED_KEYS.issubset(obj.keys()):
        _LOG.warning(
            "recover_partial: %s missing required keys %s",
            path,
            _STUB_REQUIRED_KEYS - set(obj.keys()),
        )
        return None

    # Inject defaults for the fields that *are* required by the model
    # but may be missing from a truncated payload (finished_at, status,
    # items, summary, messages). Use started_at as a finished_at proxy
    # so the order check passes.
    started_at = obj.get("started_at")
    stub_payload: dict[str, object] = {
        "schema": obj["schema"],
        "run": obj["run"],
        "host": obj["host"],
        "tool": obj["tool"],
        "phase": obj["phase"],
        "category": obj["category"],
        "started_at": started_at,
        "finished_at": obj.get("finished_at", started_at),
        "status": "failed",
        "items": [],
        "summary": {
            "total": 0,
            "success": 0,
            "up_to_date": 0,
            "failed": 0,
            "skipped": 0,
            "planned": 0,
            "partial": 0,
            "exit_code": -1,
        },
        "messages": [
            {
                "level": "error",
                "text": f"incomplete sidecar recovered from {path}",
            }
        ],
    }

    try:
        return parse_sidecar(stub_payload)
    except (ValidationError, ValueError, json.JSONDecodeError) as exc:
        _LOG.warning("recover_partial: stub synthesis failed for %s: %s", path, exc)
        return None


# ── P12: Stale lock detection ────────────────────────────────────────────────


def detect_stale_locks(
    run_dir: Path,
    *,
    max_age_seconds: int = 300,
) -> list[Path]:
    """Find ``.lock`` files older than ``max_age_seconds``.

    A lock file left behind by a crashed writer prevents any future
    writer from persisting sidecars in that run directory (on Windows;
    POSIX advisory locks auto-release on close). This helper lets the
    ``ascendo doctor`` command surface the issue.

    Args:
        run_dir: Path to a ``<base_dir>/<run-id>/`` directory.
        max_age_seconds: Files older than this are considered stale.
            Default 5 minutes — a single phase never runs that long.

    Returns:
        Sorted list of stale lock file paths.
    """
    if not run_dir.is_dir():
        return []

    import time as _time

    now = _time.time()
    stale: list[Path] = []
    for entry in run_dir.iterdir():
        if not entry.is_file():
            continue
        if not entry.name.endswith(".lock"):
            continue
        try:
            age = now - entry.stat().st_mtime
        except OSError:
            continue
        if age > max_age_seconds:
            stale.append(entry)

    stale.sort(key=lambda p: p.name)
    return stale
