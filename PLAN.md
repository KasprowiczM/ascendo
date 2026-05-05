# Ascendo — Forward Plan

> Last updated: 2026-05-05 (sesja 28) — macOS adapter M5.5 shipped (launchd IScheduler, **v0.2.0** = full M5 macOS adapter feature-complete). 34/34 PASS via `bin/validate-macos.sh` Stage 12 on Mac.r12.home; tag `v0.2.0` cut locally.
>
> This file is the **single source of truth for what comes next**. HANDOFF.md
> is the historical session log; PLAN.md is the forward roadmap. Update this
> file whenever priorities shift; prune completed items into HANDOFF.md.

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
