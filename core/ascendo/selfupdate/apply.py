"""Run the in-app core upgrade as a background job with streamed log.

For a git install we shell out to the repo's own ``update.sh`` (POSIX)
or ``update.ps1`` (Windows) — the exact same upgrade path as the public
one-liner, so there's one implementation to maintain. The script does
``git pull --ff-only`` + editable pip reinstall + ``ascendo doctor``.

Jobs run in a daemon thread; the dashboard polls ``/api/updates/status``
for live log + final state. A standalone (non-git) install can't pull,
so :func:`start_update` refuses and the caller surfaces a download link.
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from .detect import InstallInfo, detect_install

_log = logging.getLogger(__name__)

__all__ = ["start_update", "get_job", "Job", "UpdateNotSupported"]


class UpdateNotSupported(RuntimeError):
    """Raised when the current install can't self-update (e.g. packaged)."""


@dataclass
class Job:
    id: str
    state: str = "pending"          # pending | running | success | error
    returncode: int | None = None
    log: list[str] = field(default_factory=list)
    version_before: str | None = None
    version_after: str | None = None
    error: str | None = None
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None

    def to_dict(self, tail: int = 400) -> dict:
        return {
            "id": self.id,
            "state": self.state,
            "returncode": self.returncode,
            "log": self.log[-tail:],
            "log_lines": len(self.log),
            "version_before": self.version_before,
            "version_after": self.version_after,
            "error": self.error,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


_JOBS: dict[str, Job] = {}
_LOCK = threading.Lock()
_VERSION_RE = re.compile(r'__version__\s*=\s*["\']([^"\']+)["\']')


def get_job(job_id: str) -> Job | None:
    with _LOCK:
        return _JOBS.get(job_id)


def _read_installed_version(install_dir: Path) -> str | None:
    vf = install_dir / "core" / "ascendo" / "__version__.py"
    try:
        m = _VERSION_RE.search(vf.read_text(encoding="utf-8"))
        return m.group(1) if m else None
    except OSError:
        return None


def _build_command(info: InstallInfo) -> list[str]:
    updater = info.updater
    if not updater:
        raise UpdateNotSupported("no updater script found for this install")
    if info.os == "windows":
        return [
            "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
            "-File", updater,
        ]
    return ["bash", updater]


def _run(job: Job, info: InstallInfo) -> None:
    install_dir = Path(info.install_dir)
    env = dict(os.environ)
    env.setdefault("ASCENDO_HOME", str(install_dir))
    env["ASCENDO_NONINTERACTIVE"] = "1"

    try:
        cmd = _build_command(info)
    except UpdateNotSupported as exc:
        job.state = "error"
        job.error = str(exc)
        job.finished_at = time.time()
        return

    job.version_before = _read_installed_version(install_dir) or job.version_before
    job.state = "running"
    job.log.append(f"$ {' '.join(cmd)}")

    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(install_dir),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
    except OSError as exc:
        job.state = "error"
        job.error = f"could not launch updater: {exc}"
        job.finished_at = time.time()
        return

    assert proc.stdout is not None
    for line in proc.stdout:
        job.log.append(line.rstrip("\n"))
        if len(job.log) > 5000:  # bound memory on a runaway updater
            del job.log[:1000]
    proc.wait()
    job.returncode = proc.returncode
    job.version_after = _read_installed_version(install_dir)
    if proc.returncode == 0:
        job.state = "success"
    else:
        job.state = "error"
        job.error = f"updater exited with code {proc.returncode}"
    job.finished_at = time.time()


def start_update(info: InstallInfo | None = None) -> Job:
    """Kick off an upgrade in a background thread; return the Job handle.

    Raises :class:`UpdateNotSupported` if this install can't self-update.
    """
    info = info or detect_install()
    if info.method != "git" or not info.updater:
        raise UpdateNotSupported(
            "this install cannot update itself in-app (no git checkout / updater). "
            "Download the latest installer instead."
        )

    job = Job(id=uuid.uuid4().hex[:12])
    from .. import __version__

    job.version_before = __version__
    with _LOCK:
        _JOBS[job.id] = job

    thread = threading.Thread(target=_run, args=(job, info), daemon=True, name=f"ascendo-update-{job.id}")
    thread.start()
    return job
