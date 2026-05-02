# Next-session handoff — Ascendo Windows end-to-end

> Written 2026-05-02 by previous session before usage limit reset.
> Read this first when resuming. The full design is at
> `docs/superpowers/specs/2026-05-02-ascendo-windows-end-to-end-design.md`.

---

## TL;DR — where we are

**12 commits** on branch `claude/windows-end-to-end-2026-05-02` (off
`restructure/monorepo`). All 3 sub-projects (A: CLI polish, B: dashboard
+ frontend, C: Tauri 2.x desktop) implemented end-to-end with **45 new
tests, all green**. Real-hardware validated on user's DP5520WMK.

```
838cc29  fix(desktop-tauri): runtime crash — duplicate 'main' window label + 10s health window too tight
ae081c1  fix(desktop-tauri): drop unused [lib] target from Cargo.toml
4213743  fix(launch-desktop): drop --wait from vs_installer modify (unknown option)
e2f10bb  fix(launch-desktop): use vs_installer modify when BuildTools bootstrapper is already present
365371d  fix(desktop+validate): Tauri 2.x schema, MSVC prereq detection, longer poll window
42b099f  fix(windows): four real-run regressions from second smoke pass
4eb3866  fix(windows/scripts): three real-run bugs surfaced by first user smoke
6f1b4bf  docs(handoff): integrate Wave 3 — run-tag-release script, validate-windows extensions, doc updates
de54a1b  feat(dashboard): wire /inventory, /health/check, /runs/active SSE to real adapter
18c5bcf  feat(frontend): apply confirmation modal, per-category 5-phase buttons, self-host fonts, wizard theme step
f97afe8  feat(cli): wire snapshot/schedule subcommands, exit 75 on needs_reboot, runs json command
742d6cc  fix(plugin/dell-driver-update): rewrite 5 PS scripts to StrictMode-safe pattern
30d1167  feat(ui/desktop-tauri): scaffold Tauri 2.x desktop shell with Python sidecar
0ea118f  docs(spec): Windows end-to-end A+B+C design doc
```

**Confirmed working on user's host** (`DP5520WMK`, Win 11 Pro 26200,
Python 3.14, PowerShell 7.6.1, winget 1.28.240):
- ✅ CLI: `python -m ascendo run --category winget --phase {check,plan,apply,verify,cleanup}` all green
- ✅ CLI: `python -m ascendo snapshot list` (returns `no snapshots.`)
- ✅ CLI: `python -m ascendo schedule list` (returns `no schedule entries.`)
- ✅ CLI: `python -m ascendo runs json <real-uuid> --pretty` emits valid `ascendo/run/v1`
- ✅ CLI: `python -m ascendo doctor` — all 5 components healthy
- ✅ Dashboard: `python -m ascendo dashboard` serves real `/categories`, `/inventory`, `/inventory/summary`, `/health/check`, `/runs/active`, `/runs/active/stream` (SSE)
- ✅ Dashboard: SPA modal (`apply-confirm-modal`), self-hosted fonts at `/static/fonts/inter-tight-400.woff2`
- ✅ `bin/run-tag-release.ps1`: full 7-phase flow worked from elevated PowerShell on first try (preflight → snapshot → plan → confirm-gate → apply → verify → cleanup → doctor)
- ✅ `bin/validate-windows.ps1`: 90s poll window passes the async run check; new endpoint smokes all green
- ✅ Tauri 2.x build: cargo metadata clean, MSVC + Rust + Node detected by prereq script, `cargo build` succeeds (3m 37s first time, ~30s warm), 5/5 scaffold tests pass

**What is NOT yet confirmed end-to-end:** the **runtime smoke** of the
Tauri shell. Last attempt panicked on two bugs (now fixed in commit
`838cc29`) — needs one more launch to verify the desktop window actually
opens and renders the dashboard inside.

---

## What to do FIRST when you resume

### 1. Pull latest on the worktree (if anything was pushed)

```powershell
cd D:\Dev_Env\Ascendo\.claude\worktrees\unruffled-shamir-7d473c
git status                              # should be clean
git log --oneline -5                    # confirm 838cc29 is HEAD
```

### 2. Refresh the editable installs (worktree-vs-primary trap)

Both `ascendo` (core) and `ascendo_windows` (adapter) MUST point at the
worktree, not the primary `D:\Dev_Env\Ascendo\` checkout. Verify:

```powershell
pip show ascendo          | Select-String 'Editable'
pip show ascendo_windows  | Select-String 'Editable'
```

Both should show the worktree path. If `ascendo_windows` points at the
primary repo, re-run:

```powershell
cd D:\Dev_Env\Ascendo\.claude\worktrees\unruffled-shamir-7d473c
pip install -e adapters/windows --no-deps
```

### 3. The ONE remaining smoke: `bin/launch-desktop.ps1`

This is the only step that wasn't verified end-to-end before the
session ran out. The two bugs that caused the last panic
(`a webview with label 'main' already exists` + 10s health timeout) are
fixed in `838cc29`. Run:

```powershell
cd D:\Dev_Env\Ascendo\.claude\worktrees\unruffled-shamir-7d473c
.\bin\launch-desktop.ps1
```

Expected behaviour:
- `[prereq] rustc, cargo, MSVC, node all present` (one line)
- `Installing npm deps... up to date` (one line)
- `Launching dev shell (Ctrl+C to stop)...`
- Cargo recompiles only `ascendo-desktop` (~30s — most deps are cached)
- `Finished dev profile target(s) in 30s`
- `Running target\debug\ascendo-desktop.exe`
- (silent — backend spawns and the prereq output stops)
- A native window appears: 1280×800, titled "Ascendo - Unified Updates",
  showing the dashboard (Overview / Categories / Run Center etc.)
- Closing the window kills the Python sidecar cleanly

If anything crashes, the most likely candidates and where to look:

| Symptom | Likely cause | First place to look |
|---|---|---|
| "Failed to setup app: error encountered during setup hook" | webview build error | `ui/desktop-tauri/src-tauri/src/main.rs:113` |
| Window opens but shows "ERR_CONNECTION_REFUSED" | health window still too short on cold cache | `main.rs:94` (currently 60s) — try 120 |
| Window opens blank | URL parse error or wrong port | inspect with F12 inside the window if Tauri devtools enabled, or look at `target\debug\ascendo-desktop.exe` stdout |
| Build error mentions `tauri::generate_context!` | `tauri.conf.json` schema mismatch | check `app.windows = []` is preserved (commit `838cc29`) |
| Window flashes then closes immediately | sidecar spawn failed | wrap `spawn_backend()` so errors print to stderr instead of `expect()` |

### 4. After the desktop smoke, the natural follow-ups

In rough priority order (none of these are blockers; the user can stop here):

1. **Tag and push.** When the user is happy with the desktop smoke:
   ```powershell
   git push -u origin claude/windows-end-to-end-2026-05-02
   git push --tags          # only if user wants v0.0.7-alpha shipped
   ```
   The `bin/run-tag-release.ps1` script created `v0.0.7-alpha` locally on
   the previous user run; if it should move to a different SHA the
   command is `git tag -fa v0.0.7-alpha -m "..."`.

2. **Address the 7 pre-existing test failures.** Carried forward
   untouched from `restructure/monorepo`:
   - 2 in `tests/contract/test_dashboard_spa.py`
     (`test_spa_brand_asset_traversal_blocked`,
     `test_spa_index_pins_dark_theme_by_default`)
   - 5 in `tests/python/test_frontend.py` (missing `client` fixture)

   None of these block the user's flows; they're cleanup work. They were
   broken on `restructure/monorepo` before this branch.

3. **Light-theme contrast pass** (PLAN.md §"Pending Windows polish",
   ~4-6h). The `--accent-fg` alias mitigates lime-on-paper but a manual
   WCAG AA audit on every accent surface in light mode would close the
   loop.

4. **Move `app/frontend/` → `ui/frontend/`** (PLAN.md M4, ~1-2 days).
   Currently `app/frontend/` is mounted by `core/ascendo/dashboard/app.py`
   directly; the move requires updating the mount path and any contract
   tests that reference the path. Deferred from this session — works
   fine where it is.

5. **Fix Dell plugin sidecar filename**. The Dell plugin scripts now
   save as `<phase>__plugin.json` (we changed the source-type enum from
   `dell_driver_update` to the schema-valid `plugin`). If any downstream
   adapter hardcodes `<phase>__dell_driver_update.json`, update it. The
   in-tree code doesn't — but worth a `grep -r dell_driver_update` to
   confirm.

6. **Real-hardware Tauri build** (`bin/launch-desktop.ps1 -Build`). The
   user has the toolchain now; producing a packaged `.exe + .msi` in
   `target/release/bundle/` is just one command and ~10 min. Useful as a
   sanity check before M4 distribution work.

---

## Architectural notes worth keeping

Findings from this session that should NOT be re-discovered:

1. **`Set-StrictMode -Version Latest` + `[Nullable[int]]` parameters**:
   PowerShell auto-unwraps the Nullable to `int-or-$null` AT THE CALLEE.
   So `.HasValue` / `.Value` are not available; check `$null -ne $param`
   instead. Bit us in `AscendoJson.psm1::Add-SidecarItem` (commit
   `42b099f`).

2. **`$h.contains` in PowerShell hashtables** is the
   `IDictionary.Contains()` method, not the value of a key named
   `contains`. Always use bracket indexing `$h['contains']` for keys
   whose names overlap with method names. Bit us in `validate-windows.ps1`
   (commit `42b099f`).

3. **`ConvertTo-Json` on `@()` produces empty string**, which makes
   downstream JSON parsers choke. For "list" actions in PowerShell
   scripts that emit JSON, force the array shape with explicit `[]`
   when empty (commit `4eb3866`).

4. **Tauri 2.x renamed**: `build.devPath` → `build.devUrl`,
   `build.distDir` → `build.frontendDist`, `build.withGlobalTauri` →
   `app.withGlobalTauri`. The agent's scaffold used the 1.x names
   initially (commit `365371d`).

5. **Tauri 2.x `[lib]` is for mobile**. Desktop-only apps don't need it
   and it crashes `cargo metadata` if `src/lib.rs` doesn't exist
   (commit `ae081c1`).

6. **Tauri window labels must not collide** between `app.windows[]` in
   the conf and runtime `WebviewWindowBuilder::new(app, "main", ...)`.
   For dynamic-URL setups, leave conf `windows = []` and build the
   window in setup (commit `838cc29`).

7. **`winget install` won't re-run with `--override` if the package is
   already installed**. To modify an existing Visual Studio install, use
   `vs_installer.exe modify` directly. Note: `--wait` is for the
   bootstrapper, not `modify` (commits `e2f10bb` + `4213743`).

8. **Worktree editable-install trap**: `pip install -e core/` from a
   worktree updates `ascendo` but `ascendo_windows` (separate package)
   keeps pointing at wherever it was first installed. Both must be
   refreshed when switching trees.

---

## Commands that are confirmed working (paste-ready)

```powershell
# CLI smoke
cd D:\Dev_Env\Ascendo\.claude\worktrees\unruffled-shamir-7d473c
python -m ascendo --help
python -m ascendo doctor
python -m ascendo run --category winget --phase check
python -m ascendo runs list -n 5
python -m ascendo runs json <real-uuid> --pretty
python -m ascendo snapshot list
python -m ascendo schedule list

# Dashboard smoke
python -m ascendo dashboard          # then http://127.0.0.1:8765/
.\bin\validate-windows.ps1           # 30s, all PASS

# End-to-end real apply (elevated PS)
.\bin\run-tag-release.ps1            # interactive 'apply' gate
.\bin\run-tag-release.ps1 -WhatIf    # plan only, no mutation
.\bin\run-tag-release.ps1 -NoTag     # apply without git tag

# Desktop (the one remaining smoke — see §3 above)
.\bin\launch-desktop.ps1
.\bin\launch-desktop.ps1 -Build      # produce .exe + .msi
```

## Test runs (confirmed green)

```powershell
$env:PYTHONPATH = "$(Get-Location)\core"
python -m pytest tests/contract/test_cli_polish.py -v        # 5/5
python -m pytest tests/contract/test_dashboard_real.py -v    # 20/20
python -m pytest tests/python/test_frontend_smoke.py -v      # 8/8
python -m unittest discover -s plugins/dell-driver-update/tests -v   # 8/8
python -m unittest discover -s ui/desktop-tauri/tests -v             # 5/5
```

---

## Open questions for the next session (only if user asks)

- Does the user want me to merge this branch into `restructure/monorepo`
  before proceeding to M4 distribution work, or keep it as a tagged
  release branch for now? PLAN.md implies the latter.
- Should `bin/launch-desktop.ps1` ALSO be copied into the primary
  `D:\Dev_Env\Ascendo\bin\` so it works without `cd` to the worktree?
  Probably yes after merge, no before.
- The validate-windows.ps1 SkipDashboard path was never re-tested
  after the 90s poll bump. Low risk; would benefit from a CI run.

---

End of handoff. Branch is clean, working tree is clean, push when ready.
