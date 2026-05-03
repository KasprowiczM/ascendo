# Cross-platform development guide

> How to work on Ascendo from Windows, macOS, and Ubuntu in parallel
> without stepping on yourself. **Single source of truth for code: `origin/main` on
> GitHub. Private secrets: `dev-sync` cloud overlay (Proton/Drive/etc.).**

---

## TL;DR

```
Code → GitHub origin/main (git push / pull)
Secrets → dev-sync overlay (rclone copy to Proton)
3 machines → 1 repo each, all on main, frequent push/pull
```

You don't need branches. You don't need worktrees. **One repo per machine, all on `main`, you commit small, push often, pull before you start.** This guide explains why and exactly how.

---

## What's common vs. platform-specific

The monorepo layout was designed for exactly this question. Here's the rule:

```
ascendo/
├── core/                   ★ COMMON — Python, OS-agnostic.
│                              CLI, dashboard backend, schemas, models, orchestrator.
│                              Edit anywhere; tested everywhere.
├── adapters/
│   ├── windows/            ⊟ WINDOWS-ONLY — pwsh + .ps1 + WingetManager etc.
│   │                          Compiles & runs only on Win11. Linux/Mac CI skips.
│   ├── ubuntu/             ⊞ LINUX-ONLY — bash + apt/snap/brew managers.
│   │                          Compiles & runs only on Ubuntu/Debian/Pop!_OS.
│   └── macos/              ⊠ MACOS-ONLY — brew/mas/softwareupdate/launchd.
│                              Stub today; you'll fill it in on the Mac.
├── ui/
│   ├── frontend/           ★ COMMON — vanilla SPA. One bundle, all 3 OSes.
│   └── desktop-tauri/      ★ COMMON Tauri 2.x shell — same Rust source produces
│                              .msi (Win) / .deb + .AppImage (Linux) / .dmg (Mac).
├── plugins/
│   ├── _template/          ★ COMMON template
│   ├── dell-driver-update/ ⊟ WINDOWS-ONLY (Dell DCU)
│   ├── nvidia-driver-update/ ⊞ LINUX-ONLY (apt + nvidia-driver-*)
│   └── agent-clis/         ★ COMMON (Claude Code, Codex, Gemini, Qwen, OpenCode)
├── packaging/
│   ├── pyinstaller/        ★ COMMON spec, runs on each OS to make the OS's binary
│   ├── msi/                ⊟ WINDOWS bundler config
│   ├── deb/                ⊞ LINUX .deb metadata + scripts
│   ├── pkg/                ⊠ MACOS .pkg (TODO when adapter lands)
│   ├── homebrew-tap/       ⊠ MACOS brew formula (TODO)
│   └── winget-manifest/    ⊟ WINDOWS winget submission
├── bin/
│   ├── *.ps1               ⊟ WINDOWS PowerShell scripts (install-dev, launch-desktop, validate-windows)
│   ├── *.sh                ⊞ LINUX/MAC bash scripts (install-dev, launch-desktop, validate)
│   └── ascendo             ★ COMMON wrapper — bash on Linux/Mac, .cmd on Win
├── app/                    ⊞ LEGACY Linux code being migrated into adapters/ubuntu/
├── tests/                  ★ COMMON test suite. Adapter tests skip on wrong OS via marker.
├── docs/                   ★ COMMON docs.
├── branding/               ★ COMMON SVG/PNG assets.
└── i18n/, schemas/, share/ ★ COMMON.
```

### The simple rule for "where does my edit go?"

| If your change is… | Edit in… | Result |
|---|---|---|
| Backend logic that should work everywhere | `core/` | All 3 OSes pick it up after `git pull` |
| SPA UI / styles / i18n | `app/frontend/` (will move to `ui/frontend/`) | All 3 OSes pick it up |
| Tauri shell (Rust) or HTML splash | `ui/desktop-tauri/` | All 3 OSes pick it up |
| A winget / pwsh script | `adapters/windows/` | Windows only — Mac/Linux ignore |
| An apt / snap / bash script | `adapters/ubuntu/` | Ubuntu only |
| A brew / mas / softwareupdate script | `adapters/macos/` | macOS only |
| A docs file | `docs/` | All 3 OSes |
| Anything cosmetic (icon, favicon, logo) | `branding/` | All 3 OSes |
| Test the common backend | `tests/` | All 3 OSes |
| Test a Windows-only adapter | `adapters/windows/tests/` | Skipped on non-Windows |

**If you're not sure: ask "would this code make sense to RUN on macOS?" If yes → common. If no (it spawns pwsh, reads HKLM, calls winget) → platform-specific.**

---

## The 3-machine workflow (what you actually do daily)

### One-time setup per machine

#### Windows (you've already done this)
```powershell
git clone https://github.com/KasprowiczM/ascendo.git D:\Dev_Env\Ascendo
cd D:\Dev_Env\Ascendo
.\bin\install-dev.ps1                  # core + adapters/windows + smoke
```

#### Ubuntu (when you boot into Linux)
```bash
git clone https://github.com/KasprowiczM/ascendo.git ~/Dev_Env/Ascendo
cd ~/Dev_Env/Ascendo
# Until adapters/ubuntu/ is fully migrated, the legacy app/ code still runs.
# Once migrated, the install will be:
./bin/install-dev.sh                   # core + adapters/ubuntu + smoke
# For now (legacy):
bash setup.sh                          # legacy installer
bash app/install.sh                    # FastAPI venv
python3 -m ascendo dashboard           # http://127.0.0.1:8765
```

#### macOS (when you sit down at the MacBook)
```bash
git clone https://github.com/KasprowiczM/ascendo.git ~/Dev_Env/Ascendo
cd ~/Dev_Env/Ascendo
# adapters/macos/ is currently a stub; you'll be building it out.
# Until then the dashboard works against an empty adapter (read-only).
brew install python@3.13 rust node      # prereqs
pip3 install -e core/ -e adapters/macos/
python3 -m ascendo dashboard
```

### Every working session, regardless of OS

```bash
# 1. PULL FIRST. Always. Even if "you only just pushed from the other machine".
cd ~/Dev_Env/Ascendo   # or D:\Dev_Env\Ascendo on Windows
git pull --ff-only

# 2. (Optional) Refresh the editable Python install if pyproject.toml changed.
pip install -e core/ --no-deps         # idempotent

# 3. Work. Edit files. Run tests as you go.

# 4. PUSH OFTEN. Small commits beat one giant end-of-day push.
git add <specific files>
git commit -m "feat(adapter/macos): add brew manager skeleton"
git push origin main

# 5. (Other machines pick up changes on their next `git pull`.)
```

### Conflict avoidance (the only thing that bites you)

Real conflicts happen **only** when two machines edit the same file between pulls. With 3 OSes this is easy to avoid:

- **Working on different platforms in parallel?** Each OS has its own `adapters/<os>/` folder. Edit those concurrently — zero conflict.
- **Working on common code (`core/`, `ui/frontend/`, `docs/`)?** Pull right before you start, push right when done. If you're switching machines mid-feature, push from the old machine FIRST.
- **Long-running edit you can't push yet?** Stash with `git stash` before switching, or commit to a topic branch:
  ```bash
  git checkout -b feat/macos-brew     # branch for the in-progress work
  git push -u origin feat/macos-brew  # so other machines can see it
  # later, on a different machine:
  git fetch && git checkout feat/macos-brew && git pull
  ```
  Merge to main when feature is complete:
  ```bash
  git checkout main && git pull
  git merge --ff-only feat/macos-brew
  git push origin main
  git branch -D feat/macos-brew && git push origin :feat/macos-brew  # cleanup
  ```

### The CRITICAL rule

**Do NOT use `git worktree add`.** Earlier sessions accidentally created multiple parallel worktrees that drifted and had to be reconciled by hand. The repo is one directory, `D:\Dev_Env\Ascendo` (Win) or `~/Dev_Env/Ascendo` (Mac/Linux), on `main`, always. CLAUDE.md hard-codes this rule.

---

## Cross-platform code that touches all 3 OSes

When you edit `core/` (which runs on every OS), please:

1. **Run the test suite** on the OS you edited from:
   ```bash
   python -m pytest core/ tests/contract/ tests/python/ --rootdir=.
   python -m pytest adapters/<your-os>/tests/
   ```
2. **Check the cross-OS smoke** if your change touches subprocess / paths / encoding:
   - Path handling: use `pathlib.Path`, never raw `/` joins.
   - Subprocess: import `from ascendo.utils.proc import no_window_kwargs` and **always** unpack `**no_window_kwargs()` so Windows doesn't flash a console.
   - File encoding: `open(path, encoding="utf-8")` always; never rely on platform default.
3. **GitHub Actions CI** (when wired) will run the test matrix across all 3 OSes on every push, so if you broke macOS from your Windows machine, you'll see it within minutes of pushing — fix it from the Mac next time you're there.

---

## Per-OS specifics — what's different by design

| Subsystem | Windows | Linux | macOS |
|---|---|---|---|
| Package manager | winget | apt/snap/brew/flatpak | brew/mas |
| OS updates | PSWindowsUpdate (`Get-WUList`/`Install-WindowsUpdate`) | `apt full-upgrade` | `softwareupdate -ia -R` |
| App store | Microsoft Store (via winget msstore) | (none) | Mac App Store via `mas` |
| Snapshot backend | Volume Shadow Copy (`vssadmin`) | `timeshift` / `etckeeper` | Time Machine (read-only) |
| Scheduler | Task Scheduler (`schtasks`) | `systemd --user` timer | `launchd` |
| Service install | NSSM-wrapped Windows service | `systemd --user` unit | `launchd` plist |
| Elevation | UAC via `ShellExecute` `runas` | `sudo` + askpass helper | `sudo` + `osascript` for GUI prompt |
| Dashboard launch | `bin\launch-desktop.ps1` (Tauri 2.x) | `bin/launch-desktop.sh` | `bin/launch-desktop.sh` |
| Installer artifact | `.msi` (WiX) + `-setup.exe` (NSIS) | `.deb` + AppImage | `.dmg` + `.pkg` |
| Distribution channel | winget manifest | apt repo / AUR | Homebrew tap |
| User config dir | `%APPDATA%\Ascendo\` | `~/.config/ascendo/` | `~/Library/Application Support/Ascendo/` |
| User data dir | `%LocalAppData%\Ascendo\` | `~/.local/share/ascendo/` | `~/Library/Application Support/Ascendo/` |
| Run history | `%LocalAppData%\Ascendo\runs\` | `~/.ascendo/runs/` | `~/.ascendo/runs/` |

The `adapter_factory` in `core/ascendo/adapter_factory/` reads the host OS at startup and picks the right adapter. Common code never has to switch on platform — it asks the adapter to do the OS-specific bit.

---

## dev-sync — what it actually syncs (and what it doesn't)

dev-sync is a **private-overlay sync**, NOT a code sync. It mirrors a small set of files (≈8) that are git-ignored because they contain secrets, OAuth tokens, or per-machine settings:

```
.env.local                            # OPENAI_API_KEY, ANTHROPIC_API_KEY, etc.
.dev_sync_config.json                 # provider/remote names (so it's bootstrap-recoverable)
~/.config/ascendo/lang                # CLI language preference
~/.config/ascendo/secrets/*           # any per-user secrets you put there
~/.codex/config.toml                  # agent profiles
~/.claude/settings.json               # agent profiles
.codex.local/                         # local agent overrides
```

**Code lives on GitHub. Secrets live on Proton via dev-sync. Don't mix them.**

When you sit down at a fresh machine:

### Linux / macOS

```bash
# 1. Pull the public code from GitHub
git clone https://github.com/KasprowiczM/ascendo.git ~/Dev_Env/Ascendo
cd ~/Dev_Env/Ascendo

# 2. Install rclone + configure your Proton remote (one-time, interactive)
brew install rclone                            # Mac
# OR sudo apt install rclone                   # Ubuntu
rclone config                                  # interactive — pick "protondrive", auth via browser

# 3. Pull the private overlay from Proton
bash dev-sync/provider_setup.sh                # tells Ascendo which remote to use
bash dev-sync-restore-preflight.sh             # safety check
bash dev-sync-import.sh --dry-run --verbose    # preview
bash dev-sync-import.sh                        # real
bash dev-sync-verify-full.sh                   # confirm both GitHub + Proton coverage
```

### Windows (PowerShell — same workflow, `.ps1` wrappers)

Every `.sh` wrapper has a Windows mirror at the repo root with the same name and `.ps1` extension. Both delegate to the same cross-platform Python backends in `dev-sync/`, so the behaviour is identical.

```powershell
# 1. Pull the public code from GitHub
git clone https://github.com/KasprowiczM/ascendo.git D:\Dev_Env\Ascendo
cd D:\Dev_Env\Ascendo

# 2. Install rclone + configure your Proton remote (one-time, interactive)
winget install Rclone.Rclone
rclone config                                  # interactive — pick "protondrive", auth in browser

# 3. Pull the private overlay from Proton
.\dev-sync-provider-setup.ps1                  # writes .dev_sync_config.json
.\dev-sync-restore-preflight.ps1               # safety check
.\dev-sync-import.ps1 --dry-run --verbose      # preview
.\dev-sync-import.ps1                          # real
.\dev-sync-verify-full.ps1                     # confirm coverage
```

### Daily commands — both shells

| Action | Linux / Mac | Windows |
|---|---|---|
| Configure provider (one-time, interactive) | `bash dev-sync/provider_setup.sh` | `.\dev-sync-provider-setup.ps1` |
| Preview what would be exported | `bash dev-sync-export.sh --dry-run --verbose` | `.\dev-sync-export.ps1 --dry-run --verbose` |
| Push private overlay to provider | `bash dev-sync-export.sh` | `.\dev-sync-export.ps1` |
| Pull overlay onto a fresh clone | `bash dev-sync-import.sh` | `.\dev-sync-import.ps1` |
| Check what's on Proton | `bash dev-sync-proton-status.sh` | `.\dev-sync-proton-status.ps1` |
| Verify Git + provider coverage | `bash dev-sync-verify-full.sh` | `.\dev-sync-verify-full.ps1` |
| Verify Git only (read-only) | `bash dev-sync-verify-git.sh` | `.\dev-sync-verify-git.ps1` |
| Plan-quarantine excluded files | `bash dev-sync-prune-excluded.sh` | `.\dev-sync-prune-excluded.ps1` |
| Apply the reviewed quarantine purge | `bash dev-sync-purge-quarantine.sh --apply` | `.\dev-sync-purge-quarantine.ps1 --apply` |
| Restore-readiness preflight | `bash dev-sync-restore-preflight.sh` | `.\dev-sync-restore-preflight.ps1` |

The `.ps1` scripts hunt for Python in this order: `py` (Microsoft launcher) → `python` → `python3`. Install Python via `winget install Python.Python.3.13` if none of those are on PATH.

Now both your code AND your secrets are on the new machine. Daily: `git pull` for code; only re-run `dev-sync-export.sh` when you've changed a secret (e.g. rotated an API key).

---

## What's already in place (rc10 status as of 2026-05-03)

✅ **Windows MVP feature-complete and shipping**: CLI + dashboard + Tauri shell + MSI/NSIS installer + first-run wizard + per-tab help + live SSE streaming + Windows service + UAC elevation. v0.0.7-rc10 tagged.

⚠️ **Linux**: legacy code at top-level (`app/`, `lib/`, `scripts/`, `update-all.sh`) still works. Migration into `adapters/ubuntu/` is the user's next move when on Ubuntu.

⏳ **macOS**: `adapters/macos/` is a stub package. Brew + mas + softwareupdate + launchd managers all to be built. Time Machine snapshot manager. The user's plan: do this on the Mac.

The Tauri shell, Python core, dashboard, and SPA all already work cross-platform — what each new OS needs is **its adapter implementation**, not changes to the common code.

---

## Suggested order to bring up the other OSes

### When you switch to Ubuntu

1. Clone the repo, run dev-sync import.
2. **Migrate `app/` → `adapters/ubuntu/`** mechanically. The Linux scripts at the top level (`update-all.sh`, `scripts/<cat>/{check,plan,apply,verify,cleanup}.sh`, `lib/*.sh`) move into `adapters/ubuntu/scripts/`, `adapters/ubuntu/lib/`, etc. Wire them into `adapters/ubuntu/ascendo_ubuntu/` Python package — mirror the Windows adapter's structure.
3. Run `python -m ascendo doctor` — confirm 5 components OK.
4. Run `python -m ascendo run --category apt --phase check` — first end-to-end test.
5. Build a `.deb` via `bash packaging/build-deb.sh` and install it.
6. Tag `v0.0.8-alpha` when Ubuntu MVP is ready.

### When you switch to macOS

1. Clone the repo, run dev-sync import.
2. Read `adapters/windows/ascendo_windows/managers/winget.py` end-to-end — that's your reference implementation.
3. Build out `adapters/macos/ascendo_macos/managers/brew.py` (formulae + casks), `mas.py` (Mac App Store CLI), `softwareupdate.py`, `launchd.py` (scheduler), `time_machine.py` (snapshot read-only), `elevation.py` (sudo + osascript).
4. `python -m ascendo doctor` — confirm.
5. `bash bin/build-installer.sh` — should produce `dist/Ascendo-0.0.x-arm64.dmg` and `.pkg`.
6. Submit to Homebrew tap.
7. Tag `v0.0.9-alpha` when macOS MVP is ready.

### Once all 3 OSes work end-to-end

Tag `v0.1.0`. Open the GitHub release with `.msi`/`.exe`/`.deb`/`.dmg`/`.pkg` artifacts attached, plus the v0.1.0 CHANGELOG entry. Submit to winget + brew tap + AUR.

---

## Common pitfalls (worth memorising)

1. **Pip editable installs point at the wrong path.** When you change worktrees or move the checkout, run `pip install -e core --no-deps --force-reinstall` or `python -m ascendo` will silently load stale code from the old path. We hit this 4 sessions in a row in November.

2. **Browser cache hides SPA changes.** After `git pull`, hard-reload (Ctrl-Shift-R) or open in InPrivate. The SPA's `app.js` doesn't have a cache-buster on its query string.

3. **`/inventory/{cat}` has a 60s cache.** After running `check` from Categories, the row may show pre-check data for up to 60s. The new `loadCategoryDetail` busts the cache before reading, but a manual `Refresh` button on the Overview tab uses the cached state for one cycle.

4. **PowerShell `return ,@()` over-wraps when the caller does `@(...)`.** Always plain `return` from PowerShell functions. See `AscendoWingetActions.Stop-PackageProcesses` comment.

5. **`Set-StrictMode -Version Latest` requires `PSObject.Properties[name].Value`** for any registry / object property that might not exist. Plain `$obj.Foo` throws.

6. **Subprocess on Windows must pass `**no_window_kwargs()`** or every spawn flashes a black console box on screen. Helper at `core/ascendo/utils/proc.py`.

7. **Tauri 2.x renamed config keys** from 1.x: `build.devPath`→`build.devUrl`, `build.distDir`→`build.frontendDist`, `build.withGlobalTauri`→`app.withGlobalTauri`. The agent-generated scaffold used 1.x names initially.

---

## Where to get help (in order)

1. [`PLAN.md`](../PLAN.md) — forward roadmap (what's next).
2. [`HANDOFF.md`](../HANDOFF.md) — historical session log.
3. Latest dated handoff under `docs/superpowers/specs/<date>-*.md` — most recent context.
4. Per-OS quickstarts: [`WINDOWS_QUICKSTART.md`](../WINDOWS_QUICKSTART.md) (Win11 done). Mac and Linux equivalents will land alongside their adapters.
5. Architecture: [`docs/architecture/`](architecture/) — 7 ADRs covering monorepo / Tauri / JSON-v1 sidecar / 6-layer architecture / Tier-1-vs-Tier-2 adapters / plugin manifest.

---

End of guide. **Code on GitHub, secrets on Proton, one repo per machine, push often, pull before you start.** That's the whole pattern.
