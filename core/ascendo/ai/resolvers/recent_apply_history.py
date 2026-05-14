"""recent_apply_history: last 5 apply runs summary from update_history."""
from __future__ import annotations


def resolve(*, adapter, inventory_db, runs_dir) -> tuple[str, int]:
    if inventory_db is None or not hasattr(inventory_db, "_connect"):
        return "", 0
    try:
        conn = inventory_db._connect()
    except Exception:
        return "", 0
    try:
        try:
            rows = conn.execute(
                "SELECT category, name, from_version, to_version, status, applied_at "
                "FROM update_history ORDER BY applied_at DESC LIMIT 20"
            ).fetchall()
        except Exception:
            return "", 0
    finally:
        conn.close()
    if not rows:
        return "## Recent apply history\n(empty)", 4
    lines = ["## Recent apply history (last 20)"]
    for r in rows:
        try:
            cat, name, fr, to, status, when = r[0], r[1], r[2], r[3], r[4], r[5]
        except Exception:
            continue
        lines.append(f"- [{when}] {name} ({cat}): {fr} -> {to} [{status}]")
    return "\n".join(lines), 6
