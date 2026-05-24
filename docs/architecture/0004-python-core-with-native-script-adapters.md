# ADR 0004: Python core with native script adapters

- **Status:** Accepted
- **Date:** 2026-04-30
- **Deciders:** @KasprowiczM
- **Supersedes:** —

## Context

Ascendo absorbs three pre-existing codebases that each took years and many
bugfix iterations to get right:

- **Windows (PowerShell)** has hidden gems that took 6+ patch cycles to
  surface — column-position parser for `winget upgrade` (header-row offset
  detection, UTF-8 ellipsis handling), unknown-version suppression with
  local AppX/MSIX evidence, `NativeInstallPaths` whitelist for tools that
  ship native installers (Claude Code), exit-code mapping
  (`-1978335190`/`-1978335212`/`3010`), separator-before-header detection.
- **Linux (Bash)** has the FastAPI dashboard, the JSON v1 sidecar emitter,
  the snapshot integration (timeshift/etckeeper), the dev-sync overlay
  with cleanup_protected_patterns, the systemd timer scheduler, and a
  full plugin manifest validator.
- **macOS (Bash 3.2)** has the i18n loader covering 7 languages, the DMG
  verification chain (`hdiutil` + `spctl` + `pkgutil`), session-dir +
  `trap EXIT` cleanup pattern, Keystone integration, App Store two-track
  detection.

Rewriting any of this in Python loses years of bugfix history embedded
in the live code. Yet a unified user experience needs *some* shared
language for orchestration, persistence, and HTTP. We had to choose
**how much of the existing native code stays native**, and **what
language the cross-cut code is written in**.

The disagreement was on whether to fully Pythonize (rewrite everything)
or fully outsource (keep three repos, just rebrand). We rejected both
extremes. The middle path — Python core + native scripts retained as
adapters — is what ADR-0001 calls "Wariant A."

## Decision

**Python is the language of the core (Layer 4) and the Python adapters
(Layer 5). Native scripts (Bash on Linux/macOS, PowerShell on Windows)
remain the actual mutator code (Layer 6). They are not rewritten.**

The Python core:
- Owns the orchestration loop (5-phase contract, run lifecycle, lock
  file, cancellation).
- Owns the JSON v1 sidecar contract (ADR-0003) and persistence (SQLite).
- Owns the dashboard backend (FastAPI) and the CLI (Typer).
- Defines the interfaces (`IPackageManager`, `IScheduler`, `ISnapshot`,
  `IInventory`, etc.) that adapters implement.

The Python adapters:
- Live in `adapters/<os>/ascendo_<os>/` (Layer 5).
- Implement the interfaces by spawning native scripts with structured
  arguments and parsing the JSON v1 sidecars they emit.
- Hold OS-specific imports (`pywin32`, `python-apt`, `pyobjc-*`).

The native scripts:
- Live in `adapters/<os>/scripts/` and `adapters/<os>/lib/` (Layer 6).
- Are the actual subjects of the existing six-iteration bugfix history.
- Emit JSON v1 sidecars and only that as their structured output.

**Promotion-on-demand:** if a piece of native logic turns out to be
needed cross-OS (e.g. version-comparison rules that match `dpkg
--compare-versions` semantics), we promote it into core Python — but
only when the third OS proves the need. We do not pre-port code "just in
case."

## Consequences

### Positive

- **90% reuse of the most mature codebase** (Linux/Ascendo
  — FastAPI backend, JSON contract, plugin loader, scheduler, snapshot
  integration, dev-sync). These move into `core/` and `adapters/ubuntu/`
  with mechanical refactoring, not rewriting.
- **100% reuse of PowerShell hidden gems.** Every line of the column
  parser, the unknown-version suppression state machine, the exit-code
  mapping — all of it stays in PowerShell where it was tuned.
- **Time-to-MVP measured in weeks, not months.** A full Pythonization
  estimate was 4-9 months single-dev; the adapter-retention path is
  6-8 weeks for Linux + Windows MVP.
- **Clean architectural boundary.** The JSON v1 sidecar (ADR-0003) is
  the contract. There's a precise, testable line where Python ends and
  native code begins.
- **Adding macOS = adding a 4th implementation of interfaces, not
  changing core.** This is the test that proves the architecture is
  right.
- **Future migration door is open.** If we ever decide to rewrite parts
  of core in Go or Rust (e.g. for static binary distribution), the JSON
  sidecar contract is the only API the adapters know about. Core
  language is replaceable; the contract is not.

### Negative

- **Two languages for contributors.** Native-script work is in Bash or
  PowerShell, depending on OS. Cross-cut work is in Python. Contributors
  pick a side, but the project as a whole has no single language.
  Mitigated by per-folder READMEs and clear interface boundaries.
- **Subprocess overhead per phase.** Each `check`/`plan`/`apply` spawns
  a fresh shell. Overhead is ~50-200 ms per spawn — acceptable for
  human-driven and scheduled runs.
- **Encoding hazards at the boundary.** PowerShell + UTF-8 + Bash + JSON
  all need to agree. The pre-merge code already solved this
  (`[Console]::OutputEncoding = UTF8` in PowerShell, `LANG=C.UTF-8`
  before Bash JSON emission). We carry this discipline forward.
- **Plugin authors must learn the JSON v1 contract** to ship a manifest.
  Mitigated by `plugins/_template/` scaffold and a manifest validator
  in pre-commit.

### Neutral

- The "right" language for shipping a self-contained binary on Windows
  is debated (Go, Rust, .NET). Python with PyInstaller works today and
  is well-understood. We accept the bundle size (~30-50 MB) as a v0.x
  trade-off.

## Alternatives Considered

### Alternative 1: Full Pythonization

Description: Rewrite all PowerShell and Bash logic as Python modules
inside `core/`. Use `pywin32` / `python-apt` / `pyobjc` for native calls.

Why rejected:
- Estimated 4-9 months single-dev. Project never ships v0.1.
- Loses years of native-script bugfix history. Re-discovering corner
  cases (winget exit codes, dpkg lock contention, mas CVE-2025-43411)
  costs another year on top.
- Forces native modules (`pywin32`, `python-apt`) into core, which would
  defeat ADR-0001's core-vs-adapters separation.
- `python-apt` is broken in PyInstaller bundles; `pyobjc` is large and
  fragile across macOS versions; `pywin32` requires per-Python-version
  builds.

### Alternative 2: Pure native scripts + lightweight orchestrator

Description: Keep three repos. Orchestrate via a thin shell wrapper
that invokes the right OS's scripts. No Python core.

Why rejected:
- Kills the dashboard, the scheduler, the run history, the plugin system
  — all currently Python. Project regresses to the pre-merge state.
- No cross-OS consistency in reporting. Each OS prints its own
  ad-hoc summary.
- Scaling to a fourth OS requires writing a new orchestrator, not
  registering an adapter.

### Alternative 3: Go or Rust core, native scripts retained

Description: Same architecture but Go/Rust instead of Python.

Why rejected (for v0.x):
- `app/backend/` (FastAPI) is already Python. Rewriting it in Go is 4-6
  weeks of net-zero work for v0.1.
- Frontend SPA + Tauri shell already speaks HTTP+JSON. The backend
  language is opaque to them.
- Re-evaluate at M3 if PyInstaller bundle becomes a real distribution
  problem (signing, antivirus, size).

## References

- Related ADRs: [0001](0001-monorepo-with-adapters.md), [0003](0003-json-v1-sidecar-contract.md),
  [0005](0005-six-layer-architecture.md), [0006](0006-two-tier-adapter-system.md)
- Native bugfix history (PowerShell):
  `Ascendo/CLAUDE.md` — "Known Issues & Mitigations"
- Native bugfix history (Bash):
  `Ascendo/docs/agents/critical_rules.md`
- HANDOFF.md — section "Dlaczego Wariant A (Python core + native scripts adapters)?"
