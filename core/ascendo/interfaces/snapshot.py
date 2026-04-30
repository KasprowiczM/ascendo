"""ISnapshot — system snapshot create / list / restore.

Per ADR ("Rollback — 3 poziomy"), the second rollback level is a
system snapshot. Each OS has a different mechanism:

- Linux: ``timeshift`` (BTRFS / RSYNC) or ``etckeeper`` (config-only).
- Windows: VSS (Volume Shadow Copy Service) — ``vssadmin`` + WMI.
- macOS: Time Machine (read-only mount via ``tmutil``).

The interface is deliberately thin — adapters expose creation, listing,
and metadata; actual *restore* is gated behind an explicit user action
in the dashboard or CLI (we don't auto-restore).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from ..models.host import HostInfo


class SnapshotInfo(BaseModel):
    """Metadata for a single system snapshot. Frozen."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(description="Backend-specific identifier (timeshift name, VSS shadow GUID, Time Machine timestamp).")
    created_at: datetime = Field(description="ISO-8601 creation time (UTC).")
    label: str | None = Field(
        default=None,
        description="Human-readable label set by Ascendo at snapshot time. e.g. 'before apt apply 2026-05-01'.",
    )
    backend: str = Field(description="Backend slug: 'timeshift' | 'vss' | 'time_machine' | 'manual'.")
    size_bytes: int | None = Field(
        default=None,
        description="On-disk size if reported by the backend.",
    )
    notes: str | None = Field(default=None)


class ISnapshot(ABC):
    """System snapshot interface.

    Implementations live in adapter packages (e.g.
    ``ascendo_ubuntu.snapshot.TimeshiftSnapshot``).

    **Availability is gracefully degraded.** A host without a snapshot
    backend (e.g. ext4 + no timeshift) returns ``is_available() == False``;
    the orchestrator records this and proceeds with rollback level 3
    (markdown instructions only).
    """

    @property
    @abstractmethod
    def backend(self) -> str:
        """Backend slug. Stable per implementation."""

    @abstractmethod
    def is_available(self, host: HostInfo) -> bool:
        """True if the snapshot backend is operational on this host."""

    @abstractmethod
    def create(
        self,
        host: HostInfo,
        *,
        label: str,
        notes: str | None = None,
    ) -> SnapshotInfo:
        """Create a new snapshot.

        Called pre-apply for runs whose profile requires snapshots
        (typically ``profile in {'safe', 'full'}``).

        Args:
            host: Host context.
            label: Human-readable label persisted with the snapshot.
            notes: Optional longer free-form description.

        Returns:
            Metadata for the created snapshot.

        Raises:
            SnapshotError: backend rejected the request.
        """

    @abstractmethod
    def list(self, host: HostInfo) -> list[SnapshotInfo]:
        """List all snapshots known to the backend on this host."""

    @abstractmethod
    def get(self, host: HostInfo, snapshot_id: str) -> SnapshotInfo | None:
        """Return metadata for a specific snapshot, or None if not found."""


class SnapshotError(RuntimeError):
    """Snapshot backend rejected an operation."""
