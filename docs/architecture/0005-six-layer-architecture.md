# ADR 0005: Six-layer Clean Architecture

- **Status:** Accepted
- **Date:** 2026-04-30
- **Deciders:** @KasprowiczM
- **Supersedes:** —

## Context

ADR-0001 chose a monorepo. ADR-0004 chose Python core + native script
adapters. We still need to define **how the components inside that
monorepo depend on each other** — what's allowed to import what, who
crosses the network boundary, who knows about which OS.

Without explicit rules, the codebase will accrete cross-cutting imports
quickly: the dashboard imports from a Linux helper, a Windows adapter
imports from another Windows adapter, a plugin imports from core. Each
short-cut is locally rational and globally fatal. The Linux pre-merge
codebase (`Ascendo`) already had this problem — `app/backend/`
referenced `lib/` and `scripts/` directly, which is fine when there's
only one OS but breaks when adding two more.

We adopted Clean Architecture (Robert C. Martin) as the discipline:
**dependencies point inward, never outward; outer layers know about
inner layers, never the reverse.** We mapped the existing Ascendo
components to six concrete layers.

## Decision

**Six layers, with a strict dependency rule: layer N may import from
layer N or any inner layer; never from an outer layer.** Specifically:

```
┌──────────────────────────────────────────────────────────────────┐
│ Layer 1 — Frontend SPA (vanilla JS, HTML, CSS)                   │
│   ui/frontend/                                                   │
│   knows: HTTP+JSON contract with Layer 3 ONLY                    │
│   ↓ talks to ↓                                                    │
├──────────────────────────────────────────────────────────────────┤
│ Layer 2 — Tauri shell (Rust, ~80 LOC)                            │
│   ui/desktop-tauri/                                              │
│   knows: how to spawn Layer 3, open a webview at its URL         │
│   ↓ spawns ↓                                                      │
├──────────────────────────────────────────────────────────────────┤
│ Layer 3 — Backend HTTP (FastAPI, Python)                         │
│   core/ascendo/dashboard/                                        │
│   knows: REST routes, SSE streaming, delegates to Layer 4        │
│   ↓ uses ↓                                                        │
├──────────────────────────────────────────────────────────────────┤
│ Layer 4 — Core domain (Python)                                   │
│   core/ascendo/{interfaces,models,orchestrator,                  │
│                 adapter_factory,scheduler,snapshot,...}          │
│   knows: NOTHING about specific OSes; only interfaces            │
│   ↓ depends on interfaces ↓                                       │
├──────────────────────────────────────────────────────────────────┤
│ Layer 5 — Adapter Python (per OS)                                │
│   adapters/<os>/ascendo_<os>/                                    │
│   knows: how to satisfy interfaces from Layer 4 by               │
│           orchestrating Layer 6                                  │
│   ↓ shells out to ↓                                               │
├──────────────────────────────────────────────────────────────────┤
│ Layer 6 — Native scripts                                         │
│   adapters/<os>/scripts/{check,plan,apply,verify,cleanup}        │
│   adapters/<os>/lib/                                             │
│   plugins/<id>/<os>/<phase>                                      │
│   knows: how to mutate the OS; emits JSON v1 sidecars            │
└──────────────────────────────────────────────────────────────────┘
```

### Concrete dependency rules

1. **`core/ascendo/` MUST NOT import from `adapters/*` or `plugins/*`.**
   Enforced by `import-linter` configured in `core/pyproject.toml`.
2. **`adapters/<os>/` MUST NOT import from `adapters/<other-os>/`.**
   Enforced by `import-linter`.
3. **Plugins MUST NOT import from any adapter or from core's internals.**
   They consume only the documented `core/ascendo/plugins_loader/api.py`
   surface (the "plugin SDK"). Enforced by import-linter + plugin smoke
   tests.
4. **The frontend SPA MUST NOT call native scripts directly.** It speaks
   only HTTP to Layer 3.
5. **Native scripts MUST NOT call back into Python.** The communication
   is one-directional: Python spawns script, script emits JSON sidecar
   to disk, Python reads the sidecar.
6. **Layer 3 (FastAPI) MUST NOT call native scripts directly.** It
   delegates to Layer 4 orchestrator, which delegates to Layer 5
   adapters, which call Layer 6 scripts.

## Consequences

### Positive

- **Adding a new OS is a Layer 5+6 task only.** No core changes. No
  dashboard changes. No frontend changes. The architectural promise of
  ADR-0001 is enforceable, not just aspirational.
- **Tests at every boundary.** Core tests mock interfaces (no OS
  required). Adapter tests fake the JSON sidecar parser. Native scripts
  have their own bash/pester tests. The pyramid is natural.
- **Replaceable layers.** Want a Go backend in v2? Replace Layer 3
  without touching Layers 1, 4, 5, 6. Want a different shell than
  Tauri? Replace Layer 2 without touching anything else.
- **The frontend can be developed independently.** A web developer with
  no Linux/Windows/macOS knowledge can work on Layer 1 against a mocked
  Layer 3.
- **Plugin authors don't see core internals.** They get a stable SDK
  surface that's narrower than "everything in `core/`."

### Negative

- **More indirection.** A "simple" feature like "add a button that
  upgrades all apt packages" touches all 6 layers — frontend route,
  REST endpoint, core orchestrator method, Linux adapter implementation,
  bash script. Mitigated by clear scaffolding patterns; once you've
  done one feature you've done them all.
- **`import-linter` configuration must be maintained.** Adding a new
  package under `core/` requires updating the layered contract.
  Acceptable cost.
- **Cross-cutting features (logging, metrics, errors) need explicit
  pass-through.** A tracing context generated in Layer 4 has to be
  serialized into the JSON sidecar to reach Layer 6 logs. The pre-merge
  code already has this with `phase` JSON; we extend it.

### Neutral

- This is **textbook Clean Architecture**, not a novel pattern. Junior
  contributors find docs and books to learn from. Senior contributors
  can apply muscle memory.

## Alternatives Considered

### Alternative 1: Three-layer (frontend / backend / OS)

Description: SPA → FastAPI → "OS layer" (mixed Python + scripts).

Why rejected:
- "OS layer" hides the core ↔ adapter split that ADR-0004 made
  central. Without that boundary, core ends up importing OS-specific
  code, defeating cross-OS portability.
- No place for Tauri (Layer 2). Either bundled into "frontend" (where
  it doesn't belong because it's Rust) or "backend" (where it doesn't
  belong because it's not HTTP).

### Alternative 2: Hexagonal architecture (ports + adapters, no layers)

Description: Define ports (interfaces); plug any adapter into any port.
No vertical layering.

Why rejected:
- Loses the natural top-to-bottom flow of "request → orchestration →
  OS work → response." Junior contributors find layered diagrams more
  approachable than a port-cluster diagram.
- Hexagonal is functionally what we have inside Layer 4 (interfaces ↔
  factory ↔ adapter implementations). The 6-layer model is the
  outer-shell view; hexagonal is the inner-detail view. Not a conflict.

### Alternative 3: Microservices

Description: Each OS adapter is a separate process that the dashboard
talks to over HTTP/gRPC.

Why rejected:
- A 50-line apt-update script does not need its own HTTP server.
- Massive operational overhead (process supervisors, ports, auth between
  components, packaging) for zero gain on a single-machine local tool.

## References

- Related ADRs: [0001](0001-monorepo-with-adapters.md) (the folder
  structure), [0004](0004-python-core-with-native-script-adapters.md)
  (the language split), [0007](0007-plugin-manifest-v1.md) (how plugins
  fit into Layer 6)
- Robert C. Martin, *Clean Architecture* (2017)
- Import linter: https://import-linter.readthedocs.io/
- Configuration: `core/pyproject.toml` `[tool.importlinter]` section
