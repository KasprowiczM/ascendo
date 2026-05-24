"""Settings backup/restore — bundle config into a portable tar.gz."""
from __future__ import annotations

import io
import json
import os
import tarfile
import time
from pathlib import Path
from typing import Any

from . import config

# Paths inside the repo or user dirs that we bundle.
_REPO_RELATIVE = (
    "config/apt-packages.list",
    "config/apt-repos.list",
    "config/snap-packages.list",
    "config/brew-formulas.list",
    "config/brew-casks.list",
    "config/npm-globals.list",
    "config/pip-packages.list",
    "config/pipx-packages.list",
    "config/flatpak-packages.list",
    "config/exclusions.list",
    "config/host-profiles",
    "config/restore-manifest.json",
)


def _user_paths() -> list[Path]:
    base = Path(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"))
    return [
        base / "ascendo" / "settings.json",
        base / "ascendo" / "onboarded.json",
        base / "ascendo" / "lang",
    ]


def export_bundle() -> bytes:
    """Produce a tar.gz blob of all config-relevant artifacts."""
    repo = config.repo_root()
    buf = io.BytesIO()
    manifest = {"created_at": time.time(), "host": os.uname().nodename, "items": []}
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for rel in _REPO_RELATIVE:
            p = repo / rel
            if not p.exists():
                continue
            tar.add(str(p), arcname=f"repo/{rel}")
            manifest["items"].append(f"repo/{rel}")
        for p in _user_paths():
            if not p.exists():
                continue
            arc = f"user/{p.relative_to(p.parent.parent.parent)}"
            tar.add(str(p), arcname=arc)
            manifest["items"].append(arc)
        # Manifest blob.
        m_bytes = json.dumps(manifest, indent=2).encode("utf-8")
        info = tarfile.TarInfo(name="MANIFEST.json")
        info.size = len(m_bytes)
        tar.addfile(info, io.BytesIO(m_bytes))
    return buf.getvalue()


def import_bundle(blob: bytes) -> dict[str, Any]:
    """Restore a bundle. Files in repo/ are written under the repo root, files
    in user/ are written under $HOME/.config (and xdg-config-home equivalents).
    Existing files get a `.bak_<ts>` suffix instead of overwriting."""
    repo = config.repo_root()
    home = Path(os.path.expanduser("~"))
    cfg_base = Path(os.environ.get("XDG_CONFIG_HOME") or (home / ".config"))
    restored: list[str] = []
    skipped: list[str] = []
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
        for member in tar.getmembers():
            if not member.isfile():
                continue
            name = member.name
            if name == "MANIFEST.json":
                continue
            if name.startswith("repo/"):
                target = repo / name[len("repo/"):]
            elif name.startswith("user/"):
                # user/<dirname>/... → $XDG_CONFIG_HOME/<dirname>/...
                target = cfg_base / name[len("user/"):]
            else:
                skipped.append(name)
                continue
            # Path-traversal hardening
            try:
                target.resolve().relative_to(repo.resolve()) if name.startswith("repo/") else target.resolve()
            except Exception:
                skipped.append(name); continue
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                bak = target.with_suffix(target.suffix + f".bak_{int(time.time())}")
                target.rename(bak)
            with tar.extractfile(member) as src:  # type: ignore[union-attr]
                target.write_bytes(src.read())
            restored.append(name)
    return {"ok": True, "restored": restored, "skipped": skipped}
