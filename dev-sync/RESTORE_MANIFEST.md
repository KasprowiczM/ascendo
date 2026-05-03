# Fresh-Clone Restore Manifest

GitHub is the source of truth for tracked project files. The provider overlay
stores only private, Git-ignored local state needed to make a fresh clone usable
on this machine.

## Restore Order

1. Clone the GitHub repository.
2. Configure the private overlay provider:

```sh
bash dev-sync/provider_setup.sh
```

3. Run the preflight check:

```sh
bash dev-sync-restore-preflight.sh
```

4. Preview and import the private overlay:

```sh
bash dev-sync-import.sh --dry-run --verbose
bash dev-sync-import.sh
```

5. Verify reconstruction:

```sh
bash dev-sync-verify-full.sh
```

Top-level wrapper:

```sh
bash scripts/restore-from-proton.sh --dry-run --verbose
bash scripts/restore-from-proton.sh --verbose
```

Windows PowerShell wrapper:

```powershell
.\scripts\restore-from-proton.ps1 --dry-run --verbose
.\scripts\restore-from-proton.ps1 --verbose
```

## Provider Overlay Scope

Expected private overlay examples:
- `.dev_sync_config.json`
- `.env.local`
- `github` and `github.pub`
- local agent/editor settings that are intentionally Git-ignored

Never restore tracked project files from the provider overlay. Import skips
tracked files for local and rclone providers; GitHub must provide them. rclone
export writes `.dev_sync_manifest.json`; rclone import stages remote content in
a temporary directory, applies manifest/exclude policy, and then copies selected
paths into the project.

## Rebuildable Files

These files are intentionally not restored from the provider overlay:
- `APPS.md`
- `logs/`
- `config/*.bak_*`
- `.codex.local/tmp/`
- dependency, build, test, and cache outputs

Regenerate `APPS.md` with:

```sh
./scripts/update-inventory.sh
```

See `config/dev-sync-excludes.txt` for the exact exclusion list.
See `config/restore-manifest.json` for the tracked high-level recovery contract.
