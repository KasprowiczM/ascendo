# Cross-machine sync — bringing Windows + Ubuntu up to parity

Operator workflow for replicating the full Ascendo working state from
your macOS development MacBook onto a fresh Windows or Ubuntu machine.
The public source tree comes from GitHub; per-machine secrets, AI
agent state, and any private overlay come from Proton Drive via the
`dev-sync` toolchain.

This doc is the "I just sat down at the Windows box / Ubuntu box —
what do I run?" recipe. Two phases per machine, in order.

---

## Phase 1 — Pull public source from GitHub

Identical on both Windows and Ubuntu. Pick the matching block.

### Windows (PowerShell, any window)

```powershell
# 1. Install prerequisites if missing (winget handles all three).
winget install --id Git.Git
winget install --id Python.Python.3.12
winget install --id rclone.rclone

# 2. Clone Ascendo from GitHub
cd D:\Dev_Env
git clone https://github.com/KasprowiczM/ascendo.git
cd ascendo
```

### Ubuntu (any shell)

```bash
# 1. Install prerequisites if missing
sudo apt update
sudo apt install -y git python3 python3-pip python3-venv rclone curl jq

# 2. Clone Ascendo from GitHub
mkdir -p ~/Dev_Env
cd ~/Dev_Env
git clone https://github.com/KasprowiczM/ascendo.git
cd ascendo
```

At this point both machines have an **identical public source tree**.
Run a quick sanity check:

```bash
git log --oneline -1
# Should match the operator's MacBook commit.
```

---

## Phase 2 — Pull the private overlay from Proton Drive

The dev-sync overlay carries per-machine secrets and AI-agent state
that are NOT in the public Git tree (gitignored) — things like:

- `.env.local` — local credentials
- `.dev_sync_config.json` — your Proton provider path
- `dev-sync-overlay/` — private docs (HANDOFF, PLAN, CLAUDE.md, AI tool
  configs)
- `.codex.local/`, `.gemini/`, `.claude/` settings if you use AI tools

The provider for this project is **Proton Drive** via the macOS Finder
cloud-storage mount. On Windows and Ubuntu we can't use Finder's mount,
so the import uses `rclone` configured against your Proton account.

### One-time provider setup (per machine)

Run the provider setup wizard once on each new machine:

```bash
# Ubuntu / Windows (same script via Git Bash on Windows):
bash dev-sync/provider_setup.sh
```

```powershell
# Windows native PowerShell alternative:
.\dev-sync-provider-setup.ps1
```

The wizard asks for:
1. Provider type — pick **rclone** on Windows/Ubuntu (the macOS box uses
   `protondrive` for its native CloudStorage mount; non-macOS uses
   rclone with the same Proton account).
2. rclone remote name — defaults to `proton`. The setup auto-runs
   `rclone config` if no remote with that name exists.
3. rclone remote path — points at `Dev_Env/Ascendo` in your Proton
   Drive root.

The wizard writes `.dev_sync_config.json` in the repo root with the
chosen values. This file IS gitignored — each machine has its own.

### Pull the overlay

```bash
# Ubuntu:
bash dev-sync-import.sh
```

```powershell
# Windows:
.\dev-sync-import.ps1
```

Import fetches all 2124 overlay files from your Proton remote into the
local repo tree. It NEVER touches Git-tracked files — only the
private/private-overlay paths. Idempotent: re-run safely.

After import you should have everything the MacBook has: HANDOFF.md
(if still in your private set), AI tool configs, local creds, etc.

### Verify

Run both verifiers — same as on the MacBook:

```bash
# Confirm git is clean + pushed
bash dev-sync-verify-git.sh

# Confirm overlay matches Proton exactly (both directions: missing &
# stale flagged separately)
bash dev-sync-verify-full.sh
```

Both should print `PASS`. If `verify-full` reports `Missing-from-local`
entries, re-run `dev-sync-import.sh` — those files exist on Proton but
not locally yet. If it reports `Orphan local files`, those exist
locally but not on Proton — usually fine for files you've just
created; flush them up with `dev-sync-export.sh` if you want them
shared.

---

## Day-to-day flow on each machine

After initial setup the cadence is:

```bash
# Start of work session — pull latest public source + private overlay
git pull --ff-only
bash dev-sync-import.sh         # or .ps1 on Windows
# (rebuilds editable installs automatically only if you re-run install.sh
# / install.ps1 — see below)

# Mid-work — make commits as normal
git add … && git commit …
git push origin main

# End of work — push private overlay changes (AI state, configs)
bash dev-sync-export.sh         # or .ps1 on Windows
```

The MacBook → Windows / Ubuntu flow:

```
[macOS]   commit + push  →  [GitHub]  ←  git pull   [Windows / Ubuntu]
[macOS]   dev-sync-export →  [Proton]  ←  dev-sync-import  [Windows / Ubuntu]
```

GitHub is the source of truth for **code**; Proton is the source of
truth for **per-machine private state**. They're independent — pushing
to one doesn't affect the other.

---

## Updating Ascendo itself (the venv + adapters)

After `git pull` brings in new code, refresh the editable installs:

```bash
# Ubuntu — re-run install.sh in update mode (idempotent)
curl -fsSL https://raw.githubusercontent.com/KasprowiczM/ascendo/main/install.sh \
  | bash -s -- --update
# OR if you already have ascendo on PATH:
ascendo_update
```

```powershell
# Windows
iwr -useb https://raw.githubusercontent.com/KasprowiczM/ascendo/main/update.ps1 | iex
# OR (same effect):
ascendo_update
```

Both `update.sh` and `update.ps1` are idempotent — they `git pull
--ff-only` the existing checkout, refresh the venv's editable
installs, restart any standalone (CLI-spawned) dashboard, re-symlink
helper shims, and self-test via `ascendo doctor`. Tauri-spawned
dashboard sidecars are left alone (their lifecycle is owned by the
desktop app — quit + reopen to refresh).

---

## What lands on Proton vs what lands on GitHub — the contract

| Lives on … | Examples |
|------------|----------|
| **GitHub (public source)** | All Python code under `core/`, `adapters/`, `plugins/`, `contrib/` · All bash + PowerShell scripts · All docs in `docs/` + top-level `*.md` (except HANDOFF/PLAN/CLAUDE/AGENTS/CODEX if pruned) · All tests · `.gitignore`, `.github/`, `CHANGELOG.md`, `RELEASE_NOTES.md` |
| **Proton (private overlay)** | `.env.local` · `.dev_sync_config.json` · `.dev_sync_manifest.json` · `dev-sync-overlay/` private docs (AI tool configs, dated handoffs, graphify outputs) · per-machine logs (`dev_sync_logs/`, `logs/runs/`) · `APPS.md` (auto-generated) |
| **NEITHER** *(local-only, regenerated)* | `__pycache__/` · `.pytest_cache/` · `node_modules/` · `target/` · `dist/` · `build/` · `bin-staging*/` · all `*.dmg`/`*.msi`/`*.exe`/`*.pkg`/`*.deb` · `.claude/worktrees/` *(Claude Code agent worktrees — multi-GB per dispatch, ALWAYS reconstructible from the canonical branch on origin)* |

> **Sync size expectation**: a clean overlay is ~88 files / ~1.5 MB.
> If `bash dev-sync-export.sh --dry-run` reports more than a few
> hundred files, something is wrong — most likely a stray
> `.claude/worktrees/` subtree got copied INTO the overlay during a
> past migration. The post-Sesja-56 dev-sync code prevents this from
> happening again, but stale Proton-only files from earlier exports
> can persist until pruned. To rebuild a clean overlay state on the
> MacBook:
>
> ```bash
> bash dev-sync-prune-excluded.sh --plan-out /tmp/plan.json   # plan
> bash dev-sync-prune-excluded.sh --apply-plan /tmp/plan.json # quarantine
> bash dev-sync-verify-full.sh                                # confirm PASS
> bash dev-sync-purge-quarantine.sh --apply                   # delete quarantine
> ```

The cleanest validation that you've correctly partitioned: after a
fresh clone + import, every script you'd run on the MacBook should
work identically on Windows / Ubuntu — and `dev-sync-verify-full.sh`
should report `PASS` immediately.

### Why your Ubuntu import was slow (Sesja 56 root cause)

If on Ubuntu the `dev-sync-import.sh` took forever, this is the fix:
the overlay was previously bloated with ~2,000 stale Claude Code
worktree files (29 MB) staged by an earlier `dev-sync-overlay-migrate.sh`
run. rclone-over-network handles each file as a separate API call, so
2,000+ tiny files dominated the wall time. After Sesja 56:

1. `bin/dev-sync-overlay-migrate.sh` now skips `worktrees/` during the
   staging copy (rsync `--exclude='worktrees/'` + scrub-as-fallback).
2. `dev-sync/dev_sync_core.py`'s pattern matcher now catches
   `.claude/worktrees/` at ANY depth, not just the repo root — defence
   in depth.
3. The 2,000 stale worktree copies were quarantined + purged from
   Proton.

Result: overlay is 88 files / 1.5 MB. Ubuntu import should now
complete in single-digit seconds over a normal home connection.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| `dev-sync-import.sh: rclone: command not found` | rclone not installed | `sudo apt install rclone` (Ubuntu) / `winget install rclone.rclone` (Windows) |
| `dev-sync-import.sh: provider not configured` | First run on a fresh machine | `bash dev-sync/provider_setup.sh` first |
| `verify-full` reports `Orphan local files` | You created files locally that haven't been pushed to Proton | `bash dev-sync-export.sh` |
| `verify-full` reports `Missing-from-local files` | Proton has files your local doesn't yet | `bash dev-sync-import.sh` |
| `dev-sync-import.sh` fails on `permission denied` writing into the repo | You ran the import as root earlier and now non-root can't write | `sudo chown -R $USER ~/Dev_Env/ascendo` |
| Tauri-spawned dashboard process won't die after `ascendo web stop` | `ascendo web stop` only kills the CLI-started dashboard, never the Tauri sidecar (by design) | Quit Ascendo.app to release; restart fresh |
| `git pull` says "your branch is ahead by N commits" after `dev-sync-import` | dev-sync overwrote git-tracked files (shouldn't happen — bug) | Open an issue + share the verify-full log |

Full dev-sync architecture is in [dev-sync/README.md](../dev-sync/README.md);
restore from scratch flow is in [dev-sync/RESTORE_MANIFEST.md](../dev-sync/RESTORE_MANIFEST.md).
