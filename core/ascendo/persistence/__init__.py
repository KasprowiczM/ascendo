"""SQLite-based persistence layer — runs, phase results, package state.

- db.py — SQLite (WAL mode) connection management
- migrations/ — schema migrations (versioned files)
- repositories/ — RunRepo, PhaseRepo, PackageStateRepo
- paths.py — XDG-compliant per-OS data dirs:
    Linux:   ~/.local/share/ascendo/db.sqlite3
    macOS:   ~/Library/Application Support/Ascendo/db.sqlite3
    Windows: %LOCALAPPDATA%\\Ascendo\\db.sqlite3

Schema includes: runs, phase_results, package_state, snapshot_metadata,
plugin_state, audit_log.
"""
