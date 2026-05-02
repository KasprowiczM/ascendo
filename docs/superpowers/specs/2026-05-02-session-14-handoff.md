# Session 14 handoff — Windows desktop-app bugfix wave + quickstart

> Written 2026-05-02 by Claude Opus 4.7 (1M context).
> Supersedes [`2026-05-02-next-session-handoff.md`](2026-05-02-next-session-handoff.md).
> Read this first when resuming.

---

## TL;DR — where we are

Branch `claude/windows-end-to-end-2026-05-02` (off `restructure/monorepo`),
14 commits, working tree clean. Real-hardware tested on user's
DP5520WMK (Win 11 Pro 26200, PowerShell 7.6.1, winget 1.28.240, Python 3.14).

```
41221b2  fix(windows): unbreak inventory + dashboard runs end-to-end   ← session 14
01010e4  fix(tests+manifest): close 7 pre-existing failures + Dell sidecar enum drift
766e904  docs(handoff): write next-session handoff before usage limit reset
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
…
```

**Confirmed working on user's host:**

- ✅ Tauri 2.x desktop window opens, renders the dashboard inside (the
  one item the previous handoff flagged as un-verified — closed in
  this session by user action).
- ✅ Inventory: 210 packages bucketed across 3 sources
  (winget=79 / msstore=77 / registry_arp=54). The single mash-row
  surfaced before this session is gone.
- ✅ Categories tab shows four rows (winget / msstore / registry_arp /
  windows_update) with real per-source counts and working 5-phase
  buttons (check / plan / apply / verify / cleanup / run all).
- ✅ POST `/runs/async` accepts the new `categories: list[SourceType]` /
  `phases: list[Phase]` shape (was 422'ing on legacy
  `only`/`phase` singular keys).
- ✅ History tab populated — 43 runs returned with full SPA fields
  (`id`, `started_at`, `ended_at`, `status`, `profile`, `dry_run`,
  `summary.phases[]`), sorted newest-first.
- ✅ Run Center renders SSE sidecar events into the per-phase progress
  panel (was empty before because runs never started — root cause was
  the 422).
- ✅ `bin/validate-windows.ps1 -DashboardPort 8768` ALL CHECKS PASSED.

**What is NOT verified yet on real hardware:**

- The user has not yet pulled+restarted to pick up the parser/payload
  fixes in `41221b2`. They were instructed to "kill python.exe, restart
  desktop, Ctrl-Shift-R the SPA" — verify on first resume.
- The branch is **not pushed** (the user explicitly held it for review).

---

## What landed in session 14 (commit `41221b2`)

Six user-reported bugs all root-caused and fixed in a single commit
+ a new operator quickstart guide.

| # | Bug | Root cause | File touched |
|---|-----|-----------|--------------|
| 1 | Inventory: 1 super-row with 200+ package names mashed into one cell | `Read-WingetTabularOutput` ended with `return ,$rows.ToArray()` — leading comma over-wrapped, callers got a 1-element array containing the inner array, PowerShell coerced array→string by space-joining IDs | [adapters/windows/lib/AscendoWinget.psm1](../../../adapters/windows/lib/AscendoWinget.psm1):343 |
| 2 | Categories shows only `winget` row, msstore + ARP missing | `inventory/list.ps1` hard-coded `Category='winget'` for every emitted item — even though winget's output already has a `Source` column and the IDs use `MSIX\` / `ARP\` prefixes | [adapters/windows/scripts/inventory/list.ps1](../../../adapters/windows/scripts/inventory/list.ps1):301-345 (added `Resolve-InventoryCategory`) |
| 3 | `POST /runs/async 422 extra_forbidden` on `only` + `phase` | Legacy SPA sent pre-monorepo singular keys; new `RunRequest` is Pydantic v2 `extra='forbid'` and wants `categories: list` / `phases: list` | [app/frontend/app.js](../../../app/frontend/app.js): per-category buttons, run form, `data-quick` quick-actions (added `normaliseRunBody` translator); `startRunWithSudo` reads both `body.phases` (list) and legacy `body.phase` (string) |
| 4 | History tab empty | Backend `/runs` returned `{run_id, sidecar_count, phases}`; SPA reads `{id, started_at, status, profile, summary, source, dry_run, needs_reboot}` | [core/ascendo/dashboard/schemas.py](../../../core/ascendo/dashboard/schemas.py): `RunListEntry` enriched. [core/ascendo/dashboard/routes/runs.py](../../../core/ascendo/dashboard/routes/runs.py): `_read_run_metadata()` populates fields per-row; mtime-sorted descending |
| 5 | Logs tab dropdown blank | Same root cause as #4 (it reads `r.id` / `r.started_at` / `r.profile` / `r.status`) | Fixed by the same enrichment |
| 6 | Run Center: no detail during run | Downstream of #3 (runs never started → SSE had nothing). Plus `loadRunDetail` couldn't parse the new `/runs/{id}` list-of-sidecars shape | [app/frontend/app.js](../../../app/frontend/app.js):685-720 — synthesises run-level fields when response is an Array; falls through to legacy `{run: {…}}` wrapper otherwise |

**Plus:** [WINDOWS_QUICKSTART.md](../../../WINDOWS_QUICKSTART.md) — single-screen
operator guide. Cross-links to existing [WINDOWS_TESTING.md](../../../WINDOWS_TESTING.md).

---

## Verification (all green at end of session 14)

```powershell
# All pytest under tests/ — same count as pre-session, no regressions
python -m pytest tests/                              # 172 passed

# Dell plugin lint tests
python -m pytest plugins/dell-driver-update/tests/   # 8 passed

# Tauri scaffold tests
python -m pytest ui/desktop-tauri/tests/             # 5 passed

# End-to-end CLI + dashboard real-hardware smoke
.\bin\validate-windows.ps1 -DashboardPort 8768       # ALL CHECKS PASSED

# Direct inventory smoke (post parser fix)
$tmp = New-Item -ItemType Directory ...
pwsh ... inventory/list.ps1 ...
# items count: 210
# winget: 79 · msstore: 77 · registry_arp: 54

# Direct API smoke (post payload fix)
POST /runs/async {categories: ['winget'], phases: ['check'], dry_run: true}  # → 202 + run_id
POST /runs/async {only: 'msstore', phase: 'check'}                           # → 422 (correct rejection)
```

---

## Pre-existing test drift (NOT regressions from this session)

`python -m pytest adapters/windows/tests/` shows **6 failures**.
Confirmed pre-existing via `git stash + rerun`:

- `test_msstore_unavailable_on_non_windows`,
  `test_arp_unavailable_on_non_windows`,
  `test_snapshot_unavailable_on_non_windows`,
  `test_scheduler_unavailable_on_non_windows`,
  `test_elevation_denies_on_non_windows`
  — all fail with `AttributeError: type object 'OperatingSystem' has no attribute 'LINUX'`.
  The enum is `OperatingSystem.LINUX_UBUNTU`; the tests reference an
  alias that never existed.

- `test_adapter_package_managers_includes_windows_update` — asserts
  `len(package_managers) == 2`; the adapter actually returns 4
  (winget, msstore, registry_arp, windows_update) per
  `adapter.py:74-80`. Stale assertion from the M3.8 era.

These were **green-by-omission** in earlier sessions: the
"test commands that pass" list in HANDOFF.md only ran specific test
files, never the entire `adapters/windows/tests/` directory. Worth
fixing in a small follow-up but not blocking.

---

## What to do FIRST when you resume

### 1. Verify the worktree is intact

```powershell
cd D:\Dev_Env\Ascendo\.claude\worktrees\unruffled-shamir-7d473c
git status                                     # should be clean
git log --oneline -3                           # 41221b2 should be HEAD
pip show ascendo         | Select-String 'Editable'   # path = this worktree
pip show ascendo-windows | Select-String 'Editable'   # path = this worktree
```

### 2. Validate the user has actually picked up the fixes

The user pulled the worktree but had the desktop app running with the
OLD parser + OLD SPA in memory. They were instructed to:

```
1. Close the desktop app window
2. Kill any orphan python.exe (the FastAPI sidecar holds old PowerShell parser code)
3. Re-launch via Desktop shortcut OR `bin\launch-desktop.ps1`
4. Hard-reload the browser tab (Ctrl-Shift-R) so the SPA picks up new app.js
5. Click Categories → expect four rows: winget / msstore / registry_arp / windows_update
```

If they confirmed it working — great. If not, the most likely failure
mode is browser cache: they'll see the same single mash row even though
the backend is fixed. Check `pip show` editable paths and force a hard
reload.

### 3. Outstanding follow-ups (in rough priority order, none blocking)

1. **Push the branch** — `git push -u origin claude/windows-end-to-end-2026-05-02`.
   Held back at user's request. Tag suggestion: `v0.0.7-rc2` (the
   previous session created `v0.0.7-alpha` locally but didn't push).

2. **Fix the 6 pre-existing `adapters/windows/tests/` failures.**
   Mechanical: search/replace `OperatingSystem.LINUX` →
   `OperatingSystem.LINUX_UBUNTU`, update `assert len(...) == 2` →
   `>= 2` or `== 4`.

3. **Light-theme contrast pass** (carried from previous handoff,
   ~4-6h). The `--accent-fg` alias mitigates lime-on-paper but a manual
   WCAG AA audit on every accent surface in light mode would close the
   loop.

4. **Move `app/frontend/` → `ui/frontend/`** (PLAN.md M4, ~1-2 days).
   Currently `app/frontend/` is mounted by `core/ascendo/dashboard/app.py`
   directly; the move requires updating the mount path and any contract
   tests that reference the path. Deferred — works fine where it is.

5. **Real-hardware Tauri build** (`bin/launch-desktop.ps1 -Build`).
   Toolchain is in place; producing a packaged `.exe + .msi` in
   `target/release/bundle/` is one command + ~10 min. Useful as a
   sanity check before M4 distribution work.

6. **Inventory: enumerate Windows Update items.** The
   `windows_update` category renders with `total: 0` until the user
   clicks "check" on that row (which runs PSWindowsUpdate). For a
   nicer first-launch UX, `inventory/list.ps1` could call
   `Get-WUList -MicrosoftUpdate` and add KB items to the sidecar with
   `category='windows_update'`. Risk: PSWindowsUpdate calls are slow
   (~30-60s) — would need to gate behind a flag or cache aggressively.

7. **macOS adapter port.** User explicitly mentioned "I would like
   then handoff and start working on my macbook." The Linux adapter
   is at `adapters/ubuntu/`; Windows at `adapters/windows/`; macOS
   stub exists at `adapters/macos/` but needs M3-equivalent build-out
   (Brew formula + cask manager, MAS manager, time-machine snapshot
   manager, launchd scheduler).

---

## Architectural notes worth keeping (additions to previous handoff)

These are session-14 discoveries; the session-13 list still applies.

8. **PowerShell `,$arr.ToArray()` over-wraps when the caller uses `@(...)`**.
   The leading comma is the standard idiom for "wrap as a 1-element
   array to prevent enumeration", but combined with `@(callee)`, the
   collection becomes `[<inner-array>]` instead of just `<inner-array>`.
   Bit us in `Read-WingetTabularOutput`. When you want to preserve
   array shape from a function and the caller uses `@(...)`, just
   `return $arr.ToArray()` — `@()` collects the pipeline output
   correctly. (Commit `41221b2`.)

9. **PowerShell array-to-string coercion uses single space**. So
   `[string]@('a','b','c')` is `'a b c'`. This is why the mashed-row
   surfaced as a space-joined accumulation, not as e.g. `[a, b, c]`
   or `Object[]`.

10. **Pydantic `_WireBase` with `extra='forbid'` is strict on POST
    body**. Adding fields to a response model is safe (clients ignore
    unknown fields), but adding a field to a request model that the
    SPA might already be sending requires either renaming the field
    or accepting `extra='ignore'`. We chose to fix the SPA (translation
    layer) rather than relax the schema. (Commit `41221b2`.)

11. **The legacy SPA's contract is implicit**. `loadHistory`,
    `loadLogsList`, `loadRunDetail` all read field names that have no
    corresponding Pydantic model on the new backend. The fix is to
    populate those fields server-side as additive enrichment so the
    SPA doesn't need to be rewritten. Where the shape mismatch is
    deeper (e.g. `/runs/{id}` returns `list` not `{run: {…}}`), the
    SPA absorbs the new shape via a small adapter at the entry point.

---

## Commands that are confirmed working (paste-ready)

```powershell
# Worktree verification
cd D:\Dev_Env\Ascendo\.claude\worktrees\unruffled-shamir-7d473c
git log --oneline -3                                # 41221b2 HEAD

# Inventory direct (verifies parser + classification fixes)
$tmp = New-Item -ItemType Directory -Path (Join-Path $env:TEMP "asc-inv-$([guid]::NewGuid())") -Force
pwsh -NoProfile -NonInteractive -ExecutionPolicy Bypass `
    -File "adapters\windows\scripts\inventory\list.ps1" `
    -RunId ([guid]::NewGuid()) -Trigger plugin -Profile inventory `
    -OutputDir $tmp.FullName 2>&1 | Out-Null
$j = (Get-ChildItem $tmp -Recurse -Filter "*.json" | Select-Object -First 1) |
    Get-Content -Raw | ConvertFrom-Json
$j.items | Group-Object -Property category | ForEach-Object { "  $($_.Name): $($_.Count)" }
# Expected: winget: 79 · msstore: 77 · registry_arp: 54

# Dashboard sanity (no real apply)
$job = Start-Job -ScriptBlock { cd D:\Dev_Env\Ascendo\.claude\worktrees\unruffled-shamir-7d473c; python -m ascendo dashboard --port 8767 }
Start-Sleep 6
Invoke-RestMethod 'http://127.0.0.1:8767/categories'         | ConvertTo-Json -Depth 3   # 4 sources
Invoke-RestMethod 'http://127.0.0.1:8767/inventory/summary'  | ConvertTo-Json -Depth 3   # totals + per-cat
Invoke-RestMethod 'http://127.0.0.1:8767/runs?limit=3'       | ConvertTo-Json -Depth 4   # 43 runs, enriched
Stop-Job $job; Remove-Job $job -Force

# Full end-to-end smoke
.\bin\validate-windows.ps1 -DashboardPort 8768       # ALL CHECKS PASSED expected
```

---

## Open questions for the next session (only if user asks)

- Should we rename the user-facing branch `claude/windows-end-to-end-2026-05-02`
  to something less Claude-marked (e.g. `feature/windows-mvp`) before
  merging into `restructure/monorepo`?
- Push as-is or squash the 14 commits into a single
  `feat: Windows end-to-end MVP (CLI + dashboard + Tauri desktop)` for
  cleaner history?
- The `ascendo-windows` PyPI metadata says version `0.0.1-dev` —
  bump to `0.0.7` before pushing the tag?

---

End of handoff. Branch is clean, working tree is clean, push and tag
when ready.
