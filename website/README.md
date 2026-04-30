# website/

Landing page for Ascendo, deployed to GitHub Pages.

## Stack

- **Astro** — static site generator (fast builds, minimal JS by default)
- **Plain HTML/CSS** for content pages, **Astro components** for shared layout
- Deployed via `.github/workflows/deploy-website.yml` to GitHub Pages

## Pages

- `/` — hero + auto-detect OS + download CTA per OS + features
- `/install` — detailed install guide per OS (3 tabs)
- `/docs` — link to renderable `docs/` (Starlight or mdBook integration TBD)
- `/plugins` — auto-generated catalog from `plugins/*/manifest.toml`
- `/changelog` — rendered from root `CHANGELOG.md`
- `/about` — credits, license, links

## Auto-detect OS

JS detects user OS via `navigator.userAgent` + `navigator.platform`, highlights
the appropriate download button:

- Windows → `winget install Ascendo.Ascendo`
- macOS → `brew install KasprowiczM/tap/ascendo`
- Linux → `.deb` download

## Latest version

JS pulls latest release tag from GitHub API (`https://api.github.com/repos/KasprowiczM/ascendo/releases/latest`)
and inserts into download buttons + page header.

## Deploy

Triggered by push to `main` that touches `website/**`. See
`.github/workflows/deploy-website.yml`.

## URL

- Current: `https://kasprowiczm.github.io/ascendo` (GitHub Pages)
- Future: custom domain after v0.2.0 (TBD — see HANDOFF.md "Open decisions")
