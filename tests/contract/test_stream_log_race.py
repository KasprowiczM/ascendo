"""The per-run stream-log path must be race-free across concurrent runs.

Regression guard for the audit's P2 stream-log race: the async worker used to
mutate the process-global ``os.environ[ASCENDO_STREAM_LOG]``, so two concurrent
runs could clobber each other's log path. The path is now conveyed via a
thread-local, and the global is never mutated.
"""
from __future__ import annotations

import os
import threading
import time
from pathlib import Path

from ascendo.orchestrator.stream_log import (
    STREAM_LOG_ENV_VAR,
    child_env_with_stream_log,
    current_stream_log_path,
    stream_log_context,
)


def test_concurrent_runs_do_not_clobber_stream_log(monkeypatch) -> None:
    monkeypatch.delenv(STREAM_LOG_ENV_VAR, raising=False)

    results: dict[str, str | None] = {}
    barrier = threading.Barrier(2)

    def worker(name: str, path: str) -> None:
        with stream_log_context(Path(path)):
            barrier.wait()          # force the two threads to interleave
            time.sleep(0.02)        # widen the window where a global would race
            env = child_env_with_stream_log()
            results[name] = env.get(STREAM_LOG_ENV_VAR)
            # Each thread also sees its own thread-local, not the other's.
            results[name + ":tls"] = str(current_stream_log_path())

    a = threading.Thread(target=worker, args=("a", "/tmp/runA/_stream.log"))
    b = threading.Thread(target=worker, args=("b", "/tmp/runB/_stream.log"))
    a.start(); b.start(); a.join(); b.join()

    assert results["a"] == "/tmp/runA/_stream.log"
    assert results["b"] == "/tmp/runB/_stream.log"
    assert results["a:tls"] == "/tmp/runA/_stream.log"
    assert results["b:tls"] == "/tmp/runB/_stream.log"
    # The process-global env is NEVER mutated by the conveyance mechanism.
    assert STREAM_LOG_ENV_VAR not in os.environ


def test_child_env_drops_stale_global_when_no_run_bound(monkeypatch) -> None:
    # A stale global from some other context must not leak into a child env
    # for a thread with no run bound.
    monkeypatch.setenv(STREAM_LOG_ENV_VAR, "/tmp/stale/_stream.log")
    env = child_env_with_stream_log()
    assert STREAM_LOG_ENV_VAR not in env


def test_context_restores_prior_on_exit() -> None:
    assert current_stream_log_path() is None
    with stream_log_context(Path("/tmp/x/_stream.log")):
        assert current_stream_log_path() == Path("/tmp/x/_stream.log")
    assert current_stream_log_path() is None
