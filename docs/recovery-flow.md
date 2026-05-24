# Recovery Flow

## Source Of Truth

| Class | Source |
|---|---|
| Git-tracked project files | GitHub |
| Private ignored overlay | dev-sync provider, usually Proton Drive through rclone |
| Generated local state | Regenerated locally |
| Machine-specific package state | Reconciled from `config/*.list` and local hardware checks |

`config/restore-manifest.json` is the tracked contract for the expected private
overlay and rebuildable files.

## Fresh Clone

```bash
git clone https://github.com/KasprowiczM/Ascendo.git
cd Ascendo
bash scripts/preflight.sh
bash dev-sync/provider_setup.sh
bash scripts/restore-from-proton.sh --dry-run --verbose
bash scripts/restore-from-proton.sh --verbose
bash scripts/bootstrap.sh --skip-sync
bash scripts/verify-state.sh
```

Windows PowerShell equivalent for the private overlay part:

```powershell
git clone https://github.com/KasprowiczM/ascendo.git D:\Dev_Env\Ascendo
cd D:\Dev_Env\Ascendo
.\dev-sync\provider_setup.ps1
.\scripts\restore-from-proton.ps1 --dry-run --verbose
.\scripts\restore-from-proton.ps1 --verbose
.\dev-sync-verify-full.ps1
```

## Existing Machine After `git pull`

```bash
git pull --ff-only
bash scripts/preflight.sh
bash dev-sync-export.sh --dry-run --verbose
bash dev-sync-export.sh
bash scripts/verify-state.sh
```

Windows private-overlay export:

```powershell
git pull --ff-only
.\dev-sync-export.ps1 --dry-run --verbose
.\dev-sync-export.ps1
.\dev-sync-verify-full.ps1
```

## Safety Rules

- Import never treats Proton/rclone as authoritative for Git-tracked files.
- rclone import stages remote content in a temporary directory, filters through
  manifest/exclude policy, and then copies selected relative paths.
- Local Proton Drive exports use rsync without permission/owner/group metadata,
  because cloud-synced mounts can reject chmod/chown metadata operations while
  still accepting content updates.
- Provider cleanup remains quarantine-first through `dev-sync-prune-excluded.sh`;
  no broad `rclone sync --delete` is used.
- `.dev_sync_config.json` is private. A fresh clone may need
  `dev-sync/provider_setup.sh` before remote import is possible.
