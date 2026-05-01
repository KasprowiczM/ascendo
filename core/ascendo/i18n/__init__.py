"""i18n — 7-language support ported from macOS bash i18n/.

Languages: en, pl, es, it, pt, de, fr.

- ``loader`` — locale resolver + fallback chain (override -> env ->
  POSIX ``LC_*`` -> Windows ``GetUserDefaultLocaleName`` -> ``en``).
- ``errors`` — exception hierarchy rooted at :class:`I18nError`.
- ``locales/<lang>.json`` — flat ``{key: template}`` catalogs (UTF-8).
  JSON is the on-disk format because it's parseable by non-Python tools
  (CI lint, the Tauri/Rust desktop binding) — see ``loader.py`` for the
  rationale.

Used by both CLI (Typer prints) and dashboard (FastAPI returns localized
strings).

Translation key convention: dot-separated namespace (``cli.run.started``,
``dashboard.dialogs.confirm_apply``, ``msg.error.elevation_denied``).

Quick start::

    from ascendo.i18n import detect_locale, get_translator

    t = get_translator(detect_locale())
    print(t.t("msg.run.starting"))
    print(t.t("msg.package.upgrading", name="firefox"))

See ``docs/i18n-author-guide.md`` for translation guidelines.
"""

from .errors import I18nError, MissingMessageError, UnsupportedLocaleError
from .loader import (
    DEFAULT_LOCALE,
    SUPPORTED_LOCALES,
    I18nLoader,
    Translator,
    detect_locale,
    get_translator,
    set_default_locale,
)

__all__ = [
    "DEFAULT_LOCALE",
    "I18nError",
    "I18nLoader",
    "MissingMessageError",
    "SUPPORTED_LOCALES",
    "Translator",
    "UnsupportedLocaleError",
    "detect_locale",
    "get_translator",
    "set_default_locale",
]
