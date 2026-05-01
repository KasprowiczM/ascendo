"""i18n error hierarchy.

All i18n exceptions descend from :class:`I18nError` so callers can write a
single ``except I18nError`` clause and catch every failure mode emitted by
:mod:`ascendo.i18n.loader`.

Two specialised types exist:

* :class:`UnsupportedLocaleError` — raised when an explicit locale request
  cannot be honoured (e.g. the file is missing on disk).
* :class:`MissingMessageError` — raised by strict lookup paths when a key
  is absent from every catalog in the fallback chain. The default
  ``Translator.t()`` does *not* raise this — it returns a placeholder and
  logs a warning. The class is exposed for consumers that want to enforce
  strict translation completeness in tests or in CI.
"""

from __future__ import annotations


class I18nError(RuntimeError):
    """Base class for all i18n-related failures."""


class UnsupportedLocaleError(I18nError):
    """Raised when a locale is requested that the loader cannot satisfy.

    This typically means the locale is not in
    :data:`ascendo.i18n.loader.SUPPORTED_LOCALES` *and* a JSON file for it
    does not exist on disk. Callers using :func:`detect_locale` will not
    see this — detection always falls back to ``en``. Direct
    :meth:`I18nLoader.load` calls *can* raise it.
    """


class MissingMessageError(KeyError, I18nError):
    """Raised by strict lookup paths for a key absent from every catalog.

    Subclasses :class:`KeyError` so existing ``dict[key]`` style code paths
    keep working, and :class:`I18nError` so the broader ``except`` clause
    catches it too.
    """
