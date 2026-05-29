"""LinuxElevation — concrete IElevation impl for Ubuntu / Debian.

Holds an in-memory sudo password and exposes it to child sudo processes
via the static SUDO_ASKPASS helper script at
``adapters/ubuntu/lib/askpass_helper.sh``. The helper reads the password
from the per-spawn environment variable ``_ASCENDO_SUDO_PW`` which
LinuxElevation injects only for Phase.APPLY child processes.

Mirrors the macOS adapter's MacElevation API surface so the existing
``/elevation/auth`` and ``/elevation/status`` dashboard endpoints work
without modification (the duck-type check in
``core/ascendo/dashboard/routes/elevation.py`` looks for
``has_password_registered`` / ``register_password`` / ``invalidate``).

Security model (T4 mitigation per ADR-0005):
  - argv-only contract — shell strings are rejected with TypeError.
  - Allow-list enforced — only registered basenames may run elevated.
  - Empty argv rejected.
  - Allow-list normalised to lowercase basenames.

TODO(M6): Polkit (pkexec) integration as an alternative to sudo for
desktops where sudo is not configured. Current scope: the operator has
working passwordless sudo via askpass which is sufficient for the
dashboard apply flow.
"""
from __future__ import annotations

import atexit
import logging
import os
import shutil
import subprocess
import threading
import time
from collections.abc import Iterable, Sequence
from pathlib import Path

from ascendo.interfaces.elevation import (
    ElevationDenied,
    ElevationResult,
    ElevationTimeout,
    IElevation,
)
from ascendo.models.host import ElevationMethod, HostInfo, OperatingSystem

_log = logging.getLogger(__name__)

_DEFAULT_TIMEOUT_SEC = 1800
_VERIFY_TIMEOUT_SEC = 15


class LinuxElevation(IElevation):
    """sudo-driven elevation with optional in-memory password cache.

    Lifecycle:
        register_password(pw)   verify via ``sudo -S -k -p '' -v`` then
                                store the password in process memory.
        run(host, argv, ...)    sudo -A argv (uses SUDO_ASKPASS + env
                                var) when a password is cached, else
                                sudo argv (TTY prompt fallback).
        invalidate()            wipe in-memory password + ``sudo -k``.

    Args:
        askpass_helper: Path to the static askpass shell script (typically
            ``adapters/ubuntu/lib/askpass_helper.sh``). Must exist and be
            executable.
    """

    def __init__(self, *, askpass_helper: Path | str) -> None:
        self._askpass_helper = Path(askpass_helper)
        self._lock = threading.Lock()
        self._password: str | None = None
        self._allowlist: frozenset[str] = frozenset()
        atexit.register(self._cleanup_at_exit)

    # ── IElevation surface ────────────────────────────────────────────────

    @property
    def available_methods(self) -> tuple[ElevationMethod, ...]:
        return (ElevationMethod.SUDO,) if shutil.which("sudo") else ()

    def is_currently_elevated(self, host: HostInfo) -> bool:
        try:
            return os.geteuid() == 0  # type: ignore[attr-defined]
        except AttributeError:
            return False

    def register_allowlist(self, allowed_commands: Iterable[str]) -> None:
        """Normalise and store the allow-list as lowercase basenames."""
        self._allowlist = frozenset(
            Path(c).name.lower() for c in allowed_commands if c
        )

    def run(
        self,
        host: HostInfo,
        argv: Sequence[str],
        *,
        timeout_sec: int | None = None,
        env: dict[str, str] | None = None,
        cwd: str | None = None,
        method: ElevationMethod | None = None,
    ) -> ElevationResult:
        if isinstance(argv, str):
            raise TypeError("argv must be Sequence[str], not a shell string")
        if not argv:
            raise ElevationDenied("argv is empty")
        head = Path(argv[0]).name.lower()
        if head not in self._allowlist:
            raise ElevationDenied(
                f"command {head!r} not in allow-list "
                f"{sorted(self._allowlist)!r}"
            )
            
        # P3/P6: Resolve elevated argv[0] and compare to expected basename resolution
        resolved_argv0 = shutil.which(argv[0])
        resolved_expected = shutil.which(head)
        if not resolved_argv0 or resolved_argv0 != resolved_expected:
            raise ElevationDenied(
                f"command path spoofing detected: {argv[0]!r} resolves to {resolved_argv0!r}, "
                f"expected {resolved_expected!r}"
            )
            
        if shutil.which("sudo") is None:
            raise ElevationDenied("sudo not on PATH")

        full_env = dict(os.environ)
        if env:
            full_env.update(env)

        with self._lock:
            password = self._password
        if password is not None and self._askpass_helper.is_file():
            full_env["SUDO_ASKPASS"] = str(self._askpass_helper)
            full_env["_ASCENDO_SUDO_PW"] = password
            sudo_argv = ["sudo", "-A", *argv]
        else:
            sudo_argv = ["sudo", *argv]

        timeout = timeout_sec if timeout_sec is not None else _DEFAULT_TIMEOUT_SEC
        started = time.monotonic()
        try:
            res = subprocess.run(  # noqa: S603
                sudo_argv,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=full_env,
                cwd=cwd,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise ElevationTimeout(
                f"sudo {argv[0]} exceeded {timeout}s"
            ) from exc
        duration_ms = int((time.monotonic() - started) * 1000)
        return ElevationResult(
            exit_code=res.returncode,
            stdout=res.stdout or "",
            stderr=res.stderr or "",
            method=ElevationMethod.SUDO,
            duration_ms=duration_ms,
        )

    # ── Concrete-only surface (extra; consumed by dashboard router) ───────

    def register_password(
        self,
        password: str,
        *,
        verify: bool = True,
        timeout: int = _VERIFY_TIMEOUT_SEC,
    ) -> tuple[bool, str]:
        """Validate the password via ``sudo -S -k -p '' -v`` and cache it.

        The ``-k`` flag invalidates any cached sudo timestamp first so
        the verify call genuinely tests the password rather than hitting
        a still-warm timestamp from an earlier sudo. On success the
        password is stored in process memory; subsequent ``run()`` calls
        wire it through ``SUDO_ASKPASS`` + the ephemeral
        ``_ASCENDO_SUDO_PW`` env var.

        Returns:
            ``(True, "<info>")`` on success, ``(False, "<reason>")`` on
            authentication failure or transport error. Reasons are
            sanitised to ≤ 300 chars (the dashboard router relays them
            verbatim into HTTP 401 bodies).
        """
        if not password:
            return False, "empty password"
        if verify:
            if shutil.which("sudo") is None:
                return False, "sudo not installed"
            try:
                res = subprocess.run(  # noqa: S603, S607
                    ["sudo", "-S", "-k", "-p", "", "-v"],
                    input=password + "\n",
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                )
            except subprocess.TimeoutExpired:
                return False, "sudo -S -v timed out"
            except FileNotFoundError:
                return False, "sudo not installed"
            if res.returncode != 0:
                err = (res.stderr or res.stdout).strip()
                return False, err[:300] or f"sudo -S -v exited {res.returncode}"
        with self._lock:
            self._password = password
        return True, "password verified and stored"

    def verify_password(
        self,
        password: str,
        *,
        timeout: int = _VERIFY_TIMEOUT_SEC,
    ) -> bool:
        """Run ``sudo -S -k -v`` to validate ``password`` without caching.

        Returns True iff sudo accepts the password. Does NOT mutate the
        in-memory cache. The ``-k`` flag wipes any prior sudo timestamp
        so the verify call genuinely tests the password.
        """
        if not password or shutil.which("sudo") is None:
            return False
        try:
            res = subprocess.run(  # noqa: S603, S607
                ["sudo", "-S", "-k", "-p", "", "-v"],
                input=password + "\n",
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            return False
        return res.returncode == 0

    def clear_password(self) -> None:
        """Wipe the in-memory password (does NOT call ``sudo -k``)."""
        with self._lock:
            self._password = None

    def invalidate(self) -> None:
        """Wipe in-memory password + run ``sudo -k`` to clear the OS timestamp."""
        with self._lock:
            self._password = None
        try:
            subprocess.run(  # noqa: S603, S607
                ["sudo", "-k"],
                capture_output=True,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass

    def has_password_registered(self) -> bool:
        with self._lock:
            return self._password is not None

    # Mirror of MacElevation.has_password_registered for symmetry with
    # callers that prefer a more conventional name.
    def is_authenticated(self) -> bool:
        return self.has_password_registered()

    def askpass_path(self) -> Path | None:
        """Return the static askpass helper path iff a password is cached.

        Returns None when no password is registered (so callers don't
        wire SUDO_ASKPASS pointlessly) or when the helper file is
        missing on disk.
        """
        with self._lock:
            has_pw = self._password is not None
        if not has_pw:
            return None
        if not self._askpass_helper.is_file():
            return None
        return self._askpass_helper

    # ── Apply-env injection (consumed by per-manager _build_env) ──────────

    def build_subprocess_env(self) -> dict[str, str]:
        """Return ``os.environ`` extended with SUDO_ASKPASS + password.

        Used by per-manager ``_build_env(phase)`` overrides for
        ``Phase.APPLY``. When no password is registered (or the helper
        is missing) the caller falls back to a vanilla
        ``dict(os.environ)`` and sudo prompts the controlling TTY (or
        fails fast in headless contexts).
        """
        env = dict(os.environ)
        with self._lock:
            password = self._password
        if password is not None and self._askpass_helper.is_file():
            env["SUDO_ASKPASS"] = str(self._askpass_helper)
            env["_ASCENDO_SUDO_PW"] = password
        return env

    # ── Availability ──────────────────────────────────────────────────────

    def is_available(self, host: HostInfo) -> bool:
        """True iff host is Linux AND sudo is on PATH."""
        if not isinstance(host, HostInfo):
            return False
        if not host.os.value.startswith("linux_"):
            return False
        return shutil.which("sudo") is not None

    # ── Internals ─────────────────────────────────────────────────────────

    def _cleanup_at_exit(self) -> None:
        with self._lock:
            self._password = None
