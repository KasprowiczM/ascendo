# CLAUDE.md — Ascendo

Cross-platform unified-updates app: one repo for **CLI + Web (FastAPI dashboard) + Desktop (Tauri 2.x)** across **Windows, Ubuntu, macOS**. Monorepo with shared `core/` + per-OS `adapters/`.

---

## Repository layout

```
core/                    # platform-agnostic Python: ascendo CLI, dashboard, schemas, models
adapters/
  windows/               # winget / msstore / registry_arp / windows_update / snapshot / scheduler / elevation
  ubuntu/                # apt / snap / brew / npm / pip / flatpak / drivers / firmware (legacy app/ being migrated here)
  macos/                 # brew / mas / time-machine / launchd  (stub — to be built)
ui/
  desktop-tauri/         # Tauri 2.x shell (one binary, all platforms) — Python sidecar = core dashboard
  frontend/              # vanilla SPA served by FastAPI dashboard (Inter Tight + JetBrains Mono, self-hosted)
plugins/                 # third-party phase scripts (e.g. dell-driver-update)
bin/                     # PowerShell + bash scripts (launch, install, validate, run-tag-release, build-installer)
packaging/
  msi/   pyinstaller/    # Windows installer artifacts
  deb/   homebrew-tap/   # Linux + macOS distribution
  winget-manifest/  pkg/ # winget submission, macOS .pkg
schemas/                 # JSON-Schema phase-result/v1, run/v1, plugin manifest
docs/superpowers/specs/  # design docs and per-session handoffs
```

Legacy Linux-only top-level paths (`app/`, `lib/`, `scripts/`, `update-all.sh`, `setup.sh`) are still present for transitional reasons and will fold into `adapters/ubuntu/` over time.

## CRITICAL workflow rule — NO new worktrees

**Always work directly in `D:/Dev_Env/Ascendo` on `main`.** Do not run `git worktree add` or otherwise spawn `.claude/worktrees/<name>/`. Earlier sessions accidentally created three parallel worktrees that had to be reconciled by hand. The rule is: one repo, one branch (`main`), commits go straight there and `git push origin main`.

If you need isolation for an experimental change, create a topic branch in the primary worktree and switch back to `main` when done. Never check it out elsewhere.

## Active branch + remote

- `main` is canonical. Origin: `https://github.com/KasprowiczM/ascendo.git`.
- `claude/windows-end-to-end-2026-05-02` is preserved on origin as a safety snapshot of the Windows MVP work; not for active development.
- `restructure/monorepo` is a historical anchor (the v0.0.7-alpha tag commit). Do not commit to it.

## Forward roadmap

Read these in order when picking up:

1. [PLAN.md](./PLAN.md) — what's next (single source of truth)
2. [HANDOFF.md](./HANDOFF.md) — session log (what already shipped)
3. Latest dated handoff in `docs/superpowers/specs/` — most recent context
4. [WINDOWS_QUICKSTART.md](./WINDOWS_QUICKSTART.md) — operator install + first run
5. [WINDOWS_TESTING.md](./WINDOWS_TESTING.md) — full test matrix

## Commands (Windows-first; Ubuntu/macOS commands live in their adapter docs)

```powershell
# Dev install (idempotent — safe to re-run after git pull)
.\bin\install-dev.ps1                  # core + adapters/windows + venv + smoke
.\bin\install-shortcut.ps1             # Desktop + Start menu icons

# CLI
python -m ascendo doctor                                                    # 5-component health
python -m ascendo run --category winget --phase check                       # one cat × one phase
python -m ascendo run --categories winget,msstore --phases check,plan       # multi
python -m ascendo runs list -n 10
python -m ascendo runs show <id>
python -m ascendo runs json <id> --pretty                                   # consolidated ascendo/run/v1
python -m ascendo snapshot {create|list|restore}
python -m ascendo schedule {install|remove|list|trigger}
python -m ascendo dashboard [--background] [--port 8765]

# Web
xdg-open http://127.0.0.1:8765    # or just open in a browser

# Desktop (Tauri 2.x, native window)
.\bin\launch-desktop.ps1                # dev (cargo run)
.\bin\launch-desktop.ps1 -Build         # produces target\release\bundle\{msi,nsis}\

# End-to-end smoke (real Windows hardware)
.\bin\validate-windows.ps1 -DashboardPort 8768
.\bin\run-tag-release.ps1 -WhatIf       # plan only, no mutation
.\bin\run-tag-release.ps1               # interactive 'apply' gate
```

## Phase contract (schema v1)

Every category × {check, plan, apply, verify, cleanup} script writes a JSON sidecar at
`logs/runs/<run-id>/<source>/<phase>__<source>.json` validated against
`schemas/phase-result.schema.json`. The orchestrator aggregates per-run summaries
into `run.json` (schema `ascendo/run/v1`). See [docs/agents/contract.md](./docs/agents/contract.md).

Exit codes (Windows: per `adapters/windows/lib/AscendoJson.psm1`):
0 ok · 1 warn · 2 bad-usage · 10 precondition · 11 lock · 12 timeout · 20 apply-fail-known · 30 apply-fail-unknown · 75 needs-reboot.

## Permission model

- **Windows:** Administrator elevation via `adapters/windows/ascendo_windows/managers/elevation.py` (UAC); SPA caches an in-memory elevation token after the user provides Administrator credentials, helper passes `RUN_AS_ADMIN=1` env var down to PowerShell phase scripts. **Do not refer to "sudo" in user-facing copy on Windows** — use "Administrator" / "Administrator elevation".
- **Ubuntu:** sudo cache via askpass helper in `$XDG_RUNTIME_DIR/ascendo/askpass-*.sh`.
- **macOS:** sudo + osascript (TBD when adapter built out).

## Planning rule (immutable)

Do not change code until you understand the request and the affected code well enough to be ≥95% certain what to build. In planning mode, read code, ask questions, validate assumptions multiple times.

## Context + log control

- Watch context fill; at ~60% summarise the working session into the latest handoff in `docs/superpowers/specs/<date>-*.md` and trim.
- Don't paste long logs into context — write to a file and `head`/`tail`/`grep` it.
- Don't commit `APPS.md` or `.env.local` (already gitignored).

## References

- @PLAN.md — forward roadmap (read first when resuming)
- @HANDOFF.md — historical session log
- @WINDOWS_QUICKSTART.md — Windows operator install + first run
- @WINDOWS_TESTING.md — full Windows test matrix
- @docs/agents/contract.md — 5-phase JSON sidecar contract
- @docs/agents/architecture.md — overall architecture
- @docs/agents/security.md — secrets, elevation, dev-sync rules
- @adapters/windows/README.md — Windows adapter internals
- @adapters/ubuntu/README.md — Ubuntu adapter internals (legacy, in migration)
- @ui/desktop-tauri/README.md — Tauri shell + Python sidecar
- @app/README.md — FastAPI + SPA dashboard (frontend lives in `app/frontend/`, will move to `ui/frontend/`)
