# Ascendo on Linux — Quickstart (operator)

One-screen guide: install → open the dashboard → see what's installed →
update apt / snap / flatpak / brew / npm / pip / drivers / web. Tested
on Ubuntu 24.04 LTS (mk-uP5520, Dell Precision 5520) — bash 5.2,
Python 3.14, apt 2.8, snapd 2.75, flatpak 1.14, Linuxbrew 5.1.
Compatible with Ubuntu 22.04+ and Debian 12+; Pop!_OS / Mint should
work via the `ID_LIKE=ubuntu` ancestor match.

> **As of v0.6.1, Ubuntu adapter is at full feature-parity with macOS.**
> All 5 IAdapter capabilities declared (`PACKAGE_MANAGEMENT |
> INVENTORY | SNAPSHOTS | SCHEDULING | ELEVATION`), 8 IPackageManager
> implementations (apt / snap / brew / npm / pip / flatpak / drivers /
> **web**), 13-component health rollup. End-to-end smoke
> `bin/validate-ubuntu.sh` is 23/23 PASS. See
> [`LINUX_TESTING.md`](LINUX_TESTING.md) for the full test matrix.

For the deep architecture (legacy bash `update-all.sh` + Python
`UbuntuAdapter` shim) and per-platform parity matrix see
[`docs/PLATFORM_STATUS.md`](docs/PLATFORM_STATUS.md).

For the cross-OS three-interface walkthrough (CLI vs web vs desktop)
see [`USER_GUIDE.md`](USER_GUIDE.md).

---

## 1 · Install (≈ 3 min, one-time)

**Recommended — one-liner from any terminal:**

```bash
curl -fsSL https://raw.githubusercontent.com/KasprowiczM/ascendo/main/install.sh | bash
```

That single command:
- Detects your OS (`/etc/os-release` → ubuntu / debian / fedora / arch /
  pop / mint via `ID_LIKE` ancestor match)
- Asks for language (en/pl) and install profile (cli / web / desktop)
- Verifies network connectivity + ≥1 GB free disk
- Detects + reports missing Python 3.11+ (auto-installs via `apt`,
  `dnf`, or `pacman` depending on distro)
- Detects + warns on locked dpkg (`fuser /var/lib/dpkg/lock`)
- Auto-installs missing system deps (`git`, `curl`, `jq`, `python3-venv`)
- Clones to `~/.local/share/ascendo`
- Sets up a venv-equivalent + editable pip install of core + Ubuntu adapter
- Symlinks `ascendo` to `~/.local/bin/ascendo` (auto-detects `$SHELL`
  for PATH instructions in bash / zsh / fish)
- Runs `ascendo doctor` self-test; bails loudly on non-zero

Optional flags: `--reinstall` (wipe + rebuild), `--update` (skip clone,
just upgrade), `--verbose` (trace every command), `--non-interactive`
(CI mode). Env-var overrides: `ASCENDO_LANG`, `ASCENDO_PROFILE`,
`ASCENDO_HOME`, `ASCENDO_NONINTERACTIVE`, `ASCENDO_REPO_URL`,
`ASCENDO_BRANCH`. `HTTPS_PROXY` / `http_proxy` are honoured natively.

**To update an existing install:**
```bash
curl -fsSL https://raw.githubusercontent.com/KasprowiczM/ascendo/main/update.sh | bash
```
(`git pull --ff-only`, refresh editable installs, restart any running
dashboard, print version delta.)

**Manual / dev path** (already-cloned repo):

```bash
cd ~/Dev_Env
git clone https://github.com/KasprowiczM/ascendo.git
cd ascendo
pip install --user -e ./core
pip install --user -e ./adapters/ubuntu --no-deps
pip install --user 'fastapi>=0.111' 'uvicorn[standard]' httpx
python3 -m ascendo doctor                  # 10-component self-test
```

### Architecture note — the bridge to legacy bash

The Linux adapter is a **Python scaffold over the mature legacy bash
scripts** in the repo root (`update-all.sh`, `lib/`, `scripts/`). The
`UbuntuAdapter` discovers them via env-var IPC; sidecars are emitted
in the legacy `ubuntu-aktualizacje/v1` schema and auto-translated to
`ascendo/v1` by `parse_sidecar()` in core. Override the legacy script
location via `$ASCENDO_UBUNTU_REPO_ROOT`.

The 7 managers that wrap them are: **apt → snap → brew → npm → pip →
flatpak → drivers** (canonical run order, drivers last because of
reboot semantics).

## 2 · Open the dashboard

Four paths — pick one:

| | Where | What it does |
|-|-------|--------------|
| **A** *(recommended)* | `ascendo web start` | Detached background dashboard with pidfile tracking, **opens browser automatically**. Pair with `ascendo web stop`, `restart`, `status`. Idempotent. |
| **B** | `python3 -m ascendo dashboard` | Backend in the foreground; visit `http://127.0.0.1:8765` in your browser. Useful for debugging. |
| **C** | `bash systemd/user/install-dashboard.sh` | Installs a `systemd --user` unit so the dashboard starts on every login. |
| **D** | `ascendo-launch` (after `install-dashboard.sh`) | Opens the SPA in your default browser; `.desktop` entries are also installed in your application menu. |

The first time you launch any of these, the **Apps inventory** is
populated by `adapters/ubuntu/scripts/inventory/list.sh` (≈ 5–15 s on
a typical box; ~2000 dpkg packages on Ubuntu Desktop). Click the
Categories tab to see the per-source breakdown.

## 3 · See what's installed (Categories tab)

The Categories tab shows one row per source:

| Source | Where the data comes from | What it represents |
|--------|---------------------------|--------------------|
| **apt** | `dpkg-query -W` filtered to `install ok installed` | All Debian packages installed via `apt` / `apt-get` / `aptitude` (~2000 entries on Ubuntu Desktop) |
| **snap** | `snap list` | Canonical's universal package format — Firefox, Chromium, VS Code if installed via snap, etc. |
| **flatpak** | `flatpak list --columns=application,version` | Flathub / GNOME Software apps |
| **brew** | `brew list --formula --versions` + `brew list --cask --versions` (Linuxbrew at `/home/linuxbrew/.linuxbrew`) | Homebrew on Linux — formulae + casks, just like macOS |
| **npm** | `npm list -g --depth=0 --json` | Global npm CLIs (claude-code, codex-cli, prettier, etc.) |
| **pip** | `pip3 list --format=json` | Global Python tools (ruff, black, pipx, uv, etc.) |
| **drivers** | `fwupdmgr get-devices` + `apt list --installed nvidia-driver-*` | Firmware (LVFS) + NVIDIA proprietary driver packages |
| **inventory** | All of the above merged | Complete cross-source enumeration via single `list.sh` invocation |

Click a row to expand it and see every package with installed/candidate
version + status pill.

## 4 · Check for updates

Each category row has its own 5-phase buttons:

```
check  →  plan  →  apply  →  verify  →  cleanup
```

You almost always want **`check`** first — it's read-only, takes a few
seconds, and surfaces every available update without changing anything.
The Run Center tab pops open showing live progress; sidecars stream
in via SSE as each phase finishes.

| You want to … | Click |
|---------------|-------|
| Find new **APT / Debian package** updates | apt → check |
| Find new **snap** updates | snap → check |
| Find new **flatpak** updates | flatpak → check |
| Find new **Homebrew on Linux** updates (formulae + casks) | brew → check |
| Find new **npm global CLI** updates | npm → check |
| Find new **Python global tool** updates | pip → check |
| Find new **firmware / NVIDIA driver** updates | drivers → check |

## 5 · Apply updates

Click **`apply`** on the source you want to update. Every apply gates
on a confirmation modal — type the literal word `apply` to proceed.

Apply phases that need elevation (`apt`, `snap`, `flatpak`, `drivers`)
prompt once for sudo. The dashboard caches the password in memory and
forwards it via `SUDO_ASKPASS` to all child processes — never written
to disk, never logged. `brew` / `npm` / `pip` run as the user (no sudo).

| Update target | What to click | Notes |
|---------------|---------------|-------|
| **APT packages** | apt → apply | `sudo apt-get upgrade -y`. Idempotent; safe to re-run. |
| **snap** | snap → apply | `sudo snap refresh`. Snaps auto-update by default; `apply` forces an immediate refresh. |
| **flatpak** | flatpak → apply | `flatpak update -y` (user-mode by default; `--system` if installed system-wide). |
| **Homebrew** | brew → apply | User-mode; runs as the user that owns `${BREW_PREFIX}/Cellar`. |
| **npm globals** | npm → apply | No sudo; respects `NPM_CONFIG_PREFIX`. Reads `config/npm-globals.list` if present. |
| **pip globals** | pip → apply | No sudo. Falls back to `--break-system-packages` on PEP 668 (Ubuntu 24.04 system Python); `--user` for user-site installs. |
| **NVIDIA + firmware** | drivers → apply | NVIDIA APT packages are **held by default** (safe). Pass `--nvidia` to opt in. `fwupdmgr refresh + get-updates`; firmware install requires `--firmware` confirmation. |

After every apply phase, the dashboard invalidates the inventory cache
and a banner appears at the top if a reboot is required (e.g. kernel
upgrade, NVIDIA driver swap).

## 6 · From the CLI (no dashboard needed)

```bash
python3 -m ascendo doctor                                      # 10-component health snapshot
python3 -m ascendo run --category apt      --phase check       # ≈ 5 s
python3 -m ascendo run --category snap     --phase check
python3 -m ascendo run --category flatpak  --phase check
python3 -m ascendo run --category brew     --phase check
python3 -m ascendo run --category npm      --phase check
python3 -m ascendo run --category pip      --phase check
python3 -m ascendo run --category drivers  --phase check       # firmware + NVIDIA
python3 -m ascendo run --category apt      --phase apply       # mutating; sudo
python3 -m ascendo runs list -n 5                              # last 5 runs
python3 -m ascendo runs json <run-id> --pretty | jq .summary
```

Or use the legacy master orchestrator directly:

```bash
./update-all.sh                          # full profile, all categories
./update-all.sh --profile quick          # read-only check across all sources (~15 s)
./update-all.sh --profile safe           # full 5-phase pipeline minus drivers
./update-all.sh --only apt --phase check
./update-all.sh --dry-run                # check + plan only, no mutation
./update-all.sh --nvidia                 # opt-in NVIDIA APT upgrade
./update-all.sh --no-drivers --no-notify
./update-all.sh --snapshot               # take timeshift/etckeeper snapshot before apt apply
```

Exit codes the run command emits:

| Code | Meaning |
|------|---------|
| `0`  | success |
| `1`  | warnings only (e.g. some snaps deferred) |
| `2`  | bad input (e.g. unknown category) |
| `10` | precondition failed (missing tool / no sudo) |
| `30` | hard failure during apply |
| `75` | success, but **reboot required** |

## 7 · Update the system right now (apt + snap + flatpak)

Most direct path:

```bash
# 1. Read-only sweep across all sources
./update-all.sh --profile quick

# 2. Inspect the most recent sidecars
ls -1 ~/.local/state/ascendo/runs/ | tail -1
# (or logs/runs/ in legacy mode)

# 3. Take a snapshot before mutating (timeshift recommended)
./update-all.sh --snapshot --profile safe   # snapshot + full apply minus drivers

# 4. If exit code 75 (reboot required), reboot manually:
sudo reboot

# 5. After reboot, verify everything landed:
python3 -m ascendo run --category apt --phase verify
python3 -m ascendo run --category snap --phase verify
python3 -m ascendo run --category flatpak --phase verify
```

## 8 · Run as a systemd service (recommended)

Want the dashboard to be always on — no terminal window required, ready
the moment you log in? Install it as a **systemd user unit**. No root
required (it's a `--user` unit; runs in your login session, not as a
system service).

```bash
bash systemd/user/install-dashboard.sh
```

That single script:
- Bootstraps the venv if missing (PEP 668 safe)
- Installs the user unit at `~/.config/systemd/user/ubuntu-aktualizacje-dashboard.service`
- Runs `systemctl --user daemon-reload && enable --now`
- Drops two `.desktop` entries into `~/.local/share/applications/` so
  Ascendo appears in your application menu (GNOME / KDE / XFCE)
- Installs the `ascendo-launch` shim into `~/.local/bin/`
- Verifies the dashboard is listening on `127.0.0.1:8765`

Inspect / control:

```bash
systemctl --user status ubuntu-aktualizacje-dashboard.service
systemctl --user restart ubuntu-aktualizacje-dashboard.service
journalctl --user -u ubuntu-aktualizacje-dashboard.service -f
systemctl --user disable --now ubuntu-aktualizacje-dashboard.service   # uninstall
```

For **scheduled runs** (nightly safe-profile sweep), the legacy
`scripts/scheduler/install.sh` writes a systemd `--user` timer:

```bash
bash scripts/scheduler/install.sh --calendar "Sun *-*-* 03:00:00" --profile safe
bash scripts/scheduler/install.sh --status
bash scripts/scheduler/install.sh --remove
```

(There's no `python3 -m ascendo schedule` CLI yet on Linux — the
`IScheduler` Python interface is not wired into `UbuntuAdapter`. Use
the bash script above directly. See
[`docs/PLATFORM_STATUS.md`](docs/PLATFORM_STATUS.md) for parity gaps.)

## 9 · Pre-apply snapshots (timeshift / etckeeper)

The legacy bash pipeline supports two snapshot providers:

| Provider | Scope | Install |
|----------|-------|---------|
| **timeshift** (preferred) | Whole filesystem (`@`/`@home` btrfs subvolumes, or rsync mode) | `sudo apt install timeshift` |
| **etckeeper** (fallback) | `/etc` only (cheap, fast, git-based) | `sudo apt install etckeeper` |

Take a snapshot before apply:

```bash
# Manual:
bash scripts/snapshot/create.sh "before-experiment"

# Automatic before apt apply:
./update-all.sh --snapshot --profile safe

# List snapshots:
bash scripts/snapshot/list.sh

# Restore (timeshift only, requires GUI / interactive):
sudo timeshift --restore --snapshot <name>
```

The `python3 -m ascendo snapshot` CLI is **not yet wired** on Linux
(no `ISnapshot` impl in `UbuntuAdapter`). Use the bash scripts above
directly — `update-all.sh --snapshot` is fully functional.

## 10 · Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| `error: externally-managed-environment` (PEP 668) | Ubuntu 24.04 system Python | Use `pip install --user --break-system-packages` (already in `install.sh`) |
| `Could not get lock /var/lib/dpkg/lock` | Another apt / unattended-upgrades running | `sudo fuser /var/lib/dpkg/lock` to identify, wait or kill, re-run |
| `ascendo doctor` shows `brew: unavailable` | Linuxbrew not installed | `/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"` |
| `ascendo doctor` shows `flatpak: unavailable` | Not a default install on minimal Ubuntu | `sudo apt install flatpak && flatpak remote-add --if-not-exists flathub https://flathub.org/repo/flathub.flatpakrepo` |
| Dashboard not listening on :8765 after `install-dashboard.sh` | venv missing or unit failed | `journalctl --user -u ubuntu-aktualizacje-dashboard.service -e` |
| NVIDIA upgrade silently skipped | NVIDIA packages are held by default for safety | `./update-all.sh --nvidia` to opt in (re-test after; rollback via timeshift if it breaks X) |
| `~/.local/bin` not on `$PATH` | Default not in some shells | Add `export PATH="$HOME/.local/bin:$PATH"` to `~/.profile` / `~/.zshrc` / `~/.config/fish/config.fish` |
| `python3 -m ascendo schedule` says "not implemented" | Linux scheduler CLI not wired yet | Use `bash scripts/scheduler/install.sh` (systemd timer) directly |
| Tauri build fails on `webkit2gtk` | Missing GTK dev headers | `sudo apt install libwebkit2gtk-4.1-dev libssl-dev libgtk-3-dev libayatana-appindicator3-dev librsvg2-dev` |
| MEGA APT repo is broken / 404 | Legacy `meganz.list` lingering | `update-all.sh` auto-migrates to canonical `megaio.sources` — re-run apt apply |
| brew cleanup permission denied | `${BREW_PREFIX}/Cellar` ownership drift | `update-brew.sh` / `scripts/brew/cleanup.sh` auto-fixes ownership and retries |

## 11 · Where everything lives

```
~/Dev_Env/Ascendo/
├── adapters/ubuntu/                       # Tier-1 Python adapter (scaffold)
│  ├── ascendo_ubuntu/                     # UbuntuAdapter + 7 managers
│  │   ├── adapter.py                      # capabilities, health_check, manager wiring
│  │   ├── inventory.py                    # UbuntuInventory (drives list.sh)
│  │   └── managers/
│  │       ├── apt.py
│  │       ├── snap.py
│  │       ├── brew.py
│  │       ├── npm.py
│  │       ├── pip.py
│  │       ├── flatpak.py
│  │       └── drivers.py
│  └── scripts/inventory/list.sh           # Cross-source enumerator (apt+snap+brew+npm+pip+flatpak)
├── scripts/                               # Legacy bash 5-phase scripts (the real implementation)
│  ├── apt/{check,plan,apply,verify,cleanup}.sh
│  ├── snap/…                              # Same 5 phases per source
│  ├── flatpak/…
│  ├── brew/…
│  ├── npm/…
│  ├── pip/…
│  ├── drivers/…                           # NVIDIA + fwupd
│  ├── inventory/apply.sh                  # APPS.md generator
│  ├── snapshot/{create,list,restore}.sh   # timeshift / etckeeper
│  └── scheduler/install.sh                # systemd --user timer
├── lib/                                   # Shared bash + Python helpers
│  ├── orchestrator.sh                     # 5-phase runner (legacy)
│  ├── common.sh                           # logging, sudo, summary counters
│  ├── detect.sh                           # OS / hardware / package-manager detection
│  ├── repos.sh                            # APT repo idempotent add
│  ├── json.sh                             # bash wrapper around _json_emit.py
│  ├── _json_emit.py                       # Python sidecar emitter
│  └── …
├── update-all.sh                          # Master orchestrator (legacy entrypoint)
├── core/ascendo/                          # OS-agnostic CLI + orchestrator + REST API
├── ui/desktop-tauri/                      # Tauri 2.x native shell (Rust + WebKitGTK)
├── app/frontend/                          # The SPA the desktop shell renders
├── systemd/user/                          # User-level dashboard service
│  ├── install-dashboard.sh
│  └── ubuntu-aktualizacje-dashboard.service
├── share/                                 # XDG desktop integration
│  ├── applications/                       # `.desktop` entries
│  ├── icons/hicolor/scalable/apps/        # ascendo.svg
│  └── bin/ascendo-launch                  # browser-open shim
├── config/                                # `*.list` files for tracked tools
│  ├── npm-globals.list
│  └── pip-globals.list
└── ~/.local/state/ascendo/runs/<uuid>/    # All sidecars + per-phase logs
   # (or `logs/runs/<uuid>/` in legacy mode)
```

## 12 · .deb packaging (good news: no signing tax)

Unlike macOS DMGs and Windows MSI/EXE, **Ubuntu .deb files work without
signing or notarization**. dpkg's signing concept is for apt
*repositories* (the `Release` file signed by the maintainer's GPG key),
not standalone .deb downloads. A user who downloads
`ascendo_<version>_amd64.deb` from GitHub Releases and runs:

```bash
sudo apt install ./ascendo_0.7.0_amd64.deb
```

…gets a clean install with NO warning, override, or signing prompt.
apt resolves dependencies (Python ≥3.11, git, curl, jq) against the
user's package index automatically.

For contributors who want to build the .deb locally:

```bash
bash packaging/build-deb.sh
# Produces: dist/ascendo_<version>_amd64.deb
```

So Ubuntu is the **easiest** of the three OSes to ship publicly — no
$99-$700/year signing tax. See
[`docs/DESKTOP_INSTALLER_STATUS.md`](docs/DESKTOP_INSTALLER_STATUS.md)
for the cross-platform comparison.

Note: the current public-release path on Linux remains the
`curl … install.sh \| bash` one-liner from §1 (same as macOS) for
consistency with the cross-platform install experience. The .deb is
useful when you want apt to manage dependencies for you, or when
shipping into restricted environments without curl access.

## 13 · AI Tools chat (Sesja 70 / v0.5.0)

The Suggestions tab grew a new chat surface that combines Sesja 67's
rule-based + AI-augmented quick cards with a conversational LLM-backed
diagnosis flow. The URL path stays `#suggest` so any external bookmarks
keep working; only the visible label flips to **"AI Tools" / "Narzędzia AI"**
via i18n.

### Pick a backend

Ascendo resolves the first available backend in this order:

1. **claude** — Claude Code CLI (vendor docs)
2. **gemini** — Gemini CLI
3. **codex** — Codex CLI
4. **opencode** — open-source CLI
5. **API key fallback** — Settings → AI configures anthropic / openai /
   openrouter / ollama / google / lm_studio.

The backend pill at the top right of the AI Tools tab shows which
backend is active. If it reads "No backend configured", install one of
the CLIs above OR configure an API key in Settings → AI.

### 10 starter prompts (grouped)

The right rail is a "Prompt library" with 10 curated starters across
three groups (Diagnostics / Setup / Customize). Each prompt has EN+PL
titles and auto-injects the relevant context (latest failed sidecar,
outdated apps, REPORT.md, etc.) when clicked.

### Action chips

The LLM emits fenced `ascendo-action` JSON blocks that render as
clickable chips below the assistant message. Clicks proxy through
`POST /ai/chat/action` which validates against a 12-entry whitelist:
`run_check`, `run_plan`, `run_apply`, `run_verify`, `run_cleanup`,
`install_schedule`, `remove_schedule`, `trigger_schedule`,
`refresh_inventory`, `add_web_override`, `edit_skip_list`, `open_view`.

### Chat history is local-only

Conversations + messages persist to `~/.ascendo/chats.db` (SQLite, mode
0600, per-host). The file is in the dev-sync HARD_EXCLUDE list so it
never leaves the machine.

To wipe: `rm ~/.ascendo/chats.db` — dashboard recreates on next launch.

### Smoke-test the chat surface

```bash
ascendo web start
curl -s http://127.0.0.1:8765/ai/chat/backends | python3 -m json.tool
curl -s http://127.0.0.1:8765/ai/chat/library  | python3 -m json.tool

CID=$(curl -s -X POST -H 'Content-Type: application/json' -d '{}' \
  http://127.0.0.1:8765/ai/chat/conversations | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
TURN=$(curl -s -X POST -H 'Content-Type: application/json' \
  -d "{\"conversation_id\":\"$CID\",\"message\":\"hello\",\"locale\":\"en\"}" \
  http://127.0.0.1:8765/ai/chat | python3 -c "import sys,json; print(json.load(sys.stdin)['turn_id'])")
curl -N http://127.0.0.1:8765/ai/chat/stream/$TURN
```

`bin/validate-ubuntu.sh` Stage 14 covers all of the above (prompt
library, action whitelist size, backend resolver, ChatsDB writes,
i18n parity, and three live dashboard endpoints).

## 14 · One-liner sanity check

If anything seems off, run this first:

```bash
./update-all.sh --profile quick --no-notify
python3 -m ascendo doctor
```

Quick profile is a read-only sweep across every source (~15 s); doctor
prints the 10-component health table. Anything red names the failed
component (apt / snap / brew / npm / pip / flatpak / fwupd / bash /
ascendo_lib / ascendo_scripts) so you know exactly where to start.

For a fuller validation pipeline (mirroring `bin/validate-macos.sh` /
`bin/validate-windows.ps1`) see
[`docs/PLATFORM_STATUS.md`](docs/PLATFORM_STATUS.md) — a Linux
equivalent (`bin/validate-linux.sh`) is on the parity backlog.
