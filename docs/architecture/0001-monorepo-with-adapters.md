# ADR 0001: Monorepo with adapters per OS

- **Status:** Accepted
- **Date:** 2026-04-30
- **Deciders:** @KasprowiczM
- **Supersedes:** —

## Context

Ascendo unifies three previously independent repositories — `Ascendo`
(macOS, Bash 3.2), `Ascendo` (Windows, PowerShell), and
`Ascendo` (Linux, Bash + Python FastAPI + Tauri). All three solve
the same problem (orchestrating system updates) on a different OS, with
different package managers and different runtime constraints.

We had to choose a code organization model that serves four constraints:

1. **Atomic cross-OS changes** — modifying the JSON sidecar contract or a
   plugin manifest field needs to update core + every adapter in a single
   commit; otherwise the contract drifts and integration breaks.
2. **One brand, one URL** — Ascendo is open-source on GitHub. A single
   `KasprowiczM/ascendo` URL is dramatically easier to discover, link to,
   and contribute to than three siloed repos.
3. **Low onboarding friction** — a contributor cloning the repo should be
   able to read code paths end-to-end (frontend → API → core → adapter →
   native script) without juggling submodule URLs or repo permissions.
4. **Future OS additions are folder-additions, not repo-creations** — when
   FreeBSD, Fedora, or ChromeOS land as Tier 2, the cost should be writing
   a manifest and a few scripts, not setting up another repository.

The team disagreed early on whether to keep the three legacy repos for
backward compatibility. We resolved this by creating an explicit migration
tag (`pre-monorepo-restructure`) on the source repos so legacy state is
preserved without the operational overhead of three live repos.

## Decision

**Use a single Git repository (`KasprowiczM/ascendo`) with subdirectories
per architectural concern, not per OS.** OS-specific code lives under
`adapters/<os>/` and `contrib/adapters/<os>/`, but it is one repo, one CI
pipeline, one issue tracker, one release cadence.

The legacy repos are tagged at their last pre-merge commit and archived as
read-only references — not deleted, not actively maintained.

## Consequences

### Positive

- Single PR can change the JSON v1 contract in `core/`, the Linux emitter
  in `adapters/ubuntu/lib/`, and the Windows emitter in
  `adapters/windows/lib/` atomically. No drift.
- One CI matrix (Linux + Windows + macOS runners) validates everything on
  every push. Cross-OS regressions are caught before merge.
- One `CHANGELOG.md`, one release tag, one set of GitHub Releases binaries.
- Contributors can grep across all OS adapters to find prior art when
  implementing a new package manager (`grep -r "winget upgrade" adapters/`
  shows how the existing Windows code handles this exact thing).
- Path-based import linter rules (`core/` MUST NOT import from `adapters/*`)
  are simple to enforce in a monorepo and impossible to enforce across
  separate repos.

### Negative

- Repo size grows over time (3 OS × 2 tiers × scripts × tests). Mitigated
  by `.gitignore` discipline and Git LFS only if/when binary assets land
  (none yet).
- `git clone` downloads code for OSes you may never run. Acceptable —
  uncompressed source for 3 OSes is < 50 MB.
- A bad commit can break all three OSes simultaneously. Mitigated by
  branch protection + required CI on PR.
- New contributors face a larger codebase from day one. Mitigated by clear
  per-folder `README.md` files and the
  [`HANDOFF.md`](../../HANDOFF.md) entry-point document.

### Neutral

- Submodule purists are unhappy. We deliberately do not use submodules —
  the operational cost of submodule pinning, recursive clone, and detached
  HEAD states is well-known and not worth the theoretical isolation.
- `core/` and `adapters/<os>/` each have their own `pyproject.toml`. Tools
  like `uv` and `hatch` workspace mode handle this cleanly; older tools
  may need explicit per-folder install.

## Alternatives Considered

### Alternative 1: Multi-repo (one per OS, plus core)

Description: Keep the existing three repos and add `ascendo-core` as a
fourth. Core is published to PyPI; adapters depend on it.

Why rejected:
- Cross-OS contract changes require coordinated PRs across 4 repos with
  matching version pins. Extremely error-prone.
- Releases must be lockstepped — `ascendo-core 1.2.0` requires
  `ascendo-ubuntu 1.2.0` and `ascendo-windows 1.2.0`. Any drift breaks
  contract tests.
- Issue tracking is fragmented — users can't tell which repo to file a
  bug against until they've already debugged it.
- Quadruples the GitHub Actions setup work.

### Alternative 2: Monorepo with packages per concern (no `adapters/` folder)

Description: One repo, but flat package layout — `windows-package-manager/`,
`linux-package-manager/`, `dashboard/`, `plugins/`, all siblings.

Why rejected:
- Loses the `core/` ↔ `adapters/` architectural firewall. Without that
  separation, anyone can `from windows_package_manager import ...` from
  the dashboard, and the OS-agnostic-core promise is silently violated.
- Makes Tier 1 vs Tier 2 (ADR-0006) harder to express in the layout.
- Adapter implementers can't easily see all the implementations of one
  interface.

### Alternative 3: Polyrepo with shared submodule for contracts

Description: Three OS repos + one `ascendo-contracts` submodule that
defines JSON sidecar schema, plugin manifest, etc.

Why rejected:
- All the multi-repo problems plus submodule operational pain.
- Contract changes still require coordinated PRs to update submodule
  pointers in three repos.
- Tooling (CI, IDE, search) consistently breaks on submodules.

## References

- Related ADRs: [0004](0004-python-core-with-native-script-adapters.md)
  (the firewall), [0005](0005-six-layer-architecture.md) (layering inside
  the monorepo), [0006](0006-two-tier-adapter-system.md) (community vs.
  official adapters)
- HANDOFF.md — section "FAZA 2 — Wariant A (zatwierdzony)"
- Migration tag: `pre-monorepo-restructure` on this repo
- External reference: [trunk-based development in monorepos](https://trunkbaseddevelopment.com/monorepos/)
