"""churn_history_30d: apps with >=3 updates in last 30 days.

Sources from update_history table (added in HANDOFF Sesja 43). Falls back to
an empty section if the table doesn't exist (older deployments).
"""
from __future__ import annotations


def resolve(*, adapter, inventory_db, runs_dir) -> tuple[str, int]:
    if inventory_db is None or not hasattr(inventory_db, "_connect"):
        return "", 0
    try:
        conn = inventory_db._connect()
    except Exception:
        return "", 0
    try:
        # Probe for update_history table; not all hosts have it.
        try:
            rows = conn.execute(
                "SELECT category, name, COUNT(*) AS n FROM update_history "
                "WHERE applied_at >= date('now','-30 days') "
                "GROUP BY category, name HAVING n >= 3 "
                "ORDER BY n DESC LIMIT 25"
            ).fetchall()
        except Exception:
            return "", 0
    finally:
        conn.close()
    if not rows:
        return "## Churn history (30d)\n(no apps updated 3+ times in last 30 days)", 4
    lines = ["## Churn history (30d) — apps updated 3+ times"]
    for r in rows:
        try:
            cat, name, n = r[0], r[1], r[2]
        except Exception:
            continue
        lines.append(f"- {name} ({cat}): {n} updates")
    return "\n".join(lines), 6
