"""LaunchdScheduler — IScheduler via macOS launchd LaunchAgents.

Drives a single bash script `scheduler.sh` over JSON-IPC. Mirrors the
M3.13 WindowsScheduler shape exactly:
  - install / uninstall / list / get / trigger map to ``--action <verb>``.
  - Schedule expression (DSL: ``DAILY/WEEKLY/MONTHLY/HOURLY/MINUTE``) is
    parsed by the bash driver and translated to a ``StartCalendarInterval``
    plist dict.
  - Per-user agents only — written to ``~/Library/LaunchAgents/dev.ascendo.<name>.plist``.

Description metadata that doesn't fit in a launchd plist (free-form
description string) is stored in a sidecar JSON at
``~/Library/Application Support/Ascendo/schedules/<name>.json``.
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


class LaunchdScheduler(IScheduler):
    """launchd LaunchAgent-backed IScheduler for macOS."""

    BACKEND: ClassVar[str] = "launchd"
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
        if host.os is not OperatingSystem.MACOS:
            return False
        return shutil.which("launchctl") is not None

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
                raise SchedulerError(f"failed to spawn bash for scheduler {action}: {exc}") from exc

            if completed.returncode != 0 and not output.exists():
                raise SchedulerError(
                    f"scheduler {action} failed: exit={completed.returncode} "
                    f"stderr={completed.stderr[:300]!r}"
                )
            if not output.exists():
                # install / uninstall / trigger may not produce output.
                return None
            try:
                parsed = json.loads(output.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                raise SchedulerError(f"scheduler {action} emitted invalid JSON: {exc}") from exc
            if completed.returncode != 0 and isinstance(parsed, dict) and "error" in parsed:
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
        for candidate in ("bash", "/bin/bash", "/usr/local/bin/bash"):
            found = shutil.which(candidate)
            if found is not None:
                self._bash_resolved = found
                return found
        raise SchedulerError("no bash binary on PATH")
