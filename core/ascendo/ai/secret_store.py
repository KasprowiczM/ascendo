"""ISecretStore implementations: keyring-backed and JSON-file fallback.

Usage::

    from ascendo.ai.secret_store import get_secret_store

    store = get_secret_store()
    store.set("api_key", "sk-...")
    assert store.get("api_key") == "sk-..."
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from ascendo.interfaces.secret_store import ISecretStore

_log = logging.getLogger(__name__)

_SERVICE_NAME = "ascendo-ai"


# ---------------------------------------------------------------------------
# Keyring-backed implementation
# ---------------------------------------------------------------------------


class KeyringSecretStore(ISecretStore):
    """Wraps the ``keyring`` library with service name ``ascendo-ai``.

    Preferred on macOS (Keychain), Windows (Credential Locker) and Linux
    (SecretService / kwallet) when the ``keyring`` package is installed
    and a working backend is available.
    """

    def __init__(self) -> None:
        import keyring as _kr  # deferred; caller already checked available()

        self._kr = _kr

    @classmethod
    def available(cls) -> bool:
        """Return True if ``keyring`` can be imported and has a working backend."""
        try:
            import keyring as _kr

            backend = _kr.get_keyring()
            return "fail" not in type(backend).__module__
        except Exception:
            return False

    def get(self, key: str) -> str | None:
        return self._kr.get_password(_SERVICE_NAME, key)

    def set(self, key: str, value: str) -> None:
        self._kr.set_password(_SERVICE_NAME, key, value)

    def delete(self, key: str) -> None:
        import contextlib

        with contextlib.suppress(self._kr.errors.PasswordDeleteError):
            self._kr.delete_password(_SERVICE_NAME, key)

    def has(self, key: str) -> bool:
        return self.get(key) is not None


# ---------------------------------------------------------------------------
# File-backed fallback
# ---------------------------------------------------------------------------


def _default_config_path() -> Path:
    override = os.environ.get("ASCENDO_AI_CONFIG")
    if override:
        return Path(override)
    return Path.home() / ".config" / "ascendo" / "ai.json"


class FileSecretStore(ISecretStore):
    """JSON-file secret store with 0600 permissions.

    Mirrors the original ``_write_config`` / ``_read_config`` behaviour in
    ``routes/ai.py`` so existing users keep working transparently.
    """

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or _default_config_path()

    @property
    def path(self) -> Path:
        return self._path

    # -- helpers -------------------------------------------------------------

    def _read(self) -> dict[str, Any]:
        if not self._path.is_file():
            return {}
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError) as exc:
            _log.warning("failed to read secret store at %s: %s", self._path, exc)
            return {}

    def _write(self, data: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
        tmp.chmod(0o600)
        tmp.replace(self._path)

    # -- ISecretStore --------------------------------------------------------

    def get(self, key: str) -> str | None:
        val = self._read().get(key)
        return val if isinstance(val, str) else None

    def set(self, key: str, value: str) -> None:
        data = self._read()
        data[key] = value
        self._write(data)

    def delete(self, key: str) -> None:
        data = self._read()
        if key in data:
            del data[key]
            self._write(data)

    def has(self, key: str) -> bool:
        return self.get(key) is not None


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def get_secret_store(config_path: Path | None = None) -> ISecretStore:
    """Return the best available secret store.

    Tries :class:`KeyringSecretStore` first; falls back to
    :class:`FileSecretStore` when keyring is unavailable.
    """
    if KeyringSecretStore.available():
        _log.debug("using keyring secret store (service=%s)", _SERVICE_NAME)
        return KeyringSecretStore()
    _log.debug("keyring unavailable — falling back to file secret store")
    return FileSecretStore(path=config_path)
