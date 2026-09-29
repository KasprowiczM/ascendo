"""Shared bounded process runner for the macOS package managers.

Every manager (brew, mas, npm, pip, softwareupdate, web) launches one bash
phase script and streams its output. This module is the single place that
does it, so a hung script can never wedge a run:

* the deadline is **wall-clock** -- it fires even when the child prints nothing
  (the previous per-line ``readline`` loop never woke up for a silent child);
* stdin is ``/dev/null`` -- a TTY-less dashboard run must never block on input;
* the child never outlives us: on a timeout **or any abnormal exit of the
  runner** (``KeyboardInterrupt``, any ``BaseException``) the child and its
  descendants are killed (SIGTERM, ``kill_grace`` seconds, SIGKILL).

Two spawn modes (chosen per call by :func:`_isolate_child`):

* **isolated** (parent has *no* controlling terminal -- dashboard, Tauri,
  launchd): the child is a new session leader (``start_new_session=True``) and
  the whole process group is signalled with ``os.killpg``.
* **shared** (parent *has* a controlling terminal -- ``ascendo run`` in a
  shell): the child stays in the parent's session/process group, exactly as
  before, so the ``/dev/tty`` sudo password fallback keeps working, Ctrl-C
  reaches the child and there is no SIGTTIN. The kill path then walks the
  child's descendants (``pgrep -P``, recursively) and signals each pid.

  Detection: ``os.open("/dev/tty")`` succeeds only when the process has a
  controlling terminal -- precisely the condition the ``/dev/tty`` fallback
  needs. Test seam: ``ASCENDO_PROC_ISOLATE=1`` forces isolated, ``=0`` forces
  shared; unset/other = auto-detect.

Interface for managers::

    completed = run_streaming(argv, log_path, timeout, env=env)
        # -> subprocess.CompletedProcess (stdout = merged stdout+stderr)
        # raises subprocess.TimeoutExpired (after the child tree is dead)
        # raises OSError if the spawn fails

    sc = handle_phase_timeout(output_dir=..., run_id=..., phase=...,
                              category="brew", message=msg, exc=exc)
        # after a TimeoutExpired: returns the (partial) sidecar the script
        # left behind as FAILED + PHASE_TIMEOUT message, or raises
        # ManagerError(msg) when there is nothing to salvage.

The bash-side twin is ``ascendo_run_with_timeout`` in ``lib/ascendo_proc.sh``.
"""
from __future__ import annotations

import codecs
import logging
import os
import queue
import select
import signal
import subprocess
import threading
import time
from pathlib import Path
from uuid import UUID

from ascendo.interfaces.package_manager import ManagerError
from ascendo.models.result import Message, MessageLevel
from ascendo.models.run import Phase, PhaseStatus
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
_SELECT_TICK = 0.25  # reader wake-up interval (lets it notice the stop flag)


def _isolate_child() -> bool:
    """True -> spawn the child in its own session (parent has no controlling tty)."""
    forced = os.environ.get("ASCENDO_PROC_ISOLATE")
    if forced in ("0", "1"):
        return forced == "1"
    try:
        fd = os.open("/dev/tty", os.O_RDWR | os.O_NOCTTY)
    except OSError:
        return True
    os.close(fd)
    return False


# -- signalling ------------------------------------------------------------

def _signal_pid(pid: int, sig: int) -> None:
    try:
        os.kill(pid, sig)
    except (ProcessLookupError, PermissionError):
        pass


def _signal_group(proc: subprocess.Popen, sig: int) -> None:
    """Signal the child's process group (child is a session leader: pgid == pid).

    ``PermissionError`` happens when the group holds a root-owned process
    (``sudo``); then only the pid we own is signalled.
    """
    try:
        os.killpg(proc.pid, sig)
        return
    except ProcessLookupError:
        return
    except PermissionError:
        pass
    _signal_pid(proc.pid, sig)


def _children(pid: int) -> list[int]:
    try:
        out = subprocess.run(  # noqa: S603,S607 (fixed argv)
            ["pgrep", "-P", str(pid)], capture_output=True, text=True, timeout=5, check=False,
        ).stdout
    except (OSError, subprocess.TimeoutExpired):
        return []
    return [int(x) for x in out.split() if x.isdigit()]


def _descendants(pid: int) -> list[int]:
    """All descendants of ``pid`` (recursive ``pgrep -P``), children before grandchildren."""
    found: list[int] = []
    queue_: list[int] = [pid]
    while queue_:
        for child in _children(queue_.pop(0)):
            if child not in found:
                found.append(child)
                queue_.append(child)
    return found


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _kill(proc: subprocess.Popen, isolated: bool, grace: float) -> None:
    """SIGTERM the child (tree), wait ``grace`` for it, then SIGKILL what is left.

    Isolated: the whole process group. Shared: the descendants snapshotted
    *before* signalling (once the child dies its children are re-parented and
    no longer discoverable) plus the child itself. The final SIGKILL is
    unconditional so grandchildren that outlive a child exiting on SIGTERM are
    reaped; a pid/pgid cannot be recycled while a member is alive, and the
    residual window for the snapshot pids is microseconds.
    """
    if isolated:
        _signal_group(proc, signal.SIGTERM)
        victims: list[int] = []
    else:
        victims = _descendants(proc.pid)
        for pid in victims:
            _signal_pid(pid, signal.SIGTERM)
        _signal_pid(proc.pid, signal.SIGTERM)
    try:
        proc.wait(timeout=max(0.0, grace))
    except subprocess.TimeoutExpired:
        pass
    if isolated:
        _signal_group(proc, signal.SIGKILL)
    else:
        for pid in victims + _descendants(proc.pid):
            if _pid_alive(pid):
                _signal_pid(pid, signal.SIGKILL)
        _signal_pid(proc.pid, signal.SIGKILL)
    try:
        proc.wait(timeout=5.0)
    except subprocess.TimeoutExpired:  # pragma: no cover - unkillable (D state)
        _log.warning("child pid=%s did not die after SIGKILL", getattr(proc, "pid", "?"))


def _hard_kill(proc: subprocess.Popen, isolated: bool) -> None:
    """Last resort when :func:`_kill` itself was interrupted: SIGKILL, no waiting."""
    if isolated:
        _signal_group(proc, signal.SIGKILL)
    else:
        for pid in _descendants(proc.pid):
            _signal_pid(pid, signal.SIGKILL)
    _signal_pid(proc.pid, signal.SIGKILL)


# -- output reader ----------------------------------------------------------

def _start_reader(
    fd: int, out: "queue.Queue[str | None]", stop: threading.Event,
) -> threading.Thread:
    """Reader thread: ``select`` + ``os.read`` on ``fd``, pushes complete lines.

    Unlike a blocking ``readline`` it wakes every ``_SELECT_TICK`` to notice
    ``stop`` (a grandchild holding the pipe open can no longer pin the thread,
    so ``proc.stdout`` can be closed safely). Lines end at ``\n``, ``\r`` or
    ``\r\n`` (normalised to ``\n``, like text-mode universal newlines). Ends
    with a ``None`` sentinel.
    """
    def _emit(buf: str, final: bool) -> str:
        while True:
            idx = -1
            for i, ch in enumerate(buf):
                if ch in "\r\n":
                    idx = i
                    break
            if idx < 0:
                return buf
            if buf[idx] == "\r":
                if idx + 1 == len(buf) and not final:
                    return buf  # maybe half of "\r\n"; wait for more data
                end = idx + 2 if buf[idx + 1:idx + 2] == "\n" else idx + 1
            else:
                end = idx + 1
            out.put(buf[:idx] + "\n")
            buf = buf[end:]

    def _run() -> None:
        dec = codecs.getincrementaldecoder("utf-8")(errors="replace")
        buf = ""
        try:
            def _read_ready(timeout: float) -> bool | None:
                """True: data consumed; False: nothing ready; None: EOF/error."""
                nonlocal buf
                ready, _, _ = select.select([fd], [], [], timeout)
                if not ready:
                    return False
                data = os.read(fd, 65536)
                if not data:
                    return None
                buf = _emit(buf + dec.decode(data), False)
                return True

            while not stop.is_set():
                if _read_ready(_SELECT_TICK) is None:
                    break
            else:
                # stop requested: take whatever is already in the pipe
                while _read_ready(0) is True:
                    pass
        except (OSError, ValueError):
            pass
        finally:
            buf = _emit(buf + dec.decode(b"", final=True), True)
            if buf:
                out.put(buf)
            out.put(None)  # EOF sentinel

    reader = threading.Thread(target=_run, name="ascendo-proc-reader", daemon=True)
    reader.start()
    return reader


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
    stdout+stderr. On deadline breach the child tree is killed and
    ``subprocess.TimeoutExpired`` is raised (``.output`` carries what was
    captured so far). If this function exits for *any* other reason after the
    child was spawned (``KeyboardInterrupt``, an unexpected exception) the
    child tree is killed too and the exception re-raised -- no orphans.
    """
    isolated = _isolate_child()
    captured: list[str] = []
    timed_out = False
    rc: int | None = None

    # Open the log BEFORE spawning: if it fails there is no child to clean up.
    with log_path.open("a", encoding="utf-8") as fh:
        proc = subprocess.Popen(  # noqa: S603 (argv list, no shell)
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=0,
            env=env,
            start_new_session=isolated,
        )
        lines: queue.Queue[str | None] = queue.Queue()
        stop = threading.Event()
        reader: threading.Thread | None = None

        def _take(item: str) -> None:
            captured.append(item)
            try:
                fh.write(item)
                fh.flush()
            except OSError:
                pass

        def _drain_queue() -> None:
            while True:
                try:
                    item = lines.get_nowait()
                except queue.Empty:
                    return
                if item is not None:
                    _take(item)

        try:
            reader = _start_reader(proc.stdout.fileno(), lines, stop)
            deadline = time.monotonic() + timeout
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    timed_out = True
                    break
                try:
                    item = lines.get(timeout=min(_TICK, remaining))
                except queue.Empty:
                    # Silent so far. If the child already exited but a
                    # grandchild keeps the pipe open, stop waiting for EOF.
                    if proc.poll() is not None:
                        stop.set()
                        reader.join(timeout=_PIPE_DRAIN)
                        _drain_queue()
                        break
                    continue
                if item is None:
                    break
                _take(item)

            if not timed_out:
                try:
                    rc = proc.wait(timeout=max(0.1, deadline - time.monotonic()))
                except subprocess.TimeoutExpired:
                    timed_out = True
            if timed_out:
                _kill(proc, isolated, kill_grace)
        except BaseException:
            # Never leave the child running when we are unwinding.
            try:
                _kill(proc, isolated, kill_grace)
            except BaseException:  # noqa: BLE001 - interrupted while killing
                _hard_kill(proc, isolated)
            raise
        finally:
            stop.set()
            if reader is not None:
                reader.join(timeout=2.0)
                _drain_queue()
                if reader.is_alive():  # pragma: no cover - select tick is 0.25 s
                    _log.warning("output reader did not stop; leaving pipe open")
                    reader = None
            if reader is not None:
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


def handle_phase_timeout(
    *,
    output_dir: Path,
    run_id: UUID,
    phase: Phase,
    category: str,
    message: str,
    exc: BaseException,
) -> Sidecar:
    """Shared ``except subprocess.TimeoutExpired`` body for every manager.

    Returns the salvaged sidecar at ``<output_dir>/<run_id>/<phase>__<category>.json``
    (FAILED + ``PHASE_TIMEOUT``), or raises ``ManagerError(message)`` chained
    to ``exc`` when the script left nothing readable behind.
    """
    salvaged = salvage_timeout_sidecar(
        Path(output_dir) / str(run_id) / f"{phase.value}__{category}.json", message,
    )
    if salvaged is None:
        raise ManagerError(message) from exc
    return salvaged
