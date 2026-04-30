"""IPackageManager — per-source driver interface.

A `PackageManager` instance corresponds to **one source** (apt OR winget OR
brew formula OR brew cask). The OS adapter (`IAdapter`) registers all
available managers. The core orchestrator iterates over them per phase.

This is the highest-traffic interface in the system — every `ascendo run`
fans out to N package managers in parallel.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable

from ..models.host import HostInfo
from ..models.package import SourceType
from ..models.run import Phase, RunInfo
from ..models.sidecar import Sidecar


class IPackageManager(ABC):
    """Abstract per-source package manager.

    Implementations live in ``adapters/<os>/ascendo_<os>/managers/<src>.py``.
    Each implementation:

    1. Owns a single :class:`SourceType` (e.g. ``SourceType.APT``).
    2. Knows how to detect availability on the host
       (e.g. ``which apt`` returns 0).
    3. Drives the 5-phase contract by spawning native scripts and
       parsing their JSON v1 sidecars.

    **Implementations MUST be stateless w.r.t. runs.** All run-scoped
    state lives in the :class:`Sidecar` instances they return. Multiple
    managers may run concurrently; per-instance mutation is forbidden.
    """

    # ── Identity ─────────────────────────────────────────────────────

    @property
    @abstractmethod
    def category(self) -> SourceType:
        """The source category this manager owns. Stable per implementation."""

    @property
    @abstractmethod
    def display_name(self) -> str:
        """Human-readable name for dashboard / CLI banners. e.g. 'Advanced Package Tool'."""

    # ── Availability check ───────────────────────────────────────────

    @abstractmethod
    def is_available(self, host: HostInfo) -> bool:
        """True if this manager's tool is present and usable on the given host.

        Called once per run to filter out unavailable managers (e.g. ``snap``
        on a system without snapd installed). MUST NOT mutate state and
        SHOULD complete in < 100 ms (typical: ``which`` + version probe).
        """

    # ── Phase execution ──────────────────────────────────────────────

    @abstractmethod
    def run_phase(
        self,
        phase: Phase,
        run: RunInfo,
        host: HostInfo,
        *,
        item_filter: Iterable[str] | None = None,
    ) -> Sidecar:
        """Execute one phase of the 5-phase contract for this manager.

        Args:
            phase: Which phase to run. Manager MAY no-op for phases it
                doesn't support (e.g. a read-only inventory manager
                returns an empty success sidecar for ``apply``).
            run: Run-level metadata (id, profile, dry_run flag).
            host: Detected host info (OS, arch, elevation).
            item_filter: Optional iterable of package IDs to restrict the
                operation to. If None, the manager processes all
                applicable items.

        Returns:
            A :class:`Sidecar` describing what happened. The sidecar is
            persisted by the orchestrator; the manager does NOT write to
            disk itself.

        Raises:
            ManagerError: only on unrecoverable infrastructure failures
                (e.g. the underlying tool's binary disappeared mid-run).
                Per-item failures MUST be recorded in ``sidecar.items[]``,
                not raised.
        """


class ManagerError(RuntimeError):
    """Unrecoverable infrastructure failure within a package manager.

    Distinct from per-item failures (which live in ``Sidecar.items[]``).
    Raised only when the manager itself cannot complete its phase —
    e.g. the binary disappeared, a sidecar file failed to be parseable,
    a sandbox restriction prevented spawning the script.
    """
