"""IScheduler — schedule periodic Ascendo runs.

OS-specific backends:

- Linux: ``systemd --user`` timers (preferred) or ``cron`` (fallback).
- Windows: Task Scheduler (``schtasks.exe`` or PowerShell
  ``ScheduledTasks`` module).
- macOS: ``launchd`` ``LaunchAgents``.

The interface is intentionally narrow: install / uninstall / status /
trigger-now. We do NOT expose generic timer manipulation — Ascendo
manages exactly its own schedule entries.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from ..models.host import HostInfo
from ..models.run import ProfileName


# Scheduling expression. Backend interprets per-OS:
# - systemd: OnCalendar string (e.g. "Sun *-*-* 03:00:00")
# - launchd: cron-like string (compatible subset)
# - Windows Task Scheduler: schtasks /SC argument
ScheduleExpr = Annotated[
    str,
    StringConstraints(min_length=1, max_length=256, strip_whitespace=True),
]


class ScheduleSpec(BaseModel):
    """Declarative schedule entry. Frozen."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: Annotated[str, StringConstraints(pattern=r"^[a-z0-9-]+$", min_length=1, max_length=64)] = Field(
        description="Slug used by the backend to identify the entry. e.g. 'ascendo-weekly'.",
    )
    expression: ScheduleExpr = Field(
        description="Schedule expression in backend-native syntax (see backend docs).",
    )
    profile: ProfileName = Field(
        description="Profile to run. 'quick' | 'safe' | 'full' or custom.",
    )
    enabled: bool = Field(default=True, description="Whether the schedule is active.")
    description: str | None = Field(
        default=None,
        description="Optional free-form description shown in the dashboard.",
    )


class IScheduler(ABC):
    """Schedule manager for periodic Ascendo runs."""

    @property
    @abstractmethod
    def backend(self) -> str:
        """Backend slug: 'systemd' | 'launchd' | 'task_scheduler' | 'cron'."""

    @abstractmethod
    def is_available(self, host: HostInfo) -> bool:
        """True if the backend is operational on this host."""

    @abstractmethod
    def install(self, host: HostInfo, spec: ScheduleSpec) -> None:
        """Install / overwrite a schedule entry.

        Idempotent — re-installing the same name updates the entry.
        """

    @abstractmethod
    def uninstall(self, host: HostInfo, name: str) -> None:
        """Remove a schedule entry. No-op if it doesn't exist."""

    @abstractmethod
    def list(self, host: HostInfo) -> list[ScheduleSpec]:
        """List Ascendo's own schedule entries (NOT every system entry)."""

    @abstractmethod
    def get(self, host: HostInfo, name: str) -> ScheduleSpec | None:
        """Return the entry for `name`, or None if it doesn't exist."""

    @abstractmethod
    def trigger(self, host: HostInfo, name: str) -> None:
        """Run the named schedule entry immediately.

        Useful for "Run now" buttons in the dashboard. The trigger
        produces a regular run with ``Trigger.SCHEDULER`` so the run
        history correctly attributes it.
        """


class SchedulerError(RuntimeError):
    """Scheduler backend rejected an operation."""
