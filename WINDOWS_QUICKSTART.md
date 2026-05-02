# Ascendo on Windows — Quickstart (operator)

One-screen guide: install → open the desktop app → scan installed apps →
update Windows / Store apps / winget apps. Tested on Dell Precision 5520,
Windows 11 Pro 26200, PowerShell 7.6.1, winget 1.28+, Python 3.14.

For deep-dive (CI flow, all flags, every endpoint) see
[`WINDOWS_TESTING.md`](WINDOWS_TESTING.md).

---

## 1 · Install (≈ 3 min, one-time)

```powershell
cd D:\Dev_Env
git clone https://github.com/KasprowiczM/ascendo.git
cd ascendo
.\bin\install-dev.ps1                  # core + Windows adapter + venv + smoke
.\bin\install-shortcut.ps1             # Desktop + Start menu icons
```

Re-running `install-dev.ps1` after a `git pull` is safe — it's idempotent
and re-installs the editable Python packages so your dashboard always
runs the latest code.

## 2 · Open the desktop app

Three equivalent paths — pick whichever you prefer:

| | Where | What it does |
|-|-------|--------------|
| **A** | Double-click "Ascendo" on the Desktop | Spawns the FastAPI sidecar, opens the SPA in your default browser |
| **B** | `.\bin\launch-desktop.ps1` (elevated PS) | Native Tauri 2.x window — the app in a real OS window, not a browser tab |
| **C** | `python -m ascendo dashboard` | Server only; open `http://127.0.0.1:8765` yourself |

The first time you launch any of these, the **Apps inventory** is
populated by the `inventory/list.ps1` script — count is on the bottom of
the Categories tab once the scan finishes (~10–20 s on a typical box).

## 3 · See what's installed (Categories tab)

The Categories tab shows one row per source:

| Source | Where the data comes from | What it represents |
|--------|---------------------------|--------------------|
| **winget** | `winget list` (winget-feed apps) | Things you installed via `winget install …` |
| **msstore** | `winget list` (Microsoft Store entries + MSIX bundles) | Microsoft Store / pre-installed Windows apps |
| **registry_arp** | `winget list` (Add-or-Remove-Programs detected) | Legacy MSI/EXE installers (Office, drivers, …) |
| **windows_update** | `Get-WUList` via PSWindowsUpdate | OS patches (KB updates, defender defs, security rollups) |

Click a row to expand it and see every package with installed/candidate
version + status pill.

## 4 · Check for updates

Each category row has its own 5 phase buttons:

```
check  →  plan  →  apply  →  verify  →  cleanup
```

You almost always want **`check`** — it's read-only, takes a few seconds,
and surfaces every available update without changing anything. The Run
Center tab pops open showing live progress; sidecars stream in via SSE
as each phase finishes.

After `check`, the row's **Outdated** column updates so you can see at a
glance which sources have updates pending.

| You want to … | Click |
|---------------|-------|
| Find new updates from the **winget** ecosystem | winget → check |
| Find new **Microsoft Store** updates | msstore → check |
| Find new **Windows 11 KB** patches | windows_update → check |
| Find driver / MSI updates from **legacy installers** | registry_arp → check |

## 5 · Apply updates

Click **`apply`** on the source you want to update. Every apply gates on
a confirmation modal — you must type the literal word `apply` to proceed
(this is intentional; it prevents click-throughs from changing system
state by accident).

Apply phases that need elevation (winget, msstore, windows_update) prompt
once for sudo (the dashboard caches the password in memory and forwards
it via `SUDO_ASKPASS` to all child processes — never written to disk,
never logged).

| Update target | What to click | Notes |
|---------------|---------------|-------|
| **winget feed apps** (Chrome, VSCode, …) | winget → apply | Idempotent; safe to re-run |
| **Microsoft Store apps** | msstore → apply | Bound to the user's Store account |
| **Windows 11 itself** | windows_update → apply | Downloads + stages KB patches; reboot required |
| **Drivers via Dell Command Update** | (separate) `python -m ascendo run --category plugin --plugin dell-driver-update --phase apply` — must be Admin | Currently driven from CLI, not UI |

After every apply phase, the dashboard invalidates the inventory cache
and a banner appears at the top if a reboot is required.

## 6 · From the CLI (no dashboard needed)

```powershell
python -m ascendo doctor                                    # 5-component health snapshot
python -m ascendo run --category winget        --phase check    # 1 min
python -m ascendo run --category msstore       --phase check
python -m ascendo run --category windows_update --phase check    # ~5 min first time
python -m ascendo run --category windows_update --phase apply    # mutating; needs Admin
python -m ascendo runs list -n 5                            # last 5 runs
python -m ascendo runs json <run-id> --pretty | jq .summary
python -m ascendo snapshot create -m "before manual upgrade"
python -m ascendo schedule install --weekly                 # nightly Task Scheduler entry
```

Exit codes the run command emits:

| Code | Meaning |
|------|---------|
| `0`  | success |
| `1`  | warnings only (e.g. some upgrades deferred) |
| `2`  | bad input (e.g. unknown category) |
| `30` | hard failure during apply |
| `75` | success, but **reboot required** |

## 7 · Update Windows 11 right now

Most direct path — five buttons in the dashboard:

1. **Categories → windows_update → check** — populates the count of
   pending KBs.
2. Click the row to expand and review the list (KB number, title,
   category, size).
3. **Categories → windows_update → apply** — type `apply` to confirm,
   sudo prompts once, the SSE stream shows each KB downloading then
   installing.
4. When the run finishes, look at the top banner — if it says **reboot
   required**, click the Restart button (5-second delay confirmation).
5. After reboot, click **windows_update → verify** to confirm everything
   landed.

CLI equivalent (single command):

```powershell
# elevated PowerShell 7+
python -m ascendo run --category windows_update --phase apply
```

## 8 · Troubleshooting (the bugs we just shipped fixes for)

| Symptom | Likely cause | Quick check |
|---------|--------------|-------------|
| `POST /runs/async 422 extra_forbidden` | Stale browser tab on old SPA | Hard reload (Ctrl-Shift-R) — frontend now sends `categories: list` + `phases: list` |
| Categories tab shows 1 winget row with all packages mashed into one cell | Pre-fix `AscendoWinget.Read-WingetTabularOutput` over-wrap | `git pull` + restart dashboard — the parser was returning a 1-element collection containing the whole array |
| History tab is empty | Pre-fix `/runs` endpoint missing `started_at`/`status`/`profile` | Same `git pull` — backend now enriches each entry from the first sidecar of each run dir |
| Logs tab dropdown empty / unlabeled | Same as History | Same fix |
| msstore updates exist on the box but Ascendo says 0 | You haven't clicked `msstore → check` yet | Inventory enumerates installed apps, NOT pending updates — `check` is what scans the source feeds |
| Run Center shows nothing during a run | The run never started (probably the 422) — SSE stream had no data to render | Kick a fresh `winget → check` and watch the Run Center light up |
| "no PowerShell binary on PATH" | pwsh.exe missing | `winget install --id Microsoft.PowerShell` |
| `winget` says "v1.28" but `ascendo doctor` says it's missing | Per-user install on a different account | `winget install --id Microsoft.AppInstaller --scope machine` |

## 9 · Where everything lives

```
D:\Dev_Env\ascendo\
├─ adapters\windows\
│  ├─ ascendo_windows\        # Python: WindowsAdapter, WingetManager,
│  │   adapter.py             #   MSStoreManager, ArpManager,
│  │   inventory.py           #   WindowsUpdateManager, WindowsInventory
│  │   managers\…
│  ├─ scripts\                # Per-category PowerShell phase scripts
│  │   winget\{check,plan,apply,verify,cleanup}.ps1
│  │   msstore\…              # Same 5 phases per source
│  │   windows_update\…
│  │   inventory\list.ps1     # The "what's installed" enumerator
│  └─ lib\                    # AscendoJson, AscendoWinget PowerShell modules
├─ core\ascendo\               # OS-agnostic CLI + orchestrator + REST API
│  ├─ cli\…                    # `python -m ascendo …` entry point
│  ├─ dashboard\               # FastAPI app, served at 127.0.0.1:8765
│  └─ orchestrator\            # 5-phase runner + JSON-v1 sidecar IO
├─ ui\desktop-tauri\           # Tauri 2.x native shell (Rust + WebView2)
├─ app\frontend\               # The SPA the desktop shell renders
├─ bin\
│  ├─ install-dev.ps1          # one-shot setup
│  ├─ install-shortcut.ps1     # Desktop + Start menu icons
│  ├─ launch-desktop.ps1       # Tauri dev launch
│  ├─ run-tag-release.ps1      # 7-phase release flow (preflight → tag)
│  └─ validate-windows.ps1     # End-to-end smoke (CLI + dashboard)
└─ logs\runs\<uuid>\           # All sidecars and per-phase logs
```

## 10 · One-liner sanity check

If anything seems off, run this first — exits 0 only when CLI + dashboard
+ all 5 phases × winget produce real sidecars and the SPA assets serve
correctly:

```powershell
.\bin\validate-windows.ps1            # ≈ 90 s; ALL CHECKS PASSED on green
```

Anything red there will name the failed component (CLI, manager, sidecar
parse, dashboard endpoint, asset) so you know exactly where to start.
