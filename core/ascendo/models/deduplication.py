"""Cross-source application deduplication configuration schema."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field


class AppSourceConfig(BaseModel):
    """Configuration for a single logical app and its known sources."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(description="Logical application ID (e.g., 'claude').")
    name: str = Field(description="Display name (e.g., 'Claude').")
    preferred_order: list[str] = Field(
        description="Ordered list of preferred source types (e.g., ['web', 'winget', 'npm'])."
    )
    sources: dict[str, str] = Field(
        description="Mapping of source type to the source-specific package ID."
    )


class AppSourcesRegistry(BaseModel):
    """Top-level schema for app_sources.toml."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    apps: list[AppSourceConfig] = Field(default_factory=list, alias="app")

    @classmethod
    def load(cls, path: Path) -> "AppSourcesRegistry":
        if not path.exists():
            return cls(apps=[])
        with path.open("rb") as fh:
            data = tomllib.load(fh)
        return cls.model_validate(data)
