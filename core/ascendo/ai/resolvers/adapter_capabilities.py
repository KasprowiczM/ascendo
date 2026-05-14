"""adapter_capabilities: capability flag + manager list."""
from __future__ import annotations


def resolve(*, adapter, inventory_db, runs_dir) -> tuple[str, int]:
    if adapter is None:
        return "", 0
    try:
        caps = adapter.capabilities
    except Exception:
        return "", 0
    return f"## Adapter capabilities\n{caps}", 5
