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

## TL;DR — six commands

```powershell
# 1. Clone (or pull) the repo
cd D:\Dev_Env
git clone https://github.com/KasprowiczM/ascendo.git    # first time only
cd ascendo
git pull                                                  # subsequent times

# 2. One-shot install (core + Windows adapter + dashboard deps + auto-validate)
.\bin\install-dev.ps1

# 3. Read-only validation across all 5 phases + dashboard sync + async + SSE
.\bin\validate-windows.ps1            # should print ALL CHECKS PASSED.

# 4. (Optional) Real apply — actually upgrades packages winget reports
.\bin\run-apply.ps1                   # interactive, asks for 'apply' confirmation

# 5. Browser-visible dashboard
python -m ascendo dashboard --port 8765
# open http://127.0.0.1:8765/docs in a browser
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
| 6 — Native scripts | `adapters/windows/{lib,scripts/winget/}` | ✅ via `run` (real winget) |

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

Beyond this baseline (v0.0.1-alpha), the roadmap is:

- **v0.0.2-alpha** — first real apply landed via `run-apply.ps1`.
- **v0.0.3-alpha** — Microsoft Store manager (msstore source — second source,
  proves the pattern replicates).
- **v0.0.4-alpha** — PSWindowsUpdate (OS patches via Windows Update).
- **v0.1.0** — frontend SPA wired up + Tauri desktop app.
- **v1.0** — full 3-OS support (Linux + macOS adapters land), code signing,
  msi/dmg/deb releases.

See `HANDOFF.md` for the per-session work log and the full backlog.
