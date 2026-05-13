# Ascendo on Windows — Testing Guide

Single-page, copy-paste-ready guide for trying Ascendo end-to-end on a real
Windows machine. Tested on **Dell Precision 5520, Windows 11 Pro Build 26200,
PowerShell 7.6.1, winget v1.28.240, Python 3.14**.

---

## Want a clickable Windows app? (skip the CLI)

```powershell
cd D:\Dev_Env\ascendo
.\bin\install-dev.ps1                # one-time install
.\bin\install-shortcut.ps1           # creates Desktop + Start Menu shortcuts
```

Now **double-click "Ascendo" on your desktop** (or hit ⊞ Win, type "Ascendo",
Enter). The dashboard starts and your browser opens at `http://127.0.0.1:8765/docs`.

Press **Ctrl+C** in the console window to stop.

To remove the shortcuts: `.\bin\install-shortcut.ps1 -Uninstall`.

That's it for the click-to-launch experience. The rest of this document is
the CLI flow + reference docs.

---

## TL;DR — seven commands

```powershell
# 1. Clone (or pull) the repo
cd D:\Dev_Env
git clone https://github.com/KasprowiczM/ascendo.git    # first time only
cd ascendo
git pull                                                  # subsequent times

# 2. One-shot install (core + Windows adapter + dashboard deps + auto-validate)
.\bin\install-dev.ps1

# 3. Flush inventory DB cache so the SPA paints instantly on first launch
python -m ascendo build-inventory

# 4. Read-only validation across all 5 phases + new managers + dashboard
.\bin\validate-windows.ps1            # should print ALL CHECKS PASSED.

# 5. (Optional) Real apply — actually upgrades packages winget reports
.\bin\run-apply.ps1                   # interactive, asks for 'apply' confirmation

# 6. Browser-visible dashboard
python -m ascendo dashboard --port 8765
# open http://127.0.0.1:8765/docs in a browser

# 7. (Optional) Detached dashboard with pidfile + auto-open browser
.\bin\ascendo-web-start.ps1
```

That's it. The rest of this document is reference: prerequisites, what each
step does, how to interpret the output, and how to troubleshoot.

---

## 1. Prerequisites

| Component | Required | Verify |
|---|---|---|
| Python 3.11+ | yes | `python --version` |
| pip | yes | `pip --version` |
| PowerShell 5.1 or 7.x | yes | `pwsh --version` or `powershell --version` |
| winget | yes (Windows-side adapter MVP) | `winget --version` |
| Git | yes | `git --version` |
| Internet access | yes (for pip + winget) | — |

**Anything missing?** Get it from `winget install Python.Python.3.14`,
`winget install Git.Git`, etc.

---

## 2. Install

```powershell
cd D:\Dev_Env\ascendo
.\bin\install-dev.ps1
```

What this script does (in order):

1. `pip install -e .\core\` — installs the `ascendo` core package (editable)
2. `pip install -e .\adapters\windows\ --no-deps` — installs the Windows
   adapter (`--no-deps` to skip pip's PyPI lookup of the core dep, which
   isn't published yet)
3. `pip install pywin32 pywin32-ctypes` — Windows native deps
4. `pip install fastapi 'uvicorn[standard]' httpx` — dashboard runtime
5. Prints `pip show` output for all four packages
6. Auto-runs `bin\validate-windows.ps1` at the end

**Useful options:**

```powershell
.\bin\install-dev.ps1 -SkipValidate   # install only, don't validate
.\bin\install-dev.ps1 -Reinstall      # force re-install all four packages
```

If you see warnings like *"WARNING: The script ascendo.exe is installed in
… which is not on PATH"* — that's expected. Use `python -m ascendo` instead
of bare `ascendo` (we use it everywhere; it sidesteps the PATH issue).

---

## 3. Validate (read-only / dry-run)

```powershell
.\bin\validate-windows.ps1
```

What it checks (in order):

1. `python -m ascendo --help` works (PATH-independent invocation)
2. `python -m ascendo version` → `ascendo 0.0.1-dev`
3. `python -m ascendo doctor` → `windows (Windows) tier=1`,
   `winget ok`, `pwsh ok`, `ascendo_lib ok`
4. **All 5 phases** of the contract on real winget data:
   - `check` — read-only inventory
   - `plan` — read-only enumeration of what would be upgraded
   - `apply --dry-run` — emits `planned` items, **no real mutations**
   - `verify` — re-check vs apply sidecar
   - `cleanup --dry-run` — would-prune emit, no actual deletes
5. **Dashboard smoke** — start in background, hit `/version` + `/health` +
   `POST /runs/async` + poll `GET /runs/{id}/status` until completed,
   stop cleanly

**Expected final line:**

```
ALL CHECKS PASSED.
```

If any step fails, the script prints `[FAIL]` with diagnostic info
(sidecar.status, messages[], stdout/stderr). Paste that output back and
we'll diagnose.

**Useful options:**

```powershell
.\bin\validate-windows.ps1 -DashboardPort 18765   # use a different port
.\bin\validate-windows.ps1 -SkipDashboard         # skip dashboard tests (faster)
```

---

## 4. (Optional) Real apply — first real mutation

```powershell
.\bin\run-apply.ps1
```

This is the **safety harness for the first real upgrade** on your machine.
Steps:

1. **Banner with red warning** — explicit "this WILL upgrade real packages".
2. **Plan phase first** — runs `--phase plan` and prints a table of what
   would change.
3. **Confirmation gate** — interactive prompt:
   ```
   About to upgrade 1 package(s) via winget.
   Type 'apply' to proceed, anything else to abort:
   ```
   You must type the literal string `apply` (exactly). Anything else
   aborts with exit 0 ("No changes made.").
4. **Real apply** — runs `--phase apply` (no `--dry-run`). winget actually
   upgrades the packages. UAC may prompt for elevation on individual
   installers.
5. **Sidecar contents printed** — table of items[] with status / current /
   target / resolved / exit_code, plus messages[] from the script.

**Useful options:**

```powershell
# Upgrade only specific packages (filter by ID):
.\bin\run-apply.ps1 -Packages 'Microsoft.PowerShell','Mozilla.Firefox'

# Skip the interactive prompt (USE WITH CARE — for automation):
.\bin\run-apply.ps1 -IAcceptUpgradeRisk

# Use a different category (currently only 'winget' implemented):
.\bin\run-apply.ps1 -Category winget -Profile safe
```

**Exit codes:**
- 0 = all upgrades succeeded (or nothing to upgrade)
- 1 = partial — some succeeded, some failed (sidecar items[] has the detail)
- 2 = full failure
- (anything else) = script-level error, didn't run apply

---

## 5. Browser-visible dashboard

```powershell
python -m ascendo dashboard --port 8765
```

Then in a browser:

- **`http://127.0.0.1:8765/docs`** — interactive Swagger UI with all endpoints
- **`http://127.0.0.1:8765/version`** — `{"ascendo": "0.0.1-dev", "adapter": "windows", "adapter_tier": 1}`
- **`http://127.0.0.1:8765/health`** — `{"status": "ok", "adapter": "windows", "components": {"winget": "ok: ...", "pwsh": "ok: ...", ...}}`

**Drive a run from the browser** — in the Swagger UI:

1. Expand `POST /runs/async` → "Try it out"
2. Body: `{"phases": ["check"], "categories": ["winget"]}`
3. Click "Execute" — response includes `run_id`, `status_url`, `stream_url`
4. `GET /runs/{run_id}/events` opens an **SSE stream** (Server-Sent Events)
   that emits each new sidecar as the run progresses. Watch the run
   complete in real time.
5. `GET /runs/{run_id}` returns the full parsed sidecars.

**Stop the dashboard:** `Ctrl+C` in the PowerShell window where it's running.

---

## 5b. Apply updates from the dashboard (Wave 2)

The dashboard now drives full apply workflows from the browser — no CLI
needed for everyday operation.

1. Open `http://127.0.0.1:8765/`, navigate to **Categories**.
2. Each category row now has 5 phase buttons: `check / plan / apply /
   verify / cleanup`. Click **plan** first to see what would change.
3. Click **apply** → a modal opens asking you to type the literal string
   `apply` to proceed (anything else aborts). Once confirmed, the run
   starts and the log streams live via SSE.
4. Watch the run progress in real time; when it finishes you'll see
   ✓ Run complete with the per-category status table.
5. Switch to the **History** tab to inspect saved sidecars from past runs.

The same flow works for individual phase buttons (e.g. just `check` or
`verify`). This mirrors `python -m ascendo run --category <c> --phase <p>`
on the CLI — same sidecars on disk under `~/.ascendo/runs/<run-id>/`.

---

## 5c. Launch the desktop app (Wave 4)

```powershell
.\bin\launch-desktop.ps1                # dev mode (Ctrl+C to stop)
.\bin\launch-desktop.ps1 -Build         # produce a packaged .exe + .msi
```

Build prerequisites: Rust (`winget install Rustlang.Rustup`), Node 18+,
WebView2 runtime (preinstalled on Win11), MSVC linker
(`winget install Microsoft.VisualStudio.2022.BuildTools`).

The Tauri 2.x shell spawns the FastAPI backend as a sidecar process and
opens a native WebView2 window pointing at `http://127.0.0.1:8765/` —
same SPA, same REST API, no browser tab.

---

## 5d. End-to-end first apply with snapshot + tag (Wave 3)

`bin/run-tag-release.ps1` is the user's "apply + tag v0.0.7-alpha"
one-liner. **Run from an elevated PowerShell.**

```powershell
.\bin\run-tag-release.ps1               # normal (interactive confirm)
.\bin\run-tag-release.ps1 -WhatIf       # show plan only, no mutation
.\bin\run-tag-release.ps1 -NoSnapshot   # if VSS is full / disabled
.\bin\run-tag-release.ps1 -NoTag        # apply but don't tag
```

What it does, in order:
1. **Preflight** — admin check, repo root, PYTHONPATH wiring (so the
   worktree's `core/` is used, not the editable install).
2. **Snapshot** — `Checkpoint-Computer` VSS restore point (skip with
   `-NoSnapshot`).
3. **Plan** — `python -m ascendo run --category winget --phase plan`.
4. **Confirm gate** — interactive prompt requiring the literal string
   `apply` (skip with `-IAcceptUpgradeRisk` for automation).
5. **Apply** — `python -m ascendo run --category winget --phase apply`.
   Exit code 75 = reboot required.
6. **Verify** — `python -m ascendo run --category winget --phase verify`.
7. **Cleanup** — `python -m ascendo run --category winget --phase cleanup`.
8. **Health card** — `python -m ascendo doctor`.
9. **Tag** — `git tag -a v0.0.7-alpha -m "..."` (skip with `-NoTag`).
   The script does NOT push; run `git push --tags` when ready.

If a reboot is pending, the script prints a clearly-marked banner with
`shutdown /r /t 30` so you can reboot on your own schedule.

---

## 5e. Apply updates via npm / pip / web (Sesja 58)

Three new package managers landed in v0.0.8 to bring Windows parity
with macOS + Ubuntu.

**npm globals.** Run `python -m ascendo run --category npm --phase check`
to enumerate the ~15 globals declared in
`adapters/windows/config/npm_global_clis.txt` (claude-code, codex,
gemini, typescript, eslint, prettier, etc.). Run `--phase plan` to see
what's outdated, then `--phase apply` to update. No sudo / Admin needed
— npm globals install to user-owned `%APPDATA%\npm`. Stderr-tail on
failure surfaces the actual reason (EACCES, registry 404, etc.).

**pip globals.** Same flow for pip globals declared in
`adapters/windows/config/pip_global_clis.txt` (uv, ruff, mypy, pytest,
black, etc.). `pip install --user --upgrade <name>` is the apply
command; lives entirely in user-site. Self-skip rule for the `pip`
package itself when installed via the system Python.

**Web (Tier-A 4 + Tier-B 6).** WebManager v1 ships 10 curated apps in
`adapters/windows/config/web_apps.toml`. 4 have real candidate-version
probes (Brave + Obsidian + Notion + OBS Studio via `github_release` and
`release_feed` handlers); 6 are Tier-B `builtin` (Discord, Slack, Zoom,
Cursor, GitHub Desktop, Brave Nightly) where check reports the
installed version but apply opens the vendor download page. Full
download+install with Authenticode verification + UAC handoff lands in
v0.0.9. Run `--phase check` to see which are outdated.

---

## 5f. Build inventory + cache

```powershell
python -m ascendo build-inventory
```

Outputs a per-source count + flushes everything to
`~/.ascendo/inventory.db`. The SPA reads from this DB so subsequent
Categories tab loads are instant (no live re-scan unless the cache is
older than 24h or you click Refresh). Idempotent — safe to re-run any
time. Honours `ASCENDO_INVENTORY_DB` env var override; pass `--no-db`
for read-only enumeration; pass `--verbose` for per-source trace.

---

## 5h. Dell driver + BIOS updates via DCU

Dell Precision / Latitude / OptiPlex boxes that have **Dell Command
Update** installed get an extra `plugin` category. Ascendo wraps
`dcu-cli.exe` via the `plugins/dell-driver-update/` plugin. CLI:

    python -m ascendo run --category plugin --phase check   # Admin
    python -m ascendo run --category plugin --phase apply   # Admin

dcu-cli requires Administrator elevation for **every** subcommand
(including `/scan`). Non-elevated runs report `items=0` and surface a
"requires elevation" warn-level message. `ascendo doctor` reports the
absolute path to dcu-cli.exe under the `dcu` component row.

Install Dell Command Update via Microsoft Store (search "Dell Command
Update") or `winget install Dell.CommandUpdate`. After install, restart
the dashboard so `health_check()` picks up the new probe target.

---

## 5g. Convenience wrappers under bin/

Five PowerShell shims wrap the `python -m ascendo …` invocations with
auto-PYTHONPATH resolution so they work from any cwd:

```powershell
.\bin\ascendo-web-start.ps1            # detached dashboard + open browser
.\bin\ascendo-web-stop.ps1             # SIGTERM + cleanup pidfile
.\bin\ascendo-web-restart.ps1          # stop + start cycle
.\bin\ascendo-web-status.ps1           # pid + URL + health snapshot
.\bin\build-inventory.ps1              # alias of `ascendo build-inventory`
```

Pin to taskbar / Start Menu / use right-click → Run with PowerShell for
zero-CLI daily ops.

---

## 5h. Schedule recurring runs via the new Schedule tab (Sesja 67)

The SPA gained a dedicated **Schedule** tab in the sidebar (between
Hosts and Settings). It drives Windows Task Scheduler via the
adapter's `IScheduler` implementation.

```powershell
# Smoke-test the backend without the UI:
curl http://127.0.0.1:8765/scheduler/list

# Install a daily safe schedule via the API:
curl -X POST http://127.0.0.1:8765/scheduler/install `
     -H "Content-Type: application/json" `
     -d '{"name":"ascendo-daily","expression":"DAILY 03:00","profile":"safe","enabled":true}'

# Verify in Task Scheduler GUI: Task Scheduler Library → \Ascendo\ → ascendo-daily

# Trigger once now:
curl -X POST http://127.0.0.1:8765/scheduler/trigger `
     -H "Content-Type: application/json" `
     -d '{"name":"ascendo-daily"}'

# Remove:
curl -X POST http://127.0.0.1:8765/scheduler/remove `
     -H "Content-Type: application/json" `
     -d '{"name":"ascendo-daily"}'
```

Expression DSL: `DAILY HH:MM` · `WEEKLY DAY HH:MM` · `MONTHLY HH:MM`
· `HOURLY HH:MM` · `MINUTE N`. Submitting an existing name UPDATES
the schedule in place (no duplicate task IDs in Task Scheduler).

---

## 5i. Suggestions AI integration (Sesja 67)

The `/suggestions/library` endpoint now optionally calls a configured
LLM to augment the rule-based cards. To enable:

1. Open Settings → AI providers → pick a provider (anthropic /
   openai / openrouter / ollama / google / lm_studio).
2. Paste API key (or leave blank for ollama / lm_studio local servers).
3. Click **Test connection** — model list appears.
4. Pick a model → **Save**.
5. Open Suggestions tab. Cards now load with 1-3 AI-generated rows
   ON TOP of the rule-based ones.

Smoke-test the AI path without the UI:

```powershell
# After configuring a provider in Settings → AI:
curl http://127.0.0.1:8765/suggestions/library | jq '.ai, .ai_generated_count, .count'
# Expected: {"provider":"anthropic","model":"claude-3-5-sonnet-...","ok":true,"count":N}
#           ai_generated_count: 1..3
#           total cards count
```

If the provider is offline or rate-limited, Ascendo silently falls
back to rule-based cards (no 500, no panic) — the `ai.ok=false`
flag in the response tells the SPA to show a small "AI off" hint.

---

## 6. What's been validated end-to-end

After steps 1-5, you've exercised every single layer of the 6-layer
architecture on real hardware:

| Layer | Module | Validated? |
|---|---|---|
| 1 — Frontend SPA | (legacy `app/frontend/*` not yet wired to new endpoints) | next milestone |
| 2 — Tauri shell | (legacy `app/tauri/*`) | next milestone |
| 3 — Backend HTTP | `core/ascendo/dashboard/` | ✅ via Swagger UI + validate |
| 4 — Core domain | `core/ascendo/{models,interfaces,orchestrator,cli}/` | ✅ via CLI |
| 5 — Adapter Python | `adapters/windows/ascendo_windows/` | ✅ via `doctor` + `run` |
| 5 — `NpmManager` (Sesja 58) | `adapters/windows/ascendo_windows/managers/npm.py` | ✅ via `npm --phase check/plan/apply` |
| 5 — `PipManager` (Sesja 58) | `adapters/windows/ascendo_windows/managers/pip.py` | ✅ via `pip --phase check/plan/apply` |
| 5 — `WebManager` (Sesja 58) | `adapters/windows/ascendo_windows/managers/web.py` | ✅ via `web --phase check` (4 Tier-A probes live) |
| 5 — `DellDriverManager` (post-Sesja 58) | `adapters/windows/ascendo_windows/managers/dell.py` | ✅ via `plugin --phase check` (Dell DCU; auto-skip on non-Dell hardware) |
| 5 — Sidecar salvage (Sesja 58) | `_BaseWindowsManager._salvage_sidecar` mixin | ✅ via salvage stage in `validate-windows.ps1` |
| 3 — Schedule tab (Sesja 67) | `core/ascendo/dashboard/routes/scheduler_real.py` | ✅ via `GET/POST /scheduler/{list,install,remove,trigger}` |
| 3 — Suggestions AI (Sesja 67) | `core/ascendo/dashboard/routes/ai.py` `call_provider_inference()` + `routes/suggestions.py` augment | ✅ via 6 providers (anthropic / openai / openrouter / ollama / google / lm_studio) |
| 4 — Inventory dedup (Sesja 67) | `core/ascendo/dashboard/inventory_db.py` v2 schema | ✅ multi-arch packages now keep separate rows; PK widened to `(category, name, item_id)` |
| 6 — Native scripts | `adapters/windows/{lib,scripts/winget/}` | ✅ via `run` (real winget) |
| 6 — npm/pip PS scripts (Sesja 58) | `adapters/windows/scripts/{npm,pip}/*.ps1` | ✅ via npm + pip phases |
| 6 — Web handlers (Sesja 58) | `adapters/windows/lib/handlers/{github_release,release_feed,builtin}.psm1` | ✅ via `web --phase check` |
| bin/ wrappers (Sesja 58) | `bin/ascendo-web-{start,stop,restart,status}.ps1` + `build-inventory.ps1` | ✅ via lifecycle stage in validate |

---

## 7. Troubleshooting

### `'ascendo' is not recognized as a name of a cmdlet…`

Your `…\Python\Scripts` directory isn't on PATH. Use `python -m ascendo`
instead — it's always equivalent and PATH-independent. All our scripts use
this form.

### `pip install` complains about `ascendo>=0.0.1`

Old version of `bin/install-dev.ps1`. `git pull` and re-run; the current
script uses `--no-deps` for the adapter install which sidesteps the PyPI
lookup.

### `validate-windows.ps1` prints `[FAIL]` on `doctor`

Almost certainly the Windows adapter isn't installed. Run `pip install -e
.\adapters\windows\ --no-deps` and re-run validate.

### `validate-windows.ps1` prints `[FAIL]` on `run`

Re-run with `git pull` first — recent fixes addressed PowerShell
boolean-binding (DryRun → `[switch]`), AppX/MSIX merged-row parsing,
and string-length caps. If still failing, paste the full sidecar.messages
block from the [FAIL] output.

### Dashboard binds but `/health` returns `status=error`

The adapter is installed but its health check is reporting a degraded
component. Run `python -m ascendo doctor -v` to see component-by-component
status. Common causes: pwsh not on PATH (install
PowerShell 7), winget not on PATH (install winget from the Microsoft
Store), or `ascendo_lib` directory missing (run `git pull`).

### `run-apply.ps1` says "Nothing to upgrade"

Plan returned an empty items[]. winget thinks everything is up to date.
Either run `winget upgrade --all` first to confirm independently, or wait
until winget reports a new upgrade.

---

## 8. Reporting issues

If anything in this document doesn't work as described, paste:

1. The exact command you ran
2. The full output (especially the [FAIL] lines + sidecar.messages if any)
3. Your `python --version` and `pwsh --version`
4. The output of `git log --oneline -3` (so we know which commit you're on)

The architecture is stable; bugs from here on are localized and quick to
diagnose given the diagnostic output the validate script emits.

---

## 9. What's next

Beyond this baseline (v0.0.7-alpha), the roadmap is:

- **v0.0.7-alpha** — Windows MVP feature-complete: real-hardware-validated
  apply on DP5520WMK + frontend apply UX (per-category phase buttons,
  confirm modal, live SSE) + Tauri 2.x scaffold. Tagged via
  `bin/run-tag-release.ps1` from an elevated shell.
- **v0.1.0** — MSI installer (WiX) + winget manifest + GitHub Releases CI +
  Authenticode signing + Tauri 2.x packaged build + frontend physically
  moved to `ui/frontend/`.
- **v0.2.0** — macOS adapter (brew, mas, softwareupdate, launchservices,
  Time Machine, launchd).
- **v1.0** — full 3-OS support (Linux re-integrated), security audit,
  plugin signing + verification, msi/dmg/deb releases.

See `HANDOFF.md` for the per-session work log and the full backlog.
