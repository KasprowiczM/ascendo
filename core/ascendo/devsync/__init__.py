"""GitHub + cloud overlay sync.

GitHub holds source code. Proton/iCloud/GoogleDrive/etc. hold a private
overlay (`.env.local`, `.dev_sync_config.json`, agent keys, host-specific
configs) that's gitignored.

- core.py — rclone wrapper, provider-agnostic
- manifest.py — restore-manifest.json parser (declares overlay vs reproducible)
- safety.py — cleanup_protected_patterns, sha256 helpers
- providers/{proton,gdrive,icloud,onedrive,mega}.py — provider configs

Inherits from existing dev-sync code in Ascendo/dev-sync/ and
Ascendo/dev_sync_*.py (already cross-OS-ready Python).
"""
