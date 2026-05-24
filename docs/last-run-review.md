# Last Run Review

## 2026-04-30 — Full run analysis + UX/perf overhaul

Reviewed run:

```text
logs/runs/20260430T055813Z-4173/run.json
status: warn  (1 brew cleanup warn + 1 snap verify race)
duration: 5m 38s
inventory phase alone: 85s
sudo prompts in CLI: 4×
```

### Findings & fixes shipped

| Finding | Root cause | Fix |
|---|---|---|
| sudo prompted 4× per CLI run | each `require_sudo` could trigger `sudo -v` if cache expired between phases (long apt-get); keepalive subshell sometimes loses the timestamp under `tty_tickets` | `update-all.sh` now reads password ONCE, writes 0700 askpass helper to `$XDG_RUNTIME_DIR/ascendo/askpass-*.sh`, exports `SUDO_ASKPASS`. `lib/common.sh::sudo()` wraps every sudo call as `sudo -A`. Helper unlinked on EXIT trap. |
| Inventory phase 85s | `scan_apt_third_party_manual` calls `apt-cache policy <pkg>` per-package (~250 iterations × 50ms each) | new `apt_inventory_cache_init` does ONE batched `apt-cache policy ${manual[@]}`, parses with awk, populates `APT_CACHE_VERSION/CANDIDATE/SOURCE` assoc arrays. Single `dpkg-query -W` for installed versions. ~11s end-to-end. |
| `BREW-CLEANUP-WARN` recurring on `pipx __pycache__` | brew can't unlink root-owned pyc files written by an old root-mode update | scripts/brew/cleanup.sh now proactively `chown -R ${USER}` over `${BREW_PREFIX}/Cellar` (askpass-aware) BEFORE the cleanup, plus retry-after-heal on initial failure. |
| `SNAP-STILL-OUTDATED` warn | Canonical edge can publish a new firefox revision between our apply (08:00) and verify (08:03). Not actually a failure | snap/verify.sh now emits `info SNAP-NEW-REVISION` (no warn counter bump). |
| No live progress in CLI / dashboard | orchestrator redirected phase output to log file only; only `[INFO] orch run cat:phase -> json` was visible | `lib/orchestrator.sh::orch_run_phase` now `tee`s phase script output to terminal + log. apt:apply prints upgradable list preview before silent batch upgrade. `ORCH_QUIET=1` opt-out for headless. |
| Dashboard Overview re-scanned every tab visit | `ui.show()` always called `loadOverview` | `ui._loaded[view]` cache map; auto-runs on first visit, on Refresh button click, and on run completion (`invalidateCaches()`). |
| No reboot UX in dashboard | needed CLI awareness | `POST /system/reboot?delay=5` (askpass-aware), banner with **Restart now** button. CLI master prints rich reboot box at end with `systemctl reboot` + `shutdown -r +5` suggestions. |
| dev-sync exporting 3527 files | `DEFAULT_EXCLUDE_PATTERNS` had no Rust/Cargo/Tauri/Gradle/*.db patterns; the recent Tauri build pushed `app/tauri/src-tauri/target/` (3500+ files) into the gitignored set, which dev-sync treated as overlay | added `target/`, `**/target/`, `app/tauri/src-tauri/target/`, `Cargo.lock`, `**/bundle/`, `*.db*`, `.gradle/`, `.m2/`, `vendor/`, `.Trash-*/`. Overlay now: 8 files (matches `restore-manifest.json::expected_private_overlay`). |

### Verification

```text
./update-all.sh --profile quick --no-notify   →  6/6 categories pass, 14.5s
inventory standalone                           →  10.9s (vs 85s)
python3 tests/validate_phase_json.py           →  232/232 PASS
PYTHONDONTWRITEBYTECODE=1 python3 tests/test_dev_sync_safety.py   →  9/9 OK
python3 dev-sync/dev_sync_export.py --dry-run  →  Files selected: 8
```

### Suggested follow-ups (deferred)

1. **Per-package live progress in apt:apply.** Currently shows the
   upgradable list once; switching from `apt-get upgrade -q` to streaming
   would let us emit `[3/47] firefox 131 → 132 ✓` line-by-line. ~1 day of
   work in scripts/apt/apply.sh.
2. **Tauri reskin** — backend + REST API are stable; native shell is
   nice-to-have.
3. **libsecret integration** — `.env.local` and rclone/dev-sync tokens
   should move to `secret-tool` for portability across machines.
4. **CI: assert dev-sync overlay ≤ 50 files** to catch future bloat early.

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
