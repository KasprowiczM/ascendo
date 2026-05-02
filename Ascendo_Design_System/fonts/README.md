# Fonts

Ascendo's design system uses three Google Fonts, loaded via the `@import` at the top of `colors_and_type.css`. No font files are bundled.

| Family | Role | Fallback |
|---|---|---|
| **Inter Tight** | UI, headings, body | `Inter, system-ui, -apple-system, sans-serif` |
| **JetBrains Mono** | Code, CLI, terminal mockups, eyebrow micro-labels | `ui-monospace, "SF Mono", Menlo, Consolas, monospace` |
| **Instrument Serif** (italic) | Landing-page display moments only | `"Iowan Old Style", Georgia, serif` |

## To self-host instead

1. Download woff2s from [fonts.google.com](https://fonts.google.com) for the families/weights below.
2. Drop into this folder.
3. Replace the `@import` at the top of `colors_and_type.css` with `@font-face` blocks pointing to `./fonts/...`.

**Weights needed:**
- Inter Tight: 400, 500, 600, 700, 800
- JetBrains Mono: 400, 500, 600
- Instrument Serif: 400 (regular + italic)

## Substitution flag (read me)

The original Ascendo codebase did not ship font files. It relied on `font-family: Inter, "SF Pro", Roboto, sans-serif` and the OS to resolve. This redesign **commits to Inter Tight + JetBrains Mono + Instrument Serif as a deliberate choice** — they're free, web-safe via Google Fonts, and tighter than the original generic stack.

If the user prefers a different family (e.g. self-hosted `Inter` proper, or a paid display face), swap inside `colors_and_type.css` only — no other file references the family directly. Components reference `var(--font-sans)` / `var(--font-mono)` / `var(--font-display)`.
