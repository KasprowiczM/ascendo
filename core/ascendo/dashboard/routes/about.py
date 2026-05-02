"""Platform-aware About / release-notes endpoints.

Replaces the legacy ``spa_stubs.about_stub`` placeholder. Two endpoints:

- ``GET /about`` — same shape as before (``name``, ``version``, ``host``, …)
  but now reports the OS via ``platform`` so the SPA can pick which Help
  body and release-notes feed to render.
- ``GET /about/release-notes`` — returns the platform-tagged section of
  ``CHANGELOG.md``. Matching is by header: any ``## [version]`` block whose
  body mentions the platform name (case-insensitive) OR is tagged with
  ``[Windows]`` / ``[Linux]`` / ``[macOS]`` in its heading line surfaces;
  un-tagged entries are treated as cross-platform and always shown.

The CHANGELOG is the single source of truth — no separate per-OS files,
no GitHub API call. CHANGELOG.md ships in every install, so this works
offline and in air-gapped environments.
"""
from __future__ import annotations

import logging
import platform as _platform
import re
import sys
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request

_log = logging.getLogger(__name__)
router = APIRouter(tags=["about"])


# ── Helpers ──────────────────────────────────────────────────────────────


def _detect_platform() -> str:
    """Return ``"windows"`` / ``"linux"`` / ``"macos"`` / ``"unknown"``."""
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    if sys.platform.startswith("linux"):
        return "linux"
    return "unknown"


def _repo_root() -> Path:
    """Walk up from this file until we find CHANGELOG.md or pyproject.toml.

    This file lives at ``core/ascendo/dashboard/routes/about.py``; the
    repo root is 4 levels up. Defensive: fall back to walk if the layout
    changes.
    """
    here = Path(__file__).resolve()
    for parent in [here.parent.parent.parent.parent.parent, *here.parents]:
        if (parent / "CHANGELOG.md").is_file() or (parent / "pyproject.toml").is_file():
            return parent
    return Path.cwd()


_VERSION_HEADER = re.compile(r"^##\s+\[(?P<version>[^\]]+)\](?:\s*[—-]\s*(?P<rest>.*))?$")
_PLATFORM_TAG = re.compile(r"\b(windows|linux|macos|mac os|ubuntu|debian)\b", re.IGNORECASE)


def _parse_changelog(text: str) -> list[dict[str, Any]]:
    """Split CHANGELOG.md into ``[{version, date, body, platforms}, ...]``.

    ``platforms`` is the inferred set; an entry whose body mentions
    "Windows" maps to ``["windows"]``, etc. Multi-platform entries get
    multiple tags. An entry with no platform mentions is treated as
    cross-platform (``["windows", "linux", "macos"]``).
    """
    entries: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    body_lines: list[str] = []

    def _flush() -> None:
        if current is None:
            return
        body = "\n".join(body_lines).strip()
        platforms = sorted({m.group(1).lower() for m in _PLATFORM_TAG.finditer(body + " " + current.get("rest", ""))})
        # Normalise a couple of aliases.
        platforms = [
            "macos" if p in {"mac os", "macos"} else
            "linux" if p in {"ubuntu", "debian"} else
            p
            for p in platforms
        ]
        platforms = sorted(set(platforms))
        if not platforms:
            platforms = ["windows", "linux", "macos"]
        entries.append({**current, "body": body, "platforms": platforms})

    for line in text.splitlines():
        m = _VERSION_HEADER.match(line)
        if m:
            _flush()
            current = {
                "version": m.group("version"),
                "rest": m.group("rest") or "",
            }
            body_lines = []
        elif current is not None:
            body_lines.append(line)
    _flush()
    return entries


def _filter_for_platform(entries: list[dict[str, Any]], target: str) -> list[dict[str, Any]]:
    """Return only entries that apply to ``target`` (windows / linux / macos)."""
    if target not in {"windows", "linux", "macos"}:
        return entries
    return [e for e in entries if target in e["platforms"]]


# ── Endpoints ────────────────────────────────────────────────────────────


@router.get("/about")
async def about(request: Request) -> dict[str, Any]:
    """About-tab payload (replaces spa_stubs.about_stub)."""
    from ... import __version__

    adapter = getattr(request.app.state, "adapter", None)
    host_dict: dict[str, Any] = {
        "hostname": _platform.node(),
        "os": sys.platform,
        "arch": _platform.machine(),
    }
    if adapter is not None:
        try:
            h = adapter.detect_host()
            host_dict = {
                "hostname": h.hostname,
                "os": h.os.value if hasattr(h.os, "value") else str(h.os),
                "os_version": h.os_version,
                "arch": h.arch,
                "user": h.user,
            }
        except Exception:  # noqa: BLE001
            _log.debug("detect_host() failed", exc_info=True)

    return {
        "name": "Ascendo",
        "tagline": "Unified updates. Every app. One click.",
        "version": __version__,
        "platform": _detect_platform(),
        "python": sys.version.split()[0],
        "host": host_dict.get("hostname"),
        "host_info": host_dict,
        "distro": host_dict.get("os"),
        "kernel": host_dict.get("os_version"),
        "arch": host_dict.get("arch"),
        "release_notes_md": "",  # legacy; SPA prefers /about/release-notes now
        "release_notes_url": "/about/release-notes",
    }


@router.get("/about/release-notes")
async def release_notes(platform: str | None = None, limit: int = 10) -> dict[str, Any]:
    """Platform-tagged release-notes from CHANGELOG.md.

    Query params:
        platform: windows | linux | macos. Defaults to the running OS.
        limit:    number of entries to return (newest-first). Default 10.
    """
    target = (platform or _detect_platform()).lower()
    repo = _repo_root()
    changelog = repo / "CHANGELOG.md"
    if not changelog.is_file():
        return {
            "platform": target,
            "entries": [],
            "source": str(changelog),
            "error": "CHANGELOG.md not found",
        }
    try:
        text = changelog.read_text(encoding="utf-8")
    except OSError as exc:
        return {
            "platform": target,
            "entries": [],
            "source": str(changelog),
            "error": f"could not read CHANGELOG: {exc}",
        }
    entries = _parse_changelog(text)
    filtered = _filter_for_platform(entries, target)[:max(1, min(limit, 50))]
    return {
        "platform": target,
        "entries": filtered,
        "source": str(changelog.relative_to(repo)) if changelog.is_relative_to(repo) else str(changelog),
        "total_in_changelog": len(entries),
    }


__all__ = ["router"]
