"""Duck-typed provider for the per-OS web-app registry.

`core` must not import an adapter package (ADR-0005). The dashboard's
web-config surface (``routes/web_config.py``) instead asks the active
:class:`~ascendo.interfaces.adapter.IAdapter` for a provider via
``adapter.web_registry()``. The macOS adapter returns an object satisfying
this protocol that wraps its ``ascendo_macos.web_registry`` types; other OSes
return ``None``.

Runtime-checkable so a fake provider in tests is accepted structurally.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class IWebRegistryProvider(Protocol):
    """Everything the dashboard web-config routes need from an OS adapter."""

    @property
    def shipped_registry_path(self) -> Path | None:
        """Absolute path to the adapter's shipped ``web_apps.toml`` (or None)."""
        ...

    @property
    def lib_dir(self) -> Path | None:
        """Absolute path to the adapter's ``lib/`` dir (probe handlers)."""
        ...

    def load_merged(self, user_path: Path | None) -> Any | None:
        """Load the shipped registry merged with ``user_path`` (when present).

        Returns the registry object (``.find(slug)`` + iterable) or ``None``
        when no registry is available. Never raises.
        """
        ...

    def validate_app(self, raw: dict) -> Any:
        """Validate a candidate entry against the OS WebApp model.

        Returns the validated app; raises ``ValueError`` (or a subclass such
        as pydantic ``ValidationError``) on an invalid entry.
        """
        ...

    def validate_registry(self, apps: list[dict]) -> None:
        """Validate a whole merged registry (the user-override write gate).

        Raises on an invalid registry; returns ``None`` on success.
        """
        ...
