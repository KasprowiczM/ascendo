# Ascendo — User Guide

A cross-OS, three-interface walkthrough. Pick your interface, follow the
copy-paste recipe.

> Operating systems supported: **macOS** (v0.2.0 — feature-complete),
> **Windows** (v0.0.7 — feature-complete), **Linux/Ubuntu** (legacy code,
> being migrated into `adapters/ubuntu/`).
>
> For OS-specific install + first-run see:
> - [MACOS_QUICKSTART.md](MACOS_QUICKSTART.md)
> - [WINDOWS_QUICKSTART.md](WINDOWS_QUICKSTART.md)
> - [docs/CROSS_PLATFORM.md](docs/CROSS_PLATFORM.md) — details on the
>   shared 5-phase contract + sidecar JSON

---

## What Ascendo does in one paragraph

Ascendo is a **unified-update orchestrator**. You install it once, and
it talks to every package source on your machine through one set of
commands: Homebrew + Mac App Store + macOS softwareupdate (on macOS);
winget + Microsoft Store + Add/Remove Programs + Windows Update (on
Windows); apt + snap + brew + flatpak + npm + pip (on Linux). Every
operation goes through a **5-phase contract** — `check` (read-only
inventory), `plan` (what would change), `apply` (the only mutating
phase), `verify` (post-apply re-check), `cleanup` — and writes a JSON
"receipt" (sidecar) for every change.

Three interfaces, same backend:
- **CLI** — `python3 -m ascendo …` for power users + scripting.
- **Web app** — FastAPI dashboard at `http://127.0.0.1:8765/`. Vanilla
  JS SPA with the Categories tab, Run Center (live progress), History,
  Logs, Sync, Apps inventory, Settings.
- **Desktop app** — Tauri 2.x native window that wraps the same web
  app in a single 1280×800 webview, no browser needed.

---

## 0. First-time install

| OS | Command |
|----|---------|
| macOS | `bash bin/install-dev-macos.sh` |
| Windows (PowerShell) | `.\bin\install-dev.ps1` |
| Linux (Ubuntu) | `pip install -e core/` then `pip install -e adapters/ubuntu/` (manual until M5+ Ubuntu adapter polish lands) |

After install, run `bash bin/validate-macos.sh` (or `.\bin\validate-windows.ps1`)
to confirm everything works. Expected: `ALL CHECKS PASSED. (34/34)` on
macOS, similar on Windows.

---

## 1. CLI walkthrough (the precision tool)

Best for: scripting, CI, headless servers, "run this profile every
night via cron / Task Scheduler / launchd."

### 1a. The 30-second tour

```bash
# Health snapshot (10 components: brew/jq/mas/system_profiler/softwareupdate/
# tmutil/launchctl/bash/ascendo_lib/ascendo_scripts on macOS)
python3 -m ascendo doctor

# What package sources can my adapter talk to?
python3 -m ascendo doctor --verbose

# Read-only check across the brew source
python3 -m ascendo run --category brew --phase check

# What would change if I ran apply?
python3 -m ascendo run --category brew --phase plan

# Apply (mutating)
python3 -m ascendo run --category brew --phase apply

# Re-scan to confirm everything took
python3 -m ascendo run --category brew --phase verify

# Tidy up source caches
python3 -m ascendo run --category brew --phase cleanup
```

Each command writes a JSON sidecar to `~/.ascendo/runs/<run-id>/<phase>__<category>.json`.

### 1b. Profiles (canned multi-category runs)

Profiles bundle multiple categories into one invocation:

```bash
python3 -m ascendo run --profile=quick   # check on every available category (≈15 s)
python3 -m ascendo run --profile=safe    # 5-phase pipeline, but skips drivers
python3 -m ascendo run --profile=full    # everything (drivers gated by manual confirm)
```

### 1c. Browsing prior runs

```bash
python3 -m ascendo runs list -n 10                          # last 10 runs
python3 -m ascendo runs show <run-id>                       # human-readable summary
python3 -m ascendo runs json <run-id> --pretty | jq .       # machine-readable JSON
python3 -m ascendo runs json <run-id> --pretty | jq '.summary'
python3 -m ascendo runs json <run-id> --pretty | jq '.sidecars[] | {phase, category, status}'
```

### 1d. Snapshots (system rollback)

| OS | Backend | What `snapshot create` does |
|----|---------|---------------------------|
| macOS | Time Machine local APFS | `snapshot list` works (read-only); `snapshot create` raises `SnapshotError` because APFS local snapshots are auto-managed by macOS — use System Settings → Time Machine to seed a real backup |
| Windows | Volume Shadow Copy | `snapshot create` runs `Checkpoint-Computer -Description "Ascendo …"` — registers a System Restore point |
| Linux | timeshift / etckeeper / btrfs | (placeholder until `adapters/ubuntu/` polish lands) |

```bash
python3 -m ascendo snapshot list
python3 -m ascendo snapshot create -m "before manual brew upgrade"
python3 -m ascendo snapshot restore <id>      # destructive; confirm prompt
```

### 1e. Scheduler (run automatically on a cron-like schedule)

| OS | Backend | Schedule store |
|----|---------|----------------|
| macOS | launchd LaunchAgent | `~/Library/LaunchAgents/dev.ascendo.<name>.plist` |
| Windows | Task Scheduler | `\Ascendo\<name>` task hierarchy |
| Linux | systemd timer | (placeholder) |

DSL is **identical across OSes**. `python3 -m ascendo schedule install --calendar "<expression>"`:

| `--calendar` form     | When it runs                  |
|-----------------------|-------------------------------|
| `DAILY 03:30`         | every day at 03:30            |
| `WEEKLY MONDAY 06:00` | every Monday at 06:00         |
| `MONTHLY 03:00`       | the 1st of the month at 03:00 |
| `MONTHLY 15 03:00`    | the 15th of the month at 03:00|
| `HOURLY :15`          | every hour at :15 past        |
| `MINUTE 30`           | every 30 minutes              |

```bash
# Install
python3 -m ascendo schedule install --name nightly --calendar "DAILY 03:30" --profile safe

# List
python3 -m ascendo schedule list

# Run NOW (synchronous)
python3 -m ascendo schedule trigger --name nightly

# Remove
python3 -m ascendo schedule remove --name nightly
```

### 1f. Exit codes (for cron / CI gates)

| Code | Meaning |
|------|---------|
| `0`  | success |
| `1`  | warnings only |
| `2`  | bad input (unknown flag / category / etc.) |
| `30` | hard failure during apply (system in known state) |
| `75` | success **but reboot required** (macOS softwareupdate, Windows Update, etc.) |

Bash one-liner that gates a CI step on a successful Ascendo run:

```bash
python3 -m ascendo run --profile=safe || {
    rc=$?
    case $rc in
        75) echo "Reboot required."; exit 0 ;;
        *)  echo "Ascendo failed with exit $rc"; exit $rc ;;
    esac
}
```

---

## 2. Web app walkthrough (the dashboard)

Best for: visual exploration, demos, ad-hoc apply with the safety
modal, watching live progress via SSE.

### 2a. Start it

```bash
python3 -m ascendo dashboard --port 8765
# (in another shell or browser)
open http://127.0.0.1:8765/
```

`Ctrl+C` to stop.

Background mode (returns immediately):

```bash
python3 -m ascendo dashboard --background --port 8765
```

### 2b. The five tabs

The sidebar has five primary tabs:

1. **Overview** — health card (10 component statuses), reboot banner if
   set, quick actions. The health card maps to `GET /health` on the REST
   API.
2. **Categories** — one row per source (brew / mas / softwareupdate on
   macOS; winget / msstore / registry_arp / windows_update on Windows).
   Click a row to expand. Each row has 5 phase buttons.
3. **Run Center** — live SSE progress stream of the current run. Shows
   per-(phase × category) status pills as sidecars stream in.
4. **History** — paginated list of all past runs. Click any row to see
   the parsed sidecars + per-phase logs.
5. **Logs** — newest run highlighted; pick any run-id from the dropdown
   to see its `.log` files.

### 2c. The apply flow (with confirm modal)

1. Click any category row (e.g. `brew`).
2. Click **check** — read-only, ≈ 5–10 s. Run Center pops open.
3. After check completes, click **plan** to see what apply would do.
4. Click **apply**. A modal appears:
   ```
   This will run apply on brew.
   Type 'apply' to confirm:
   [____________]   [Cancel]   [Confirm]
   ```
   Type the literal word `apply` (case-sensitive) and press Confirm.
5. Run Center streams progress live. When done, a banner shows the
   overall result + a "show sidecar" link.
6. (macOS) If apply needs sudo (`mas`, `softwareupdate`), the dashboard
   prompts once for the password — cached in memory only, forwarded to
   subprocesses via `SUDO_ASKPASS`.

### 2d. REST API endpoints (for integration)

| Endpoint | What it does |
|---|---|
| `GET /version` | adapter + ascendo version |
| `GET /health` | 10-component status dict |
| `GET /inventory` | installed apps (cached 60 s) |
| `GET /inventory/summary` | per-source counts |
| `GET /inventory/category/<source>` | drill into one source |
| `POST /runs/async` | start a run, returns 202 + `{run_id, stream_url, status_url}` |
| `GET /runs/<id>/status` | poll lifecycle (pending/running/completed/failed) |
| `GET /runs/<id>/events` | **SSE stream** of `status` / `sidecar` / `done` events |
| `GET /runs` | list run-ids on disk |
| `GET /runs/<id>` | parsed sidecars for one run |
| `POST /elevation/auth` | (macOS) supply sudo password, get 200/401 |
| `GET /elevation/status` | (macOS) is the sudo cache populated? |

Full Swagger UI at `http://127.0.0.1:8765/docs`.

---

## 3. Desktop app walkthrough (Tauri 2.x)

Best for: daily use without a browser tab, production-style icon in
your dock / Start menu / task switcher.

### 3a. Dev mode (compiles in 1-3 min on first run)

| OS | Command |
|----|---------|
| macOS | `bash bin/launch-desktop-macos.sh` |
| Windows | `.\bin\launch-desktop.ps1` |

Prerequisites (one-time):

- **Rust** (1.78+) — install with `curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh` (macOS/Linux) or `winget install Rustlang.Rustup` (Windows).
- **Node 18+** — `brew install node` (macOS) or `winget install OpenJS.NodeJS.LTS` (Windows).
- **macOS Apple CLI tools** — `xcode-select --install` (one-time).
- **Windows MSVC build tools** — `winget install Microsoft.VisualStudio.2022.BuildTools` (with C++ workload).
- **Windows WebView2 runtime** — preinstalled on Win 11; on Win 10 install from Microsoft.

What dev mode does:

1. `npm install` (one-time, ≈ 30 s).
2. Cargo compiles `src-tauri/` (≈ 5–10 min on first run, < 30 s after).
3. The shell binary spawns `python3 -m ascendo dashboard --port <ephemeral>`
   as a sidecar process.
4. Polls `/health` for up to 10 s.
5. Opens a 1280×800 native webview window pointing at the dashboard.
6. On close: kills the sidecar.

### 3b. Production build

| OS | Command | Output |
|----|---------|--------|
| macOS | `bash bin/launch-desktop-macos.sh --build` | `ui/desktop-tauri/src-tauri/target/release/bundle/macos/Ascendo.app` + `…/dmg/Ascendo_<v>_aarch64.dmg` |
| Windows | `.\bin\launch-desktop.ps1 -Build` | `ui/desktop-tauri/src-tauri/target/release/bundle/{msi,nsis}/` |

The build is **not code-signed yet** (M6 work). On macOS, Gatekeeper
will refuse to open the `.app` unless you right-click → Open the first
time, or `xattr -dr com.apple.quarantine Ascendo.app`. On Windows,
SmartScreen will warn "Unknown publisher" — click "More info" → "Run anyway".

### 3c. Run as a service / agent (background daemon)

| OS | How |
|----|-----|
| macOS | Use the launchd scheduler instead — it's the macOS-native way. See [MACOS_QUICKSTART.md §8](MACOS_QUICKSTART.md). |
| Windows | `.\bin\install-service.ps1 -Action install` registers AscendoDashboard as a real Windows service via NSSM. See [WINDOWS_QUICKSTART.md §8](WINDOWS_QUICKSTART.md). |

---

## 4. Side-by-side: same outcome, three interfaces

To upgrade Homebrew packages on macOS, all three of these do the same thing:

```bash
# CLI
python3 -m ascendo run --category brew --phase apply

# Web app
# Open http://127.0.0.1:8765/ → Categories → brew → click "apply" → type "apply" in modal

# Desktop app
# bash bin/launch-desktop-macos.sh → Categories → brew → click "apply" → type "apply" in modal
```

They all hit the same orchestrator (`core/ascendo/orchestrator/runner.py`),
which calls the same `BrewManager.run_phase()`, which spawns
`bash adapters/macos/scripts/brew/apply.sh`, which writes the same
`~/.ascendo/runs/<run-id>/apply__brew.json` sidecar.

Differences:

| | CLI | Web | Desktop |
|---|---|---|---|
| Best for | scripting, CI | demos, exploration | daily use |
| Confirm gate | `--phase apply` directly mutates (no prompt unless via `bin/run-tag-release-*.sh`) | Modal: type `apply` | Modal: type `apply` |
| Live progress | text in terminal | SSE → Run Center tab | SSE → Run Center tab in window |
| Sidecar paths | `~/.ascendo/runs/…` | same | same |
| Reboot detection | exit code 75 | top banner | top banner |
| Sudo (macOS) | interactive prompt | password modal once, cached | password modal once, cached |

---

## 5. Common workflows (recipes)

### "What's outdated?" (read-only, ≈ 30 s)

```bash
# CLI:
python3 -m ascendo run --profile=quick

# Web/Desktop: Categories tab → click each row's "check" button.
```

### "Update everything except drivers" (safe profile)

```bash
# CLI:
python3 -m ascendo run --profile=safe

# Web: Quick Actions → Safe profile (or click each category's check → plan
# → apply in turn, confirming each).
```

### "Set up nightly auto-update on a Mac" (one-time)

```bash
python3 -m ascendo schedule install \
    --name nightly \
    --calendar "DAILY 03:30" \
    --profile safe
```

(Verify with `python3 -m ascendo schedule list`.)

### "Investigate why an apply failed last night"

```bash
# Find the latest run
last_run=$(python3 -m ascendo runs list -n 1 --status failed | awk 'NR==2 {print $1}')

# Show the summary
python3 -m ascendo runs show "$last_run"

# Drill into the apply phase sidecar
python3 -m ascendo runs json "$last_run" --pretty | jq '.sidecars[] | select(.phase == "apply") | .messages'

# Look at the per-phase log
cat ~/.ascendo/runs/"$last_run"/apply__*.log
```

### "Wipe Ascendo state (start fresh)"

```bash
rm -rf ~/.ascendo/                           # all runs + sidecars + logs
# (re-running ascendo will recreate it on next run)
```

---

## 6. Where things live

```
~/Dev_Env/ascendo/                  # the repo
├── adapters/{macos,windows,ubuntu}/  # OS-specific managers
├── core/ascendo/                     # OS-agnostic core (CLI, dashboard, orchestrator)
├── ui/desktop-tauri/                 # Tauri 2.x native shell
├── app/frontend/                     # vanilla JS SPA
├── bin/                              # PowerShell + bash launcher / install / validate scripts
└── plugins/dell-driver-update/       # first official plugin (Windows only)

~/.ascendo/                          # runtime state
├── runs/<uuid>/                       # one folder per run
│   ├── check__brew.json
│   ├── plan__brew.json
│   ├── apply__brew.json               # the sidecar = the receipt
│   ├── apply__brew.log                # plain log
│   ├── verify__brew.json
│   ├── cleanup__brew.json
│   └── run.json                       # consolidated summary
└── (no other files)

~/Library/LaunchAgents/dev.ascendo.<name>.plist     # macOS schedules
~/Library/Application Support/Ascendo/schedules/    # macOS schedule sidecars
%LocalAppData%\Ascendo\logs\service\                # Windows service logs
\Ascendo\<name>                                     # Windows Task Scheduler tasks
```

---

## 7. Trouble?

- **macOS** issues → see [MACOS_TESTING.md §9 Troubleshooting](MACOS_TESTING.md#9-troubleshooting).
- **Windows** issues → see [WINDOWS_TESTING.md §6 Troubleshooting](WINDOWS_TESTING.md#6-troubleshooting).
- **Linux** issues → still in migration; see legacy `app/README.md` for the FastAPI dashboard until `adapters/ubuntu/` polish completes.

If anything in this guide doesn't match what you see, paste the exact
command + output + `git log --oneline -3` so we can pin it to a commit.

---

## 8. Roadmap pointers

- **Forward roadmap** → [PLAN.md](PLAN.md)
- **Per-session log** → [HANDOFF.md](HANDOFF.md)
- **Architecture decisions** → [docs/architecture/](docs/architecture/)
- **5-phase JSON contract** → [docs/agents/contract.md](docs/agents/contract.md)
- **Cross-platform notes** → [docs/CROSS_PLATFORM.md](docs/CROSS_PLATFORM.md)

License: [MIT](LICENSE) — do whatever you want, just keep the copyright notice.
