"""schedules_current: installed schedules from IScheduler.list()."""
from __future__ import annotations


def resolve(*, adapter, inventory_db, runs_dir) -> tuple[str, int]:
    if adapter is None:
        return "", 0
    try:
        scheduler = adapter.scheduler()
    except Exception:
        return "", 0
    if scheduler is None:
        return "## Schedules\n(no IScheduler on this adapter)", 3
    try:
        schedules = scheduler.list()
    except Exception:
        return "", 0
    if not schedules:
        return "## Schedules\n(none installed)", 4
    lines = ["## Schedules (installed)"]
    for s in schedules:
        name = getattr(s, "name", None) or s.get("name", "?") if isinstance(s, dict) else getattr(s, "name", "?")
        expr = getattr(s, "expression", None) or (s.get("expression", "") if isinstance(s, dict) else "")
        profile = getattr(s, "profile", None) or (s.get("profile", "") if isinstance(s, dict) else "")
        lines.append(f"- {name}: {expr} -> profile={profile}")
    return "\n".join(lines), 5
