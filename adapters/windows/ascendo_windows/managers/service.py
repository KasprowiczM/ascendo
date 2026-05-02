"""WindowsServiceManager — manage the AscendoDashboard Windows service.

Thin Python wrapper around ``bin\\install-service.ps1``. The PowerShell
script owns the actual NSSM/sc.exe interaction; this module's only job
is to spawn it, parse the JSON status payload, and surface a typed
result the FastAPI router can return as-is.

Why a wrapper instead of calling NSSM directly from Python?

1. NSSM's ``status`` text output is single-line and trivial to parse but
   ``edit`` / ``set`` calls cluster into one idempotent flow that's much
   cleaner to keep in PowerShell where the original install logic lives.
2. The script already handles elevation checks, NSSM bootstrap (download
   + SHA-256 verification), recovery actions via ``sc.exe failure``, and
   the health probe loop. Re-implementing that in Python would double
   the surface area without adding value.
3. The same script is invoked by the NSIS installer hook (``-Action
   install`` in elevated context) — if the dashboard router used a
   different code path, divergence would creep in.

The only hard contract this module relies on is the JSON shape emitted
by ``-Action status -Json``::

    {
      "installed":      true|false,
      "running":        true|false,
      "port_listening": true|false,
      "health":         "ok" | null | "<other>",
      "last_started":   "2026-05-02T12:34:56.7890123Z" | null,
      "pid":            <int>,
      "service_name":   "AscendoDashboard",
      "state":          "SERVICE_RUNNING" | "SERVICE_STOPPED" | "ABSENT" | ...,
      "port":           8765
    }

Tests at ``adapters/windows/tests/test_service_manager_smoke.py`` mock
``subprocess.run`` with realistic NSSM/PS outputs and assert each parser
branch.
"""
from __future__ import annotations

import json
import logging
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

_log = logging.getLogger(__name__)


class ServiceManagerError(RuntimeError):
    """Raised on any failure to communicate with the PowerShell wrapper."""


class ServiceElevationRequired(ServiceManagerError):
    """The action requires admin/elevation but the current process lacks it."""


@dataclass
class ServiceStatus:
    """Typed payload returned by :meth:`WindowsServiceManager.status`."""

    installed: bool
    running: bool
    port_listening: bool
    health: str | None
    last_started: str | None
    pid: int
    service_name: str = "AscendoDashboard"
    state: str = "ABSENT"
    port: int = 8765

    @classmethod
    def from_payload(cls, data: dict) -> ServiceStatus:
        return cls(
            installed=bool(data.get("installed", False)),
            running=bool(data.get("running", False)),
            port_listening=bool(data.get("port_listening", False)),
            health=data.get("health"),
            last_started=data.get("last_started"),
            pid=int(data.get("pid") or 0),
            service_name=str(data.get("service_name", "AscendoDashboard")),
            state=str(data.get("state", "ABSENT")),
            port=int(data.get("port") or 8765),
        )

    def to_dict(self) -> dict:
        return {
            "installed":      self.installed,
            "running":        self.running,
            "port_listening": self.port_listening,
            "health":         self.health,
            "last_started":   self.last_started,
            "pid":            self.pid,
            "service_name":   self.service_name,
            "state":          self.state,
            "port":           self.port,
        }


@dataclass
class ServiceActionResult:
    """Outcome of install/uninstall/start/stop/restart."""

    ok: bool
    exit_code: int
    output: str

    def to_dict(self) -> dict:
        return {"ok": self.ok, "exit_code": self.exit_code, "output": self.output}


class WindowsServiceManager:
    """Manage the AscendoDashboard service via ``bin\\install-service.ps1``.

    The constructor takes the repo root (the directory that contains
    ``bin/install-service.ps1``); production code resolves it via
    :func:`default_repo_root`. Tests pass a ``tmp_path`` fixture and a
    fake ``pwsh_path`` so subprocess mocking works without an actual
    PowerShell binary on PATH.
    """

    SCRIPT_REL = Path("bin") / "install-service.ps1"
    DEFAULT_TIMEOUT_SEC = 60
    INSTALL_TIMEOUT_SEC = 120  # download NSSM + start + health probe

    # Exit codes from install-service.ps1 (kept in sync with the .ps1 file).
    EXIT_OK            = 0
    EXIT_GENERIC       = 1
    EXIT_BAD_ARGS      = 2
    EXIT_NSSM_FAILED   = 10
    EXIT_NEED_ELEVATE  = 11
    EXIT_HEALTH_FAILED = 12

    def __init__(
        self,
        *,
        repo_root: Path | str,
        pwsh_path: str | None = None,
        service_name: str = "AscendoDashboard",
        port: int = 8765,
        timeout_sec: int = DEFAULT_TIMEOUT_SEC,
    ) -> None:
        self._repo_root    = Path(repo_root)
        self._pwsh_override = pwsh_path
        self._pwsh_resolved: str | None = None
        self._service_name  = service_name
        self._port          = port
        self._timeout       = timeout_sec

    # ── Public API ──────────────────────────────────────────────────

    def status(self) -> ServiceStatus:
        """Read the current service state. Always safe; no elevation."""
        result = self._invoke("status", extra_args=["-Json"], capture_json=True)
        if result.payload is None:
            # Fallback when the script could not produce JSON (e.g.
            # PowerShell missing entirely). Return a plausible "absent"
            # snapshot instead of raising — callers always want SOMETHING.
            return ServiceStatus(
                installed=False,
                running=False,
                port_listening=False,
                health=None,
                last_started=None,
                pid=0,
                service_name=self._service_name,
                state="UNKNOWN",
                port=self._port,
            )
        return ServiceStatus.from_payload(result.payload)

    def install(self) -> ServiceActionResult:
        return self._mutating_action("install", timeout=self.INSTALL_TIMEOUT_SEC)

    def uninstall(self, *, purge_logs: bool = False) -> ServiceActionResult:
        extra = ["-PurgeLogs"] if purge_logs else []
        return self._mutating_action("uninstall", extra_args=extra)

    def start(self) -> ServiceActionResult:
        return self._mutating_action("start")

    def stop(self) -> ServiceActionResult:
        return self._mutating_action("stop")

    def restart(self) -> ServiceActionResult:
        return self._mutating_action("restart")

    # ── Internals ────────────────────────────────────────────────────

    @dataclass
    class _InvokeResult:
        completed: subprocess.CompletedProcess
        payload: dict | None  # parsed JSON when capture_json + parsable

    def _mutating_action(
        self,
        action: str,
        *,
        extra_args: list[str] | None = None,
        timeout: int | None = None,
    ) -> ServiceActionResult:
        result = self._invoke(action, extra_args=extra_args, timeout=timeout)
        if result.completed.returncode == self.EXIT_NEED_ELEVATE:
            raise ServiceElevationRequired(
                f"action '{action}' requires administrator elevation",
            )
        # Concatenate stdout + stderr for the caller's benefit so they
        # see the full operator narrative ("downloading NSSM...", "SHA-256
        # verified...", etc).
        merged = (result.completed.stdout or "") + (result.completed.stderr or "")
        return ServiceActionResult(
            ok=result.completed.returncode == self.EXIT_OK,
            exit_code=result.completed.returncode,
            output=merged.strip(),
        )

    def _invoke(
        self,
        action: str,
        *,
        extra_args: list[str] | None = None,
        capture_json: bool = False,
        timeout: int | None = None,
    ) -> _InvokeResult:
        script = self._repo_root / self.SCRIPT_REL
        if not script.exists():
            raise ServiceManagerError(f"install-service.ps1 not found at {script}")

        argv: list[str] = [
            self._resolve_pwsh(),
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy", "Bypass",
            "-File", str(script),
            "-Action", action,
            "-ServiceName", self._service_name,
            "-Port", str(self._port),
        ]
        if extra_args:
            argv += list(extra_args)

        try:
            completed = subprocess.run(  # noqa: S603
                argv,
                capture_output=True,
                text=True,
                timeout=timeout or self._timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise ServiceManagerError(
                f"install-service.ps1 -Action {action} timed out after "
                f"{timeout or self._timeout}s",
            ) from exc
        except OSError as exc:
            raise ServiceManagerError(
                f"failed to spawn pwsh for install-service.ps1: {exc}",
            ) from exc

        payload: dict | None = None
        if capture_json and completed.stdout:
            payload = self._parse_json_payload(completed.stdout)
        return self._InvokeResult(completed=completed, payload=payload)

    @staticmethod
    def _parse_json_payload(stdout: str) -> dict | None:
        """Find and parse the single-line JSON document in stdout.

        ``-Action status -Json`` writes one ``ConvertTo-Json -Compress``
        line; surrounding writes (banner, prompts) should be absent
        because the status path is silent. Defensive: scan each line
        and return the first that parses to a dict.
        """
        for line in stdout.splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                return obj
        return None

    def _resolve_pwsh(self) -> str:
        if self._pwsh_resolved is not None:
            return self._pwsh_resolved
        if self._pwsh_override is not None:
            self._pwsh_resolved = self._pwsh_override
            return self._pwsh_resolved
        for candidate in ("pwsh.exe", "pwsh", "powershell.exe", "powershell"):
            found = shutil.which(candidate)
            if found is not None:
                self._pwsh_resolved = found
                return found
        raise ServiceManagerError("no PowerShell binary on PATH")


def default_repo_root() -> Path:
    """Resolve the repo root relative to this module.

    ``adapters/windows/ascendo_windows/managers/service.py`` -> 4 levels
    up = repo root. The dashboard router uses this when it has no
    explicit root override.
    """
    return Path(__file__).resolve().parents[4]


def is_windows() -> bool:
    return sys.platform.startswith("win")


__all__ = [
    "ServiceActionResult",
    "ServiceElevationRequired",
    "ServiceManagerError",
    "ServiceStatus",
    "WindowsServiceManager",
    "default_repo_root",
    "is_windows",
]
