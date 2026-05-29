## 2026-05-29 — Windows cross-platform hardening & parity validation

Reviewed run:
``text
Windows Full Upgrade loop
id: latest
status: PASS E validation & Web Updater fixes
duration: ~2-5m
``

### Findings & fixes shipped

| Finding | Root cause | Fix |
|---|---|---|
| Proton Mail / Squirrel installers exit code -1 | Web installer failed because Update.exe (Squirrel updater) locked the installation directory in the background. | Added "Update" to kill_processes for proton-mail, proton-drive, and opencode in web_apps.toml to kill ghost updaters before launching the silent install. |
| Overlapping package updates across managers | Same app installed via multiple sources (e.g., Claude via npm vs winget) could be double-updated. | Implemented core/ascendo/orchestrator/deduplicator.py and pp_sources.toml to deduplicate planned items, prioritizing recommended install sources based on explicit app tiers. |
| PASS E Hardening: Windows chats.db ACLs | Missing SDDL protection for SQLite DB. | Verified already implemented in persistence.py using ctypes (D:P(A;;FA;;;OW)(A;;FA;;;SY)). |
| PASS E Hardening: UAC Env fail-fast | UAC children don't inherit overrides safely on Windows. | Verified already implemented in elevation.py via NotImplementedError("UAC elevation does not support environment variable overrides"). |

### Result
The Windows platform is fully verified and ready for production testing on Ubuntu and macOS.

