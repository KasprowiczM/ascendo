# Ascendo — Forward Plan

> Last updated: 2026-05-01 — Sesja 12 closed, v0.0.7-alpha-rc on real DP5520WMK.
>
> This file is the **single source of truth for what comes next**. HANDOFF.md
> is the historical session log; PLAN.md is the forward roadmap. Update this
> file whenever priorities shift; prune completed items into HANDOFF.md.

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

### `dell-driver-update` plugin scripts
- 5 plugin PowerShell scripts under `plugins/dell-driver-update/windows/` use the
  same broken patterns the in-tree msstore/arp scripts had (inline hashtable
  splat, `Get-WingetVersion` calls, `-Tool` instead of `-ToolName`).
- **Fix pattern (now well-understood):** copy `scripts/winget/check.ps1`
  line-by-line, change `category='dell_driver_update'`, swap winget calls for
  `dcu-cli.exe /scan` / `/applyUpdates`. Same splatting + StrictMode-safe
  property access.
- Effort: 2-3 hours once you have `dcu-cli.exe` installed.

### CLI snapshot/schedule wiring
- `ascendo snapshot create` / `list` / `restore` currently is a placeholder
  (`raise typer.Exit(64)`). M3.12 manager exists; just wire the CLI.
- Same for `ascendo schedule install / uninstall / list / trigger`.
- Effort: ~150 LOC, half a day.

### Light-theme contrast pass
- Manual WCAG AA audit on every accent surface in light mode. `--accent-fg`
  alias already mitigates lime-on-paper, but a few hardcoded colors may still
  leak through.
- Effort: 4-6 hours.

---

## M4 — Distribution (path to v0.1.0-alpha, ~2-3 weeks)

| Item | Effort | Files |
|---|---|---|
| **MSI installer (WiX)** | 3-4 days | `packaging/windows/ascendo.wxs` + `build-msi.ps1`; bundles PyInstaller one-folder + FastAPI runtime + .psm1 modules |
| **winget manifest** | 1 day | PR to `microsoft/winget-pkgs`: `manifests/A/Ascendo/Ascendo/<version>/*.yaml` |
| **GitHub Releases CI** | 2-3 days | `.github/workflows/release.yml`: build + sign + publish on tag |
| **Authenticode signing** | 1 day | toolchain setup (azure trusted-signing or DigiCert); required for SmartScreen |
| **Tauri 2.x shell** | 3-4 days | rebuild `app/tauri/` for Tauri 2.x + FastAPI backend pairing; replace legacy shell |
| **Frontend SPA migration** | 1-2 days | physically move `app/frontend/` → `ui/frontend/`; update mount path in `core/ascendo/dashboard/app.py` |
| **Self-host webfonts** | 2 hours | drop Inter Tight + JetBrains Mono woff2 into `app/frontend/fonts/`; `@font-face` + remove Google Fonts CDN import |

**Tag:** `v0.1.0-alpha` after MSI ships and runs end-to-end through winget install.

---

## M5 — macOS adapter (path to v0.2.0, ~3 weeks)

Mirror `adapters/windows/` as `adapters/macos/`. Same patterns, OS-specific tools:

| Manager | Tool | Est LOC (Python + Bash) |
|---|---|---|
| `managers/brew.py` | Homebrew (formulae + casks) | 150 + 350 |
| `managers/mas.py` | Mac App Store via `mas` CLI | 100 + 200 |
| `managers/softwareupdate.py` | `softwareupdate -l` + `-i -R` | 100 + 150 |
| `managers/launchservices.py` | LaunchServices (ARP-equivalent) | 100 + 200 |
| `managers/snapshot.py` | Time Machine read-only | 80 + 150 |
| `managers/scheduler.py` | launchd | 80 + 200 |
| `managers/elevation.py` | sudo + AuthorizationCreate | 80 + 100 |

**lib:** Bash equivalents of `AscendoJson.psm1` + `AscendoWinget.psm1`. Port from
`Aktualizacje_MAC/` shell logic (already battle-tested).

**Critical rules to preserve from `Aktualizacje_MAC/CLAUDE.md`:**
- `softwareupdate` MUST have `-R` flag.
- `mas upgrade` MUST have `sudo` (CVE-2025-43411).
- Bash 3.2 only (no `declare -A`, `mapfile`, `readarray`).

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

1. **IInventory wired to SPA `/apps`** — M3.11 backend exists, just stub the endpoint to live data. ~150 LOC Python + ~50 JS.
2. **Wizard step for theme picker** — current wizard has 4 steps (lang, profile, schedule, snapshot); add a 5th for dark vs light.
3. **`ascendo runs json <id>`** — emit the consolidated run report as a single JSON blob (useful for piping into `jq`).
4. **Health card on dashboard Overview** — already has the API (`/health/check`), just render.
5. **Reboot detection in CLI** — `python -m ascendo run` exits 0 even if `needs_reboot=true`; add a separate exit code (e.g. 75) and a clear stderr line.

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
