"""IInventory — installed-package enumeration across all sources.

Inventory is the read-only sweep that produces:

- ``PROGRAMS.md`` / ``PROGRAMS.json`` (Windows) — see Ascendo.
- ``APPS.md`` (Linux) — see Ascendo.
- ``APPLICATIONS.md`` (macOS) — see Ascendo.

In Ascendo, inventory consolidates these into a per-host list of
:class:`Package` instances tagged with their :class:`ItemSource`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable

from ..models.host import HostInfo
from ..models.package import Package
from ..models.run import RunInfo
from ..models.sidecar import Sidecar


class IInventory(ABC):
    """Cross-source enumeration of installed packages.

    Each adapter typically provides one ``Inventory`` implementation that
    fans out to all available :class:`IPackageManager` instances and
    aggregates their per-source listings.

    The inventory is a *snapshot* — point-in-time. Repeated calls during
    a single run MAY return cached results; calls across runs MUST
    re-query the system.
    """

    @abstractmethod
    def list_installed(
        self,
        host: HostInfo,
        *,
        categories: Iterable[str] | None = None,
    ) -> list[Package]:
        """Return all installed packages.

        Args:
            host: Host context (used for source filtering — e.g. a Linux
                host won't query winget).
            categories: Optional filter on :class:`SourceType`. If None,
                every available source is queried.

        Returns:
            List of :class:`Package` instances. Order is implementation-
            defined; callers needing stable ordering MUST sort.
        """

    @abstractmethod
    def emit_sidecar(
        self,
        run: RunInfo,
        host: HostInfo,
        packages: list[Package],
    ) -> Sidecar:
        """Render an inventory sidecar (phase=check, category=inventory).

        Used by the orchestrator to persist the inventory in run history
        even when no apply happens. The dashboard's "what's installed"
        view reads these sidecars.
        """
