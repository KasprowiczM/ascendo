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
        raise NotImplementedError("M5.5.7")

    def uninstall(self, host: HostInfo, name: str) -> None:
        raise NotImplementedError("M5.5.7")

    def list(self, host: HostInfo) -> list[ScheduleSpec]:  # noqa: A003
        raise NotImplementedError("M5.5.7")

    def get(self, host: HostInfo, name: str) -> ScheduleSpec | None:
        raise NotImplementedError("M5.5.7")

    def trigger(self, host: HostInfo, name: str) -> None:
        raise NotImplementedError("M5.5.7")
