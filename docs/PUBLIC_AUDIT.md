# Public-repo audit

Audit of every top-level path in the Ascendo working tree, marking
each as **PUBLIC** (stays in the public GitHub repo) or **PRIVATE**
(moves to `dev-sync-overlay/` and is gitignored).

> Last updated: 2026-05-09 (Sesja 51 — pre-public-flip audit).

## What's public

All source code, all user-facing docs, all configs needed to build
and run Ascendo from a fresh clone:

- **Source trees**: `core/`, `adapters/`, `app/`, `ui/`, `lib/`,
  `plugins/`, `bin/` (minus dev-sync helpers — see below),
  `schemas/`, `i18n/`, `share/`, `systemd/`, `packaging/`, `config/`,
  `contrib/`, `tests/`, `scripts/`, `website/`, `branding/`,
  `Ascendo_Design_System/`, `docs/` (minus
  `docs/superpowers/specs/`).
- **Project configs**: `pyproject.toml`, `.gitignore`,
  `.gitattributes`, `.markdownlint.json`, `.pre-commit-config.yaml`,
  `.github/`.
- **User-facing docs**: `README.md`, `LICENSE`, `CHANGELOG.md`,
  `RELEASE_NOTES.md`, `CONTRIBUTING.md`, `SECURITY.md`,
  `USER_GUIDE.md`, `MACOS_QUICKSTART.md`, `MACOS_TESTING.md`,
  `WINDOWS_QUICKSTART.md`, `WINDOWS_TESTING.md`, `MIGRATION.md`,
  `RUN.md`, `APPS.md.example`.
- **Installer entrypoints**: `install.sh`, `install.ps1`,
  `update.sh`, `update.ps1`, `update-all.sh`, `setup.sh`.
- **Public stub placeholders**: `CLAUDE.md.example`,
  `AGENTS.md.example`, `HANDOFF.md.example` (point public
  contributors at the right alternatives).

## What's private (moves to `dev-sync-overlay/`)

| Path                              | Reason                              | Overlay target                            |
|-----------------------------------|-------------------------------------|-------------------------------------------|
| `CLAUDE.md`                       | AI instructions for Claude          | `dev-sync-overlay/ai-instructions/`       |
| `AGENTS.md`                       | AI instructions for general agents  | `dev-sync-overlay/ai-instructions/`       |
| `CODEX.md`                        | AI instructions for Codex           | `dev-sync-overlay/ai-instructions/`       |
| `.claude/`                        | Claude Code session state           | `dev-sync-overlay/ai-state/`              |
| `.claudeignore`                   | Claude ignore rules                 | `dev-sync-overlay/ai-state/`              |
| `.codex`, `.codex.local/`         | Codex marker + local state          | `dev-sync-overlay/ai-state/`              |
| `.codexignore`                    | Codex ignore                        | `dev-sync-overlay/ai-state/`              |
| `.gemini/`                        | Gemini agent state                  | `dev-sync-overlay/ai-state/`              |
| `.geminiignore`                   | Gemini ignore                       | `dev-sync-overlay/ai-state/`              |
| `.graphifyignore`                 | Graphify ignore                     | `dev-sync-overlay/ai-state/`              |
| `graphify-out/`                   | Knowledge graph artefacts           | `dev-sync-overlay/graphify/`              |
| `HANDOFF.md` (313 KB)             | Internal per-session log            | `dev-sync-overlay/handoff/`               |
| `PLAN.md` (44 KB)                 | Internal forward roadmap            | `dev-sync-overlay/handoff/`               |
| `DEV_SCRIPTS_README.md`           | Dev-sync internal docs              | `dev-sync-overlay/handoff/`               |
| `docs/superpowers/specs/`         | Per-session design docs             | `dev-sync-overlay/handoff/specs/`         |
| `dev-sync/`                       | Rclone configs + Python helpers     | (stays in tree, gitignored)               |
| `dev-sync-*.sh`, `dev-sync-*.ps1` | 15 wrapper scripts                  | (stay in tree, gitignored)                |
| `dev_sync_logs/`                  | Already gitignored                  | (no change)                               |
| `dist/`                           | Build artefacts                     | (gitignored)                              |

`.env`, `.env.local`, `__pycache__/`, `node_modules/`, `target/`,
`*.lock`, etc. are already covered by the existing `.gitignore`.

## How the split works

The dev-sync overlay system (Proton Drive via rclone) gives
maintainers a 1:1 working tree on every dev machine without
exposing anything in the public repo:

1. `bin/dev-sync-overlay-migrate.sh` **copies** (does not move) every
   private path into `dev-sync-overlay/`. Idempotent — re-run safely.
2. `dev-sync-export.sh` pushes the overlay (plus `dev-sync/`,
   `dev-sync-*.sh`, etc.) to the private Proton bucket.
3. After verification, the originals get `git rm`'d from the public
   repo. The dev-sync overlay keeps the canonical copies.
4. New dev machines run `install.sh` (basic edition; gets only
   public files) or pull from Proton via `dev-sync-import.sh` to
   restore the dev edition.

## Maintenance going forward

When adding new files to the working tree, decide upfront:

- Is it source code, a build artefact, or user-facing docs?
  → public, commit normally.
- Is it AI-agent state, a session log, an internal spec, or a
  dev-sync runtime artefact? → private, put it under
  `dev-sync-overlay/` (or in a path that's already gitignored).

When in doubt, `git status` will surface anything that isn't
covered. Re-audit by re-reading `.gitignore` plus this file.

## Pre-public-flip checklist

Run this end-to-end before flipping the GitHub repo to public:

- [ ] Run `bin/dev-sync-overlay-migrate.sh` (populates the overlay).
- [ ] Run `bash dev-sync-export.sh` (push overlay to Proton).
- [ ] Run `bash dev-sync-verify-full.sh` (confirm overlay reached
      Proton with the expected file set).
- [ ] Manually `git rm` the private originals. The complete list:
  - `CLAUDE.md`, `AGENTS.md`, `CODEX.md`
  - `.claudeignore`, `.codex`, `.codex.local/`, `.codexignore`,
    `.gemini/`, `.geminiignore`, `.graphifyignore`
  - `HANDOFF.md`, `PLAN.md`, `DEV_SCRIPTS_README.md`
  - `docs/superpowers/specs/` (whole directory)
  - `graphify-out/` (whole directory)
  - `dev-sync/` (whole directory)
  - All `dev-sync-*.sh` and `dev-sync-*.ps1` (15 files)
- [ ] `git ls-files | grep -Ei 'claude|codex|gemini|handoff|plan\.md|graphify|dev-sync'`
      should return only the `.example` stubs.
- [ ] `git status` shows clean.
- [ ] Tag the release: `git tag -a v0.6.0 -m "v0.6.0 — public release"`.
- [ ] `git push origin main --tags`.
- [ ] On GitHub: Settings → Danger Zone → Make repo public.
- [ ] Test by cloning the public repo into a fresh directory and
      running `install.sh` end-to-end.
