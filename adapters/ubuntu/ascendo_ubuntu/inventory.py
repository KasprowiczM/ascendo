"""UbuntuInventory - IInventory placeholder for the Ubuntu adapter.

The legacy ``scripts/inventory/`` flow + ``update-inventory.sh`` produce
``APPS.md`` (markdown) — they don't currently emit a parseable list of
:class:`Package` objects. Wiring full inventory enumeration (apt + snap +
flatpak + brew + npm + pip → Package objects with versions) is deferred
to a later session.

For now this implementation:
  * returns an empty list from ``list_installed`` (zero items, no error).
  * synthesizes an empty ``check / inventory`` sidecar from
    ``emit_sidecar`` so the orchestrator can persist a no-op inventory
    snapshot during a run.
"""
from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path

from ascendo.interfaces.inventory import IInventory
from ascendo.models.host import HostInfo, OperatingSystem
from ascendo.models.package import Package, SourceType
from ascendo.models.result import Item, ItemStatus, Summary
from ascendo.models.run import Phase, PhaseStatus, RunInfo
from ascendo.models.sidecar import Sidecar, SidecarSchema, ToolInfo

_log = logging.getLogger(__name__)


class UbuntuInventory(IInventory):
    """Placeholder inventory enumerator for Ubuntu.

    Returns an empty list / empty sidecar for now. Real enumeration
    lands in a later session.

    Args:
        scripts_dir: Path to the legacy top-level ``scripts/`` directory.
        lib_dir: Path to the legacy top-level ``lib/`` directory.
    """

    def __init__(self, *, scripts_dir: Path, lib_dir: Path) -> None:
        self._scripts_dir = Path(scripts_dir)
        self._lib_dir = Path(lib_dir)

    @property
    def scripts_dir(self) -> Path:
        return self._scripts_dir

    def list_installed(
        self,
        host: HostInfo,
        *,
        categories: Iterable[str] | None = None,
    ) -> list[Package]:
        """Return an empty list — full enumeration is deferred."""
        if not host.os.value.startswith("linux_"):
            return []
        _log.debug(
            "UbuntuInventory.list_installed: returning empty list "
            "(full enumeration not yet implemented)"
        )
        return []

    def emit_sidecar(
        self,
        run: RunInfo,
        host: HostInfo,
        packages: list[Package],
    ) -> Sidecar:
        """Synthesize a minimal ``check / inventory`` sidecar.

        Items reflect ``packages``; status is always ``up_to_date``
        (everything in the inventory is, by definition, installed).
        """
        now = datetime.now(timezone.utc).isoformat()
        items: list[Item] = []
        for pkg in packages:
            items.append(
                Item(
                    id=pkg.id,
                    name=pkg.name,
                    category=pkg.category,
                    source=pkg.source,
                    current_version=pkg.installed_version,
                    target_version=pkg.available_version,
                    status=ItemStatus.UP_TO_DATE,
                )
            )
        summary = Summary(
            total=len(items),
            success=0,
            up_to_date=len(items),
            failed=0,
            skipped=0,
            planned=0,
            partial=0,
            duration_ms=None,
            exit_code=0,
        )
        return Sidecar.model_validate(
            {
                "schema": SidecarSchema.V1_ASCENDO,
                "run": run,
                "host": host,
                "tool": ToolInfo(name="ubuntu-inventory", version="0.0.7"),
                "phase": Phase.CHECK,
                "category": SourceType.INVENTORY,
                "started_at": now,
                "finished_at": now,
                "status": PhaseStatus.SUCCESS,
                "items": items,
                "summary": summary,
                "messages": [],
                "needs_reboot": False,
            }
        )
