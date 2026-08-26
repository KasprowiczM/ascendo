# Last Run Review

## 2026-08-26 — Ascendo 1.0.3 changelog

Full notes: [`docs/releases/v1.0.3.md`](releases/v1.0.3.md). Shipped on `main`
as `8abc614`. Tag target: `v1.0.3`. Root `CHANGELOG.md` is still `[Unreleased]`
→ `[1.0.1]` this session.

- Claude Desktop web-only (removed brew+web `id=claude` row).
- Ledger Live `files[dmg].url` + YAML walk for DMG vs zip SHA512.
- Brew cask downgrade guard (`ascendo_brew_cask_would_downgrade`).
- Native CLI installer timeout 180s, exit 124.
- `softwareupdate --include-config-data` on check/plan/apply/verify.
- Teams stays RC 95 GUI; iPad list UniFi / WiFiman / Picsart.

1.0.2 (`d2b2e86`): port macOS_updates brew/npm/MAU hardening; desktop version lockstep.

## 2026-08-25 — macOS_updates last completed run (ported in Ascendo 1.0.3)

Sister log: `macOS_updates/logs/update_all_20260825_095956.log` (14m 6s, exit 0, warnings). Ascendo had no newer local run.

| Finding | Root cause | Fix |
|---|---|---|
| Claude brew+web duplicate | `macos_app_sources.toml` brew=claude | Removed row |
| Ledger SHA mismatch | files[1] not always DMG | files[dmg].url |
| Brave cask 1.93 vs app 151 | no downgrade guard | ascendo_brew_cask_would_downgrade |
| Codex installer 124 | no timeout on curl\|sh | 180s timeout |
| softwareupdate omits XProtect | Tahoe needs flag | --include-config-data |
| Teams unchanged | msupdate cannot install TEAMS21 | notes + RC 95 |

## 2026-05-29 — macOS Ascendo Web Phase fixes

Reviewed run:
User reported issues during `ascendo` update phase for Chrome, Codex, Antigravity, Antigravity IDE, Perplexity, VSCode, and MS365.

### Findings & fixes shipped

| Finding | Root cause | Fix |
|---|---|---|
| `Chrome` apply hang | `hdiutil attach` hangs waiting for stdin on DMGs with EULAs (like Google Chrome). | Piped `yes` to `hdiutil attach` in `adapters/macos/lib/ascendo_web.sh` to auto-accept EULAs. Also ensured `download_url` is set to direct DMG for silent install. |
| `Chrome` stale update loop | Direct download URL provides older version than `versionhistory.googleapis.com` due to rollout caching, causing infinite fake update loops. | Removed `download_url` in `web_apps.toml` to revert Chrome to trigger-only mode. Keystone handles updates silently in background. |
| `Codex` downloading x64 on Apple Silicon | Python under Rosetta 2 reports `platform.machine()` as `x86_64`, tricking Sparkle hardware check. | Replaced `platform.machine()` check with `sysctl -n hw.optional.arm64` in `ascendo_web.sh` for reliable Apple Silicon detection regardless of Python environment. |
| `Ms365` not on host | `msupdate_check` failed to return global version when `app_id` was empty. | Updated `msupdate.sh` to fallback to global MAU `AutoUpdateVersion` when no specific `app_id` is passed. |
| `Ms365` silent update hang | `msupdate --install` takes a very long time in the background even if no updates are available, giving the perception of a hang. | Modified `msupdate_apply` to abandon the silent background install entirely. Now returns exit code 95 with instructions for the user to open the native Microsoft AutoUpdate app to install updates manually. |
| `Ms365` fake update loop | `check.sh` reads `CFBundleShortVersionString` (4.83) but `msupdate.sh` returned `AutoUpdateVersion` (4.83.26040910) leading to false positive updates. | Modified `msupdate_check` to return `CFBundleShortVersionString` when no updates are pending globally to properly match the inventory. |
| `Antigravity` / `Antigravity IDE` version missing | Upstream endpoint changed string format to `Fixed Version: X.Y.Z`. | Updated `version_regex` in `web_apps.toml` to support `(?:Stable\|Fixed)` prefix. |
| `Antigravity IDE` wrong version target | The IDE inherited the auto-updater endpoint from the main `antigravity` app, causing it to see 2.0.6 when it should be 2.0.3. | Added a specific `[app.release_feed]` definition for `antigravity-ide` pointing to its correct endpoint in `web_apps.toml`. |
| `VSCode` silent install missing | `download_path` was missing. Ascendo previously only triggered `open -a` (exit 95). | Added `download_path = "url"` to `web_apps.toml` and verified `_web_install_dmg` supports `.zip` unpacking natively. |
| `Perplexity` version missing | Cloudflare anti-bot blocks `curl` requests to the Sparkle `appcast.xml`. | Documented limitation: Perplexity requires manual update or a bypass strategy outside simple curl. |


## 2026-05-29 — Ubuntu Deduplication & Test Validation

Reviewed run:
```text
Ubuntu Dry-Run / Test suite validation
id: latest
status: PASS
duration: ~1m
```

### Findings & fixes shipped

| Finding | Root cause | Fix |
|---|---|---|
| Pytest `ModuleNotFoundError` | Multiple `tests/` directories shadowed each other when collected by Pytest default import behavior. | Added `--import-mode=importlib` to `pyproject.toml` configuration to isolate imports. |
| Deduplicator hardcoded for Windows | `deduplicator.py` assumed `winget` and Windows config file. | Added cross-platform OS detection, `ubuntu_app_sources.toml`, and native command generators for `apt`, `snap`, `flatpak`, `brew`, `npm`, `pip`. |
| `test_deduplicator` failure | Test assertions expected skipped uninstalls, but non-tty execution performs auto-uninstall planning. | Fixed assertions to expect `planned` uninstalls and perform in-memory validation of sidecars. |
| Outdated Smoke Test | `test_ubuntu_adapter_smoke.py` expected `UbuntuAdapter.source()` to be unimplemented (`None`), but it was implemented recently. | Fixed test to properly assert `UbuntuSource` singleton instead. |

### Result
Ubuntu core and CLI tests are completely green. Cross-source deduplication logic correctly generates appropriate `apt`, `snap`, and `flatpak` removal/installation instructions. Ready for macOS development.

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
| Overlapping package updates across managers | Same app installed via multiple sources (e.g., Claude via npm vs winget) could be double-updated. | Implemented core/ascendo/orchestrator/deduplicator.py and  pp_sources.toml to deduplicate planned items, prioritizing recommended install sources based on explicit app tiers. |
| PASS E Hardening: Windows chats.db ACLs | Missing SDDL protection for SQLite DB. | Verified already implemented in persistence.py using ctypes (D:P(A;;FA;;;OW)(A;;FA;;;SY)). |
| PASS E Hardening: UAC Env fail-fast | UAC children don't inherit overrides safely on Windows. | Verified already implemented in elevation.py via NotImplementedError("UAC elevation does not support environment variable overrides"). |

### Result
The Windows platform is fully verified and ready for production testing on Ubuntu and macOS.


## 2026-05-28 — Windows parity validation run

Reviewed run:

```text
id: f94a682e-c3ae-4945-8eae-5521902978a9
status: failed (1 winget apply failure on Mega.MEGASync, which cascaded to verify)
duration: ~6m
```

### Findings & fixes shipped

| Finding | Root cause | Fix |
|---|---|---|
| `winget` phase failed | `Mega.MEGASync` failed to apply with exit code `-1978335189` (`0x8A15002B` `APPINSTALLER_CLI_ERROR_NO_APPLICABLE_UPGRADE`). Winget found an update but it was not applicable to the system configuration. | Mapped `-1978335189` to `up_to_date` status in `AscendoWinget.psm1::Convert-WingetExitCode`, alongside the already-mapped `-1978335190`. |

---

## 2026-04-30 â€” Full run analysis + UX/perf overhaul

Reviewed run:

```text
logs/runs/20260430T055813Z-4173/run.json
status: warn  (1 brew cleanup warn + 1 snap verify race)
duration: 5m 38s
inventory phase alone: 85s
sudo prompts in CLI: 4Ă—
```

### Findings & fixes shipped

| Finding | Root cause | Fix |
|---|---|---|
| sudo prompted 4Ă— per CLI run | each `require_sudo` could trigger `sudo -v` if cache expired between phases (long apt-get); keepalive subshell sometimes loses the timestamp under `tty_tickets` | `update-all.sh` now reads password ONCE, writes 0700 askpass helper to `$XDG_RUNTIME_DIR/ascendo/askpass-*.sh`, exports `SUDO_ASKPASS`. `lib/common.sh::sudo()` wraps every sudo call as `sudo -A`. Helper unlinked on EXIT trap. |
| Inventory phase 85s | `scan_apt_third_party_manual` calls `apt-cache policy <pkg>` per-package (~250 iterations Ă— 50ms each) | new `apt_inventory_cache_init` does ONE batched `apt-cache policy ${manual[@]}`, parses with awk, populates `APT_CACHE_VERSION/CANDIDATE/SOURCE` assoc arrays. Single `dpkg-query -W` for installed versions. ~11s end-to-end. |
| `BREW-CLEANUP-WARN` recurring on `pipx __pycache__` | brew can't unlink root-owned pyc files written by an old root-mode update | scripts/brew/cleanup.sh now proactively `chown -R ${USER}` over `${BREW_PREFIX}/Cellar` (askpass-aware) BEFORE the cleanup, plus retry-after-heal on initial failure. |
| `SNAP-STILL-OUTDATED` warn | Canonical edge can publish a new firefox revision between our apply (08:00) and verify (08:03). Not actually a failure | snap/verify.sh now emits `info SNAP-NEW-REVISION` (no warn counter bump). |
| No live progress in CLI / dashboard | orchestrator redirected phase output to log file only; only `[INFO] orch run cat:phase -> json` was visible | `lib/orchestrator.sh::orch_run_phase` now `tee`s phase script output to terminal + log. apt:apply prints upgradable list preview before silent batch upgrade. `ORCH_QUIET=1` opt-out for headless. |
| Dashboard Overview re-scanned every tab visit | `ui.show()` always called `loadOverview` | `ui._loaded[view]` cache map; auto-runs on first visit, on Refresh button click, and on run completion (`invalidateCaches()`). |
| No reboot UX in dashboard | needed CLI awareness | `POST /system/reboot?delay=5` (askpass-aware), banner with **Restart now** button. CLI master prints rich reboot box at end with `systemctl reboot` + `shutdown -r +5` suggestions. |
| dev-sync exporting 3527 files | `DEFAULT_EXCLUDE_PATTERNS` had no Rust/Cargo/Tauri/Gradle/*.db patterns; the recent Tauri build pushed `app/tauri/src-tauri/target/` (3500+ files) into the gitignored set, which dev-sync treated as overlay | added `target/`, `**/target/`, `app/tauri/src-tauri/target/`, `Cargo.lock`, `**/bundle/`, `*.db*`, `.gradle/`, `.m2/`, `vendor/`, `.Trash-*/`. Overlay now: 8 files (matches `restore-manifest.json::expected_private_overlay`). |

### Verification

```text
./update-all.sh --profile quick --no-notify   â†’  6/6 categories pass, 14.5s
inventory standalone                           â†’  10.9s (vs 85s)
python3 tests/validate_phase_json.py           â†’  232/232 PASS
PYTHONDONTWRITEBYTECODE=1 python3 tests/test_dev_sync_safety.py   â†’  9/9 OK
python3 dev-sync/dev_sync_export.py --dry-run  â†’  Files selected: 8
```

### Suggested follow-ups (deferred)

1. **Per-package live progress in apt:apply.** Currently shows the
   upgradable list once; switching from `apt-get upgrade -q` to streaming
   would let us emit `[3/47] firefox 131 â†’ 132 âś“` line-by-line. ~1 day of
   work in scripts/apt/apply.sh.
2. **Tauri reskin** â€” backend + REST API are stable; native shell is
   nice-to-have.
3. **libsecret integration** â€” `.env.local` and rclone/dev-sync tokens
   should move to `secret-tool` for portability across machines.
4. **CI: assert dev-sync overlay â‰¤ 50 files** to catch future bloat early.

---

## 2026-04-27 (previous baseline)

Reviewed run:

```text
logs/master_20260427_182631.log
Ubuntu 24.04.4 LTS
Kernel 6.17.0-22-generic
Host mk-uP5520
```

## Result

The latest full `update-all.sh` run completed without fatal errors.

| Area | Result | Notes |
|---|---|---|
| APT | OK | Package lists refreshed, no packages upgraded. |
| APT phased updates | Deferred | `remmina*` and `thermald` were deferred by Ubuntu phased updates. Do not force unless needed. |
| Snap | OK in full update | Snap packages reported current and no disabled revisions were removed. |
| Homebrew | WARN | `brew cleanup --prune=7` hit a permission issue in old `pipx` keg cleanup. `brew doctor` still reported ready. |
| npm | OK | Global AI CLIs are current: Claude Code, Gemini CLI, Codex. |
| pip/pipx | OK | `graphifyy 0.4.23` present through pipx. |
| Flatpak | OK | Nothing to update; no Flatpak apps installed. |
| NVIDIA | OK | NVIDIA upgrade skipped by policy; `nvidia-smi` reports Quadro M1200 on driver `570.211.01`. |
| Firmware | OK | fwupd reports no updates available. |
| Reboot | OK | No reboot required. |
| Inventory | OK | `APPS.md` regenerated locally and remains gitignored. |

## Known Operational Notes

- `brew cleanup` can warn when old Homebrew files are not owned by the invoking user.
  If it repeats, run:

```bash
sudo chown -R "$USER:$USER" /home/linuxbrew/.linuxbrew/Cellar/pipx
brew cleanup --prune=7
```

- `setup.sh --check --non-interactive` now avoids hanging when `snap list`
  does not respond. It prints a warning and skips Snap check after
  `SNAP_CMD_TIMEOUT` seconds.

- The full update regenerated `APPS.md`, but this file is intentionally local
  inventory and is ignored by Git and dev-sync provider export.

## Dev-Sync State

Latest provider verification:

```text
dev_sync_logs/20260427-182426-verify-full.log
OVERALL PASS
provider=protondrive
provider_snapshot=8
dirty_tracked_entries=0
orphan_local=0
missing_from_local=0
missing_from_provider_overlay=0
stale_provider_only=0
content_mismatches=0
```

Current expected private overlay:

- `.claude/agents/advisor.md`
- `.claude/agents/worker-haiku.md`
- `.claude/settings.json`
- `.claude/settings.local.json`
- `.dev_sync_config.json`
- `.env.local`
- `github`
- `github.pub`

## Proton Drive Export Note

Local Proton Drive folders can reject permission metadata changes with
`Read-only file system` while still accepting content writes. The dev-sync
rsync transport is configured to copy content and directory structure without
owner/group/permission metadata for this reason.

