"""Per-run stream-log path conveyance — thread-local, race-free.

The dashboard tails ``<run-dir>/_stream.log`` for live progress; the bash phase
scripts append to ``$ASCENDO_STREAM_LOG``. The async worker used to mutate the
*process-global* ``os.environ[ASCENDO_STREAM_LOG]`` with save/restore. With two
concurrent runs (two worker threads) sharing one process, that global raced:
run A could set its path, run B overwrite it, then A's subprocess inherit B's
log path — progress lines landing in the wrong run's stream.

We convey the path through a **thread-local** instead. Each worker thread sets
its own path via :func:`stream_log_context`; package managers run synchronously
*in that same worker thread*, so they read the correct path via
:func:`child_env_with_stream_log` when building the child env they pass to
``subprocess.Popen(env=...)``. No process-global mutation, no cross-run clobber.
"""
from __future__ import annotations

import contextlib
import os
import threading
from collections.abc import Iterator, Mapping
from pathlib import Path

STREAM_LOG_FILENAME = "_stream.log"
STREAM_LOG_ENV_VAR = "ASCENDO_STREAM_LOG"

_tls = threading.local()


def current_stream_log_path() -> Path | None:
    """The stream-log path for the *current thread's* run, or ``None``."""
    return getattr(_tls, "path", None)


@contextlib.contextmanager
def stream_log_context(path: Path | None) -> Iterator[None]:
    """Bind ``path`` as this thread's stream log for the duration of the block.

    Re-entrant: restores the prior value on exit so nested contexts behave.
    """
    prev = getattr(_tls, "path", None)
    _tls.path = path
    try:
        yield
    finally:
        _tls.path = prev


def child_env_with_stream_log(base_env: Mapping[str, str] | None = None) -> dict[str, str]:
    """A child-process env (copy of ``base_env`` / ``os.environ``) with
    ``ASCENDO_STREAM_LOG`` set from the current thread's run, or removed when
    this thread has no run bound (so a stale inherited global can't leak in).
    """
    env = dict(base_env if base_env is not None else os.environ)
    path = current_stream_log_path()
    if path is not None:
        env[STREAM_LOG_ENV_VAR] = str(path)
    else:
        env.pop(STREAM_LOG_ENV_VAR, None)
    return env
