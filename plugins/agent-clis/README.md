# plugins/agent-clis/

**Tier 1, cross-OS plugin** — manages AI agent command-line tools across
Linux, Windows, and macOS.

Currently bundled tools:

| Tool | npm package | Native installer (preferred where available) |
|---|---|---|
| Claude Code | `@anthropic-ai/claude-code` | `~/.local/bin/claude.exe` (Anthropic native) |
| OpenAI Codex | `@openai/codex` | npm only |
| Google Gemini | `@google/gemini-cli` | npm only |
| Qwen Code | `@qwen-code/qwen-code` | npm only |
| OpenCode | `opencode-ai` | npm only |

## Architecture

Tool list is **declarative** in `tools.toml`. To add a new tool, append a
TOML entry — no script changes needed.

```toml
[[tool]]
id              = "claude-code"
npm_package     = "@anthropic-ai/claude-code"
binary_name     = "claude"
update_strategy = "native-first-fallback-npm"
process_names   = ["Claude"]

[[tool]]
id              = "codex"
npm_package     = "@openai/codex"
binary_name     = "codex"
update_strategy = "npm"
process_names   = []

# ... etc
```

## Native installer detection (Windows + macOS)

Some agent CLIs ship native installers that are preferred over npm:

- **Claude Code:** `~/.local/bin/claude.exe` (Windows), `/usr/local/bin/claude` (mac/Linux)
- **Cursor / Aider / Continue.dev** (future): TBD per upstream

The plugin detects native installations via `NativeInstallPaths` whitelist
and emits a SUCCESS phase result with note "uses native installer" — npm
shim becomes a shadowed fallback.

## Source

- Windows: `$NPM_CLI_TOOLS` from `D:\Dev_Env\Aktualizacje-W11-Dell5520\3_Update-Programs.ps1`
- macOS: `update_npm_cli.sh` from `D:\Dev_Env\Aktualizacje_MAC`
- Linux: `npm-globals.list` from `D:\Dev_Env\Ubuntu_Aktualizacje\config\`

Refactored into a single declarative TOML + per-OS phase scripts in M3.
