"""latest_report_md: most recent REPORT.md, truncated to 2k chars."""
from __future__ import annotations

from pathlib import Path


def resolve(*, adapter, inventory_db, runs_dir) -> tuple[str, int]:
    if runs_dir is None:
        return "", 0
    p = Path(runs_dir)
    if not p.exists():
        return "", 0
    candidates: list[tuple[float, Path]] = []
    for sub in p.iterdir():
        if sub.is_dir():
            r = sub / "REPORT.md"
            if r.exists():
                candidates.append((r.stat().st_mtime, r))
    if not candidates:
        return "", 0
    candidates.sort(reverse=True)
    _, path = candidates[0]
    text = path.read_text()[:2000]
    return f"## Latest REPORT.md\n{text}", 8
