# Session 17 handoff — real-hardware bugfix wave from rc2 → rc3

> Written 2026-05-03 by Claude Opus 4.7 (1M context).
> Supersedes [`2026-05-03-session-16-handoff.md`](2026-05-03-session-16-handoff.md).
> Read this first when resuming.

---

## TL;DR

Session 16 shipped rc2 thinking everything worked because TestClient
smoke was green. Resident operator tested rc2 on real Win11 hardware
and reported a fresh wave of bugs (apps menu still empty, run-center
detail invisible, categories versions blank, theme has no auto, etc).
Root-caused 6 distinct issues, all real, none cache. Fixed every one,
shipped as **v0.0.7-rc3** (commit `84e0580`).

**The blocking root cause**: pip's editable install of `ascendo` was
still pointed at a deleted worktree path (`...agent-a5e47d44f63314b9d/core`).
The user's running dashboard was loading code from THAT stale path,
not the primary `D:/Dev_Env/Ascendo`. So all of session-16's fixes
were physically present in main but not running. **First thing
session-18 does**: confirm `pip show ascendo | findstr Editable`
points at `D:\Dev_Env\Ascendo\core`. If not, re-run
`pip install -e D:/Dev_Env/Ascendo/core --no-deps --force-reinstall`.

---

## The 6 bugs fixed this session

| # | Symptom | Root cause | Fix |
|---|---|---|---|
| 1 | Inventory shows installed=— / candidate=— even after running check | Sidecars are written FLAT under `<run-id>/check__<source>.json`, but my overlay was hunting nested `<run-id>/<source>/check__<source>.json`. Real layout overlay matched 0 items → inventory rows stayed empty | `_latest_check_overlay` now checks BOTH layouts, also keys by id AND name (sidecars carry id like `RARLab.WinRAR`; inventory carries display name `WinRAR 7.20 (64-bit)`); strips literal `"Unknown"` versions |
| 2 | Run Center "detail panel" never appears | Subagent's IIFE called `$$("run-detail-panel")` (querySelectorAll on a TAG name — returns empty Array — `.classList.add` is undefined) instead of `$("#run-detail-panel")` (id selector). 10 occurrences — wholesale | Bulk-replaced all 10 instances with the correct id-selector form |
| 3 | Theme switcher missing 'auto/system' option (only sun + moon, no monitor icon); wizard 'Auto (match Windows)' radio doesn't apply | TWO storage keys in flight: wizard wrote `ascendo_theme`, topbar wrote `ui-theme`, pre-paint script read `ui-theme` AND coerced "auto" → "dark". So even when wizard wrote auto, next paint reverted to dark | Unified on `ui-theme` (with `ascendo_theme` fallback for installs upgraded mid-session-17). Pre-paint script now resolves "auto" via `prefers-color-scheme` matchMedia. `applyThemePref` kept as alias around `applyTheme` |
| 4 | Categories `+add` and `Remove` buttons silently no-op | Both POSTed to legacy `/apps/add` and `/apps/remove` stub endpoints (return `{ok:true, stub:true}` and do nothing) instead of the new `/apps/include` and `/apps/exclude` real endpoints | Wired both buttons to the new endpoints; also patched `appsAdd()` shim |
| 5 | PowerShell windows flash whenever the dashboard runs anything | Every `subprocess.run`/`Popen` to pwsh.exe / nssm.exe / vssadmin / schtasks etc. spawns a console child window with no flag to hide it | Subagent (`93504f9`) added `core/ascendo/utils/proc.py::no_window_kwargs()` returning `{"creationflags": CREATE_NO_WINDOW}` on Windows. Patched 12 call sites across 8 manager / adapter / inventory files. 6 unit tests in `tests/python/test_no_window_kwargs.py` |
| 6 | Apps menu shows zero apps (legacy "tracked / detected / missing" pills) | The user's dashboard was running OLD code from a stale pip editable path | Reinstalled pip editables against primary `D:/Dev_Env/Ascendo` |

---

## How I caught the stale-pip-path issue

```
> pip show ascendo
Editable project location: D:\Dev_Env\Ascendo\.claude\worktrees\agent-a5e47d44f63314b9d\core
```

That path was deleted at end of session 16 (worktree removed after
service-subagent merge). pip didn't auto-detect — it kept happily
loading the stale code, which was pre-session-15. So every "I fixed
this" claim from sessions 15 + 16 was true on disk but invisible to
the running process.

**Lesson for tomorrow**: every session that removes a worktree MUST
end with `pip show ascendo | findstr Editable` and a reinstall if it
points anywhere other than `D:/Dev_Env/Ascendo/core`. I'll add this
to the worktree-removal sequence in CLAUDE.md.

---

## Verification (everything green at session-17 close)

```powershell
# Primary editable now correct:
pip show ascendo         | Select-String 'Editable'   # → D:\Dev_Env\Ascendo\core
pip show ascendo-windows | Select-String 'Editable'   # → D:\Dev_Env\Ascendo\adapters\windows

# Tests:
python -m pytest adapters/windows/tests/ plugins/dell-driver-update/tests/ ui/desktop-tauri/tests/
# → 101 passed + 40 subtests
python -m pytest tests/contract/ tests/python/ --rootdir=.
# → 192 passed, 1 skipped (Windows-only assertion correctly skipped)

# Total: 293 + 40 subtests passing (+6 from session 16's 287 — the new
# no_window_kwargs tests).

# Live overlay verification (TestClient):
GET /inventory/winget → 79 items, AutoHotkey shows installed=2.0.24,
   Docker Desktop shows installed=4.71.0, etc.
GET /inventory/summary → winget: ok=75, outdated=3, missing=1, total=79
   (3 outdated: WinRAR 7.20→7.22, Remote Desktop Manager
   2026.1.19→2026.1.20, OpenCode 1.14.31→1.14.33)
```

The 'Available updates' panel will now show real outdated counts — the
user's biggest complaint ("everything is up to date when I know it
isn't") goes away.

---

## What the user MUST do to see the fixes

After the next `git pull`:

```powershell
# 1. Confirm pip points at primary (one-time check):
pip show ascendo         | Select-String 'Editable'
pip show ascendo-windows | Select-String 'Editable'
# Both must read D:\Dev_Env\Ascendo\... (NOT a worktrees path).

# 2. Kill any running python.exe holding the dashboard / sidecar:
Get-Process python | Stop-Process -Force

# 3. Restart the dashboard:
python -m ascendo dashboard --port 8765 --background

# 4. Hard-reload the SPA (Ctrl-Shift-R) — the SPA changed a lot.
# Or open in a fresh InPrivate browser window.

# 5. Walk through:
#    - Apps tab        : see 210 rows with "in config" checkbox per app
#    - Categories tab  : expand winget row, see installed + candidate versions
#    - Theme switcher  : click 3 times → moon → sun → monitor (auto)
#    - Run Center      : kick off a winget check, watch the LIVE detail
#                        panel populate per-package below the sidecar
#    - Help            : "Operator manual for Windows" banner at top
#    - About           : 3 release-notes entries from CHANGELOG
#    - No PowerShell   : zero black flashes on screen changes / runs.
#      windows
```

---

## Commits pushed to `origin/main` (this session)

```
84e0580 fix(spa+inventory): real-hardware bugs from session-17 — overlay layout, $$ selector, theme key, default-include legacy buttons
93504f9 fix: suppress console window on Windows subprocess spawns (subagent)
```

Tag pushed: `v0.0.7-rc3`. HEAD = `84e0580`.

---

## Outstanding for session 18 (none blocking)

1. **Verify rc3 on real hardware.** The user does the 5-step walkthrough
   above; if anything's still wrong, root-cause it from screenshots.
2. **Build a fresh installer with the rc3 dashboard code.** The `.msi`
   and `.exe` artifacts in `dist/` are from rc1 and bundle the OLD
   dashboard. `pwsh -File bin\build-installer.ps1` → fresh artifacts
   → users on the .exe path get the rc3 fixes. 
3. **Decide tag strategy**: bump to rc4 / final / v0.1.0 once user
   confirms rc3 works.
4. **Add the pip-editable-path check to CLAUDE.md** so a future Claude
   session that removes a worktree gets reminded to re-point pip.
5. **Apps in wrong categories** (the user's complaint #5): the actual
   bucketing logic in `adapters/windows/scripts/inventory/list.ps1` is
   correct (per the `Resolve-InventoryCategory` function comments).
   Sidecar inspection showed registry_arp items DO go into registry_arp
   category. If the user still sees something in the "wrong" category
   after rc3, ask them to point at a specific app + category and I'll
   trace it.
6. **WinRAR / Remote Desktop Manager apply was failing in the user's
   last run.** The sidecar showed `apply__winget.json status=failed
   items=1 (apply phase error)`. The actual error message wasn't
   surfaced. With the new run-detail panel + no-window subprocess +
   the diagnostics tail, the next apply attempt should show what
   winget actually said. If apply still fails, root-cause from the
   diagnostics tail.

---

## Worktree cleanup (do this from outside Claude Code)

Three subagent worktrees still on disk after session 17:

```powershell
git worktree remove --force D:\Dev_Env\Ascendo\.claude\worktrees\agent-a5e47d44f63314b9d
git worktree remove --force D:\Dev_Env\Ascendo\.claude\worktrees\agent-a8b3c75472639660a
git worktree remove --force D:\Dev_Env\Ascendo\.claude\worktrees\agent-ac5705e8e77381971
git worktree remove --force D:\Dev_Env\Ascendo\.claude\worktrees\agent-a5c327c290020b507
git branch -D worktree-agent-a5e47d44f63314b9d worktree-agent-a8b3c75472639660a worktree-agent-ac5705e8e77381971 worktree-agent-a5c327c290020b507 2>$null
```

After cleanup, **re-run the pip check**:

```powershell
pip show ascendo | Select-String 'Editable'
# Must still read D:\Dev_Env\Ascendo\core. If it shifted to a removed
# worktree path, reinstall:
pip install -e D:/Dev_Env/Ascendo/core --no-deps --force-reinstall
pip install -e D:/Dev_Env/Ascendo/adapters/windows --no-deps --force-reinstall
```

---

End of handoff. Tomorrow you wake up, the user does the 5-step
walkthrough, and we either confirm rc3 works or root-cause whatever's
still off.
