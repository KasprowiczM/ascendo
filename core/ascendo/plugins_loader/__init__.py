"""Plugin discovery, validation, and dispatch.

- discovery.py — scan plugins/ + ~/.config/ascendo/plugins/ + system locations
- manifest.py — Pydantic validator for ascendo-plugin/v1 schema
- dispatcher.py — invoke phase script with standard flags (passes JSON-out path)
- registry.py — in-memory plugin index (cached)

Plugin manifest spec: docs/architecture/0007-plugin-manifest-v1.md
Plugin author guide: docs/plugin-author-guide.md
"""
