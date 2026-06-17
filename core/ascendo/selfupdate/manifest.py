"""Fetch the published release manifest (what 'latest' is).

The manifest is a small JSON file committed to ``main`` and served from
raw.githubusercontent.com, so the check works offline-tolerant (fails
soft) and needs no GitHub API token / rate limit.

Override the URL with ``ASCENDO_UPDATE_MANIFEST_URL`` and the channel
with ``ASCENDO_CHANNEL`` (stable | beta). The manifest may carry
per-channel blocks; if absent we use the top-level ``core`` / ``shell``.

Manifest schema (ascendo/update-manifest/v1):
    {
      "schema": "ascendo/update-manifest/v1",
      "channel": "beta",
      "core":  {"version": "1.0.0b1", "notes_url": "...", "published_at": "..."},
      "shell": {"version": "0.0.7", "artifacts": {
          "macos_arm64": {"dmg_url": "...", "sha256": "..."},
          "windows_x64": {"msi_url": "..."},
          "linux_x64":   {"appimage_url": "..."}
      }},
      "channels": { "stable": {...}, "beta": {...} }   # optional override
    }
"""
from __future__ import annotations

import json
import os
import urllib.request

__all__ = ["DEFAULT_MANIFEST_URL", "fetch_manifest", "select_channel"]

DEFAULT_MANIFEST_URL = (
    "https://raw.githubusercontent.com/KasprowiczM/ascendo/main/releases/latest.json"
)


def manifest_url() -> str:
    return os.environ.get("ASCENDO_UPDATE_MANIFEST_URL", DEFAULT_MANIFEST_URL)


def channel() -> str:
    return (os.environ.get("ASCENDO_CHANNEL") or "stable").strip().lower()


def fetch_manifest(url: str | None = None, timeout: float = 8.0) -> dict:
    """Fetch + parse the manifest JSON. Raises on network / parse error."""
    target = url or manifest_url()
    req = urllib.request.Request(target, headers={"User-Agent": "ascendo-updater"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (https only)
        raw = resp.read().decode("utf-8")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("manifest is not a JSON object")
    return data


def select_channel(manifest: dict, chan: str | None = None) -> dict:
    """Return the {core, shell, channel} block for the requested channel.

    Falls back to the top-level core/shell if no per-channel override.
    """
    chan = (chan or channel()).lower()
    channels = manifest.get("channels") or {}
    if chan in channels and isinstance(channels[chan], dict):
        block = {**channels[chan]}
        block.setdefault("channel", chan)
        return block
    return {
        "channel": manifest.get("channel", chan),
        "core": manifest.get("core") or {},
        "shell": manifest.get("shell") or {},
    }
