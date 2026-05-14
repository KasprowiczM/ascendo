"""latest_failed_sidecar: most recent sidecar with status=failed, truncated."""
from __future__ import annotations

import json
from pathlib import Path


def resolve(*, adapter, inventory_db, runs_dir) -> tuple[str, int]:
    if runs_dir is None:
        return "", 0
    p = Path(runs_dir)
    if not p.exists():
        return "", 0
    candidates: list[tuple[float, Path, dict]] = []
    for sub in p.iterdir():
        if not sub.is_dir():
            continue
        for sc in sub.glob("*__*.json"):
            try:
                data = json.loads(sc.read_text())
            except Exception:
                continue
            if data.get("status") == "failed":
                candidates.append((sc.stat().st_mtime, sc, data))
    if not candidates:
        return "", 0
    candidates.sort(reverse=True)
    _, path, data = candidates[0]
    payload = json.dumps(data, indent=2)[:2000]
    return f"## Latest failed sidecar ({path.name})\n```json\n{payload}\n```", 9
