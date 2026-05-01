"""Package + source descriptors used in sidecar `items[]`.

A `Package` is the user-facing identity of a thing that can be installed,
upgraded, or removed (e.g. `firefox`, `Microsoft.VisualStudioCode`,
`com.docker.docker`). The same package may be available from multiple
sources (apt + snap, Homebrew + Mac App Store, winget + msstore) — those
are different `ItemSource` instances pointing at the same `Package`.

`ItemEvidence` carries proof of the actual installed version, used by
the unknown-version suppression logic ported from PowerShell. `ItemRollback`
records how a successful apply could be reverted.
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints


class SourceType(str, Enum):
    """Kind of package source / feed.

    `web` covers everything that's not a real package manager — direct
    DMG downloads, GitHub Releases, manual installer URLs. The plugin
    system is built around `web` for ad-hoc installers.
    """

    APT = "apt"
    SNAP = "snap"
    FLATPAK = "flatpak"
    BREW_FORMULA = "brew_formula"
    BREW_CASK = "brew_cask"
    WINGET = "winget"
    MSSTORE = "msstore"
    APPX = "appx"          # Windows AppX/MSIX installed but not via msstore
    REGISTRY_ARP = "registry_arp"   # Windows Add/Remove Programs entry
    MAC_APP_STORE = "mac_app_store"
    NPM = "npm"
    PIP = "pip"
    PIPX = "pipx"
    WEB = "web"
    PLUGIN = "plugin"      # plugin-supplied source
    UNKNOWN = "unknown"


# Free-form identifier — what the source uses internally to refer to the
# package. Examples: 'firefox', 'Microsoft.PowerShell', 'com.brave.Browser',
# or full MSIX package family names like
# 'AutoHotkey.AutoHotkey_2.0.24.0_x64__1234abcd5678'.
#
# NB: NO upper length cap. We previously capped this at 512 / 2048 chars
# but real-world output from `winget list` on machines with many AppX/MSIX
# packages can produce parser-merged rows that exceed any sane cap. An
# arbitrary cap aborts the entire phase; we'd rather let the weird row
# through as visible data and fix the parser side. Min-length 1 still
# rejects truly-empty IDs.
PackageId = Annotated[str, StringConstraints(min_length=1, strip_whitespace=True)]

# Free-form version string. We deliberately keep this as plain `str` rather
# than parsed (e.g. PEP 440 / SemVer) — package managers use wildly
# different version conventions and we should not lose information by
# normalising too early. Comparison is delegated to per-source helpers.
# Same rationale as PackageId for not capping length.
VersionStr = Annotated[str, StringConstraints(min_length=1)]


class Package(BaseModel):
    """A package identity.

    Two packages are the *same package* if they share `category` and `id`.
    Different `current_version` / `target_version` / `source.feed` are
    instance-level, not identity.
    """

    model_config = ConfigDict(extra="forbid")

    id: PackageId = Field(
        description="Source-specific identifier. e.g. 'firefox' (apt), 'Microsoft.PowerShell' (winget).",
    )
    name: str = Field(
        description="Human-readable display name. Often equal to id but may differ (e.g. 'PowerShell' vs 'Microsoft.PowerShell').",
    )
    category: SourceType = Field(
        description="Source/feed type. Drives which adapter handles this package.",
    )


class ItemSource(BaseModel):
    """The source/feed an item was discovered through.

    Distinct from `Package.category`: a package can have multiple sources
    (apt + snap), and `ItemSource.feed` may be more specific than the
    category (e.g. category='apt', feed='ubuntu/noble/main' or 'PPA:ondrej/php').
    """

    model_config = ConfigDict(extra="forbid")

    type: SourceType = Field(description="Source type.")
    feed: str | None = Field(
        default=None,
        description=(
            "Optional sub-source identifier. apt: repository name. "
            "winget: source name (winget/msstore). brew: tap name."
        ),
    )
    url: str | None = Field(
        default=None,
        description=(
            "Optional canonical URL. For 'web' sources this is the install "
            "page or download URL. For repository-based sources this is the "
            "repository URL where applicable."
        ),
    )


class ItemEvidence(BaseModel):
    """Local-version evidence for unknown-version suppression.

    Some package managers (notably winget on certain MSIX packages)
    return `Version = Unknown` even though the local install has a
    parseable version. The PowerShell adapter records what it can find
    via AppX manifest, registry ARP, dpkg, etc. — and the orchestrator
    suppresses repeat upgrade offers until the source actually advertises
    a newer `Available` version.

    All fields are optional; presence indicates the corresponding evidence
    was actually gathered.
    """

    model_config = ConfigDict(extra="forbid")

    appx_version: VersionStr | None = Field(
        default=None,
        description="Version from AppX/MSIX package manifest (Windows).",
    )
    registry_version: VersionStr | None = Field(
        default=None,
        description="Version from Windows registry Add/Remove Programs entry.",
    )
    dpkg_version: VersionStr | None = Field(
        default=None,
        description="Version from `dpkg -s <pkg>` output (Debian/Ubuntu).",
    )
    binary_version: VersionStr | None = Field(
        default=None,
        description="Version output of `<binary> --version` if reproducible.",
    )
    binary_path: str | None = Field(
        default=None,
        description="Absolute path to the resolved binary (for native installers).",
    )
    binary_sha256: str | None = Field(
        default=None,
        description="SHA-256 hex of the binary, when checksum verification was performed.",
    )
    note: str | None = Field(
        default=None,
        description="Free-form note explaining why this evidence was gathered.",
    )


class ItemRollback(BaseModel):
    """How to undo a successful apply on this item.

    Per ADR + threat-model: every apply that succeeds must record a
    rollback hint, even if rollback is implemented via system snapshot
    (Time Machine / VSS / timeshift) rather than per-package downgrade.
    """

    model_config = ConfigDict(extra="forbid")

    available: bool = Field(
        description="False means rollback is not possible without a system snapshot.",
    )
    method: str | None = Field(
        default=None,
        description=(
            "Concrete rollback command, if available. "
            "Example: 'winget install --id Microsoft.PowerShell --version 7.6.0' "
            "or 'apt install firefox=128.0+build1-0ubuntu0.24.04.1'."
        ),
    )
    snapshot_id: str | None = Field(
        default=None,
        description=(
            "ID of the pre-apply system snapshot (timeshift snapshot name, "
            "VSS shadow copy GUID, Time Machine timestamp). When set, rollback "
            "via snapshot is the recommended path."
        ),
    )
    instructions_path: str | None = Field(
        default=None,
        description=(
            "Path to a generated markdown file containing human-readable "
            "rollback instructions. Default: ~/.ascendo/rollback/<run-id>.md."
        ),
    )
