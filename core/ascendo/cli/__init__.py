"""Typer-based CLI: `ascendo <command>`.

Subcommands (planned):
- ascendo run             — execute update cycle
- ascendo apps            — manage tracked apps
- ascendo plugin          — list/install/run plugins
- ascendo schedule        — manage periodic runs
- ascendo snapshot        — list/create/restore system snapshots
- ascendo settings        — read/write user config
- ascendo dashboard       — launch FastAPI backend (background)
- ascendo health          — system status check
- ascendo setup           — first-run interactive wizard

The thin bash launcher `bin/ascendo` just delegates to
`python -m ascendo.cli.main`.
"""
