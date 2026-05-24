# Dev Sync Quick Start

1. Configure provider:

```sh
bash dev-sync/provider_setup.sh
```

```powershell
.\dev-sync\provider_setup.ps1
```

2. Preview what would be copied:

```sh
bash dev-sync-export.sh --dry-run --verbose
```

```powershell
.\dev-sync-export.ps1 --dry-run --verbose
```

3. Export private overlay:

```sh
bash dev-sync-export.sh
```

```powershell
.\dev-sync-export.ps1
```

4. Verify GitHub and provider coverage:

```sh
bash dev-sync-verify-git.sh
bash dev-sync-verify-full.sh
```

```powershell
.\dev-sync-verify-git.ps1
.\dev-sync-verify-full.ps1
```

5. Restore on a new machine:

```sh
git clone <repo-url>
cd Ascendo
bash dev-sync/provider_setup.sh
bash dev-sync-restore-preflight.sh
bash dev-sync-import.sh --dry-run --verbose
bash dev-sync-import.sh
bash dev-sync-verify-full.sh
```

Or use the top-level recovery wrapper:

```bash
bash scripts/restore-from-proton.sh --dry-run --verbose
bash scripts/restore-from-proton.sh --verbose
```

Windows:

```powershell
.\scripts\restore-from-proton.ps1 --dry-run --verbose
.\scripts\restore-from-proton.ps1 --verbose
```

Do not use `rclone sync` or manual broad deletion for this overlay. Use the provided prune/quarantine scripts.

The restore scope and rebuildable exclusions are listed in `dev-sync/RESTORE_MANIFEST.md`.
