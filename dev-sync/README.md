# Dev Sync

`dev-sync` backs up the private overlay for `Ascendo` while GitHub remains the source of truth for tracked project files.

## Commands

| Linux/macOS | Windows PowerShell | Purpose |
|---|---|---|
| `bash dev-sync/provider_setup.sh` | `.\dev-sync\provider_setup.ps1` or `.\dev-sync-provider-setup.ps1` | Create `.dev_sync_config.json` for Proton/rclone or a local provider path. |
| `bash dev-sync-export.sh --dry-run --verbose` | `.\dev-sync-export.ps1 --dry-run --verbose` | Show Git-ignored private overlay files selected for provider backup. |
| `bash dev-sync-export.sh` | `.\dev-sync-export.ps1` | Copy selected Git-ignored private overlay files to the provider. |
| `bash dev-sync-restore-preflight.sh` | `.\dev-sync-restore-preflight.ps1` | Check whether a fresh clone is ready for overlay restore. |
| `bash dev-sync-import.sh --dry-run --verbose` | `.\dev-sync-import.ps1 --dry-run --verbose` | Preview restore from provider. |
| `bash dev-sync-import.sh` | `.\dev-sync-import.ps1` | Restore private overlay files, skipping Git-tracked files for local and rclone providers. |
| `bash dev-sync-verify-git.sh` | `.\dev-sync-verify-git.ps1` | Verify tracked files are clean and pushed to upstream. |
| `bash dev-sync-verify-full.sh` | `.\dev-sync-verify-full.ps1` | Verify Git-tracked files plus provider overlay reconstruct local state. |
| `bash dev-sync-prune-excluded.sh` | `.\dev-sync-prune-excluded.ps1` | Plan stale/generated provider cleanup. |
| `bash dev-sync-purge-quarantine.sh --apply` | `.\dev-sync-purge-quarantine.ps1 --apply` | Permanently delete reviewed quarantine. |

## Provider Policy

Use `rclone` for Proton Drive on Ubuntu unless you have an explicit local provider folder. Export uses copy semantics, never destructive mirror semantics. Cleanup is plan-first and quarantine-first.

Only Git-ignored files are eligible for provider export. Nonignored untracked
files are treated as project content and should be committed to GitHub or
explicitly ignored.

## Private Overlay Examples

- `.env.local`
- `.dev_sync_config.json`
- local key files such as `github`
- ignored local Claude settings

## Rebuildable Files Not Synced

- `APPS.md`
- `logs/`
- `config/*.bak_*`
- `.codex.local/tmp/`
- dependency, build, test, and cache outputs

See `config/dev-sync-excludes.txt` for project-specific exclusions.

rclone exports write `.dev_sync_manifest.json` to the remote project folder.
rclone imports stage remote content in a temporary directory first, then filter
through the manifest/exclusion policy before copying files into the repo.

## Fresh-Clone Restore

Use [RESTORE_MANIFEST.md](RESTORE_MANIFEST.md) as the bounded recovery checklist.
Run `bash dev-sync-restore-preflight.sh` after provider setup and before
`bash dev-sync-import.sh` to verify config, provider availability, restore docs,
and tracked-tree cleanliness.

On Windows, use the PowerShell equivalents:

```powershell
.\dev-sync\provider_setup.ps1
.\scripts\restore-from-proton.ps1 --dry-run --verbose
.\scripts\restore-from-proton.ps1 --verbose
```
