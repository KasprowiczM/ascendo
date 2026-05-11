"""Pydantic-validated web_apps.toml registry for the Linux WebManager.

Loads the shipped registry at adapters/ubuntu/config/web_apps.toml plus
an optional user override at ~/.config/ascendo/web_apps.toml. Override
merge keyed by app_id (user wins; new app_ids append).

Linux flavour (M-Linux-Web v0.1):
  Tier-A handlers (real candidate-version probe):
    - github_release: GitHub Releases API + asset_pattern (.AppImage / .deb / .tar.gz)
    - release_feed:   generic JSON-over-HTTPS probe (port of macOS handler)
    - appimage:       embedded `--appimage-version` query (Tier-A IFF supported)
  Tier-B handlers (trigger-only):
    - builtin: emit `triggered` only; user must update via vendor flow

Unlike macOS, Linux does NOT need sparkle / keystone / squirrel — those
are macOS update frameworks. Most Linux "web apps" actually flow through
APT, snap, or flatpak (and the discovery layer excludes those).
"""
from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Annotated, Literal, Optional

from pydantic import AnyUrl, BaseModel, ConfigDict, Field, model_validator
from pydantic.networks import UrlConstraints

# https-only — feed/release URLs must not be MITM-able (T3 mitigation).
HttpsUrl = Annotated[AnyUrl, UrlConstraints(allowed_schemes=["https"])]


class ReleaseFeedConfig(BaseModel):
    """Config for the generic release_feed handler.

    The handler fetches `url` over HTTPS, parses the response as JSON
    (or text when format="text"), walks `version_path` (dotted, supports
    `[N]` indices), and echoes the string at that path as the candidate
    version. Optional `version_regex` + `version_replace` transform the
    raw extracted version. If `download_path` (or `download_asset_pattern`
    for GitHub Releases shape) is set, the handler is Tier-A on apply too:
    it downloads the URL at that path.
    """

    model_config = ConfigDict(extra="forbid")

    url: HttpsUrl
    version_path: Optional[Annotated[str, Field(min_length=1, max_length=256,
                                                pattern=r"^[A-Za-z0-9_.\-\[\]]+$")]] = None
    download_path: Optional[Annotated[str, Field(min_length=1, max_length=256,
                                                  pattern=r"^[A-Za-z0-9_.\-\[\]]+$")]] = None
    download_asset_pattern: Optional[Annotated[str, Field(min_length=1, max_length=256)]] = None
    http_timeout_s: Annotated[int, Field(ge=1, le=60)] = 8
    version_regex: Optional[Annotated[str, Field(min_length=1, max_length=256)]] = None
    version_replace: Optional[Annotated[str, Field(min_length=0, max_length=256)]] = None
    format: Literal["json", "text"] = "json"

    @model_validator(mode="after")
    def _validate(self) -> "ReleaseFeedConfig":
        if self.format == "json" and self.version_path is None:
            raise ValueError(
                "release_feed handler with format=json requires version_path")
        if self.format == "text" and self.version_regex is None:
            raise ValueError(
                "release_feed handler with format=text requires version_regex")
        if (self.version_regex is None) != (self.version_replace is None):
            raise ValueError(
                "version_regex and version_replace must be supplied together")
        if self.version_regex is not None:
            import re as _re
            try:
                _re.compile(self.version_regex)
            except _re.error as exc:
                raise ValueError(
                    f"version_regex is not a valid Python regex: {exc}")
        if (self.download_asset_pattern is not None
                and self.download_path is not None):
            raise ValueError(
                "download_asset_pattern and download_path are mutually exclusive")
        if self.download_asset_pattern is not None:
            import re as _re
            try:
                _re.compile(self.download_asset_pattern)
            except _re.error as exc:
                raise ValueError(
                    f"download_asset_pattern is not a valid Python regex: {exc}")
        return self


class GitHubReleaseConfig(BaseModel):
    """Config for the github_release handler (Linux assets).

    Probes GitHub's `releases/latest` API; uses `tag_name` (with optional
    leading `v` stripped) as the candidate version. If `asset_pattern`
    matches an asset name, that asset's `browser_download_url` becomes
    the apply download URL.
    """

    model_config = ConfigDict(extra="forbid")

    repo: Annotated[str, Field(pattern=r"^[\w.-]+/[\w.-]+$",
                                min_length=3, max_length=128)]
    asset_pattern: Annotated[str, Field(min_length=1, max_length=256)]
    prerelease: bool = False
    strip_v_prefix: bool = True
    http_timeout_s: Annotated[int, Field(ge=1, le=60)] = 8

    @model_validator(mode="after")
    def _validate(self) -> "GitHubReleaseConfig":
        import re as _re
        try:
            _re.compile(self.asset_pattern)
        except _re.error as exc:
            raise ValueError(f"asset_pattern is not a valid Python regex: {exc}")
        return self


class AppImageConfig(BaseModel):
    """Config for the appimage handler.

    The check phase tries `<path> --appimage-version` to read the embedded
    version. Many AppImages don't support that flag, in which case the
    handler returns empty (Tier-B trigger-only).

    `path` is the absolute on-disk path to the AppImage file. Required for
    apply; optional for check (discovery may have already located it).
    """

    model_config = ConfigDict(extra="forbid")

    path: Optional[Annotated[str, Field(min_length=1, max_length=512)]] = None


class WebApp(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Identity
    app_id: Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9._-]*$",
                                  min_length=1, max_length=64)]
    display_name: Annotated[str, Field(min_length=1, max_length=128)]
    handler: Literal["github_release", "release_feed", "appimage", "builtin"]
    enabled: bool = True
    notes: Optional[str] = None

    # Discovery hint: a substring or filename pattern used by the discovery
    # layer to associate a found AppImage / binary with this registry entry.
    # Examples: "Cursor", "obsidian", "Joplin". Case-insensitive.
    discovery_hint: Optional[Annotated[str, Field(min_length=1, max_length=128)]] = None

    # Per-handler config sub-tables
    github_release: Optional[GitHubReleaseConfig] = None
    release_feed: Optional[ReleaseFeedConfig] = None
    appimage: Optional[AppImageConfig] = None

    # Tier-B: builtin
    update_url: Optional[HttpsUrl] = None

    # Behaviour overrides
    defer_if_running: Optional[bool] = None
    install_path: Optional[Annotated[str, Field(min_length=1, max_length=512)]] = None

    @model_validator(mode="after")
    def _validate_handler_fields(self) -> "WebApp":
        h = self.handler
        if h == "github_release" and self.github_release is None:
            raise ValueError("github_release handler requires [apps.github_release] table")
        if h == "release_feed" and self.release_feed is None:
            raise ValueError("release_feed handler requires [apps.release_feed] table")
        # appimage may have no sub-table (path discovered at runtime)

        if h != "github_release" and self.github_release is not None:
            raise ValueError(
                f"github_release sub-table only valid for github_release handler; got {h!r}")
        if h != "release_feed" and self.release_feed is not None:
            raise ValueError(
                f"release_feed sub-table only valid for release_feed handler; got {h!r}")
        if h != "appimage" and self.appimage is not None:
            raise ValueError(
                f"appimage sub-table only valid for appimage handler; got {h!r}")
        if h != "builtin" and self.update_url is not None:
            raise ValueError(
                f"update_url only valid for builtin; got {h!r}")
        return self


class WebRegistry(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_version: Literal["ascendo-web-apps-linux/v1"] = Field(alias="schema")
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
            by_id = {a.app_id: a for a in registry.apps}
            for ua in user_reg.apps:
                by_id[ua.app_id] = ua  # user replaces shipped
            registry = WebRegistry(
                schema="ascendo-web-apps-linux/v1",
                app=list(by_id.values()),
            )
        return registry

    @staticmethod
    def _read_toml(path: Path) -> dict:
        with path.open("rb") as fh:
            return tomllib.load(fh)

    def active_apps(self) -> list[WebApp]:
        return [a for a in self.apps if a.enabled]

    def find(self, app_id: str) -> Optional[WebApp]:
        for app in self.active_apps():
            if app.app_id == app_id:
                return app
        return None
