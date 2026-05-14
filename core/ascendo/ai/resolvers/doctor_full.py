"""doctor_full: full health_check rollup with per-component status + message."""
from __future__ import annotations


def resolve(*, adapter, inventory_db, runs_dir) -> tuple[str, int]:
    if adapter is None:
        return "", 0
    try:
        health = adapter.health_check()
    except Exception:
        return "", 0
    lines = ["## Doctor (full)"]
    for c in health.get("components", []) or []:
        name = c.get("name", "?")
        status = c.get("status", "?")
        msg = c.get("message", "")
        lines.append(f"- {name}: {status} ({msg})")
    return "\n".join(lines), 8
