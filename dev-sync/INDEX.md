# Dev Sync File Index

- `dev_sync_core.py` - config, provider abstraction, path safety, Git classification, export/import, verification.
- `dev_sync_export.py` - CLI for private overlay export.
- `dev_sync_import.py` - CLI for overlay restore.
- `dev_sync_restore_preflight.py` - CLI for fresh-clone restore readiness checks.
- `dev_sync_verify_git.py` - checks clean tracked state and upstream push status.
- `dev_sync_verify_full.py` - checks reconstruction from GitHub plus provider overlay.
- `dev_sync_prune_excluded.py` - creates and applies quarantine plans for stale/generated provider files.
- `dev_sync_purge_quarantine.py` - deletes reviewed quarantine only with `--apply`.
- `dev_sync_proton_status.py` - macOS File Provider status helper; not the primary Ubuntu/rclone verification path.
- `provider_setup.sh` - interactive provider config writer.
- `dev-sync-*.sh` - shell wrappers for Python CLIs.
- `provider_setup.ps1` - PowerShell provider setup wrapper.
- `dev-sync-*.ps1` - PowerShell wrappers for Python CLIs.
- `Invoke-DevSyncPython.ps1` - shared PowerShell launcher for Python backend scripts.
- `RESTORE_MANIFEST.md` - bounded fresh-clone restore checklist and overlay scope.
- `../config/restore-manifest.json` - tracked high-level contract for private overlay and rebuildable files.
