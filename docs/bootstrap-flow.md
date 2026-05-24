# Bootstrap Flow

## Goal

Bring a fresh Ubuntu clone to a known, auditable state without committing
machine-local secrets or generated files.

## Flow

```text
git clone
  -> scripts/preflight.sh
  -> dev-sync/provider_setup.sh
  -> scripts/restore-from-proton.sh --dry-run
  -> scripts/restore-from-proton.sh
  -> scripts/bootstrap.sh --skip-sync
  -> scripts/verify-state.sh
```

## Responsibilities

| Step | Responsibility |
|---|---|
| `scripts/preflight.sh` | Read-only OS, command, Git, manifest, and unattended-upgrades visibility checks. |
| `dev-sync/provider_setup.sh` | Creates local `.dev_sync_config.json`; this file is private and gitignored. |
| `scripts/restore-from-proton.sh` | Calls dev-sync preflight/import/verify for private overlay only. |
| `setup.sh --non-interactive` | Reconciles package managers from `config/*.list`. |
| `scripts/verify-state.sh` | Runs syntax, Python tests, Git verification, dev-sync verification, and systemd template verification. |

## Concurrency

`update-all.sh`, `setup.sh`, `scripts/bootstrap.sh`, and
`scripts/restore-from-proton.sh` use the shared project lock from
`lib/common.sh`. This prevents timer/manual/bootstrap runs from colliding on
APT/dpkg, Snap, Flatpak, Homebrew, inventory, and provider state.

The lock first uses `XDG_RUNTIME_DIR` when writable and falls back to `/tmp`.
If a shell session is interrupted, verify no process is active before removing a
stale `/tmp/ascendo.lock` file.
