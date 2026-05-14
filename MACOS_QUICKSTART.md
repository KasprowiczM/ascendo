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

**Recommended — one-liner from any terminal:**

```bash
curl -fsSL https://raw.githubusercontent.com/KasprowiczM/ascendo/main/install.sh | bash
```

That single command:
- Detects your OS (Darwin / Ubuntu / Debian / Fedora / Arch)
- Asks for language (en/pl) and install profile (cli / web / desktop)
- Verifies network connectivity + ≥1 GB free disk
- Detects + reports missing Python 3.11+ (you install via Homebrew if needed)
- Auto-installs missing system deps via `brew` (or `apt` / `dnf` / `pacman` on Linux)
- Clones to `~/.local/share/ascendo`
- Sets up a venv-equivalent + editable pip install of core + macOS adapter
- Symlinks `ascendo` to `~/.local/bin/ascendo` (auto-detects `$SHELL` for PATH instructions)
- Runs `ascendo doctor` self-test; bails loudly on non-zero

Optional flags: `--reinstall` (wipe + rebuild), `--update` (skip clone, just upgrade),
`--verbose` (trace every command), `--non-interactive` (CI mode).
Env-var overrides: `ASCENDO_LANG`, `ASCENDO_PROFILE`, `ASCENDO_HOME`,
`ASCENDO_NONINTERACTIVE`, `ASCENDO_REPO_URL`, `ASCENDO_BRANCH`.

**To update an existing install:**
```bash
curl -fsSL https://raw.githubusercontent.com/KasprowiczM/ascendo/main/update.sh | bash
```
(`git pull --ff-only`, refresh editable installs, restart any running
dashboard, print version delta.)

**Manual / dev path** (if you've already cloned the repo):

```bash
cd ~/Dev_Env
git clone https://github.com/KasprowiczM/ascendo.git
cd ascendo
bash bin/install-dev-macos.sh             # core + adapter + venv-equivalent + smoke
```

Re-running `install-dev-macos.sh` after a `git pull` is safe — it's
idempotent and re-installs the editable Python packages so the CLI always
runs the latest code.

> **Why your dashboard might be showing stale results.** The desktop
> app, the CLI (`python3 -m ascendo`), and the dashboard all import
> the macOS adapter as an editable Python install. The install
> location is wherever you ran `pip install -e adapters/macos` from
> — typically `~/Dev_Env/Ascendo`, not a worktree. After you `git
> pull` on `~/Dev_Env/Ascendo`, the **CLI and dashboard see the new
> code immediately** (Python re-reads on next invocation), but
> Ascendo.app needs a fresh launch (quit + reopen). If you keep the
> dashboard running across upgrades, restart it to pick up bash
> handler changes.

Verify which adapter is active:
```bash
python3 -c "import ascendo_macos; print(ascendo_macos.__file__)"
# /Users/<you>/Dev_Env/Ascendo/adapters/macos/ascendo_macos/__init__.py
```
If that path looks wrong (points at a stale clone, a virtualenv, or
nothing at all), re-run `bash bin/install-dev-macos.sh` from the
correct repo directory.

The script:
1. Checks for `python3`, `bash`, `brew`, installs `jq` if missing.
2. `pip install -e ./core` (with `--break-system-packages` per Homebrew
   Python's PEP 668 guard).
3. `pip install -e ./adapters/macos --no-deps`.
4. Installs FastAPI / uvicorn / httpx for the dashboard.
5. Auto-runs `bash bin/validate-macos.sh` (34/34 checks).

**Skip the validate step:** `bash bin/install-dev-macos.sh --skip-validate`.

### Profiles: quick vs safe vs full

The Overview tab exposes three "Quick action" buttons that map to update
profiles. They differ in **which phases run** AND **which categories are
in scope**:

| Profile | Phases | Categories | Forced reboot risk | Use case |
|---------|--------|------------|--------------------|----------|
| **Quick** (action 2) | `check` only | all | none (read-only) | "What's available to update?" — ~10–60 s, no sudo |
| **Safe** (action 3) | `check + plan + apply + verify + cleanup` | all EXCEPT `softwareupdate`, `drivers`, `firmware` | none — your session stays alive | "Upgrade my apps without losing my session" |
| **Full** (action 5) | `check + plan + apply + verify + cleanup` | all (incl. `softwareupdate`) | macOS patches may force reboot | "Upgrade everything; I'm ready to reboot" |
| Full dry-run (action 4) | all 5 phases | all | none (no mutation) | "Show me exactly what Full would do, without doing it" |

Concretely on macOS:

- **Quick** scans brew + mas + npm + pip + web + softwareupdate for
  candidates and surfaces them in Categories / Apps. No mutations.
- **Safe** updates brew formulae, Mac App Store apps, npm globals, pip
  globals, and web apps (sparkle/github_dmg/release_feed/omaha apply
  paths). Skips `softwareupdate` so macOS patches that require a reboot
  don't fire mid-day.
- **Full** does everything Safe does, plus `softwareupdate` (with the
  mandatory `-R` flag — restart required if any patch needs it).

CLI equivalents:
```bash
python3 -m ascendo run --profile quick                  # read-only sweep
python3 -m ascendo run --profile safe                   # upgrade apps, skip OS patches
python3 -m ascendo run --profile full                   # upgrade everything (may reboot)
python3 -m ascendo run --profile full --dry-run         # show full plan, no mutation
python3 -m ascendo run -c brew,npm --phases check,apply # explicit override
```

### Editions: basic (default) vs dev

Ascendo ships in two editions. The same code, different surfaces:

| Edition | For | What's visible | Helper scripts |
|---------|-----|----------------|----------------|
| **`basic`** (default) | Everyday user | Overview · Categories · Run Center · History · Apps · Settings · Help · About | `ascendo_update`, `ascendo_doctor`, `ascendo_maintenance`, … |
| **`dev`** | Maintainer / contributor | Everything basic shows + Sync · Hosts · Logs (raw events) · Git push · dev-sync overlay | basic set + `dev/` shims (`ascendo_sync`, `ascendo_push`, …) |

Setting the edition (priority order, highest wins):

1. **`ASCENDO_EDITION=basic|dev`** environment variable
2. **`$ASCENDO_HOME/.ascendo-edition`** marker file (one line)
3. **`basic`** default

```bash
# Switch the running install to dev:
echo dev > ~/.local/share/ascendo/.ascendo-edition
# Restart any dashboard for the change to take effect:
pkill -f 'python.*-m ascendo dashboard' && nohup ascendo dashboard --background &
```

The basic edition's `EditionGateMiddleware` returns HTTP 404 for `/sync/*`,
`/hosts*`, `/git/push*`, `/dev-sync*`, `/profiles/import*`. CSS hides the
nav entries, and the helper scripts in `~/.local/bin/dev/` are only
symlinked if `edition=dev` at install time.

### Build a DMG locally (dev / testing only)

> **DMG distribution is NOT part of the public release surface today.**
> The public path on macOS is the `curl … install.sh \| bash` one-liner
> from §1 above. macOS Gatekeeper hard-blocks unsigned downloaded apps
> on Sequoia 15 and Tahoe 16 (no "Open Anyway" button in the dialog),
> so a DMG you upload to GitHub Releases without an Apple Developer ID
> + notarization just frustrates your recipients. See
> [`docs/DESKTOP_INSTALLER_STATUS.md`](docs/DESKTOP_INSTALLER_STATUS.md)
> for the cross-platform rationale and
> [`docs/DMG_DISTRIBUTION.md`](docs/DMG_DISTRIBUTION.md) for the
> sign-and-notarize playbook when you're ready to re-introduce DMG
> releases.

For contributors who want to build a DMG locally — for testing on your
own Mac only:

```bash
# Default — basic edition, "full" profile baked in:
bash bin/build-dmg.sh
# Produces: dist/Ascendo-Basic-0.6.0-arm64.dmg

# Dev edition for maintainers:
bash bin/build-dmg.sh --edition=dev --profile=full
# Produces: dist/Ascendo-Dev-0.6.0-arm64.dmg
```

Each DMG bakes a `.ascendo-edition` marker file shipped inside the
`.app`'s `Resources/`. On first launch on the SAME Mac it was built on
the .app works fine; on another Mac without the signing chain it'll
hit Gatekeeper. Use `xattr -dr com.apple.quarantine /Applications/Ascendo.app`
as a workaround for hand-shared builds.

> **`build-dmg.sh --help`** documents `--with-installer`,
> `--with-create-dmg`, `--profile {quick,safe,full}`, and `--skip-cargo`.

## 2 · Open the dashboard

Three paths — pick one:

| | Where | What it does |
|-|-------|--------------|
| **A** *(recommended)* | **`ascendo web start`** | Detached background dashboard with pidfile tracking, **opens browser automatically**. Pair with `ascendo web stop`, `restart`, `status`. Idempotent. |
| **B** | `python3 -m ascendo dashboard` | Backend in the foreground (Ctrl-C to stop); visit `http://127.0.0.1:8765` in your browser. Useful for debugging. |
| **C** *(dev / testing only)* | `bash bin/launch-desktop-macos.sh` | Native Tauri 2.x window (WKWebView). Requires Rust + Node. Not for public distribution today (see Gatekeeper note in §1). |

### `ascendo web` lifecycle commands

```bash
ascendo web start             # start dashboard in background; pidfile at ~/.ascendo/dashboard.pid
ascendo web start --open      # also open in default browser
ascendo web status            # human-readable: running pid=… http://127.0.0.1:8765/ started=…
ascendo web status --json     # machine-readable; useful in scripts
ascendo web stop              # graceful SIGTERM, waits up to 5s
ascendo web stop --force      # escalates to kill -9 if SIGTERM doesn't take
ascendo web restart           # stop + start in one call
ascendo web open              # open running dashboard in default browser (refuses if not running)
```

Status output reads truth from BOTH the pidfile AND a live socket probe,
so it correctly distinguishes:
- `running` — your pidfile + port bound + /version returns 200
- `stale pidfile` — pidfile present but process gone (auto-cleared by `stop`)
- `bound by something else` — port 8765 in use but no pidfile (likely
  Tauri-spawned sidecar from Ascendo.app)
- `stopped` — nothing is running

`ascendo web stop` ONLY kills the process tracked by its own pidfile. A
Tauri-spawned dashboard sidecar is left alone — its lifecycle belongs
to the desktop app.

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
| **npm** | `npm outdated -g` + node/bun version probes | Global npm CLIs (claude-code, codex-cli, etc.) + Node + Bun |
| **pip** | `pip list --outdated` for tracked Python CLIs | Global Python tools (ruff, black, pipx, uv, etc.) |
| **web** *(M5.7 + M5.7.1 + M5.7.2)* | `lib/web_discovery.sh` walks `/Applications` Info.plists × `_apps.toml` v2 overrides × 8 handlers | **Every** installed `.app` not owned by brew/mas/softwareupdate (~50 on a typical Mac). On Mac.r12.home **17 apps** report a real candidate version (v0.4.2 vs 4 in v0.4.0). Tier-A real-candidate handlers: Sparkle (`SUFeedURL`; switched Docker here in M5.7.1 — was probing CLI plugin v0.3.0!), GitHub Releases (overrides), `release_feed` (generic JSON+YAML probe — covers Claude/Codex/Notion-Cal/Cursor via M5.7.2 app.asar binary mining + VSCode/Zoom/Firefox-Dev/Notion/Ledger/KeePassXC/Obsidian via M5.7.1), MS AutoUpdate. Tier-B trigger-only: Keystone (Chrome/Drive/Brave — `KSProductID`), Squirrel.Mac (apps where update URL is injected at runtime — `Squirrel.framework`), builtin (everything else). Tier-B apply emits status `triggered` with informational `triggered_pending`/`triggered_confirmed` from verify (vendor agent reconciles asynchronously). 6 apps still need mitmproxy on launch to surface their URLs (ChatGPT/Warp/MEGAsync/LM Studio/Antigravity/Comet — deferred to M5.7.3). |
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
| Find new **npm global CLI** updates | npm → check |
| Find new **Python global tool** updates | pip → check |
| Find new updates for **web-installed apps** (Brave, Chrome, Slack, Claude, etc.) | web → check |
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
| **npm global CLIs** | npm → apply | No sudo; user-site only |
| **Python global tools** | pip → apply | No sudo; user-site or brew-Python-site depending on flavour |
| **Web-installed apps** | web → apply | 7 handlers: silent updates for Sparkle/GH/Keystone/Squirrel/msupdate/Docker. Defer-if-running policy: sparkle/github_dmg/squirrel skip when app is open (close it and re-run); keystone/msupdate/docker apply regardless. `/Applications` writes try without sudo first; sudo on EACCES. spctl signature verify + quarantine xattr strip on installed bundles. |
| **macOS itself** | softwareupdate → apply | `sudo -A softwareupdate -ir -R --verbose`. The `-R` flag is mandatory — without it updates download but never apply. May reboot the Mac mid-run. |

After every apply phase, the dashboard invalidates the inventory cache
and a banner appears at the top if a reboot is required.

## 6 · From the CLI (no dashboard needed)

```bash
python3 -m ascendo doctor                                     # 12-component health snapshot
python3 -m ascendo run --category brew         --phase check  # ≈ 5 s
python3 -m ascendo run --category mas          --phase check
python3 -m ascendo run --category npm          --phase check
python3 -m ascendo run --category pip          --phase check
python3 -m ascendo run --category web          --phase check  # M5.6 — ~24 web apps
python3 -m ascendo run --category softwareupdate --phase check  # ≈ 30 s
python3 -m ascendo run --category brew         --phase apply  # mutating
python3 -m ascendo run --category web          --phase apply --filter chrome  # one app
python3 -m ascendo run --category web          --phase apply --dry-run        # preview only
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

## 10 · One-time Touch ID setup (skip the password prompt)

Apply phases that need root (mas, softwareupdate, msupdate, web) prompt
for elevation. By default macOS shows a password dialog. To use **Touch
ID instead** (one tap per run, password fallback automatic via the "Use
Password" button in the same prompt), enable PAM Touch ID once:

```bash
# Ascendo prefers /etc/pam.d/sudo_local (Sonoma 14+, survives macOS upgrades).
# Older macOS: edit /etc/pam.d/sudo directly with the same auth line.
sudo tee /etc/pam.d/sudo_local <<'EOF'
auth       sufficient     pam_tid.so
EOF
```

Verify:

```bash
sudo -K              # clear any cached sudo timestamp
python3 -m ascendo run --category mas --phase apply --dry-run
# Expected: macOS Touch ID prompt sheet (NOT a password dialog).
# If you click "Use Password" or pam_tid fails, sudo falls back to
# the standard password prompt at the same TTY — no extra dialog.
```

After PAM Touch ID is wired:
- The dashboard's **password modal is automatically skipped** when you
  click "Full update" / "Safe update" — `sudoMgr.ensure()` polls
  `/elevation/touchid/status` and short-circuits when `enabled=true`.
- The first apply phase fires the Touch ID sheet via `_ascendo_sudo_warm`
  (TTY-PAM). After you tap, the sudo timestamp is cached for ~5 minutes
  and every later apply phase short-circuits via `sudo -n -v` — **one
  Touch ID tap per run, total**.
- Apply scripts call `_ascendo_sudo` (in `lib/ascendo_json.sh`), which
  picks `sudo -A` (askpass) or plain `sudo` (TTY-PAM) by env. So both
  the dashboard-typed-password flow and the TouchID-only flow work
  without any code change.

Why this matters: `osascript … with administrator privileges`
**bypasses PAM entirely** (it goes through Apple's SecurityAgent /
AuthorizationCreate path), so `pam_tid.so` would be ignored. Ascendo
calls `sudo -v` directly, which respects PAM order — Touch ID first
when configured, password fallback automatic.

If you're running Ascendo headless (cron / CI / SSH without TTY)
and don't want any GUI dialog, set `ASCENDO_SUDO_NO_GUI=1`. To
re-enable the SecurityAgent osascript fallback as a last resort, set
`ASCENDO_SUDO_ALLOW_GUI=1` (default is off; SecurityAgent doesn't use
Touch ID, so this only helps fully unattended automation).

## 11 · Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| `pip: command not found` | Homebrew Python uses `pip3` or `python3 -m pip` | Use `python3 -m pip ...` |
| `error: externally-managed-environment` | PEP 668 (Homebrew Python 3.12+) | Use `--break-system-packages` (already in `install-dev-macos.sh`) |
| Sudo password dialog appears every run instead of Touch ID | `pam_tid.so` not configured in `/etc/pam.d/sudo_local` | See §10 above — one-line fix, persistent across reboots |
| Brave / Chrome / etc. crashed mid-apply | Vendor's `--check-for-update` CLI flag failed and our timeout-watcher killed the spawned PID. Fixed in v0.3.0 fix-ups (graceful TERM before KILL, plus apply_cli_argv removed from Brave entry). | `git pull` to get the fixes; user override your registry to drop apply_cli_argv on any app where it misbehaves |
| `ascendo doctor` shows `mas: unavailable` | mas not installed | `brew install mas` |
| `mas` apply fails with "no entitlements" | iPad-only App Store app | Currently unsupported headlessly — install via App Store GUI |
| softwareupdate apply hangs forever | macOS dialog asking for password | Set `SUDO_PW='...'` env var first, or run interactively |
| Tauri `npm run tauri dev` fails on `linker 'cc'` | Apple CLI tools missing | `xcode-select --install` |
| Tauri build .app won't open ("damaged") | Unsigned binary, Gatekeeper | Right-click → Open the first time; or `xattr -dr com.apple.quarantine Ascendo.app` |
| `launchctl bootstrap` says "Service already loaded" | Re-installing same agent | Idempotent — the install action does `bootout` first; safe to re-run |
| `python3 -m ascendo` says capabilities lack SCHEDULING | Stale `__pycache__` | `find . -name '__pycache__' -type d -exec rm -rf {} +` then re-run |
| Cmd+Tab still shows old Ascendo icon after `git pull` | macOS IconServices cache + stale .app bundle | `bash bin/launch-desktop-macos.sh --build && bash bin/refresh-macos-icon.sh` |
| `zsh: command not found: #` / `not enough directory stack entries` when copy-pasting commands | zsh by default does not honour `#` comments in interactive mode, AND treats `~N` (e.g. `~15` from "~15 s") as a directory-stack reference | One-time fix: `echo 'setopt interactive_comments' >> ~/.zshrc && source ~/.zshrc`. After that, lines like `command # comment` work as expected. As a side-note, our launch script auto-strips `#` and unknown args so the build still completes — but the SECOND command in a multi-line paste won't run if the first one trips zsh. |

## 12 · Where everything lives

```
~/Dev_Env/Ascendo/
├── adapters/macos/
│  ├── ascendo_macos/                    # Python: MacOSAdapter + 6 managers
│  │   ├── adapter.py                    # capability flag, health_check, manager wiring
│  │   ├── inventory.py                  # MacOSInventory (system_profiler)
│  │   ├── snapshot.py                   # TimeMachineSnapshot (read-only)
│  │   ├── web_registry.py               # Pydantic _apps.toml validator (M5.6)
│  │   └── managers/
│  │       ├── brew.py
│  │       ├── mas.py
│  │       ├── npm.py
│  │       ├── pip.py
│  │       ├── web.py                    # WebManager (M5.6)
│  │       ├── elevation.py
│  │       ├── softwareupdate.py
│  │       └── scheduler.py              # LaunchdScheduler (M5.5)
│  ├── config/
│  │   └── web_apps.toml                 # ~24-app curated registry (M5.6)
│  ├── scripts/                          # Per-category bash phase scripts
│  │   ├── brew/{check,plan,apply,verify,cleanup}.sh
│  │   ├── mas/...
│  │   ├── npm/...
│  │   ├── pip/...
│  │   ├── web/...                       # M5.6 — dispatches to handlers/
│  │   ├── softwareupdate/...
│  │   ├── inventory/list.sh
│  │   ├── snapshot/list.sh
│  │   └── scheduler/scheduler.sh        # JSON-IPC bash driver (M5.5)
│  └── lib/                              # Shared bash + Python helpers
│      └── handlers/                     # 7 per-mechanism handlers (M5.6)
│          ├── sparkle.sh                # appcast XML + DMG install
│          ├── github_dmg.sh             # GH Releases + arm64 asset
│          ├── keystone.sh               # Google Software Update agent
│          ├── squirrel.sh               # Squirrel.Mac auto-on-relaunch
│          ├── builtin.sh                # open + emit instruction
│          ├── msupdate.sh               # Microsoft AutoUpdate suite
│          └── docker.sh                 # Docker Desktop CLI updater
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

## 13 · Sesja 67 features (cross-platform)

Three big improvements that landed for Windows and Ubuntu (HANDOFF Sesja
67) live in `core/` + `app/frontend/`, so they work on macOS without any
adapter-side change:

### Inventory dedup — schema v2
`~/.ascendo/inventory.db` primary key widened to `(category, name,
item_id)`. Multi-architecture packages (e.g. parallel VC++ redistributables
on Windows) no longer collapse into a single row. Migration is automatic
on first dashboard launch; legacy v1 rows are dropped and repopulated
within seconds by the next live-scan or run. On macOS this matters less
than on Windows (Apple bundle ids are already unique per app), but the
schema is unified — `ascendo build-inventory` benefits from the same
upsert-only post-run flush that prevents stale rows after uninstall.

### Suggestions AI integration
Open **Settings → AI providers**, pick one of 6 providers (anthropic /
openai / openrouter / ollama / google / lm_studio), paste API key (or
`base_url` for ollama/lm_studio), Test connection, pick model, Save.
The **Suggestions** tab in the sidebar then prepends 1-3 AI-generated
cards on top of the rule-based ones. AI failures (rate limit, network)
fall back to rule-based silently — the operator never sees a 500.

```bash
# Smoke-test from the CLI (no UI):
curl http://127.0.0.1:8765/suggestions/library | jq '.ai, .ai_generated_count, .count'
# Expected: {"provider":"anthropic","model":"claude-3-5-sonnet-...","ok":true,"count":N}
#           ai_generated_count: 1..3
#           total cards count
```

### Schedule tab — LaunchdScheduler driver
The SPA sidebar now has a **Schedule** tab between Hosts and Settings.
Install / list / trigger / remove launchd LaunchAgents through one UI.
DSL: `DAILY HH:MM` · `WEEKLY DAY HH:MM` · `MONTHLY HH:MM` ·
`HOURLY HH:MM` · `MINUTE N`. Files land at
`~/Library/LaunchAgents/dev.ascendo.<name>.plist` +
`~/Library/Application Support/Ascendo/schedules/<name>.json`.

```bash
# Smoke-test the backend without the UI:
curl http://127.0.0.1:8765/scheduler/list

# Install a daily quick run via the API:
curl -X POST http://127.0.0.1:8765/scheduler/install \
  -H 'Content-Type: application/json' \
  -d '{"name":"ascendo-daily","expression":"DAILY 03:00","profile":"quick","enabled":true}'

# Trigger once now:
curl -X POST http://127.0.0.1:8765/scheduler/trigger \
  -H 'Content-Type: application/json' -d '{"name":"ascendo-daily"}'

# Remove:
curl -X POST http://127.0.0.1:8765/scheduler/remove \
  -H 'Content-Type: application/json' -d '{"name":"ascendo-daily"}'
```

The Help tab now has a macOS-specific **"12 · Recent additions"** +
**"13 · Operator tooling"** block with 14 expandable details documenting
WebManager, Omaha protocol, release_feed extensions, mas CVE rule,
softwareupdate -R rule, Touch ID, Time Machine snapshots, LaunchdScheduler,
MacElevation, validate harness, Suggestions AI, Schedule tab, and the
inventory dedup schema.

## 14 · One-liner sanity check

If anything seems off, run this first — exits 0 only when CLI + dashboard
+ all 5 phases × 6 categories produce real sidecars and the SPA assets
serve correctly. Stage 12 exercises the launchd scheduler round-trip
(install + list + trigger + remove a throwaway agent); Stage 13 exercises
the M5.6 / M5.7 web app updater across all 5 phases (37 apps in shipped
registry):

```bash
bash bin/validate-macos.sh
# Expected: ALL CHECKS PASSED. (44/44)
```

Anything red names the failed component (CLI, manager, sidecar parse,
dashboard endpoint, SPA asset, scheduler) so you know exactly where to
start.
