"""SystemdScheduler — IScheduler via systemd user timers.

Drives a single bash script ``scheduler.sh`` over JSON-IPC. Mirrors the
LaunchdScheduler shape (macOS) and WindowsScheduler (Windows) exactly:
  - install / uninstall / list / get / trigger map to ``--action <verb>``.
  - Schedule expression (DSL: ``DAILY/WEEKLY/MONTHLY/HOURLY/MINUTE``) is
    parsed by the bash driver and translated to ``OnCalendar=...`` /
    ``OnUnitActiveSec=...`` strings in the .timer unit.
  - Per-user only — units written to ``~/.config/systemd/user/ascendo-<name>.{service,timer}``.

Description metadata that doesn't fit in a .timer file is stored in a
sidecar JSON at ``~/.local/share/ascendo/schedules/<name>.json``.
"""
from __future__ import annotations

import json
import logging
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import ClassVar

from ascendo.interfaces.scheduler import IScheduler, ScheduleSpec, SchedulerError
from ascendo.models.host import HostInfo, OperatingSystem

_log = logging.getLogger(__name__)

# All linux_* variants are accepted (Ubuntu, Debian, Other).
_LINUX_OS = {
    OperatingSystem.LINUX_UBUNTU,
    OperatingSystem.LINUX_DEBIAN,
    OperatingSystem.LINUX_OTHER,
}


class SystemdScheduler(IScheduler):
    """systemd user-timer-backed IScheduler for Linux."""

    BACKEND: ClassVar[str] = "systemd"
    DEFAULT_TIMEOUT_SEC: ClassVar[int] = 30

    def __init__(
        self,
        *,
        scripts_dir: Path,
        lib_dir: Path,
        bash_path: str | None = None,
        timeout_sec: int = DEFAULT_TIMEOUT_SEC,
    ) -> None:
        self._scripts_dir = Path(scripts_dir)
        self._lib_dir = Path(lib_dir)
        self._bash_override = bash_path
        self._bash_resolved: str | None = None
        self._timeout_sec = timeout_sec

    @property
    def backend(self) -> str:
        return self.BACKEND

    def is_available(self, host: HostInfo) -> bool:
        if host.os not in _LINUX_OS:
            return False
        return shutil.which("systemctl") is not None

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
        if not isinstance(result, dict):
            return []
        items = result.get("schedules", [])
        if not isinstance(items, list):
            return []
        out: list[ScheduleSpec] = []
        for item in items:
            try:
                out.append(self._parse_spec(item))
            except (TypeError, ValueError):
                continue
        return out

    def get(self, host: HostInfo, name: str) -> ScheduleSpec | None:
        try:
            result = self._invoke("get", payload={"name": name})
        except SchedulerError:
            return None
        if not isinstance(result, dict) or "name" not in result:
            return None
        try:
            return self._parse_spec(result)
        except (TypeError, ValueError):
            return None

    def trigger(self, host: HostInfo, name: str) -> None:
        self._invoke("trigger", payload={"name": name})

    # ── Internals ────────────────────────────────────────────────────────

    def _invoke(self, action: str, *, payload: dict | None = None):
        script = self._scripts_dir / "scheduler" / "scheduler.sh"
        bash = self._resolve_bash()
        with tempfile.TemporaryDirectory(prefix="ascendo-sched-") as tmp:
            output = Path(tmp) / "result.json"
            payload_path = None
            argv: list[str] = [
                bash,
                str(script),
                "--action", action,
                "--output-path", str(output),
            ]
            if payload is not None:
                payload_path = Path(tmp) / "payload.json"
                payload_path.write_text(json.dumps(payload), encoding="utf-8")
                argv += ["--payload-path", str(payload_path)]
            try:
                completed = subprocess.run(  # noqa: S603
                    argv, capture_output=True, text=True,
                    timeout=self._timeout_sec, check=False,
                )
            except subprocess.TimeoutExpired as exc:
                raise SchedulerError(f"scheduler {action} timed out") from exc
            except OSError as exc:
                raise SchedulerError(
                    f"failed to spawn bash for scheduler {action}: {exc}"
                ) from exc

            if completed.returncode != 0 and not output.exists():
                raise SchedulerError(
                    f"scheduler {action} failed: exit={completed.returncode} "
                    f"stderr={completed.stderr[:300]!r}"
                )
            if not output.exists():
                return None
            try:
                parsed = json.loads(output.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                raise SchedulerError(
                    f"scheduler {action} emitted invalid JSON: {exc}"
                ) from exc
            if isinstance(parsed, dict) and "error" in parsed and completed.returncode != 0:
                raise SchedulerError(f"scheduler {action} failed: {parsed['error']}")
            return parsed

    def _parse_spec(self, item: dict) -> ScheduleSpec:
        return ScheduleSpec(
            name=str(item.get("name", "")),
            expression=str(item.get("expression", "")),
            profile=str(item.get("profile", "full")),
            enabled=bool(item.get("enabled", True)),
            description=item.get("description") or None,
        )

    def _resolve_bash(self) -> str:
        if self._bash_resolved is not None:
            return self._bash_resolved
        if self._bash_override is not None:
            self._bash_resolved = self._bash_override
            return self._bash_resolved
        for candidate in ("bash", "/bin/bash"):
            found = shutil.which(candidate)
            if found is not None:
                self._bash_resolved = found
                return found
        # Last-resort: if /bin/bash exists, return it regardless of PATH.
        if Path("/bin/bash").exists():
            self._bash_resolved = "/bin/bash"
            return self._bash_resolved
        raise SchedulerError("no bash binary on PATH")
