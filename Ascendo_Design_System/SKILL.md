# Ascendo design system — agent skill

> One tool to keep a machine up-to-date across operating systems, package managers, and software sources. Linux + Windows MVP, macOS to follow. Open source, MIT, pre-release.

## When this skill applies

Use this design system whenever the user asks for:
- Anything Ascendo-branded (landing page, dashboard, desktop app, CLI output, docs, blog post, slide).
- Update orchestration / package-manager dashboards / cross-OS infrastructure tools — the patterns transfer cleanly to that adjacent space.
- A "calm, technical, terminal-adjacent" infra-tool aesthetic.

## How to use it

1. Link the tokens once: `<link rel="stylesheet" href="colors_and_type.css">` from the project root. All variables (`--bg`, `--fg`, `--accent`, `--font-sans`, `--space-*`, etc.) plus a base reset and Google Font imports come along for the ride.
2. Reference the surface kits in `ui_kits/` for working markup. Each kit has a `README.md` and an `index.html`:
   - `ui_kits/landing/` — marketing site (Astro recreation in plain HTML).
   - `ui_kits/webapp/` — dashboard SPA (sidebar + dense grid).
   - `ui_kits/desktop/` — Tauri shell (macOS chrome + run center).
   - `ui_kits/cli/` — terminal output (banner, phase headers, summary card).
3. Use the brand mark from `assets/logo-mark.svg` (proposed new mark — see `LOGO_PROPOSAL.md`) or `assets/original/icon.svg` (legacy chevron, only when matching existing site assets).
4. Iconography: pull from `assets/original/icons.js` first; fall back to Lucide at 1.8 stroke.

## Hard rules — do not break

- **Sentence case** for all UI strings. Never Title Case.
- **Second person** ("your machine", "you"). Never "we" / "our".
- **At most one lime element** in any visible region. Lime is the single primary action / active nav indicator. Everything else is ink + paper + status hue.
- **No emoji**. Use the icon set. The single `⚡` in the legacy codebase is being phased out.
- **No gradients** on primary surfaces or the brand mark. Allowed only as low-opacity grid/dot textures on hero sections.
- **Status = icon + text**, never icon alone.
- **No left-border accent stripes** on cards. (Common AI-design trope; do not introduce it.)
- **No invented stats / dummy numbers** in marketing copy. Real product capabilities only.
- Headlines describe behavior, not benefit. "Read-only sweep (~15s)" beats "Lightning-fast checks ⚡".

## Voice cheat sheet

| Don't | Do |
|---|---|
| "Lightning-fast updates ⚡" | "Read-only sweep (~15s)." |
| "We keep your system safe." | "Snapshot before apply. Roll back from the CLI." |
| "Effortless package management." | "Five-phase pipeline. JSON sidecar per phase." |
| "Get Started!" | "Install for Linux →" |

Cadence: short claim, em-dash, precise qualifier. Imperative second person. Code names appear inline so users can verify.

## Color quick reference

| Token | Hex | Use |
|---|---|---|
| `--ink-900` | `#0B1020` | Default dark surface, wordmark on light |
| `--paper-25` | `#F5F4EE` | Default light surface |
| `--lime-400` (`--accent`) | `#C8FF4B` | Single accent — primary actions, active state |
| `--moss-500` | `#3EBF7A` | Status: ok / pass |
| `--ochre-500` | `#E0A82E` | Status: warn / outdated |
| `--terracotta-500` | `#DC4B43` | Status: error / missing |
| `--dusty-blue-500` | `#4A7BC9` | Info / link / OS-neutral accent |

## Typography quick reference

| Family | Use |
|---|---|
| **Inter Tight** (`--font-sans`) | All UI, headings, body |
| **JetBrains Mono** (`--font-mono`) | CLI output, code, eyebrow labels, version pills |
| **Instrument Serif italic** (`--font-display`) | Landing-page italic accent words only |

Sizes follow a fluid clamp ramp; see `colors_and_type.css` for the full type scale.

## Output format

When producing Ascendo deliverables, default to:
- A single self-contained HTML file linking `colors_and_type.css`.
- Real Ascendo copy where possible — pull from `README.md` "Content fundamentals" examples.
- Real package names / commands (`apt`, `winget`, `brew`, `pipx`, `flatpak`, `snap`).
- Realistic version strings and category counts; do not invent telemetry-style stats.

## Files

```
README.md                ← visual + content fundamentals (start here)
SKILL.md                 ← this file
LOGO_PROPOSAL.md         ← rationale for the new mark
colors_and_type.css      ← design tokens
assets/                  ← logos, icons, originals
fonts/                   ← Google Fonts pointer
preview/                 ← design-system review cards
ui_kits/                 ← landing · webapp · desktop · cli
```
