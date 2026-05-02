# Ascendo Design System — Handoff

> Drop-in package for the Ascendo monorepo. One CSS file, five SVGs, and a migration map.

---

## What's in this folder

```
colors_and_type.css     ← THE design tokens. Single source of truth.
HANDOFF.md              ← you are here — copy-into-repo guide
index.html              ← live preview of every element (light + dark)
README.md               ← brand voice, content rules, visual foundations
LOGO_PROPOSAL.md        ← rationale for the new mark
SKILL.md                ← agent skill manifest

assets/
  logo-mark.svg              ← primary mark (ink + lime, 64×64)
  logo-mark-light.svg        ← mark on paper surfaces
  logo-mark-mono.svg         ← single-color (currentColor)
  logo-wordmark.svg          ← horizontal lockup (light)
  logo-wordmark-dark.svg     ← horizontal lockup (dark)
  original/                  ← imported source files (do not ship)

ui_kits/
  landing/  webapp/  desktop/  cli/   ← reference recreations per surface
```

---

## 1. Drop into the repo

Copy these files into the Ascendo monorepo:

| Source (this project)            | Destination (Ascendo repo)                      |
|----------------------------------|-------------------------------------------------|
| `colors_and_type.css`            | `app/frontend/styles/tokens.css`                |
| `assets/logo-mark.svg`           | `branding/icon.svg` (replaces existing)         |
| `assets/logo-mark-light.svg`     | `branding/icon-light.svg`                       |
| `assets/logo-mark-mono.svg`      | `branding/icon-mono.svg`                        |
| `assets/logo-wordmark.svg`       | `branding/logo.svg` (replaces existing)         |
| `assets/logo-wordmark-dark.svg`  | `branding/logo-dark.svg`                        |

In `app/frontend/index.html`, add **once** at the top of `<head>`:

```html
<link rel="stylesheet" href="styles/tokens.css">
```

Then keep existing `style.css` loading **after** it — your component CSS overrides nothing important; it consumes the tokens.

---

## 2. Theme switch

Light is the default. Flip to dark by setting `data-theme="dark"` on `<html>`. The existing theme toggle (sun / moon icon in the topbar) should already write to `localStorage`; just point it at the `<html>` attribute:

```js
document.documentElement.setAttribute('data-theme', 'dark');  // dark
document.documentElement.removeAttribute('data-theme');       // light
```

`prefers-color-scheme: dark` is honored automatically when no explicit attribute is set.

---

## 3. Migration map — `app/frontend/style.css`

Replace raw values with semantic tokens. The renames below are mechanical:

| Old (Tailwind / hardcoded) | New (semantic token)        | Where it shows up |
|----------------------------|-----------------------------|-------------------|
| `#22c55e` (green)          | `var(--accent)` (lime)      | primary buttons, active nav |
| `#0ea5e9` (blue)           | `var(--info)`               | dry-run, info banners |
| `#f59e0b` (amber)          | `var(--warn)`               | restart-pending, held |
| `#ef4444` (red)            | `var(--err)`                | failed phase, stop |
| `#fff` / `#fafafa`         | `var(--bg)` / `var(--bg-elev)` | page bg / cards |
| `#000` / `#111827`         | `var(--fg)` / `var(--bg-inverse)` | text / inverse surfaces |
| `#6b7280` (slate)          | `var(--fg-muted)`           | meta, helper text |
| `#e5e7eb` (border)         | `var(--border)`             | 1px alpha borders |
| `Inter, system-ui`         | `var(--font-sans)`          | all UI |
| `'SF Mono', monospace`     | `var(--font-mono)`          | terminal, eyebrows, kbd |

### Component classes to keep using

The token CSS exposes ready-made classes you can use directly:

- `.t-eyebrow` — uppercase mono micro-label
- `.t-h1` … `.t-h4` — headlines
- `.t-lead`, `.t-body`, `.t-body-sm`, `.t-caption`
- `.t-mono`, `.t-code-block`, `.t-kbd`
- `.t-display` — italic Instrument Serif (landing only)

### Buttons (recipe, not class — keep your own classnames)

```css
.btn-primary { background: var(--accent); color: var(--accent-ink); border: 0; }
.btn-primary:hover { background: var(--lime-300); }

.btn-secondary { background: var(--bg-inverse); color: var(--fg-inverse); border: 0; }

.btn-ghost { background: transparent; color: var(--fg);
             border: 1px solid var(--border-strong); }
.btn-ghost:hover { background: rgba(11,16,32,0.04); }

.btn-danger { background: var(--err); color: #fff; border: 0; }
```

### Status pills

```css
.pill-ok   { background: var(--ok-bg);   color: var(--ok); }
.pill-warn { background: var(--warn-bg); color: var(--warn); }
.pill-err  { background: var(--err-bg);  color: var(--err); }
.pill-info { background: var(--info-bg); color: var(--info); }
```

---

## 4. Iconography

Icons stay where they are — `app/frontend/icons.js` is already Lucide-style at 24×24 / 1.8 stroke / `currentColor`. **No changes needed.** Any new glyphs should pull from [lucide.dev](https://lucide.dev) at the same spec.

The single legacy `⚡` emoji on the NVIDIA button should be replaced with the Lucide `zap` glyph from the existing sprite.

---

## 5. Things to delete

- `app/frontend/style.css` rules that hardcode Tailwind hex values for the old green/blue gradient brand mark — the new mark is solid ink+lime.
- The radial gradient on the legacy logo background (now a flat 18px-radius square).
- Any `border-left: 4px solid <color>` "accent stripe" patterns on cards — explicitly avoided in the new system.

---

## 6. Open decisions for the team

- **Display serif** — Instrument Serif is current. `index.html` shows three alternatives (Fraunces, Cormorant Garamond, Newsreader). Pick one, then update the `@import url(...)` line and `--font-display` in `colors_and_type.css`.
- **Sans family** — Inter Tight is current. Alternatives previewed: Geist, Space Grotesk, DM Sans.
- **Mono family** — JetBrains Mono is current. Alternatives previewed: IBM Plex Mono, Geist Mono, Fira Code.
- **Self-hosted fonts** — currently loaded from Google Fonts CDN. If the team wants offline / CSP-strict, drop woff2 files into `branding/fonts/` and replace the `@import` with `@font-face` blocks at the top of `colors_and_type.css`.

---

## 7. Verification checklist

After the swap, this should still be true:

- [ ] Sidebar active item has lime background tint, not green
- [ ] Primary buttons are lime, not Tailwind green
- [ ] Page background is cool grey `#EDEFF4`, not warm cream and not pure white
- [ ] Code blocks / terminal output stay dark in both themes (`--code-bg` is theme-invariant)
- [ ] Status banners use the muted palette (moss / ochre / terracotta / dusty blue), not Tailwind defaults
- [ ] Theme toggle flips `data-theme="dark"` on `<html>` and persists in `localStorage`
- [ ] Logo mark in topbar uses the new 3-bar mark, not the legacy gradient chevron

---

Ship it.
