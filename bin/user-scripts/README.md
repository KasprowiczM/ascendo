# bin/user-scripts/

Friendly one-word shims around the verbose `python -m ascendo …` CLI.
The installer drops these on `PATH` (POSIX: `~/.local/bin`; Windows:
`%LOCALAPPDATA%\Microsoft\WindowsApps`) so operators can type
`ascendo_update` instead of remembering the full incantation.

Every shim resolves the install root via `$ASCENDO_HOME` (or
`%LOCALAPPDATA%\Ascendo\src` on Windows). They contain no business
logic — just argument forwarding and venv discovery.

## Available everywhere

| Command                  | What it does                                                    |
|--------------------------|-----------------------------------------------------------------|
| `ascendo_update`         | `git pull --ff-only` + refresh editable installs                 |
| `ascendo_start_web`      | Start the FastAPI dashboard in the background                    |
| `ascendo_stop_web`       | Stop any running dashboard process                               |
| `ascendo_restart_web`    | Stop then start the dashboard                                    |
| `ascendo_start_desktop`  | Launch the Tauri desktop shell (macOS / Windows)                 |
| `ascendo_stop_desktop`   | Terminate the Tauri shell                                        |
| `ascendo_doctor`         | `ascendo doctor` plus runtime + log-dir + DB-integrity checks    |
| `ascendo_maintenance`    | High-level ops: `full`, `quick`, `dry-run`, `category=<name>`,   |
|                          | `rebuild-inventory`, `check-errors`                              |

## Dev edition only (`dev/` subdir)

| Command         | What it does                                  |
|-----------------|-----------------------------------------------|
| `ascendo_sync`  | Push the dev-sync overlay to Proton via rclone |
| `ascendo_push`  | `git push` from `$ASCENDO_HOME`                |

## Conventions

- POSIX scripts have no extension and start with `#!/usr/bin/env bash`
  + `set -euo pipefail`. Marked executable.
- PowerShell scripts share the base name + `.ps1`. Use
  `$ErrorActionPreference = 'Stop'` for fail-fast semantics.
- Every shim is idempotent and forwards extra args to the underlying
  command (`exec "$@"` / `& $cmd @args`).
- They never write to disk under their own name — all logs / state live
  under `~/.ascendo/` (POSIX) or `%USERPROFILE%\.ascendo\` (Windows).

## Examples

```bash
# Quick health snapshot
ascendo_doctor

# Update Ascendo itself (one-liner replacement for update.sh)
ascendo_update

# Start the dashboard, open browser, stop later
ascendo_start_web
open http://127.0.0.1:8765        # macOS
ascendo_stop_web

# Run a full update sweep
ascendo_maintenance full

# Inspect a specific category
ascendo_maintenance category=brew

# Rebuild the inventory cache from scratch
ascendo_maintenance rebuild-inventory

# Find recent failures
ascendo_maintenance check-errors
```
