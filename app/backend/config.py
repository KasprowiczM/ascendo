"""Runtime configuration for the dashboard backend."""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path


def repo_root() -> Path:
    env = os.environ.get("UA_REPO_ROOT")
    if env:
        return Path(env).resolve()
    # backend lives at <repo>/app/backend/
    return Path(__file__).resolve().parents[2]


def db_path() -> Path:
    env = os.environ.get("UA_DB_PATH")
    if env:
        return Path(env).resolve()
    base = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share")))
    p = base / "ascendo" / "history.db"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


@dataclass(frozen=True)
class CategoryDef:
    id: str
    display_name: str
    privilege: str
    risk: str
    manual_confirm: bool
    depends_on: list[str]
    config_files: list[str]
    timeout_sec: int
    phases: list[str]


@dataclass(frozen=True)
class ProfileDef:
    id: str
    description: str
    phases: list[str]
    categories: list[str]
    require_approval_for: list[str]


def load_categories() -> dict[str, CategoryDef]:
    path = repo_root() / "config" / "categories.toml"
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    out: dict[str, CategoryDef] = {}
    for cid, body in raw.items():
        out[cid] = CategoryDef(
            id=cid,
            display_name=body.get("display_name", cid),
            privilege=body.get("privilege", "user"),
            risk=body.get("risk", "low"),
            manual_confirm=bool(body.get("manual_confirm", False)),
            depends_on=list(body.get("depends_on", [])),
            config_files=list(body.get("config_files", [])),
            timeout_sec=int(body.get("timeout_sec", 900)),
            phases=list(body.get("phases", ["check", "plan", "apply", "verify", "cleanup"])),
        )
    return out


def load_profiles() -> dict[str, ProfileDef]:
    path = repo_root() / "config" / "profiles.toml"
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    out: dict[str, ProfileDef] = {}
    for pid, body in raw.items():
        out[pid] = ProfileDef(
            id=pid,
            description=body.get("description", ""),
            phases=list(body.get("phases", [])),
            categories=list(body.get("categories", [])),
            require_approval_for=list(body.get("require_approval_for", [])),
        )
    return out


def runs_dir() -> Path:
    return repo_root() / "logs" / "runs"
