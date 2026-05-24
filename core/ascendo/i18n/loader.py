"""Locale detection, catalog loading, and string translation.

This module is the Python port of the legacy macOS bash i18n system
(``Ascendo/i18n/loader.sh`` + ``lang_<code>.sh``). It supersedes
the Linux-only ``ascendo/i18n/*.txt`` flat-file format with structured
JSON catalogs that are also parseable by non-Python tooling (CI lint,
the future Tauri/Rust binding for the desktop app, etc.).

Architecture:

* :func:`detect_locale` — pure function. Resolves the user's preferred
  locale through a five-step chain and *always* returns a code from
  :data:`SUPPORTED_LOCALES`.
* :class:`I18nLoader` — owns the catalogs directory and a per-locale
  in-memory cache.
* :class:`Translator` — immutable view over a single locale's catalog
  with formatting + fallback semantics.
* :func:`get_translator` / :func:`set_default_locale` — module-level
  conveniences backed by a singleton :class:`I18nLoader` (see the
  ``_state`` block at the bottom of this file).

The module has **no third-party dependencies**. Windows locale detection
uses :mod:`ctypes`; on POSIX (or any host where ``ctypes`` import fails)
we fall back to environment variables. mypy ``--strict`` clean.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
from pathlib import Path
from typing import Final

from .errors import MissingMessageError, UnsupportedLocaleError

# ─────────────────────────────────────────────────────────────────────────────
# Module-level constants
# ─────────────────────────────────────────────────────────────────────────────

#: The seven locales we ship catalogs for. Order is the canonical display
#: order in language pickers (English first, then sorted by approximate
#: speaker count among target users).
SUPPORTED_LOCALES: Final[tuple[str, ...]] = (
    "en",
    "pl",
    "es",
    "it",
    "pt",
    "de",
    "fr",
)

#: Fallback locale used when:
#:
#: - detection fails entirely;
#: - the requested locale is not in :data:`SUPPORTED_LOCALES`;
#: - a key is missing in the requested locale (try ``en`` next).
DEFAULT_LOCALE: Final[str] = "en"

#: Environment variable consulted before any system locale lookup. Lets
#: the user (and tests) pin a locale without touching shell ``LC_*``
#: settings. Mirrors the legacy bash ``MAC_LANG`` mechanism.
ENV_VAR: Final[str] = "ASCENDO_LOCALE"

_logger: Final[logging.Logger] = logging.getLogger("ascendo.i18n")


# ─────────────────────────────────────────────────────────────────────────────
# Locale detection
# ─────────────────────────────────────────────────────────────────────────────


def _normalize(raw: str | None) -> str | None:
    """Reduce a BCP-47 / POSIX locale string to its bare language code.

    Examples:
        ``'pl_PL.UTF-8'`` → ``'pl'``
        ``'pl-PL'``       → ``'pl'``
        ``'C'`` / ``'POSIX'`` / ``''`` → ``None`` (caller will fall through)
    """
    if not raw:
        return None
    head = raw.strip()
    # Drop everything after the first '.', '@', or '_'/'-' boundary.
    for sep in (".", "@"):
        if sep in head:
            head = head.split(sep, 1)[0]
    for sep in ("_", "-"):
        if sep in head:
            head = head.split(sep, 1)[0]
    head = head.lower()
    if head in ("", "c", "posix"):
        return None
    return head


def _detect_from_env() -> str | None:
    """POSIX-style detection from ``LC_ALL`` / ``LC_MESSAGES`` / ``LANG``."""
    for var in ("LC_ALL", "LC_MESSAGES", "LANG"):
        candidate = _normalize(os.environ.get(var))
        if candidate:
            return candidate
    return None


def _detect_from_windows() -> str | None:
    """Win32 ``GetUserDefaultLocaleName`` lookup, returns ``None`` on failure.

    Uses ``ctypes`` because :mod:`locale` on Windows does not always honour
    the user's UI language (vs. the formatting locale). Gracefully degrades
    if ``ctypes`` import fails — for example on a hardened POSIX build with
    ``ctypes`` removed.
    """
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes
    except ImportError:  # pragma: no cover — extremely unusual
        return None

    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        get_user_default = kernel32.GetUserDefaultLocaleName
        get_user_default.restype = ctypes.c_int
        get_user_default.argtypes = [wintypes.LPWSTR, ctypes.c_int]
        # LOCALE_NAME_MAX_LENGTH is 85 in winnls.h.
        buf = ctypes.create_unicode_buffer(85)
        chars_copied = get_user_default(buf, 85)
        if chars_copied == 0:
            return None
        return _normalize(buf.value)
    except (OSError, AttributeError):  # pragma: no cover — Win API edge cases
        return None


def detect_locale(*, override: str | None = None) -> str:
    """Detect the user's preferred locale.

    Resolution order (first non-empty wins):

    1. ``override`` argument (test seam — also used by CLI ``--locale`` flag).
    2. ``ASCENDO_LOCALE`` environment variable.
    3. POSIX env vars: ``LC_ALL`` → ``LC_MESSAGES`` → ``LANG``.
       Format ``pl_PL.UTF-8`` is parsed to ``pl``.
    4. Windows: ``GetUserDefaultLocaleName`` via :mod:`ctypes`.
       Format ``pl-PL`` is parsed to ``pl``.
    5. :data:`DEFAULT_LOCALE`.

    The return value is *always* a member of :data:`SUPPORTED_LOCALES`. If
    detection yields a code we do not ship (e.g. ``'ja'``), the function
    falls back to :data:`DEFAULT_LOCALE` rather than raising.

    Args:
        override: Optional explicit locale code. Bypasses all probing.
            Useful for tests and ``--locale`` CLI flags.

    Returns:
        A locale code guaranteed to appear in :data:`SUPPORTED_LOCALES`.
    """
    candidates: list[str | None] = [
        _normalize(override),
        _normalize(os.environ.get(ENV_VAR)),
        _detect_from_env(),
        _detect_from_windows(),
    ]
    for cand in candidates:
        if cand and cand in SUPPORTED_LOCALES:
            return cand
    return DEFAULT_LOCALE


# ─────────────────────────────────────────────────────────────────────────────
# Translator
# ─────────────────────────────────────────────────────────────────────────────


class Translator:
    """A locale-bound message catalog with formatting + fallback.

    Use :func:`get_translator` to obtain one rather than constructing
    directly. Catalogs are loaded by :class:`I18nLoader` and cached.
    """

    __slots__ = ("_catalog", "_fallback", "_locale")

    def __init__(
        self,
        locale: str,
        catalog: dict[str, str],
        *,
        fallback: "Translator | None" = None,
    ) -> None:
        """Initialise.

        Args:
            locale: The locale code this catalog represents.
            catalog: Flat ``{key: template}`` mapping. Templates use
                Python ``str.format`` placeholder syntax (``{name}``).
            fallback: Optional next-link in the fallback chain. The
                module sets this to the English translator for any
                non-English locale; for ``en`` itself the fallback is
                ``None`` (root of the chain).
        """
        self._locale = locale
        self._catalog = catalog
        self._fallback = fallback

    @property
    def locale(self) -> str:
        """The locale code (``'pl'``, ``'en'``, …) backing this translator."""
        return self._locale

    def has(self, key: str) -> bool:
        """Return True if *key* exists in *this* catalog (no fallback walk)."""
        return key in self._catalog

    def t(self, key: str, /, **kwargs: object) -> str:
        """Look up *key* and format with **kwargs (PEP 3101 ``.format()``).

        Resolution order:

        1. The current locale's catalog.
        2. The fallback translator chain (typically → ``en``).
        3. If still not found: log a WARNING and return the literal
           placeholder ``⟨key⟩`` so the missing string is *visible* in
           the UI without crashing.

        Format-string failures (KeyError on a missing placeholder, etc.)
        are caught and downgraded to a WARNING with the unformatted
        template returned, again to favour graceful UX over hard crashes.
        """
        template = self._lookup(key)
        if template is None:
            _logger.warning(
                "i18n: missing key %r in locale chain (start=%r)",
                key,
                self._locale,
            )
            return f"⟨{key}⟩"
        if not kwargs:
            return template
        try:
            return template.format(**kwargs)
        except (KeyError, IndexError, ValueError) as exc:
            _logger.warning(
                "i18n: format failure for key=%r locale=%r kwargs=%r: %s",
                key,
                self._locale,
                kwargs,
                exc,
            )
            return template

    def t_strict(self, key: str, /, **kwargs: object) -> str:
        """Strict variant of :meth:`t` — raises on missing keys.

        Reserved for callers (typically tests) that want to assert
        translation completeness. Production code should prefer
        :meth:`t`, which never raises.

        Raises:
            MissingMessageError: if *key* is in neither this catalog
                nor any fallback.
        """
        template = self._lookup(key)
        if template is None:
            raise MissingMessageError(key)
        if not kwargs:
            return template
        return template.format(**kwargs)

    # ── internal ────────────────────────────────────────────────────────
    def _lookup(self, key: str) -> str | None:
        """Walk the fallback chain returning the first hit, or ``None``."""
        if key in self._catalog:
            return self._catalog[key]
        if self._fallback is not None:
            hit = self._fallback._lookup(key)  # noqa: SLF001 — chain access by design
            if hit is not None and self._fallback.locale != self._locale:
                _logger.debug(
                    "i18n: key %r resolved via fallback %r (start=%r)",
                    key,
                    self._fallback.locale,
                    self._locale,
                )
            return hit
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Loader
# ─────────────────────────────────────────────────────────────────────────────


class I18nLoader:
    """Discovers and caches catalogs from a ``locales/`` directory.

    Files are JSON with a flat ``{key: translated_string}`` shape — chosen
    over Python source so non-Python tooling (lint, future Rust binding)
    can read them directly.

    Catalogs are loaded lazily (first :meth:`load` or :meth:`get_translator`
    call) and cached for the lifetime of this loader instance. The cache
    is thread-safe via a single mutex.
    """

    def __init__(self, *, locales_dir: Path | None = None) -> None:
        """Initialise.

        Args:
            locales_dir: Directory containing ``<locale>.json`` files.
                Defaults to ``<this-package>/locales/``. Override is
                useful for tests and for plugin-supplied catalogs.
        """
        if locales_dir is None:
            locales_dir = Path(__file__).resolve().parent / "locales"
        self._dir: Final[Path] = locales_dir
        self._catalog_cache: dict[str, dict[str, str]] = {}
        self._translator_cache: dict[str, Translator] = {}
        self._lock = threading.Lock()
        self._default_locale: str = DEFAULT_LOCALE

    # ── catalog access ──────────────────────────────────────────────────
    def load(self, locale: str) -> dict[str, str]:
        """Load (and cache) the raw catalog dict for *locale*.

        Raises:
            UnsupportedLocaleError: if no JSON file exists for *locale*.
        """
        with self._lock:
            cached = self._catalog_cache.get(locale)
            if cached is not None:
                return cached
            path = self._dir / f"{locale}.json"
            if not path.is_file():
                raise UnsupportedLocaleError(
                    f"No catalog file at {path!s} for locale {locale!r}"
                )
            try:
                with path.open(encoding="utf-8") as fh:
                    data = json.load(fh)
            except (OSError, json.JSONDecodeError) as exc:
                raise UnsupportedLocaleError(
                    f"Failed to read catalog {path!s}: {exc}"
                ) from exc
            if not isinstance(data, dict):
                raise UnsupportedLocaleError(
                    f"Catalog {path!s} must be a JSON object, got {type(data).__name__}"
                )
            # Coerce non-str values defensively — JSON allows numbers/bools.
            catalog: dict[str, str] = {str(k): str(v) for k, v in data.items()}
            self._catalog_cache[locale] = catalog
            return catalog

    def available_locales(self) -> list[str]:
        """Return the locales for which a JSON file exists, in canonical order.

        Order matches :data:`SUPPORTED_LOCALES` for known locales; any
        extra (plugin-supplied) locales are appended sorted alphabetically.
        """
        on_disk = {p.stem for p in self._dir.glob("*.json")}
        ordered = [loc for loc in SUPPORTED_LOCALES if loc in on_disk]
        extras = sorted(on_disk - set(SUPPORTED_LOCALES))
        return ordered + extras

    # ── translator access ───────────────────────────────────────────────
    def get_translator(self, locale: str | None = None) -> Translator:
        """Return a :class:`Translator` for *locale*.

        If *locale* is ``None``, the loader's current default is used
        (initialised to :data:`DEFAULT_LOCALE`, mutable via
        :meth:`set_default_locale`).

        Unsupported locales (not in :data:`SUPPORTED_LOCALES` and missing
        on disk) silently fall back to the default — :func:`detect_locale`
        is the recommended way to obtain a known-good code.
        """
        target = locale if locale is not None else self._default_locale
        if target not in SUPPORTED_LOCALES and not (self._dir / f"{target}.json").is_file():
            _logger.debug(
                "i18n: locale %r unknown, falling back to %r",
                target,
                self._default_locale,
            )
            target = self._default_locale

        with self._lock:
            cached = self._translator_cache.get(target)
            if cached is not None:
                return cached
        # Build outside the lock — load() takes its own lock internally and
        # we want to avoid holding _lock across nested acquisitions.
        catalog = self.load(target)
        fallback: Translator | None
        if target == DEFAULT_LOCALE:
            fallback = None
        else:
            fallback = self.get_translator(DEFAULT_LOCALE)
        translator = Translator(target, catalog, fallback=fallback)
        with self._lock:
            self._translator_cache.setdefault(target, translator)
            return self._translator_cache[target]

    def set_default_locale(self, locale: str) -> None:
        """Set the default locale used when :meth:`get_translator` gets ``None``.

        Unsupported codes are coerced to :data:`DEFAULT_LOCALE` to keep
        the loader in a consistent state.
        """
        with self._lock:
            if locale in SUPPORTED_LOCALES or (self._dir / f"{locale}.json").is_file():
                self._default_locale = locale
            else:
                _logger.warning(
                    "i18n: refusing to set unknown default locale %r; keeping %r",
                    locale,
                    self._default_locale,
                )
                self._default_locale = DEFAULT_LOCALE


# ─────────────────────────────────────────────────────────────────────────────
# Module-level singleton + convenience API
# ─────────────────────────────────────────────────────────────────────────────

_state_lock = threading.Lock()
_loader_singleton: I18nLoader | None = None


def _get_loader() -> I18nLoader:
    """Lazy-init the process-wide singleton loader."""
    global _loader_singleton
    if _loader_singleton is None:
        with _state_lock:
            if _loader_singleton is None:
                _loader_singleton = I18nLoader()
    return _loader_singleton


def get_translator(locale: str | None = None) -> Translator:
    """Module-level convenience — returns a translator from the singleton.

    Equivalent to ``_get_loader().get_translator(locale)``. Most callers
    should use this rather than instantiating :class:`I18nLoader` directly.
    """
    return _get_loader().get_translator(locale)


def set_default_locale(locale: str) -> None:
    """Module-level convenience — set the singleton loader's default locale."""
    _get_loader().set_default_locale(locale)
