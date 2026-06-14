"""Secret storage abstraction for sensitive credentials."""
from __future__ import annotations

from abc import ABC, abstractmethod


class ISecretStore(ABC):
    """Platform-agnostic secret storage.

    Implementations:
    - KeyringSecretStore: uses the ``keyring`` library (works on macOS Keychain,
      Windows Credential Locker, Linux SecretService)
    - FileSecretStore: fallback JSON file with 0600 permissions (current behavior)
    """

    @abstractmethod
    def get(self, key: str) -> str | None:
        """Retrieve a secret by key, or None if not stored."""

    @abstractmethod
    def set(self, key: str, value: str) -> None:
        """Store a secret."""

    @abstractmethod
    def delete(self, key: str) -> None:
        """Remove a secret. No-op if not present."""

    @abstractmethod
    def has(self, key: str) -> bool:
        """Check if a secret exists."""
