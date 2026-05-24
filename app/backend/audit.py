"""Audit log for mutating dashboard endpoints.

Every mutating action (run start/stop, sync export, system reboot, scheduler
install/remove, settings change, snapshot restore) appends a JSON line to
``~/.local/state/ascendo/audit.log`` so the operator can answer
"what was done on this host, when, by whom" without reading FastAPI logs.

The file lives outside the repo on purpose — audit data is per-host and
per-user.  Format:

    {"ts":"2026-04-30T08:23:11Z","action":"run.start","actor":"127.0.0.1",
     "details":{"profile":"safe","dry_run":false}}

Best-effort: failures to write the audit log are swallowed so a full disk
or a permission glitch never stalls a user-facing action.
"""
from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
from typing import Any


def _audit_path() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    p = Path(base) / "ascendo" / "audit.log"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def log(action: str, *, actor: str | None = None, details: dict[str, Any] | None = None) -> None:
    """Append a single audit record. Never raises."""
    try:
        record = {
            "ts": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "action": action,
            "actor": actor or os.environ.get("USER") or "?",
            "details": details or {},
        }
        with _audit_path().open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass


def tail(limit: int = 200) -> list[dict[str, Any]]:
    """Read the most recent ``limit`` audit records (newest last)."""
    p = _audit_path()
    if not p.exists():
        return []
    try:
        lines = p.read_text(encoding="utf-8").splitlines()[-limit:]
    except Exception:
        return []
    out: list[dict[str, Any]] = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out
