"""MacElevation — concrete IElevation impl for macOS.

Holds an in-memory password and exposes it to child sudo processes via
an ephemeral SUDO_ASKPASS helper script. When no password is registered,
falls back to letting sudo prompt the controlling TTY.

Mirrors the proven app/backend/sudo.py (Linux dashboard) pattern; layers
the IElevation ABC + allow-list T4 guard from ADR-0005 on top.
"""
from __future__ import annotations

import atexit
import logging
import os
import shutil
import subprocess
import tempfile
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
from ascendo.models.host import ElevationMethod, HostInfo

_log = logging.getLogger(__name__)

_DEFAULT_TIMEOUT_SEC = 1800
_VERIFY_TIMEOUT_SEC = 15


class MacElevation(IElevation):
    """sudo-driven elevation with optional askpass cache.

    Lifecycle:
        register_password(pw)   verify + store + create askpass helper
        run(host, argv, ...)    sudo -A argv (uses SUDO_ASKPASS) when
                                password registered, else sudo argv
                                (TTY prompt fallback)
        invalidate()            wipe in-memory password + unlink helper
                                + sudo -k
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._password: str | None = None
        self._askpass_path: Path | None = None
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
        if self._allowlist and head not in self._allowlist:
            raise ElevationDenied(
                f"command {head!r} not in allow-list "
                f"{sorted(self._allowlist)!r}"
            )
        if shutil.which("sudo") is None:
            raise ElevationDenied("sudo not on PATH")

        full_env = dict(os.environ)
        if env:
            full_env.update(env)

        with self._lock:
            askpass = self._askpass_path
        if askpass is not None:
            full_env["SUDO_ASKPASS"] = str(askpass)
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

    # ── Concrete-only surface (extra) ─────────────────────────────────────

    def register_password(
        self, password: str, *, verify: bool = True, timeout: int = _VERIFY_TIMEOUT_SEC
    ) -> tuple[bool, str]:
        if not password:
            return False, "empty password"
        if verify:
            try:
                res = subprocess.run(  # noqa: S603, S607
                    ["sudo", "-S", "-p", "", "-v"],
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
            self._askpass_path = self._create_askpass_helper(password)
        return True, "password verified and stored"

    def invalidate(self) -> None:
        with self._lock:
            self._password = None
            old = self._askpass_path
            self._askpass_path = None
        if old is not None and old.exists():
            try:
                old.unlink()
            except OSError:
                pass
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

    def askpass_path(self) -> Path | None:
        with self._lock:
            return self._askpass_path

    # ── Internals ─────────────────────────────────────────────────────────

    def _askpass_dir(self) -> Path:
        base = Path(os.environ.get("TMPDIR") or tempfile.gettempdir())
        d = base / "ascendo"
        d.mkdir(parents=True, exist_ok=True)
        try:
            d.chmod(0o700)
        except OSError:
            pass
        return d

    def _create_askpass_helper(self, password: str) -> Path:
        # Single-quote escape rule: ' -> '\''
        quoted = "'" + password.replace("'", "'\\''") + "'"
        body = "#!/usr/bin/env bash\nprintf '%s\\n' " + quoted + "\n"
        fd, path = tempfile.mkstemp(
            prefix="askpass-", suffix=".sh", dir=str(self._askpass_dir())
        )
        try:
            with os.fdopen(fd, "w") as fh:
                fh.write(body)
            os.chmod(path, 0o700)
        except OSError:
            try:
                os.unlink(path)
            except OSError:
                pass
            raise
        return Path(path)

    def _cleanup_at_exit(self) -> None:
        with self._lock:
            old = self._askpass_path
            self._askpass_path = None
            self._password = None
        if old is not None:
            try:
                old.unlink()
            except OSError:
                pass
