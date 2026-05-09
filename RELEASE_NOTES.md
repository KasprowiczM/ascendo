# Ascendo — Release Notes

## v0.6.0-rc1 — 2026-05-09 (Edition split + GUI-PATH fixes)

Sesja 51-53. Three sessions of work shipped together: a `basic` /
`dev` edition split, a clickable .dmg installer per edition, and a
class of fixes for macOS GUI-launched processes that were poisoning
package installs.

### Highlights

- **Two editions, one repo.** `ASCENDO_EDITION=basic` (default) or
  `ASCENDO_EDITION=dev`. Basic hides Sync/Hosts/Logs nav, merges
  History+Logs inline, removes the raw-events box. Dev keeps the
  full 12-tab UI. Same code, same tests, same release.
- **8-cell install matrix** (`{basic, dev}` × `{cli, web, desktop,
  full}`) shipped as one-liners in README.
- **Clickable macOS DMG with edition baked in.** `bin/build-dmg.sh
  --edition=basic` produces `dist/Ascendo-Basic-0.0.7-arm64.dmg`,
  `--edition=dev` produces `dist/Ascendo-Dev-0.0.7-arm64.dmg`. End
  user drags to /Applications, double-clicks; first-run bootstrap
  auto-installs Python ≥ 3.11 / git / curl / jq via Homebrew.
- **`bin/user-scripts/` helpers** drop on PATH at install time:
  `ascendo_update`, `ascendo_start_web`, `ascendo_doctor`,
  `ascendo_maintenance`. Plus dev-only `ascendo_sync` + `ascendo_push`.
- **`docs/PLATFORM_STATUS.md`** — honest cross-platform feature matrix.
- **`LINUX_QUICKSTART.md`** + rewritten `USER_GUIDE.md` (basic) +
  new `DEV_GUIDE.md` (dev edition).
- **Public-repo audit + dev-sync overlay** — copy-only migration
  script stages private files (AI instructions, internal handoffs)
  into Proton-synced overlay before public flip. Dev-sync TOOLING
  stays PUBLIC — anyone can use it with their own provider.

### Critical fixes

- **macOS Tauri shell no longer crashes on launch** (was: SIGABRT in
  `applicationDidFinishLaunching:`). `locate_sidecar()` probes 6+
  absolute paths; spawn failures show a WebView recovery page instead
  of panicking.
- **opencode-cli + similar npm packages with bun/node postinstall hooks
  install correctly** (was: `sh: bun: command not found`).
  npm/apply.sh extends PATH with canonical node + bun bin dirs.
- **pip targets the right Python** (was: silently installing into
  Xcode's Python 3.9 framework, leaking to `~/Library/Python/3.9/bin`).
  `ascendo_pip_pip_bin` probes brew first, rejects Xcode shim.
- **Apps view reflects post-apply truth** (was: stuck on "outdated"
  after a successful upgrade). `_latest_check_overlay` walks
  check / apply / verify newest-first; verify wins on tie.

### Cleanup of misinstalled Xcode-Python packages

If you ran a Full update before this fix, the Python 3.9 packages
that ended up in `~/Library/Python/3.9/bin/` (poetry, ruff, mypy,
black, pytest, httpx, isort, pipx, uv, virtualenv) are unreachable
from `ascendo` (which runs under brew Python 3.14). Safe to remove:

```bash
rm -rf ~/Library/Python/3.9
```

### Tests

683 green: 290 contract + 393 macOS adapter (9 pre-existing
Windows-only test_service_endpoints failures unchanged). Real-Ubuntu
mk-uP5520 + Windows DP5520WMK validation pending operator.

---

## v0.5.2 — 2026-05-09 (Cross-platform parity + one-line install/update)

### Highlights

- **One-line install + update for all 3 OSes.** macOS / Linux:
  `curl -fsSL https://raw.githubusercontent.com/KasprowiczM/ascendo/main/install.sh | bash`
  Windows: `iwr -useb https://raw.githubusercontent.com/KasprowiczM/ascendo/main/install.ps1 | iex`.
  Update equivalents at `update.sh` / `update.ps1`. Idempotent,
  resilient to missing deps (auto-installs Python via winget on
  Windows; via Homebrew/apt on Linux/Mac), behind-corporate-proxy
  aware, locked-package-manager safe, final `ascendo doctor` self-test.
- **Ubuntu transitions from stub to Tier-1 Python adapter** with
  `UbuntuAdapter` + 7 managers (apt/snap/brew/npm/pip/flatpak/drivers)
  + full IInventory enumeration via `adapters/ubuntu/scripts/
  inventory/list.sh` covering all 6 sources with graceful fallback on
  missing CLIs. Bridges to mature legacy bash scripts via env-var IPC
  contract; schema translation transparent.
- **Windows apply.ps1 stderr capture** — operators finally see actual
  error reasons in sidecar messages instead of cryptic "exited N".
- **Pre-dispatch up_to_date guard** in winget + msstore apply skips
  re-attempts on already-current packages.
- **Cross-cutting bug fix**: `_flush_run_to_inventory_db` clears
  categories before bulk_upsert (4th missing path from Sesja 40
  stale-rows fix).

### Added

- `install.sh` rewrite (451 LOC): `--update` / `--reinstall` /
  `--verbose` / `--non-interactive` flags + env-var overrides
  (`ASCENDO_LANG`, `ASCENDO_PROFILE`, `ASCENDO_HOME`,
  `ASCENDO_NONINTERACTIVE`, `ASCENDO_REPO_URL`, `ASCENDO_BRANCH`).
  Network preflight, disk-space check, locked-pkg-mgr detection.
- `update.sh` (187 LOC) — POSIX one-liner updater. `git pull
  --ff-only`, refresh editable installs, restart any running dashboard.
- `install.ps1` (382 LOC) — Windows `iwr | iex` one-liner. PS 5.1 +
  7.x compatible. Auto-installs Python 3.12 via winget. Refuses
  Win < 10 b17763.
- `update.ps1` (147 LOC) — Windows updater. Restarts AscendoDashboard
  service if installed.
- `adapters/ubuntu/ascendo_ubuntu/` — full Python adapter scaffold.
- `adapters/ubuntu/scripts/inventory/list.sh` (427 LOC) — full
  inventory enumeration across apt+snap+flatpak+brew+npm+pip.
- `SourceType.DRIVERS` + `SourceType.FIRMWARE` core enum values.
- 4 stderr-capture fixes in Windows apply.ps1 scripts (winget /
  msstore / arp / windows_update).
- 6 Windows regression tests + 36 Ubuntu adapter tests + 9 inventory
  tests + 32 installer contract tests.

### Fixed

- Post-run flush stale rows: was the missing 4th path from Sesja 40's
  fix. Operator's local DB had 312 web rows when discovery only
  emitted 37.

### Tests

```
contract:   283/292   (9 pre-existing service_endpoints failures unchanged)
macOS:      391/391   (no regression)
Windows:     99/99    (after fixes)
Ubuntu:      36/36    (+ 2 Linux-only skips on macOS sandbox)
installers:  32/32    (+ 1 pwsh-only skip)
TOTAL:      841/848 = 99.2% green
```

---

## v0.5.0 — 2026-05-04 (Release-quality polish, profile templates, apt rollback)

### Added
- **Profile templates** (`config/profiles/*.list`): `dev-workstation`,
  `media-server`, `minimal-laptop`. UI panel in Settings (Preview / Apply)
  and CLI `ascendo profile {list|import [--dry-run]}`.
- **Per-package apt rollback** — `↓ rollback` button per row in Categories
  detail; runs `apt-get install --allow-downgrades pkg=ver`.
- **GitHub Releases auto-update notifier** — `/updates/check`, settings.updates.check_repo,
  Settings card with current vs latest tag.
- **Help section** — full operator docs (11 chapters: install, first run, CLI
  cheat-sheet, scripts reference table, configuration files, dashboard tour,
  scheduler, snapshots, dev-sync, AI, troubleshooting), 1rem fonts.
- **About section** — version + git head + system info + Markdown render of
  release notes from `RELEASE_NOTES.md`.
- **Hosts edit UI** — Add/Edit/Delete buttons writing to `config/hosts.toml`
  with `.bak_<ts>` per save (`/hosts/upsert`, `/hosts/delete`).
- **AI providers**: Anthropic, OpenAI, **Google Gemini**, **Ollama (local)**,
  **LM Studio (local)**, OpenAI-compatible. Local providers need only Base URL.
  Test connection button.
- **Cloud sync providers**: Proton Drive / Google Drive / Dropbox / OneDrive /
  WebDAV / S3 / local. Remote name dropdown from `rclone listremotes`,
  Browse… modal with folder picker (`rclone lsf --dirs-only`).
- **Logs detail viewer** — run dropdown + per-phase log viewer pane
  (clickable from sidecar table).
- **Smart Suggestions** panel with heuristic + opt-in LLM enrichment
  (read-only, applied as diffs only with manual confirm).
- **Live progress bar** in Run Center parsing `PROGRESS|...` markers from
  scripts.
- **Health card** on Overview (post-run audit, score 0-100).
- **ETA banner** on History (avg / p90 / ok% per profile from past runs).
- **Exclusions** — `config/exclusions.list` filters every apply phase;
  Apps tab has per-package skip checkbox.
- **Settings backup** — tar.gz export/import via UI or CLI.
- **CHECK-ONLY banner** in CLI when no apply phases would run.
- **Detailed package summary** at the end of every run (upgrade/install/refresh/noop).
- **`--budget Nm`** flag stops the pipeline cleanly when wall time exceeded.
- **`should-run.sh`** scheduler gate: defers on battery, outside maintenance
  window, or while apt/dpkg busy.

### Changed
- **Layout**: full sidebar on the left with 12 inline Lucide-style icons
  (overview/categories/run/history/logs/sync/apps/suggest/hosts/settings/help/about);
  topbar utilities (lang/theme/font); hamburger drawer below 768px.
- **Slogan** under logo (vertical), `Ascendo` gradient + `unified updates`
  tagline.
- **Sudo cache indicator** back in status bar (footer right).
- **Theme switcher** uses monitor icon for auto mode (cycle monitor → sun → moon).
- **Pie chart** redesigned: larger donut with rounded segments, total + % ok
  centered, legend with percentages.
- **Snap apply UX**: emits `SNAP-AUTO-REFRESHED` advisory when snapd's
  background refresh wins the race; surfaces specific blocking snap name
  for "running apps" errors.
- **NVIDIA detection**: `apt_pkg_candidate` + `dpkg --compare-versions`
  instead of `madison NR==1` — no false-positive "available" when the
  candidate is actually older.
- **Snapshot create.sh**: hard 300s timeout + askpass-aware so dashboard
  runs no longer hang at pre-apply snapshot.
- **Categories drivers/inventory** populated (NVIDIA pkg + smi + fwupd; APPS.md mtime).
- **Scheduler install** wires `ExecStartPre=` to `should-run.sh`.
- **Help/About** font sizes bumped to 1rem (from 0.85rem).

### Fixed
- Theme switcher cycle no longer collapses to always-light (was reading
  `data-theme` resolved value instead of preference).
- Categories `+ add` reflects the new package immediately by busting the
  60s inventory cache.
- Snapshot stuck-at-pre-apply (timeshift hang without TTY) — bounded with
  `timeout`, never blocks the run.
- Stuck dashboard runs (`141545Z`, `141641Z`) cleaned from history.
- Duplicate PL i18n entries (logs/sync/hosts) removed.

### Notes
- 31 GET endpoints all 200 in CI smoke.
- 9-file private overlay (Proton Drive) PASS verify-full.
- Targets Ubuntu 24.04 / Debian-derived. macOS/Windows abstractions deferred.

## v0.4.0 — 2026-04-30 (Sidebar UI, Smart Suggestions, exclusions)

### Added
- Left sidebar UI with Lucide-style icons; hamburger drawer + responsive grid.
- Topbar utilities for theme/lang/font-size; persisted in settings.
- Smart Suggestions panel (heuristic + opt-in Anthropic/OpenAI provider).
- Health card with 0-100 score and History ETA averages.
- Backup/restore tar.gz from Settings and `ascendo settings export/import`.
- Per-user `config/exclusions.list` + `lib/exclusions.sh` wired into all 6 apply scripts.
- `lib/progress.sh` with progress bar, `print_found` helpers, and PROGRESS markers parsed via SSE.
- `bin/ascendo` subcommands: `settings`, `health`, `exclusions`.
- `scripts/scheduler/should-run.sh` battery + maintenance-window guard.
- 10 new backend endpoints: `/suggestions{,/apply,/dismiss}`, `/health/{check,run}`, `/backup/{export,import}`, `/telemetry/eta`, `/exclusions{,/add,/remove}`.
- `StartRunRequest.extra_args` whitelist (`--nvidia`, `--snapshot`, `--no-drivers`, `--no-health`).

### Changed
- All `check.sh` scripts now show found pkg `old -> new` lists.
- `apt:apply` emits per-package `[N/M]` progress markers.
- `update-all.sh` gains `--budget`, `--no-health`, CHECK-ONLY banner, detailed summary, post-run health check.
- Categories tab adds add-package widget and per-row add/remove buttons.
- Brand wordmark switched to gradient with "unified updates" tagline.

### Fixed
- NVIDIA detection now uses `apt_pkg_candidate` + `dpkg --compare-versions` (eliminated security-pocket pin false positive).
- Snapshot hang without TTY: 300s timeout + per-run `snapshot.log`; never blocks the run.
- Snap refresh prints actionable hint when blocked by running apps.

---

## v0.3.0 — 2026-04-30 (Ascendo brand, i18n, app registration)

### Added
- Ascendo branding: `branding/{logo,icon}.svg`, banner, favicon, dashboard wordmark, ASCII banner on `update-all.sh` and `fresh-machine.sh`.
- CLI i18n without gettext: `lib/i18n.sh`, `i18n/{en,pl}.txt`, `t`/`tn` helpers, persisted at `~/.config/ascendo/lang`.
- Backend `GET /i18n/{en,pl}` so dashboard reuses CLI catalogs.
- Wizard step 0 = language radio; live dashboard switch.
- `lib/tables.sh` Unicode box-drawing tables with `@ok/@warn/@err/@skip/@info` colour pills.
- App registration: `scripts/apps/{detect,add,remove,list,install-missing}.sh` with `.bak_<ts>` backups.
- Backend `/apps/detect|add|remove` endpoints; "Apps" dashboard tab with add/remove buttons.
- `bin/ascendo` CLI shim with auto-resolved root (`ASCENDO_ROOT` > `/opt/ubuntu-aktualizacje` > repo).
- User Journey docs in EN + PL (`docs/{en,pl}/user-journey.md`) covering 6 flows.

### Changed
- `.deb` metadata: `Package: ascendo`, `Source: ubuntu-aktualizacje`, version 0.3.0.
- `fresh-machine.sh` first prompt = language pick; never auto-installs packages, runs `apps detect` read-only.
- `dev_sync_export.py` renders boxed TTY summary; non-TTY path stays plain for CI parsers.

---

## v0.2.0 — 2026-04-30 (Etap-5 roadmap: .deb, wizard, auth, metrics)

### Added
- `.deb` packaging: `packaging/build-deb.sh`, control/postinst/prerm, `/usr/bin/ubuntu-aktualizacje` shim with `fresh|dashboard|schedule|snapshot` subcommands.
- First-run wizard modal triggered when `~/.config/ubuntu-aktualizacje/onboarded.json` missing; `GET /onboarding/state`, `POST /onboarding/complete`.
- Bearer token middleware (`app/backend/auth.py`) + `/auth/{status,generate-token,revoke-token}`; no-op when token file absent.
- Audit log (`app/backend/audit.py`) JSONL at `~/.local/state/ubuntu-aktualizacje/audit.log`; `/audit` endpoint tail.
- Prometheus `/metrics` text 0.0.4, dependency-free (`run_total`, `last_run_duration_seconds`, `phase_summary`, `inventory_totals`, `reboot_required`).
- Run diff (`GET /runs/diff?a=X&b=Y`) and Markdown report (`GET /runs/{id}/report.md`).
- Snapshot restore: `scripts/snapshot/restore.sh` (timeshift→etckeeper) + `POST /snapshots/restore` with confirm token.
- `scripts/maintenance/prune-logs.sh` log retention (keep last N runs OR D days).
- Notification routing in `scripts/notify.sh`: ntfy.sh, Slack webhook, email, Telegram bot.
- shellcheck step in `validate.yml` (severity=warning, ignores SC1090/91/2086).

### Changed
- `apt:apply` streams `Setting up X (ver)` via `tee` + awk, prints `[N/total] pkg → ver` live and emits per-package JSON items.

### Notes
- Roadmap recommendations appended to `docs/agents/handoff.md`: P0-P3 priorities across onboarding, UX, security, observability, multi-host, cross-distro, testing.

---

## v0.1.0 — 2026-04-30 (Hybrid CLI + dashboard, phase contract, UX/perf overhaul)

### Added
- 5-phase contract per category (`check/plan/apply/verify/cleanup`) with JSON sidecar schema v1 (`schemas/phase-result.schema.json`).
- `lib/json.sh` + `lib/_json_emit.py` emitter and `lib/orchestrator.sh` runner aggregating `run.json`.
- Native phases for 8 categories: apt, snap, brew, npm, pip, flatpak, drivers, inventory.
- FastAPI dashboard (`app/backend`, 13 REST endpoints + SSE live log) with vanilla SPA (`app/frontend`, 8 views).
- SQLite history with migrations; `app/install.sh` venv bootstrap (PEP-668 safe); systemd user-service autostart.
- Snapshot helpers (`scripts/snapshot/`, timeshift→etckeeper fallback) and `scripts/scheduler/install.sh` systemd timer generator.
- Plugin system (`plugins/example/` manifest + 5 phases, `category=plugin:<id>`).
- Secrets: `lib/secrets.sh` + `scripts/secrets/migrate-to-libsecret.sh`.
- Host profiles (`config/host-profiles/<host>/`) and multi-host SSH read-only preflight (`config/hosts.toml`).
- Tauri native shell (`app/tauri/src-tauri`): spawns uvicorn sidecar; `build.sh` auto-installs Rust + libs; pure-stdlib icon generator.
- Live inventory: `app/backend/inventory.py` strukturalne skany, 60s cache, `/inventory{,/{cat},/summary,/refresh}`.
- Overview SVG donut + bar charts; Categories expandable rows; PL/EN i18n (91 keys), live language switch; theme auto/light/dark.
- Sudo flow: `POST /sudo/auth` → in-memory password → `SUDO_ASKPASS` helper consumed by `update-all.sh`.
- `scripts/fresh-machine.sh` one-liner (preflight → bootstrap → venv → user-service → verify) with `--check-only`, `--dry-run`, `--no-{dashboard,service,sync}`.
- CI dev-sync overlay size guard (fails build if >50 files).
- Tests: `validate_phase_json.py`, bats (`json_emit`, `orchestrator`, `phase_contract`), `tests/python/test_frontend.py`.
- RUN.md, `docs/agents/{contract,hybrid-mode}.md`, refreshed `docs/PROJECT_MAP.md`.

### Changed
- `update-all.sh` reads sudo password ONCE; ephemeral 0700 askpass helper at `$XDG_RUNTIME_DIR/ubuntu-aktualizacje/`; `lib/common.sh` wraps `sudo` as `sudo -A`.
- Orchestrator `tee`s phase output to terminal + per-phase log; `ORCH_QUIET=1` opts back into silent mode.
- Inventory phase: bulk `dpkg-query` + single batched `apt-cache policy` drops runtime ~85s → ~11s (8×).
- Brew cleanup proactively `chown`s Cellar before pruning; retries after heal.
- `SNAP-STILL-OUTDATED` downgraded to info `SNAP-NEW-REVISION`.
- Dashboard Overview caches first load; refreshes on button or run completion.
- dev-sync overlay: 3527 → 8 files (added Cargo `target/`, Tauri `bundle/`, `*.db*`, `.gradle/`, `.m2/`, `vendor/`, `.Trash-*/`).
- Legacy `scripts/update-*.sh` retained alongside native phases for standalone CLI.

### Fixed
- `POST /system/reboot` + reboot banner with confirm modal when `/var/run/reboot-required` is set; CLI prints rich reboot box.

---

## v0.0.3 — 2026-04-27 (Dev-sync workflow, recovery, stability)

### Added
- Dev-sync workflow and project graph.
- "Latest update run review" documentation.

### Changed
- Hardened Ubuntu recovery and dev-sync workflow.
- Proton metadata-limited exports handled (rsync copies content + structure without owner/group/permission metadata).

### Fixed
- Avoid stale locks and Snap check hangs during `setup.sh --check --non-interactive`.

---

## v0.0.2 — 2026-04-18 (AI agents, sudo flow, repo hygiene)

### Added
- Codex and Gemini AI agent integration: workspace configuration, documentation, tooling profiles.

### Changed
- Agent model versions updated; Codex configuration and documentation structure clarified.
- README and docs refreshed to match the current update workflow.

### Fixed
- Update warnings, MEGA repo conflict resolved; single-sudo authentication flow.
- Sudo session handling now matches the native one-password flow.

---

## v0.0.1 — 2026-03-22 → 2026-03-24 (Initial suite, config-driven refactor, hardening)

### Added
- Initial Ubuntu update suite: `update-all.sh` master, per-manager scripts (apt, snap, brew, npm, drivers, inventory), `setup.sh` bootstrap.
- `lib/common.sh` shared logging/sudo/user-context helpers; APPS.md inventory.
- Universal config-driven architecture: `config/{apt-packages,apt-repos,snap-packages,brew-formulas,brew-casks,npm-globals}.list`.
- `lib/detect.sh` universal scanner (OS/hardware/GPU/all package managers); `lib/repos.sh` idempotent APT repo setup.
- Flatpak, pip, and pipx managers with their own `config/*.list`.
- systemd timer (`ubuntu-aktualizacje.{service,timer}` + `install-timer.sh`) replacing cron.
- `setup.sh --rollback`, `--discover`, `--check`, `--nvidia` modes.
- GitHub Actions CI (`validate.yml`): config syntax, `bash -n`, required files, APPS.md gitignore, secret scan.
- DKMS rebuild script (`scripts/rebuild-dkms.sh`).
- Desktop notification (`scripts/notify.sh`) with `--no-notify` flag.
- `lib/git-push.sh` GitHub push helper using `GITHUB_TOKEN` from `.env.local`.

### Changed
- APPS.md is gitignored (machine-specific); `APPS.md.example` ships as template.
- `INVENTORY_SILENT=1` prevents duplicate writes from the master script.
- Brew/npm/pipx run as `SUDO_USER` via `run_as_user`/`run_silent_as_user`.
- NVIDIA packages held by default during apt; `--nvidia` flag overrides; `update-drivers.sh` respects `UPGRADE_NVIDIA`.
- `update-npm.sh` switched to `npm install -g @latest` for reliability.
- Drivers table in README reflects ubuntu-drivers as info-only.

### Fixed
- Removed `ubuntu-drivers autoinstall` (it downgraded explicitly-managed nvidia-580 and broke DKMS on kernel 6.19.x).
- `npm outdated -g --json` exit-1 falsely reporting "all up to date" — switched to `|| true`.
- fwupd false WARN matching "no available firmware updates".
- APT duplicate-source warnings now surfaced with exact remediation (rm meganz.list).
- Brew cleanup recurring WARN: chowns root-owned files in Cellar back to `SUDO_USER`.
- pip self-upgrade WARN on PEP-668 brew Python: detect and skip with info.
- Inventory silent crash: `brew list --cask` invoked via `run_as_user` (Homebrew 4.x exits 1 as root).
- `set -e` + `pipefail` failures in apt/pip/inventory: `apt_pkg_version` now returns 0 with empty string on missing pkg; pipx loop uses process substitution.
- Inventory crash on `dpkg -l 'linux-modules-nvidia*'` glob with no matches; APT sources grep against comment-only files.

### Notes
- `.gitignore` updated to ignore SSH keys.
