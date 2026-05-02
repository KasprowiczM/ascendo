# Ascendo Design System

> Brand + UI system for **Ascendo — Unified Updates**, the cross-platform update orchestrator that brings Linux, Windows, and macOS package managers, app stores, and AI CLIs under one CLI, dashboard, and desktop app.

---

## What is Ascendo?

Ascendo is a single tool that keeps a machine up-to-date across operating systems and software sources — APT, winget, brew, snap, flatpak, npm, pip/pipx, app stores, drivers, AI agent CLIs, and more — through one CLI (`ascendo run`), one local web dashboard (`http://127.0.0.1:8765`), and a Tauri-shelled desktop app. Open source, MIT, currently pre-release, with a Linux+Windows MVP first and macOS following.

Surfaces this design system covers:

| Surface | Form | Primary use |
|---|---|---|
| **Landing site** | Astro static site | Marketing, install instructions, plugin catalog |
| **Web Dashboard** | Vanilla-JS SPA at `127.0.0.1:8765` | Day-to-day update management |
| **Desktop App** | Same SPA inside a Tauri Rust shell | Native-feeling install per OS |
| **CLI** | Bash / Python click commands | Power users, scheduler, scripts |

## Sources used

This system was built from a read-only mount of the Ascendo monorepo:

- `ascendo/branding/` — original `icon.svg`, `logo.svg`, ASCII `banner.txt`, palette README
- `ascendo/app/frontend/` — actual SPA (`index.html`, `style.css`, `icons.js`, `app.js`, `i18n.js`)
- `ascendo/README.md`, `ascendo/website/README.md`, `ascendo/docs/` — product copy, architecture, feature list
- `ascendo/app/frontend/icons.js` — Lucide-style icon set (24×24, 1.8 stroke, currentColor) used in the SPA
- `ascendo/branding/README.md` — original palette (`#22c55e` / `#0ea5e9` / `#f59e0b` / `#ef4444`) and naming rationale ("ascendō — I ascend")

The original system used a green→blue Tailwind gradient and Inter/system stacks. **This redesign retains the core ideas (ascending motif, terminal-adjacent feel, three-OS neutrality) and proposes a more distinctive direction** — see `LOGO_PROPOSAL.md` and visual foundations below.

## Index

```
.
├── README.md                  ← you are here
├── SKILL.md                   ← agent skill manifest
├── colors_and_type.css        ← design tokens (colors, type, spacing, motion)
├── LOGO_PROPOSAL.md           ← rationale for the new mark
├── assets/
│   ├── logo-mark.svg          ← new mark on midnight
│   ├── logo-mark-light.svg    ← new mark on paper
│   ├── logo-mark-mono.svg     ← single-color (currentColor)
│   ├── logo-wordmark.svg      ← horizontal lockup
│   ├── logo-wordmark-dark.svg ← inverted lockup
│   └── original/              ← imported source assets
├── preview/                   ← design-system review cards
├── fonts/                     ← README pointing to Google Fonts
└── ui_kits/
    ├── landing/               ← marketing site recreation
    ├── webapp/                ← dashboard recreation
    ├── desktop/               ← desktop-app frame
    └── cli/                   ← CLI / terminal kit
```

---

## Content fundamentals

Ascendo's voice is **calm, technical, and direct** — built for power users who already know what `apt`, `winget`, and `brew` do. Copy reads like good `--help` text written by an engineer who respects your time.

**Tone & voice**
- **Direct, terse, factual.** No marketing puff. Headlines describe behavior, not benefit ("read-only sweep (~15s)" not "lightning-fast checks ⚡").
- **Second-person + imperative.** "Pick a profile." "Authenticate sudo." Avoids "we" / "our".
- **Acknowledges what's risky.** Banners say "Restart required" plainly. The dashboard literally calls a profile `safe` and another `full` and explains the difference in one line.
- **Technical confidence, not jargon-flexing.** Real command names appear inline (`apt full-upgrade`, `softwareupdate -ia -R`) so users can verify; nothing hides behind brand euphemisms.
- **Bilingual (en/pl).** All UI strings flow through `i18n.js`. Copywriting must work translated — keep clauses short.

**Casing**
- **Sentence case** for everything UI: titles, buttons, menu items ("Run center", "Start run", "Quick check"). No Title Case Marketing Speak.
- **lowercase** for the tagline and brand subtitles ("unified updates · clean state").
- **Code-style casing** preserved literally: `apt`, `winget`, `pipx`, `--profile=safe`.

**Pronouns & address**
- "**You**" — always. "Your machine", "your config", "your machines".
- Never "we" or "our" in product copy. The product *is* a tool; it does things, not relationships.

**Emoji**
- **Almost never.** The only emoji-adjacent symbols in the original are `⚡` on a single NVIDIA button and `✓ × ⚠` for status. Treat emoji as a code smell — use the Lucide icon set instead.
- Status uses **icons + text**, never icons alone.

**Examples (from the actual product)**

> *"One tool to keep your machine up-to-date — across operating systems, package managers, and software sources."*

> *"quick — read-only check (~15s)" / "safe — full pipeline, no driver changes" / "full — everything incl. NVIDIA/firmware"*

> *"Required for apt / snap / drivers apply phases. Password is sent to 127.0.0.1 only and used to warm the OS sudo timestamp."*

> *"Heuristic engine runs free, deterministic rules over inventory + run history. Optional LLM step *enriches* rationales — never edits files autonomously."*

Note the rhythm: a short claim, an em-dash, a precise qualifier. That's the house cadence.

---

## Visual foundations

### Color
- **Midnight (`#0B1020`)** is the brand anchor — used as the default app surface in dark mode and as the wordmark color in light mode. Slight blue cast keeps it from feeling oily.
- **Paper (`#F5F4EE`)** replaces stark white. Warm off-white gives the system identity and pairs better with monospace.
- **Lime (`#C8FF4B`)** is the single accent. It carries every primary action, the active nav indicator, and the "ascending" idea (terminal cursor energy). Used sparingly — at most one lime element per visible region.
- **Status colors are muted, professional**: moss `#3EBF7A`, ochre `#E0A82E`, terracotta `#DC4B43`, dusty blue `#4A7BC9`. None of them are saturated Tailwind defaults — this product handles infrastructure, not party invites.
- Imagery is **cool and grayscale** by default. No warm-cinema gradients. When color is used in screenshots/illustrations, it flows from the four named status hues.

### Typography
- **Inter Tight** for all UI and headings (tight letter-spacing, geometric, neutral).
- **JetBrains Mono** for code, CLI output, terminal mockups, and *micro labels* (eyebrows, badges, tags) — extends the terminal vocabulary into the UI.
- **Instrument Serif (italic)** for landing-page display moments only. Adds a single drop of editorial warmth to balance the otherwise-mechanical type system.
- All three are free Google Fonts; no font files shipped — see `fonts/README.md`.

### Spacing & layout
- 4px ramp (`--space-1`..`--space-10`).
- Grid-based, generous on the landing page (max 1200px content width); dense and information-rich in the app (240px sidebar + fluid main).
- **Fixed elements**: sidebar, footer status line, sticky reboot banner. Topbar is mobile-only; its utilities (lang/theme/font) float top-right on desktop.

### Backgrounds & motifs
- **Solid surfaces over gradients.** The original used a green→blue gradient mark; the proposed mark drops it.
- **Dotted/grid backgrounds** are allowed for hero / empty states only, at low opacity (`rgba(0,0,0,0.04)` lines on paper, `rgba(255,255,255,0.06)` on midnight).
- **Mono overlays** — small monospace meta lines (`v0.1.0 · MIT · linux/win/mac`) appear under headings as a brand signature.
- No hand-drawn illustrations, no AI-generated stock imagery, no full-bleed photography.

### Borders & corners
- **Radii are crisp**: 6px (controls), 8px (cards), 12px (large containers), 18px (logo mark, modal). Never pill, except for small status badges.
- **Borders are 1px**, defined as alpha (`rgba(11,16,32,0.10)`) so they feather correctly across themes.
- A card is `1px border + soft shadow`, not a heavy drop shadow alone.

### Shadows & elevation
Four-step elevation:
- `--shadow-sm` — flush controls, hover-lift on rows.
- `--shadow-md` — cards floating above bg.
- `--shadow-lg` — popovers, dropdowns.
- `--shadow-xl` — modals only.
Inner shadow (`--shadow-inner`) sits inside inputs/code blocks for depth without a heavy border.

### Animation
- **Quick + linear-leaning.** `--dur-1: 90ms` for hover color, `--dur-2: 160ms` for transforms, `--dur-3: 240ms` for layout transitions.
- Easing: `cubic-bezier(0.22, 1, 0.36, 1)` (`--ease-out`) is the default. No bouncy springs except on first-run wizard CTAs.
- **No fades on critical state changes.** A finished run swaps state immediately so it doesn't feel like the UI is buffering.
- Loading states use a 0.6s linear spin + a subtle horizontal scanline on the progress bar.

### Hover & press
- **Hover**: bg shifts to `--bg-sunk` (~6% darker on light, ~6% lighter on dark). No translateY tricks, no glow.
- **Press**: 1px translateY + brightness 0.96. Micro-feedback only — this is a tool, not a toy.
- **Focus**: 2px solid ring in `--border-focus` (ink in light, lime in dark). 1px offset.

### Transparency & blur
- Sidebar + topbar are opaque (the app is information-dense; readability beats aesthetics).
- Modals use `rgba(0,0,0,0.55)` scrim, no backdrop-blur (Tauri/WebKitGTK perf).
- Lime accent is occasionally rendered at 14% opacity for active-nav backgrounds and status pill backgrounds.

### Cards
A card is: `1px solid var(--border)` + `var(--bg-elev)` + `8px radius` + `var(--shadow-sm)` on hover. The card title is uppercase mono in `--fg-muted` (eyebrow style). Cards never lean on left-border accent stripes (we explicitly avoid that AI-design trope).

---

## Iconography

- **Source set: `app/frontend/icons.js`** — a Lucide-style sprite hand-rolled in the codebase. 24×24 viewBox, 1.8 stroke, `currentColor` everywhere. We import this set directly into the design system at `assets/original/icons.js` and reuse it. Coverage includes: overview, categories, run, history, logs, sync, apps, suggest, hosts, settings, help, about, menu, close, sun, moon, globe, type, nvidia, check, alert, plus, trash, cloud, edit, monitor, folder, chevron_down.
- **For new icons not in the set**: use [Lucide](https://lucide.dev) at the same weight/style — the codebase set is a Lucide-compatible subset, so additions slot in seamlessly. Lucide is loaded from CDN in mocks: `https://unpkg.com/lucide-static@latest/icons/<name>.svg`.
- **Brand mark**: see `assets/logo-mark.svg` (proposed new mark — three ascending bars implying an "A") and `assets/original/icon.svg` (legacy gradient chevron).
- **Emoji**: not used. The single emoji-adjacent character in the original codebase (`⚡` on the NVIDIA button) is replaced by the Lucide `nvidia` glyph in the kit.
- **Unicode glyphs** used as UI affordances: `↑ ↓ ↻ ×` only, in the same monospace stack as the rest. No `▶ ◀ ★`-style decoration.
- **PNGs**: none — every icon is SVG so it renders crisply across the dashboard, desktop shell, and CLI's HTML helpers.

⚠️ **Substitution flagged**: the original ships no font files; the codebase relied on system stacks (`Inter, "SF Pro", Roboto`). This system commits to **Inter Tight + JetBrains Mono + Instrument Serif** loaded from Google Fonts. If you'd prefer self-hosted woff2s, drop them into `fonts/` and update `colors_and_type.css`.

---

## Quick start for designers

```html
<link rel="stylesheet" href="../colors_and_type.css">
<header>
  <img src="../assets/logo-wordmark.svg" alt="Ascendo" height="28">
</header>
<h1 class="t-h1">One tool. Three OSes. Every update.</h1>
<p class="t-lead">The unified update orchestrator for Linux, Windows, and macOS.</p>
<button style="background:var(--accent); color:var(--accent-ink); border:0;
               padding:.7rem 1.1rem; border-radius:var(--radius-md);
               font:var(--fw-semibold) var(--fs-base) var(--font-sans);">
  Install Ascendo
</button>
```

For full component recipes, see each `ui_kits/<surface>/README.md`.
