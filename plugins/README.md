# plugins/

First-party (Tier 1) plugins for Ascendo. Each plugin is a self-contained
extension that adds a category of work to the orchestrator without modifying
core.

## Structure

```
plugins/
├── agent-clis/             # AI agent CLIs (Claude, Codex, Gemini, Qwen, OpenCode) — cross-OS
├── dell-driver-update/     # Dell Command Update wrapper — Windows only
├── nvidia-driver-update/   # NVIDIA driver via apt + dkms — Linux only
└── _template/              # Scaffold for new plugin authors
```

## Plugin manifest

Every plugin has `manifest.toml` with `schema = "ascendo-plugin/v1"`. See
`docs/architecture/0007-plugin-manifest-v1.md` for full spec.

## Phase scripts

Each plugin implements 5-phase contract (`check`, `plan`, `apply`, `verify`,
`cleanup`) per supported OS:

```
plugins/<id>/
├── manifest.toml
├── linux/{check,plan,apply,verify,cleanup}.sh
├── windows/{check,plan,apply,verify,cleanup}.ps1
└── macos/{check,plan,apply,verify,cleanup}.sh
```

Scripts emit JSON v1 sidecar via `lib/json_emit.{sh,ps1}` (provided by the
adapter for the running OS) on the path passed via `--json-out`.

## Tier 2 (community) plugins live in `contrib/plugins/`

## See also

- `docs/plugin-author-guide.md` — how to write a plugin
- `plugins/_template/` — copy-paste starting point
