"""ascendo-ubuntu — Tier 1 adapter for Ubuntu / Debian.

Bridges to the legacy bash phase scripts at top-level ``scripts/<cat>/<phase>.sh``
(which emit ``ascendo/v1`` sidecars). The orchestrator's
``parse_sidecar()`` (in ``core/ascendo/models/sidecar.py``) auto-translates
the legacy schema to ``ascendo/v1`` via ``core/ascendo/models/legacy.py``,
so we do NOT rewrite the bash here.

Capabilities declared:
    PACKAGE_MANAGEMENT — apt, snap, brew, npm, pip, flatpak, drivers
    INVENTORY          — UbuntuInventory placeholder

Scheduler / Snapshot / Elevation / Source accessors return ``None`` for
this scaffold; they're deferred to a later session.
"""
from __future__ import annotations

from .adapter import UbuntuAdapter

__all__ = ["UbuntuAdapter"]
