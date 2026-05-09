# Ascendo — Developer Guide (Dev Edition)

For maintainers, contributors, and operators running the **dev edition**
of Ascendo. If you installed with `ASCENDO_EDITION=dev`, this is the
guide for you. End-users running the basic edition want
[USER_GUIDE.md](USER_GUIDE.md) instead.

This guide covers everything Basic doesn't:

- Dev-only UI surfaces (Sync, Hosts, raw events)
- Dev-sync overlay tooling — how Ascendo's private files travel
  between your machines
- Bootstrapping a fresh dev box from a clean repo + cloud overlay
- Adding new package managers, platforms, plugins
- Release engineering, signing, packaging
- Where the dev/private split lives in the repo

---

## 1. Install the dev edition

Two valid paths.

### A. Fresh dev install (one-liner)

```bash
# macOS / Linux
curl -fsSL https://raw.githubusercontent.com/KasprowiczM/ascendo/main/install.sh \
  | ASCENDO_EDITION=dev ASCENDO_PROFILE=full bash
```

```powershell
# Windows
$env:ASCENDO_EDITION = 'dev'
$env:ASCENDO_PROFILE = 'full'
iwr -useb https://raw.githubusercontent.com/KasprowiczM/ascendo/main/install.ps1 | iex
```

The dev edition ships:

- Every Basic feature
- The 12-tab dashboard (Basic shows 8 of these — see §3)
- Dev helper scripts under `bin/user-scripts/dev/` — `ascendo_sync`
  and `ascendo_push` are added to PATH
- Dev-sync overlay tooling under `dev-sync/`
- The full repo checkout (Basic also gets full checkout, but the
  dev edition unlocks the dev-only surfaces in the dashboard)

### B. Clone-and-pip from an existing repo

For when you already have a checkout you want to develop in directly:

```bash
git clone https://github.com/KasprowiczM/ascendo.git ~/Dev_Env/Ascendo
cd ~/Dev_Env/Ascendo

# Pick your platform:
bash bin/install-dev-macos.sh                    # macOS
.\bin\install-dev.ps1                            # Windows (PowerShell)
pip install -e core/ -e adapters/ubuntu/         # Linux

# Mark as dev edition:
echo dev > "$HOME/.local/share/ascendo/.ascendo-edition"
# (Windows: %LOCALAPPDATA%\Ascendo\src\.ascendo-edition)
```

The `.ascendo-edition` marker file is what the dashboard reads on
startup to gate the dev-only UI surfaces.

---

## 2. Bootstrapping a NEW dev machine

This is the canonical "I just got a new laptop" flow:

```bash
# 1. Install Ascendo dev edition via the one-liner (above).

# 2. Pull the dev-sync overlay (private files: secrets, agent context,
#    HANDOFF.md, machine-specific configs that aren't in git).
cd ~/Dev_Env/Ascendo
bash dev-sync/provider_setup.sh                  # one-time per machine
bash dev-sync-import.sh                          # pulls overlay from your cloud

# 3. Verify everything stitched together correctly.
bash dev-sync-verify-full.sh                     # checks git + overlay state
ascendo_doctor                                   # 10-component self-test
```

After step 2, your tree has both the public Git-tracked files AND the
private overlay files (CLAUDE.md, HANDOFF.md, .env.local,
.dev_sync_config.json, AI agent state, etc.). See §6 for the public /
private split.

---

## 3. The 12-tab UI (dev edition)

The dev edition unlocks four extra tabs on top of the eight from the
basic edition:

| Tab | Basic | Dev | What it does |
|-----|-------|-----|--------------|
| Overview     | ✅ | ✅ | Health card + quick actions |
| Categories   | ✅ | ✅ | Per-source phase buttons |
| Run Center   | ✅ | ✅ | Live SSE progress |
| History      | ✅ | ✅ | Past runs + inline logs |
| Apps         | ✅ | ✅ | Inventory + per-app history + exclusions |
| Suggestions  | ✅ | ✅ | Preset + AI recommendations |
| Settings     | ✅ | ✅ | Locale, theme, scheduler, AI providers |
| About / Help | ✅ | ✅ | Versions + release notes + troubleshooting |
| **Sync**     | ❌ | ✅ | Dev-sync overlay export/import via Proton/rclone |
| **Hosts**    | ❌ | ✅ | Multi-machine register: track + push to remote hosts |
| **Logs (raw events)** | ❌ | ✅ | Raw SSE event stream for debugging |
| **Settings → GitHub repo config** | ❌ | ✅ | Configure origin URL + push permissions |

The gating happens in the frontend via `data-edition` attributes — see
`app/frontend/index.html` and `app/frontend/app.js` for the gate
points.

---

## 4. Dev-sync overlay walkthrough

Ascendo separates "code that's in Git" from "operator state that
shouldn't be in Git" via a **dev-sync overlay** stored in your cloud
provider (Proton Drive via rclone is the default).

### What's in the overlay

Approximately:

- `CLAUDE.md`, `HANDOFF.md`, `PLAN.md` — agent context + per-session log
- `.env.local` — local secrets, API keys
- `.dev_sync_config.json` — provider config + manifest paths
- `~/.config/ascendo/ai.json` — AI provider credentials (redacted at rest)
- Machine-specific quickstart additions, branding overrides
- Anything else listed in `.gitignore` but valuable across machines

### One-time setup per machine

```bash
cd ~/Dev_Env/Ascendo
bash dev-sync/provider_setup.sh                  # configures rclone remote
```

This walks you through:

1. Picking a provider (Proton Drive, Dropbox, Google Drive, etc.)
2. OAuth-ing rclone against it
3. Writing `.dev_sync_config.json` with your remote name + manifest

### Day-to-day commands

```bash
# Push your overlay to the cloud (after you change a private file):
ascendo_sync                                     # helper shim
# or:
bash dev-sync-export.sh --dry-run --verbose      # preview
bash dev-sync-export.sh                          # actual upload

# Pull from cloud onto a fresh machine:
bash dev-sync-import.sh

# Verify Git is clean + overlay matches manifest:
bash dev-sync-verify-git.sh                      # public only
bash dev-sync-verify-full.sh                     # public + overlay

# Dashboard equivalent: Sync tab → Export / Import / Verify buttons
```

### What to do when

| Situation | Run |
|-----------|-----|
| Just changed a private file (`.env.local`, agent state) | `ascendo_sync` |
| Set up a new dev machine | `bash dev-sync-import.sh` |
| Clean up cloud quarantine after a pruned file | `bash dev-sync-purge-quarantine.sh` |
| Audit before a major refactor | `bash dev-sync-verify-full.sh` |
| `git push` a public commit | `ascendo_push` (just `git push origin main` under the hood) |

---

## 5. Adding a new package manager (cross-platform pattern)

The "make a change once, it ships everywhere" promise comes from the
contract. Here's how to honor it.

### Where things live

| Layer | What | Where |
|-------|------|-------|
| 4 — Core domain | Pydantic models, interfaces, orchestrator | `core/ascendo/` (OS-agnostic, never imports adapters) |
| 5 — Adapter Python | `IPackageManager` impl per OS | `adapters/<os>/ascendo_<os>/managers/<name>.py` |
| 6 — Native scripts | 5 phase scripts emitting JSON v1 sidecars | `adapters/<os>/scripts/<name>/{check,plan,apply,verify,cleanup}.{sh,ps1}` |

### The contract for a new manager

1. **Add a `SourceType`** enum value in `core/ascendo/models/package.py`
   if your manager represents a new source type. Regenerate the schema
   afterwards (`scripts/export-sidecar-schema.py`).
2. **Implement `IPackageManager`** in your adapter. The interface
   lives in `core/ascendo/interfaces/package_manager.py`. Methods:
   `is_available(host)`, `run_phase(phase, run, host, ...) -> Sidecar`,
   plus the cached identity properties.
3. **Write the 5 phase scripts.** Native bash on macOS / Linux,
   PowerShell on Windows. Use the `lib/ascendo_json.sh` (POSIX) or
   `lib/AscendoJson.psm1` (Windows) helpers — they handle JSON v1
   emission, run-id wiring, exit-code mapping.
4. **Wire into your adapter.** Add to `package_managers()` in
   `adapters/<os>/ascendo_<os>/adapter.py`. Add a health-check
   component if appropriate.
5. **Tests.** At minimum:
   - Unit tests in `adapters/<os>/tests/test_<name>_*.py` that mock
     the bash/PS subprocess and assert the parsed Sidecar shape.
   - A contract test in `tests/contract/` that exercises your manager
     through the orchestrator with a synthetic adapter.
6. **Validate end-to-end.** `bin/validate-macos.sh` (or
   `bin/validate-windows.ps1`) gives you a Stage-N harness — add a
   stage for your new manager.

### Reference implementations

- **macOS brew** (`adapters/macos/ascendo_macos/managers/brew.py` +
  `adapters/macos/scripts/brew/*.sh`) — canonical example of a Tier-A
  apply path.
- **macOS web manager** (`managers/web.py` + `lib/handlers/*.sh`) —
  example of a multi-handler manager that dispatches per-app to one
  of seven update mechanisms.
- **Windows winget** (`adapters/windows/ascendo_windows/managers/winget.py`
  + `adapters/windows/scripts/winget/*.ps1`) — canonical PowerShell
  pattern with stderr capture.

---

## 6. Adding a new platform (Tier 2 → Tier 1)

See [ADR-0006](docs/architecture/0006-two-tier-adapter-system.md) for
the full design. Cliff's notes:

1. **Start in `contrib/adapters/<os>/`** — Tier 2 means manifest +
   scripts + smoke test only. No requirement to implement every
   capability. Fallback paths in core handle missing managers
   gracefully.
2. **Build the minimum:** `IAdapter.detect()`, at least one
   `IPackageManager`, a `health_check()` reporting at least one
   component.
3. **After 3+ months without critical bugs** + at least one external
   user testing it, propose promotion to `adapters/<os>/`.
4. **Tier 1 bar:** full Pydantic + interface coverage, `INVENTORY` +
   `SCHEDULING` + `SNAPSHOTS` + `ELEVATION` capabilities wired,
   `bin/validate-<os>.sh` E2E harness, CI matrix slot, docs.

---

## 7. Writing a plugin

Plugins extend Ascendo without touching `core/` or `adapters/*`. See
[ADR-0007](docs/architecture/0007-plugin-manifest-v1.md) for the
manifest schema.

```bash
# Scaffold a new plugin from the template:
cp -r plugins/_template/ plugins/my-plugin/
edit plugins/my-plugin/manifest.toml             # set id, name, OS support, capabilities
edit plugins/my-plugin/<os>/{check,plan,apply,verify,cleanup}.{sh,ps1}

# Smoke test:
ascendo run --category plugin --plugin my-plugin --phase check --dry-run
```

Reference plugins:

- `plugins/dell-driver-update/` — first official plugin (Windows-only,
  wraps Dell Command Update CLI)
- `plugins/agent-clis/` — cross-OS agent CLI bundle (Claude Code,
  GitHub Copilot CLI, etc.)
- `plugins/nvidia-driver-update/` — Linux NVIDIA driver helper

---

## 8. Debugging

### Verbose logs

Every shim respects `ASCENDO_VERBOSE=1`:

```bash
ASCENDO_VERBOSE=1 ascendo_maintenance full       # traces every command
```

### Reading sidecars

```bash
# Find the latest run dir
latest=$(ls -t ~/.ascendo/runs/ | head -1)
ls ~/.ascendo/runs/$latest/

# Pretty-print one sidecar
ascendo runs json $latest --pretty | jq '.sidecars[] | select(.phase == "apply")'

# Or just cat — they're plain JSON
cat ~/.ascendo/runs/$latest/apply__brew.json | jq .

# The plain logs (with stderr captured) live next to the JSON:
cat ~/.ascendo/runs/$latest/apply__brew.log
```

### Replaying a run

There's no first-class replay yet — instead, `ascendo run --category X
--phase Y --run-id Z` reuses an existing run-id directory so the
sidecars accumulate in one place.

### Live SSE event stream (raw)

The dev edition's **Logs (raw events)** tab dumps the underlying SSE
stream verbatim. Equivalent CLI:

```bash
# Start an async run:
run_id=$(curl -s -X POST http://127.0.0.1:8765/runs/async \
  -H 'Content-Type: application/json' \
  -d '{"phases":["check"],"categories":["brew"]}' | jq -r .run_id)

# Watch the events:
curl -N http://127.0.0.1:8765/runs/$run_id/events
```

### Dashboard health endpoint

```bash
curl -s http://127.0.0.1:8765/health | jq .
```

Returns the same data the Overview health card renders — useful for
remote monitoring or CI gates.

---

## 9. Releasing

The release flow lives in `bin/run-tag-release-<os>.sh` and the
`packaging/` tree.

```bash
# macOS — interactive 7-stage flow (preflight → snapshot → plan →
# confirm → apply → verify → cleanup → tag):
bash bin/run-tag-release-macos.sh                # interactive 'apply' gate
bash bin/run-tag-release-macos.sh --whatif       # plan only

# Windows — equivalent flow:
.\bin\run-tag-release.ps1 -WhatIf                # preview
.\bin\run-tag-release.ps1                        # interactive 'apply' gate
```

### Build the installers

```bash
# Windows MSI + NSIS:
.\bin\build-installer.ps1                        # produces dist\Ascendo-<v>-x64.msi + .exe

# macOS .pkg + .dmg:
bash bin/launch-desktop-macos.sh --build         # produces .app + .dmg in target/release/bundle/

# Debian .deb:
cd packaging/deb && bash build.sh                # see packaging/deb/README.md
```

See [`packaging/README.md`](packaging/README.md) for the per-OS
signing + notarization steps (Apple Developer ID for macOS,
Authenticode for Windows, GPG for Debian).

### Tagging convention

Tag-name format: `v<major>.<minor>.<patch>` for stable, `v<x>.<y>.<z>-<phase>`
for development (`-alpha`, `-beta`, `-rc`). Pre-release tags don't
trigger the publish workflow; only `v<x>.<y>.<z>` does.

---

## 10. The dev-sync workflow rules

When to **export** (push your local state to cloud):

- After editing any file in the overlay (CLAUDE.md, HANDOFF.md,
  AGENTS.md, etc.)
- Before switching to another dev machine
- Before any major refactor where you might want a rollback

When to **import** (pull cloud state to local):

- Setting up a new dev machine
- After a colleague pushed an overlay update (rare; this is
  mostly single-user)
- Recovering from a corrupt `.env.local` or agent state file

When to **verify**:

- Before a `git push` (catches accidentally-tracked private files)
- After any installer / updater run (`bash dev-sync-verify-full.sh`)
- After a quarantine or purge operation

The Sync tab in the dashboard exposes all three operations as
buttons, with progress + log streaming.

---

## 11. AI provider configuration

The dev edition exposes the same AI config surface as Basic, but adds
provider-specific knobs:

```bash
# Edit credentials directly:
$EDITOR ~/.config/ascendo/ai.json

# Test a provider connection without writing creds to disk:
curl -X POST http://127.0.0.1:8765/ai/test-connection \
  -H 'Content-Type: application/json' \
  -d '{"provider":"anthropic","api_key":"sk-…","model":"claude-3-5-sonnet"}'
```

Schema for `ai.json`:

```json
{
  "providers": {
    "anthropic": { "api_key": "REDACTED", "default_model": "claude-3-5-sonnet" },
    "openai":    { "api_key": "REDACTED", "default_model": "gpt-4o" },
    "ollama":    { "base_url": "http://127.0.0.1:11434", "default_model": "llama3" }
  }
}
```

API keys are redacted in the dashboard but stored as plaintext on
disk. Do **not** commit this file (it's in `.gitignore` and the
overlay's hard-exclude list).

---

## 12. Adding a new helper script (`bin/user-scripts/`)

The PATH shims live in `bin/user-scripts/`. Conventions:

- POSIX scripts have no extension and start with `#!/usr/bin/env bash`
  + `set -euo pipefail`. Marked executable.
- PowerShell scripts share the base name + `.ps1`.
  `$ErrorActionPreference = 'Stop'` for fail-fast.
- Every shim is idempotent and forwards extra args to the underlying
  command (`exec "$@"` on POSIX, `& $cmd @args` on PS).
- They never write to disk under their own name — all logs / state
  lives under `~/.ascendo/` (POSIX) or `%USERPROFILE%\.ascendo\`
  (Windows).

Dev-only shims live under `bin/user-scripts/dev/` and are only
linked into PATH when the install profile is `dev`. See
[`bin/user-scripts/README.md`](bin/user-scripts/README.md) for the
complete inventory.

---

## 13. Public / private split

Everything in this repo is **public** unless explicitly listed in
`.gitignore`. The most important private items:

| File | Purpose | Travels via |
|------|---------|-------------|
| `CLAUDE.md` | Claude Code agent context for THIS project | Dev-sync overlay |
| `HANDOFF.md` | Per-session log (often >100 KB; full history) | Dev-sync overlay |
| `AGENTS.md` | Multi-agent coordination doc | Dev-sync overlay |
| `.env.local` | Secrets, API keys, machine-local config | Dev-sync overlay |
| `.dev_sync_config.json` | Provider config | Dev-sync overlay |
| `~/.config/ascendo/ai.json` | AI credentials | Dev-sync overlay |
| `APPS.md` | Per-machine "what's installed" snapshot | Dev-sync overlay |
| `dev-sync-overlay/` | The local materialized overlay | Never committed |

The `.example` versions of the agent files (`CLAUDE.md.example`,
`HANDOFF.md.example`, `AGENTS.md.example`) ARE committed and
demonstrate the schema for new contributors.

The `.gitignore` is the source of truth for the public/private split.
The dev-sync overlay tooling reads `.gitignore` to derive what to
upload — anything Git ignores is a candidate for the overlay (subject
to `dev-sync/HARD_EXCLUDE_PATTERNS` for things that should NEVER
travel, e.g. `.claude/worktrees/`, build artifacts, `node_modules/`).

---

## 14. Where to next

- **Architecture** — [docs/architecture/](docs/architecture/)
  - [ADR-0001 monorepo with adapters](docs/architecture/0001-monorepo-with-adapters.md)
  - [ADR-0003 JSON v1 sidecar contract](docs/architecture/0003-json-v1-sidecar-contract.md)
  - [ADR-0005 six-layer architecture](docs/architecture/0005-six-layer-architecture.md)
  - [ADR-0006 two-tier adapter system](docs/architecture/0006-two-tier-adapter-system.md)
  - [ADR-0007 plugin manifest v1](docs/architecture/0007-plugin-manifest-v1.md)
- **5-phase contract** — [docs/agents/contract.md](docs/agents/contract.md)
- **Cross-platform contract** — [docs/CROSS_PLATFORM.md](docs/CROSS_PLATFORM.md)
- **Forward roadmap** — [PLAN.md](PLAN.md)
- **Per-session history** — [HANDOFF.md](HANDOFF.md) (private — overlay only)
- **Contributor workflow** — [CONTRIBUTING.md](CONTRIBUTING.md)
- **Security** — [SECURITY.md](SECURITY.md)

License: [MIT](LICENSE) — do whatever you want, just keep the
copyright notice.
