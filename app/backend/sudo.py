"""Sudo password management for the dashboard.

Background: when the dashboard launches `update-all.sh` via subprocess, sudo's
TTY-tickets timestamp does NOT reliably propagate from `sudo -v` warmed in
the parent process to subprocesses launched without a controlling TTY.
Relying on the sudo cache is therefore unreliable across the dashboard →
update-all.sh → phase-script chain — confirmed by users hitting:

    sudo cache empty and no terminal/askpass available

even right after a successful `/sudo/auth` POST.

The robust solution is **SUDO_ASKPASS**: we accept the password via HTTP body
once, hold it in memory for the duration of the dashboard process, and
generate an ephemeral askpass helper script that prints it on demand.
update-all.sh receives ``SUDO_ASKPASS`` in env and uses ``sudo -A``; all
sub-sudos in the run inherit the env automatically.

Lifecycle:
  POST /sudo/auth           store password in memory + verify via `sudo -S -v`
  POST /runs (mutating)     create askpass helper, pass SUDO_ASKPASS to child
  child exits               unlink askpass helper (NOT the password — it
                            stays for the next run within the session)
  POST /sudo/invalidate     wipe both the password and any helper file

Security caveats:
  - Backend binds to 127.0.0.1 only; password never leaves the host.
  - Held in memory only between /sudo/auth and /sudo/invalidate. Not in
    DB, not in logs, not on disk except as a 0700 askpass helper limited
    to the calling user's $XDG_RUNTIME_DIR.
  - On hard crash an askpass file may linger; check
    $XDG_RUNTIME_DIR/ascendo/.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path


@dataclass
class SudoStatus:
    cached: bool        # password is held in memory and was verified
    detail: str = ""


# In-memory password store. Single-threaded access via _lock.
_lock = threading.Lock()
_password: str | None = None
_askpass_path: Path | None = None


def have_password() -> bool:
    with _lock:
        return _password is not None


def status() -> SudoStatus:
    """Whether the dashboard currently holds a verified sudo password."""
    with _lock:
        if _password is not None:
            return SudoStatus(cached=True, detail="password held in memory")
    return SudoStatus(cached=False, detail="no password stored — call POST /sudo/auth")


def store(password: str, *, verify: bool = True, timeout: int = 15) -> tuple[bool, str]:
    """Verify ``password`` via ``sudo -S -v`` and stash it in memory.

    Returns (ok, detail). On failure nothing is stored.
    """
    if not password:
        return False, "empty password"
    if verify:
        try:
            res = subprocess.run(
                ["sudo", "-S", "-p", "", "-v"],
                input=password + "\n",
                capture_output=True, text=True, timeout=timeout,
            )
            if res.returncode != 0:
                err = (res.stderr or res.stdout).strip()
                return False, err[:300] or f"sudo -S -v exited {res.returncode}"
        except subprocess.TimeoutExpired:
            return False, "sudo -S -v timed out"
        except FileNotFoundError:
            return False, "sudo not installed"

    global _password
    with _lock:
        _password = password
    return True, "password verified and stored in memory"


def invalidate() -> bool:
    """Wipe the in-memory password and any active askpass helper."""
    global _password
    with _lock:
        _password = None
    cleanup_askpass()
    try:
        subprocess.run(["sudo", "-k"], capture_output=True, timeout=5)
    except Exception:
        pass
    return True


def _askpass_dir() -> Path:
    base = os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()
    p = Path(base) / "ascendo"
    p.mkdir(parents=True, exist_ok=True)
    try:
        p.chmod(0o700)
    except Exception:
        pass
    return p


def make_askpass(*, password: str | None = None) -> Path:
    """Create a 0700 askpass shell script that prints the stored password.

    The password is embedded as a single-quoted shell literal — no escapes
    are needed except literal apostrophes. The file is unlinked by
    :func:`cleanup_askpass`.
    """
    global _askpass_path
    pw = password
    if pw is None:
        with _lock:
            pw = _password
    if pw is None:
        raise RuntimeError("no password stored; call POST /sudo/auth first")

    quoted = "'" + pw.replace("'", "'\\''") + "'"
    body = "#!/usr/bin/env bash\nprintf '%s\\n' " + quoted + "\n"

    fd, path = tempfile.mkstemp(prefix="askpass-", suffix=".sh",
                                dir=str(_askpass_dir()))
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(body)
        os.chmod(path, 0o700)
    except Exception:
        try:
            os.unlink(path)
        except Exception:
            pass
        raise
    _askpass_path = Path(path)
    return _askpass_path


def cleanup_askpass() -> None:
    """Unlink the askpass helper if any. Idempotent."""
    global _askpass_path
    p = _askpass_path
    _askpass_path = None
    if p is None:
        return
    try:
        if p.exists():
            p.unlink()
    except Exception:
        pass
