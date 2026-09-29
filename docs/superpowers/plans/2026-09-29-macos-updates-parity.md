# Plan — Ascendo macOS adapter parity with macOS_updates v1.5.2

**Spec (binding authority):** the six gap reports in
`docs/superpowers/specs/2026-09-29-macos-updates-parity/` (`brew.md`, `cli.md`, `system-appstore.md`,
`web-vendor.md`, `web-updaters.md`, `inventory-orchestration.md`; shared context in `_common.md`).
They compare macOS_updates `84e7a01..e5b39a2` (v1.4.2 → v1.5.2) with Ascendo `fda5aba` (1.0.4).
Every task below names the report sections ("Spec X") it implements. Read those sections in full;
they contain the algorithms, config rows and test ideas. When a report says "copy from macOS_updates
file X", the source is `/Users/mk/Dev_Env/macOS_updates` (read-only — never edit it).

## Global Constraints

1. Work directly on `main` in `/Users/mk/Dev_Env/Ascendo` (project rule: no worktrees). Commit per task
   with a Conventional Commit message; do not push, do not tag.
2. **Never run a real mutating update on this Mac** (no real `brew upgrade`, `mas upgrade`,
   `softwareupdate -i`, `msupdate --install`, vendor installers, `/Applications` writes). Tests use fake
   binaries on `PATH` / env overrides / temp dirs, following the existing tests in `adapters/macos/tests/`.
   Read-only probes (`--version`, `-l`, `mas list`, `brew info --json`) are allowed.
3. Bash **3.2** only in `adapters/macos/**/*.sh` (no `declare -A`, `mapfile`, `readarray`, `${x,,}`, `|&`).
   Python helpers invoked from bash use `python3` from `PATH` (3.11+).
4. Hard rules: `softwareupdate` installs always keep `-R`; `mas upgrade` always runs under sudo
   (CVE-2025-43411); dashboard runs are TTY-less — no `read` prompts, never auto-accept licences,
   never auto-uninstall; **never quit/kill an app the user already had open**; `ASCENDO_SAFE_MODE`
   (safe/quick profiles) never launches app GUIs; a major macOS upgrade is never installed automatically.
5. Honesty: a probe that fails or times out is `unknown`/`skipped` with a reason — never `up_to_date`
   and never `success`. `success` requires a post-action measurement.
6. Sidecar contract unchanged: `ascendo/v1` via `adapters/macos/lib/ascendo_json.sh` /
   `_json_emit.py`; item statuses from `core/ascendo/models` (`success`, `failed`, `skipped`, `planned`,
   `up_to_date`, `triggered`, `missing`, `partial`). Machine-readable reason codes go in message text
   / item messages (e.g. `XCODE_LICENSE`, `CASK_ORPHAN`, `feed_stale`, `rollout_hold`, `needs_restart`).
7. `core/` changes must keep Windows/Ubuntu behaviour intact (shared code). Run
   `PYTHONPATH=core:adapters/macos python3 -m pytest tests/contract -q` for any `core/` change.
8. Test gate per task: `PYTHONPATH=core:adapters/macos python3 -m pytest adapters/macos/tests -q`
   must stay green (baseline 452 passed) plus the new tests; `bash -n` every touched `.sh`.
   Bash-harness tests (`adapters/macos/tests/*.sh`) touched by the task must pass too.
9. Opt-in only (default OFF) for behaviour that conflicts with Ascendo safety rules:
   `ASCENDO_MAS_USER_RETRY=1` (mas retry without sudo), `ASCENDO_MAU_SCOPED_INSTALL=1`
   (silent `msupdate --install`), `ASCENDO_ALLOW_MAJOR_UPGRADE=1`, `ASCENDO_BOOTSTRAP_CLI=1`,
   `ASCENDO_INSTALL_ROSETTA=1`.
10. No new third-party Python dependencies. Network fetches: https only, `--proto =https`, bounded size
    and time.

---

### Task 1: Shared bounded process runner (timeouts that actually kill)

Implements `inventory-orchestration.md` Spec 3, `web-updaters.md` Spec A, `cli.md` Spec P2.

- Add one bash helper `ascendo_run_with_timeout SECONDS CMD...` (new `adapters/macos/lib/ascendo_proc.sh`,
  sourced by `ascendo_json.sh` so every phase script has it): GNU `timeout --kill-after=5` / `gtimeout`
  when present; otherwise run the child in its own process group (python3 `os.setpgrp` wrapper or
  equivalent) and SIGTERM → 5 s → SIGKILL the **group**; rc 124 on timeout, map 137→124 only when
  elapsed ≥ deadline; stdin detached (`</dev/null`) unless the caller opts out.
- Replace `ascendo_npm_run_with_timeout` (`lib/ascendo_npm.sh`) and `_web_run_apply_cli`'s kill path
  (`lib/ascendo_web.sh`) with the shared helper (keep the old function names as thin wrappers if tests
  or callers use them).
- Python managers (`adapters/macos/ascendo_macos/managers/*.py`): the per-phase timeout must fire even
  when the child prints nothing (use a wall-clock deadline, not per-line reads) and must kill the whole
  process group (`start_new_session=True` + `os.killpg`). On timeout still read/salvage whatever sidecar
  exists and report `failed` with a timeout message rather than losing it.
- Tests: TERM-ignoring child killed within deadline+kill-after; silent child killed by Python manager;
  grandchild dies with the group; 137→124 mapping; existing npm/web timeout tests updated.

### Task 2: Core orchestration honesty — report, filter plumbing, action-required

Implements `inventory-orchestration.md` Spec 1 and Spec 2, `system-appstore.md` row 21 (filter bug) and
Spec D.

- `core/ascendo/orchestrator/report.py`: REPORT.md must never say "all updates applied successfully" /
  "Failed (0)" when any phase sidecar of the run (incl. verify/cleanup) is `failed`/`partial`; surface
  failed steps and unknown/unverified items by name; unknown ≠ 0.
- `core/ascendo/dashboard/routes/runs.py::_resolve_item_filter` and managers: the exclusion-derived
  filter must only be passed to phases/managers that accept `--filter` (check which scripts parse it),
  and must carry the identifiers each category matches on (mas: numeric ids; softwareupdate: labels —
  all entries, not just the first; web: slugs). Fix `managers/softwareupdate.py` keeping only the first
  filter entry, and make `scripts/mas/*.sh` match both id and name.
- Extend action-required collection (`report.py` `_action_items` / `collect_action_required`) beyond
  `SourceType.WEB` to skipped major macOS upgrades, pending iPad apps, `XCODE_LICENSE`, orphan casks
  and `needs_restart` items (use a token/code in item messages; keep the existing web behaviour).
- Tests in `tests/contract/` (report honesty, filter routing) and `adapters/macos/tests/` (mas filter by
  name and id, softwareupdate multi-label filter).

### Task 3: Homebrew — never close user apps, Xcode licence gate, outdated rc, sudo env

Implements `brew.md` Spec 8 (P0), Spec 1, Spec 3 part "rc handling" (row 2), plus `BrewManager` env.

- Remove the quit/`pkill` of running cask apps before upgrade (`scripts/brew/apply.sh`,
  `lib/ascendo_brew.sh` `ascendo_brew_kill_cask_apps`). A cask whose app is running is `skipped` with
  `needs_restart`/"app in use — quit it and re-run" (message + action-required token), not upgraded.
- Xcode licence preflight `ascendo_brew_xcode_license_ok` (per Spec 1) in every brew phase before any
  `brew` call: when not accepted → exit 10 path, sidecar `skipped`/`failed` with code `XCODE_LICENSE`
  and remedy `sudo xcodebuild -license accept`; never auto-accept.
- `brew outdated` rc=1 without an `^\s*Error:` stderr line = success (H1/I1); real errors keep their
  stderr reason in the sidecar message instead of `2>/dev/null`.
- `managers/brew.py`: inject `SUDO_ASKPASS` for APPLY like mas/web managers do.
- Tests: running-app cask is skipped and never killed; licence gate; rc=1 handling; askpass env.

### Task 4: Homebrew — cask facts module: greedy, downgrade guard, orphans, sudo casks

Implements `brew.md` Spec 3 (greedy tokens), Spec 4, Spec 5 (+row 7), Spec 6; x-ref
`inventory-orchestration.md` row 14.

- Port `lib/python/brew_casks.py` from macOS_updates as `adapters/macos/lib/brew_casks.py` (guard facts,
  `.app` targets incl. `uninstall.delete` hints for pkg casks, `find_orphan_casks`, `cask_requires_sudo`,
  `app_vs_package_version_relation`), plus the three-way version compare it needs.
- Replace the hard-coded-table downgrade guard in `lib/ascendo_brew.sh` with the recorded-version +
  bundle-relation guard (Brave must no longer be skipped forever). Update
  `tests/test_ascendo_brew_helpers.py` assertions that encode the old buggy behaviour.
- Greedy: `brew outdated --cask` ∪ `--greedy-auto-updates <tokens>` for the configured brew-managed
  auto-updating casks (token list in the report → new config file), deduped by token.
- Orphan casks: not upgraded; item `skipped` with `CASK_ORPHAN` and remedy
  `brew uninstall --cask --force <token>` (report only). Sudo-requiring casks: not attempted TTY-less;
  item `skipped` with `CASK_NEEDS_ADMIN` unless an askpass/password is cached.
- Tests from the report (`test_brew_casks` equivalents with JSON fixtures).

### Task 5: softwareupdate — classification, label-based install, no automatic major upgrade

Implements `system-appstore.md` Spec A (rows 1-5, 7, 8, 22).

- New `adapters/macos/lib/su_classify.py` (port of `system_updates.py`): parse `softwareupdate -l
  [--include-config-data]` records; classify same_major / major / other against `sw_vers
  -productVersion`.
- apply: never `-ia`, never bare `-r` for everything; non-restart labels installed per label with `-R`;
  restart labels in one batched `-i <labels> -R` call; major upgrades never installed unless
  `ASCENDO_ALLOW_MAJOR_UPGRADE=1` — otherwise item `skipped` + action-required token. Per-label status
  from actual results + post-install remeasure (`-l` failure ⇒ unknown, not success). Restart labels
  under the `safe` profile are deferred even when the category is pinned explicitly.
- check/plan/verify use the same parser. Keep `--include-config-data` (Ascendo is ahead there).
- Tests: fixture with a Recommended major upgrade is never installed; per-label success/failure; `-R`
  always present; remeasure unknown path; safe-profile restart deferral.

### Task 6: Mac App Store — honesty, timeouts, remeasure, iPad report-only

Implements `system-appstore.md` Spec B and Spec C (rows 12-17, 19, 20).

- `mas outdated` failure/timeout ⇒ unknown (sidecar `skipped`/`failed` with reason), never "nothing
  pending". Timeouts via Task 1 helper (check 120 s, upgrade 1800 s).
- `MAS_NO_AUTO_INDEX=1` must survive sudo (`sudo env MAS_NO_AUTO_INDEX=1 mas upgrade …`, still sudo).
- After the sudo upgrade: second `mas outdated`; still-outdated ids ⇒ `failed`/`partial`, not `success`.
  Optional one retry in the user session only with `ASCENDO_MAS_USER_RETRY=1` (default off).
- iPad apps: new helper (port of `appstore_ios.sh`) scanning `Wrapper/iTunesMetadata.plist`, iTunes
  Lookup gate (country from mas config/store), lookup failure = soft `unknown`; report-only (items
  `planned` with action-required "update in App Store"), never drive the App Store GUI. Keep default
  `mas outdated` (no `--accurate`).
- Tests with fake `mas`/`curl`.

### Task 7: Native CLIs — update subcommands, safe installer, honest verification

Implements `cli.md` Specs P1, P3, P4 (rows 1-7, 20).

- Installed native CLIs update with their own subcommand (`claude update`, `codex update`,
  `agy update`, `agent update`); vendor install script only for bootstrap. Installer is downloaded to a
  temp file (https-only, size cap, non-empty) then executed with per-vendor env
  (`CODEX_NON_INTERACTIVE=1`), per-vendor args, stdin `</dev/null`, timeout 360 s (env override,
  <60 ⇒ 360) via Task 1 helper.
- Success only when the binary runs `--version` and prints a digit-bearing version different from or
  equal to the target as appropriate; product name alone (`codex-cli`) is not a version.
- Tests: `claude update` used when installed; TUI-ignoring installer killed; no-version ⇒ failed.

### Task 8: npm CLIs — only-installed gate, opencode self-update, sanitising, stdin hygiene

Implements `cli.md` Specs P5, P6, P8, P9 and row 17 (bun).

- Only-installed: missing CLIs/Node/bun are `skipped` ("not installed — skipping") unless
  `ASCENDO_BOOTSTRAP_CLI=1` or explicitly named in `--filter`; verify does not fail not-installed CLIs;
  never `brew install node` unasked.
- opencode: `self-update` method (`opencode upgrade --method npm` with managed prefix env and
  `NPM_CONFIG_ALLOW_SCRIPTS=opencode-ai`), broken-stub detection by running the binary, repair only if
  installed. Update `config/npm_global_clis.txt`.
- bun: `bun upgrade` when installed.
- Sanitise every recorded stderr tail (tokens, auth lines, URL credentials, 500 chars/line).
- Every mutating child in manifest loops gets `</dev/null`; add `ASCENDO_NPM_MANIFEST` test seam.
- Tests per the report.

### Task 9: Web — vendor feeds module, registry schema, three-way compare, Sparkle parity

Implements `web-vendor.md` Spec A, Spec D, Spec E (rows 1-7, 16-18 config).

- Port `vendor_feeds.py` as `adapters/macos/lib/vendor_feeds.py` (normalize, version_key/compare with
  prerelease rank, Sparkle appcast with channel + minimumSystemVersion filters and short→build
  fallback, JSON/YAML/kv selectors, tauri_latest, cursor_update).
- Registry: optional `[app.vendor_feed]` table in `web_apps.toml` + Pydantic schema in
  `ascendo_macos/web_registry.py` + `lib/web_registry.py` CLI; add the feed rows from the report.
- `_web_version_cmp` (newer/equal/older/unknown) used by web check/plan/apply; `older` ⇒ `feed_stale`
  (skipped, unverified), `unknown` ⇒ skipped, never `up_to_date`.
- Existing `sparkle.sh` uses the new parser (RDM must parse as its real newest version).
- Tests: fixtures for each selector, stale feed, prerelease ordering, channel filter.

### Task 10: Web — hardened installer with atomic swap, identity check, rollback

Implements `web-vendor.md` Spec B and Spec C (rows 10-15, 24).

- `vendor_direct_install` + `copy_verified_app` in `lib/ascendo_web.sh` (or new
  `lib/ascendo_vendor_direct.sh`): https + exact host allowlist, bounded fetch (`.part`, size cap,
  atomic mv), checksum when available (sha256/sha512 from the same feed entry), DMG
  (`hdiutil verify`, attach `-nobrowse -readonly`, always detach), zip, tar.gz; `spctl --assess` on source
  and staging; bundle-ID and Team-ID must equal the installed app's; `ditto` to
  `/Applications/.ascendo_staging.*`, then backup-swap with automatic rollback; app must not be running.
- `_web_install_dmg` routes through it (no more `rm -rf` + `cp -R`). Ledger sha512 from the same
  `files[]` entry.
- Tests with fake `hdiutil`/`spctl`/`codesign`/`ditto` and temp `/Applications` root (env override).

### Task 11: Web — vendor-truth flow, direct-first apps, Docker Desktop, RDM

Implements `web-vendor.md` Spec F and Spec G (rows 9, 15-20) and the app-by-app handler diff.

- Squirrel apps with a vendor feed (Claude, ChatGPT, Codex, Cursor, Warp, Antigravity, Antigravity IDE,
  OpenCode, Proton Mail): check/plan use the feed (equal ⇒ up_to_date without launch; newer ⇒ planned;
  older ⇒ feed_stale; unknown ⇒ skipped). Apply: newer + idle + direct_first (Cursor, OpenCode) ⇒
  vendor-direct install; newer + running ⇒ `needs_restart` skipped; otherwise existing hidden-launch
  path.
- Docker Desktop: vendor appcast is truth (fix CLI-plugin version bug), idle Docker updated from the
  verified DMG, running Docker never updated (`needs_restart`).
- RDM: productinfo kv feed with sha256 DMG.
- Update `tests/test_web_apps_toml_shipped.py` (`_SISTER_WEB_HANDLERS`, docker/rdm assertions).

### Task 12: Web — verify that works, toolkit-launched quit, status codes, Firefox Dev

Implements `web-updaters.md` Spec D, Spec E, Spec G, Spec H (rows 8-16, 26, 28).

- `scripts/web/verify.sh`: find the apply sidecar via the canonical run dir (like Windows
  `Find-AscendoSiblingSidecar`: per-phase dir, then `$ASCENDO_RUNS_DIR`, then `~/.ascendo/runs`), resolve
  the app path from the registry/bundle id (not `item.name`), no auto-fail for unchanged Tier-B items
  without a stale-days bound (default 45, env override).
- Record apps Ascendo launched itself (only if not running before `open`) in a run-scoped file and quit
  them gracefully after the settle window; never quit apps that were already running.
- Status vocabulary (`current_verified`, `current_vendor`, `updated`, `behind`, `update_available`,
  `needs_restart`, `rollout_hold`, `feed_stale`, `unverified`) mapped onto sidecar statuses + message
  codes; `report.py` must not promote every `triggered` item to action-required — only unverified /
  behind / needs_restart, listed by name.
- Firefox Dev: `version_regex` fix so new-major betas are not skipped forever.

### Task 13: Web — Chromium updaters (Chrome, Google Drive, Gemini, Comet)

Implements `web-updaters.md` Spec C (rows 1-7).

- New `chromium_updater` handler: VersionHistory pre-check for Chrome (installed ≥ public ⇒ current, no
  wake), proof from `updater_history.jsonl` / `updater.log` (+`.old` rotation) before waking,
  GoogleUpdater `--wake-all` (user → system → legacy Keystone agent), CometUpdater for Comet, shared
  once-per-run wake/wait window, short + build version compare (Drive), `rollout_hold` when updater says
  noupdate but public is newer. No browser launch. Registry rows per the report (fix Chrome ticket-id
  case).
- Tests with fixture logs/jsonl and fake updater binaries.

### Task 14: Web — Microsoft AutoUpdate honesty

Implements `web-updaters.md` Spec B and Spec F (rows 18, 21-25, 27).

- One serialized `msupdate --list` per run with timeout (Task 1), validity check (interruption strings,
  empty/unknown ⇒ `__MAU_UNKNOWN__` ⇒ unknown, never up_to_date).
- TEAMS21 DeferralVersions pin released only when strictly older than installed build, edited
  per-key with backup/verification (not whole-domain rewrite); DeferralDays quarantine awareness;
  upstream regression detection (offered ≤ installed) with channel diagnostic; Teams oracle.
- Scoped silent install only with `ASCENDO_MAU_SCOPED_INSTALL=1`; default keeps RC 95 action-required.
- Tests with fake `msupdate`/`defaults`.

### Task 15: Inventory scan cleanup, iPad identity, drift-free reconcile

Implements `inventory-orchestration.md` Spec 4 and Spec 8 (rows 11-13, 24, 25).

- Inventory/web discovery: only real top-level apps of `/Applications` + `~/Applications`
  (overridable dirs), exclusions config (incl. Ascendo itself, helper/updater apps, test fixtures),
  iPad apps identified via `Wrapper/iTunesMetadata.plist` and filed under mas/appstore, not web.
- Wire `inventory_db.reconcile()` (drift-free, never wipe a group when new count is 0 but old > 0).
- Tests with fixture app trees.

### Task 16: Orchestration safety — restart-safe blocking, run lock, unattended sudo

Implements `inventory-orchestration.md` Spec 5, Spec 6, Spec 7 (rows 5-9, 18, 23).

- A hard failure in earlier categories defers the restart-capable softwareupdate apply (skipped with
  reason); report/inventory written before the step that may reboot.
- Cross-process run lock (PID + start time, stale release) shared by CLI and dashboard for mutating
  phases.
- TTY-less runs without askpass/password never attempt interactive `sudo -v`; root-only tracks skip
  softly with a reason. One sudo keep-alive per run, killed on all exit paths.
- Tests (contract + adapter). Keep Windows/Ubuntu paths unaffected.

### Task 17: Architecture/Rosetta awareness, scheduler honesty, CLT health, P2 items

Implements `system-appstore.md` Spec E and Spec F, `brew.md` row 11, `inventory-orchestration.md`
Spec 9 and rows 19/22 where cheap, `cli.md` Spec P7.

- Rosetta: flag x86_64-only apps (lipo/`file`) as action-required ("requires Rosetta"); install Rosetta
  only with `ASCENDO_INSTALL_ROSETTA=1`.
- launchd scheduler: bootstrap failure is an error, strip quarantine from plist, `launchctl enable`.
- `clt` component in `MacOSAdapter.health_check`.
- CLI version retention in `scripts/npm/cleanup.sh` (keep current + 1, symlink canonicalisation,
  `ASCENDO_KEEP_CLI_VERSIONS=1` opt-out).
- Background-change attribution and chronic warnings: implement if small, else document as deferred.

### Task 18: Docs, version 1.1.0, full validation

- Bump version to **1.1.0** via `scripts/bump-version.sh` (lockstep: `core/ascendo/__version__.py`,
  Tauri `tauri.conf.json`, `Cargo.toml`, `package.json`, `releases/latest.json` as the script does).
- `docs/releases/v1.1.0.md` release notes (what was ported from macOS_updates v1.4.2→v1.5.2, new opt-in
  env vars, behaviour changes). Update `MACOS_QUICKSTART.md`, `MACOS_TESTING.md`, `adapters/macos/README.md`,
  `PLAN.md` header entry and `HANDOFF.md` Sesja entry (if an editor hook blocks root CHANGELOG/HANDOFF,
  record that and keep notes in `docs/releases/`). i18n EN+PL parity if any UI string changes.
- Run full gates: adapter tests, contract tests, `bash bin/validate-macos.sh --quick` style read-only
  stages (no real apply), `scripts/check-i18n-parity.py`.
