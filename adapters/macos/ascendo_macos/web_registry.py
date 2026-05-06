"""Pydantic-validated _apps.toml registry for the WebManager.

Loads the shipped registry at adapters/macos/config/web_apps.toml plus
an optional user override at ~/.config/ascendo/web_apps.toml. Merge by
slug (user wins; new slugs append).
"""
from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Annotated, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator

HANDLERS = ("sparkle", "github_dmg", "keystone", "squirrel",
            "builtin", "msupdate", "docker")


class WebApp(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slug: Annotated[str, Field(pattern=r"^[a-z0-9-]+$", min_length=1, max_length=64)]
    bundle_id: Annotated[str, Field(min_length=1, max_length=256)]
    display_name: Annotated[str, Field(min_length=1, max_length=128)]
    handler: Literal["sparkle", "github_dmg", "keystone", "squirrel",
                     "builtin", "msupdate", "docker"]
    app_path: Optional[Path] = None
    enabled: bool = True
    notes: Optional[str] = None

    # Sparkle
    appcast_url: Optional[HttpUrl] = None
    apply_cli_argv: Optional[list[str]] = None

    # GitHub DMG
    github_repo: Annotated[Optional[str], Field(pattern=r"^[\w.-]+/[\w.-]+$")] = None
    asset_pattern: Optional[str] = None
    arch: Literal["arm64", "x86_64", "universal"] = "arm64"
    prerelease: bool = False

    # Keystone
    ksadmin_product_id: Optional[str] = None

    # Builtin
    update_url: Optional[HttpUrl] = None

    @model_validator(mode="after")
    def _validate_handler_fields(self) -> "WebApp":
        h = self.handler
        if h == "sparkle" and self.appcast_url is None:
            raise ValueError("sparkle handler requires appcast_url")
        if h == "github_dmg":
            if self.github_repo is None:
                raise ValueError("github_dmg handler requires github_repo")
            if self.asset_pattern is None:
                raise ValueError("github_dmg handler requires asset_pattern")
        if h == "keystone" and self.ksadmin_product_id is None:
            raise ValueError("keystone handler requires ksadmin_product_id")

        # Reject handler-irrelevant fields to catch typos.
        if h != "sparkle":
            if self.appcast_url is not None or self.apply_cli_argv is not None:
                raise ValueError(
                    f"appcast_url / apply_cli_argv only valid for sparkle; "
                    f"got handler={h!r}"
                )
        if h != "github_dmg":
            if self.github_repo is not None or self.asset_pattern is not None:
                raise ValueError(
                    f"github_repo / asset_pattern only valid for github_dmg; "
                    f"got handler={h!r}"
                )
        if h != "keystone" and self.ksadmin_product_id is not None:
            raise ValueError(
                f"ksadmin_product_id only valid for keystone; got handler={h!r}"
            )
        if h != "builtin" and self.update_url is not None:
            raise ValueError(
                f"update_url only valid for builtin; got handler={h!r}"
            )
        return self


class WebRegistry(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_version: Literal["ascendo-web-apps/v1"] = Field(alias="schema")
    apps: list[WebApp] = Field(default_factory=list, alias="app")

    @classmethod
    def load(
        cls,
        shipped: Path,
        user_override: Optional[Path],
    ) -> "WebRegistry":
        shipped_data = cls._read_toml(shipped)
        registry = cls.model_validate(shipped_data)

        if user_override is not None and user_override.exists():
            user_data = cls._read_toml(user_override)
            user_reg = cls.model_validate(user_data)
            by_slug = {a.slug: a for a in registry.apps}
            for ua in user_reg.apps:
                by_slug[ua.slug] = ua  # user replaces shipped
            registry = WebRegistry(
                schema=registry.schema_version,
                app=list(by_slug.values()),
            )
        return registry

    @staticmethod
    def _read_toml(path: Path) -> dict:
        with path.open("rb") as fh:
            return tomllib.load(fh)

    def active_apps(self) -> list[WebApp]:
        return [a for a in self.apps if a.enabled]

    def find(self, slug: str) -> Optional[WebApp]:
        for app in self.active_apps():
            if app.slug == slug:
                return app
        return None
