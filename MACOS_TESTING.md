# Ascendo on macOS — Testing Guide

Single-page, copy-paste-ready guide for trying Ascendo end-to-end on a real
Mac. Tested on **Mac.r12.home (Apple Silicon, macOS 15.x, bash 3.2.57,
Homebrew 5.1.9, Python 3.13, jq 1.8.1, mas 7.0.0)**.

---

## Want a clickable Mac app? (skip the CLI)

```bash
cd ~/Dev_Env/Ascendo
bash bin/install-dev-macos.sh             # one-time install
bash bin/launch-desktop-macos.sh --build  # builds .app + .dmg (≈ 5–10 min on first run)
open ui/desktop-tauri/src-tauri/target/release/bundle/macos/Ascendo.app
```

If macOS Gatekeeper says "Ascendo.app is damaged" (because the build is
not yet code-signed — that's M6 work), strip the quarantine attribute:

```bash
xattr -dr com.apple.quarantine ui/desktop-tauri/src-tauri/target/release/bundle/macos/Ascendo.app
```

Or right-click the `.app` → **Open** the first time (this records your
explicit consent so subsequent launches don't prompt).

The rest of this document is the **CLI flow + reference docs**.

---

## TL;DR — five commands

```bash
# 1. Clone (or pull) the repo
cd ~/Dev_Env
git clone https://github.com/KasprowiczM/ascendo.git    # first time only
cd ascendo
git pull                                                  # subsequent times

# 2. One-shot install (core + macOS adapter + dashboard deps + auto-validate)
bash bin/install-dev-macos.sh

# 3. Read-only validation across all 5 phases × 3 categories + dashboard +
#    LaunchServices inventory + Time Machine list + launchd scheduler round-trip
bash bin/validate-macos.sh                # should print ALL CHECKS PASSED. (34/34)

# 4. (Optional) Real apply — actually upgrades brew packages
bash bin/run-tag-release-macos.sh         # interactive, asks for 'apply' confirmation

# 5. Browser-visible dashboard
python3 -m ascendo dashboard --port 8765
# open http://127.0.0.1:8765/ in a browser
```

That's it. The rest of this document is reference: prerequisites, what
each step does, how to interpret the output, troubleshooting.

---

## 1. Prerequisites

| Component | Required | Verify |
|---|---|---|
| macOS 13+ (Ventura or newer) | yes | `sw_vers -productVersion` |
| Python 3.11+ | yes | `python3 --version` |
| Bash 3.2 (system) | yes (macOS ships it) | `bash --version` |
| Homebrew | yes | `brew --version` |
| jq | yes (auto-installed by `install-dev-macos.sh`) | `jq --version` |
| mas (optional, for Mac App Store) | recommended | `mas version` |
| Git | yes | `git --version` |
| Internet access | yes (for pip + brew) | — |

**Anything missing?**

```bash
# Apple command-line tools (needed for git + clang for any pip native deps):
xcode-select --install

# Homebrew (if not installed):
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# Python 3.13 via Homebrew:
brew install python@3.13

# mas (Mac App Store CLI, optional):
brew install mas
```

---

## 2. Install

```bash
cd ~/Dev_Env/Ascendo
bash bin/install-dev-macos.sh
```

What this script does (in order):

1. Detects toolchain (python3, bash, brew); installs `jq` via brew if missing.
2. `pip install -e ./core` (the `ascendo` package, editable). Passes
   `--break-system-packages` to Homebrew Python's PEP 668 guard.
3. `pip install -e ./adapters/macos --no-deps` (the `ascendo-macos` package).
4. `pip install fastapi uvicorn[standard] httpx` (dashboard runtime).
5. Prints `pip show ascendo ascendo-macos` to confirm install location.
6. Auto-runs `bash bin/validate-macos.sh` at the end.

**Useful options:**

```bash
bash bin/install-dev-macos.sh --skip-validate   # install only
bash bin/install-dev-macos.sh --reinstall       # force re-install
```

If you see PEP 668 errors on a manual `pip install`, prefer
`python3 -m pip install ... --break-system-packages` or use a venv:

```bash
python3 -m venv ~/.venv/ascendo
source ~/.venv/ascendo/bin/activate
bash bin/install-dev-macos.sh
```

---

## 3. Validate (read-only / dry-run)

```bash
bash bin/validate-macos.sh
```

What it checks (in order):

1. `python3 -m ascendo --help` / `version` / `doctor` exit 0.
2. **Stage 2** — Five-phase brew contract: check / plan / apply --dry-run
   / verify / cleanup --dry-run. Each must produce a sidecar with
   `schema=ascendo/v1`, `phase=<expected>`, `category=brew`.
3. **Stage 3** — Dashboard smoke: `/version` + `/health` + `POST /runs/async`
   + status poll, clean teardown.
4. **Stage 8** — `mas` + dashboard askpass round-trip (M5.2): doctor
   reports mas, five-phase mas contract, `/elevation/auth` +
   `/elevation/status` cycle. **8.7 (askpass) only runs if you set
   `SUDO_PW`.**
5. **Stage 9** — LaunchServices inventory (M5.3): doctor reports
   system_profiler, `list.sh` end-to-end produces sidecar with 50+ apps,
   classification distribution sanity (system/mas/brew/web),
   `MacOSAdapter.inventory()` Python wrapper.
6. **Stage 10** — softwareupdate (M5.4): doctor reports softwareupdate,
   five-phase softwareupdate contract via CLI (check/plan/verify/cleanup
   + apply --dry-run; **real apply EXCLUDED** — would reboot).
7. **Stage 11** — Time Machine read-only (M5.4): doctor reports tmutil,
   `TimeMachineSnapshot.list()` end-to-end (≥ 0 snapshots).
8. **Stage 12** — launchd scheduler round-trip (M5.5): doctor reports
   launchctl, install + list + trigger + remove an `ascendo-validate-test`
   throwaway agent. Cleanup is in a `trap EXIT` handler so a failed run
   never leaks plists.

**Expected final line:**

```
ALL CHECKS PASSED. (34/34)
```

If any step fails, the script prints `[FAIL]` with diagnostic info. Paste
that output back and we'll diagnose.

**Useful options:**

```bash
bash bin/validate-macos.sh --port 18765          # use a different dashboard port
bash bin/validate-macos.sh --skip-dashboard      # skip dashboard tests (faster)
SUDO_PW='your-pw' bash bin/validate-macos.sh     # exercise Stage 8.7 askpass
```

---

## 4. (Optional) Real apply — first real mutation

```bash
bash bin/run-tag-release-macos.sh
```

This is the **safety harness for the first real upgrade** on your Mac.
The 7-stage flow:

1. **Preflight** — checks brew/jq/git/Python on PATH; sets `PYTHONPATH`.
2. **Snapshot** — currently a `[WARN]` that says "open System Settings →
   Time Machine to back up first" (APFS local snapshots are auto-managed
   so Ascendo can't create them). Opt out with `--no-snapshot`.
3. **Plan** — `python3 -m ascendo run --category brew --phase plan` and
   prints what would change.
4. **Confirm gate** — interactive prompt requiring the literal string
   `apply`. Anything else aborts. Skip with `--i-accept-upgrade-risk`
   (use only in CI).
5. **Apply** — `python3 -m ascendo run --category brew --phase apply`. brew
   actually upgrades the packages. Exit 0 = success, 75 = reboot required.
   - **5b** — opt-in `--mas` flag also runs `sudo mas upgrade <id>` for
     Mac App Store apps. Requires `SUDO_PW` env var (so the
     dashboard askpass round-trip in validate-macos Stage 8.7 can run too).
6. **Verify + cleanup** — both phases run unconditionally.
7. **Doctor + tag** — prints all 10 components green; if apply succeeded
   AND verify exited 0/1, creates the local tag (currently `v0.2.0`).

**Useful options:**

```bash
bash bin/run-tag-release-macos.sh --what-if               # show plan, no mutation
bash bin/run-tag-release-macos.sh --no-tag                # apply but don't tag
bash bin/run-tag-release-macos.sh --no-snapshot           # skip the snapshot warn
bash bin/run-tag-release-macos.sh --i-accept-upgrade-risk # skip interactive confirm
SUDO_PW='your-pw' bash bin/run-tag-release-macos.sh --mas # also run mas upgrade
```

**Exit codes:**
- 0 — all stages succeeded
- 1 — apply or verify failed
- 30 — apply failed in known state
- 75 — apply succeeded, reboot required

---

## 5. Browser-visible dashboard

```bash
python3 -m ascendo dashboard --port 8765
```

Then in a browser:

- **`http://127.0.0.1:8765/`** — the SPA (sidebar + Categories + Run Center
  + History + Logs)
- **`http://127.0.0.1:8765/docs`** — interactive Swagger UI with all
  REST endpoints
- **`http://127.0.0.1:8765/version`** — `{"ascendo": "0.0.7", "adapter": "macos", "adapter_tier": 1}`
- **`http://127.0.0.1:8765/health`** — `{"status": "ok", "adapter": "macos", "components": {...10 keys...}}`

**Drive a run from the browser** — in the Categories tab:

1. Click any category row (brew / mas / softwareupdate). Click **check**
   first.
2. Run Center pops open with live SSE stream.
3. Once check finishes, click **plan** then **apply**. The apply phase
   asks for the literal `apply` confirmation in a modal.
4. Sidecars accumulate in `~/.ascendo/runs/<uuid>/`. The History tab
   lists them.

**Stop the dashboard:** `Ctrl+C` in the terminal where it's running.

---

## 6. Launch the desktop app (Tauri 2.x)

```bash
bash bin/launch-desktop-macos.sh             # dev mode (Ctrl-C to stop)
bash bin/launch-desktop-macos.sh --build     # produce a packaged .app + .dmg
```

Build prerequisites: Apple CLI tools (`xcode-select --install`), Rust
(`https://rustup.rs`), Node 18+ (`brew install node`).

The Tauri 2.x shell:
1. Spawns `python3 -m ascendo dashboard --port <ephemeral>` as a
   sidecar process (stdio detached).
2. Polls `http://127.0.0.1:<port>/health` every 200 ms for up to 10 s.
3. Opens a 1280×800 native WKWebView window pointing at the dashboard.
4. On window close: kills the sidecar process.

Build artefacts land in:
- `ui/desktop-tauri/src-tauri/target/release/bundle/macos/Ascendo.app`
- `ui/desktop-tauri/src-tauri/target/release/bundle/dmg/Ascendo_<version>_aarch64.dmg`

The build is **not code-signed** — running it on another Mac will hit
Gatekeeper. See "Gatekeeper" troubleshooting below; code signing is M6
work.

---

## 7. End-to-end first apply with snapshot warn + tag

`bin/run-tag-release-macos.sh` is the macOS equivalent of the Windows
`bin/run-tag-release.ps1`. See §4 above.

To reproduce the v0.2.0 release exactly:

```bash
# Without sudo password (brew-only, no mas):
bash bin/run-tag-release-macos.sh

# With sudo password (full coverage including mas):
read -rsp "sudo pw: " SUDO_PW; export SUDO_PW; echo
bash bin/run-tag-release-macos.sh --mas
```

The script does NOT push the tag — run `git push --tags` manually when
you're confident about the result.

---

## 8. What's been validated end-to-end

After §1-§5, you've exercised every layer of the 6-layer architecture
on real macOS hardware:

| Layer | Module | Validated? |
|---|---|---|
| 1 — Frontend SPA | `app/frontend/*` | ✅ via Categories + Run Center clicks |
| 2 — Tauri shell | `ui/desktop-tauri/*` | 🟡 dev mode works; build produces unsigned `.app`/`.dmg` (signing is M6) |
| 3 — Backend HTTP | `core/ascendo/dashboard/` | ✅ via Swagger UI + validate Stage 3 |
| 4 — Core domain | `core/ascendo/{models,interfaces,orchestrator,cli}/` | ✅ via CLI |
| 5 — Adapter Python | `adapters/macos/ascendo_macos/` | ✅ via doctor + run + scheduler |
| 6 — Native scripts | `adapters/macos/{lib,scripts/*}` | ✅ via run (real brew + mas + softwareupdate + scheduler) |

---

## 9. Troubleshooting

### `pip: command not found`

Homebrew Python uses `pip3` or `python3 -m pip`. Use those instead.

### `error: externally-managed-environment` on pip install

PEP 668 (Homebrew Python 3.12+). Three fixes:

1. **Easiest** — pass `--break-system-packages`:
   ```bash
   python3 -m pip install -e adapters/macos --no-deps --break-system-packages
   ```
2. **Cleaner** — use a venv:
   ```bash
   python3 -m venv ~/.venv/ascendo
   source ~/.venv/ascendo/bin/activate
   bash bin/install-dev-macos.sh
   ```
3. The `bin/install-dev-macos.sh` script already passes
   `--break-system-packages` automatically.

### `validate-macos.sh` prints `[FAIL]` on `doctor`

Almost certainly the macOS adapter isn't installed. Re-run
`bash bin/install-dev-macos.sh` and try again.

### `validate-macos.sh` Stage 12 fails on schedule install

If you see "no such option: --expression" — your validate script is
older than M5.5.11.2; pull from main and re-run.

### `ascendo doctor` capability list omits SCHEDULING

Stale Python bytecode. Clear it:
```bash
find . -name '__pycache__' -type d -exec rm -rf {} +
```

### Tauri `npm run tauri dev` fails

| Error                              | Fix |
|------------------------------------|-----|
| `linker 'cc' not found`            | `xcode-select --install` |
| `command not found: cargo`         | install Rust: `curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs \| sh` |
| `command not found: npm`           | `brew install node` |

### Tauri `.app` won't open ("damaged" or "untrusted developer")

Gatekeeper. Two fixes:

1. **Right-click → Open** the first time (records explicit consent).
2. **Strip the quarantine attribute:**
   ```bash
   xattr -dr com.apple.quarantine ui/desktop-tauri/src-tauri/target/release/bundle/macos/Ascendo.app
   ```

Real fix is code signing (Apple Developer ID), planned for M6.

### softwareupdate apply hangs forever

macOS is showing a sudo password dialog. Either:
- Set `SUDO_PW='...'` env var BEFORE running.
- Run from a terminal with the dashboard's askpass round-trip already
  primed (`POST /elevation/auth`).

### `mas` upgrade fails with "Permission denied"

`mas upgrade` (without `sudo`) is a CVE-2025-43411 vector — Ascendo
enforces `sudo mas upgrade` always. If you don't have `sudo`, you can't
upgrade Mac App Store apps headlessly. Use the App Store GUI instead.

### launchctl bootstrap "Service already loaded"

Idempotent — the install action does `bootout` first then `bootstrap`.
Safe to re-run. If you see this error in a non-Ascendo context, run
`launchctl bootout gui/$(id -u)/dev.ascendo.<name>` then retry.

---

## 10. Reporting issues

If anything doesn't work as described, paste:

1. The exact command you ran.
2. The full output (especially `[FAIL]` lines + sidecar.messages if any).
3. `python3 --version`, `bash --version`, `brew --version`,
   `sw_vers -productVersion`.
4. `git log --oneline -3` (so we know which commit you're on).
5. The newest sidecar that failed:
   `ls -1 ~/.ascendo/runs/ | tail -1` then
   `cat ~/.ascendo/runs/<that-uuid>/<phase>__<category>.json`.

---

## 11. What's next

Beyond v0.2.0:

- **v0.2.1** — operator-feedback fixes from real-Mac usage.
- **v0.3.0 (M6 hardening)** — security audit (T1–T7 per ADR-0005), code
  signing across all three OSes (Apple Developer ID + Authenticode +
  potentially Linux .deb signing), plugin signing + verification.
- **v0.4.0** — plugin marketplace UX in the dashboard, localization
  beyond en/pl (es/it/pt/de/fr translations land for the existing token
  slots).
- **v1.0** — security audit complete + signed binaries for all 3 OSes +
  stable API contract.

See `PLAN.md` for the forward roadmap and `HANDOFF.md` for the
per-session log.
