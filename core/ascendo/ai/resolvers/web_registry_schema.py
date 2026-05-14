"""web_registry_schema: TOML schema documentation (no actual values).

Used for the "How do I add a custom web app?" prompt-library entry.
Reading the real ~/.config/ascendo/web_apps.toml would leak user data
into the LLM context; this just describes the schema.
"""
from __future__ import annotations

_SCHEMA_DOC = """## Web app registry schema (override file: ~/.config/ascendo/web_apps.toml)

The user can override or extend Ascendo's curated web_apps.toml with
their own entries. Top-level structure:

```toml
schema = 2

[[apps]]
slug = "my-app"                      # short identifier; lowercase
bundle_id = "com.example.myapp"      # macOS bundle id (or windows_uninstall_key on Windows)
display_name = "My App"
handler = "github_release"           # one of: github_release, release_feed, sparkle,
                                     #         keystone, squirrel, builtin, msupdate,
                                     #         docker, omaha, appimage
enabled = true

[apps.github_release]                # required when handler = "github_release"
repo = "owner/repo"
asset_pattern = "MyApp-[0-9.]+-arm64\\\\.dmg$"
```

Other handler-specific tables follow the same `[apps.<handler>]` shape.
See `adapters/<os>/config/web_apps.toml` in the repo for working examples.
"""


def resolve(*, adapter, inventory_db, runs_dir) -> tuple[str, int]:
    return _SCHEMA_DOC, 5
