"""Configuration loader (TOML + .list files).

- loader.py — categories.toml, profiles.toml, hosts.toml
- lists.py — *.list parsers (apt-packages.list, npm-globals.list, ...)
- exclusions.py — exclusions.list (cat:pkg or cat:* opt-out)
- env.py — ENV variable overrides (ASCENDO_*)

User-facing config files live in `config/` at repo root. Adapter-specific
overrides go to `adapters/<os>/config/`.
"""
