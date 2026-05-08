"""Pydantic-validated _apps.toml registry for the WebManager.

Loads the shipped registry at adapters/macos/config/web_apps.toml plus
an optional user override at ~/.config/ascendo/web_apps.toml. Override
merge keyed by bundle_id (user wins; new bundle_ids append).

v1 schema (M5.6) is auto-coerced to v2 on load. v1 used slug-keyed
overrides; v2 uses bundle_id. Slug remains in the row for display only.

release_feed handler (new in v2) is a generic JSON-over-HTTPS probe;
its config sits under [apps.release_feed] sub-table.
"""
from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Annotated, Literal, Optional

from pydantic import AnyUrl, BaseModel, ConfigDict, Field, model_validator
from pydantic.networks import UrlConstraints

# https-only — appcast / update channels must not be MITM-able (T3 mitigation).
HttpsUrl = Annotated[AnyUrl, UrlConstraints(allowed_schemes=["https"])]


class ReleaseFeedConfig(BaseModel):
    """Config for the generic release_feed handler.

    The handler fetches `url` over HTTPS, parses the response as JSON,
    walks `version_path` (dotted, supports `[N]` indices), and echoes the
    string at that path as the candidate version.

    If `download_path` is set, the handler is Tier-A on apply too: it
    downloads the URL at that path and installs the DMG. Without it,
    apply falls back to `open -a` (Tier-B trigger semantics).
    """

    model_config = ConfigDict(extra="forbid")

    url: HttpsUrl
    version_path: Annotated[str, Field(min_length=1, max_length=256,
                                       pattern=r"^[A-Za-z0-9_.\[\]]+$")]
    download_path: Optional[Annotated[str, Field(min_length=1, max_length=256,
                                                  pattern=r"^[A-Za-z0-9_.\[\]]+$")]] = None
    arch_path: Optional[Annotated[str, Field(min_length=1, max_length=256,
                                              pattern=r"^[A-Za-z0-9_.\[\]]+$")]] = None
    expected_arch: Optional[Literal["arm64", "x86_64", "universal"]] = None
    http_timeout_s: Annotated[int, Field(ge=1, le=60)] = 8


class WebApp(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Identity
    slug: Annotated[str, Field(pattern=r"^[a-z0-9-]+$", min_length=1, max_length=64)]
    bundle_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9._-]+$",
                                    min_length=1, max_length=256)]
    display_name: Annotated[str, Field(min_length=1, max_length=128)]
    handler: Literal["sparkle", "github_dmg", "keystone", "squirrel",
                     "builtin", "msupdate", "docker", "release_feed"]
    app_path: Optional[Path] = None
    enabled: bool = True
    notes: Optional[str] = None

    # Sparkle
    appcast_url: Optional[HttpsUrl] = None
    apply_cli_argv: Optional[list[str]] = None

    # GitHub DMG
    github_repo: Annotated[Optional[str], Field(pattern=r"^[\w.-]+/[\w.-]+$")] = None
    asset_pattern: Optional[str] = None
    arch: Optional[Literal["arm64", "x86_64", "universal"]] = None
    prerelease: Optional[bool] = None

    # Keystone
    ksadmin_product_id: Optional[str] = None

    # Builtin
    update_url: Optional[HttpsUrl] = None

    # Release feed (new v2 handler)
    release_feed: Optional[ReleaseFeedConfig] = None

    # Behaviour overrides (apply to any handler)
    defer_if_running: Optional[bool] = None
    kill_safe: Optional[bool] = None

    @property
    def effective_arch(self) -> str:
        return self.arch or "arm64"

    @property
    def effective_prerelease(self) -> bool:
        return self.prerelease if self.prerelease is not None else False

    @model_validator(mode="after")
    def _validate_handler_fields(self) -> "WebApp":
        h = self.handler
        # Required by handler
        if h == "sparkle" and self.appcast_url is None:
            raise ValueError("sparkle handler requires appcast_url")
        if h == "github_dmg":
            if self.github_repo is None:
                raise ValueError("github_dmg handler requires github_repo")
            if self.asset_pattern is None:
                raise ValueError("github_dmg handler requires asset_pattern")
        if h == "keystone" and self.ksadmin_product_id is None:
            raise ValueError("keystone handler requires ksadmin_product_id")
        if h == "release_feed" and self.release_feed is None:
            raise ValueError("release_feed handler requires [apps.release_feed] table")

        # Cross-handler fields rejected (catches typos)
        if h != "sparkle" and (self.appcast_url is not None
                                or self.apply_cli_argv is not None):
            raise ValueError(
                f"appcast_url / apply_cli_argv only valid for sparkle; got handler={h!r}")
        if h != "github_dmg":
            if self.github_repo is not None or self.asset_pattern is not None:
                raise ValueError(
                    f"github_repo / asset_pattern only valid for github_dmg; got handler={h!r}")
            if self.arch is not None or self.prerelease is not None:
                raise ValueError(
                    f"arch / prerelease only valid for github_dmg; got handler={h!r}")
        if h != "keystone" and self.ksadmin_product_id is not None:
            raise ValueError(
                f"ksadmin_product_id only valid for keystone; got handler={h!r}")
        if h != "builtin" and self.update_url is not None:
            raise ValueError(
                f"update_url only valid for builtin; got handler={h!r}")
        if h != "release_feed" and self.release_feed is not None:
            raise ValueError(
                f"release_feed sub-table only valid for release_feed handler; got handler={h!r}")
        return self


class WebRegistry(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_version: Literal["ascendo-web-apps/v1", "ascendo-web-apps/v2"] = Field(alias="schema")
    apps: list[WebApp] = Field(default_factory=list, alias="app")

    @classmethod
    def load(
        cls,
        shipped: Path,
        user_override: Optional[Path],
    ) -> "WebRegistry":
        shipped_data = cls._read_toml(shipped)
        registry = cls.model_validate(shipped_data)

        # Auto-coerce v1 → v2 (no field shape changed; just bump the literal)
        if registry.schema_version == "ascendo-web-apps/v1":
            registry = WebRegistry(
                schema="ascendo-web-apps/v2",
                app=registry.apps,
            )

        if user_override is not None and user_override.exists():
            user_data = cls._read_toml(user_override)
            user_reg = cls.model_validate(user_data)
            if user_reg.schema_version == "ascendo-web-apps/v1":
                user_reg = WebRegistry(
                    schema="ascendo-web-apps/v2",
                    app=user_reg.apps,
                )
            by_bundle = {a.bundle_id: a for a in registry.apps}
            for ua in user_reg.apps:
                by_bundle[ua.bundle_id] = ua  # user replaces shipped
            registry = WebRegistry(
                schema="ascendo-web-apps/v2",
                app=list(by_bundle.values()),
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

    def find_by_bundle_id(self, bundle_id: str) -> Optional[WebApp]:
        for app in self.active_apps():
            if app.bundle_id == bundle_id:
                return app
        return None
