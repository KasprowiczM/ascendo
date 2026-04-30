"""IAdapter — the OS-level entry point.

An adapter is a single Python class per OS that aggregates all the
sub-interfaces (package managers, snapshot, scheduler, source, elevation,
inventory) into a coherent OS implementation. The ``adapter_factory``
in core picks the right adapter based on detected :class:`HostInfo.os`.

Tier 1 adapters live in ``adapters/<os>/ascendo_<os>/`` and inherit from
``IAdapter`` directly. Tier 2 adapters in ``contrib/adapters/<os>/`` MAY
provide a partial implementation; the core fallback dispatcher fills in
missing capabilities by delegating to native scripts directly via the
plugin manifest contract.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import Flag, auto

from ..models.host import HostInfo
from .elevation import IElevation
from .inventory import IInventory
from .package_manager import IPackageManager
from .scheduler import IScheduler
from .snapshot import ISnapshot
from .source import ISource


class AdapterCapability(Flag):
    """Optional capabilities an adapter may declare.

    Tier 1 adapters typically declare all of these. Tier 2 adapters
    declare a subset — the orchestrator gracefully degrades when a
    capability is missing.
    """

    NONE = 0
    PACKAGE_MANAGEMENT = auto()
    INVENTORY = auto()
    SNAPSHOTS = auto()
    SCHEDULING = auto()
    SOURCE_VERIFICATION = auto()
    ELEVATION = auto()
    PLUGIN_HOSTING = auto()
    # Convenient aggregates:
    TIER_1_FULL = (
        PACKAGE_MANAGEMENT
        | INVENTORY
        | SNAPSHOTS
        | SCHEDULING
        | SOURCE_VERIFICATION
        | ELEVATION
        | PLUGIN_HOSTING
    )


class IAdapter(ABC):
    """OS-level adapter — the aggregate root for an OS implementation.

    The lifetime of an adapter spans an entire process. Adapters are
    instantiated once at startup by the :class:`AdapterFactory`; they
    are NOT recreated per run. Per-run state lives in :class:`RunInfo`
    and the produced :class:`Sidecar` instances.
    """

    # ── Identity ─────────────────────────────────────────────────────

    @property
    @abstractmethod
    def name(self) -> str:
        """Slug. e.g. 'ubuntu', 'windows', 'macos'."""

    @property
    @abstractmethod
    def display_name(self) -> str:
        """Human-readable. e.g. 'Ubuntu / Debian', 'Windows', 'macOS'."""

    @property
    @abstractmethod
    def capabilities(self) -> AdapterCapability:
        """Bit-set of declared capabilities. Drives orchestrator dispatch."""

    @property
    @abstractmethod
    def tier(self) -> int:
        """1 (official, full pack) or 2 (community, minimum)."""

    # ── Sub-interface accessors ──────────────────────────────────────

    @abstractmethod
    def package_managers(self, host: HostInfo) -> list[IPackageManager]:
        """All available :class:`IPackageManager` instances for this host.

        Order is significant — the orchestrator runs phases in the
        returned order. Adapters typically order managers by privilege
        requirement (low → high) so that user-context managers run before
        elevated ones.
        """

    @abstractmethod
    def inventory(self) -> IInventory:
        """Return the inventory implementation. Required if ``INVENTORY`` capability is set."""

    @abstractmethod
    def snapshot(self) -> ISnapshot | None:
        """Snapshot backend, or None if ``SNAPSHOTS`` capability is unset."""

    @abstractmethod
    def scheduler(self) -> IScheduler | None:
        """Scheduler backend, or None if ``SCHEDULING`` capability is unset."""

    @abstractmethod
    def source(self) -> ISource | None:
        """Source registry, or None if ``SOURCE_VERIFICATION`` capability is unset."""

    @abstractmethod
    def elevation(self) -> IElevation | None:
        """Elevation backend, or None if ``ELEVATION`` capability is unset."""

    # ── Lifecycle ────────────────────────────────────────────────────

    @abstractmethod
    def detect_host(self) -> HostInfo:
        """Build a :class:`HostInfo` snapshot of the current host.

        Called once per run by the orchestrator. The result is passed to
        every other adapter method that needs host context. Implementations
        SHOULD cache the result — repeated calls within a process MAY
        return the same instance.
        """

    @abstractmethod
    def health_check(self) -> dict[str, str]:
        """Adapter self-test — returns a dict of ``component → status string``.

        Used by ``ascendo doctor`` and the dashboard's diagnostics view.
        Each component's status is one of:

        - ``"ok"``: working as expected.
        - ``"degraded: <reason>"``: partially functional.
        - ``"unavailable: <reason>"``: not usable on this host.
        - ``"error: <reason>"``: detected fault.
        """
