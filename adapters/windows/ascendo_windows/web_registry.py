"""Pydantic-validated ``web_apps.toml`` registry for the Windows WebManager.

Loads the shipped registry at ``adapters/windows/config/web_apps.toml`` plus
an optional user override at ``%LOCALAPPDATA%/Ascendo/web_apps.toml`` or
``~/.config/ascendo/web_apps.toml`` (Windows respects both). Override merge
keyed by ``slug`` (user wins; new slugs append).

This is the v1 Windows companion to the macOS v2 registry; we intentionally
ship a smaller handler set (``github_release``, ``release_feed``, ``builtin``)
because the macOS-specific update channels (Sparkle / Keystone / Squirrel /
Omaha / msupdate / Docker) don't apply on Windows. The schema literal is
``ascendo-web-apps-windows/v1``.

Threat model: https-only on every remote URL (T3 MITM mitigation). Slug +
``windows_uninstall_key`` are constrained character classes (T4 — no shell
injection via registry value or filesystem path).
"""
from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Annotated, Literal, Optional

from pydantic import AnyUrl, BaseModel, ConfigDict, Field, model_validator
from pydantic.networks import UrlConstraints

# https-only — release feeds / GH API must not be MITM-able.
HttpsUrl = Annotated[AnyUrl, UrlConstraints(allowed_schemes=["https"])]


class GitHubReleaseConfig(BaseModel):
    """Per-app GitHub Releases API probe (Tier-A).

    The handler queries
    ``https://api.github.com/repos/<repo>/releases/latest``, filters assets
    by ``asset_pattern`` (regex against ``name``), and returns the matching
    release's ``tag_name`` as the candidate version.

    **Apply tiers**:
      * Default (v1): Tier-B trigger-only — apply opens the release HTML
        page in the default browser; operator runs the installer manually.
      * Opt-in Tier-A (set ``tier_a_apply=True`` on the app entry): apply
        downloads the matching asset, runs Get-AuthenticodeSignature, then
        executes the installer silently (NSIS ``/S`` by default, ``msiexec
        /qn /norestart`` for .msi). Verify reads the installed version
        back from the Uninstall registry hive.

    ``arch`` defaults to ``x64`` because that's the modal Windows install
    target; the regex in ``asset_pattern`` ultimately selects which asset
    binds. ``prerelease`` (when True) tells the handler to fetch
    ``/releases`` (not ``/releases/latest``) and walk descending until it
    finds one whose pattern matches.

    **Tier-A apply fields** (ignored when ``tier_a_apply=False`` on the
    parent ``WebAppV1``):
      * ``expected_publisher``: substring expected in the Authenticode
        ``SignerCertificate.Subject``. When ``None`` the publisher check
        is skipped (signature must still be ``Valid`` -- "unsigned" is
        always rejected). Set this to the vendor's CN (e.g.
        ``"O=Brave Software, Inc."``) to bind the install to a specific
        signer and harden against asset-pattern repo-typo attacks.
      * ``silent_args``: argv list passed to the installer. ``None``
        means "auto-detect" -- the handler uses ``["/S"]`` for .exe
        (NSIS / Inno default) and ``["/qn", "/norestart"]`` after
        ``msiexec /i <path>`` for .msi.
      * ``installer_kind``: ``"exe"`` or ``"msi"``. ``None`` means
        "auto-detect from the asset filename extension". Set explicitly
        for repos that ship both shapes from the same pattern.
      * ``kill_processes``: list of process basenames (no .exe) to kill
        before running the installer. Many vendors fail or prompt when
        the running app holds open file handles in ``Program Files``.
      * ``display_name_pattern``: regex matched against ``DisplayName``
        when scanning the Uninstall registry for the post-install
        version readback. ``None`` falls back to substring-match of
        the app slug against ``DisplayName``.
    """

    model_config = ConfigDict(extra="forbid")

    repo: Annotated[str, Field(pattern=r"^[\w.-]+/[\w.-]+$",
                               min_length=3, max_length=128)]
    asset_pattern: Annotated[str, Field(min_length=1, max_length=256)]
    arch: Annotated[str, Field(pattern=r"^[a-z0-9_]+$",
                                min_length=2, max_length=16)] = "x64"
    prerelease: bool = False
    http_timeout_s: Annotated[int, Field(ge=1, le=60)] = 8

    # Tier-A apply fields (all optional; ignored unless the parent
    # WebAppV1 has tier_a_apply=True).
    expected_publisher: Optional[Annotated[str, Field(
        min_length=1, max_length=256)]] = None
    silent_args: Optional[list[Annotated[str, Field(
        min_length=1, max_length=128)]]] = None
    installer_kind: Optional[Literal["exe", "msi"]] = None
    kill_processes: Optional[list[Annotated[str, Field(
        min_length=1, max_length=128, pattern=r"^[\w.\- ]+$")]]] = None
    display_name_pattern: Optional[Annotated[str, Field(
        min_length=1, max_length=256)]] = None

    @model_validator(mode="after")
    def _validate_regex(self) -> "GitHubReleaseConfig":
        import re as _re
        try:
            _re.compile(self.asset_pattern)
        except _re.error as exc:
            raise ValueError(
                f"asset_pattern is not a valid Python regex: {exc}")
        if self.display_name_pattern is not None:
            try:
                _re.compile(self.display_name_pattern)
            except _re.error as exc:
                raise ValueError(
                    f"display_name_pattern is not a valid Python regex: {exc}")
        if self.silent_args is not None and len(self.silent_args) == 0:
            raise ValueError(
                "silent_args must contain at least one argument when set; "
                "use None for auto-detection")
        if self.kill_processes is not None and len(self.kill_processes) == 0:
            raise ValueError(
                "kill_processes must contain at least one process name "
                "when set; use None to skip the kill step")
        return self


class ReleaseFeedConfig(BaseModel):
    """Per-app generic JSON / text feed probe (Tier-A).

    The handler fetches ``url`` over HTTPS. When ``format="json"`` it parses
    the body as JSON and walks ``version_path`` (dotted, supports ``[N]``
    indices for lists). When ``format="text"`` it skips JSON parsing and
    matches ``version_regex`` against the raw body directly — required for
    vendors who publish key=value text feeds or Electron-builder YAML files
    (``latest.yml`` / ``latest-mac.yml`` / etc.).

    ``version_regex`` + ``version_replace`` are an XOR-validated pair: both
    must be set together or neither. When set, a single ``re.sub`` runs on
    the extracted (json) value or against the raw body (text). On no-match
    the handler falls back to the raw extracted string rather than erroring
    — vendor format changes degrade gracefully.
    """

    model_config = ConfigDict(extra="forbid")

    url: HttpsUrl
    version_path: Optional[Annotated[str, Field(
        min_length=1, max_length=256, pattern=r"^[A-Za-z0-9_.\-\[\]]+$"
    )]] = None
    format: Literal["json", "text"] = "json"
    version_regex: Optional[Annotated[str, Field(min_length=1, max_length=256)]] = None
    version_replace: Optional[Annotated[str, Field(min_length=0, max_length=256)]] = None
    http_timeout_s: Annotated[int, Field(ge=1, le=60)] = 8

    # Tier-A apply fields (mirror of GitHubReleaseConfig). The release_feed
    # handler's Tier-A apply resolves the download URL from the same JSON
    # body (or a sibling ``download_path``) and follows the identical
    # download -> verify -> install -> readback pipeline. All fields are
    # ignored unless the parent WebAppV1 has tier_a_apply=True.
    download_url: Optional[HttpsUrl] = None
    download_path: Optional[Annotated[str, Field(
        min_length=1, max_length=256, pattern=r"^[A-Za-z0-9_.\-\[\]]+$"
    )]] = None
    expected_publisher: Optional[Annotated[str, Field(
        min_length=1, max_length=256)]] = None
    silent_args: Optional[list[Annotated[str, Field(
        min_length=1, max_length=128)]]] = None
    installer_kind: Optional[Literal["exe", "msi"]] = None
    kill_processes: Optional[list[Annotated[str, Field(
        min_length=1, max_length=128, pattern=r"^[\w.\- ]+$")]]] = None
    display_name_pattern: Optional[Annotated[str, Field(
        min_length=1, max_length=256)]] = None

    @model_validator(mode="after")
    def _validate_combination(self) -> "ReleaseFeedConfig":
        # version_path required for json; ignored for text (regex matches body).
        if self.format == "json" and self.version_path is None:
            raise ValueError(
                "release_feed handler with format=json requires version_path")
        if self.format == "text" and self.version_regex is None:
            raise ValueError(
                "release_feed handler with format=text requires version_regex "
                "(the regex extracts the version from the raw body)")
        # XOR: both supplied or both absent.
        if (self.version_regex is None) != (self.version_replace is None):
            raise ValueError(
                "version_regex and version_replace must be supplied together "
                "(both set, or both absent)")
        # Compile-time validate the regex.
        if self.version_regex is not None:
            import re as _re
            try:
                _re.compile(self.version_regex)
            except _re.error as exc:
                raise ValueError(
                    f"version_regex is not a valid Python regex: {exc}")
        if self.display_name_pattern is not None:
            import re as _re
            try:
                _re.compile(self.display_name_pattern)
            except _re.error as exc:
                raise ValueError(
                    f"display_name_pattern is not a valid Python regex: {exc}")
        # download_url and download_path are mutually exclusive (XOR-ish):
        # download_url pins a static URL ignoring the feed body; download_path
        # walks the parsed JSON to extract one. Both set is ambiguous.
        if self.download_url is not None and self.download_path is not None:
            raise ValueError(
                "release_feed: download_url and download_path are mutually "
                "exclusive (use one or the other; download_path walks the "
                "JSON body, download_url is a static absolute URL)")
        if self.silent_args is not None and len(self.silent_args) == 0:
            raise ValueError(
                "silent_args must contain at least one argument when set; "
                "use None for auto-detection")
        if self.kill_processes is not None and len(self.kill_processes) == 0:
            raise ValueError(
                "kill_processes must contain at least one process name "
                "when set; use None to skip the kill step")
        return self


class BuiltinConfig(BaseModel):
    """Per-app builtin (Tier-B / manual) config.

    No version probe; on apply we ``Start-Process`` the configured ``url``
    so the operator can run the vendor's own update flow. Used for apps
    that auto-update internally (Discord, Slack, Zoom) and for apps whose
    update endpoints we haven't yet wired (Cursor's todesktop URL changed,
    GitHub Desktop's central.github.com endpoint 404s, etc.).
    """

    model_config = ConfigDict(extra="forbid")

    url: HttpsUrl


class WebAppV1(BaseModel):
    """One row in ``web_apps.toml``.

    Identity: ``slug`` is the stable cross-host id used in sidecar item ids
    (e.g. ``web:obsidian``); ``windows_uninstall_key`` is the exact name of
    the ``HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\X``
    or ``HKCU:\\...\\Uninstall\\X`` registry key (i.e. the bare GUID for MSI
    products, or the human-readable name for Inno/NSIS installers). The
    PowerShell side scans both hives to detect installed-ness and the
    installed version (``DisplayVersion`` value). When the registry key is
    not present, the app is considered "not installed on this host" and
    no item is emitted for it.

    Per-handler config is enforced via a model_validator: ``handler="github_release"``
    requires ``[apps.github_release]`` AND forbids ``[apps.release_feed]``
    or ``[apps.builtin]``, etc.
    """

    model_config = ConfigDict(extra="forbid")

    slug: Annotated[str, Field(pattern=r"^[a-z0-9-]+$",
                                min_length=1, max_length=64)]
    display_name: Annotated[str, Field(min_length=1, max_length=128)]
    handler: Literal["github_release", "release_feed", "builtin"]
    # The pattern intentionally allows the punctuation that appears in
    # real Windows registry DisplayNames -- ``Notepad++ (64-bit x64)``,
    # ``Mozilla Firefox (x64 en-US)``, ``Visual Studio Build Tools 2022``,
    # ``Microsoft 365``, etc. The Get-WebInstalledVersion helper uses
    # this string as a registry sub-key name OR matches it case-
    # insensitively against ``DisplayName`` values; neither path
    # evaluates the string in a shell context (T4 mitigation lives at
    # the elevation interface, not here). Forbidden chars are the ones
    # that ARE shell-meaningful (backtick, ``$``, ``;``, ``&``, ``|``,
    # ``<``, ``>``, ``"``, ``'``, newline, control chars) plus path
    # separators.
    windows_uninstall_key: Optional[Annotated[str, Field(
        min_length=1, max_length=256, pattern=r"^[^`$;&|<>\"'\\/\r\n\t\x00-\x1f]+$"
    )]] = None
    enabled: bool = True
    notes: Optional[str] = None

    # When True, apply.ps1 dispatches the handler's Tier-A "Real" apply
    # (download installer -> Authenticode verify -> silent execute ->
    # registry readback) instead of the v1 default Tier-B trigger-only
    # path (open vendor page in browser). Only meaningful when handler
    # is "github_release" or "release_feed" -- builtin is always Tier-B
    # by definition. False by default so existing operators keep their
    # current click-through behaviour until they explicitly opt in.
    tier_a_apply: bool = False

    # Per-handler sub-tables. Exactly one (matching ``handler``) must be set.
    github_release: Optional[GitHubReleaseConfig] = None
    release_feed: Optional[ReleaseFeedConfig] = None
    builtin: Optional[BuiltinConfig] = None

    @model_validator(mode="after")
    def _validate_handler_config(self) -> "WebAppV1":
        h = self.handler
        # Required-by-handler.
        if h == "github_release" and self.github_release is None:
            raise ValueError(
                f"slug={self.slug!r}: handler=github_release requires "
                f"[apps.github_release] sub-table")
        if h == "release_feed" and self.release_feed is None:
            raise ValueError(
                f"slug={self.slug!r}: handler=release_feed requires "
                f"[apps.release_feed] sub-table")
        if h == "builtin" and self.builtin is None:
            raise ValueError(
                f"slug={self.slug!r}: handler=builtin requires "
                f"[apps.builtin] sub-table")

        # XOR: other-handler sub-tables MUST NOT be set (catches typos +
        # accidental copy-paste from the wrong row).
        if h != "github_release" and self.github_release is not None:
            raise ValueError(
                f"slug={self.slug!r}: [apps.github_release] only valid "
                f"with handler=github_release; got handler={h!r}")
        if h != "release_feed" and self.release_feed is not None:
            raise ValueError(
                f"slug={self.slug!r}: [apps.release_feed] only valid "
                f"with handler=release_feed; got handler={h!r}")
        if h != "builtin" and self.builtin is not None:
            raise ValueError(
                f"slug={self.slug!r}: [apps.builtin] only valid "
                f"with handler=builtin; got handler={h!r}")

        # tier_a_apply only makes sense for Tier-A handlers. builtin has no
        # candidate version probe, so there's nothing to download; the only
        # path is opening the vendor URL.
        if self.tier_a_apply and h == "builtin":
            raise ValueError(
                f"slug={self.slug!r}: tier_a_apply=true is incompatible "
                f"with handler=builtin (builtin has no candidate version "
                f"probe; there's no installer to download). Set "
                f"tier_a_apply=false or change handler.")
        if self.tier_a_apply:
            # Tier-A apply needs a way to verify the install landed. Without
            # windows_uninstall_key we can't read DisplayVersion back.
            if not self.windows_uninstall_key:
                raise ValueError(
                    f"slug={self.slug!r}: tier_a_apply=true requires "
                    f"windows_uninstall_key (we read DisplayVersion from "
                    f"the Uninstall hive to confirm the install landed)")
        return self


class WebRegistryV2(BaseModel):
    """Top-level ``web_apps.toml`` shape.

    The class name keeps ``V2`` for parity with the macOS class even though
    the Windows schema is at v1 — there's no v0 to coerce from on Windows.
    The actual schema literal is ``ascendo-web-apps-windows/v1``.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_version: Literal["ascendo-web-apps-windows/v1"] = Field(alias="schema")
    apps: list[WebAppV1] = Field(default_factory=list, alias="app")

    @classmethod
    def load(
        cls,
        shipped: Path,
        user_override: Optional[Path] = None,
    ) -> "WebRegistryV2":
        shipped_data = cls._read_toml(shipped)
        registry = cls.model_validate(shipped_data)

        if user_override is not None and user_override.exists():
            user_data = cls._read_toml(user_override)
            user_reg = cls.model_validate(user_data)
            registry = merge_overrides(registry, user_reg)
        return registry

    @staticmethod
    def _read_toml(path: Path) -> dict:
        with path.open("rb") as fh:
            return tomllib.load(fh)

    def active_apps(self) -> list[WebAppV1]:
        return [a for a in self.apps if a.enabled]

    def find(self, slug: str) -> Optional[WebAppV1]:
        for app in self.active_apps():
            if app.slug == slug:
                return app
        return None


def merge_overrides(
    shipped: WebRegistryV2, user: WebRegistryV2,
) -> WebRegistryV2:
    """Merge user override into shipped registry. User entries replace
    shipped entries with the same ``slug``; new ``slug``s are appended.
    Returns a fresh ``WebRegistryV2`` (does not mutate inputs).
    """
    by_slug = {a.slug: a for a in shipped.apps}
    for ua in user.apps:
        by_slug[ua.slug] = ua
    return WebRegistryV2(
        schema="ascendo-web-apps-windows/v1",
        app=list(by_slug.values()),
    )
