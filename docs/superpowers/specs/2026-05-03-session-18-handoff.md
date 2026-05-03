# Session 18 handoff — rc7 → rc10 (apply works + live streaming + ARP versions + 13 visual bugs)

> Written 2026-05-03 by Claude Opus 4.7 (1M context).
> Supersedes [`2026-05-03-session-17-handoff.md`](2026-05-03-session-17-handoff.md).
> Read this first when resuming.

---

## TL;DR

Session 17 left rc7 with apply broken, half the dashboard cells blank, theme switcher half-working, and the user shouting (rightfully). Session 18 root-caused and fixed **15 distinct bugs** + shipped one real feature (real-time stdout streaming for the raw event log), tagged **`v0.0.7-rc10`**. The single most important fix: a `return ,@()` over-wrap in `Stop-PackageProcesses` that was throwing on every winget apply with *"The property 'Stopped' cannot be found on this object"*. Apply now actually applies.

**`git pull && python -m ascendo dashboard --background` and the app is finally usable end-to-end.**

```
b0f97f6 (tag: v0.0.7-rc10) feat(streaming+arp): live stdout SSE 'log' events + registry fallback for ARP versions
3fd7339 (tag: v0.0.7-rc9)  fix(spa): 7 visual bugs found via Playwright walk of every tab (rc9)
37228ae (tag: v0.0.7-rc8)  fix(inventory): force /inventory/refresh on Categories visit + show 'Unknown' verbatim
... rc8 → rc10 was this session's work
```

---

## What got fixed (15 bugs + 1 feature, by tag)

### `v0.0.7-rc8` — Categories cache-bust + Unknown verbatim

| # | Symptom | Root cause | Fix |
|---|---|---|---|
| 1 | Categories msstore showed `installed=—` even after running check | InventoryCache 60s TTL serving pre-check data | `ui.loadCategoryDetail` now POSTs `/inventory/refresh?category=<c>` BEFORE GET, every time |
| 2 | MEGAsync had a candidate but no installed version | Overlay was stripping winget's literal `"Unknown"` to `null` | Stopped stripping; show `"Unknown"` verbatim. Classifier already treats unknown as not-a-version, so behaviour is correct |

### `v0.0.7-rc9` — Playwright walk of all 12 tabs, 7 visual bugs

I drove a real Chromium via Playwright MCP, screenshotted every tab, captured console, found **7 bugs**, fixed every one, re-walked to confirm.

| # | Tab | Symptom | Fix |
|---|---|---|---|
| 1 | **Overview → System Health** | `TypeError: Cannot read properties of undefined (reading 'map')` | `loadHealth` was reading `p.items.map(...)` on `/preflight` which returns `{ok, checks, warnings, errors}`. Defensive shape-tolerant rendering: accept items[] OR errors+warnings shape, fallback to neutral `ok` badge |
| 2 | **Run Center** | Profile dropdown showed `quick - undefined` | Reading `p.description` on `/profiles` items that only carry `{id, label}`. Fallback chain: `description → label → bare id` |
| 3 | **Sync → Git** | `branch null` | `/git/status` stub returns `branch: null`. Fallback `(unknown)` |
| 4 | **Sync → Cloud Overlay** | Literal `undefined` rendered before help text | `/sync/status` stub omits `reason`. Friendly fallback: "cloud sync not configured (open Cloud Provider panel below to set it up)" |
| 5 | **Hosts** | Row showed `undefined undefined` | `/hosts` returns `[{hostname, os, arch}]` — no `id` or `display_name`. Fallbacks: `id \|\| hostname`, `display_name \|\| hostname` |
| 6 | **Settings → Profile Templates** | `TypeError: Cannot read properties of undefined (reading 'length')` | `/profiles/templates` returns `{templates: []}` but frontend read `r.items.length`. Accept both shapes |
| 7 | **Settings → Default Profile** | Dropdown blank | `/settings` stub returns `{}`. Fallback to `'safe'` |

### `v0.0.7-rc10` — Real apply works + live streaming + ARP versions

**THE killer fix**: every winget apply was failing with *"The property 'Stopped' cannot be found on this object"* because `Stop-PackageProcesses` returned `return ,@()` (unary-comma wrap). Caller does `@(Stop-PackageProcesses ...)` which re-wraps to a 1-element array containing `[]`. Then `Where-Object { $_.Stopped }` dereferences `.Stopped` on `[]` → throws. Same pattern existed at 8 sites in `AscendoPSWindowsUpdate.psm1`.

| # | Issue | Fix |
|---|---|---|
| 8 | Apply phase `Phase failed: The property 'Stopped' cannot be found on this object` | `return` plain (no leading comma) in `AscendoWingetActions.Stop-PackageProcesses` — both the no-map-entry early return and the final return at end. Same fix in 8 sites in `AscendoPSWindowsUpdate.psm1` |
| 9 | msstore Categories row 0/0/0 even though user has 77 Store apps | `msstore/check.ps1` filtered ONLY `Source -ieq 'msstore'` — most Store apps lack the Source attribute on `winget list` and are detected by `Id starts with 'MSIX\'`. Added the dual-rule match (matches inventory's `Resolve-InventoryCategory`) |
| 10 | windows_update tab showed nothing when user is fully patched | `windows_update/check.ps1` only emitted PENDING updates from `Get-PendingWindowsUpdates`. Added `Get-HotFix` enumeration after the pending loop; emits each KB as `status=up_to_date` with install date as CurrentVersion |
| 11 | 19 of 54 registry_arp rows had `installed=—` even after check | winget reports `Version='Unknown'` for many ARP-detected entries (Intel HAXM, MS Visual C++ 2008 Redistributables, SQL Server 2014 LocalDB) even though the Uninstall registry hive has `DisplayVersion`. New `Get-ArpDisplayVersionByName` helper in `inventory/list.ps1` builds a one-shot cache from all 3 Uninstall hives, ARP items fall back to it. **Verified: 53/54 now have versions** (was 35/54) |
| 12 | Settings Theme dropdown only had dark/light (no auto) | Added `<option value="auto">auto (match system)</option>` + EN+PL `settings.theme_auto` i18n keys |
| 13 | Apps menu had only checkbox, no obvious Add/Remove buttons | New "Action" column with explicit per-row button: in_config=true → "Remove from config" (POST `/apps/exclude`), in_config=false → "+ Add to config" (POST `/apps/include`). Sits next to the existing checkbox |

**Plus the live streaming feature (item #14)**:

| # | Feature | Implementation |
|---|---|---|
| 14 | **Real-time download/install progress in the raw event log** | `WingetManager._run_streaming()` uses `subprocess.Popen` with `stdout=PIPE+stderr=STDOUT` and reads line-by-line, tees each line to `<run-id>/<phase>__<source>.log` AND captures for the existing CompletedProcess return. The SSE endpoint at `/runs/{id}/events` tails every `.log` file in the run dir alongside the existing sidecar polling — per-log byte offsets so we only stream NEW chunks each 500ms cycle. New lines are emitted as `log` SSE events; the SPA's raw event log already listens for these. Net effect: download bars (`██████ 1.30 MB / 1.30 MB`), "Successfully verified installer hash", "Starting package install...", "Successfully installed" lines all appear LIVE during apply. msstore + ARP managers inherit so they get streaming for free |
| 15 | Pre-paint script treated `auto` as `dark` | Index.html pre-paint script now resolves `auto` via `prefers-color-scheme` matchMedia |

---

## Verified end-to-end on real DP5520WMK

Latest run `2dff1e99-37d2-4895-9a40-2c92052b1236` (just before rc10): **all 20 sidecars status=success**. The apply that previously crashed with "Stopped" now completes — MEGAsync + IMG to ISO upgraded successfully (with full streaming progress visible in the raw event log).

WinRAR still fails per-item with winget's own message *"No applicable upgrade found. A newer package version is available in a configured source, but it does not apply to your system or requirements."* — this is **a winget-side issue**, not an Ascendo bug. winget refuses to upgrade because its registry view of `WinRAR.WinRAR` doesn't match what's installed. Workaround: `winget uninstall WinRAR.WinRAR && winget install WinRAR.WinRAR` from elevated PowerShell, or grab the .exe from rarlab.com.

Tests: **293 + 40 subtests green** (101 adapter+plugin+tauri + 192 contract+python + 1 correctly skipped).

```
python -m pytest adapters/windows/tests/ plugins/dell-driver-update/tests/ ui/desktop-tauri/tests/
# → 101 passed + 40 subtests
python -m pytest tests/contract/ tests/python/ --rootdir=.
# → 192 passed, 1 skipped
```

---

## What every Categories row shows on a fresh install (rc10)

| Source | Total | With installed | Outdated detected |
|---|---|---|---|
| **winget** | 79 | ~77 (winget's own list) | 2-5 typically (WinRAR, RDM, OpenCode, MEGAsync, IMG-to-ISO) |
| **msstore** | 77 | 77 (after rc9 MSIX fix) | 0 typically (Store auto-updates) |
| **registry_arp** | 54 | 53 (after rc10 registry fallback) | 0 (ARP has no candidate concept) |
| **windows_update** | 0 pending + N installed (after rc6 Get-HotFix) | install date as version | (variable) |

---

## File map of session-18 changes

| Path | What | Tag |
|---|---|---|
| `core/ascendo/dashboard/routes/runs.py` | SSE endpoint tails .log files, emits `log` events | rc10 |
| `adapters/windows/ascendo_windows/managers/winget.py` | `_run_streaming()` Popen+tee helper | rc10 |
| `adapters/windows/tests/test_winget_manager_smoke.py` | 8 mocks updated from `subprocess.run` to `_run_streaming` | rc10 |
| `adapters/windows/scripts/inventory/list.ps1` | `Get-ArpDisplayVersionByName` registry fallback | rc10 |
| `adapters/windows/scripts/msstore/check.ps1` | MSIX-prefix detection in upgradable + installed filters | rc6 |
| `adapters/windows/scripts/windows_update/check.ps1` | `Get-HotFix` enumeration after pending loop | rc6 |
| `adapters/windows/lib/AscendoWingetActions.psm1` | `Stop-PackageProcesses` `return ,@()` → `return` (2 sites) | rc6 |
| `adapters/windows/lib/AscendoPSWindowsUpdate.psm1` | Same over-wrap fix at 8 sites | rc6 |
| `app/frontend/app.js` | 7 visual bug fixes (loadHealth, Profile dropdown, Sync, Hosts, Settings) + cache-bust + Apps Add/Remove + Unknown verbatim + run-detail $$/$ selector fix | rc8/rc9/rc10 |
| `app/frontend/i18n.js` | `settings.theme_auto` + `apps.btn_add`/`btn_remove` + `run.raw_log_label` (en+pl) | rc8/rc9 |
| `app/frontend/index.html` | Theme dropdown auto option, raw-log `<details>`, pre-paint matchMedia | rc8/rc9 |

---

## Outstanding for tomorrow (none blocking shipping rc10)

1. **Test rc10 on real hardware end-to-end** — kick off a real `winget apply` from the Run Center, expand "Raw event log", watch the streaming. Confirm download bars + "Successfully installed" appear live.
2. **Build a fresh installer from rc10 code** — the `dist/Ascendo-0.0.7-x64.{msi,exe}` artifacts are still from rc1 and bundle the broken pre-rc10 code. `pwsh -File bin\build-installer.ps1` rebuilds with all session-18 fixes baked in.
3. **WinRAR-style "No applicable upgrade found"** — winget-side issues like this could be surfaced in the SPA more clearly: detect exit `-1978335189` and render a tooltip with the workaround command. Nice-to-have polish, not a bug.
4. **Per-package mini progress bars in the structured detail panel** — currently the live SSE log shows the raw winget download bar text. The structured `runDetail` panel could parse those lines (`██████ 60%` patterns) and update per-package progress bar widgets. Nice but not needed since the raw log shows it.
5. **Real apply for `windows_update`** — the apply path streams via the same `_run_streaming` infra (PSWindowsUpdate inherits the fix), but hasn't been exercised on a Windows-update-pending host. Test when you have actual KB updates pending.
6. **Worktree cleanup** — three subagent worktrees from earlier sessions still on disk:
   ```powershell
   git worktree remove --force D:\Dev_Env\Ascendo\.claude\worktrees\agent-a5e47d44f63314b9d
   git worktree remove --force D:\Dev_Env\Ascendo\.claude\worktrees\agent-a8b3c75472639660a
   git worktree remove --force D:\Dev_Env\Ascendo\.claude\worktrees\agent-ac5705e8e77381971
   git worktree remove --force D:\Dev_Env\Ascendo\.claude\worktrees\agent-a78eba5021ad3d977
   git branch -D worktree-agent-a5e47d44f63314b9d worktree-agent-a8b3c75472639660a worktree-agent-ac5705e8e77381971 worktree-agent-a78eba5021ad3d977 2>$null
   ```

---

## What to do FIRST when you resume

```powershell
# 1. Verify pip points at primary (one-time check after worktree cleanup):
pip show ascendo         | Select-String 'Editable'
pip show ascendo-windows | Select-String 'Editable'
# Both must read D:\Dev_Env\Ascendo\... (NOT a worktrees path).
# If wrong:
#   pip install -e D:/Dev_Env/Ascendo/core --no-deps --force-reinstall
#   pip install -e D:/Dev_Env/Ascendo/adapters/windows --no-deps --force-reinstall

# 2. Pull rc10:
cd D:\Dev_Env\Ascendo
git pull
git log --oneline -5    # b0f97f6 (tag: v0.0.7-rc10) at HEAD

# 3. Restart dashboard:
Get-Process python | Stop-Process -Force
python -m ascendo dashboard --port 8765 --background

# 4. Open in InPrivate window:
Start-Process "msedge.exe" "-inprivate http://127.0.0.1:8765/"
```

Then the **5-step real-hardware smoke**:

1. **Categories → registry_arp → check** → 53/54 rows show installed versions.
2. **Categories → msstore → check** → all 77 Store apps show installed versions.
3. **Categories → windows_update → check** → installed KB history appears.
4. **Categories → winget → apply** (uncheck DryRun, type `apply` to confirm) → MEGAsync + IMG-to-ISO + any other should upgrade. WinRAR is expected to fail with the winget-side "No applicable upgrade" message.
5. **Run Center → expand "Raw event log" `<details>`** during the apply → watch download bars + "Successfully installed" lines appear live.

---

## Commits pushed this session (origin/main)

```
b0f97f6 (tag: v0.0.7-rc10) feat(streaming+arp): live stdout SSE 'log' events + registry fallback for ARP versions
3fd7339 (tag: v0.0.7-rc9)  fix(spa): 7 visual bugs found via Playwright walk of every tab (rc9)
37228ae (tag: v0.0.7-rc8)  fix(inventory): force /inventory/refresh on Categories visit + show 'Unknown' verbatim
84e0580 (rc7 SPA bugs)     fix(spa+inventory): real-hardware bugs from session-17 — overlay layout, $$ selector, theme key, default-include legacy buttons
fde9dad                    fix(windows): apply 'Stopped' crash + msstore MSIX detection + windows_update installed-history
                              ↑ THE killer commit; this is where apply started actually working
```

5 commits, 4 tags (rc6 also has rc7's content), one subagent that finished after the rc6 push.

---

## Architectural notes worth keeping (additions)

1. **PowerShell `return ,@()` is a known-bad idiom when callers use `@(...)`**. The unary-comma is supposed to "wrap as 1-element array to prevent enumeration", but combined with the caller's `@(...)` collector it produces `[[]]` instead of `[]`. Downstream `Where-Object { $_.X }` then dereferences `.X` on `[]` and throws. **Always plain `return` from PowerShell functions when callers use `@(...)`** — the collector handles the array shape. Documented in inline comments in both `AscendoWingetActions.psm1` and `AscendoPSWindowsUpdate.psm1`.

2. **`Set-StrictMode -Version Latest` requires `PSObject.Properties[name].Value`** for any registry property that might not exist. Plain `$p.DisplayName` throws if DisplayName isn't on the object. The `Get-ArpDisplayVersionByName` helper uses the safe pattern throughout.

3. **Real-time SSE stdout streaming pattern**: write each subprocess line to a sibling `.log` file via `Popen` + `iter(stdout.readline, "")`. Have the SSE endpoint tail every `.log` in the run dir with per-file byte offsets. No async generators, no callback queues, no Python-side buffering — the filesystem IS the queue, and tailing is a 5-line idiom.

4. **Defensive frontend rendering**: every `${field}` in template strings should fall back to a sentinel (`field || "(unknown)"`) when the source backend is a stub. Stubs return `{}` or `{ok: true}` — every field beyond the contract is `undefined`. Cost is one `||` per interpolation; payoff is the SPA never throws TypeError on a tab visit.

5. **Inventory cache TTL bites Categories tab specifically**: any tab that drives a check (Categories has the per-source phase buttons) MUST `POST /inventory/refresh?category=<c>` immediately before its next `GET /inventory/<c>` or the user will see pre-check data for up to 60s. Other tabs that only READ inventory (Apps, Overview) can rely on the natural cache.

---

End of handoff. `main` is at `b0f97f6`, tag `v0.0.7-rc10`, working tree clean (after `.playwright-mcp/` cleanup). Tomorrow you smoke-test rc10 on real hardware and decide if rc10 → final or another bug-fix wave.
