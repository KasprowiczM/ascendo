# Ascendo on Linux — Testing Guide

Single-page, copy-paste-ready guide for testing Ascendo end-to-end on
a real Linux machine. Tested on **Ubuntu 24.04 LTS, mk-uP5520 (Dell
Precision 5520), bash 5.2, Python 3.14, apt 2.8, snap 2.75, flatpak
1.14, Linuxbrew 5.1**. As of v0.6.1 the Ubuntu adapter is full
feature-parity with macOS — see [`PLAN.md`](PLAN.md) for the milestone
banner.

---

## Want a clickable Linux app? (skip the CLI)

Today's release ships the CLI + Web profiles by default; the desktop
shell is only built locally for contributors (no signing yet). To get
the dashboard in your browser without typing PYTHONPATH on every
shell:

```bash
cd ~/Dev_Env/Ascendo
pip install --break-system-packages -e core/ -e adapters/ubuntu/
ascendo dashboard --port 18765 &
xdg-open http://127.0.0.1:18765
```

`pip install -e` is the editable install path — `git pull` updates
take effect immediately. `--break-system-packages` is required because
Linuxbrew's Python 3.14 (and Ubuntu 24.04's system Python) are marked
externally-managed.

The rest of this document is the deeper test surface.

---

## TL;DR — six commands

```bash
# 1. Clone (or pull) the repo
cd ~/Dev_Env
git clone https://github.com/KasprowiczM/ascendo.git    # first time
cd Ascendo
git pull                                                 # subsequent

# 2. Install (editable; no PYTHONPATH after this)
pip install --break-system-packages -e core/ -e adapters/ubuntu/

# 3. Read-only validation: 10 stages, 23 checks across all 7 categories +
#    dashboard sync + async + SSE + scheduler/snapshot/elevation
bash bin/validate-ubuntu.sh                # ALL CHECKS PASSED. (23/23)

# 4. (Optional) Real apply — actually upgrades packages where outdated
ascendo run -c brew,npm,pip,flatpak,web -p apply    # safe (no sudo)
# Or with sudo wired via askpass for apt/snap:
ascendo run -c apt,snap,brew,npm,pip,flatpak,web -p apply,verify,cleanup

# 5. Browser-visible dashboard
ascendo dashboard --port 18765
# open http://127.0.0.1:18765 in a browser
```

That's the operator surface. The rest of this doc is reference.

---

## 1. Prerequisites

| Component | Required | Verify |
|---|---|---|
| Python 3.11+ | yes | `python3 --version` |
| pip | yes | `pip3 --version` |
| bash 5+ | yes | `bash --version` |
| git | yes | `git --version` |
| systemd (user session) | scheduler only | `systemctl --user is-system-running` |
| timeshift | snapshots only | `which timeshift` |
| sudo | apt/snap apply | `sudo -V` |
| apt / dpkg | apt category | `apt-get --version` |
| snap | snap category | `snap version` |
| flatpak | flatpak category | `flatpak --version` |
| Linuxbrew | brew category | `brew --version` |
| npm | npm category | `npm --version` |
| pip3 | pip category | `pip3 --version` |
| fwupd | drivers category | `fwupdmgr --version` |
| curl + jq | web category | `curl --version; jq --version` |

Missing any of these? `apt install` (apt-managed) or `winget`-equivalent
for distro-specific tooling. Inventory enumeration **degrades
gracefully** — categories without a backing tool are silently skipped
with an info message in the sidecar.

---

## 2. Install

```bash
cd ~/Dev_Env/Ascendo
pip install --break-system-packages -e core/ -e adapters/ubuntu/
```

Verify:

```bash
ascendo doctor
```

Expected output:

```
adapter: ubuntu (Ubuntu / Debian) tier=1
capabilities: AdapterCapability.PACKAGE_MANAGEMENT|INVENTORY|SNAPSHOTS|SCHEDULING|ELEVATION
  apt        ok: apt 2.8.3 (amd64)
  brew       ok: Homebrew 5.1.11
  bash       ok: GNU bash, version 5.2.21(1)-release ...
  flatpak    ok: Flatpak 1.14.6
  fwupd      ok: ...
  npm        ok: 11.13.0
  pip        ok: pip 26.1
  snap       ok: snap 2.75.2
  sudo       ok: Sudo version 1.9.15p5 + askpass helper
  systemctl  ok: running
  timeshift  ok: <version>  (or "degraded: timeshift not installed (snapshots unavailable; install: sudo apt install timeshift)")
  ascendo_lib    ok: 14 module(s)
  ascendo_scripts ok
```

---

## 3. Validate (read-only)

```bash
bash bin/validate-ubuntu.sh
```

What it checks (in order — 10 stages, 23 checks):

1. **CLI** — `ascendo --help / version / doctor` all exit 0;
   doctor selects the ubuntu adapter
2. **Five-phase brew_formula contract** — check / plan / apply
   --dry-run / verify / cleanup --dry-run each produces a sidecar with
   `schema=ascendo/v1`, `phase=<expected>`, `category=brew_formula`
3. **All 6 categories check phase** — apt + snap + brew + npm + pip +
   flatpak; expect 6 sidecars + overall=success
4. **plan + verify + cleanup across 6 categories** — full phase contract
5. **Inventory `list.sh`** — enumerates ≥50 packages (got **2579** on
   this host; was 2539 before the Sesja 55 npm/pip parser fix)
6. **Dashboard** — `/version`, `/health`, `POST /runs/async` + status
   poll, clean teardown
7. **ISnapshot via timeshift** — `TimeshiftSnapshot.list()` works
   (returns 0 snapshots if timeshift not installed; degrades cleanly)
8. **IScheduler via systemd** — bash driver `--action list` smoke
9. **IElevation (sudo askpass)** — askpass helper round-trip +
   `LinuxElevation` lifecycle
10. **WebManager** — `web` category check produces a sidecar (0–5 items
   depending on what AppImages discovery finds locally)

Final line on success: **`ALL CHECKS PASSED. (23/23)`**.

Useful flags:

```bash
bash bin/validate-ubuntu.sh --port 18999       # use a different port
bash bin/validate-ubuntu.sh --skip-dashboard   # skip the dashboard stages
bash bin/validate-ubuntu.sh --skip-scheduler   # skip live systemd timer install/remove
bash bin/validate-ubuntu.sh --skip-web         # skip web manager check
```

---

## 4. (Optional) Real apply — first real mutation

**Safest first apply**: only the user-space categories (no sudo
needed):

```bash
ascendo run -c brew,npm,pip,flatpak -p apply --runs-dir /tmp/asc-test
```

Inspect the produced REPORT.md:

```bash
ls /tmp/asc-test/*/
cat /tmp/asc-test/*/REPORT.md
```

**Full apply with sudo** (apt/snap upgrades, requires sudo via askpass
or interactive password):

```bash
# Wire askpass via dashboard
ascendo dashboard --port 18765 &
curl -X POST http://127.0.0.1:18765/elevation/auth \
     -H "Content-Type: application/json" \
     -d "{\"password\":\"$(read -rsp 'sudo: ' p && echo $p)\"}"

# Then trigger the apply
curl -X POST http://127.0.0.1:18765/runs/async \
     -H "Content-Type: application/json" \
     -d '{"phases":["check","plan","apply","verify","cleanup"],
          "categories":["apt","snap","brew","npm","pip","flatpak","web"]}'

# Or from CLI with UPDATE_ALL_SUDO_READY (skips re-prompt; assumes
# sudo timestamp already warm):
sudo -v
UPDATE_ALL_SUDO_READY=1 ascendo run \
    -c apt,snap,brew,npm,pip,flatpak,web \
    -p check,plan,apply,verify,cleanup
```

The dashboard's askpass cache means you type the sudo password ONCE
per dashboard session; every subsequent apply uses the cached
password via `SUDO_ASKPASS`.

---

## 5. Inspect inventory

```bash
sqlite3 ~/.ascendo/inventory.db \
   "SELECT category, COUNT(*) FROM inventory_items GROUP BY category;"
```

Expected on a typical Ubuntu 24.04 host:

```
apt|2476           (dpkg-query enumerated)
brew_formula|47    (brew list --formula)
npm|4              (npm list -g --depth=0)
pip|36             (pip3 list --format=json)
snap|16            (snap list)
```

Per-package details:

```bash
sqlite3 ~/.ascendo/inventory.db \
   "SELECT name, installed, candidate, status
      FROM inventory_items WHERE category='snap';"
```

If your DB is empty or shows wrong versions, force a rebuild:

```bash
rm -f ~/.ascendo/inventory.db
# Next dashboard request triggers a fresh enumeration via list.sh
ascendo dashboard --port 18765 &
sleep 3
curl -X POST http://127.0.0.1:18765/inventory/refresh
```

---

## 6. Browser-visible dashboard

```bash
ascendo dashboard --port 18765
```

Visit:

- **`http://127.0.0.1:18765/`** — full SPA with Categories / Apps /
  Run Center / History / Logs / etc.
- **`http://127.0.0.1:18765/version`** — adapter info JSON
- **`http://127.0.0.1:18765/health`** — 13-component health rollup
- **`http://127.0.0.1:18765/inventory/summary`** — totals donut
- **`http://127.0.0.1:18765/inventory/snap`** — per-category items list
- **`http://127.0.0.1:18765/docs`** — Swagger UI

Drive a run from the SPA: Categories tab → click any category → 5
phase buttons (check/plan/apply/verify/cleanup) appear → click Apply →
type the literal word `apply` in the confirmation modal → SSE stream
shows live activity in Run Center.

---

## 7. Background dashboard (always-on)

```bash
ascendo web start                 # detached, browser auto-opens
ascendo web status                # check pid + port
ascendo web restart               # graceful restart
ascendo web stop                  # graceful shutdown
```

For a real systemd user service that auto-starts on login:

```bash
bash systemd/user/install-dashboard.sh
# Then:
systemctl --user status ascendo-dashboard.service
systemctl --user restart ascendo-dashboard.service
systemctl --user disable --now ascendo-dashboard.service   # uninstall
```

---

## 8. Schedule recurring runs (systemd user timers)

```bash
ascendo schedule install --name nightly --calendar "DAILY 03:00" \
                         --profile safe
ascendo schedule list
ascendo schedule trigger --name nightly        # run now without waiting
ascendo schedule remove  --name nightly
```

Under the hood: writes
`~/.config/systemd/user/ascendo-nightly.{service,timer}` and runs
`systemctl --user daemon-reload && enable --now`. Sidecar JSON at
`~/.local/share/ascendo/schedules/nightly.json`.

---

## 9. Pre-apply snapshots (timeshift)

```bash
sudo apt install timeshift                     # one-time
sudo timeshift --create --comments "Before risky upgrade"

# Or via Ascendo's wrapper (uses sudo askpass when wired):
ascendo snapshot create -m "Before risky upgrade"
ascendo snapshot list

# Restore (interactive, requires GUI):
sudo timeshift --restore --snapshot <name>
```

ISnapshot's `restore()` is intentionally NOT exposed by Ascendo — it's
destructive and requires a recovery shell. The Python wrapper supports
`create()` and `list()` only.

---

## 10. Run as a Windows-style background service

(See section 7 above — the systemd user-unit path is the equivalent.)

---

## 11. Troubleshooting (Sesja 55-68 fixes recap)

| Symptom | Likely cause | Quick check |
|---------|--------------|-------------|
| `safe update hangs on apt` for 10+ minutes (heartbeat: `>>> apt apply still running (Ns elapsed)` forever) | Pre-Sesja-68 bug — keepalive subshell inherited parent's stdout/stderr pipes; Python's `subprocess.run(capture_output=True)` blocked forever on pipe EOF. apt apply.sh additionally overwrote common.sh's trap chain, dropping the keepalive killer. Pull main; subshell now redirects stdio to /dev/null AND apt's custom trap kills keepalive. | `grep -F '</dev/null >/dev/null 2>&1 &' lib/common.sh` should print 2 lines |
| `snap apply produced no sidecar` (status=failed, items=0 in synthesized fallback) | Pre-Sesja-68 bug — chained EXIT trap had `kill $PID 2>/dev/null; finalize`. When keepalive PID already dead (TTY-less sudo cache fails on first iteration), kill returns 1 + set -e aborts trap before finalize runs. Pull main; trap now wraps kill with `\|\| true` + keepalive uses `sudo -A -v` for proper askpass refresh. | `grep -F 'kill ${SUDO_KEEP_ALIVE_PID} 2>/dev/null \|\| true' lib/common.sh` should print 2 lines |
| `npm/pip categories show 0 items` in inventory | Pre-Sesja-55 bug — bash heredoc parser error in `inventory/list.sh`. Pull main + rebuild: `rm ~/.ascendo/inventory.db && ascendo dashboard` | `git log --oneline | head -10` should show `32db6f1 fix(ubuntu/inventory)` |
| `snap apply shows status=failed but stream log says it worked` | Pre-Sesja-55 bug — `require_sudo` was clobbering the json EXIT trap. Pull + retry. | `git log --oneline | grep require_sudo` should show `497b629` |
| `SPA candidate column empty after Quick check` | Pre-Sesja-55 bug — overlay didn't index by trailing name segment. Pull + retry. | `git log --oneline | grep check-overlay` should show `3c4ca99` |
| `Dashboard hangs on apply, no SSE updates` | Pre-Sesja-55 bug — bridge subprocess inherited stdin from parent, blocked on prompts. Pull + restart dashboard. | New runs should show `>>> <cat> <phase> still running (Ns elapsed)` heartbeats |
| `Ctrl+C on dashboard kills mid-flight apply with no sidecar` | Pre-Sesja-55 — process group bug. Now bridge runs in own session. Apply finishes; sidecar gets written. | `git log --oneline | grep start_new_session` (commit `cd827db`) |
| `brew apply takes 10+ minutes silently` | `--greedy` flag re-downloads every cask. Default no longer uses it. To force: `ASCENDO_BREW_GREEDY=1 ascendo run -c brew -p apply` | — |
| `ascendo: command not found` | Editable install missing. Re-run `pip install --break-system-packages -e core/ -e adapters/ubuntu/` | `which ascendo` should resolve |
| `apt check fails with exit 1` | apt source lists are >24h old. apt's `apt-get update` first; or accept the warn-class advisory (now mapped to status=success per contract.md) | `git log --oneline | grep "exit_code 1"` should show `11b6d69` |
| `flatpak shows 0 items but I have flatpaks installed` | flatpak list is empty? `flatpak list` returns nothing? Flatpak might be installed but with no remotes added. `flatpak remotes` to confirm. | — |
| `Dashboard reports degraded health` | Look at which component returned non-`ok`. Most common: `timeshift` if not installed (cosmetic, snapshots just unavailable). | `ascendo doctor` shows component-by-component status |

---

## 12. From the CLI (no dashboard needed)

```bash
ascendo doctor                                          # 13-component health snapshot
ascendo run --category apt --phase check                # ~3 sec, read-only
ascendo run --category snap --phase check               # ~5 sec
ascendo run -c apt,snap,brew,npm,pip,flatpak -p check   # all 6 in parallel
ascendo run --category windows_update --phase check     # NA on Linux (no manager)

# History inspection
ascendo runs list -n 5
ascendo runs json <run-id> --pretty | jq .summary

# Snapshots
ascendo snapshot create -m "Before bulk upgrade"
ascendo snapshot list

# Schedule a nightly safe-profile run
ascendo schedule install --name nightly --calendar "DAILY 03:00" --profile safe
```

Exit codes the run command emits:

| Code | Meaning |
|------|---------|
| `0`  | success |
| `1`  | warnings only (e.g. some upgrades deferred) |
| `2`  | bad input (e.g. unknown category) |
| `30` | hard failure during apply |
| `75` | success, but **reboot required** |
| `130` | interrupted by SIGINT (Ctrl+C); partial sidecar saved with `ASCENDO-INTERRUPTED` diagnostic |
| `143` | killed by SIGTERM; partial sidecar saved |

---

## 13. Update Ubuntu right now

Most direct path — five buttons in the dashboard:

1. **Categories → apt → check** — populates pending package list
2. Click the row to expand and review
3. **Categories → apt → apply** — type `apply` to confirm; sudo
   prompts ONCE (cached for the session); SSE stream shows each
   package downloading + dpkg-installing live
4. When done, look at the top banner — if it says **reboot required**
   (gnome-shell, kernel, libc), reboot at your convenience
5. After reboot, click **apt → verify** to confirm everything landed

CLI equivalent:

```bash
sudo -v
UPDATE_ALL_SUDO_READY=1 ascendo run --category apt --phase apply
```

---

## 14. Where everything lives

```
~/Dev_Env/Ascendo/
├─ adapters/ubuntu/
│  ├─ ascendo_ubuntu/         # Python: UbuntuAdapter, all 8 managers
│  │  ├─ adapter.py
│  │  ├─ inventory.py
│  │  ├─ snapshot.py          # TimeshiftSnapshot
│  │  └─ managers/
│  │     ├─ apt.py / snap.py / brew.py / npm.py / pip.py
│  │     ├─ flatpak.py / drivers.py / web.py
│  │     ├─ elevation.py      # LinuxElevation
│  │     ├─ scheduler.py      # SystemdScheduler
│  │     └─ _base.py          # BashPhaseManager bridge (signal-safe)
│  ├─ scripts/                # Per-category bash phase scripts
│  │  ├─ inventory/list.sh    # Enumerator (apt/snap/brew/npm/pip/flatpak)
│  │  ├─ scheduler/scheduler.sh
│  │  ├─ web/{check,plan,apply,verify,cleanup}.sh
│  │  └─ ...
│  ├─ lib/                    # Shared bash helpers + handlers/ for web
│  │  ├─ askpass_helper.sh
│  │  ├─ web_discovery.sh
│  │  └─ handlers/{appimage,github_release,release_feed,builtin}.sh
│  ├─ config/
│  │  └─ web_apps.toml        # Override registry for web apps
│  └─ tests/
├─ scripts/                    # Legacy bash for {apt,snap,brew,npm,pip,flatpak}/{check,plan,apply,verify,cleanup}.sh
├─ lib/                        # Legacy shared bash (json.sh, common.sh, etc.)
├─ core/ascendo/               # OS-agnostic CLI + orchestrator + REST API
│  ├─ cli/                     # `ascendo …` entry point
│  ├─ dashboard/               # FastAPI app, served at 127.0.0.1:8765
│  └─ orchestrator/            # 5-phase runner + JSON-v1 sidecar IO
├─ ui/desktop-tauri/           # Tauri 2.x native shell (Rust + WebKitGTK)
├─ app/frontend/               # The SPA the desktop shell renders
├─ bin/
│  ├─ install-dev.ps1          # Windows
│  ├─ install-dev-macos.sh     # macOS
│  ├─ install.sh               # Cross-OS one-liner installer (curl|bash)
│  ├─ launch-desktop.ps1       # Tauri dev launch (Win)
│  ├─ run-tag-release.ps1      # Win release flow
│  ├─ validate-ubuntu.sh       # ← you are here
│  ├─ validate-macos.sh
│  └─ validate-windows.ps1
├─ ~/.ascendo/                 # Per-user data
│  ├─ inventory.db             # SQLite cache
│  └─ runs/<uuid>/             # All sidecars + REPORT.md per run
└─ ~/.local/share/ascendo/     # Schedules + state
   └─ schedules/<name>.json
```

---

## 15. One-liner sanity check

If anything seems off, this is your starting point — exits 0 only when
CLI + dashboard + all 5 phases × 7 categories produce real sidecars
and the SPA serves them correctly:

```bash
bash bin/validate-ubuntu.sh        # ≈ 90 s; ALL CHECKS PASSED on green
```

Anything red there will name the failed component (CLI, manager,
sidecar parse, dashboard endpoint, asset, scheduler, snapshot,
elevation, web) so you know exactly where to start.

---

## 16. Reporting issues

If anything in this document doesn't work as described, paste:

1. The exact command you ran
2. The full output (especially `[FAIL]` lines + sidecar messages if any)
3. `python3 --version`, `bash --version`, `ascendo doctor` output
4. `git log --oneline -3` (so we know which commit you're on)

The Sesja 54 + 55 work shipped with reproducer-driven fixes for every
real failure mode hit during operator testing on this host. Bugs from
here on should be localized and quick to diagnose given the diagnostic
output the validate script emits.

---

## 17. What's next

Beyond v0.6.1 (Ubuntu adapter at full parity), the M6 roadmap is:

- Security audit (T1-T7 threat-model items per ADR-0005)
- Code signing across all three OSes
- Plugin signing + verification (FAZA II)
- Plugin marketplace UX in dashboard
- Localization beyond en/pl (tokens already support es/it/pt/de/fr)
- Telemetry (opt-in, 100% local-only — no centralized backend per
  project rules)

See [`HANDOFF.md`](HANDOFF.md) for the per-session work log + full
backlog. [`PLAN.md`](PLAN.md) is the forward roadmap.
