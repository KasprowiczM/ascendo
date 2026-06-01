"""macOS implementation of the core :class:`IWebRegistryProvider` protocol.

Wraps the adapter's own ``ascendo_macos.web_registry`` types so the dashboard
web-config routes can resolve the registry through ``adapter.web_registry()``
instead of importing the adapter package from core (ADR-0005 / audit A5).
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)


class MacWebRegistryProvider:
    """Satisfies ``ascendo.interfaces.web_registry.IWebRegistryProvider``."""

    def __init__(self, *, shipped_registry_path: Path, lib_dir: Path) -> None:
        self._shipped = shipped_registry_path
        self._lib_dir = lib_dir

    @property
    def shipped_registry_path(self) -> Path | None:
        return self._shipped

    @property
    def lib_dir(self) -> Path | None:
        return self._lib_dir

    def load_merged(self, user_path: Path | None) -> Any | None:
        if self._shipped is None or not self._shipped.is_file():
            return None
        try:
            from .web_registry import WebRegistry

            return WebRegistry.load(
                self._shipped, user_path if (user_path and user_path.exists()) else None
            )
        except Exception:  # noqa: BLE001 — a bad registry must not 500 the dashboard
            _log.exception("macOS web registry load failed")
            return None

    def validate_app(self, raw: dict) -> Any:
        from .web_registry import WebApp

        return WebApp.model_validate(raw)

    def validate_registry(self, apps: list[dict]) -> None:
        from .web_registry import WebRegistry

        WebRegistry.model_validate({"schema": "ascendo-web-apps/v2", "app": apps})
