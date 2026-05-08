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
import warnings
from pathlib import Path
from typing import Annotated, Literal, Optional

from pydantic import AnyUrl, BaseModel, ConfigDict, Field, model_validator
from pydantic.networks import UrlConstraints

# https-only — appcast / update channels must not be MITM-able (T3 mitigation).
HttpsUrl = Annotated[AnyUrl, UrlConstraints(allowed_schemes=["https"])]


class OmahaConfig(BaseModel):
    """Per-app Google Omaha update protocol probe.

    The Omaha protocol (POST + XML or JSON body) is used by
    Google's tools.google.com / update.googleapis.com endpoint to
    drive Keystone / GoogleUpdater clients, and by some Chromium
    forks (e.g. Comet/Perplexity) hosting their own Omaha-compatible
    services.

    `endpoint` is the vendor's Omaha service URL. For Google products
    use ``https://update.googleapis.com/service/update2``. For Comet
    use ``https://www.perplexity.ai/rest/browser/update2``.

    `appid` is the vendor-assigned application id. The format is
    vendor-defined: Google uses lowercase reverse-DNS strings
    (``com.google.drivefs``, ``com.google.geminimacos``) for first-party
    products and 8-4-4-4-12 UUIDs in braces (``{8A69D345-...}``) for
    Chrome. Comet uses the bundle id (``ai.perplexity.comet``).

    `protocol`:
      - "3.0" (default) — XML body, used by Google's Omaha service.
      - "4.0" — JSON body, used by Comet's Perplexity-hosted service.
        Returns updatecheck.nextversion instead of manifest.version.

    `tag` is the Omaha "channel" (e.g. ``m1-prod`` for Gemini,
    ``stable`` for Chrome). Without the right tag, Google's service
    returns ``noupdate`` even for fresh installs.

    `brand` is a 4-character brand code (e.g. ``GGLG`` for Google).
    Optional; defaults to empty.

    Apply remains Tier-B (trigger-only) — Keystone / CometUpdater
    own the actual install; we surface candidate version only.
    """

    model_config = ConfigDict(extra="forbid")

    endpoint: HttpsUrl
    # appid accepts both UUID-in-braces ({8A69D345-...}) and
    # reverse-DNS string formats. The pattern allows letters, digits,
    # dots, dashes, underscores, and braces — sufficient for every
    # known Omaha appid shape.
    appid: Annotated[str, Field(min_length=1, max_length=128,
                                pattern=r"^[A-Za-z0-9._\-{}]+$")]
    protocol: Literal["3.0", "4.0"] = "3.0"
    tag: Optional[Annotated[str, Field(min_length=1, max_length=64,
                                       pattern=r"^[A-Za-z0-9._\-]+$")]] = None
    brand: Optional[Annotated[str, Field(min_length=4, max_length=4,
                                         pattern=r"^[A-Z]+$")]] = None
    http_timeout_s: Annotated[int, Field(ge=1, le=60)] = 8


class MsupdateConfig(BaseModel):
    """Per-app Microsoft AutoUpdate targeting.

    Set `app_id` to the MAU Application ID (e.g. ``XCEL2019``,
    ``MSWD2019``). check phase reads the installed version from
    ``msupdate --config``; apply phase runs ``msupdate --install --apps
    <app_id>`` so only this product updates. Leaving the entire subtable
    out keeps legacy global behaviour (one entry triggers all pending
    Microsoft updates).
    """

    model_config = ConfigDict(extra="forbid")

    app_id: Annotated[str, Field(min_length=2, max_length=32,
                                 pattern=r"^[A-Z0-9]+$")]


class ReleaseFeedConfig(BaseModel):
    """Config for the generic release_feed handler.

    The handler fetches `url` over HTTPS, parses the response as JSON,
    walks `version_path` (dotted, supports `[N]` indices), and echoes the
    string at that path as the candidate version.

    If `download_path` is set, the handler is Tier-A on apply too: it
    downloads the URL at that path and installs the DMG. Without it,
    apply falls back to `open -a` (Tier-B trigger semantics).

    `version_regex` + `version_replace` (M5.7.4) optionally transform the
    raw extracted version string with a single ``re.sub`` pass before it
    is reported as the candidate. Both fields must be supplied together;
    when present, the regex is matched against the raw extracted value
    and ``version_replace`` substitutions are applied. If the regex does
    not match the raw value, the handler falls back to the raw value
    rather than failing — so a vendor format change degrades to "raw"
    detection rather than silently breaking the probe. Use this for
    vendors who publish version strings in non-canonical shapes
    (e.g. Warp's ``v0.2026.05.06.15.42.stable_02`` vs
    CFBundleShortVersionString ``0.2026.05.06.15.42.02``).
    """

    model_config = ConfigDict(extra="forbid")

    url: HttpsUrl
    # version_path is required for json / yaml formats. When format="text"
    # the path is ignored — version_regex matches directly against the
    # raw body. We allow None at the schema level and validate the
    # combination below.
    version_path: Optional[Annotated[str, Field(min_length=1, max_length=256,
                                                pattern=r"^[A-Za-z0-9_.\-\[\]]+$")]] = None
    download_path: Optional[Annotated[str, Field(min_length=1, max_length=256,
                                                  pattern=r"^[A-Za-z0-9_.\-\[\]]+$")]] = None
    # download_asset_pattern (M5.7.5) — for GitHub Releases API responses
    # where ``assets`` is a list of objects with stable ``name`` fields
    # but unstable ordering. The handler walks ``assets[]``, picks the
    # first whose ``name`` matches this regex, and uses its
    # ``browser_download_url``. Mutually exclusive with download_path.
    # Used by Brave (universal DMG buried among ~30 platform assets).
    download_asset_pattern: Optional[Annotated[str, Field(min_length=1, max_length=256)]] = None
    arch_path: Optional[Annotated[str, Field(min_length=1, max_length=256,
                                              pattern=r"^[A-Za-z0-9_.\-\[\]]+$")]] = None
    expected_arch: Optional[Literal["arm64", "x86_64", "universal"]] = None
    http_timeout_s: Annotated[int, Field(ge=1, le=60)] = 8
    version_regex: Optional[Annotated[str, Field(min_length=1, max_length=256)]] = None
    version_replace: Optional[Annotated[str, Field(min_length=0, max_length=256)]] = None
    # Body parser. "json" tries JSON first then falls back to minimal YAML
    # (Electron-builder latest-mac.yml shape). "text" treats the body as
    # plain text — version_regex matches the entire body directly,
    # version_path is ignored. Useful for vendors who publish key=value
    # text feeds (e.g. Devolutions' productinfo.htm).
    format: Literal["json", "text"] = "json"

    @model_validator(mode="after")
    def _validate_version_transform(self) -> "ReleaseFeedConfig":
        # version_path is required for json format, irrelevant for text.
        if self.format == "json" and self.version_path is None:
            raise ValueError(
                "release_feed handler with format=json requires version_path")
        if self.format == "text" and self.version_regex is None:
            raise ValueError(
                "release_feed handler with format=text requires version_regex "
                "(the regex extracts the version from the raw body)")
        # Both fields must be supplied together — regex without replace
        # (or vice versa) is ambiguous and almost certainly a typo.
        if (self.version_regex is None) != (self.version_replace is None):
            raise ValueError(
                "version_regex and version_replace must be supplied together "
                "(both set, or both absent)")
        # Compile-time validate the regex so a typo can't slip through to
        # runtime as a swallowed re.error.
        if self.version_regex is not None:
            import re as _re
            try:
                _re.compile(self.version_regex)
            except _re.error as exc:
                raise ValueError(
                    f"version_regex is not a valid Python regex: {exc}")
        # download_asset_pattern (M5.7.5) is mutually exclusive with
        # download_path — they're two different ways to point at a DMG
        # in the response body, and supplying both would be ambiguous.
        if (self.download_asset_pattern is not None
                and self.download_path is not None):
            raise ValueError(
                "download_asset_pattern and download_path are mutually "
                "exclusive (pick one)")
        if self.download_asset_pattern is not None:
            import re as _re
            try:
                _re.compile(self.download_asset_pattern)
            except _re.error as exc:
                raise ValueError(
                    f"download_asset_pattern is not a valid Python regex: {exc}")
        return self


class WebApp(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Identity
    slug: Annotated[str, Field(pattern=r"^[a-z0-9-]+$", min_length=1, max_length=64)]
    bundle_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9._-]+$",
                                    min_length=1, max_length=256)]
    display_name: Annotated[str, Field(min_length=1, max_length=128)]
    handler: Literal["sparkle", "github_dmg", "keystone", "squirrel",
                     "builtin", "msupdate", "docker", "release_feed",
                     "omaha"]
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

    # Microsoft AutoUpdate per-app targeting (M5.7.4)
    # When set, msupdate handler runs `--install --apps <app_id>` and reads
    # the per-app installed version from `msupdate --config` so the apply
    # phase can correctly classify up_to_date apps.
    msupdate: Optional["MsupdateConfig"] = None

    # Google Omaha protocol probe (M5.7.5 Phase A)
    # When handler="omaha", this sub-table carries the endpoint + appid
    # (and optional channel tag, brand, protocol-version override).
    # Apply still routes through keystone_apply when ksadmin_product_id
    # is also set; otherwise apply is `open -a` (Tier-B trigger).
    omaha: Optional["OmahaConfig"] = None

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
        if h == "omaha" and self.omaha is None:
            raise ValueError("omaha handler requires [apps.omaha] table")

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
        # ksadmin_product_id is permitted on keystone (canonical) and
        # on omaha (where it lets apply delegate to keystone_apply
        # instead of falling back to `open -a`).
        if h not in ("keystone", "omaha") and self.ksadmin_product_id is not None:
            raise ValueError(
                f"ksadmin_product_id only valid for keystone or omaha; got handler={h!r}")
        if h != "builtin" and self.update_url is not None:
            raise ValueError(
                f"update_url only valid for builtin; got handler={h!r}")
        if h != "release_feed" and self.release_feed is not None:
            raise ValueError(
                f"release_feed sub-table only valid for release_feed handler; got handler={h!r}")
        if h != "msupdate" and self.msupdate is not None:
            raise ValueError(
                f"msupdate sub-table only valid for msupdate handler; got handler={h!r}")
        if h != "omaha" and self.omaha is not None:
            raise ValueError(
                f"omaha sub-table only valid for omaha handler; got handler={h!r}")
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

        # Auto-coerce v1 → v2 (no field shape changed; just bump the literal).
        # Spec §5.1 requires emitting a deprecation message exactly once
        # per load so operators on M5.6 setups know to bump their override.
        if registry.schema_version == "ascendo-web-apps/v1":
            warnings.warn(
                f"web_apps registry at {shipped} declares schema "
                "'ascendo-web-apps/v1'; auto-coerced to 'ascendo-web-apps/v2'. "
                "Bump the 'schema' field on next edit.",
                DeprecationWarning,
                stacklevel=2,
            )
            registry = WebRegistry(
                schema="ascendo-web-apps/v2",
                app=registry.apps,
            )

        if user_override is not None and user_override.exists():
            user_data = cls._read_toml(user_override)
            user_reg = cls.model_validate(user_data)
            if user_reg.schema_version == "ascendo-web-apps/v1":
                warnings.warn(
                    f"user override at {user_override} declares schema "
                    "'ascendo-web-apps/v1'; auto-coerced to 'ascendo-web-apps/v2'. "
                    "Bump the 'schema' field on next edit.",
                    DeprecationWarning,
                    stacklevel=2,
                )
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
