# Ascendo — Platform Status

> **Single source of truth for cross-platform parity.** Honest audit of
> what's implemented, partial, or missing on each of the three Tier-1
> platforms (macOS, Windows, Linux/Ubuntu) as of v0.6.1 (Sesja 54+55,
> 2026-05-11). Use this when planning roadmap work, when answering
> "is X on Y supported yet?", or when triaging a bug report by
> platform.
>
> Markers: ✅ shipped & validated · 🟡 partial / shim / deferred · ❌
> not implemented · N/A not applicable on this platform.
>
> When a row says ✅ on macOS, it means the operator has run it
> end-to-end on real hardware (Mac.r12.home). When it says ✅ on
> Windows, the same on DP5520WMK. **As of v0.6.1, ✅ on Linux now
> means operator-validated end-to-end on mk-uP5520 (Ubuntu 24.04)** —
> 23/23 `bin/validate-ubuntu.sh`, 35 sidecars × 5 phases × 7
> categories, 2579 inventoried items, eight live-fire bug fixes
> shipped post-validation.

---

## A. Feature matrix

### A.1 — Package managers / sources

| Source | macOS | Windows | Linux/Ubuntu | Notes |
|--------|:-----:|:-------:|:------------:|-------|
| Homebrew (formulae + casks) | ✅ | N/A | ✅ Linuxbrew at `/home/linuxbrew/.linuxbrew` | macOS canonical; same Python `BrewManager` shape both sides |
| Mac App Store (`mas`) | ✅ CVE-2025-43411 mitigation enforced | N/A | N/A | `sudo mas upgrade` — bare `mas upgrade` rejected |
| macOS softwareupdate (OS / security / Safari) | ✅ `-R` flag mandatory | N/A | N/A | reboot-survival: pre-emit success items + JSON_FINALIZED=1 |
| macOS LaunchServices inventory (`/Applications/*.app`) | ✅ 387 apps on Mac.r12.home | N/A | N/A | `system_profiler -json SPApplicationsDataType` + classification |
| macOS web-installed apps (Sparkle / Keystone / Squirrel / GH releases / Omaha / msupdate / Docker) | ✅ 8 handlers, ~100% real-candidate (223/224 apps) | ❌ | ❌ | M5.6 + M5.7.1–7.5; `web_apps.toml` v2 registry + auto-discovery |
| winget | N/A | ✅ stderr capture (Sesja 45) + up_to_date guard | ❌ | column-position parser, exit-code mapping, Read-WingetTabularOutput |
| Microsoft Store (msstore via winget) | N/A | ✅ stderr capture + up_to_date guard | ❌ | same `WingetManager` base, different feed |
| MSI / Registry ARP | N/A | ✅ stderr capture | ❌ | Add-or-Remove-Programs scan via 3 registry roots |
| PSWindowsUpdate (KB patches) | N/A | ✅ stderr capture (in-process cmdlet) | ❌ | reboot=disable safety; reboot signal via exit 75 |
| apt / dpkg | N/A | N/A | ✅ legacy bash | NVIDIA hold by default; `--nvidia` opt-in |
| snap | N/A | N/A | ✅ legacy bash | `sudo snap refresh` |
| flatpak | N/A | N/A | ✅ legacy bash | user-mode default; `--system` flag for system-wide |
| npm globals | ✅ `NPM_CONFIG_PREFIX` env (no `.npmrc` write) | 🟡 not in adapter (use `nvm` directly) | ✅ legacy bash + `config/npm-globals.list` | macOS scrubs `prefix=` from `.npmrc` (Sesja 44) |
| pip globals | ✅ brew-pip self-skip | 🟡 not in adapter | ✅ legacy bash + PEP 668 `--break-system-packages` | macOS handles 4 flavours: brew / pyenv / system / venv |
| firmware (LVFS / fwupd) | ❌ macOS handles via softwareupdate | 🟡 firmware via Dell DCU plugin only | ✅ `fwupdmgr` via `scripts/drivers/` | LVFS on Linux supports HP / Lenovo / many vendors |
| NVIDIA driver | N/A (no Mac NVIDIA support) | 🟡 via Dell driver plugin only | ✅ `apt install nvidia-driver-*` (held by default) | |
| Dell driver update (DCU) | N/A | ✅ `plugins/dell-driver-update/` | N/A | first official plugin; manifest v1 |

### A.2 — 5-phase contract per source

| Phase | macOS | Windows | Linux |
|-------|:-----:|:-------:|:-----:|
| `check` (read-only) | ✅ all 6 sources + web | ✅ all 4 sources | ✅ all 7 sources |
| `plan` (would-change preview) | ✅ all 6 + web | ✅ all 4 | ✅ all 7 |
| `apply` (mutating) | ✅ all 6 + web | ✅ all 4 | ✅ all 7 |
| `verify` (post-apply) | ✅ all 6 + web | ✅ all 4 | ✅ all 7 |
| `cleanup` (prune / autoremove) | ✅ all 6 + web | ✅ all 4 | ✅ all 7 |
| Real-apply tested e2e | ✅ Mac.r12.home (multiple Sesja runs) | ✅ DP5520WMK (Sesja 12 + Sesja 45 fixes) | 🟡 legacy bash validated; Python adapter shim **needs operator validation on mk-uP5520** |

### A.3 — Inventory (Categories / Apps tabs)

| Feature | macOS | Windows | Linux |
|---------|:-----:|:-------:|:-----:|
| `IInventory` Python impl | ✅ `MacOSInventory` | ✅ `WindowsInventory` | ✅ `UbuntuInventory` (drives `list.sh`) |
| Cross-source enumeration script | ✅ `inventory/list.sh` | ✅ `inventory/list.ps1` | ✅ `adapters/ubuntu/scripts/inventory/list.sh` |
| Number of sources covered | 5 (system_profiler + brew + mas + npm + pip) | 4 (winget + msstore + arp + wu) | 6 (apt + snap + flatpak + brew + npm + pip) |
| SQLite `inventory.db` cache | ✅ | ✅ | ✅ (shared core) |
| Auto-clear-before-bulk-upsert | ✅ | ✅ | ✅ (Sesja 40 + 45 fix wired in core) |
| Per-app history (`update_history` table) | ✅ Sesja 43 | ✅ via core | ✅ via core |
| Apps tab parity with Categories | ✅ Sesja 32 | ✅ via core | ✅ via core |

### A.4 — Dashboard / SPA / Run Center

| Feature | macOS | Windows | Linux |
|---------|:-----:|:-------:|:-----:|
| FastAPI backend | ✅ | ✅ | ✅ |
| Vanilla SPA (`app/frontend/`) | ✅ | ✅ | ✅ |
| Run Center with live SSE log streaming | ✅ | ✅ | ✅ |
| 5-phase per-category buttons | ✅ | ✅ | ✅ |
| Apply confirmation modal (literal `apply` gate) | ✅ | ✅ | ✅ |
| Suggestions (AI-driven 3-step wizard) | ✅ Anthropic / OpenAI / OpenRouter / Ollama / Gemini / LM Studio | ✅ same | ✅ same |
| Post-apply REPORT.md generator | ✅ Sesja 43 | ✅ via core | ✅ via core |
| Hosts editor (multi-machine) | ✅ | ✅ | ✅ |
| Adapter-conditional UI (per-OS gating) | ✅ | ✅ | ✅ Sesja 32 |
| Dark theme primary | ✅ | ✅ | ✅ |
| EN / PL i18n parity | ✅ 693/693 keys | ✅ | ✅ |
| Touch ID / biometric pass-through | ✅ via `pam_tid.so` (Sesja 36) | N/A (UAC) | ❌ no biometric pass-through |

### A.5 — Scheduler (recurring runs)

| Feature | macOS | Windows | Linux |
|---------|:-----:|:-------:|:-----:|
| Backend technology | launchd (per-user LaunchAgent) | Task Scheduler (`\Ascendo\<name>`) | systemd `--user` timer |
| `IScheduler` Python impl | ✅ `LaunchdScheduler` (M5.5) | ✅ `WindowsScheduler` (M3.13) | ✅ `SystemdScheduler` (Sesja 54, v0.6.1) |
| `ascendo schedule` CLI | ✅ install / list / trigger / remove | ✅ install / remove / list / trigger | ✅ install / list / trigger / remove |
| Direct bash script | N/A | N/A | ✅ `adapters/ubuntu/scripts/scheduler/scheduler.sh` (JSON-IPC) |
| DSL mirror (DAILY / WEEKLY / MONTHLY / HOURLY / MINUTE) | ✅ | ✅ | ✅ — translates to `OnCalendar=` / `OnUnitActiveSec=` |

### A.6 — Snapshots (pre-apply rollback)

| Feature | macOS | Windows | Linux |
|---------|:-----:|:-------:|:-----:|
| Backend | APFS local snapshots (Time Machine) | VSS (`Checkpoint-Computer`) | timeshift (preferred) / etckeeper (fallback) |
| `ISnapshot` Python impl | ✅ `TimeMachineSnapshot` (read-only — list only) | ✅ `WindowsSnapshot` (create + list) | ✅ `TimeshiftSnapshot` (Sesja 54, v0.6.1) — create + list |
| Programmatic create | ❌ APFS auto-managed; Apple deprecated `tmutil snapshot` for user code | ✅ `Checkpoint-Computer` System Restore Point | ✅ `sudo -A timeshift --create --scripted` |
| List snapshots | ✅ `tmutil listlocalsnapshots /` | ✅ `Get-CimInstance Win32_ShadowCopy` | ✅ `timeshift --list` parser |
| Restore | ❌ only via Recovery / `tmutil restore` | 🟡 deferred (M3.X — `vssadmin revert` + UAC) | ❌ deliberately omitted (destructive — use `sudo timeshift --restore` from recovery shell) |
| Pre-apply hook (auto-snapshot before `apply`) | ❌ deferred — manual `tmutil localsnapshot` | ✅ `bin/run-tag-release.ps1` integration | ✅ `update-all.sh --snapshot` AND `ascendo snapshot create` |

### A.7 — Elevation (sudo / UAC)

| Feature | macOS | Windows | Linux |
|---------|:-----:|:-------:|:-----:|
| `IElevation` Python impl | ✅ `MacElevation` | ✅ `WindowsElevation` | ✅ `LinuxElevation` (Sesja 54, v0.6.1) |
| Mechanism | sudo + `SUDO_ASKPASS` cache (in-memory) | UAC via `ShellExecuteW lpVerb=runas` | sudo + askpass helper at `adapters/ubuntu/lib/askpass_helper.sh`, password cached in-memory via `_ASCENDO_SUDO_PW` env |
| Dashboard `/elevation/auth` + `/elevation/status` endpoints | ✅ | ✅ | ✅ — works unchanged with the macOS dashboard router |
| Touch ID / biometric | ✅ `pam_tid.so` honoured (Sesja 36) | N/A | ❌ (TODO: pam_fprintd / Polkit `pkexec` follow-up) |
| Argv-only (no shell strings) | ✅ | ✅ T4 mitigation enforced | ✅ Python-side via `LinuxElevation.run()` allowlist; bash-side via `_ascendo_sudo` |
| Allow-list enforcement | ✅ | ✅ lowercase basename normalisation | 🟡 implicit via specific bash callsites |
| Dashboard `/elevation/auth` round-trip | ✅ | ✅ | 🟡 unverified — may need shim wiring |

### A.8 — Desktop shell (Tauri 2.x)

| Feature | macOS | Windows | Linux |
|---------|:-----:|:-------:|:-----:|
| Tauri 2.x scaffold | ✅ | ✅ | ✅ shared `ui/desktop-tauri/` |
| Native window backend | WKWebView | WebView2 | WebKitGTK |
| Production `.app` / `.msi` / `.deb` build | ✅ `tauri build` validated | ✅ | 🟡 not yet validated on real Ubuntu |
| Code-signed binary | ❌ deferred (M6) | ❌ deferred (M6) | N/A (unsigned) |
| Cmd+Tab / Dock / Start menu icon | ✅ refresh-macos-icon.sh handles cache | ✅ | 🟡 `.desktop` entries shipped, icon refresh untested |
| Build prerequisites | Rust + Node + Xcode CLT | Rust + Node + MSVC + WebView2 | Rust + Node + `libwebkit2gtk-4.1-dev` + GTK |

### A.9 — Service install (always-on dashboard)

| Feature | macOS | Windows | Linux |
|---------|:-----:|:-------:|:-----:|
| Service backend | launchd | NSSM-wrapped Windows Service | systemd `--user` unit |
| Install script | ❌ deferred — use launchd LaunchAgent for scheduler only | ✅ `bin/install-service.ps1` (UAC) | ✅ `bash systemd/user/install-dashboard.sh` |
| Auto-start on login | 🟡 manual launchd plist | ✅ `Automatic (Delayed Start)` | ✅ `WantedBy=default.target` |
| Auto-restart on crash | N/A | ✅ NSSM recovery | ✅ `Restart=on-failure` |
| Logs path | N/A | `%LocalAppData%\Ascendo\logs\service\` | `journalctl --user -u ascendo-dashboard` |

### A.10 — Smart installer (one-liner)

| Feature | macOS | Windows | Linux |
|---------|:-----:|:-------:|:-----:|
| Curl-pipe install | ✅ `install.sh` (POSIX) | ✅ `install.ps1` | ✅ same `install.sh` |
| Curl-pipe update | ✅ `update.sh` | ✅ `update.ps1` | ✅ same `update.sh` |
| Auto Python install | 🟡 reports + suggests `brew install python` | ✅ via winget | ✅ via apt / dnf / pacman |
| Pre-flight network check | ✅ | ✅ | ✅ |
| Pre-flight disk check (≥1 GB) | ✅ | ✅ | ✅ |
| Locked package-manager detection | N/A | N/A | ✅ `fuser /var/lib/dpkg/lock` |
| Final `ascendo doctor` self-test | ✅ | ✅ | ✅ |
| Distribution package | 🟡 `.dmg` deferred (M6) | ✅ MSI + NSIS .exe (M4) | 🟡 `.deb` packaging exists, validation pending |

### A.11 — Plugins

| Feature | macOS | Windows | Linux |
|---------|:-----:|:-------:|:-----:|
| Plugin manifest v1 (TOML) | ✅ shared core | ✅ shared core | ✅ shared core |
| Dell Driver Update plugin | N/A | ✅ shipped (M3.15) | N/A |
| NVIDIA Driver Update plugin | N/A | N/A | 🟡 scaffolded; functionality lives in `scripts/drivers/` |
| Agent CLIs (Claude / Codex / Gemini / etc.) | 🟡 cross-OS scaffold; unverified | 🟡 same | 🟡 same |
| Plugin signing / verification | ❌ deferred (M6 / FAZA II) | ❌ same | ❌ same |

### A.12 — Validation harness

| Feature | macOS | Windows | Linux |
|---------|:-----:|:-------:|:-----:|
| End-to-end smoke script | ✅ `bin/validate-macos.sh` (41/41 checks) | ✅ `bin/validate-windows.ps1` | ❌ **`bin/validate-linux.sh` missing** |
| CI matrix coverage | ❌ workflow file `.github/workflows/release.yml` deferred | ❌ same | ❌ same |
| Real-hardware sign-off in HANDOFF.md | ✅ Mac.r12.home, every Sesja since 20 | ✅ DP5520WMK, Sesja 12 + 45 | 🟡 mk-uP5520 — last validated against legacy bash; Python adapter shim unverified |

### A.13 — Dev-sync overlay

| Feature | macOS | Windows | Linux |
|---------|:-----:|:-------:|:-----:|
| `dev-sync` Python core | ✅ shared | ✅ shared | ✅ shared |
| Per-OS wrapper scripts | 🟡 `dev-sync-overlay-migrate.sh` | ✅ multiple `.ps1` wrappers | 🟡 bash equivalents partial |
| rclone integration (Proton Drive) | ✅ | ✅ Sesja 19 hardening (PATH refresh + parenthesised `Join-Path`) | ✅ |
| `.claude/worktrees/` hard-exclude | ✅ Sesja 19 fix in shared core | ✅ same | ✅ same |

---

## B. Known gaps per platform

### B.1 — macOS (most mature)

- **No programmatic snapshot create.** APFS auto-management;
  `tmutil snapshot` deprecated for user code. Workaround: operator
  runs `tmutil localsnapshot` manually before bulk apply. Pre-apply
  banner / hook is on M5.x backlog.
- **No service install path.** Dashboard runs on-demand via desktop
  app or `python3 -m ascendo dashboard --background`. A launchd
  LaunchAgent for "always-on dashboard" parallel to the scheduler
  agents would close this gap (~50 LOC).
- **No `.dmg` distribution.** `tauri build` produces a `.app` bundle;
  Apple Developer ID code signing + notarization deferred to M6.
- **Tauri builds occasionally panic on the DMG bundler.** Mitigated
  in Sesja 34 by splitting `--bundles app` and `--bundles dmg`
  passes; the DMG step is allowed to fail.
- **Parallel apply across categories not implemented.** Sequential
  per-category remains; brew + mas + npm could run in parallel
  (softwareupdate must stay sequential because of reboot semantics).
  Requires lock coordination at the manager layer.

### B.2 — Windows (feature-complete MVP)

- **No npm / pip managers in adapter.** Python global tooling on
  Windows usually goes through `nvm-windows` / `pyenv-win`; not all
  installs share a tracked-set. Could be added (~150 LOC each)
  mirroring the macOS shape.
- **No firmware / NVIDIA driver path outside Dell DCU.** Operators
  on non-Dell hardware (HP, Lenovo, custom builds) have no
  Ascendo-mediated driver update flow. Proper LVFS-equivalent on
  Windows is `Windows Update` itself for most hardware; Dell DCU
  plugin handles the rest.
- **VSS restore not exposed.** `WindowsSnapshot.restore()` deferred
  (`vssadmin revert` + UAC). Currently only `create()` and `list()`
  are wired.
- **Service install requires UAC.** No shipped headless / unattended
  install path for the service (would need a Group Policy Object /
  MDM profile in enterprise contexts).
- **MSI installer signing.** Authenticode signing certificate not
  yet acquired — current MSI / NSIS .exe trigger SmartScreen on
  first run. Deferred to M6.

### B.3 — Linux/Ubuntu (Tier-1 scaffold; functional via legacy bash)

The Ubuntu adapter is a **Python scaffold over the mature legacy bash
scripts**. Everything that works, works through `update-all.sh` and
`scripts/<cat>/<phase>.sh`. The Python `IPackageManager` subclasses
shell out to those scripts via env-var IPC. This means:

- ✅ All 7 sources have full 5-phase coverage via legacy bash.
- ✅ Inventory enumeration, dashboard SPA, Run Center, SSE all work.
- 🟡 Real-hardware end-to-end test of the Python `UbuntuAdapter`
  shim on mk-uP5520 is **pending** (Sesja 45 was static-analysis
  only; Sesja 46 owes operator validation).
- ❌ **No `IScheduler` Python impl.** `python3 -m ascendo schedule
  install` falls through to the placeholder. Operators must use
  `bash scripts/scheduler/install.sh` directly. ~100 LOC to wire
  (mirror `LaunchdScheduler` shape against systemd `--user` timer
  syntax).
- ❌ **No `ISnapshot` Python impl.** `python3 -m ascendo snapshot`
  has no Linux backend. Operators must use `update-all.sh
  --snapshot` or `bash scripts/snapshot/create.sh`. ~80 LOC to wire
  (timeshift / etckeeper detection mirroring the bash logic).
- ❌ **No `IElevation` Python impl.** Bash-side already has
  `_ascendo_sudo` + askpass helper at `$XDG_RUNTIME_DIR/ascendo/`,
  but the dashboard's `POST /elevation/auth` round-trip isn't
  delegated to a Python `LinuxElevation` shim. Apply phases that
  need sudo work via the bash-side cache, but the dashboard's
  "sudo authenticated" footer pill may be inaccurate. ~80 LOC.
- ❌ **No `bin/validate-linux.sh`.** macOS and Windows have full
  end-to-end validation harnesses; Linux has only `update-all.sh
  --profile quick` for read-only smoke. ~150 LOC to wire.
- 🟡 **`.deb` packaging exists but not validated.** `packaging/deb/`
  has metadata + scripts; final package never built + smoke-tested
  on a clean Ubuntu VM.
- 🟡 **Tauri Linux build untested.** Build prerequisites documented
  (`libwebkit2gtk-4.1-dev` etc.); actual `npm run tauri build` on
  Ubuntu hasn't been signed off in HANDOFF.md.
- 🟡 **`drivers` apply requires reboot for kernel module reload.**
  No special handling beyond exit code 75 — operator must reboot
  manually. fwupd offline-update flow could be wired to use
  `fwupdmgr update --reboot` semantics.

---

## C. Roadmap to parity (suggested next moves)

Listed by impact. Effort estimates assume a single well-scoped
session with subagents.

### C.1 — Linux operator validation (highest priority)

- **Run `update-all.sh --profile full --dry-run` on mk-uP5520** with
  the new Python adapter loaded; confirm sidecars land correctly,
  Categories tab populates, Run Center streams. ~30 min operator
  time. **Owner:** operator. **Blocker for:** declaring the Linux
  adapter "real" rather than "scaffold".

### C.2 — Linux scheduler / snapshot / elevation Python impls

- **`UbuntuScheduler(IScheduler)`** — mirror `LaunchdScheduler`
  shape; spawn `systemctl --user` for install/list/trigger/remove.
  Reuse the `WindowsScheduler` DSL parser. ~3 hours.
- **`TimeshiftSnapshot(ISnapshot)`** — provider-detection (timeshift
  preferred, etckeeper fallback); wraps the existing bash. Read-only
  `list` is trivial; `create` shells to `scripts/snapshot/create.sh`.
  ~2 hours.
- **`LinuxElevation(IElevation)`** — bridge to existing askpass
  helper; expose `POST /elevation/auth` round-trip to the
  dashboard. Argv-only contract from spec. ~3 hours.
- **`UbuntuAdapter.capabilities`** flips to `PACKAGE_MANAGEMENT |
  INVENTORY | SNAPSHOTS | SCHEDULING | ELEVATION` — full Tier-1
  parity with macOS / Windows.

### C.3 — `bin/validate-linux.sh`

- Mirror `validate-macos.sh` shape: doctor + 5 phases × 7 categories
  + dashboard endpoint smoke + scheduler round-trip. ~2 hours.

### C.4 — Linux Tauri build sign-off

- One-time validation pass on Ubuntu 24.04: `bin/launch-desktop.sh
  --build` produces working `.deb` + `.AppImage`; icon renders in
  GNOME Activities; daemon stays alive across window close. ~1
  hour operator time.

### C.5 — Windows: npm + pip managers

- **`NpmManager` / `PipManager`** mirroring macOS shape. Useful for
  power-user devs on Windows who track Python / Node CLI versions.
  ~150 LOC + 30 tests each. Lower priority — most Windows users
  manage these via per-version managers (nvm-windows, pyenv-win)
  rather than centrally.

### C.6 — macOS `.dmg` + code signing (M6)

- Apple Developer ID acquisition + notarization workflow. Single
  biggest blocker for "shareable Mac install". Cost: $99/yr +
  ~6 hours infrastructure work.

### C.7 — Cross-cutting CI matrix

- `.github/workflows/release.yml` triggers `validate-{macos,windows,
  linux}.sh` on push to `main`; matrix builds + uploads release
  artifacts on tag. Currently deferred — operator runs validate
  scripts manually before tagging.

---

## D. `ascendo doctor` reference output (per platform)

What the operator should expect to see on a fresh, fully-wired
install. Useful for diff-against-reality during triage.

### D.1 — macOS (12 components, all `ok`)

```
adapter:           macos (macOS) tier=1
capabilities:      PACKAGE_MANAGEMENT | ELEVATION | INVENTORY | SNAPSHOTS | SCHEDULING
brew               ok: Homebrew 5.1.9
jq                 ok: jq-1.8.1
mas                ok: 7.0.0
system_profiler    ok: 1.3
softwareupdate     ok: 1.0
tmutil             ok: 0.0
launchctl          ok: bootstrap 7.0.0
npm                ok: 10.9.0
pip                ok: pip 24.3.1
web                ok: 24 apps registered
bash               ok: GNU bash, version 3.2.57
ascendo_lib        ok: 12 module(s)
ascendo_scripts    ok
```

### D.2 — Windows (5 components today; 7 with npm/pip future)

```
adapter:           windows (Windows) tier=1
capabilities:      PACKAGE_MANAGEMENT | INVENTORY | SNAPSHOTS | SCHEDULING | ELEVATION
winget             ok: v1.28.240
pwsh               ok: 7.6.1
pswindowsupdate    ok: 2.2.1.5
ascendo_lib        ok: 3 module(s)
ascendo_scripts    ok
```

### D.3 — Linux/Ubuntu (10 components; some `unavailable` is expected)

```
adapter:           ubuntu (Ubuntu / Debian) tier=1
capabilities:      PACKAGE_MANAGEMENT | INVENTORY
                   (SNAPSHOTS / SCHEDULING / ELEVATION pending — see §B.3)
apt                ok: apt 2.7.14
snap               ok: snapd 2.66
brew               ok: Homebrew 4.4.x          # only if Linuxbrew installed
npm                ok: 10.9.0                  # only if Node installed
pip                ok: pip 24.x                # always (system Python)
flatpak            ok: 1.14.x                  # only if installed
fwupd              ok: 1.9.x                   # firmware updater
bash               ok: GNU bash, version 5.2.x
ascendo_lib        ok: 12 module(s)
ascendo_scripts    ok
```

`unavailable` rows for `brew` / `flatpak` / `fwupd` are normal — those
are optional managers. `apt` + `bash` + `ascendo_lib` + `ascendo_scripts`
must all be `ok` for the adapter to be functional.

---

## E. Sesja 46 — cross-platform parity quick wins (this session)

Focused parity audit + targeted ports. macOS reference untouched;
Linux + Windows reach closer to the macOS Sesja 34/45 stderr-tail and
Sesja 30 SSE-stream patterns.

### E.1 — Closed in this session

| Gap | Pre-session state | Post-session state |
|-----|-------------------|--------------------|
| Linux apply scripts swallowed stderr — operator saw bare exit codes | `run_silent_as_user` captured combined output to `LOG_FILE` only; sidecar messages had no error context | New `run_capture` / `run_capture_as_user` + `_stderr_tail` helpers in `lib/json.sh` + `lib/common.sh`; npm / brew / apt / pip / flatpak / snap apply.sh now surface last 12 non-empty lines (≤1500 chars) into `json_add_diag` on failure |
| Linux apply scripts had no SSE-stream support | `ASCENDO_STREAM_LOG` env var ignored — Run Center showed no live progress when the orchestrator pointed at a Linux adapter | `_stream_tee` / `_stream_emit` / `_stream_progress` helpers added to `lib/json.sh` (mirror of macOS `adapters/macos/lib/ascendo_json.sh`); apt / brew / npm / snap / pip / flatpak apply.sh now mirror their output to `$ASCENDO_STREAM_LOG` when set, no-op otherwise |
| Windows msstore / arp / windows_update apply scripts had no SSE-stream support | only `winget/apply.ps1` honored `$env:ASCENDO_STREAM_LOG` (Sesja 45) | New `Write-AscendoStreamLine` / `Write-AscendoStreamFile` helpers in `AscendoJson.psm1`; the 3 remaining apply scripts now emit `>>> ...` markers and mirror tempfile-captured stdout/stderr to the stream log |

### E.2 — Files modified

- `lib/json.sh` — +120 LOC of stream/capture helpers (`_stream_tee`,
  `_stream_emit`, `_stream_progress`, `_stderr_tail`, `run_capture`,
  `run_silent_with_tail`)
- `lib/common.sh` — +20 LOC: `run_capture_as_user` (mirror of
  `run_silent_as_user` routing through `run_capture`)
- `scripts/npm/apply.sh` — 3 mutation paths upgraded:
  `npm update -g`, `npm install -g <pkg>`, `npm install -g <pkg>@latest`.
  Each now emits a stream marker, captures combined output, and emits
  a sidecar diag carrying the last 12 lines on failure.
- `scripts/brew/apply.sh` — 3 paths upgraded: `brew update`,
  `brew upgrade --formula`, `brew upgrade --cask --greedy`.
- `scripts/apt/apply.sh` — `apt-get upgrade` pipeline now tees through
  `_stream_tee` for SSE; `apt-get dist-upgrade` captures + emits
  `APT-DIST-UPGRADE-FAIL` diag on failure with last-12-lines tail.
- `scripts/snap/apply.sh` — refresh output mirrored to
  `$ASCENDO_STREAM_LOG` (best-effort); failure diag now carries last
  12 lines instead of just the first `error:` grep.
- `scripts/pip/apply.sh` — `pip install --upgrade` per-package path
  captures combined output + tees to `LOG_FILE` + `$ASCENDO_STREAM_LOG`;
  failure emits `PIP-UPGRADE-FAIL` diag with last-12-lines tail.
- `scripts/flatpak/apply.sh` — flatpak update output mirrored to SSE;
  failure diag now carries last 12 lines.
- `adapters/windows/lib/AscendoJson.psm1` — +60 LOC:
  `Write-AscendoStreamLine` (line marker emitter) +
  `Write-AscendoStreamFile` (mirror an existing tempfile to the
  stream log). Both export-listed.
- `adapters/windows/scripts/msstore/apply.ps1` — invocation marker
  emitted before `Start-Process winget`; stdout + stderr tempfiles
  mirrored to stream log after the process completes (before delete).
- `adapters/windows/scripts/arp/apply.ps1` — same pattern for
  per-id `cmd /c <UninstallString>` invocations.
- `adapters/windows/scripts/windows_update/apply.ps1` —
  `>>> Install-WindowsUpdateBatch starting` marker + exception /
  stderr-tail messages mirrored to stream log on failure paths.

### E.3 — Verification

- `bash -n` clean on all 8 modified shell scripts.
- 7-test bash smoke (stream emit / tee / no-op / capture round-trip /
  exit-code propagation / stderr-tail / stream mirror): **7/7 PASS**.
- `pytest adapters/ubuntu/tests/` — **36 passed, 2 skipped** (no regression).
- `pytest adapters/windows/tests/` — **99 passed** (no regression).
- `pytest adapters/macos/tests/` — **393 passed** (untouched, no
  regression).
- `pytest tests/contract/` — 289 passed, 10 failed = same baseline
  (9 pre-existing `test_service_endpoints` + 1 in another file
  per CLAUDE.md; nothing introduced this session).

PowerShell scripts validated visually only; pwsh not available on
this Mac sandbox so no AST parse run.

### E.4 — Open gaps (sized)

**1–3 hour items** (next agent / next session):

- Wire `LinuxScheduler` (systemd `--user` timer) into `UbuntuAdapter`.
  ~100 LOC mirroring `LaunchdScheduler`. Bash side already exists at
  `scripts/scheduler/install.sh`.
- Wire `LinuxSnapshot` (timeshift / etckeeper detection) into
  `UbuntuAdapter`. ~80 LOC mirroring `TimeMachineSnapshot.list()`
  shape; bash logic in `scripts/snapshot/`.
- Wire `LinuxElevation` Python shim so `/elevation/auth` round-trip
  works for Linux dashboard sessions. ~80 LOC; askpass helper already
  written at `$XDG_RUNTIME_DIR/ascendo/askpass-*.sh`.
- Add `up_to_date` guard to Linux npm / pip per-package paths
  (mirror Sesja 50 macOS pattern). Currently the bash scripts re-run
  `npm install -g <pkg>` blindly; a quick `npm view <pkg> version`
  + comparison would skip already-current packages and shave ~1–3 s
  per package.
- Port Linux apply diag tail emission to `apt-get upgrade` (currently
  only `dist-upgrade` does it).

**1-day items**:

- Build `bin/validate-linux.sh` mirroring
  `bin/validate-macos.sh` / `bin/validate-windows.ps1` structure.
  Roughly 30 stages exercising 5-phase × 7 sources + dashboard
  smoke. Operator-side validation pass on mk-uP5520 owed.
- Real-Ubuntu validation of the new Python `UbuntuAdapter` shim.
- Sesja 45 stderr-tail audit on Windows scheduler.ps1 + snapshot.ps1
  (less critical than apply paths, but completes the parity story).

**Multi-day items**:

- Linux Tauri build sign-off (`.deb` package + `tauri build` on real
  Ubuntu host). Tracked in §C.4 of this doc.
- Windows npm / pip managers (~150 LOC each, mirror macOS shape).
  Most Windows users go through `nvm-windows` / `pyenv-win` directly
  so this is lower priority than other gaps.
- Pre-apply snapshot integration on macOS (APFS auto-managed; needs
  Apple-side API). Documented in §B.1.

### E.5 — Confidence

- **Linux stream/stderr helpers (`lib/json.sh` additions)**: HIGH —
  bash smoke test passes 7/7, semantics mirror macOS exactly,
  no-op fallback when env var unset, additions are append-only
  (no existing helper signatures changed).
- **Linux apply script ports**: HIGH for npm / pip / flatpak /
  brew / snap (small focused diffs, all `bash -n` clean). MEDIUM
  for apt — the `apt-get upgrade` pipeline change inserts
  `_stream_tee` between two existing pipe stages and PIPESTATUS[0]
  semantics preserved; verified by inspection that the fourth pipe
  stage doesn't shift apt's exit code.
- **Windows AscendoJson.psm1 helpers**: MEDIUM-HIGH — pure additive
  exports, no regression risk; not unit-tested due to no pwsh on
  this host. Operator validation expected during the next Sesja.
- **Windows apply script ports** (msstore / arp / windows_update):
  MEDIUM — only insertion of `Write-AscendoStreamLine` /
  `Write-AscendoStreamFile` calls, no logic changes; could fail at
  runtime if module load fails, in which case the calls would
  throw and be caught by the existing top-level `try/catch`.
  Mitigated by the `Add-Content -ErrorAction SilentlyContinue` in
  the helpers themselves.
