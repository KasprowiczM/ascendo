"""Ascendo core domain models (Layer 4).

These models are the **canonical data shapes** of the system. They are
shared across:

- Native scripts (Layer 6) emit JSON v1 sidecars conforming to `Sidecar`.
- Python adapters (Layer 5) parse those sidecars into model instances.
- Core orchestrator (Layer 4) operates on instances.
- FastAPI dashboard (Layer 3) serializes instances back to JSON for the SPA.

**Versioning policy.** New fields are additive and optional with sensible
defaults. Breaking changes require a schema bump (`ascendo/v2`) and a
deprecation cycle where readers accept both versions. See ADR-0003.

All models are Pydantic v2. Frozen where they represent immutable
historical records (Sidecar, RunInfo); mutable where they represent
in-flight work (Item before resolution).
"""

from .host import (
    ElevationMethod,
    HostInfo,
    OperatingSystem,
)
from .package import (
    ItemEvidence,
    ItemRollback,
    ItemSource,
    Package,
    SourceType,
)
from .result import (
    Item,
    ItemStatus,
    Message,
    MessageLevel,
    Summary,
)
from .run import (
    Phase,
    PhaseStatus,
    RunInfo,
    Trigger,
)
from .sidecar import (
    Sidecar,
    SidecarSchema,
    ToolInfo,
)

__all__ = [
    # host
    "ElevationMethod",
    "HostInfo",
    "OperatingSystem",
    # package
    "ItemEvidence",
    "ItemRollback",
    "ItemSource",
    "Package",
    "SourceType",
    # result
    "Item",
    "ItemStatus",
    "Message",
    "MessageLevel",
    "Summary",
    # run
    "Phase",
    "PhaseStatus",
    "RunInfo",
    "Trigger",
    # sidecar (top-level)
    "Sidecar",
    "SidecarSchema",
    "ToolInfo",
]
