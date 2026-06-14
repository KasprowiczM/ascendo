"""Abstract interfaces — the architectural firewall (Layer 4 → Layer 5).

These ABCs define the contract every adapter must satisfy. Core
modules depend ONLY on these interfaces, never on concrete adapter
implementations. The dependency rule is enforced at CI by import-linter
(see `core/pyproject.toml [tool.importlinter]`).

**Style choice: abc.ABC over typing.Protocol.** We use classical
abstract base classes here — adapters explicitly inherit and gain
`@abstractmethod` runtime checks. Protocols are reserved for purely
structural types (data carriers, optional capabilities). See
ADR-0005 for rationale.

**Interface inventory (M2):**

- ``IAdapter``         — the OS-level entry point; aggregates the others.
- ``IPackageManager``  — per-source driver: apt, winget, brew, snap, ...
- ``IInventory``       — installed-package enumeration across sources.
- ``ISnapshot``        — system snapshot create / list / restore.
- ``IScheduler``       — schedule periodic runs (systemd / launchd / Task Scheduler).
- ``ISource``          — package source / feed metadata + signature verification.
- ``IElevation``       — privilege escalation abstraction (sudo / UAC / pkexec).
"""

from .adapter import AdapterCapability, IAdapter
from .elevation import IElevation
from .inventory import IInventory
from .package_manager import IPackageManager
from .scheduler import IScheduler, ScheduleSpec
from .secret_store import ISecretStore
from .snapshot import ISnapshot, SnapshotInfo
from .source import ISource, SourceMetadata

__all__ = [
    # adapter (top-level)
    "AdapterCapability",
    "IAdapter",
    # primary
    "IPackageManager",
    "IInventory",
    "ISnapshot",
    "IScheduler",
    "ISource",
    "IElevation",
    "ISecretStore",
    # value types declared alongside their interfaces
    "ScheduleSpec",
    "SnapshotInfo",
    "SourceMetadata",
]
