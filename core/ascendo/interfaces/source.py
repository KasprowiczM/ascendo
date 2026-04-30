"""ISource — package source / feed metadata + signature verification.

A "source" is a place where packages come from: an apt repository, the
winget community feed, a Homebrew tap, the Mac App Store, a GitHub
Releases page. Sources have:

- An identity (URL or canonical name).
- An optional signing key / certificate that we verify when installing.
- A trust tier (`trusted` for first-party, `community` for third-party
  but reviewed, `unverified` for anything else).

This interface is the **T2 + T3 mitigation** from the threat model
(compromised source / MITM): we centralize signature verification so
adapters can't accidentally skip it.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from ..models.host import HostInfo
from ..models.package import SourceType


class TrustTier(str, Enum):
    """How much we trust a source."""

    TRUSTED = "trusted"          # First-party (apt main, winget official)
    COMMUNITY = "community"      # Third-party but vetted (Homebrew tap, AUR-style)
    UNVERIFIED = "unverified"    # Anything else (raw URL, side-loaded)


class SourceMetadata(BaseModel):
    """Static metadata about a package source. Frozen."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    type: SourceType = Field(description="Source kind.")
    name: str = Field(description="Source name (e.g. 'ubuntu/noble/main', 'winget', 'homebrew/core').")
    url: str | None = Field(default=None, description="Canonical URL of the source.")
    trust: TrustTier = Field(description="Trust tier for this source.")
    signing_key_id: str | None = Field(
        default=None,
        description=(
            "GPG key ID / certificate thumbprint used by the source. "
            "Reader uses this to validate downloads."
        ),
    )


class ISource(ABC):
    """Source registry + signature verification interface."""

    @abstractmethod
    def list_known_sources(self, host: HostInfo) -> list[SourceMetadata]:
        """Enumerate all sources currently configured on this host.

        For apt: parses ``/etc/apt/sources.list`` + ``/etc/apt/sources.list.d/``.
        For winget: queries ``winget source list``.
        For brew: queries ``brew tap``.
        For Mac App Store: a single hard-coded entry.
        """

    @abstractmethod
    def get(self, host: HostInfo, source_type: SourceType, name: str) -> SourceMetadata | None:
        """Look up a single source by ``(type, name)``."""

    @abstractmethod
    def verify_signature(
        self,
        host: HostInfo,
        source: SourceMetadata,
        artifact_path: str,
        expected_sha256: str | None = None,
    ) -> bool:
        """Verify a downloaded artifact against the source's signing key.

        Args:
            host: Host context.
            source: Source the artifact came from.
            artifact_path: Local filesystem path to verify.
            expected_sha256: Optional checksum from the source's manifest
                (e.g. SHA256SUMS file). Must match exactly.

        Returns:
            True iff signature AND optional checksum both verify.

        Raises:
            SourceVerificationError: signing key not available for a
                ``trust=trusted`` source. Trusted sources MUST have keys.
        """


class SourceVerificationError(RuntimeError):
    """Signature verification failed or signing key was missing."""
