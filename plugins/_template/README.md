# Plugin Template

This is a scaffold for new Ascendo plugins. **Copy this folder** to start a
new plugin:

```bash
# Linux/macOS:
cp -r plugins/_template plugins/your-plugin-id

# Windows (PowerShell):
Copy-Item -Recurse plugins/_template plugins/your-plugin-id
```

Then:

1. **Edit `manifest.toml`** — set `id` (must match folder name), `display_name`,
   `description`, `version`, `maintainer`, `tier`, `risk`, `phases`,
   `supported_oses`.
2. **Implement phase scripts** in `linux/`, `windows/`, `macos/` for each OS
   you support. Skeletons are provided.
3. **Validate** the plugin:
   ```bash
   python tests/validate_plugin_manifests.py plugins/your-plugin-id/
   ```
4. **Test locally**:
   ```bash
   ascendo plugin run your-plugin-id --phase=check
   ascendo plugin run your-plugin-id --phase=apply --dry-run
   ```
5. **Submit PR** if you want it merged into Ascendo's official plugins
   (Tier 1) or place it in your own repo (Tier 2 distribution).

## Phase script contract

Each phase script receives these arguments:

| Flag | Description |
|---|---|
| `--run-id <id>` | Run identifier (e.g. `2026-04-30T10-23-45Z-mk-uP5520-7f3a`) |
| `--json-out <path>` | Where to write the JSON v1 sidecar |
| `--log <path>` | Where to write the human-readable log |
| `--profile <name>` | Active profile (`quick`/`safe`/`full`) |
| `--config <path>` | Path to plugin's config dir (for additional config files) |
| `--dry-run` | If present, must not mutate state — only simulate |

Each phase script MUST emit a JSON v1 sidecar to `--json-out`. Use the helpers:

- **Linux/macOS:** `source "${ASCENDO_LIB_BASH}/json_emit.sh"` then call
  `json_init`, `json_add_item`, `json_finalize`
- **Windows:** `. "${env:ASCENDO_LIB_PS}/Json-Emit.ps1"` then call same logical
  functions

## Exit codes

- `0` — success (apply completed successfully or check found nothing to do)
- `1` — warning (apply partially succeeded, see sidecar diagnostics)
- `2` — bad usage (invalid args, missing config)
- `10-99` — adapter-specific errors (document in your manifest's
  `[adapter_specific_codes]` section)

## See also

- `docs/plugin-author-guide.md` — full author guide
- `docs/architecture/0007-plugin-manifest-v1.md` — manifest v1 spec
- `plugins/agent-clis/` — reference Tier 1 plugin (cross-OS)
- `plugins/dell-driver-update/` — reference Windows-only plugin
