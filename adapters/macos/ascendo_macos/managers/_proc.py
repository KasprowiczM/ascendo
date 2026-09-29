"""Shared bounded process runner for the macOS package managers.

Every manager (brew, mas, npm, pip, softwareupdate, web) launches one bash
phase script and streams its output. This module is the single place that
does it, so a hung script can never wedge a run:

* the deadline is **wall-clock** -- it fires even when the child prints nothing
  (the previous per-line ``readline`` loop never woke up for a silent child);
* the child runs in its **own session / process group**
  (``start_new_session=True``) and a timeout signals the whole group
  (``os.killpg``: SIGTERM, ``kill_grace`` seconds, then SIGKILL), so a
  TERM-ignoring child and its grandchildren die with it;
* stdin is ``/dev/null`` -- a TTY-less dashboard run must never block on input.

Interface for managers::

    completed = run_streaming(argv, log_path, timeout, env=env)
        # -> subprocess.CompletedProcess (stdout = merged stdout+stderr)
        # raises subprocess.TimeoutExpired (after the group is dead)
        # raises OSError if the spawn fails

    sc = salvage_timeout_sidecar(sidecar_path, message)
        # after a TimeoutExpired: the phase script may still have written a
        # (partial) sidecar (its SIGTERM trap finalises it). Returns it with
        # status forced to FAILED plus a PHASE_TIMEOUT message, or None when
        # there is nothing to salvage (caller raises ManagerError as before).

The bash-side twin is ``ascendo_run_with_timeout`` in ``lib/ascendo_proc.sh``.
"""
from __future__ import annotations

import logging
import os
import queue
import signal
import subprocess
import threading
import time
from pathlib import Path

from ascendo.models.result import Message, MessageLevel
from ascendo.models.run import PhaseStatus
from ascendo.models.sidecar import Sidecar
from ascendo.orchestrator.sidecar_io import (
    SidecarIOError,
    SidecarReadError,
    read_sidecar,
)

_log = logging.getLogger(__name__)

DEFAULT_KILL_GRACE = 5.0
_TICK = 1.0          # max seconds between deadline checks while waiting for output
_PIPE_DRAIN = 1.0    # seconds to let the reader flush after the child exited


def _signal_group(proc: subprocess.Popen[str], sig: int) -> None:
    """Send ``sig`` to the child's process group; fall back to the child pid.

    The child is a session leader (``start_new_session``) so ``pgid == pid``.
    ``PermissionError`` happens when the group holds a root-owned process
    (``sudo``); we then signal only the pid we own.
    """
    try:
        os.killpg(proc.pid, sig)
        return
    except ProcessLookupError:
        return
    except PermissionError:
        pass
    try:
        proc.send_signal(sig)
    except (ProcessLookupError, PermissionError):
        pass


def _kill_group(proc: subprocess.Popen[str], grace: float) -> None:
    """SIGTERM the group, wait ``grace`` for the leader, then SIGKILL the group.

    The SIGKILL is unconditional: it also reaps a grandchild that outlived a
    leader that exited on SIGTERM. (A pgid stays reserved while any member is
    alive, so the pid cannot have been recycled for an unrelated group.)
    """
    _signal_group(proc, signal.SIGTERM)
    try:
        proc.wait(timeout=max(0.0, grace))
    except subprocess.TimeoutExpired:
        pass
    _signal_group(proc, signal.SIGKILL)
    try:
        proc.wait(timeout=5.0)
    except subprocess.TimeoutExpired:  # pragma: no cover - unkillable (D state)
        _log.warning("child pid=%s did not die after SIGKILL", getattr(proc, "pid", "?"))


def run_streaming(
    argv: list[str],
    log_path: Path,
    timeout: float,
    *,
    env: dict[str, str] | None = None,
    kill_grace: float = DEFAULT_KILL_GRACE,
) -> subprocess.CompletedProcess[str]:
    """Run ``argv``, tee its merged output to ``log_path``, enforce ``timeout``.

    Returns a ``CompletedProcess`` whose ``stdout`` holds the merged
    stdout+stderr. On deadline breach the child's whole process group is
    killed and ``subprocess.TimeoutExpired`` is raised (``.output`` carries
    what was captured so far).
    """
    proc = subprocess.Popen(  # noqa: S603 (argv list, no shell)
        argv,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        env=env,
        start_new_session=True,
    )
    lines: queue.Queue[str | None] = queue.Queue()

    def _reader() -> None:
        try:
            for line in iter(proc.stdout.readline, ""):
                lines.put(line)
        except (OSError, ValueError):
            pass
        finally:
            lines.put(None)  # EOF sentinel

    reader = threading.Thread(target=_reader, name="ascendo-proc-reader", daemon=True)
    reader.start()

    captured: list[str] = []
    deadline = time.monotonic() + timeout
    timed_out = False

    def _take(item: str, fh) -> None:
        captured.append(item)
        try:
            fh.write(item)
            fh.flush()
        except OSError:
            pass

    with log_path.open("a", encoding="utf-8") as fh:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                break
            try:
                item = lines.get(timeout=min(_TICK, remaining))
            except queue.Empty:
                # Silent so far. If the child already exited but a grandchild
                # keeps the pipe open, stop waiting for EOF and drain.
                if proc.poll() is not None:
                    reader.join(timeout=_PIPE_DRAIN)
                    while True:
                        try:
                            item = lines.get_nowait()
                        except queue.Empty:
                            break
                        if item is not None:
                            _take(item, fh)
                    break
                continue
            if item is None:
                break
            _take(item, fh)

        rc: int | None = None
        if not timed_out:
            try:
                rc = proc.wait(timeout=max(0.1, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                timed_out = True

        if timed_out:
            _kill_group(proc, kill_grace)
            reader.join(timeout=2.0)
            while True:  # keep whatever the child managed to print
                try:
                    item = lines.get_nowait()
                except queue.Empty:
                    break
                if item is not None:
                    _take(item, fh)

    if not reader.is_alive():
        try:
            proc.stdout.close()
        except (OSError, ValueError):
            pass

    if timed_out:
        raise subprocess.TimeoutExpired(argv, timeout, output="".join(captured))
    return subprocess.CompletedProcess(
        args=argv, returncode=rc, stdout="".join(captured), stderr="",
    )


def salvage_timeout_sidecar(sidecar_path: Path, message: str) -> Sidecar | None:
    """After a phase timeout, return the sidecar the script left behind.

    The script's SIGTERM trap normally finalises a sidecar even when killed
    mid-phase. It is returned with ``status=failed``, ``summary.exit_code=124``
    and a ``PHASE_TIMEOUT`` error message so a timeout is never reported as
    success and the partial per-item results are not lost. ``None`` when no
    readable sidecar exists.
    """
    if not sidecar_path.exists():
        return None
    try:
        sc = read_sidecar(sidecar_path)
    except (SidecarReadError, SidecarIOError) as exc:
        _log.warning("timeout salvage: unreadable sidecar %s: %s", sidecar_path, exc)
        return None
    note = Message(
        level=MessageLevel.ERROR,
        text=message[:4096],
        code="PHASE_TIMEOUT",
    )
    return sc.model_copy(update={
        "status": PhaseStatus.FAILED,
        "summary": sc.summary.model_copy(update={"exit_code": 124}),
        "messages": [*sc.messages, note],
    })
