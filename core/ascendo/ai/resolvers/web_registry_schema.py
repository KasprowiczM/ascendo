"""web_registry_schema: machine-aware web-config context for the AI loop.

Injects three things the AI needs to actually make web coverage work
on THIS machine (C.1):

1. The CORRECT ``ascendo-web-apps/v2`` schema with real field names +
   a per-handler example (the old version of this resolver emitted
   ``schema = 2`` / ``[[apps]]`` / ``[apps.github_release]`` which do
   not exist — an AI following it wrote an invalid file).
2. The user's CURRENT ``~/.config/ascendo/web_apps.toml`` (so the AI
   sees what is already configured), truncated.
3. THIS machine's uncovered apps (the latest run's Action-required
   list) — the cross-machine key: the AI sees exactly what is missing
   locally.

Fail-soft throughout: any error degrades to the schema doc alone.
"""

from __future__ import annotations

import os
from pathlib import Path

_SCHEMA_DOC = '''## Web app override file: ~/.config/ascendo/web_apps.toml

Add entries here to make Ascendo cover an app it does not cover yet.
User entries override the shipped registry by `bundle_id`. Correct v2
structure (note: `schema = "ascendo-web-apps/v2"`, table is `[[app]]`,
per-handler sub-table is `[app.<handler>]`):

```toml
schema = "ascendo-web-apps/v2"

[[app]]
slug = "my-app"                       # ^[a-z0-9-]+$
bundle_id = "com.example.MyApp"       # macOS CFBundleIdentifier
display_name = "My App"
handler = "github_dmg"                # sparkle | github_dmg | release_feed
                                      # | keystone | squirrel | builtin
                                      # | msupdate | docker | omaha
enabled = true

# github_dmg keys are FLAT, directly under [[app]] (NOT a subtable):
github_repo = "owner/repo"            # when handler = "github_dmg"
asset_pattern = "MyApp-[0-9.]+-arm64\\\\.dmg$"
arch = "arm64"                        # or "universal"

# sparkle keys are also flat:    appcast_url = "https://.../appcast.xml"
# keystone:                      ksadmin_product_id = "com.x.Y"

# release_feed / msupdate / omaha use a [app.<handler>] SUBTABLE:
[app.release_feed]                    # only when handler = "release_feed"
url = "https://.../latest.json"
version_path = "version"
# download_path = "files[0].url"      # set => Tier-A silent install;
                                      # omit => Tier-B (user opens app)
# version_regex = "^v(.+)$"  version_replace = "\\\\1"  format = "json"
# [app.msupdate]  app_id = "MSWD2019"
# [app.omaha]     endpoint = "https://.../update2"
#                 appid = "..."  protocol = "3.0"  tag = "stable"
```
Working examples live in `adapters/macos/config/web_apps.toml`.'''


def _user_registry_path() -> Path:
    override = os.environ.get("ASCENDO_WEB_USER_REGISTRY_PATH")
    if override:
        return Path(override).expanduser()
    return Path("~/.config/ascendo/web_apps.toml").expanduser()


def _current_overrides() -> str:
    try:
        p = _user_registry_path()
        if not p.is_file():
            return "### Your current overrides\n\n(none yet — no user override file)\n"
        body = p.read_text(encoding="utf-8")
        if len(body) > 1500:
            body = body[:1500] + "\n… (truncated)\n"
        return f"### Your current overrides ({p})\n\n```toml\n{body}\n```\n"
    except Exception:  # noqa: BLE001
        return "### Your current overrides\n\n(none yet — could not read)\n"


def _collect_uncovered(runs_dir) -> list:
    """The latest run's Action-required items = this machine's gap."""
    try:
        from ...orchestrator.report import collect_action_required

        rd = Path(runs_dir)
        if not rd.is_dir():
            return []
        run_dirs = [d for d in rd.iterdir() if d.is_dir()]
        if not run_dirs:
            return []
        latest = max(run_dirs, key=lambda d: d.stat().st_mtime)
        return collect_action_required(latest)
    except Exception:  # noqa: BLE001
        return []


def _uncovered_section(runs_dir) -> str:
    items = _collect_uncovered(runs_dir)
    if not items:
        return (
            "### Apps not silently covered on THIS machine\n\n"
            "(none detected in the latest run — run an apply first, or "
            "the machine is fully covered)\n"
        )
    lines = [
        "### Apps not silently covered on THIS machine",
        "",
        "These are the candidates to write override entries for:",
        "",
    ]
    for a in items[:25]:
        ver = ""
        if getattr(a, "current", "") or getattr(a, "candidate", ""):
            ver = f" ({a.current or '?'} → {a.candidate or '?'})"
        lines.append(f"- **{a.name}** (`{a.slug}`){ver} — reason: {a.reason}")
    return "\n".join(lines) + "\n"


def resolve(*, adapter, inventory_db, runs_dir) -> tuple[str, int]:
    parts = [_SCHEMA_DOC, _current_overrides(), _uncovered_section(runs_dir)]
    return "\n\n".join(parts), 5
