# Logo proposal

The original Ascendo mark is a **green→blue gradient rounded square with a white chevron**. It works, but it lives in a crowded neighborhood (Tailwind defaults, generic SaaS). Six other open-source devtools ship something visually adjacent.

## Proposed direction

Three solid bars, ascending left-to-right, inside a rounded square. Reads as:

1. An implied **"A"** for Ascendo.
2. **Three operating systems** (Linux / Windows / macOS) climbing in sync — the literal product promise.
3. A **terminal/version graph** going up — "ascending versions, status improving".

Geometry is constructed on a strict grid so every bar is the same width with consistent gaps. No gradient — single-color flat means it stays crisp at favicon size and inverts cleanly for dark mode, monochrome print, and embroidery.

| File | Use |
|---|---|
| `assets/logo-mark.svg` | Default — lime bars on midnight tile |
| `assets/logo-mark-light.svg` | Inverted — midnight bars on paper tile |
| `assets/logo-mark-mono.svg` | Single-color (`fill="currentColor"`) — for CLI banners, embroidery, single-color print |
| `assets/logo-wordmark.svg` | Horizontal lockup with "Ascendo" set in Inter Tight 700 |
| `assets/logo-wordmark-dark.svg` | Wordmark on lime tile (rare, for promo only) |

## Why drop the gradient?

- Gradients of this exact shape (emerald→sky / green→cyan) are now visual default for *Vercel-era SaaS*. Ascendo is a system tool — it should look like Tailscale or fly.io, not a Tailwind landing page.
- The original chevron alone reads as "up arrow" generically. Three bars = "many things going up together", which is much closer to the actual product behavior.
- A flat geometric mark is cheaper to render in a CLI banner (we can ASCII-fy it cleanly).

## ASCII version (for `banner.txt`)

```
 ▖ ▌ █
 ▌ █ █
 █ █ █   ascendo · unified updates
```

The original ASCII banner ("___  ____ ____ ___ ___" lettering) is preserved at `assets/original/banner.txt` and remains valid as the long-form CLI splash; the three-bar version is a compact alternative for tight terminals.

## What stays the same

- The square tile with rounded corners (14px radius on a 64px viewBox) is preserved — it's the silhouette installers and dock icons learn first.
- The "ascending" idea is preserved — just rendered as bars instead of a chevron.
- The favicon, .desktop icon, app shortcut all use the same source SVG.
