# docs/architecture/

Architecture Decision Records (ADRs) — single-page rationale for each
significant architectural choice in Ascendo.

## Active ADRs

| # | Title | Status |
|---|---|---|
| 0001 | Monorepo with adapters per OS | Accepted |
| 0002 | Tauri as desktop shell | Accepted |
| 0003 | JSON v1 sidecar contract | Accepted |
| 0004 | Python core with native script adapters | Accepted |
| 0005 | Six-layer Clean Architecture | Accepted |
| 0006 | Two-tier adapter system (Tier 1 / Tier 2) | Accepted |
| 0007 | Plugin manifest v1 | Accepted |

## How to write a new ADR

1. Copy `templates/adr-template.md` to `NNNN-short-title.md` where NNNN is
   next sequential number
2. Set Status: Proposed initially
3. Open PR with the ADR for team review
4. Merge moves Status to Accepted
5. If superseded later, update Status and link to replacement ADR

## Reading order for new contributors

For grasping the overall architecture, read in this order:

1. `0001-monorepo-with-adapters.md` (the foundation)
2. `0005-six-layer-architecture.md` (clean architecture rules)
3. `0004-python-core-with-native-script-adapters.md` (key tradeoff)
4. `0003-json-v1-sidecar-contract.md` (the integration point)
5. `0002-tauri-as-desktop-shell.md` (UI strategy)
6. `0006-two-tier-adapter-system.md` (community contribution model)
7. `0007-plugin-manifest-v1.md` (extensibility)
