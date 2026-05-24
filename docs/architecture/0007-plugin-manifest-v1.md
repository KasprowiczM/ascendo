# ADR 0007: Plugin manifest v1

- **Status:** Accepted
- **Date:** 2026-04-30
- **Deciders:** @KasprowiczM
- **Supersedes:** —

## Context

Ascendo separates **adapters** (per-OS implementations of base interfaces:
package management, snapshots, scheduling) from **plugins** (optional
extensions that add specific capabilities: Dell driver updates, NVIDIA
driver updates, agent CLI maintenance, etc.).

A plugin needs to declare:

- **What it does** — human readable and machine parseable.
- **Which OSes it supports** — Dell driver update is Windows-only;
  NVIDIA driver update on Linux is Linux-only; agent CLIs are cross-OS.
- **What privilege it needs** — `user`, `sudo`, or `admin`. The
  orchestrator must decide whether to elevate before running it.
- **What risk it carries** — `low` / `medium` / `high`. Affects whether
  the user must manually confirm in interactive mode and whether a
  snapshot is required first.
- **Which phases it implements** — `check`, `plan`, `apply`, `verify`,
  `cleanup` — and what command to run for each.
- **What dependencies must exist** — binaries (`dcu-cli` for Dell, `nvidia-smi`
  for NVIDIA), Python modules, other plugins.
- **What it reports back** — does it emit a JSON v1 sidecar (yes — that's
  required) and does it have any plugin-specific reporting fields.

Without a manifest, plugins become folders of scripts with implicit
contracts. The pre-merge `Ascendo` repo had a plugin
infrastructure prototype; we formalize and version it.

## Decision

**Adopt a TOML-based manifest format named `manifest.toml`, schema-versioned
as `schema = "ascendo-plugin/v1"`, located at the root of every plugin
directory.** Required for both Tier 1 (`plugins/<id>/`) and Tier 2
(`contrib/plugins/<id>/`).

### Manifest structure (v1)

```toml
schema = "ascendo-plugin/v1"

[plugin]
id            = "dell-driver-update"
display_name  = "Dell Command Update"
description   = "Manages Dell-supplied driver and firmware updates via dcu-cli."
version       = "0.1.0"
maintainer    = "KasprowiczM"
license       = "MIT"
tier          = "official"           # official | contrib
homepage      = "https://github.com/KasprowiczM/ascendo/tree/main/plugins/dell-driver-update"

[runtime]
privilege     = "admin"              # user | sudo | admin
risk          = "high"               # low | medium | high
manual_confirm= true                 # require user OK in interactive mode
timeout_sec   = 1800
phases        = ["check", "apply", "verify"]
supported_oses= ["windows"]

[dependencies]
binaries        = ["dcu-cli.exe"]
python_modules  = []
plugins         = []

[scripts.windows]
check    = "windows/check.ps1"
apply    = "windows/apply.ps1"
verify   = "windows/verify.ps1"

[config]
# Plugin-specific configuration the user can override in
# ~/.ascendo/plugins/<id>.toml. Schema is plugin-defined.
default_severity = "recommended"

[reporting]
# Hint to the dashboard about how to render this plugin's output.
sidecar_schema = "ascendo/v1"
extra_fields   = ["dell_severity", "dell_category", "reboot_required"]
```

### Schema validation

A Pydantic v2 model in `core/ascendo/plugins_loader/manifest.py` is
authoritative. The pre-commit hook
(`tests/validate_plugin_manifests.py`) runs against every changed
`plugins/*/manifest.toml` and `contrib/plugins/*/manifest.toml` on
`git commit`.

### Plugin discovery

At Ascendo startup, the plugin loader scans:

1. `plugins/*/manifest.toml` (Tier 1, bundled).
2. `contrib/plugins/*/manifest.toml` (Tier 2, bundled).
3. `~/.ascendo/plugins/*/manifest.toml` (user-installed).
4. `/etc/ascendo/plugins/*/manifest.toml` (system-wide, optional).

Manifests are validated; failures are logged but do not crash the loader.
Successful manifests are cached in SQLite for fast startup.

### Plugin SDK boundary

Plugins MUST NOT import from `core/` or from `adapters/`. They may
import from `core.ascendo.plugins_loader.api` only — a deliberately
narrow surface that re-exports:

- `PluginContext` — run id, phase, dry-run flag, profile, logger.
- `emit_sidecar(...)` — helper to write a JSON v1 sidecar correctly.
- `RunReport` — the structured response the orchestrator expects back.

This is enforced by `import-linter` (see ADR-0005) at CI time.

## Consequences

### Positive

- **Static manifest, dynamic discovery.** Plugins are inspectable
  without execution. The dashboard can list available plugins, their
  privilege requirements, and their risk levels before any script runs.
- **TOML is human-friendly.** Easier to author and review than YAML
  (no whitespace foot-guns) or JSON (no comments).
- **Scaffold-driven plugin authoring.** `plugins/_template/` is a
  ready-to-copy starting point. Authors fill in the manifest and
  scripts; everything else is wired by the loader.
- **Privilege + risk in the manifest.** The orchestrator can refuse to
  run a `privilege="admin"` plugin if the user isn't elevated, and can
  require a snapshot before running `risk="high"` plugins. Policy is
  declarative.
- **Versioned schema.** Future breaking changes (`ascendo-plugin/v2`)
  can coexist with v1 plugins for a deprecation cycle.
- **Cross-OS plugin authoring is natural.** A plugin like `agent-clis`
  declares `supported_oses = ["linux", "windows", "macos"]` and ships
  three script trees. The manifest tells the loader which one to invoke.

### Negative

- **TOML parsing required at startup.** Adds ~1 ms per plugin to the
  startup path. Mitigated by caching parsed manifests in SQLite.
- **Manifest authoring takes 10 minutes for a trivial plugin.** Some
  contributors will see this as overhead. Mitigated by `_template/`
  having sensible defaults so most fields can be left as-is.
- **Schema evolution discipline required.** Adding a required field
  is a breaking change. We adopt the same rule as ADR-0003: new
  fields are optional with sensible defaults; schema-version bumps
  only on truly incompatible changes.
- **Plugin signing is not in v1.** A malicious plugin can do harm
  if installed. Mitigated by the official plugins being audited as
  part of the main repo, and by the privilege/risk fields making
  intent visible. Signing planned for M6 / v1.0.

### Neutral

- The manifest format is bounded. If a plugin needs configuration
  beyond what the manifest expresses, it lives in
  `~/.ascendo/plugins/<id>.toml` (user) or in the manifest's
  `[config]` table (defaults). We deliberately do not make the
  manifest a Turing-complete config language.

## Alternatives Considered

### Alternative 1: JSON manifest

Description: `manifest.json` instead of `manifest.toml`.

Why rejected:
- No comments. Plugin maintainers want to leave breadcrumbs explaining
  why a particular timeout is high or why an OS is excluded. Comments
  are essential for review-time trust.
- Trailing-comma footgun.
- TOML is the de-facto Python package metadata format (`pyproject.toml`).
  Consistency wins.

### Alternative 2: YAML manifest

Description: `manifest.yaml`.

Why rejected:
- Whitespace sensitivity. Plugin authors will produce manifests that
  look right but parse wrong.
- Multiple YAML parsers in the wild disagree on edge cases.
  TOML's spec is tighter.

### Alternative 3: Python module with declarations

Description: `plugin.py` exporting a `Plugin` dataclass.

Why rejected:
- Conflates manifest with code. A malicious plugin's manifest could
  execute arbitrary code at discovery time. Static TOML is safe.
- Harder to validate from non-Python tools (CI lint checks, GitHub
  Actions, plugin marketplaces).

### Alternative 4: Just a folder convention, no manifest

Description: Convention over configuration — if `plugins/<id>/check.sh`
exists, that's the check phase.

Why rejected:
- No place to declare privilege, risk, or supported OSes. Orchestrator
  has to either run blind or shell out to the script just to ask.
- No way to express cross-OS plugins (`apply.sh` for Linux/macOS but
  `apply.ps1` for Windows) without conventions that get progressively
  more elaborate.
- Discoverability suffers — the dashboard can't list plugin metadata
  without running each plugin.

## References

- Related ADRs: [0003](0003-json-v1-sidecar-contract.md) (the runtime
  contract — manifest declares it; sidecar carries it), [0005](0005-six-layer-architecture.md)
  (plugins as Layer 6), [0006](0006-two-tier-adapter-system.md) (Tier 1
  vs. Tier 2 plugin folders)
- Manifest model: `core/ascendo/plugins_loader/manifest.py` (M2)
- Validator: `tests/validate_plugin_manifests.py`
- Scaffold: `plugins/_template/`
- Pre-merge prototype: `Ascendo/plugins/example/manifest.toml`
