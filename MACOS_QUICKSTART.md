# Ascendo on macOS — Quickstart (operator)

One-screen guide: install → open the dashboard → see what's installed →
run brew / mas / Mac App Store / softwareupdate / Time Machine snapshot
list / launchd schedule. Tested on Mac.r12.home (Apple Silicon, macOS 15.x,
PowerShell-equivalent stack: bash 3.2.57, Homebrew 5.1.9, mas 7.0.0,
Python 3.13, jq 1.8.1).

For the full test matrix (every flag, every endpoint, every CLI exit
code) see [`MACOS_TESTING.md`](MACOS_TESTING.md).

For the cross-OS three-interface walkthrough (CLI vs web vs desktop) see
[`USER_GUIDE.md`](USER_GUIDE.md).

---

## 1 · Install (≈ 3 min, one-time)

```bash
cd ~/Dev_Env
git clone https://github.com/KasprowiczM/ascendo.git
cd ascendo
bash bin/install-dev-macos.sh             # core + adapter + venv-equivalent + smoke
```

Re-running `install-dev-macos.sh` after a `git pull` is safe — it's
idempotent and re-installs the editable Python packages so the CLI always
runs the latest code.

The script:
1. Checks for `python3`, `bash`, `brew`, installs `jq` if missing.
2. `pip install -e ./core` (with `--break-system-packages` per Homebrew
   Python's PEP 668 guard).
3. `pip install -e ./adapters/macos --no-deps`.
4. Installs FastAPI / uvicorn / httpx for the dashboard.
5. Auto-runs `bash bin/validate-macos.sh` (34/34 checks).

**Skip the validate step:** `bash bin/install-dev-macos.sh --skip-validate`.

## 2 · Open the dashboard

Three equivalent paths — pick one:

| | Where | What it does |
|-|-------|--------------|
| **A** | `python3 -m ascendo dashboard` | Backend only; visit `http://127.0.0.1:8765` in your browser. |
| **B** | `bash bin/launch-desktop-macos.sh` | Native Tauri 2.x window (WKWebView). The app in a real macOS window, not a browser tab. **Requires Rust + Node** — see prerequisites in the script header. |
| **C** | (after `tauri build`) Double-click `Ascendo.app` | Production build with bundled icon. Lives in `ui/desktop-tauri/src-tauri/target/release/bundle/macos/`. |

> **After `git pull`** — if the icon set changed (you'll see updates in
> `ui/desktop-tauri/src-tauri/icons/`), you need to **rebuild the .app
> bundle** for the new icon to appear in Cmd+Tab / Dock / Finder:
>
> ```bash
> bash bin/launch-desktop-macos.sh --build   # embeds icon.icns into the .app
> bash bin/refresh-macos-icon.sh             # flushes macOS icon caches
> ```
>
> macOS caches app-bundle icons aggressively; without the cache flush
> step, Cmd+Tab keeps rendering the previous icon even though the
> .app on disk is fresh. The refresh script touches the bundle,
> clears IconServices caches, and restarts Dock + Finder. Pass
> `--no-sudo` if you've already cleared the system caches manually.

The first time you launch any of these, the **Apps inventory** is
populated by `system_profiler SPApplicationsDataType` (≈ 5–15 s on a
typical box, 387 apps on Mac.r12.home). Click the Categories tab to see
the per-source breakdown.

## 3 · See what's installed (Categories tab)

The Categories tab shows one row per source:

| Source | Where the data comes from | What it represents |
|--------|---------------------------|--------------------|
| **brew** | `brew outdated --json=v2` (formulae + casks) | Things you installed via `brew install …` |
| **mas** | `mas outdated` | Mac App Store apps with pending upgrades |
| **softwareupdate** | `softwareupdate -l` | macOS OS / security / Safari updates |
| **inventory** (Categories sub-rows: SYSTEM / MAS / BREW / WEB) | `system_profiler -json SPApplicationsDataType` | All installed `.app` bundles, classified by signature/origin |

Click a row to expand it and see every package with installed/candidate
version + status pill.

## 4 · Check for updates

Each category row has its own 5-phase buttons:

```
check  →  plan  →  apply  →  verify  →  cleanup
```

You almost always want **`check`** first — it's read-only, takes a few
seconds, and surfaces every available update without changing anything.
The Run Center tab pops open showing live progress; sidecars stream in
via SSE as each phase finishes.

| You want to … | Click |
|---------------|-------|
| Find new **Homebrew** updates (formulae + casks) | brew → check |
| Find new **Mac App Store** updates | mas → check |
| Find new **macOS OS / security** updates | softwareupdate → check |

## 5 · Apply updates

Click **`apply`** on the source you want to update. Every apply gates on
a confirmation modal — type the literal word `apply` to proceed.

Apply phases that need elevation (`mas`, `softwareupdate`) prompt once
for sudo. The dashboard caches the password in memory and forwards it
via `SUDO_ASKPASS` to all child processes — never written to disk, never
logged.

| Update target | What to click | Notes |
|---------------|---------------|-------|
| **Homebrew packages** | brew → apply | Idempotent; safe to re-run |
| **Mac App Store apps** | mas → apply | `sudo mas upgrade <id>` enforced (CVE-2025-43411 mitigation) |
| **macOS itself** | softwareupdate → apply | `sudo -A softwareupdate -ir -R --verbose`. The `-R` flag is mandatory — without it updates download but never apply. May reboot the Mac mid-run. |

After every apply phase, the dashboard invalidates the inventory cache
and a banner appears at the top if a reboot is required.

## 6 · From the CLI (no dashboard needed)

```bash
python3 -m ascendo doctor                                     # 10-component health snapshot
python3 -m ascendo run --category brew         --phase check  # ≈ 5 s
python3 -m ascendo run --category mas          --phase check
python3 -m ascendo run --category softwareupdate --phase check  # ≈ 30 s
python3 -m ascendo run --category brew         --phase apply  # mutating
python3 -m ascendo runs list -n 5                             # last 5 runs
python3 -m ascendo runs json <run-id> --pretty | jq .summary
python3 -m ascendo snapshot list                              # APFS local snapshots (read-only)
python3 -m ascendo schedule install \
    --name nightly --calendar "DAILY 03:30" --profile safe    # launchd LaunchAgent
python3 -m ascendo schedule list
python3 -m ascendo schedule trigger --name nightly            # run NOW
python3 -m ascendo schedule remove --name nightly
```

Exit codes the run command emits:

| Code | Meaning |
|------|---------|
| `0`  | success |
| `1`  | warnings only (e.g. some upgrades deferred) |
| `2`  | bad input (e.g. unknown category) |
| `30` | hard failure during apply |
| `75` | success, but **reboot required** |

## 7 · Update macOS itself (right now)

Most direct path:

```bash
# 1. List pending OS updates
python3 -m ascendo run --category softwareupdate --phase check

# 2. Inspect the sidecar (the JSON receipt)
ls -1 ~/.ascendo/runs/ | tail -1                   # newest run
python3 -m ascendo runs json $(ls -1 ~/.ascendo/runs/ | tail -1) --pretty | jq '.sidecars[0].items'

# 3. Apply (mutating, requires sudo password — set SUDO_PW or be at the terminal)
python3 -m ascendo run --category softwareupdate --phase apply

# 4. If exit code 75 (reboot required), reboot manually. Then verify:
python3 -m ascendo run --category softwareupdate --phase verify
```

## 8 · launchd scheduler (nightly auto-run)

Install a per-user LaunchAgent that runs the `safe` profile every night
at 03:30:

```bash
python3 -m ascendo schedule install \
    --name nightly \
    --calendar "DAILY 03:30" \
    --profile safe \
    --description "Nightly Ascendo safe profile"
```

That writes:
- `~/Library/LaunchAgents/dev.ascendo.nightly.plist` (the launchd plist)
- `~/Library/Application Support/Ascendo/schedules/nightly.json` (description metadata)

DSL forms supported (mirror the Windows scheduler exactly):

| DSL form              | When it runs                  | launchd plist                                  |
|-----------------------|-------------------------------|------------------------------------------------|
| `DAILY HH:MM`         | every day at HH:MM            | `StartCalendarInterval{Hour, Minute}`          |
| `WEEKLY DAY HH:MM`    | every <DAY> at HH:MM          | `StartCalendarInterval{Hour, Minute, Weekday}` |
| `MONTHLY HH:MM`       | the 1st of the month at HH:MM | `StartCalendarInterval{Hour, Minute, Day=1}`   |
| `MONTHLY DAY HH:MM`   | the <DAY>th of the month      | `StartCalendarInterval{Hour, Minute, Day}`     |
| `HOURLY :MM`          | every hour at :MM past        | `StartCalendarInterval{Minute}`                |
| `MINUTE N`            | every N minutes               | `StartInterval=N*60`                           |

Other commands:

```bash
python3 -m ascendo schedule list                          # show all installed
python3 -m ascendo schedule trigger --name nightly        # run NOW (synchronous kickstart)
python3 -m ascendo schedule remove --name nightly         # bootout + rm plist + rm sidecar
```

## 9 · Time Machine snapshots (read-only)

APFS local snapshots are auto-managed by macOS. Ascendo can list them
but cannot create them (use System Settings → Time Machine for that):

```bash
python3 -m ascendo snapshot list                          # show snapshot ids + dates
python3 -m ascendo snapshot create -m "before-experiment" # raises SnapshotError; APFS is auto-managed
```

**Recommended pre-apply ritual (manual, until Apple opens the API):**

```bash
# 1. Force a fresh local APFS snapshot before bulk apply
tmutil localsnapshot
# 2. Confirm Ascendo can see it
python3 -m ascendo snapshot list | head -5
# 3. Now run bulk apply with the dashboard or CLI
python3 -m ascendo run --phase apply
# If something goes wrong, restore via macOS Recovery → Restore From Time
# Machine Backup, or `tmutil restore` to a specific snapshot.
```

This is a workaround for the fact that the orchestrator can't programatically
create snapshots on macOS like it does on Windows (VSS). Plumbing a pre-apply
hook that calls `tmutil localsnapshot` is on the M5.x backlog.

## 10 · Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| `pip: command not found` | Homebrew Python uses `pip3` or `python3 -m pip` | Use `python3 -m pip ...` |
| `error: externally-managed-environment` | PEP 668 (Homebrew Python 3.12+) | Use `--break-system-packages` (already in `install-dev-macos.sh`) |
| `ascendo doctor` shows `mas: unavailable` | mas not installed | `brew install mas` |
| `mas` apply fails with "no entitlements" | iPad-only App Store app | Currently unsupported headlessly — install via App Store GUI |
| softwareupdate apply hangs forever | macOS dialog asking for password | Set `SUDO_PW='...'` env var first, or run interactively |
| Tauri `npm run tauri dev` fails on `linker 'cc'` | Apple CLI tools missing | `xcode-select --install` |
| Tauri build .app won't open ("damaged") | Unsigned binary, Gatekeeper | Right-click → Open the first time; or `xattr -dr com.apple.quarantine Ascendo.app` |
| `launchctl bootstrap` says "Service already loaded" | Re-installing same agent | Idempotent — the install action does `bootout` first; safe to re-run |
| `python3 -m ascendo` says capabilities lack SCHEDULING | Stale `__pycache__` | `find . -name '__pycache__' -type d -exec rm -rf {} +` then re-run |
| Cmd+Tab still shows old Ascendo icon after `git pull` | macOS IconServices cache + stale .app bundle | `bash bin/launch-desktop-macos.sh --build && bash bin/refresh-macos-icon.sh` |
| `zsh: command not found: #` / `not enough directory stack entries` when copy-pasting commands | zsh by default does not honour `#` comments in interactive mode, AND treats `~N` (e.g. `~15` from "~15 s") as a directory-stack reference | One-time fix: `echo 'setopt interactive_comments' >> ~/.zshrc && source ~/.zshrc`. After that, lines like `command # comment` work as expected. As a side-note, our launch script auto-strips `#` and unknown args so the build still completes — but the SECOND command in a multi-line paste won't run if the first one trips zsh. |

## 11 · Where everything lives

```
~/Dev_Env/Ascendo/
├── adapters/macos/
│  ├── ascendo_macos/                    # Python: MacOSAdapter + 5 managers
│  │   ├── adapter.py                    # capability flag, health_check, manager wiring
│  │   ├── inventory.py                  # MacOSInventory (system_profiler)
│  │   ├── snapshot.py                   # TimeMachineSnapshot (read-only)
│  │   └── managers/
│  │       ├── brew.py
│  │       ├── mas.py
│  │       ├── elevation.py
│  │       ├── softwareupdate.py
│  │       └── scheduler.py              # LaunchdScheduler (M5.5)
│  ├── scripts/                          # Per-category bash phase scripts
│  │   ├── brew/{check,plan,apply,verify,cleanup}.sh
│  │   ├── mas/...
│  │   ├── softwareupdate/...
│  │   ├── inventory/list.sh
│  │   ├── snapshot/list.sh
│  │   └── scheduler/scheduler.sh        # JSON-IPC bash driver (M5.5)
│  └── lib/                              # Shared bash + Python helpers
├── core/ascendo/                        # OS-agnostic CLI + orchestrator + REST API
│  ├── cli/                              # python3 -m ascendo … entry point
│  ├── dashboard/                        # FastAPI app, served at 127.0.0.1:8765
│  └── orchestrator/                     # 5-phase runner + JSON-v1 sidecar IO
├── ui/desktop-tauri/                    # Tauri 2.x native shell (Rust + WKWebView)
├── app/frontend/                        # The SPA the desktop shell renders
├── bin/
│  ├── install-dev-macos.sh              # one-shot setup (this guide §1)
│  ├── launch-desktop-macos.sh           # Tauri dev / build
│  ├── validate-macos.sh                 # End-to-end smoke (34/34 on green)
│  └── run-tag-release-macos.sh          # 7-stage release flow (preflight → tag)
└── ~/.ascendo/runs/<uuid>/              # All sidecars + per-phase logs (NB: home, not repo)
```

## 12 · One-liner sanity check

If anything seems off, run this first — exits 0 only when CLI + dashboard
+ all 5 phases × 3 categories produce real sidecars and the SPA assets
serve correctly. Stage 12 also exercises the launchd scheduler round-trip
(install + list + trigger + remove a throwaway agent):

```bash
bash bin/validate-macos.sh
# Expected: ALL CHECKS PASSED. (34/34)
```

Anything red names the failed component (CLI, manager, sidecar parse,
dashboard endpoint, SPA asset, scheduler) so you know exactly where to
start.
