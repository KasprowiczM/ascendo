"""i18n — 7-language support ported from macOS bash i18n/.

Languages: en, pl, es, it, pt, de, fr.

- loader.py — locale resolver + fallback chain (user lang → system locale → en)
- locales/<lang>.json — translation strings (UTF-8, structured by feature area)

Used by both CLI (Typer prints) and dashboard (FastAPI returns localized strings).

Translation key convention: dot-separated namespace (e.g. `cli.run.started`,
`dashboard.dialogs.confirm_apply`, `errors.elevation_denied`).

See `docs/i18n-author-guide.md` for translation guidelines.
"""
