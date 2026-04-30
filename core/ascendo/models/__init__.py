"""Pydantic v2 models for Ascendo domain types.

Models:
- Package, PackageRef, Version, Source — package abstraction
- Run, RunId, Phase, PhaseResult — execution state
- Sidecar (ascendo/v1) — JSON sidecar emitted by phase scripts
- PluginManifest (ascendo-plugin/v1) — plugin manifest schema
- Profile, Category — user-facing config types
- HostOverlay — config/hosts.toml entries

See `docs/architecture/0003-json-v1-sidecar-contract.md` for the sidecar
schema. M2 (Core skeleton) milestone implements these.
"""
