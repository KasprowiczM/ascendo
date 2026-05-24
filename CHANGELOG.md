# Changelog

All notable changes to Ascendo will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added — Touch-first responsive UI kit (Sesja 74)

- New `app/frontend/ui-components.js`: **no native dropdowns anywhere**
  — every `<select>` is upgraded at runtime to an accessible
  segmented control, choice-card group, or searchable progressive
  list (native select kept value-synced so `FormData`/`.value`/
  `change` listeners are unchanged).
- **Mobile bottom tab bar** (5 destinations, ≤768px), Run Center
  **3-step progressive reveal** (Profile → Options → Confirm) with a
  sticky mobile action bar, and a **tappable mobile card** layout for
  the History table.
- 44px minimum touch targets, `:focus-visible`, keyboard radiogroup
  navigation, `prefers-reduced-motion`. New `uikit.*` i18n namespace
  (EN+PL parity **1060/1060**).

### Changed — Light theme contrast & hierarchy (Sesja 74)

- Retuned the light token set (`colors_and_type.css`): a real 3-tier
  surface ramp (`--paper-base/-nested/-card/-sunk` + new
  `--bg-nested`), darker muted/faint text + borders, `--accent-strong`
  → lime-700 (AA as fg accent). Identity preserved (cool-grey + lime).
- New `--ok/warn/err/info-text` tokens split bright semantic *fills*
  from WCAG-AA semantic *text on tint* (light-mode badges/inline
  errors/diagnostics were ~2:1 before). **Dark theme unchanged**
  (tokens mapped back to the bright primitives).
- Responsive header density: eliminated a ~288px mobile dead-band
  (root cause: `.app-header-text` flex-basis becoming a forced height
  in the mobile column layout); `.app-header` 439px → 59–99px on a
  390-wide phone; demoted the redundant per-view `<h2>` to a compact
  section label; token-only spacing tiers for mobile/tablet/desktop.

### Fixed — interaction QA + pip verify (Sesja 74)

- **Help table-of-contents links** no longer get hijacked by the hash
  router (they were throwing the user back to Dashboard); they now
  scroll to the section and leave the route untouched.
- **Run Center "Stop"** is now always reachable during an active run
  (was hidden inside the collapsed progressive-disclosure step).
- macOS `pip verify` now defers **all** brew-owned formulas (not just
  pip/setuptools/wheel) to brew, so `uv` is no longer reported as a
  verify failure against the PyPI candidate — pip verify
  `partial → success`.

### Changed — UX/IA refactor: 5-destination AppShell (Sesja 73)

- **Navigation reorganised** from 10–13 flat sidebar items into **5
  workflow destinations**: Dashboard, Library (Sources/Apps/Tools),
  Runs (Start/Scheduled/History), Insights (Trends + dev Logs),
  Settings (General/Help/About + dev Hosts/Sync). Old hashes
  (`#schedule`, `#apps`, …) auto-resolve so existing bookmarks keep
  working.
- **Per-page header** with title, one-line description, and a single
  primary action. Language/Theme/Font moved into a compact
  **Preferences popover** (same control IDs — no behavioural change).
- **New `platform.js`** OS abstraction layer (`Platform.os / allow /
  supportsNvidia / elevationTerm / copy`). NVIDIA driver UI + copy is
  structurally excluded on macOS across JS, i18n, and CSS layers.
- **New `shell.js`** AppShell (sidebar, header, segmented sub-tabs,
  Preferences popover, run-detail drawer) — wraps `ui.show()` with no
  rewrite of the SSE/runs/AI router.
- **New Insights** surface: run trends, recent failures, duration
  sparkline, recent changes, platform-aware operational notes
  (assembled from existing `/runs` data — no new backend).
- **History simplified** to 5 default columns (Started · Profile ·
  Status · Duration · Run details); phases/reboot/run-id moved into a
  right-side detail drawer. Premium action-oriented empty state.
- New `shell.*` + `platform.*` i18n namespaces (EN + PL parity:
  1045 == 1045).

### Fixed — operator bug batch (run e2d0fffb, Sesja 73)

- SPA assets now send `Cache-Control: no-cache, must-revalidate` so a
  `git pull` is picked up without a manual hard-reload (fixes the
  "schedule icon not applied" staleness).
- `safe`/`quick` profile web apply no longer opens apps to the
  foreground — `ASCENDO_SAFE_MODE` + exit-95 sentinel routes
  builtin/squirrel/omaha/release_feed to a silent `skipped` with a
  manual-action message.
- Codex update fixed: `_web_install_dmg` now handles ZIP archives
  (Sparkle appcast served a `.zip`, not a `.dmg`) via `ditto`.
- Ledger Live / Warp silent-install refusal now surfaces as a real
  error: explicit `rm -rf` before `cp -R` exposes a locked running
  bundle instead of a false success.
- Uninstalled apps (Cursor/Opera/Notion/Notion-Calendar) are now
  evicted from inventory via `InventoryDB.delete_row` during the
  post-run flush instead of lingering forever.
- `pip uv` (and brew-owned pip-self) now report `up_to_date` instead
  of landing in REPORT.md's "Deferred" section.
- **History tab fixed** — a Sesja-66 `i18n.t` typo plus a `tr`
  variable shadowing the i18n helper threw mid-loop and left the
  table blank; also fixed the identical bug in the Schedule list.

### Planned

M6 — security audit (T1-T7 per ADR-0005), code signing across all three
OSes, plugin signing + verification, plugin marketplace UX in dashboard.

---

## [0.6.7] — 2026-05-14 — Inventory dedup + Suggestions AI + Schedule tab + Help/About (Sesja 67)

Sesja 67. Operator: *"check why inventory changes after each run …
implement fully working suggestions … every click in web app works".*
Four deliverables.

### Added

- **Schedule tab** (previously-deferred): new
  `core/ascendo/dashboard/routes/scheduler_real.py` with
  `GET /scheduler/list` + `POST /scheduler/{install,remove,trigger}`
  driving the adapter's `IScheduler` implementation. SPA gets a
  dedicated `#view-schedule` with list table + add-or-replace form +
  per-row Run-now / Edit / Delete. Replaces the previous
  `{ok: true, stub: true}` stubs.
- **Suggestions AI integration** (previously-deferred): new
  `call_provider_inference()` in `routes/ai.py` covers 6 providers
  (anthropic / openai / openrouter / ollama / google / lm_studio).
  `/suggestions/library` now prepends 1-3 AI-generated cards on top
  of rule-based with strict JSON parsing + action-payload sanitisation.
  Failures fall back to rule-based transparently.
- **About: Recent highlights panel** — Sesjas 58-67 capability tour
  with GitHub + Releases & downloads links.
- **Help: "12. Recent additions" + "13. Operator tooling"** sections
  wired to the Sesja 66 `help.windows.*` i18n keys that had been
  orphaned + 16 new keys (EN + PL) for ascendo web lifecycle /
  build-inventory / run-tag-release / install-service / validate
  harness / watchdog / Suggestions AI / Schedule tab.

### Fixed

- **Inventory drift across runs.** Pre-v2 `inventory_items` PK was
  `(category, name)` which silently collapsed 17 msstore + 14 winget
  + 3 ARP packages sharing DisplayNames across architectures (MSIX
  x86/x64/arm64; Microsoft Visual C++ 2008 Redistributable's 9
  parallel installs; Comet's two ARP rows; etc.). Schema migrated to
  v2 with PK `(category, name, item_id)`; bulk_upsert + query +
  flush callers all updated. Live verified on DP5520WMK: msstore
  78 → 85 rows, winget keeps 9 separate VC++ 2008 architecture
  entries. Pre-v2 DBs drop legacy data on first open; next live-scan
  or post-run flush repopulates within seconds. +7 regression tests
  in `tests/contract/test_inventory_db_item_id.py`.
- **Help managers reference table** was missing rows for npm / pip /
  web / plugin — added with Sesja 58-65 context (Tier-A silent
  install, fake-success detection, apply-mark, dedup).

### Test count

453 (Sesja 66) → **477 passing** Windows + contract (+24 new:
7 inventory_db item_id + 3 overlay + 14 suggestions_ai).
Zero regressions.

---

## [0.6.6] — 2026-05-13 — Inventory + apply-mark consistency + SPA polish (Sesja 66)

Sesja 66. Operator regression report on `DP5520WMK`: VSCode 1.119.1 →
1.120.0 was upgraded manually, but `ascendo build-inventory` still
reported the web row as `installed=1.119.1, candidate=1.120.0, outdated`
even after the latest full update run had `check__web.json` correctly
showing 1.120.0. Plus IMG to ISO was being re-applied on every full
run despite Sesja 63's apply-mark already persisting the target.

### Fixed

- **Post-apply overlay no longer leaks across runs.** `_latest_check_overlay`
  in `core/ascendo/dashboard/routes/spa_real.py` was walking apply/verify
  sidecars from ALL prior runs in `post_apply_payloads`. An OLD
  `triggered` apply from a previous run (e.g. VSCode 1.119.1 triggered
  at 11:51) would stick because every newer run's `up_to_date` status
  is skipped by the overlay (only `success`/`triggered` overlay). Fixed
  to only consider apply/verify payloads from the SAME RUN as the chosen
  check baseline. +3 regression tests in `tests/contract/test_overlay_same_run_only.py`.
- **plan.ps1 + apply.ps1 now honour Sesja 63's apply-mark.** Previously
  only `check.ps1` consulted `Get-AscendoApplyMark`. For packages whose
  `winget list Version=Unknown` BOTH before and after a successful
  upgrade (SoftSea.IMGtoISO is the canonical example), check correctly
  reported `up_to_date` but plan classified them as `planned` and apply
  re-ran the upgrade. Plan now skips marked packages; apply emits
  `status=up_to_date` without invoking winget. +5 regression tests in
  `adapters/windows/tests/test_winget_apply_mark_in_plan_and_apply.py`.
- **i18n cleanup.** Polish help / about / history / settings sections
  in `app/frontend/i18n.js` had 3-4× duplicated entries from a previous
  bad merge — fixed surgically (lines 1828-2069 trimmed; file went from
  2187 → 2041 lines). Both EN + PL now have a `windows: {…}` Help block
  describing all 8 managers (winget, msstore, npm, pip, web, plugin,
  registry_arp, windows_update) and the Sesja 63-65 mechanisms (apply-
  mark, fake-success detection, Tier-A silent install, web/winget dedup).

### Added

- **History → REPORT.md link.** Every row in the History tab now has a
  📄 link opening `/runs/{id}/report` in a new tab. The endpoint at
  `core/ascendo/dashboard/routes/runs.py:458` was already implemented
  but the SPA never surfaced it. EN + PL i18n keys `history.report` +
  `history.view_report`.

### Test count

448 (Sesja 65) → **453 passing** on Windows (+5 apply-mark regression
tests). +3 contract tests for the overlay fix. Zero regressions.

---

## [0.6.3] — 2026-05-12 — Version polarity across all phases + new logos + ascendo build-inventory

Sesja 57. Operator audit on `mk-uP5520` surfaced three classes of bug
and one missing CLI feature.

### Fixed

- **Version polarity across the 5-phase pipeline.** check / plan /
  apply / verify scripts emitted "present" items with only `to=$ver`
  set, leaving `from=` empty. The SPA overlay reads
  `from→installed` + `to→candidate`, so the inventory row painted
  `installed=null`. Across snap / apt / brew / npm / pip / flatpak /
  drivers / npm-plan, the relevant `json_add_item` calls now pass
  the version into BOTH `from=` and `to=`. After a fresh check+verify
  pass: 6/6 snap items, 24/24 apt verify items, 4/4 npm verify items,
  3/3 npm-plan force-latest items, 1/1 drivers item all carry
  `current_version` AND `target_version`.
- **Web check no longer surfaces uninstalled apps.** Pass 2 (registry-
  only / "not installed locally") gated behind `ASCENDO_WEB_INCLUDE_
  UNINSTALLED=1` env var. Default behaviour: discovery-only — only
  apps actually on disk appear in the web category. Cursor, Discord,
  and any other registry-listed-but-not-installed app drop out.
- **Auth-modal Enter key.** Explicit `keydown` listener on
  `#sudo-pass` calls `form.requestSubmit()` on Enter, so a focus-race
  in some browser/locale combinations can't swallow the keystroke.
  Native `<form>` + submit-button should already handle it; this is
  belt-and-suspenders.
- **Snap apply post-restart.** The "snap apply script produced no
  sidecar" error class — already mitigated in Sesja 56 by the
  `_BaseManager._salvage_sidecar` recovery path — is confirmed live
  on this host after a dashboard restart. The old failing run pre-
  dated the salvage fix; running uvicorn process needs to be
  restarted (`ascendo web restart`) for the new Python to load.

### Added

- **`ascendo build-inventory`** top-level CLI command. Standalone
  equivalent of the dashboard's Overview "Build inventory" button.
  Idempotent; per-source summary; flushes to
  `~/.ascendo/inventory.db`. Honours `ASCENDO_INVENTORY_DB` env;
  `--no-db` skips DB flush; `--verbose` traces. Live on this host:
  2588 packages across 6 sources.

### Changed

- **Brand assets** synced to the Ascendo design system. `app/frontend/
  favicon.svg` (browser tab icon) was still the pre-Sesja-30 green→
  blue gradient mark; replaced with the lime (`#C8FF4B`) bars on ink
  (`#0B1020`) design. Same fix for `branding/icon.svg` +
  `branding/logo.svg` (tooling source) and `app/frontend/assets/
  logo-mark-light.svg` (added the paper-bg rect that was missing).
  Tauri PNG/ICO regen via `bin/regenerate-icons.sh` requires
  ImageMagick — re-run before the next desktop build.

---

## [0.6.2] — 2026-05-12 — Linux production-readiness + .deb editions

Sesja 56. Focuses on putting the Ubuntu adapter into shippable shape:
edition-aware .deb installer (basic + dev), defensive sidecar salvage
path so a bash script that dies mid-run still leaves a real sidecar
behind, and the drivers row no longer appears as falsely outdated.

### Added

- `packaging/build-deb.sh --edition=basic|dev` flag — bakes the chosen
  edition into `/opt/ascendo/.ascendo-edition` and labels the output
  file as `ascendo-basic_<v>_all.deb` / `ascendo-dev_<v>_all.deb` so
  both artefacts coexist in `dist/`.
- `_BaseManager._salvage_sidecar()` in
  `adapters/ubuntu/ascendo_ubuntu/managers/_base.py` — when a phase
  script exits without firing its `EXIT` trap, the orchestrator now
  finalizes from the pre-allocated `JSON_BUFDIR` instead of
  synthesizing a `failed` stub. Adds an explicit `ASCENDO-SALVAGED`
  diagnostic. Belt-and-suspenders defense against the class of bugs
  that hit snap apply in Sesja 55.

### Changed

- `lib/json.sh::json_init` — honors a pre-set `JSON_BUFDIR` env var
  (the orchestrator now passes one) instead of unconditionally
  allocating a fresh `mktemp -d`. Lets Python recover partial state
  post-mortem.
- `scripts/drivers/check.sh` — NVIDIA "present" item now writes the
  version into both `from=` and `to=` (was: package name → version,
  which the SPA overlay read as `installed != candidate → outdated`).
  Package name moves to `details=`. Inventory drivers row no longer
  appears falsely outdated.
- `.gitignore` — `packaging/deb/opt/` and `packaging/deb/usr/` now
  ignored (auto-generated stage trees; `DEBIAN/*` templates stay
  tracked).

### Removed

- Legacy `packaging/deb/opt/ascendo/` stage tree (191 stale
  files from before the rebrand). The `build-deb.sh` clean-stage step
  already wipes it on each build; this commit removes it from the
  index too.

### Operator notes

- Old `ascendo-dashboard.service` systemd-user unit on
  this host was renamed to `*.disabled-by-ascendo` so it can never
  autostart again. Old + new app state are already separated
  (`~/.local/share/ascendo/` vs `~/.ascendo/`) — no
  config conflict to clean up.

---

## [0.6.1] — 2026-05-11 — Ubuntu adapter parity + production-hardening

Sesja 54 + 55. Brings Ubuntu adapter to full feature parity with macOS
and hardens it against real-world failure modes uncovered during
live-fire operator testing on Ubuntu 24.04.

### Added

- **`adapters/ubuntu/ascendo_ubuntu/managers/elevation.py`** —
  `LinuxElevation(IElevation)` mirrors `MacElevation`. sudo password
  cached in-memory, askpass helper at `adapters/ubuntu/lib/askpass_helper.sh`,
  dashboard `/elevation/auth` + `/elevation/status` endpoints work
  unchanged. 29 tests.
- **`adapters/ubuntu/ascendo_ubuntu/snapshot.py`** —
  `TimeshiftSnapshot(ISnapshot)`. Wraps `sudo -A timeshift --create
  --scripted` + `--list`. Backend slug `"timeshift"`. Degrades to
  "warn" health component when timeshift is missing. Restore
  deliberately omitted (destructive).
- **`adapters/ubuntu/ascendo_ubuntu/managers/scheduler.py`** —
  `SystemdScheduler(IScheduler)`. Per-user systemd timers under
  `~/.config/systemd/user/ascendo-<name>.{service,timer}`. DSL parser
  identical to LaunchdScheduler (DAILY/WEEKLY/MONTHLY/HOURLY/MINUTE).
  33 tests.
- **`adapters/ubuntu/ascendo_ubuntu/managers/web.py`** — 8th
  IPackageManager. Linux-flavored WebManager covering AppImage / GitHub
  releases / release_feed / builtin handlers. Slimmer than macOS (no
  Sparkle/keystone/squirrel — those are Mac-only frameworks). 17 tests.
- **`bin/validate-ubuntu.sh`** — 10-stage / 23-check end-to-end smoke
  harness mirroring `validate-macos.sh` and `validate-windows.ps1`.
- **`LINUX_TESTING.md`** — operator-facing testing guide.
- **Bridge improvements** in `adapters/ubuntu/ascendo_ubuntu/managers/_base.py`:
  `start_new_session=True`, watchdog heartbeat thread (10s silence
  trigger), `>>> starting` marker, auto-injected non-interactive env
  (`DEBIAN_FRONTEND`, `NEEDRESTART_MODE`, etc.), `stdin=DEVNULL`.
- **`lib/json.sh`** SIGINT/SIGTERM trap producing partial sidecar
  with `ASCENDO-INTERRUPTED` diagnostic + canonical exit code (130/143).

### Fixed

- **`require_sudo` clobbered the json EXIT trap.** Snap apply ran
  successfully (refreshed thunderbird visible in stream log) but the
  sidecar was never written → bridge synthesised a failed sidecar
  from "no sidecar produced" error → SPA showed phantom failure.
  Now keepalive killer chains with whatever existing trap was
  registered.
- **Inventory `list.sh` silently skipped npm + pip categories.** Two
  compounding bash bugs: heredoc inside `$(... || true)` is a parse
  error; `python3 - <<'PY'` collides with `printf | python3 -` over
  stdin. Fix: `python3 -c '<inline>'`. Live impact: enumeration
  jumped 2539 → 2579 items (+40 npm/pip rows that were silently
  dropped).
- **SPA inventory overlay never matched check-sidecar items.** Legacy
  bash check.sh emits items with synthetic compound IDs
  (`snap:upgrade:firefox`) but inventory has clean names (`firefox`).
  Now overlay also indexes by trailing colon-segment.
- **`brew --cask --greedy` looked like a hang.** Re-downloaded every
  cask whose version is "latest" or has `auto_updates=true` on every
  apply, easily 10+ minutes per run. Default upgrade is now `--cask`
  only; opt-in via `ASCENDO_BREW_GREEDY=1`.
- **`scripts/pip/plan.sh` emitted `kind=check`** clobbering the real
  check sidecar. Post-processes the sidecar to rewrite kind→plan.
- **`legacy_compat` translator mapped exit_code=1 → status=failed.**
  Per `docs/agents/contract.md`, exit 1 is "warn" (advisories only).
  Three-way mapping: `{0,1 → success, 75 → skipped, else → failed}`.
- **Legacy_compat synthesised a `uuid5` run.id** that mismatched the
  orchestrator's run.id, so post-apply hooks (REPORT.md, update_history,
  dashboard `/runs/{id}`) all 404'd. Bridge now overwrites
  `sc.run.id` after `read_sidecar`.
- **REPORT.md said "macOS web apps"** — fixed to `"Web apps
  (AppImage / GitHub releases / Sparkle)"`.
- **`validate-ubuntu.sh` was too strict** — accepted only `success`,
  rejected `partial`. Real systems hit soft advisories. Now accepts
  both.

### Live test results on `mk-uP5520`

```
$ python3 -m ascendo doctor
adapter: ubuntu (Ubuntu / Debian) tier=1
capabilities: AdapterCapability.PACKAGE_MANAGEMENT|INVENTORY|SNAPSHOTS|SCHEDULING|ELEVATION

$ bash bin/validate-ubuntu.sh
ALL CHECKS PASSED. (23/23)

$ python3 -m ascendo run -c apt,snap,brew,npm,pip,flatpak,web -p check,plan,apply,verify,cleanup
overall: success (35 sidecars, 80 items)

$ sqlite3 ~/.ascendo/inventory.db 'SELECT category, COUNT(*) FROM inventory_items GROUP BY category;'
apt|2476  brew_formula|47  npm|4  pip|36  snap|16
TOTAL: 2579 items, all with installed + candidate populated
```

143/143 ubuntu adapter tests + 13/13 contract `test_legacy_compat` +
9/9 ubuntu_inventory tests green.

---

## [0.6.0-rc1] — 2026-05-09 — Edition split + GUI-PATH fixes

Sesja 51 + 52 + 53. Splits Ascendo into `basic` and `dev` editions from
one repo, fixes a class of macOS GUI-PATH bugs that were poisoning
package installs, ships a clickable .dmg installer with edition baked
into the artefact name.

### Added

- **`ASCENDO_EDITION` flag** (`basic` | `dev`, default `basic`) plumbed
  through dashboard, frontend, helpers, and installers. Basic edition
  hides Sync/Hosts/Logs nav, merges History+Logs inline, removes
  raw-events box. Dev edition keeps the full 12-tab UI.
- **8-cell install matrix** in README — `{basic, dev}` × `{cli, web,
  desktop, full}`. Both editions buildable from one source tree.
- **Smart installers** — `bin/build-dmg.sh` (macOS, baking edition into
  the artefact: `Ascendo-Basic-0.0.7-arm64.dmg` vs
  `Ascendo-Dev-0.0.7-arm64.dmg`), modernized `packaging/build-deb.sh`
  with version sync + `--dry-run` + `--no-symlinks` flags,
  `packaging/homebrew-tap/ascendo.rb` formula stub, NSIS hooks +
  bin-staging mirror in `bin/build-installer.ps1`.
- **`bin/first-run-bootstrap-{macos,linux}.sh` + `.ps1`** — auto-install
  Python ≥ 3.11, git, curl, jq via the platform package manager on
  first launch.
- **`bin/user-scripts/`** — 21 helper shims: `ascendo_update`,
  `ascendo_start_web`, `ascendo_stop_web`, `ascendo_restart_web`,
  `ascendo_start_desktop`, `ascendo_stop_desktop`, `ascendo_doctor`,
  `ascendo_maintenance` (full / quick / dry-run / category=X /
  rebuild-inventory / check-errors), plus dev-only `ascendo_sync` +
  `ascendo_push`.
- **`LINUX_QUICKSTART.md`** mirroring the macOS / Windows quickstart
  structure (12 sections).
- **`docs/PLATFORM_STATUS.md`** — honest cross-platform feature matrix
  across 13 sub-tables, known gaps per platform, scoped roadmap.
- **`DEV_GUIDE.md`** — 507-line contributor guide.
- **`USER_GUIDE.md`** rewritten as basic-edition end-user guide
  (444 lines, all dev surfaces stripped).
- **Two onboarding wizards** — basic = 6 steps; dev = 9 steps with
  GitHub repo config + dev-sync setup + dev-resources panes.
- **Public-repo audit** — `docs/PUBLIC_AUDIT.md` + corrected `.gitignore`
  keep AI instructions, internal handoffs, and per-user dev-sync config
  private; dev-sync TOOLING (Python lib + 15 wrapper scripts) stays
  public so dev-edition users can bootstrap their own overlay against
  any rclone-supported provider.
- **`bin/dev-sync-overlay-migrate.sh`** + `dev-sync-overlay/` skeleton —
  copy-only migration tool for staging private files into the
  Proton-synced overlay before public-repo flip.
- **Cross-platform parity quick wins** — Linux apply.sh scripts
  (apt/snap/brew/npm/pip/flatpak) capture stderr-tail into sidecar
  diagnostics + emit SSE live-stream events; Windows
  msstore/arp/windows_update apply.ps1 also stream live.
- **`EditionGateMiddleware`** in `core/ascendo/dashboard/middleware/` —
  404s `/sync/*`, `/hosts*`, `/git/push*`, `/dev-sync*`,
  `/profiles/import*` when edition=basic.
- **`/sync/config-status` + `/sync/setup` endpoints** — dev-only,
  feed the wizard's dev-sync setup step.

### Fixed

- **Tauri shell crashed on launch (`Ascendo quit unexpectedly`).** Root
  cause: macOS GUI-launched apps inherit only the launchctl PATH
  (`/usr/bin:/bin:/usr/sbin:/sbin`), so `Command::new("ascendo")`
  failed with ENOENT and `.expect()` panicked during
  `applicationDidFinishLaunching:`. Fix: `locate_sidecar()` probes
  6+ absolute paths first; spawn failures return `Option<Child>`
  instead of panicking; WebView opens an embedded recovery page with
  the exact `sudo ln -sf` one-liner.
- **opencode-cli npm postinstall failed: `bun: command not found`.**
  The Tauri-launched dashboard's `sh -c` postinstall subshell didn't
  see `~/.local/share/mac-update/node/bin/node` or `~/.bun/bin/bun`.
  Fix: npm/apply.sh extends PATH with the toolchain node + bun bin
  dirs + brew + `~/.local/bin` before invoking npm.
- **Pip installed packages into Xcode Python 3.9.** The dashboard
  resolved `pip3` via launchctl PATH → `/usr/bin/pip3` → Apple's
  framework Python. Every CLI (poetry, ruff, mypy, etc.) silently
  installed into `~/Library/Python/3.9/bin/`. Fix:
  `ascendo_pip_pip_bin` and `ascendo_pip_python_bin` probe
  `/opt/homebrew/bin/pip3` first AND explicitly REJECT
  `/usr/bin/pip3` / `/usr/bin/python3` (Xcode shims). Plus
  `_augment_path_for_macos_gui()` prepends 8 known-good dirs at
  dashboard startup so all spawned subprocesses inherit the right env.
- **Apps view kept showing "outdated" after successful apply.** The
  `/inventory/db/refresh` endpoint walked only **check** sidecars when
  rebuilding inventory — post-apply truth from verify sidecars was
  overwritten with stale pre-apply data. Fix: `_latest_check_overlay`
  walks check / apply / verify newest-first with phase-priority
  tie-break (`verify > apply > check`). Operator's opencode-cli now
  correctly reflects 1.14.44 in the SPA after upgrading from 1.14.43.
- **`bin/build-dmg.sh` failed at cargo build** with
  `glob pattern bin-staging/**/* path not found`. Sesja 52 added the
  `bundle.resources` glob but only `bin/build-installer.ps1` populated
  it. Fix: `bin/build-dmg.sh` mirrors the step before Tauri.
- **npm/pip apply re-installed everything every run** even when
  packages were already at latest. Fix: up_to_date guard in apply_npm
  / apply_pip / apply_native_node / apply_native_bun reads installed
  + latest before invoking install, skips if equal. Cache-bust after
  successful install so the post-install version lookup reflects the
  fresh state instead of the pre-install snapshot.
- **SSE stream emitted every line twice.** Server-side: `_stream.log`
  matched the `*.log` glob, so the per-run log_files list contained
  the same path twice (explicit append + glob). Fix: dedupe by Path
  identity. Frontend: `ui.attachStream()` created a fresh EventSource
  without closing prior ones, accumulating N stale ESes that all
  appended to the same DOM. Fix: track all spawned ESes on
  `window._ascendoActiveStreams` and close them at the start of every
  attachStream call.

### Tests

- 683 green: 290 contract + 393 macOS adapter (9 pre-existing
  Windows-only test_service_endpoints failures unchanged).

### Pending real-hardware validation (next session)

- Real-Ubuntu mk-uP5520 — verify new Linux apply paths
- Tauri MSI/NSIS build on Windows DP5520WMK
- Real-public-flip: bin/dev-sync-overlay-migrate.sh + git rm + tag
  v0.6.0 + GitHub make-public

---

## [0.5.2] — 2026-05-09 — Cross-platform parity + one-line install/update

Sesja 45. Brings Windows + Ubuntu adapters up to functional parity with
macOS v0.5.1 and ships true one-line install + update for all three OSes.
**841/848 tests green** (9 pre-existing service_endpoints failures + 7
platform-specific skips).

### Added — Ubuntu (transitions from stub to Tier-1)

- **`adapters/ubuntu/ascendo_ubuntu/`** — full Python adapter scaffold:
  `UbuntuAdapter` + 7 managers (apt/snap/brew/npm/pip/flatpak/drivers)
  + `UbuntuInventory` + `BashPhaseManager` base. Capabilities
  `PACKAGE_MANAGEMENT | INVENTORY`. Bridges to mature legacy bash
  scripts at top-level `scripts/<cat>/<phase>.sh` via env-var IPC
  contract matching `lib/orchestrator.sh`. Schema translation
  transparent via `parse_sidecar()`.
- **`adapters/ubuntu/scripts/inventory/list.sh`** (427 LOC) — full
  inventory enumeration across apt+snap+flatpak+brew+npm+pip with
  10s timeout per tool, graceful skip on missing CLIs, single
  ascendo/v1 sidecar with `<source>:<package>` IDs.
- **`SourceType.DRIVERS` + `SourceType.FIRMWARE`** in core enum;
  legacy translator `'drivers' → SourceType.DRIVERS` (was UNKNOWN).
- 36 new Ubuntu adapter tests + 9 inventory tests; mock-based
  (no real apt/dpkg required).

### Added — Windows parity fixes

- **stderr capture in apply.ps1 × 4 sources** (winget/msstore/arp/
  windows_update). On non-zero exit, last 12 stderr lines (capped at
  1500 chars) appended to sidecar messages — operator finally sees
  actual error reason instead of "exited N". winget+msstore use
  `Start-Process -RedirectStandardError`; windows_update uses
  `-ErrorVariable` (cmdlet, not subprocess).
- **Pre-dispatch up_to_date guard** in winget + msstore apply — skips
  packages where installed == latest. Mirrors macOS `web/apply.sh`
  Sesja 40 pattern.
- 6 new regression tests; 99/99 Windows tests pass.

### Added — One-line install + update for all three OSes

- **`install.sh`** (rewrite, 451 LOC) — adds `--update` / `--reinstall` /
  `--verbose` / `--non-interactive`, env-var overrides
  (`ASCENDO_LANG`, `ASCENDO_PROFILE`, `ASCENDO_HOME`,
  `ASCENDO_NONINTERACTIVE`, `ASCENDO_REPO_URL`, `ASCENDO_BRANCH`),
  network preflight, disk-space check, locked-package-manager
  detection (apt fuser), final `ascendo doctor` self-test that bails
  on non-zero.
- **`update.sh`** (new, 187 LOC) — POSIX one-liner. `git pull
  --ff-only` (refuses to merge), refresh editable installs, restart
  any running dashboard via pgrep, version delta print.
- **`install.ps1`** (new, 382 LOC) — Windows `iwr | iex` one-liner.
  PowerShell 5.1 + 7.x compatible. Detects + auto-installs Python 3.12
  via winget, refuses Win < 10 b17763, shim at
  `%LOCALAPPDATA%\Microsoft\WindowsApps\ascendo.cmd`.
- **`update.ps1`** (new, 147 LOC) — Windows updater. Restarts
  `AscendoDashboard` Windows service if installed.
- **32 new contract tests** for installer entrypoints (argv parsing,
  help text, env-var wiring); pwsh AST validation skipped on hosts
  without pwsh.

### Fixed — Cross-cutting

- **`_flush_run_to_inventory_db` clears categories before bulk_upsert**
  (Sesja 40 added clear_category to 3 paths but missed the 4th —
  post-run flush in `run_async.py`). User's local DB had 312 web
  rows when discovery emitted 37; root cause fixed.

### One-liners

```bash
# macOS / Linux install:
curl -fsSL https://raw.githubusercontent.com/KasprowiczM/ascendo/main/install.sh | bash
# macOS / Linux update:
curl -fsSL https://raw.githubusercontent.com/KasprowiczM/ascendo/main/update.sh | bash
```
```powershell
# Windows install:
iwr -useb https://raw.githubusercontent.com/KasprowiczM/ascendo/main/install.ps1 | iex
# Windows update:
iwr -useb https://raw.githubusercontent.com/KasprowiczM/ascendo/main/update.ps1 | iex
```

---

## [0.5.1] — 2026-05-09 — 5 operator bug fixes + portability doc

Sesja 44. Five operator-reported issues plus an architectural Q&A
documented as `docs/PORTABILITY.md`. 391/391 macOS tests + 249 contract
tests.

### Fixed

- Brave x86_64 mac bundle replaced with arm64; new
  `download_asset_pattern` field on release_feed selects universal DMG
  from GitHub release assets.
- `.npmrc prefix=` line stops coming back — `npm config set prefix`
  replaced with `NPM_CONFIG_PREFIX` env var + `scrub_npmrc` helper.
- Categories collapse-back fixed via missing CSS rule
  `.cat-detail.hidden { display: none }`.
- Touch ID sudo cache now honoured — `/sudo/status` probes `sudo -n -v`
  (1s cap) when no SPA password registered.
- Discovery brew classification fixed — `_flatten()` handles str/list,
  app filename matching, zap.trash plist mining, opt-in codesign deep
  ownership.

---

## [0.2.0] — 2026-05-05

**macOS adapter feature-complete (M5 done). Tier-1 minus source-verification.**
Tested on Mac.r12.home (Apple Silicon, macOS 15.x, bash 3.2.57,
Homebrew 5.1.9, mas 7.0.0, Python 3.13, jq 1.8.1).
**34/34 PASS** via `bin/validate-macos.sh`.

### Added

- **`adapters/macos/ascendo_macos/managers/scheduler.py`** — `LaunchdScheduler`
  implements `IScheduler` via per-user launchd LaunchAgents. Plists at
  `~/Library/LaunchAgents/dev.ascendo.<name>.plist`; description metadata
  in sidecar JSON at `~/Library/Application Support/Ascendo/schedules/<name>.json`.
  DSL mirrors WindowsScheduler exactly (DAILY / WEEKLY / MONTHLY / HOURLY /
  MINUTE → `StartCalendarInterval` plist dict; MINUTE → `StartInterval`).
- **`adapters/macos/scripts/scheduler/scheduler.sh`** — bash 3.2 driver
  for the launchd backend (install / uninstall / list / get / trigger).
  Idempotent `bootout`-then-`bootstrap` semantics. Argv-only contract;
  name regex `^[a-z0-9-]+$` enforced before plist filename interpolation.
- **`adapters/macos/ascendo_macos/managers/softwareupdate.py`** —
  `SoftwareUpdateManager` for macOS OS updates. `sudo -A softwareupdate
  -i ... -R --verbose` (the `-R` flag is mandatory).
- **`adapters/macos/ascendo_macos/snapshot.py`** — `TimeMachineSnapshot`
  read-only via `tmutil listlocalsnapshots /`. `create()` raises
  `SnapshotError` per APFS auto-management.
- **`adapters/macos/ascendo_macos/inventory.py`** — `MacOSInventory` via
  `system_profiler -json -detailLevel mini SPApplicationsDataType`. 387
  apps enumerated on Mac.r12.home with 5-rule classification (SYSTEM /
  MAS / BREW / WEB).
- **`adapters/macos/ascendo_macos/managers/mas.py`** — `MasManager` for
  the Mac App Store via `mas` CLI. `sudo mas upgrade <id>` enforced
  (CVE-2025-43411 mitigation).
- **`adapters/macos/ascendo_macos/managers/elevation.py`** —
  `MacElevation` (`IElevation` impl) with sudo askpass cache for
  dashboard-driven sudo. `POST /elevation/auth` round-trip on the
  dashboard.
- **`adapters/macos/ascendo_macos/managers/brew.py`** — `BrewManager`
  for Homebrew formulae + casks via `brew outdated --json=v2`.
- **`bin/install-dev-macos.sh` / `bin/validate-macos.sh` /
  `bin/run-tag-release-macos.sh` / `bin/launch-desktop-macos.sh`** —
  full bash equivalents of the Windows PowerShell launcher set.
- **`MACOS_QUICKSTART.md` / `MACOS_TESTING.md` / `USER_GUIDE.md`** —
  end-user-facing docs (operator install, full test matrix, cross-OS
  three-interface walkthrough).
- **Tauri 2.x macOS bundle** — `tauri.conf.json` `targets: "all"` now
  produces `.app` + `.dmg` on macOS (unsigned — code signing is M6).

### Changed

- `MacOSAdapter.capabilities` now declares the full Tier-1 minus
  `SOURCE_VERIFICATION`: `PACKAGE_MANAGEMENT | ELEVATION | INVENTORY |
  SNAPSHOTS | SCHEDULING`. `health_check()` now reports 10 components
  (was 9): added `launchctl`.
- `core/ascendo/models/sidecar.py`: `needs_reboot` moved from `Summary`
  to top-level `Sidecar` (consumer fix — dashboard router + CLI helper
  both read from the top level; Summary placement would have silently
  dropped the reboot signal on macOS softwareupdate runs).
- `Tauri 2.x`: bundle `targets` from `["msi", "nsis"]` to `"all"` so
  macOS / Linux builds produce native artefacts (.app/.dmg, .deb/.AppImage).

### Fixed

- **Critical (M5.5.11.1)** — `LaunchdScheduler._invoke` was passing
  `--output` / `--payload` to `scheduler.sh`, but the bash driver only
  accepts `--output-path` / `--payload-path`. Every `IScheduler` call on
  a real Mac would have failed with bash exit 2 (`unknown arg: --output`).
  Mock-only Python tests didn't catch it. Fix: rename to `--output-path` /
  `--payload-path`. Added regression test
  `test_invoke_with_payload_uses_payload_path_flag` so this can't drift
  silently again.
- **Important (M5.5.11.1)** — `trigger()` on a non-existent schedule
  silently returned `None` instead of raising `SchedulerError`. The bash
  driver emits `{"error": "no such schedule"}` + exit 30; Python's
  `_invoke` was returning the error dict and `trigger() -> None` was
  discarding it. Fix: when bash returns non-zero AND output JSON has an
  `"error"` key, `_invoke` raises `SchedulerError(error)`.
- **Operator-validation hotfix (M5.5.11.2)** — `bin/validate-macos.sh`
  Stage 12.2 was passing `--expression` to `python3 -m ascendo schedule
  install`, but the CLI's flag is `--calendar` (matches the Windows
  scheduler's term, predates M5.5). Fix: one-character change in
  validate-macos.sh.

### Tests

- 242 passing (was 158 on Windows-only at v0.0.7) on macOS:
  ~46 brew (M5.1) + ~63 mas/elevation (M5.2) + ~19 inventory (M5.3) +
  ~56 softwareupdate/snapshot (M5.4) + ~58 scheduler (M5.5).
- 34/34 end-to-end via `bin/validate-macos.sh` Stage 1-12 (CLI +
  dashboard + brew + mas + LaunchServices inventory + softwareupdate +
  Time Machine + launchd scheduler).

---

## [0.0.7] — pending tag (in flight)

**Windows MVP feature-complete + branded installer + first-run wizard.**
First publicly installable Ascendo build. Tested on Dell Precision 5520,
Windows 11 Pro Build 26200, PowerShell 7.6.1, winget 1.28.240, Python 3.14.

### Added

- **`packaging/winget-manifest/`** — Microsoft winget submission manifest
  (3 YAML files per spec 1.6.0): `Ascendo.Ascendo.yaml`,
  `Ascendo.Ascendo.installer.yaml`, `Ascendo.Ascendo.locale.en-US.yaml`,
  plus a submission `README.md`. Hashes filled at release time by
  `bin/build-installer.ps1`.
- **`branding/SLOGANS.md`** — single source of truth for marketing copy.
  Tagline `Unified updates. Every app. One click.` Installer banner,
  About modal, wizard welcome, Tauri config, and READMEs all pull from
  this file.
- **Windows-flavoured Help section** in the dashboard: 11 sections
  (Install / First run / CLI / Scripts / Config / Dashboard /
  Scheduler / Snapshots / Dev-sync / AI / Troubleshoot) explicitly
  cover Tauri shell + `bin/install-dev.ps1` install paths,
  `python -m ascendo` cheat-sheet, the 4 Windows package sources
  (winget / msstore / registry_arp / windows_update), Volume Shadow
  Copy snapshot/restore, and Windows-specific troubleshooting.
- **`auth.cached`-style i18n keys** with Windows wording: every "sudo"
  reference in the SPA now resolves through `tr()` to "Administrator
  authorized" / "not authorized" / "credentials needed" / "session
  expired" / "authentication cancelled". Polish parallel.
- **`docs/superpowers/specs/2026-05-02-ascendo-windows-end-to-end-design.md`** —
  the design that drove this milestone (CLI polish + dashboard wiring +
  frontend apply UX + Tauri 2.x scaffold).

### Changed

- Repository consolidated to a single `main` branch; the three sibling
  `claude/*` worktrees from earlier sessions reconciled into a linear
  history (no merge conflicts — pedantic was a strict ancestor of
  windows-end-to-end). All future work happens on `main`.
- `CLAUDE.md` rewritten for the monorepo layout
  (`core/` + `adapters/{ubuntu,windows,macos}/` + `ui/` + `plugins/`)
  and hard-codes a "no new worktrees" rule for Claude Code sessions.
- README rewrites the hero with the unified-updates pitch + Windows-first
  badge, and adds a per-platform feature matrix.

### Fixed

- 6 pre-existing `adapters/windows/tests/` failures that survived earlier
  sessions: `OperatingSystem.LINUX` references corrected to
  `OperatingSystem.LINUX_UBUNTU` (the enum never had a `.LINUX`); the
  `test_adapter_package_managers_includes_windows_update` assertion
  updated from `len() == 2` (M3.8 era) to the post-M3.15 contract of 4
  managers (winget / msstore / arp / windows_update).
- `test_windows_update_manager_smoke.py` no longer asserts a stale ordering
  contract; bookend assertions (winget first, windows_update last) plus a
  set-membership check for all 4 expected managers.

### Verified

- `python -m pytest adapters/windows/tests/ plugins/dell-driver-update/tests/ ui/desktop-tauri/tests/` → 70 + 8 + 5 = 83 pass.
- `bin/validate-windows.ps1 -DashboardPort 8770` → ALL CHECKS PASSED on real hardware: 210-package inventory bucketed across 4 sources, async run completes in ~23s, every dashboard endpoint healthy.

---

## [0.0.1-alpha] — 2026-05-01

**First end-to-end working build, validated on real Windows hardware
(Dell Precision 5520, Windows 11 Pro Build 26200, PowerShell 7.6.1,
winget v1.28.240, Python 3.14).**

A full ``python -m ascendo run --category winget --phase check`` invocation
exercises every layer of the architecture and exits 0 with a valid
``ascendo/v1`` sidecar. The dashboard binds, ``GET /version`` and ``/health``
work, ``POST /runs/async`` returns a run id, and ``GET /runs/{id}/status``
reaches ``completed``.

### Added — M1 (foundation)

- Monorepo restructure: `core/`, `adapters/{ubuntu,windows,macos}/`,
  `contrib/`, `plugins/`, `ui/`, `packaging/`, `website/`, `tests/`,
  `docs/architecture/`.
- 7 ADRs (`docs/architecture/0001` … `0007`):
  monorepo-with-adapters, tauri-as-desktop-shell, json-v1-sidecar-contract,
  python-core-with-native-script-adapters, six-layer-architecture,
  two-tier-adapter-system, plugin-manifest-v1.
- `HANDOFF.md` — cross-session resume document.
- `.gitattributes` (LF for source, CRLF for `.ps1`/`.bat`/`.cmd`),
  `.pre-commit-config.yaml` (gitleaks, ruff, mypy, shellcheck,
  PSScriptAnalyzer, markdownlint, plugin manifest validator).
- pyproject.toml workspace: root + `core/` + 3 adapter packages with
  hatchling build backend, importlinter contracts.
- Top-level docs: `LICENSE` (MIT), `CONTRIBUTING.md`, `SECURITY.md`,
  `CHANGELOG.md`.

### Added — M2 (core skeleton)

- **Pydantic v2 models** in `core/ascendo/models/`:
  `Sidecar` / `RunInfo` / `HostInfo` / `Item` / `Summary` / `Message` /
  `ToolInfo` / `Phase` / `PhaseStatus` / `Trigger` / `OperatingSystem` /
  `ElevationMethod` / `SourceType` / `ItemEvidence` / `ItemRollback`.
- **Six core interfaces** in `core/ascendo/interfaces/`: `IPackageManager`,
  `IInventory`, `ISnapshot`, `IScheduler`, `ISource`, `IElevation`, plus
  `IAdapter` aggregate root + `AdapterCapability` flag enum.
- **Adapter factory** (`core/ascendo/adapter_factory/`): `detect_os()`,
  `AdapterRegistry` with importlib.metadata entry-points + direct-import
  fallback, `select_adapter()` with `linux_*` → `linux_ubuntu` Tier-1
  fallback path.
- **JSON Schema export** at `docs/architecture/schemas/sidecar.v1.schema.json`
  (823 lines, JSON Schema 2020-12, regenerated by
  `scripts/export-sidecar-schema.py`).
- **Sidecar I/O layer** (`core/ascendo/orchestrator/sidecar_io.py`):
  cross-OS file locking (POSIX flock + Windows msvcrt), atomic writes,
  partial-sidecar recovery, jittered exponential backoff.
- **Legacy translator** (`core/ascendo/models/legacy.py`): converts
  pre-rename `ascendo/v1` payloads into `ascendo/v1`
  per ADR-0003 backward-compat.
- **i18n loader** (`core/ascendo/i18n/`): 7 locales × 42 keys
  (en/pl/es/it/pt/de/fr) ported from macOS bash; locale detection
  via ASCENDO_LOCALE / LANG / GetUserDefaultLocaleName / fallback.
- **Orchestrator** (`core/ascendo/orchestrator/runner.py`): `run_phases()`
  drives an `IAdapter` through requested phases, persists every sidecar,
  aggregates as `RunReport` with `overall_status`, `by_category()`,
  `by_phase()`, `total_items`, `aborted_after_phase`.
- **Async run + SSE** (`core/ascendo/orchestrator/run_async.py`):
  `RunRegistry` (thread-safe, bounded LRU), `start_run_async()` via
  `asyncio.to_thread`, lifecycle states (pending/running/completed/failed).
- **Typer CLI** (`core/ascendo/cli/`): `version`, `run`, `doctor` commands
  + placeholders for `schedule`, `snapshot`. `python -m ascendo` and
  `python -m ascendo.cli` both work as PATH-independent entry points.
- **Dashboard** (`core/ascendo/dashboard/`): FastAPI app with `GET /version`,
  `GET /health`, `POST /runs` (sync), `POST /runs/async`, `GET /runs/{id}/status`,
  `GET /runs/{id}/events` (Server-Sent Events stream), `GET /runs` (index),
  `GET /runs/{id}` (sidecars).
- **Contract tests** (`tests/contract/`): 41 tests covering sidecar v1
  schema, legacy compat, sidecar I/O concurrency + recovery, runner,
  dashboard sync + async + SSE.

### Added — M3 (Windows MVP)

- **PowerShell library modules** in `adapters/windows/lib/`:
  `AscendoJson.psm1` (sidecar emitter, ~626 LOC), `AscendoWinget.psm1`
  (column-position parser + exit-code mapping, ~783 LOC),
  `AscendoWingetActions.psm1` (process-kill map, uninstall-first map,
  skip list, ~570 LOC).
- **PowerShell phase scripts** in `adapters/windows/scripts/winget/`:
  `check.ps1`, `plan.ps1`, `apply.ps1`, `verify.ps1`, `cleanup.ps1`.
  All 5 phases of the contract.
- **Python WingetManager** (`adapters/windows/ascendo_windows/managers/winget.py`):
  spawns pwsh via subprocess with `[switch] $DryRun` idiom, parses
  emitted sidecar via core `read_sidecar`, maps exit codes.
- **WindowsAdapter** (`adapters/windows/ascendo_windows/adapter.py`):
  IAdapter implementation with capability=PACKAGE_MANAGEMENT, real
  health_check (winget version, pwsh version, lib presence).

### Added — packaging + DX

- `bin/install-dev.ps1` — one-shot Windows dev install
  (core + adapter + dashboard deps + auto-validate).
- `bin/validate-windows.ps1` — end-to-end automated validation harness
  (CLI commands + sidecar shape + dashboard sync + async + SSE +
  status polling).

### Known limitations (carried into 0.0.2)

- `AscendoWinget.psm1`'s `Read-WingetTabularOutput` collapses adjacent
  AppX/MSIX rows into a synthetic super-row (observed on the
  AutoHotkey block on real DP5520WMK winget output). Tracked as M3.X
  follow-up.
- `M2.7` backend migration is partial — only the new Layer 3 endpoints;
  the legacy `app/backend/*.py` files (auth, db, scheduler, hosts) are
  not yet migrated.

### Changed

- Monorepo restructure (rebrand `Ascendo` → `ascendo`):
  - JSON sidecar schema renamed `ascendo/v1` → `ascendo/v1`.
    Reader accepts both during the migration period.
  - Repository origin: new GitHub repo at
    `https://github.com/KasprowiczM/ascendo` (parent local clone:
    `D:\Dev_Env\Ascendo`).
  - Pre-restructure state preserved at git tag
    `pre-monorepo-restructure` for rollback if needed.

### Validated end-to-end on real hardware

DP5520WMK (Dell Precision 5520, Win 11 Pro 26200, PowerShell 7.6.1,
winget v1.28.240, Python 3.14):

```
==> ascendo run --category winget --phase check
  [PASS] run command exited 0/1 (not crashed)         exit=0
  [PASS] run produced at least one sidecar
  [PASS] sidecar has schema=ascendo/v1
         sidecar.status     = success
         sidecar.tool       = winget 1.28.240
         [INFO] Found 1 package(s) with upgrades available.
==> ascendo dashboard smoke
  [PASS] dashboard binds to 127.0.0.1:8765
  [PASS] GET /version
  [PASS] GET /health   status=ok
  [PASS] POST /runs/async returns run_id
  [PASS] GET /runs/{id}/status reaches completed/failed
ALL CHECKS PASSED.
```

---

## Pre-monorepo history (Ascendo legacy)

The following entries are from the source project before rename + restructure.

### [Etap 12] - 2026-04-XX

- Inventory candidate fix
- Unified Updates rename (Ascendo brand introduction)
- Tauri shell prototype
- Hybrid CLI/Dashboard mode
- Snapshot tooling (timeshift / etckeeper)
- Scheduler (systemd timers)
- Plugin system infrastructure (manifest validator)
- Dev-sync GitHub + Proton overlay

For full pre-monorepo history, see git log:
```bash
git log --oneline pre-monorepo-restructure
```

[Unreleased]: https://github.com/KasprowiczM/ascendo/compare/pre-monorepo-restructure...HEAD
