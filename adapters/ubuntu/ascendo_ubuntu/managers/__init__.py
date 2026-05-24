"""Per-source IPackageManager implementations for the Ubuntu adapter.

Each manager wraps a category subdirectory under the legacy top-level
``scripts/<cat>/`` and shells out to the existing 5 phase scripts:
``check.sh / plan.sh / apply.sh / verify.sh / cleanup.sh``.

The legacy scripts emit ``ascendo/v1`` sidecars; the
orchestrator's ``parse_sidecar()`` translates them to ``ascendo/v1``
on read.
"""
from __future__ import annotations
