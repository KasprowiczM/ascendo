# Changelog

All notable changes to Ascendo will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **Monorepo restructure (M1 milestone in progress).** Repository renamed
  from `Ubuntu_Aktualizacje` to `ascendo`. New structure under
  `core/`, `adapters/{ubuntu,windows,macos}/`, `plugins/`, `contrib/`,
  `ui/`, `packaging/`, `website/`, `docs/`, `tests/`.
- `HANDOFF.md` — implementation handoff document (single source of truth
  for cross-session work continuity)
- `.gitattributes` — explicit per-file-type line-ending policy
  (LF for source code, CRLF for `.ps1`/`.bat`/`.cmd`) to prevent CRLF
  issues on cross-OS clones
- `.pre-commit-config.yaml` — gitleaks, ruff, mypy, shellcheck,
  PSScriptAnalyzer, markdownlint, plugin manifest validation
- ADR templates and first 7 architecture decision records
- Two-tier adapter system: Tier 1 (`adapters/<os>/`, official) and
  Tier 2 (`contrib/adapters/<os>/`, community)

### Changed

- JSON sidecar schema name will migrate from `ubuntu-aktualizacje/v1` to
  `ascendo/v1` in M2. Reader accepts both during migration.
- Repository origin: new GitHub repo at
  https://github.com/KasprowiczM/ascendo (replaces local clone parent
  `D:\Dev_Env\Ubuntu_Aktualizacje`)

### Migration source

The pre-restructure state is preserved at git tag
[`pre-monorepo-restructure`](https://github.com/KasprowiczM/ascendo/releases/tag/pre-monorepo-restructure)
for rollback if needed.

---

## Pre-monorepo history (Ubuntu_Aktualizacje legacy)

The following entries are from the source project before rename + restructure.

### [Etap 12] - 2026-04-XX

- Inventory candidate fix
- Unified Updates rename (Ascendo brand introduction)
- Tauri shell prototype
- Hybrid CLI/Dashboard mode
- Snapshot tooling (timeshift / etckeeper)
- Scheduler (systemd timers)
- Plugin system infrastructure (manifest validator)
- Dev-sync GitHub + Proton overlay

For full pre-monorepo history, see git log:
```bash
git log --oneline pre-monorepo-restructure
```

[Unreleased]: https://github.com/KasprowiczM/ascendo/compare/pre-monorepo-restructure...HEAD
