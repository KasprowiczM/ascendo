"""outdated_apps: up to 50 outdated rows from InventoryDB."""
from __future__ import annotations


def resolve(*, adapter, inventory_db, runs_dir) -> tuple[str, int]:
    if inventory_db is None:
        return "", 0
    try:
        rows = inventory_db.query(status="outdated")[:50]
    except Exception:
        return "", 0
    if not rows:
        return "## Outdated apps\n(none — everything up to date)", 6
    lines = ["## Outdated apps"]
    for r in rows:
        installed = r.get("installed") if isinstance(r, dict) else getattr(r, "installed", "")
        candidate = r.get("candidate") if isinstance(r, dict) else getattr(r, "candidate", "")
        name = r.get("name") if isinstance(r, dict) else getattr(r, "name", "?")
        cat = r.get("category") if isinstance(r, dict) else getattr(r, "category", "?")
        lines.append(f"- {name} ({cat}): {installed} -> {candidate}")
    return "\n".join(lines), 7
