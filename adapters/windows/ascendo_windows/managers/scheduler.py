"""WindowsScheduler — IScheduler via Windows Task Scheduler.

Drives ``schtasks.exe`` (which has a stable, documented CLI surface) plus
the PowerShell ScheduledTasks module for richer query operations. Each
Ascendo schedule is a Task Scheduler entry under
``\\Ascendo\\<name>`` with the action set to ``ascendo run --profile <p>``.

The :class:`ScheduleSpec.expression` field is interpreted as a **schtasks
schedule string** of the form ``DAILY|WEEKLY|MONTHLY|HOURLY|MINUTE 03:00``
or as a literal ``DAYS=SUN START_TIME=03:00`` style mini-spec. The script
does the parsing — keeping the Python adapter dumb and the PowerShell side
the single source of truth for OS-specific details.
"""
from __future__ import annotations

import json
import logging
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import ClassVar

from ascendo.interfaces.scheduler import IScheduler, ScheduleSpec, SchedulerError
from ascendo.models.host import HostInfo, OperatingSystem
from ascendo.utils.proc import no_window_kwargs

_log = logging.getLogger(__name__)


class WindowsScheduler(IScheduler):
    """Task Scheduler-backed IScheduler for Windows."""

    BACKEND: ClassVar[str] = "task_scheduler"
    DEFAULT_TIMEOUT_SEC: ClassVar[int] = 60

    def __init__(
        self,
        *,
        scripts_dir: Path,
        lib_dir: Path,
        pwsh_path: str | None = None,
        timeout_sec: int = DEFAULT_TIMEOUT_SEC,
    ) -> None:
        self._scripts_dir = Path(scripts_dir)
        self._lib_dir = Path(lib_dir)
        self._pwsh_override = pwsh_path
        self._pwsh_resolved: str | None = None
        self._timeout_sec = timeout_sec

    @property
    def backend(self) -> str:
        return self.BACKEND

    def is_available(self, host: HostInfo) -> bool:
        if host.os is not OperatingSystem.WINDOWS:
            return False
        return shutil.which("schtasks") is not None or shutil.which("schtasks.exe") is not None

    def install(self, host: HostInfo, spec: ScheduleSpec) -> None:
        body = {
            "name":        spec.name,
            "expression":  spec.expression,
            "profile":     spec.profile,
            "enabled":     spec.enabled,
            "description": spec.description or "",
        }
        self._invoke("install", payload=body)

    def uninstall(self, host: HostInfo, name: str) -> None:
        self._invoke("uninstall", payload={"name": name})

    def list(self, host: HostInfo) -> list[ScheduleSpec]:  # noqa: A003
        result = self._invoke("list")
        if not isinstance(result, list):
            return []
        out: list[ScheduleSpec] = []
        for item in result:
            try:
                out.append(self._parse_spec(item))
            except (TypeError, ValueError):
                continue
        return out

    def get(self, host: HostInfo, name: str) -> ScheduleSpec | None:
        for spec in self.list(host):
            if spec.name == name:
                return spec
        return None

    def trigger(self, host: HostInfo, name: str) -> None:
        self._invoke("trigger", payload={"name": name})

    # ── Internals ────────────────────────────────────────────────────────

    def _invoke(self, action: str, *, payload: dict | None = None):
        script = self._scripts_dir / "scheduler" / "scheduler.ps1"
        pwsh = self._resolve_pwsh()
        with tempfile.TemporaryDirectory(prefix="ascendo-sched-") as tmp:
            output = Path(tmp) / "result.json"
            payload_path = None
            argv: list[str] = [
                pwsh, "-NoProfile", "-NonInteractive",
                "-ExecutionPolicy", "Bypass",
                "-File", str(script),
                "-Action", action,
                "-OutputPath", str(output),
            ]
            if payload is not None:
                payload_path = Path(tmp) / "payload.json"
                payload_path.write_text(json.dumps(payload), encoding="utf-8")
                argv += ["-PayloadPath", str(payload_path)]
            try:
                completed = subprocess.run(  # noqa: S603
                    argv, capture_output=True, text=True,
                    timeout=self._timeout_sec, check=False,
                    **no_window_kwargs(),
                )
            except subprocess.TimeoutExpired as exc:
                raise SchedulerError(f"scheduler {action} timed out") from exc
            except OSError as exc:
                raise SchedulerError(f"failed to spawn pwsh for scheduler {action}: {exc}") from exc

            if completed.returncode != 0 and not output.exists():
                raise SchedulerError(
                    f"scheduler {action} failed: exit={completed.returncode} "
                    f"stderr={completed.stderr[:300]!r}"
                )
            if not output.exists():
                # install / uninstall / trigger may not produce output.
                return None
            try:
                return json.loads(output.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                raise SchedulerError(f"scheduler {action} emitted invalid JSON: {exc}") from exc

    def _parse_spec(self, item: dict) -> ScheduleSpec:
        return ScheduleSpec(
            name=str(item.get("name", "")),
            expression=str(item.get("expression", "")),
            profile=str(item.get("profile", "full")),
            enabled=bool(item.get("enabled", True)),
            description=item.get("description") or None,
        )

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
        raise SchedulerError("no PowerShell binary on PATH")
