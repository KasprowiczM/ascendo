# Ascendo on Windows — Quickstart (operator)

One-screen guide: install → open the desktop app → scan installed apps →
update Windows / Store apps / winget apps. Tested on Dell Precision 5520,
Windows 11 Pro 26200, PowerShell 7.6.1, winget 1.28+, Python 3.14.

For deep-dive (CI flow, all flags, every endpoint) see
[`WINDOWS_TESTING.md`](WINDOWS_TESTING.md).

---

## 1 · Install (≈ 3 min, one-time)

**Recommended — one-liner from any PowerShell window:**

```powershell
iwr -useb https://raw.githubusercontent.com/KasprowiczM/ascendo/main/install.ps1 | iex
```

That single command:
- Detects Windows version (refuses < Windows 10 build 17763)
- Detects + auto-installs missing Python 3.12 via winget
- Detects + auto-installs missing git via winget
- Clones to `%LOCALAPPDATA%\Ascendo\src`
- Creates venv at `%LOCALAPPDATA%\Ascendo\venv`
- Installs core + Windows adapter editable
- Adds shim at `%LOCALAPPDATA%\Microsoft\WindowsApps\ascendo.cmd` (on PATH by default)
- Runs `ascendo doctor` self-test; bails loudly on non-zero

Optional flags: `-Profile {cli|web|desktop}`, `-Language {en|pl}`,
`-Reinstall`, `-Verbose`, `-NonInteractive`. Env-var overrides:
`ASCENDO_PROFILE`, `ASCENDO_LANG`, `ASCENDO_HOME`, `ASCENDO_NONINTERACTIVE`.

**To update an existing install:**
```powershell
iwr -useb https://raw.githubusercontent.com/KasprowiczM/ascendo/main/update.ps1 | iex
```
(`git pull --ff-only`, refresh editable installs, restart `AscendoDashboard`
service if installed.)

**Manual / dev path** (if you already cloned the repo):

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

Five package managers ship out of the box on Windows: **winget**,
**msstore**, **npm globals**, **pip globals**, **MSI/registry ARP**.
Plus **Windows Update** for OS patches. Plus a curated **WebManager**
for ~10 third-party apps installed outside winget (Brave, Obsidian,
OBS Studio, Notion, Discord, Slack, Zoom, Cursor, GitHub Desktop,
Brave Nightly) — 4 with real candidate-version probes (`Tier-A`),
6 with builtin "open vendor page" handoffs.

## 2 · Open the web dashboard

Three paths — pick one:

| | Where | What it does |
|-|-------|--------------|
| **A** *(recommended)* | `ascendo web start` (any PowerShell window) | Detached background dashboard with pidfile tracking, **opens browser automatically**. Pair with `ascendo web stop`, `restart`, `status`. Idempotent. |
| **B** | `python -m ascendo dashboard` | Server in the foreground (Ctrl-C to stop); visit `http://127.0.0.1:8765` in your browser. Useful for debugging. |
| **C** *(dev / testing only)* | `.\bin\launch-desktop.ps1` (elevated PS) | Native Tauri 2.x window. Requires Rust + Node. Not part of the public release surface yet — see §11 for SmartScreen reality on unsigned native installers. |

The first time you launch any of these, the **Apps inventory** is
populated by the `inventory/list.ps1` script — count is on the bottom of
the Categories tab once the scan finishes (~10–20 s on a typical box).

## 2.5 · Convenience wrappers (bin/)

Five PowerShell wrappers under `bin/` make daily ops a single click away.
They're thin shells over `python -m ascendo …` with auto-PYTHONPATH
resolution so they work from any cwd. Pin to taskbar / Start Menu / use
right-click → Run with PowerShell.

| Script | Equivalent | Use case |
|--------|------------|----------|
| `bin\ascendo-web-start.ps1`   | `ascendo web start`   | Detached dashboard with pidfile tracking; opens browser |
| `bin\ascendo-web-stop.ps1`    | `ascendo web stop`    | SIGTERM + cleanup pidfile |
| `bin\ascendo-web-restart.ps1` | `ascendo web restart` | Stop + start cycle |
| `bin\ascendo-web-status.ps1`  | `ascendo web status`  | Pid + URL + health snapshot |
| `bin\build-inventory.ps1`     | `ascendo build-inventory` | One-shot enumeration across every package source on the box; flushes `~/.ascendo/inventory.db` so the SPA paints instantly |

Sample output:

```
==> ascendo web start
ascendo web started (pid=14328) on http://127.0.0.1:8765/
```

All five are idempotent: starting an already-running dashboard is a
no-op (logs current pid); stopping a stopped one prints `not running`
and exits 0.

## 3 · See what's installed (Categories tab)

The Categories tab shows one row per source:

| Source | Where the data comes from | What it represents |
|--------|---------------------------|--------------------|
| **winget** | `winget list` (winget-feed apps) | Things you installed via `winget install …` |
| **msstore** | `winget list` (Microsoft Store entries + MSIX bundles) | Microsoft Store / pre-installed Windows apps |
| **npm** | `npm list -g` + `npm view <name> version` | Node.js global CLIs you installed via `npm install -g` |
| **pip** | `pip list` + PyPI JSON API | Python global CLIs in user-site (`--user` installs) |
| **web** | Curated `web_apps.toml` + handlers | Third-party apps installed outside winget/msstore |
| **plugin** (Dell DCU) | `dcu-cli /scan` (Dell hardware only) | Dell driver + BIOS updates via Dell Command Update |
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
| **npm/pip CLIs** | npm/pip → check / apply | No sudo needed; user-scope installs |
| **VS Code / KeePassXC / Notepad++ / AutoHotkey / GitHub CLI / OpenCode / Obsidian / OBS Studio** | web → apply | **Full Tier-A silent install** (Sesja 61, 64): downloads installer, Authenticode-verifies the signing publisher against `expected_publisher`, kills running process, runs silent install (`/qn` MSI or `/VERYSILENT` Inno or `/S` NSIS), reads new DisplayVersion from registry, **and verifies the version actually changed** (Sesja 64 fake-success detection). No browser, no clicks. |
| **Brave / Notion / Discord / Slack / Zoom / Cursor / GitHubDesktop / BraveNightly** | web → check | Real candidate-version probes (Tier-A check, Tier-B apply); when outdated, apply opens the vendor download page so you run the installer manually. Full silent install pending per-app silent-flag validation. |
| **Proton Mail / Proton Drive / rclone / Tuta Mail** | web → check | Real candidate-version probe; apply currently Tier-B trigger-only. Promotion to Tier-A silent install pending validation. |
| **Dell drivers + BIOS** | plugin → check / apply | Dell Command Update CLI. Needs Admin / UAC. Plugin lives at `plugins/dell-driver-update/`. |

After every apply phase, the dashboard invalidates the inventory cache
and a banner appears at the top if a reboot is required.

## 6 · From the CLI (no dashboard needed)

```powershell
python -m ascendo doctor                                    # 10-component health snapshot
python -m ascendo run --category winget        --phase check    # 1 min
python -m ascendo run --category msstore       --phase check
python -m ascendo run --category windows_update --phase check    # ~5 min first time
python -m ascendo run --category windows_update --phase apply    # mutating; needs Admin
python -m ascendo run --category npm --phase check                          # node global CLIs
python -m ascendo run --category pip --phase check                          # python global CLIs
python -m ascendo run --category web --phase check                          # Brave / Obsidian / OBS / Notion
python -m ascendo run --category plugin --phase check                       # Dell driver scan (Admin)
python -m ascendo run --category plugin --phase apply                       # apply Dell driver + BIOS updates (Admin, slow)
python -m ascendo build-inventory                                           # flush DB cache (cross-platform)
python -m ascendo web start  /  web status  /  web stop  /  web restart    # dashboard lifecycle
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

## 6.5 · Health diagnostics

`ascendo doctor` now reports **10 components** on Windows:

| Component         | What it probes |
|-------------------|----------------|
| `winget`          | `winget --version` |
| `pswindowsupdate` | `Get-Module PSWindowsUpdate` |
| `npm`             | `npm --version` |
| `pip`             | `pip --version` |
| `dcu`             | `dcu-cli.exe` on disk (Dell Command Update); `unavailable: not installed` is the normal state on non-Dell hardware |
| `pwsh`            | PowerShell 5.1 / 7.x binary |
| `ascendo_lib`     | Adapter PowerShell modules count |
| `ascendo_scripts` | Per-category script tree present |
| `web_registry`    | `web_apps.toml` parses + app count |
| `inventory_db`    | `~/.ascendo/inventory.db` reachable + row count |

Each row's status starts with one of `ok | degraded | unavailable |
error` and includes a one-line hint.

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

## 8 · Run as a Windows service (optional)

Want the dashboard to be always on — no double-click, no sidecar
spawn lag, ready instantly when you open the desktop shortcut?
Install AscendoDashboard as a real Windows service. The service is
NSSM-wrapped (Non-Sucking Service Manager — downloaded automatically
on first install, with SHA-256 verification), runs as
`Automatic (Delayed Start)` so it doesn't slow user login, and
auto-restarts on crash.

Three install paths:

```powershell
# A — from the dashboard (easiest)
# Open Settings → Windows service → Install. UAC prompts once;
# AscendoDashboard registers, starts, and the footer pill flips to
# "service running" within ~5 s.

# B — from the CLI (elevated PowerShell)
.\bin\install-service.ps1 -Action install
.\bin\install-service.ps1 -Action status -Json   # machine-readable
.\bin\install-service.ps1 -Action restart
.\bin\install-service.ps1 -Action uninstall      # idempotent

# C — at install-time (silent)
setx ASCENDO_INSTALL_AS_SERVICE 1
Ascendo-0.0.7-x64-setup.exe                      # NSIS installer picks up the env var
```

After install, the dashboard listens on `127.0.0.1:8765` 24/7. Logs go
to `%LocalAppData%\Ascendo\logs\service\`. To inspect or tweak the
service directly:

```powershell
# Built-in view
sc query AscendoDashboard
# NSSM panel for advanced config (recovery actions, env vars, etc.)
nssm edit AscendoDashboard
```

Uninstalling Ascendo (`Ascendo-0.0.7-x64-setup.exe /S` or via
Add/Remove Programs) automatically removes the service through the
NSIS pre-uninstall hook — no manual cleanup needed. User data in
`%LocalAppData%\Ascendo\` is preserved across re-installs by default.

## 9 · Troubleshooting (the bugs we just shipped fixes for)

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
| npm/pip category shows "unavailable" in doctor | Node / Python not on PATH | `winget install OpenJS.NodeJS.LTS` (npm) or `winget install Python.Python.3.12` (pip) |
| web check returns 0 outdated even though Brave/Obsidian are old | Apps not detected as installed | Registry uninstall key for that app doesn't match the registry TOML's `windows_uninstall_key`. Add an override at `~/.ascendo/web_apps.toml` |
| `>>> still running (Ns)` lines pile up | Working as intended | Watchdog heartbeat to confirm the script hasn't hung during a long winget upgrade |
| Run died and sidecar is missing | Salvage will reconstruct | New in v0.0.8: bufdir-based salvage rebuilds the sidecar from partial JSONL; look for `ASCENDO-SALVAGED` in messages[0] |
| Dell plugin reports "no pending updates" but I know there are some | dcu-cli /scan needs Administrator elevation; non-elevated runs see exit -1 + 0 items by design | Run PowerShell as Administrator and re-run `python -m ascendo run --category plugin --phase check` |
| An app shows `outdated` in Apps/Categories despite being manually upgraded | (Sesja 66 fixed) stale post-apply overlay from a previous `triggered` run | `git pull` + restart dashboard. The `_latest_check_overlay` now only walks same-run apply/verify so an old `triggered` no longer pins the row at the pre-upgrade version |
| `winget list Version=Unknown` package gets re-applied every full run | (Sesja 66 fixed) plan + apply didn't honour Sesja 63's apply-mark | `git pull` so plan.ps1 + apply.ps1 consult `Get-AscendoApplyMark`. Once a successful apply records the target, subsequent runs short-circuit to `up_to_date` |
| History tab doesn't show a way to read the per-run summary | (Sesja 66 added) — each row now has a 📄 link | Click 📄 in the rightmost column to open `REPORT.md` in a new tab |
| Categories tab shows fewer apps than `winget list` / MSStore | (Sesja 67 fixed) pre-v2 DB PK collapsed multi-arch packages sharing a DisplayName | `git pull` + `ascendo web restart`. Schema auto-migrates; next `build-inventory` repopulates with all distinct items (msstore +7, winget keeps 9 separate VC++ 2008 architecture rows) |
| Suggestions tab is empty / has no AI cards | AI provider not configured | Settings → AI → pick provider (anthropic / openai / openrouter / ollama / google / lm_studio) + paste key + pick model. Suggestions reload prepends AI cards on top of rule-based ones. AI failures fall back to rule-based transparently. |
| Want recurring `ascendo run` (e.g. weekly safe update) | (Sesja 67 added) Schedule tab in sidebar | Click **Schedule** → fill `Name` + `Expression` (e.g. `DAILY 03:00`) + `Profile` → Save. Drives Windows Task Scheduler via the adapter. |

## 10 · Where everything lives

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

## 11 · Distribution: one-liner only — MSI / NSIS retired (Sesja 64)

**Public distribution is the `iwr install.ps1 \| iex` one-liner**
(see Section 1). No pre-built `.msi` / `.exe` installers are
published. Retirement is symmetric with the macOS `.dmg` decision
the project made earlier.

### Why no native installers?

| Concern | Reality |
|---------|---------|
| SmartScreen on unsigned `.msi` / `.exe` | Every recipient sees "Windows protected your PC" on first launch, regardless of how many people downloaded successfully before. Bad first impression. |
| Code signing cost | EV Authenticode = $300-700/yr; Azure Trusted Signing = $120/yr (cheapest path). The project hasn't made that investment yet. |
| Feature parity with web | `ascendo web start` opens the SAME SPA at `http://127.0.0.1:8765/` that a Tauri native window would show. The web dashboard and the desktop shell are functionally identical. |
| Update story | `update.ps1` rolls forward cleanly via `git pull --ff-only`. No "drag the new MSI to Program Files" friction. |

### What stays in-repo (and why)

| File / dir | Status | Reason |
|---|---|---|
| `bin/install-service.ps1` | **Active** | NSSM-based 24/7 dashboard service — orthogonal to installer retirement |
| `bin/install-shortcut.ps1` | **Active** | Desktop + Start Menu shortcuts that target the existing CLI install |
| `bin/build-installer.ps1` | **Retained** | Still builds the Tauri `.app` shell bundle locally for contributor testing; no longer produces `.msi` / `.exe` artifacts since Sesja 64 retired those targets |
| `ui/desktop-tauri/` | **Retained** | Tauri 2.x scaffold for contributor dev (`launch-desktop.ps1`); same SPA as the web dashboard inside a native window |
| `packaging/winget-manifest/` | **Retained** | Local-build template; not published to microsoft/winget-pkgs yet |
| `ui/desktop-tauri/src-tauri/tauri.conf.json` | **Edited (Sesja 64)** | `bundle.windows.wix` + `bundle.windows.nsis` sub-tables removed; `bundle.targets` now `["app", "deb", "rpm", "appimage"]` — `.msi`, `.exe`, and `.dmg` excluded |

### Re-enabling later (v0.7+ roadmap)

Once code-signing infrastructure lands (EV Authenticode cert or Azure
Trusted Signing subscription), re-enabling is a config flip:

1. Add `"msi"` + `"nsis"` back to `bundle.targets` in `tauri.conf.json`
2. Re-add the `bundle.windows.wix` + `bundle.windows.nsis` blocks
   (`installer-assets/` BMPs are still in-repo)
3. Run `bin/build-installer.ps1 -CertificatePath ... -CertificatePassword ...`
   (the script already accepts these args; signing is one config
   block away when a cert is available)
4. Create `.github/workflows/release.yml` to auto-publish signed
   builds on tag push

Tracked in [`PLAN.md`](PLAN.md) under "v0.7+ desktop distribution"
and [`docs/DESKTOP_INSTALLER_STATUS.md`](docs/DESKTOP_INSTALLER_STATUS.md).

## 12 · One-liner sanity check

If anything seems off, run this first — exits 0 only when CLI + dashboard
+ all 5 phases × winget produce real sidecars and the SPA assets serve
correctly. As of v0.0.8 the harness also covers stages for npm/pip
managers, the web category check, `ascendo web start/stop` lifecycle,
`build-inventory`, and the new sidecar salvage path:

```powershell
.\bin\validate-windows.ps1            # ≈ 90 s; ALL CHECKS PASSED on green
```

Anything red there will name the failed component (CLI, manager, sidecar
parse, dashboard endpoint, asset) so you know exactly where to start.
