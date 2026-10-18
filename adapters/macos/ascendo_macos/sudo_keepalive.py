"""One sudo keep-alive per interactive macOS apply run (Spec 7b).

A Touch-ID / password-primed sudo timestamp lapses after ~5 minutes, while a
full run (a long ``mas``/``softwareupdate`` pass) can take hours. This daemon
thread refreshes it with ``sudo -n -v`` -- *never* ``sudo -v``, which would
prompt -- every ``ASCENDO_SUDO_KEEPALIVE_INTERVAL`` seconds (default 50) and
exits on the first non-zero rc (the cache is gone; the apply scripts' own gate
then defers softly), on ``stop()``, or when the context manager exits.

Not used for scheduled/unattended runs, dry runs, read-only runs, or when an
askpass helper / dashboard password is wired (``sudo -A`` needs no keep-alive).
"""
from __future__ import annotations

import logging
import os
import subprocess
import threading

_log = logging.getLogger(__name__)

INTERVAL_ENV = "ASCENDO_SUDO_KEEPALIVE_INTERVAL"
DEFAULT_INTERVAL = 50.0
THREAD_NAME = "ascendo-sudo-keepalive"


def _interval_from_env() -> float:
    raw = os.environ.get(INTERVAL_ENV, "").strip()
    if raw:
        try:
            value = float(raw)
        except ValueError:
            value = 0.0
        if value > 0:
            return value
        _log.warning("ignoring invalid %s=%r", INTERVAL_ENV, raw)
    return DEFAULT_INTERVAL


class SudoKeepAlive:
    """Context manager owning the refresher thread (idempotent start/stop)."""

    def __init__(self, interval: float | None = None) -> None:
        self.interval = float(interval) if interval is not None else _interval_from_env()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _loop(self) -> None:
        while not self._stop.wait(self.interval):
            try:
                rc = subprocess.run(
                    ["sudo", "-n", "-v"], stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    timeout=10, check=False,
                ).returncode
            except (OSError, subprocess.SubprocessError):
                return          # no sudo / hung sudo: nothing to keep alive
            if rc != 0:
                return          # cache lapsed; never escalate to a prompt

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name=THREAD_NAME, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=15)

    def __enter__(self) -> SudoKeepAlive:
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()
