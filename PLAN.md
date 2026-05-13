# Ascendo — Forward Plan

> Last updated: 2026-05-13 (sesja 63) — **Unknown-version apply-mark
> for `SoftSea.IMGtoISO` + similar packages.** Operator report:
> "img to iso always reports unknown version, fix it, even after
> update." Root cause: `winget list --id SoftSea.IMGtoISO` returns
> `Version=Unknown` BOTH before and after a successful upgrade
> because the Inno Setup uninstaller skips DisplayVersion in its
> ARP registry key. Without a state marker every check re-classifies
> the package as `planned` (Available='1.0' differs from current=
> 'Unknown') and inventory.db stays stuck at `cur=Unknown,
> status=outdated`. Sesja 63 ships two new exported helpers in
> `AscendoWinget.psm1` (`Get-/Set-AscendoApplyMark`), persists the
> apply target to `$USERPROFILE/.ascendo/state/winget_apply_marks.json`
> on successful Unknown-version applies, and consults the mark in
> check phase to flip the row to `up_to_date` when
> `mark.target == Available`. End-to-end verified live: IMG to ISO
> goes from `status=planned cur='Unknown'` to
> `status=up_to_date cur='1.0' tgt='1.0'`. Same mechanism handles
> other Inno Setup / MSI / Steam-wrapper packages that skip
> DisplayVersion. Test count 424 → **432** Windows + 324 contract
> (+8 new). See HANDOFF.md Sesja 63.
>
> Previous milestone (sesja 62) — **Post-apply ResolvedVersion
> + verify sibling-sidecar lookup.** Audit of run `91769201`
> (operator's full update at 12:36 UTC, 3 real upgrades + 598
> up_to_date + 0 failures) surfaced two latent bugs that broke the
> post-apply reconciliation chain: (1) `apply.ps1` Tier-A success
> branch never set `ResolvedVersion`, so the orchestrator's post-run
> inventory flush couldn't update `inventory.db` from the pre-install
> value (OpenCode upgraded to 1.14.48 in reality but the DB kept
> showing 1.14.33 as outdated); (2) every verify phase reported
> "No apply sidecar found; verify is a no-op" because each phase
> script runs in its own `tempfile.TemporaryDirectory` — apply's
> sidecar lives in the canonical `~/.ascendo/runs/<run-id>/`, not the
> verify-phase tempdir. Sesja 62 ships a new `Find-AscendoSiblingSidecar`
> helper that falls back to the canonical run dir (with
> `ASCENDO_RUNS_DIR` env-var override), wires it into all 5 verify
> scripts, and adds `ResolvedVersion` plumbing to web Tier-A apply
> + windows_update apply. End-to-end audit on operator's machine:
> CLI + Web dashboard + Tier-A install all functional. Test count
> 414 → **424** Windows + 324 contract (+10 new). See HANDOFF.md
> Sesja 62.
>
> Previous milestone (sesja 61) — **Web Tier-A silent install +
> JSON walker dotted-numeric + verify candidate preservation.** Sesja 61
> closes the operator's "vscode has not been updated" report:
> (1) JSON walker accepts `Releases.0.Version` alongside
> `Releases[0].Version` for macOS parity; (2) `ConvertFrom-Json
> -AsHashtable` gated on PS6+ so case-colliding keys (Proton Drive's
> `Sha512CheckSum` vs `Sha512Checksum`) don't trip the parser;
> (3) `verify.ps1` preserves the candidate version when apply was
> Tier-B triggered but the operator hasn't actually run the installer
> yet (no more `up_to_date 1.119.1` masking outdated state);
> (4) six curated entries promoted to Tier-A silent install with full
> `silent_args` / `installer_kind` / `kill_processes` /
> `expected_publisher` contract — vscode-user, keepassxc, notepadpp,
> autohotkey, github-cli, opencode; (5) opencode `asset_pattern`
> corrected to the real `opencode-desktop-win-x64.exe`. Plus
> `kill_processes` Pydantic pattern relaxed for `notepad++`. Test
> count 379 → **414** Windows + 324 contract (+19 new tests).
> See HANDOFF.md Sesja 61.
>
> Previous milestone (sesja 60) — **Curated registry expanded 10 → 20
> + `Get-WebInstalledVersion` DisplayName fallback.** Operator's first
> live test of Sesja 59 showed `web: 108 items, 0 outdated` because
> the shipped registry only covered Brave/Obsidian/Notion/etc. — none
> installed on DP5520WMK. Sesja 60 added 10 entries for common dev
> tools (keepassxc, notepadpp, autohotkey, rclone, github-cli,
> opencode, tuta-mail, vscode-user, proton-mail, proton-drive) and
> made `Get-WebInstalledVersion` fall back to a DisplayName scan when
> the exact registry subkey doesn't match (so curated entries can use
> friendly display names instead of guessing GUIDs). Plus relaxed
> `windows_uninstall_key` Pydantic pattern to accept real Windows
> DisplayName punctuation (`Notepad++ (64-bit x64)`).
>
> Previous milestone (sesja 59) — **Windows apply-hang fixed +
> Tier-A web apply + registry auto-discovery shipped.** Three Sesja 59
> commits land on `claude/friendly-banzai-aee757`: (1) windows_update
> apply.ps1 pre-checks pending via `Get-PendingWindowsUpdates` before
> `Install-WindowsUpdateBatch`, short-circuiting in seconds when 0
> pending — fixes the operator-reported "updates not applying, app
> stops at certain point" bug observed on run e5f0e0f1; plus heartbeat
> now mirrors `>>> still running Ns` to `$env:ASCENDO_STREAM_LOG` so
> the SPA Run Center shows liveness during long installs. (2) Tier-A
> web apply on github_release + release_feed handlers — opt-in via
> `tier_a_apply = true` on the app entry; downloads + Authenticode
> verifies + kills running processes + runs silent install + reads
> version back from registry. (3) Registry-based auto-discovery —
> `AscendoWebDiscovery.psm1` walks ARP roots, classifies ownership
> via winget/msstore/curated/arp layers, filters out Microsoft system
> components + Inno bundles, surfaces every web-installed app in the
> SPA's Categories tab as `web:auto:<slug>` items. Test count 280 →
> **344** (+64 across 5 new test files). See HANDOFF.md Sesja 59.
>
> Previous milestone (sesja 58 + four follow-up commits) —
> **Windows brought to feature parity with macOS + Ubuntu, then
> hardened against the first real-hardware run.** Five-wave Sesja 58
> push lands: 5 `bin/` web-service wrappers + bidirectional
> `from=/to=` fix across winget/msstore/arp/windows_update/inventory
> check.ps1 + `health_check()` expanded 5 → **10** components
> (npm, pip, dcu, web_registry, inventory_db added); `NpmManager` +
> `PipManager` Python classes with full 5-phase PS contracts +
> 15-npm + 11-pip config manifests; watchdog heartbeat helpers
> (`Start-AscendoHeartbeat` / `Stop-AscendoHeartbeat`) wired into 4
> apply.ps1 via try/finally; `WebManager` scaffold with Pydantic
> `WebRegistryV2` schema + 3 handlers (github_release, release_feed,
> builtin) + 10 curated apps (4 Tier-A: brave/obsidian/notion/obs-studio;
> 6 Tier-B builtin); new `_BaseWindowsManager._salvage_sidecar` mixin
> (bufdir-based JSONL incremental writes + `ASCENDO-SALVAGED` recovery
> diagnostic); **`DellDriverManager` wires the long-dormant M3.15
> dell-driver-update plugin into the orchestrator** (manager count
> 7 → 8 — winget, msstore, npm, pip, web, plugin, registry_arp,
> windows_update).
>
> Operator-driven hardening (post-Sesja-58 follow-up commits):
> - `7edb512` — 5 PS5.1 / Python-discovery issues that broke npm + web
>   check on first run: `.Count` StrictMode bug, em-dashes in string
>   literals, `Export-ModuleMember` in transient modules, Python order
>   picking 3.13 without pydantic, missing `sys.path` bootstrap.
> - `0d68e35` — `DellDriverManager` wired in (see above).
> - `7415e25` — `npm/pip apply.ps1` passed `[ordered]@{...}` entries
>   to `Add-SidecarItem -Messages`; the validator requires `[hashtable]`
>   specifically. Visible only when a real per-package install failed.
> - `5c3a549` — Dell apply on non-elevated PowerShell returned exit 6
>   ("Application requires elevated privileges") which the plugin
>   mapped to `failed`, triggering orchestrator abort. Plugin scripts
>   now map exit codes 6/7/3 → `skipped`; Python preflight in
>   `DellDriverManager.run_phase` short-circuits to `skipped` when
>   `host.is_elevated == False` (cleanup phase exempted, no-op there).
>
> Tests: 99 baseline → **280 passing** (+181 across Sesja 58 + 4
> follow-ups). 1 skipped (intentional dead-codepath assertion). Zero
> regressions. WindowsAdapter ships 8 IPackageManager implementations.
> See HANDOFF.md Sesja 58 + the post-Sesja-58 first-run-fixes section.
>
> Previous milestone (sesja 57) — **Version polarity bug closed
> across all 5 phases + new logos + `ascendo build-inventory` CLI.**
> Operator's second audit revealed the `from=`/`to=` polarity issue
> from Sesja 56 (drivers) was structural: every "present" item in
> check/plan/apply/verify across snap/apt/brew/npm/pip/flatpak/drivers
> emitted only `to=$ver`, leaving SPA inventory rows with
> `installed=null`. Fixed in 13 call-sites across 9 scripts. Snap apply
> confirmed working via dashboard async API after restart. Web check
> Pass 2 gated behind `ASCENDO_WEB_INCLUDE_UNINSTALLED=1` so default
> inventory excludes apps not present on this system. Auth modal
> Enter-key handler added (belt-and-suspenders). Brand assets
> (favicon + logos) synced to design system. See HANDOFF.md Sesja 57.
>
> Previous milestone (sesja 56) — **Linux production-readiness
> pass shipped.** `ascendo web {start|stop|restart|status}` validated
> live on mk-uP5520; edition-aware `.deb` installer (basic + dev)
> ships from `packaging/build-deb.sh --edition=…`; defensive sidecar
> salvage path in `_BaseManager._salvage_sidecar` covers the long
> tail of "phase script died without firing its EXIT trap"; drivers
> inventory row no longer falsely outdated; legacy
> `ubuntu-aktualizacje-dashboard.service` retired on this host; stale
> `claude/*` remote branches pruned (all merged to main). See
> HANDOFF.md Sesja 56.
>
> Previous milestone (sesja 54-55) — **Ubuntu adapter brought to
> macOS feature parity + production-hardened end-to-end** on
> mk-uP5520. UbuntuAdapter now declares all 5 IAdapter capabilities
> (PACKAGE_MANAGEMENT | INVENTORY | SNAPSHOTS | SCHEDULING | ELEVATION),
> 8 IPackageManager implementations (apt/snap/brew/npm/pip/flatpak/
> drivers/**web**), 13-component health rollup. New `bin/validate-ubuntu.sh`
> 23/23 PASS. **Eight live-fire bug fixes** in Sesja 55 closed: bash
> heredoc-in-`$(...)`-with-`||` parse error silently skipping npm+pip
> from inventory (40 items lost); python3-`-`-stdin collision with
> data pipe; `require_sudo` clobbering `json_register_exit_trap` so
> snap apply's sidecar never wrote and SPA showed phantom failures;
> SPA overlay never matching synthetic check-sidecar IDs to inventory
> clean names; SIGINT propagating through process group killing
> in-flight apply; missing watchdog heartbeat between silent phase
> transitions; `brew --cask --greedy` re-downloading every cask each
> run (silent multi-minute "hang"); pip/plan.sh emitting `kind=check`
> clobbering real check sidecar; missing non-interactive env defaults.
> Live: 2579 inventory items across 5 categories with installed +
> candidate populated. See HANDOFF.md Sesja 54 + 55.
>
> Previous milestone (sesja 45) — **cross-platform parity +
> one-line install/update for all 3 OSes shipped as v0.5.2**. Brings
> Windows + Ubuntu adapters up to functional parity with macOS and
> ships true `curl|bash` (POSIX) + `iwr|iex` (Windows) one-liners for
> install + update. **Ubuntu**: full Tier-1 Python adapter scaffold
> (UbuntuAdapter + 7 managers + 10-component health_check) bridges to
> mature legacy bash scripts via env-var IPC; full IInventory
> enumeration via `adapters/ubuntu/scripts/inventory/list.sh` covering
> apt/snap/flatpak/brew/npm/pip with 10s timeout per tool, graceful
> skip on missing CLIs. **Windows**: stderr capture in 4 apply.ps1
> scripts (winget/msstore/arp/windows_update) so operators see actual
> error reasons instead of "exited N"; pre-dispatch up_to_date guard
> in winget+msstore mirrors macOS web/apply.sh Sesja 40 pattern. **One-
> line install + update**: install.sh rewrite (--update + --reinstall +
> --verbose + --non-interactive + env-var overrides + network preflight
> + disk-space check + locked-package-manager detection + final doctor
> self-test) + update.sh (POSIX) + install.ps1 + update.ps1 (Windows;
> auto-installs Python via winget). **Cross-cutting bug fix**:
> `_flush_run_to_inventory_db` now clears categories before
> bulk_upsert (4th missing path from Sesja 40 stale-rows fix).
> 841/848 tests green (9 pre-existing service_endpoints failures
> unchanged). See HANDOFF.md Sesja 45.
>
> Previous milestone (sesja 44): **5 operator bug fixes + 1
> portability doc shipped as v0.5.1**. Fixes: (1) Brave x86_64 Mac
> bundle replaced with arm64 + new `download_asset_pattern` field on
> release_feed selects universal DMG from GitHub release assets so
> future Tier-A applies auto-replace wrong-arch bundles; (2) `.npmrc`
> `prefix=` line stops coming back — replaced our `npm config set
> prefix` with `NPM_CONFIG_PREFIX` env var + scrub_npmrc helper;
> (3) Categories collapse-back fixed via missing CSS rule
> `.cat-detail.hidden { display: none }` + chevron explicit-click
> hardening; (4) Touch ID sudo cache now honoured — `/sudo/status`
> probes `sudo -n -v` (1s cap) when no SPA password registered;
> (5) Discovery brew classification fixed — `_flatten()` handles
> str/list, app filename matching, zap.trash plist mining, opt-in
> codesign deep ownership. Plus `docs/PORTABILITY.md` (181 lines)
> answers cross-machine portability question. 391/391 macOS tests
> + 249 contract tests (+13 elevation, +26 brave/npm, +7 discovery).
> See HANDOFF.md Sesja 44.
>
> Previous milestone (sesja 43): **post-update reports, per-app
> history, github_dmg apply-guard, and SPA staleness shipped as v0.5.0**. Operator audit asked for: inventory health
> confirmation (✅ 223/223 apps Tier-A), main functionality
> verification (✅ all 5 phases × 6 categories green = 30/30 sidecars),
> human-readable post-apply report (✅ shipped — `REPORT.md` auto-
> generated; `ascendo runs report <id>` CLI; `GET /runs/{id}/report`
> endpoint), update history per app (✅ shipped — new
> `update_history` SQLite table populated automatically from apply
> sidecars; `GET /apps/{cat}/{name}/history` endpoint; SPA "History"
> link per app row). Plus 1 inline bug fix: github_dmg apps were
> failing on apply with misleading "exit 26" when GitHub API rate-
> limited; web/apply.sh now skips with `probe_unavailable` instead of
> falling through to a doomed apply. Plus Last-run staleness indicator
> (colored relative-time on Overview) + post-apply DB refresh hook so
> SPA shows fresh versions without manual reload. Bulk-preview UI
> deferred. Real Mac.r12.home: 377/377 macOS tests + 247/256 contract
> tests (+27 new). See HANDOFF.md Sesja 43.
>
> Previous milestone (sesja 42): **M5.7.5 Omaha protocol +
> last-mile static research shipped as v0.4.5**. **100% real-candidate
> coverage achieved** on Mac.r12.home: 223 of 224 apps; the 1 remaining
> is `ascendo` itself (intentionally `enabled = false` because the
> KasprowiczM/ascendo repo isn't public and has no releases yet —
> flips automatically when first GitHub Release ships). New `omaha`
> handler implements Google's Omaha update protocol (POST + XML body
> for protocol=3.0; JSON body for protocol=4.0 used by Comet).
> 7 Tier-A promotions: gdrive (keystone→omaha; **outdated detected
> 124.0→125.0.0.0!**), gemini (keystone→omaha with channel tag
> m1-prod from ksadmin), comet (squirrel→omaha 4.0 against
> www.perplexity.ai/rest/browser/update2), inkscape (builtin→
> release_feed text scraping inkscape.org/release/ HTML title),
> spotify (builtin→release_feed against Homebrew cask API at
> formulae.brew.sh/api/cask/spotify.json — vendor's own endpoint
> requires Bearer auth; **outdated detected 1.2.87.415→1.2.88.483!**),
> antigravity (squirrel→release_feed text — same Cloud Run service's
> ROOT path returns plain `Stable Version: X.Y.Z` while the JSON path
> has stale productVersion), lm-studio (squirrel→release_feed via
> Homebrew cask API; brew comma-format `0.4.12,1` regex-normalized to
> CFBundle `0.4.12+1`). 377/377 tests + 8 new (Omaha XML/JSON probe,
> ksadmin tag, last-mile static). 9 outdated apps detected (was 6).
> See HANDOFF.md Sesja 42.
>
> Previous milestone (sesja 41): **M5.7.4 release_feed extensions
> shipped as v0.4.4**. 216 of 224 apps (96%) now report real candidate
> versions on Mac.r12.home (was 211/224 = 94%). 5 web Tier-A
> promotions: warp + megasync via new `version_regex` + `version_replace`
> fields (XOR-validated, `re.sub` once, falls back to raw on no-match);
> chrome via `versionhistory.googleapis.com` (Google's public Chrome
> version API — works without auth!); brave via GitHub
> releases `name` field which carries BOTH Chromium milestone AND Brave
> internal version on one line that regex composes into 148.1.90.121
> shape; rdm via the new `format = "text"` mode against
> `devolutions.net/productinfo.htm` key=value text feed. Plus 2 MiB body
> cap (was 256 KiB; Warp's feed is 860 KiB and was being truncated mid-
> string). 369/369 macOS tests + 5 new tests for version_regex schema +
> bash handler. 5 apps explicitly ruled out as M5.7.5 backlog: gdrive +
> gemini (Omaha protocol — re-check annually); comet + lm-studio (need
> mitmproxy runtime capture); antigravity (vendor API stale).
> See HANDOFF.md Sesja 41.
>
> Previous milestone (sesja 40): **M5.7.3 web coverage push
> shipped as v0.4.3**. 211 of 224 apps (94%) now report real candidate
> versions on Mac.r12.home (was 196/228 = 86%). 3 fix areas: (1) URL
> encode in release_feed_apply (Notion Calendar / Cursor with spaces in
> filenames); (2) apply.sh pre-dispatch check probes candidate before
> invoking Tier-A handler so up_to_date apps are skipped; (3) pip
> verify.sh mirrors check.sh's brew-pip self-skip. Plus 9 new web
> Tier-A apps: ms-word/excel/powerpoint/outlook/onenote/teams via
> per-app msupdate targeting; chatgpt squirrel→sparkle (oaistatic.com);
> opencode squirrel→github_dmg (anomalyco/opencode, NOT sst/opencode);
> proton-mail new release_feed entry (proton.me version.json). 4
> ineligible apps (Google Workspace shortcuts, Defender shim) filtered
> from web discovery. Dead `perplexity` registry entry removed (it's
> a MAS app). InventoryDB stale-row cleanup wired into 3 live-scan
> paths. 365 macOS tests + 222 contract tests. 7 apps explicitly ruled
> out as M5.7.4 backlog (warp/megasync need release_feed `version_regex`;
> brave/chrome/gdrive/gemini need Omaha protocol; antigravity has stale
> upstream API; lm-studio behind R2 auth). See HANDOFF.md Sesja 40.
>
> Previous milestone (sesja 39): **M5.7.2 app.asar binary mining
> shipped as v0.4.2**. Subagent reverse-engineered Electron app.asar
> archives + native binaries for embedded update endpoints. 4 new vendor
> probes: Claude (api.anthropic.com release_feed with zero device_id UUID),
> Codex (oaistatic.com Sparkle appcast — Codex bundles BOTH frameworks
> but Sparkle is active), Notion Calendar (notion-static.com YAML;
> bundle_id is `com.cron.electron`, not `com.notion.*`), Cursor
> (todesktop.com YAML, validated despite not being installed locally).
> Plus 3 polish 2 fixes: Docker switched from `docker` handler (probed
> CLI plugin v0.3.0, NOT Docker.app v4.71!) to `sparkle` against real
> appcast at desktop.docker.com; RDM forced builtin (vendor's appcast
> frozen at 2023.1.12.0); Obsidian asset_pattern fixed for universal
> binaries. Real Mac.r12.home: web check 14 → **17 apps** with real
> candidate. Outdated detected: Docker 4.71→4.72, Firefox-Dev
> 151.0→151.0b7, ProtonVPN 6.5.0→6.5.1, Zoom .→.77593. 364/364 tests.
> See HANDOFF.md Sesja 39.
>
> Previous milestone (sesja 38): **M5.7.1 web vendor probes +
> bug fixes shipped as v0.4.1**. Operator-driven coverage push after
> testing v0.4.0 surfaced 3 real bugs + missing vendor URLs. Wins:
> discovery now extracts `SUFeedURL`+`KSProductID` into emitted JSON
> (was: silently dropped, ~6 Sparkle apps broken); MAS apps filtered
> via `_MASReceipt` (was: 8 MAS apps polluted web inventory); 8 new
> vendor probes (VSCode/Zoom/Firefox-Dev/Notion/Ledger/KeePassXC/
> Obsidian + release_feed YAML support for Electron-builder
> latest-mac.yml). Sparkle picks highest version not first.
> Real Mac.r12.home: web check 4 → **15 apps** with real candidate.
> 364/364 tests + 41/41 validate-macos.
>
> Previous milestone (sesja 37): **M5.7 web auto-discovery + tiered
> probes shipped as v0.4.0**. Discovery walks `/Applications` Info.plists,
> auto-classifies each bundle, excludes brew/mas/softwareupdate ownership.
> Override registry v2 keyed by `bundle_id`. New `release_feed` Tier-A
> handler is a generic JSON-feed probe so vendor probes become TOML config.
> New `ItemStatus.TRIGGERED` for Tier-B (keystone/squirrel/builtin) async
> agents. Real Mac.r12.home: web check 13 items → **51 items** (every
> web-orphan installed app now visible). 364/364 macOS adapter tests +
> 41/41 validate-macos. See HANDOFF.md Sesja 37.
>
> Previous milestone (sesja 36): sudo-prompt collapse: 3 prompts
> per "Full update" (password modal + Touch ID + Touch ID) → **1 tap**
> when PAM Touch ID is enabled. Four mechanical fixes:
>   1. **WebManager now injects SUDO_ASKPASS** for Phase.APPLY (was
>      missing — only mas + softwareupdate did, so web was always
>      falling through to a Touch ID prompt even after the dashboard
>      cached the password).
>   2. New `_ascendo_sudo` helper (`lib/ascendo_json.sh`) picks
>      `sudo -A` (askpass) or plain `sudo` (TTY-PAM) by env. Apply
>      scripts use it instead of hard-coding `-A`, so the
>      Touch-ID-only flow works without an askpass helper.
>   3. `_ascendo_sudo_warm` short-circuits when SUDO_ASKPASS is
>      already wired (avoids a duplicate Touch ID dialog stacked on
>      top of the SPA modal cache). osascript fallback is now opt-in
>      via ASCENDO_SUDO_ALLOW_GUI=1 (it bypasses PAM and never uses
>      Touch ID, so it's the wrong tool when biometrics are wanted).
>   4. SPA `sudoMgr.ensure()` polls `/elevation/touchid/status` on
>      macOS — when `enabled=true`, the password modal is skipped
>      entirely and the first apply phase's TTY-PAM Touch ID handles
>      auth. Subsequent phases use the cached sudo timestamp.
> 358/358 macOS adapter tests + 216/225 contract tests green
> (9 pre-existing test_service_endpoints failures unchanged).
>
> Previous milestone: Sesja 35 — operator-reported SPA polish:
> Quick-check no-prompt (profile=quick treated as read-only),
> Overview re-scan suppression on tab switch (post-run repaint
> marks `_loaded` so cache is honored), wizard footer pinned via
> sticky position so Back/Next can't escape the modal-card on
> transformed Tauri ancestors.
>
> Previous milestone: Sesja 34 — multi-front polish session:
> partial-status heuristic in `_json_emit.py` so a single failed item
> no longer aborts the whole run; npm + pip stderr capture (last 12
> lines) into sidecar messages so the operator sees the actual install
> failure reason (PEP 668, EACCES, registry 404, no RECORD file, etc.);
> Touch-ID-first sudo warming via `osascript … with administrator
> privileges` before the askpass fallback; brew-pip self-upgrade skip
> (no RECORD file); Tauri `--with-dmg` opt-in + create-dmg fallback;
> icon regeneration at 8-bit RGBA; tolerant launch script arg parser;
> Ascendo path capitalization fix across docs; **pip version-mismatch
> fix** (LATEST=INSTALLED in brew-self-skip case so the dashboard's
> Python `_classify` overlay no longer flips brew-managed rows back
> to "outdated"). 495+ tests passing.
>
> Previous milestone: Sesja 33 — macOS adapter now has full 5-manager
> parity with Ubuntu: brew · mas · npm · **pip** · softwareupdate.
> 11-component health check (added `pip`).
>
> Previous milestone: Sesja 32 (inventory SQLite DB cache,
> adapter-conditional onboarding wizard (macOS/Linux/Windows variants),
> Apps↔Categories data parity, NVIDIA hard-hidden on macOS via
> adapter-hide-macos, sidebar contextual help block (replacing
> top-of-view summaries), Overview cards compacted, sidebar widened
> for PL tagline. 471 tests green (+13 inventory_db); 693/693 EN/PL
> i18n parity.
>
> Previous milestone: Sesja 31 (icon + AI providers Gemini/LMStudio +
> cache flush), Overview reordered (1. inventory, 2. quick, 3. safe,
> 4. dry run, 5. full), About release notes expand/collapse, Help view
> fully populated for macOS / Linux (41 new keys, 624/624 EN+PL parity),
> AI providers Gemini + LM Studio promoted to fully-implemented,
> Touch ID detection endpoint with one-liner enablement instructions,
> Apps view auto-repaint after run, run stream labels translatable.
> 458 tests passing (+3 new).
>
> Previous milestone: Sesja 30 (UX overhaul:
> live verbose log streaming in Run Center, one-liner curl|bash
> installer + CLI banner, inventory cache + Refresh buttons,
> numbered Overview actions, dark-mode app icon, adapter-gated UI
> (NVIDIA + Windows-service hidden on macOS), Logs picker
> repositioned, first-run wizard re-armed, Apps view filter +
> grouping + search + candidate column, Suggestions 3-step AI
> wizard with 4 working providers + preloaded library, /sudo/*
> shim delegation that fixes the "no sudo prompt on macOS apply"
> bug, EN/PL parity confirmed at 581/581 keys. 455 tests passing
> (+17 new this session).
>
> Previous milestone: Sesja 29 (macOS apply-phase hardening).
>
> Previous milestone: v0.2.0 (sesja 28, M5 macOS adapter complete).
>
> This file is the **single source of truth for what comes next**. HANDOFF.md
> is the historical session log; PLAN.md is the forward roadmap. Update this
> file whenever priorities shift; prune completed items into HANDOFF.md.

---

## Immediate next steps (post-Sesja 34)

The macOS adapter is production-ready for everyday solo-machine use.
What's left before declaring v0.3.0:

### Hygiene follow-ups discovered in Sesja 34

- **`InventoryDB.bulk_upsert` never deletes stale rows.** When the pip
  manifest shrinks (or any manager's tracked-set changes), the SQLite
  inventory at `~/.ascendo/inventory.db` keeps the old entries —
  causing Apps to report `pip 12` while Run Center reports 11. Fix:
  in `_resolve_buckets` (or wherever live-scan repopulates), call
  `db.clear_category(cat)` before `bulk_upsert` for each category
  that's currently being scanned. ~10 LOC. Workaround for now:
  `rm ~/.ascendo/inventory.db` and trigger any check.
- **Lock in the brew-self-skip LATEST behaviour with a regression test**
  in `tests/test_pip_check_script.py` — fake brew pip flavour, assert
  emitted item has `installed == candidate` and `status == up_to_date`.

### Stage 5 polish (low-risk, fast)

- Status pill colors: contrast pass on light theme for History + Logs.
- "Last Run" staleness indicator on the Overview card.
- Hide NVIDIA driver buttons on macOS via `html[data-adapter=macos]`
  CSS gate (currently visible but inert on macOS).
- Invalidate inventory cache after apply finishes so SPA shows new
  versions without manual refresh.

### M5.x deferred follow-ups (medium scope)

- **Pre-apply snapshot integration on macOS.** APFS auto-management
  blocks `tmutil snapshot`; only fallback is to recommend the user
  configure Time Machine and document `tmutil localsnapshot` as a
  pre-apply manual step. Add a footer banner when no recent local
  snapshot is found before bulk apply.
- **Bulk-preview UI.** Aggregate plan-phase output across all
  categories into one diff view: "12 packages will change". Right
  now plan emits per-category sidecars and the SPA shows them
  per-row.
- **Parallel apply.** Run brew + mas + npm in parallel (softwareupdate
  must stay sequential because of reboot semantics). Requires lock
  coordination at the manager layer.

---

## What landed in 2026-05-12 (Sesja 58)

Windows-parity push across five waves on the `claude/nifty-jones-1773b5`
worktree:

- **Wave A — quick wins.** 5 `bin/` web-service wrappers
  (`ascendo-web-{start,stop,restart,status}.ps1` + `build-inventory.ps1`,
  ~36 LOC each) + `from=/to=` bidirectional inventory fix in 5 PS files
  (winget/msstore/arp/windows_update check.ps1 + inventory/list.ps1) +
  `health_check()` expanded 5 → 9 components (added npm, pip,
  inventory_db, web_registry).
- **Wave B — npm/pip + heartbeat.** `NpmManager` + `PipManager` Python
  classes + 5-phase PS contracts × 2 + lib/AscendoNpm.psm1 +
  lib/AscendoPip.psm1 + config manifests (npm: 15 packages, pip: 11
  packages). `Start-AscendoHeartbeat` / `Stop-AscendoHeartbeat` helpers
  emit `>>> still running (Ns)` every 10 s of silent work; wired into
  4 existing apply.ps1 with try/finally teardown.
- **Wave C — WebManager + salvage.** WebManager Python class +
  Pydantic `WebRegistryV2` schema + 3 handlers (github_release,
  release_feed, builtin) + curated 10-app `config/web_apps.toml`
  (4 Tier-A real-candidate probes: brave/obsidian/notion/obs-studio;
  6 Tier-B builtin: discord/slack/zoom/cursor/github-desktop/
  brave-nightly). New `_BaseWindowsManager._salvage_sidecar` mixin:
  bufdir-based incremental JSONL writes, salvage reconstructs the
  sidecar on crash with explicit `ASCENDO-SALVAGED` diagnostic.
- **Wave D — docs + validate.** `bin/validate-windows.ps1` extended
  with stages for npm / pip / web check, ascendo web lifecycle,
  build-inventory, sidecar salvage. WINDOWS_QUICKSTART + WINDOWS_TESTING
  + PLAN updates (this commit).

Test count 99 baseline → **229 passing** (+130; zero regressions).
WindowsAdapter `package_managers()` now returns 7 entries
(winget / msstore / npm / pip / web / arp / windows_update) — was 4.

---

## What landed in 2026-05-02 (post-Sesja 12)

Six commits on `claude/windows-end-to-end-2026-05-02`:

- `0ea118f` **docs(spec):** Windows end-to-end A+B+C design doc
  (`docs/superpowers/specs/2026-05-02-ascendo-windows-end-to-end-design.md`)
  laid out the three concurrent waves: CLI polish + dashboard wiring +
  frontend apply UX + Tauri 2.x scaffold.
- `30d1167` **feat(ui/desktop-tauri):** Tauri 2.x scaffold with Python
  sidecar + 4 scaffold tests; build hook in `bin/launch-desktop.ps1`.
- `742d6cc` **fix(plugin/dell-driver-update):** rewrote 5 PowerShell
  scripts (check/plan/apply/verify/cleanup) with the StrictMode-safe
  pattern + splat helpers + `Add-SidecarMessage -Text`; sidecars now save
  as `<phase>__plugin.json` (PowerShell-side adapter renamed enum).
- `f97afe8` **feat(cli):** wired `ascendo snapshot {create,list,restore}`,
  `ascendo schedule {install,remove,list,trigger}`, exit 75 on
  `needs_reboot`, new `ascendo runs json <id>` command.
- `de54a1b` **feat(dashboard):** `/inventory`, `/inventory/summary`,
  `/inventory/category/{c}`, `/health/check`, `/runs/active`,
  `/runs/active/stop`, SSE `/runs/{id}/events` wired to the real adapter
  (no more stubs).
- `18c5bcf` **feat(frontend):** apply confirmation modal (literal `apply`
  string), per-category 5-phase buttons, self-hosted Inter Tight +
  JetBrains Mono webfonts, wizard step for theme picker.

45 new tests (5 + 20 + 8 + 8 + 4) green; 2 pre-existing
`test_dashboard_spa.py` failures unchanged (predate this work).

---

## Current state

**Windows MVP feature-complete.** All 5 phases (`check / plan / apply / verify / cleanup`) work end-to-end against four package sources (`winget / msstore / registry_arp / windows_update`). Real-hardware validated on DP5520WMK:

```
check    →  4/4 success, 137 items inventoried (2 winget + 0 msstore + 135 ARP + 0 wu)
plan     →  4/4 success, 1 winget upgrade pending
apply    →  4/4 success on dry-run; real apply still pending the 1 winget package
verify   →  4/4 success
cleanup  →  pending re-test from Admin shell
```

Capabilities declared by `WindowsAdapter`:
`PACKAGE_MANAGEMENT | INVENTORY | SNAPSHOTS | SCHEDULING | ELEVATION`.

Plus: SPA dashboard with M2.10 async runs + SSE; CLI parity (`runs list / show`,
`dashboard --background`); design system (dark-primary) integrated; first plugin
(`dell-driver-update`) shipped.

**Branch:** `restructure/monorepo` (or whatever current branch — confirm with `git status`).

---

## Immediate next steps (the ~30-minute path to v0.0.7-alpha tag)

### 1. Run the real apply on Windows
```powershell
# Open an Administrator PowerShell. Then:
cd D:\Dev_Env\Ascendo

# Snapshot first (manual until M3.16 wires this in):
Checkpoint-Computer -Description "Ascendo pre-apply $(Get-Date -Format 'yyyy-MM-dd_HH-mm')" -RestorePointType MODIFY_SETTINGS

# Real apply on the 1 pending winget package:
python -m ascendo run --category winget --phase apply
python -m ascendo run --category winget --phase verify
python -m ascendo run --phase cleanup
```

### 2. Smoke-test the dashboard
```powershell
python -m ascendo dashboard --background
# Browser opens at http://127.0.0.1:8765/
# Expect: 137 items in Categories view; live SSE during a run from Run Center.
```

### 3. Diagnose the cleanup-1-failed-item from the earlier round (if it recurs)
```powershell
$last = (Get-ChildItem ~/.ascendo/runs -Recurse -Filter "cleanup__winget.json" |
         Sort-Object LastWriteTime -Descending | Select-Object -First 1).FullName
Get-Content $last | ConvertFrom-Json | Select-Object -ExpandProperty messages | Format-List
```
Most likely cause: `winget source reset --force` needs Admin. Re-run cleanup
from elevated shell.

### 4. Tag v0.0.7-alpha
```powershell
git tag -a v0.0.7-alpha -m "Windows MVP feature-complete on real DP5520WMK"
git push --tags
```

---

## Pending Windows polish (post-v0.0.7-alpha)

### `dell-driver-update` plugin scripts ✅ (2026-05-02)
- ~~5 plugin PowerShell scripts...~~ Done in commit `742d6cc`. All five
  (check/plan/apply/verify/cleanup) rewritten with the StrictMode-safe
  pattern; 8 lint tests pass. Sidecars now save as `<phase>__plugin.json`
  (PowerShell-side enum renamed from `dell_driver_update` to `plugin`).

### CLI snapshot/schedule wiring ✅ (2026-05-02)
- ~~`ascendo snapshot create` / `list` / `restore` placeholder...~~ Done in
  commit `f97afe8`. All snapshot subcommands (`create`/`list`/`restore`) and
  schedule subcommands (`install`/`remove`/`list`/`trigger`) wired to the
  M3.12/M3.13 managers via `_resolve_adapter_for_capability()`.

### Light-theme contrast pass
- Manual WCAG AA audit on every accent surface in light mode. `--accent-fg`
  alias already mitigates lime-on-paper, but a few hardcoded colors may still
  leak through.
- Effort: 4-6 hours.

---

## M4 — Distribution (path to v0.1.0-alpha, ~2-3 weeks)

| Item | Effort | Files |
|---|---|---|
| **MSI installer (WiX)** ✅ (2026-05-02 sesja 15) | done | `packaging/pyinstaller/ascendo.spec` + `bin/build-installer.ps1` produce `dist/Ascendo-<v>-x64.msi`. Tauri 2.x WiX bundler with `bundle.windows.wix` + branded BMPs. |
| **NSIS .exe installer** ✅ (2026-05-02 sesja 15) | done | Same pipeline, `dist/Ascendo-<v>-x64-setup.exe`. perMachine install, license page, Start menu + Desktop shortcuts, Add/Remove entry, NSIS hook file with sub-project 4 placeholder for service registration. |
| **winget manifest** | 1 day (sub-project 5) | PR to `microsoft/winget-pkgs`: `manifests/A/Ascendo/Ascendo/<version>/*.yaml` |
| **GitHub Releases CI** | 2-3 days | `.github/workflows/release.yml`: build + sign + publish on tag |
| **Authenticode signing** | 1 day | toolchain setup (azure trusted-signing or DigiCert); required for SmartScreen. `signtool` invocation documented in `packaging/README.md`. |
| **Tauri 2.x shell** ✅ scaffold (2026-05-02) | 3-4 days for full build ✅ done sesja 15 | `ui/desktop-tauri/` scaffold landed in `30d1167`; full packaged build wired in sesja 15 — `pwsh -File bin/build-installer.ps1` is the single command. Sidecar = PyInstaller-bundled `ascendo.exe`, no system Python needed. |
| **Frontend SPA migration** | 1-2 days (deferred) | Already mounted via `core/ascendo/dashboard/app.py`; physical move `app/frontend/` → `ui/frontend/` is the M4 step. |
| **Self-host webfonts** ✅ (2026-05-02) | done in `18c5bcf` | Inter Tight + JetBrains Mono woff2 dropped into `app/frontend/fonts/`; `@font-face` rules + Google Fonts CDN import removed. |

**Tag:** `v0.1.0-alpha` after MSI ships and runs end-to-end through winget install.

---

## M5 — macOS adapter (path to v0.2.0)

Mirror `adapters/windows/` as `adapters/macos/`. Same patterns, OS-specific tools.

| Sub | Status | Notes |
|---|---|---|
| **M5.1** | ✅ done (2026-05-03, **v0.0.8-alpha**) | `BrewManager` + `MacOSAdapter` (PACKAGE_MANAGEMENT only). Real `brew upgrade` performed end-to-end on Mac.r12.home. `bin/{install-dev,validate,run-tag-release}-macos.sh`. ~46 tests + 11/11 validate-macos.sh checks. Spec/plan: `docs/superpowers/specs/2026-05-03-macos-brew-mvp-design.md` + `docs/superpowers/plans/2026-05-03-macos-brew-mvp.md`. See HANDOFF.md Sesja 20. |
| **M5.2** | ✅ done (2026-05-04, **v0.0.9-alpha**) | `MasManager` + `MacElevation` (sudo askpass cache for dashboard-driven sudo). `sudo mas upgrade` enforced (CVE-2025-43411). Dashboard `POST /elevation/auth` round-trip green. 109 macOS adapter tests + 23/23 validate-macos.sh PASS on Mac.r12.home. Spec/plan: `docs/superpowers/specs/2026-05-03-macos-mas-elevation.md` + `docs/superpowers/plans/2026-05-03-macos-mas-elevation.md`. See HANDOFF.md Sesja 21. |
| **M5.3** | ✅ done (2026-05-04, **v0.0.10-alpha**) | `MacOSInventory` populates dashboard Categories tab via `system_profiler -json -detailLevel mini SPApplicationsDataType` + 5-rule classification (SYSTEM/MAS/BREW/WEB). 387 apps enumerated on Mac.r12.home (system=64, mas=13, brew=1, web=309). ~19 new tests + Stage 9 e2e via `validate-macos.sh`. Spec/plan: `docs/superpowers/specs/2026-05-04-macos-inventory-launchservices-design.md` + `docs/superpowers/plans/2026-05-04-macos-inventory-launchservices.md`. See HANDOFF.md Sesja 25. |
| **M5.4** | ✅ done (2026-05-04, **v0.0.11-alpha**) | `SoftwareUpdateManager` (default `sudo -A softwareupdate -ir -R --verbose`; `--all` for `-ia`; `--filter LABEL` for single-label apply; -R flag mandatory) + `TimeMachineSnapshot` read-only (`tmutil listlocalsnapshots /`; `create()` raises `SnapshotError` per APFS auto-management). Capability `SNAPSHOTS` added. `Sidecar.needs_reboot` moved to top-level (consumer fix). 22 local snapshots + softwareupdate 5-phase contract green on Mac.r12.home. ~56 new tests + Stage 10 + Stage 11 e2e via `validate-macos.sh`. Spec/plan: `docs/superpowers/specs/2026-05-04-macos-softwareupdate-snapshot-design.md` + `docs/superpowers/plans/2026-05-04-macos-softwareupdate-snapshot.md`. See HANDOFF.md Sesja 26. |
| **M5.5** | ✅ done (2026-05-05, **v0.2.0**) | `LaunchdScheduler` (per-user LaunchAgents in `~/Library/LaunchAgents/dev.ascendo.<name>.plist`); DSL mirrors WindowsScheduler (DAILY/WEEKLY/MONTHLY/HOURLY/MINUTE → `StartCalendarInterval` plist dict / `StartInterval` for the MINUTE form); description metadata in sidecar JSON at `~/Library/Application Support/Ascendo/schedules/<name>.json`. Capability `SCHEDULING` added; `MacOSAdapter.capabilities` now `PACKAGE_MANAGEMENT \| ELEVATION \| INVENTORY \| SNAPSHOTS \| SCHEDULING` (full Tier-1 minus `SOURCE`, which is M6 cross-cutting). Final review caught 3 bugs in pre-existing M5.5.7 code (argv flag mismatch `--output` vs `--output-path`, trigger error swallow, stale docstring) — fixed in M5.5.11.1. **34/34 PASS** via `bin/validate-macos.sh` Stage 12 e2e (5 sub-steps) on Mac.r12.home; tag `v0.2.0` cut locally. **Tag `v0.2.0` — full M5 macOS adapter feature-complete.** Spec/plan: `docs/superpowers/specs/2026-05-04-macos-launchd-scheduler-design.md` + `docs/superpowers/plans/2026-05-04-macos-launchd-scheduler.md`. See HANDOFF.md Sesja 28. |
| **M5.6** | ✅ done (2026-05-06, **v0.3.0**) | `WebManager` (sixth IPackageManager) covering ~24 apps installed outside brew/mas/softwareupdate via 7 update mechanisms: sparkle (appcast XML + DMG), github_dmg (GH Releases API + arm64 asset), keystone (Google Software Update agent), squirrel (Squirrel.Mac auto-on-relaunch), builtin (open + emit instruction), msupdate (Microsoft AutoUpdate), docker (Docker Desktop CLI). Pydantic-validated `_apps.toml` registry with shipped baseline + user override at `~/.config/ascendo/web_apps.toml`; merge by slug. Defer-if-running per-handler — sparkle/github_dmg/squirrel defer when app is running, keystone/msupdate/docker apply regardless. Verify sleeps 30s for squirrel / 10s for keystone (async update agents). `/Applications` writes try without sudo first; sudo -A on EACCES. spctl signature verification + quarantine xattr strip on installed bundles. **MacOSAdapter** package_managers 5 → 6, health_check 11 → 12 components. **41/41 PASS** via `bin/validate-macos.sh` Stage 13 (7 sub-steps) on Mac.r12.home; aggregate test suite 358/358. Spec/plan: `docs/superpowers/specs/2026-05-06-macos-web-updater-design.md` + `docs/superpowers/plans/2026-05-06-macos-web-updater.md`. |
| **M5.7** | ✅ done (2026-05-08, **v0.4.0**) | Web auto-discovery + tiered probes: closes the breadth + depth gaps in M5.6. New `lib/web_discovery.sh` walks `/Applications/*.app/Contents/Info.plist`, fingerprints each bundle (`SUFeedURL` → sparkle, `KSProductID` → keystone, `Squirrel.framework` → squirrel, otherwise builtin), excludes brew/mas/softwareupdate-owned bundles. `web_apps.toml` schema bumped v1 → v2 with `bundle_id`-keyed override merge (auto-coerces v1 with one-time DeprecationWarning). New `release_feed` Tier-A handler — generic JSON-over-HTTPS probe so per-vendor probes become TOML config rather than new code. New `ItemStatus.TRIGGERED` enum + `Summary.triggered` bucket so Tier-B handlers (keystone/squirrel/builtin) emit honest `triggered` status on apply (with `triggered_pending` / `triggered_confirmed` informational sub-states from verify) instead of conflating with synchronous `success`. Discovery-driven check + plan iteration replaces static `--list-slugs` registry walk. Real Mac.r12.home: web check now emits **51 items** (was 13) — every web-orphan installed app appears with the most precise candidate version each vendor's update mechanism allows. **41/41 PASS** via `bin/validate-macos.sh` (Stage 13.8 discovery, 13.9 release_feed, 13.10 web check >=20 items) on Mac.r12.home; aggregate test suite 364/364. Spec/plan: `docs/superpowers/specs/2026-05-08-macos-web-discovery-design.md` + `docs/superpowers/plans/2026-05-08-macos-web-discovery.md`. |
| **M5.7.1** | ✅ done (2026-05-08, **v0.4.1**) | Operator-driven coverage push after testing v0.4.0. **3 real bugs fixed**: (1) discovery now emits extracted `SUFeedURL` + `KSProductID` into JSON (was: silently dropped, ~6 Sparkle apps reported empty candidate); (2) MAS apps filtered via `Contents/_MASReceipt` directory check (was: `mas list` returned numeric track IDs not bundle IDs, so 8 MAS apps polluted web inventory); (3) sparkle handler now picks highest version not first match (was: AppCleaner reported 3.4 instead of 3.6.8). **Polish 2 fixes**: Docker switched from `docker` handler (probed CLI plugin v0.3.0, NOT Docker.app v4.71!) to `sparkle` against real appcast at `desktop.docker.com/mac/main/arm64/appcast.xml`; RDM forced to `builtin` (vendor's appcast frozen at 2023.1.12.0); Obsidian asset_pattern fixed for universal binaries (`Obsidian-[0-9.]+\.dmg$` + `arch = "universal"`); Brave reclassified `keystone` (was: sparkle reported nonsense version 1.90.121.0 vs CFBundleShortVersionString 148.1.90.121). **8 new vendor probes**: VSCode + Zoom + Firefox-Dev + Notion + Ledger Live + KeePassXC + Obsidian (release_feed JSON or YAML) + Codex (sparkle). Plus YAML support in `release_feed` handler for Electron-builder `latest-mac.yml` format. Real Mac.r12.home: web check 4 → **14 apps** with real candidate. Outdated detected: Docker 4.71→4.72, Firefox-Dev 151.0→151.0b7, ProtonVPN 6.5.0→6.5.1, Zoom .→.77593. 364/364 tests. See HANDOFF.md Sesja 38. |
| **M5.7.2** | ✅ done (2026-05-08, **v0.4.2**) | App.asar binary mining via subagent. Subagent grepped Electron `app.asar` archives + native binaries for `setFeedURL` + `https://` contexts, then dry-ran each candidate with `curl -sI` to verify HTTP 200 + parseable response. **4 new vendor probes**: **Claude** (`com.anthropic.claudefordesktop`) → `release_feed` against `api.anthropic.com/api/desktop/darwin/universal/squirrel/update?device_id=<UUID>` (zero UUID returns same currentRelease as real client; live probe 1.6608.0); **Codex** (`com.openai.codex`) → `sparkle` against `persistent.oaistatic.com/codex-app-prod/appcast.xml` (Codex bundles BOTH Squirrel + Sparkle frameworks but Sparkle is active; live probe 26.506.21252); **Notion Calendar** (`com.cron.electron` — Notion never rebranded the Cron Calendar bundle id) → `release_feed` YAML against `calendar-desktop-release.notion-static.com/latest-mac.yml` (Electron-builder format; live probe 1.133.0); **Cursor** (`com.todesktop.230313mzl4w4u92`) → `release_feed` YAML against `download.todesktop.com/230313mzl4w4u92/latest-mac.yml` (validated despite not being installed locally; live probe 0.45.14). Real Mac.r12.home: web check 14 → **17 apps** with real candidate. **6 apps still requiring mitmproxy on launch** (URLs not statically discoverable, deferred to M5.7.3): ChatGPT (Sparkle SUFeedURL injected at runtime), Warp (custom Rust GCS bucket), MEGAsync (proprietary Qt updater), LM Studio (private R2 bucket), Antigravity (per-build commit hash endpoint), Comet (Omaha4/Keystone protobuf). 364/364 tests pass. Apply dry-run: 19 items (16 planned + 3 skipped). See HANDOFF.md Sesja 39. |
| **M5.7.3** | ✅ done (2026-05-08, **v0.4.3**) | "Implement all missing updates" coverage push. **3 fixes from v0.4.2 sidecar audit**: (1) `release_feed_apply` URL-encodes resolved download URL via `urllib.parse.urljoin + quote(safe="/%")` so filenames with spaces (Notion Calendar/Cursor) survive curl; (2) `web/apply.sh` pre-dispatch `<handler>_check` call skips up_to_date Tier-A apps instead of redownloading + reinstalling vscode/notion/obsidian/etc.; (3) `pip/verify.sh` mirrors check.sh's brew-pip self-skip rule. **9 new web Tier-A apps**: 6 MS365 (`ms-word/excel/powerpoint/outlook/onenote/teams`) via per-app `[app.msupdate.app_id]` targeting (msupdate handler reads installed version from `msupdate --config` and runs `--install --apps <ID>`; new `MsupdateConfig` Pydantic schema; Application IDs MSWD2019/XCEL2019/PPT32019/OPIM2019/ONMC2019/TEAMS21 verified live against MAU 4.83); plus **chatgpt** squirrel→sparkle (oaistatic.com sidekick appcast; live 1.2026.118), **opencode** squirrel→github_dmg (anomalyco/opencode — sst guess was wrong, found via `app-update.yml`; live 1.14.41 vs installed 1.14.40 = outdated), **proton-mail** new release_feed (proton.me/download/mail/macos/version.json; live 1.13.0). **Discovery filters**: `_owned_by` returns `"ineligible"` for `com.google.drivefs.shortcuts.*` (Google Workspace shortcuts), `com.google.Chrome.app.*` (Chrome WebApp bundles), `com.microsoft.wdav.*shim*` (Defender shim); `ASCENDO_WEB_INELIGIBLE_PATTERNS` env var for user extension. **Removed dead `perplexity` registry entry** (it's a MAS app — discovery filters via `_MASReceipt`). **Hygiene follow-ups from PLAN.md Sesja 34**: `InventoryDB.bulk_upsert` paired with `db.clear_category(cat)` in 3 live-scan paths (closes "Apps shows pip 12 while Run Center shows 11"); pip brew-skip regression test added. **7 apps explicitly ruled out** as M5.7.4 backlog: warp + megasync need release_feed `version_regex` field; brave/chrome/gdrive/gemini need Omaha protocol support; antigravity has stale upstream API; lm-studio behind R2 auth; comet uses Omaha protobuf. Real Mac.r12.home: 211 of 224 apps (94%) with real candidate (was 196/228 = 86%). 365/365 macOS tests + 222/231 contract tests. See HANDOFF.md Sesja 40. |
| **M5.7.4** | ✅ done (2026-05-08, **v0.4.4**) | "Implement the rest of the missing" — closes 5 of 7 apps from M5.7.3 backlog. **Schema/handler extensions**: (1) `version_regex` + `version_replace` Optional fields on `ReleaseFeedConfig` (XOR validator, compile-time regex check, `re.sub` once with raw-fallback on no-match); (2) `format = "text"` mode on release_feed (skips JSON walking; version_regex matches the raw HTTP body directly — required for vendors who publish key=value text feeds); (3) 2 MiB body cap (was 256 KiB; Warp's `channel_versions.json` is 860 KiB and was being truncated mid-string causing silent rc=27 fails). **5 new web Tier-A apps**: **warp** squirrel→release_feed (`releases.warp.dev/channel_versions.json` `stable.version` + regex `^v(.+)\.stable_(.+)$` → `\1.\2`; live 0.2026.05.06.15.42.02); **megasync** builtin→release_feed (`api.github.com/repos/meganz/MEGAsync/releases/latest` `tag_name` + regex `^v(.+)_(?:Linux\|OSX\|Win)$` → `\1`; live tag = `v6.3.0.1_Linux` → `6.3.0.1`; outdated detected vs installed 6.2.2); **chrome** keystone→release_feed (`versionhistory.googleapis.com/v1/chrome/platforms/mac_arm64/channels/stable/versions` `versions[0].version` — Google's official Chrome Version History API works without auth!; live 148.0.7778.97); **brave** keystone→release_feed (`api.github.com/repos/brave/brave-browser/releases/latest` `name` field carries `"Release v1.90.121 (Chromium 148.0.7778.96)"` which regex composes into `148.1.90.121` matching CFBundleShortVersionString); **rdm** builtin→release_feed format=text (`devolutions.net/productinfo.htm` regex extracts `RDMMacbin.Version=...` from key=value text body; live 2026.1.11.4). **5 apps explicitly ruled out** as M5.7.5 backlog: gdrive + gemini (Omaha protocol; checked all `dl.google.com/drive-file-stream/*` paths + `versionhistory.googleapis.com/v1/{drivefs,gemini}` — all 404/400; re-check annually); comet + lm-studio (need mitmproxy runtime capture); antigravity (vendor's `productVersion` API field is stale — internally inconsistent). Real Mac.r12.home: 216 of 224 apps (96%) with real candidate (was 211/224 = 94%). Web Tier-A 26 → 31; Tier-B 13 → 8. **369/369 macOS tests** + 5 new (2 bash regex transform, 3 Pydantic schema). New outdated detected: megasync 6.2.2→6.3.0.1. See HANDOFF.md Sesja 41. |
| **M5.7.5** | ✅ done (2026-05-08, **v0.4.5**) | **100% real-candidate coverage achieved** (223 of 224 apps; the 1 trigger-only entry is `ascendo` itself, intentionally `enabled = false` until KasprowiczM/ascendo ships its first public GitHub Release). **New `omaha` handler** at `adapters/macos/lib/handlers/omaha.sh` (~280 LOC) implements Google's Omaha update protocol: POST + XML body for protocol="3.0" (Google first-party products) and POST + JSON body for protocol="4.0" (Comet's Perplexity-hosted Omaha-compatible service). New `OmahaConfig` Pydantic schema (endpoint + appid + protocol + tag + brand + http_timeout_s); cross-handler validators allow `ksadmin_product_id` on omaha entries for apply delegation. **7 Tier-A promotions**: **gdrive** keystone→omaha (`update.googleapis.com/service/update2`; appid `com.google.drivefs`; live 124.0→**125.0.0.0 outdated detected!**); **gemini** keystone→omaha (same endpoint with channel tag `m1-prod` from ksadmin output — without the tag Google returns noupdate; live 1.53.0.262); **comet** squirrel→omaha (Perplexity-hosted Omaha 4.0 JSON at `www.perplexity.ai/rest/browser/update2`; URL discovered via `strings` on CometUpdater binary; live 147.0.7727.1858); **inkscape** builtin→release_feed (text-scrape `<title>` from `inkscape.org/release/`; live 1.4.4); **spotify** builtin→release_feed (Homebrew autobump-tracked cask API at `formulae.brew.sh/api/cask/spotify.json` `.version` since vendor's own endpoint is Bearer-auth-walled; live 1.2.87.415→**1.2.88.483 outdated detected!**); **antigravity** squirrel→release_feed text (Cloud Run service's ROOT path returns plain `Stable Version: X.Y.Z` while the JSON path has stale productVersion=1.107.0; live 1.23.2 = installed); **lm-studio** squirrel→release_feed (Homebrew cask API; brew comma-format `0.4.12,1` regex-normalized to CFBundle `0.4.12+1`). **Apply remains Tier-B** for omaha entries — Keystone / CometUpdater own the actual install; we surface candidate version only. **377/377 tests pass** + 8 new (5 omaha protocol incl. XML/JSON round-trip + appid validation + tag handling; 3 last-mile tests). **9 outdated apps detected this run**: codex, docker, firefox-dev, gdrive, megasync, opencode, protonvpn, spotify, zoom. See HANDOFF.md Sesja 42. |

### Forward backlog (per-manager scope)

| Manager | Tool | Est LOC (Python + Bash) | Sub |
|---|---|---|---|
| `managers/brew.py` | Homebrew (formulae + casks) | 190 + 600 | ✅ M5.1 |
| `managers/mas.py` | Mac App Store via `mas` CLI | 100 + 200 | ✅ M5.2 |
| `managers/elevation.py` | sudo + AuthorizationCreate | 80 + 100 | ✅ M5.2 |
| `managers/launchservices.py` | LaunchServices (ARP-equivalent) | 100 + 200 | ✅ M5.3 |
| `managers/softwareupdate.py` | `softwareupdate -l` + `-i -R` | 100 + 150 | ✅ M5.4 |
| `snapshot.py` | Time Machine read-only | 80 + 150 | ✅ M5.4 |
| `managers/scheduler.py` | launchd | 80 + 200 | ✅ M5.5 |
| `managers/web.py` | Web apps (Sparkle/GH/Keystone/Squirrel/msupdate/Docker) | 250 + 1100 | ✅ M5.6 |

**lib (M5.1 shipped):** `adapters/macos/lib/{_json_emit.py, ascendo_json.sh, ascendo_brew.sh}`. The Bash + Python helper pattern matches the Linux adapter (cross-platform consistency lives in the shared CONTRACT — `ascendo/v1` schema + 5-phase + Pydantic interfaces — not in shared code).

**Critical rules to preserve from `Aktualizacje_MAC/CLAUDE.md`:**
- `softwareupdate` MUST have `-R` flag (M5.4).
- `mas upgrade` MUST have `sudo` (CVE-2025-43411) (M5.2).
- Bash 3.2 only (no `declare -A`, `mapfile`, `readarray`) — honored throughout M5.1.

---

## M6 — Hardening + v1.0 stable (open scope)

- Security audit (3-7 threat-model items per ADR-0005).
- Code signing across all three OSes.
- Plugin signing + verification (FAZA II).
- Plugin marketplace UX in dashboard.
- Localization beyond en/pl (tokens already support es/it/pt/de/fr).
- Telemetry (opt-in, 100% local-only — no centralised backend per project rules).

---

## Quick-win backlog (each < 1 day)

1. **IInventory wired to SPA `/apps`** ✅ (2026-05-02) — done in `de54a1b`.
   Endpoint renamed `/apps` → `/inventory[/summary]`; SPA Categories tab
   reads live data via the real `WindowsInventory` adapter.
2. **Wizard step for theme picker** ✅ (2026-05-02) — done in `18c5bcf`.
   Wizard now has 5 steps; theme step persists `dark` vs `light` to
   settings + `data-theme` on `<html>`.
3. **`ascendo runs json <id>`** ✅ (2026-05-02) — done in `f97afe8`.
   Emits `ascendo/run/v1` JSON with sidecars + summary + `needs_reboot`.
4. **Health card on dashboard Overview** ✅ (2026-05-02) — done in
   `de54a1b`. `/health/check` returns real `score 0-100` + `issues[]`;
   Overview card renders it.
5. **Reboot detection in CLI** ✅ (2026-05-02) — done in `f97afe8`. `run`
   now scans messages for "Reboot required" and exits 75 when SUCCESS;
   stderr line "system reboot required to complete updates".
6. **Windows health check expansion (5 → 9 components)** ✅ (2026-05-12,
   Sesja 58) — added `npm`, `pip`, `web_registry`, `inventory_db` to
   the rollup. Closes the cross-platform parity gap (macOS now has 12,
   Ubuntu 13, Windows 9).
7. **Windows bidirectional `from=/to=` inventory fix** ✅ (2026-05-12,
   Sesja 58) — same structural fix as Linux Sesja 57 applied to 5 PS
   files (winget/msstore/arp/windows_update check.ps1 +
   inventory/list.ps1). SPA inventory rows now paint `installed=` +
   `candidate=` correctly across every Windows category.
8. **Watchdog heartbeat on Windows** ✅ (2026-05-12, Sesja 58) —
   `Start-AscendoHeartbeat` / `Stop-AscendoHeartbeat` helpers in
   `AscendoJson.psm1`; wired into 4 apply.ps1 via try/finally. Long
   winget upgrades no longer look hung in the SPA.
9. **Windows NpmManager + PipManager** ✅ (2026-05-12, Sesja 58) —
   Windows operators can finally update Node + Python global CLIs
   through Ascendo. 5-phase PS contracts × 2 + lib helpers + config
   manifests (15 npm + 11 pip).
10. **Windows WebManager scaffold** ✅ (2026-05-12, Sesja 58) — Pydantic
    `WebRegistryV2` schema + 3 handlers (github_release, release_feed,
    builtin) + 10-app curated TOML registry. 4 Tier-A real-candidate
    probes live; full Tier-A apply with Authenticode + UAC handoff
    parks for v0.0.9.
11. **Sidecar salvage on Windows** ✅ (2026-05-12, Sesja 58) — new
    `_BaseWindowsManager._salvage_sidecar` mixin; bufdir-based JSONL
    writes survive Ctrl-C / UAC-denied / kernel kill; recovery
    diagnostic `ASCENDO-SALVAGED` lands in messages[0] for SPA
    visibility. Mirror of the Linux Sesja 56 mechanism.

---

## Cross-cutting tech-debt items

- **PowerShell script generator/template** — every new plugin/manager copy-pastes ~80 LOC of boilerplate (param block, lib import, helpers, sidecar init, catch block). Extract a code generator or a `scripts/_template/<phase>.ps1` skeleton. Saves ~2 days per future plugin.
- **`Read-WingetTabularOutput` not exported** — currently every winget-style script uses `Get-WingetUpgradable` / `Get-WingetInstalled`, which can't filter by source. Either export the parser or add a `-Source` parameter to the high-level functions. Will simplify future package-source plugins.
- **Sidecar test fixtures** — `tests/fixtures/sidecars/` has 2 examples; should grow as new manager types ship so contract tests catch schema drift.
- **`Set-StrictMode -Version Latest` defensive helpers** — codify `_Get-RegProp` and `_p` scriptblock patterns in `AscendoJson.psm1` so every plugin gets them for free.

---

## Decisions log (link to ADRs)

All architectural decisions are in `docs/architecture/`:
- ADR-0001 monorepo with adapters
- ADR-0002 Tauri as desktop shell
- ADR-0003 JSON v1 sidecar contract
- ADR-0004 Python core + native script adapters
- ADR-0005 six-layer clean architecture (incl. T1-T7 threat model)
- ADR-0006 two-tier adapter system (official vs contrib)
- ADR-0007 plugin manifest v1

When making a new architectural decision, write a new ADR; don't bury it in HANDOFF.md.

---

## Reference: every M3.X status

| Item | Status | Sesja |
|---|---|---|
| M3.1 — AscendoJson.psm1 | ✅ | 5 |
| M3.2 — AscendoWinget.psm1 (column parser) | ✅ | 5 |
| M3.3 — winget/check.ps1 | ✅ | 5 |
| M3.4 — WindowsAdapter + WingetManager | ✅ | 5 |
| M3.5 — Integration smoke | ✅ | 5 |
| M3.6 — winget/apply.ps1 | ✅ | 6 |
| M3.7 — plan/verify/cleanup for winget | ✅ | 6 |
| M3.8 — msstore | ✅ | 11 |
| M3.9 — registry_arp | ✅ | 11 |
| M3.10 — PSWindowsUpdate | ✅ | 10 |
| M3.11 — WindowsInventory | ✅ | 10 |
| M3.12 — VSS snapshot | ✅ | 12 |
| M3.13 — Task Scheduler | ✅ | 12 |
| M3.14 — UAC elevation | ✅ | 12 |
| M3.15 — Dell DCU plugin | ✅ shipped, ⚠️ scripts need same fixes msstore got | 12 |
| M3.16 — real-hardware validation | ✅ on DP5520WMK | 12+ |

Beyond M3 = M4 (distribution) → M5 (macOS) → M6 (hardening).
