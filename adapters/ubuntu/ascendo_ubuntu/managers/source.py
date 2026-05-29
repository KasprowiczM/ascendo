"""UbuntuSource - implements ISource for Ubuntu."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

from ascendo.interfaces.source import ISource, SourceMetadata, SourceVerificationError, TrustTier
from ascendo.models.host import HostInfo
from ascendo.models.package import SourceType


class UbuntuSource(ISource):
    """Source registry for Ubuntu/Debian.
    
    Parses apt sources and provides hash-based package verification
    anchored by apt's repository GPG validation.
    """

    def list_known_sources(self, host: HostInfo) -> list[SourceMetadata]:
        """Parse apt sources.list and sources.list.d."""
        sources: list[SourceMetadata] = []
        
        # We perform a basic parse of one-line style and deb822 style.
        # This is a best-effort enumeration for the T2 threat model.
        paths = [Path("/etc/apt/sources.list")]
        sources_dir = Path("/etc/apt/sources.list.d")
        if sources_dir.is_dir():
            paths.extend(sources_dir.glob("*.list"))
            paths.extend(sources_dir.glob("*.sources"))
            
        for path in paths:
            if not path.is_file():
                continue
            try:
                content = path.read_text(encoding="utf-8")
                for line in content.splitlines():
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                        
                    # Basic one-line deb format
                    if line.startswith("deb "):
                        # deb [arch=amd64 signed-by=/key] http://url noble main
                        parts = line.split()
                        url = next((p for p in parts if p.startswith("http")), None)
                        name = path.name
                        trust = TrustTier.TRUSTED if url and "ubuntu.com" in url else TrustTier.COMMUNITY
                        sources.append(
                            SourceMetadata(
                                type=SourceType.APT,
                                name=name,
                                url=url,
                                trust=trust,
                            )
                        )
                    # Basic deb822 format (just looking for URIs:)
                    elif line.startswith("URIs:"):
                        url = line.split(":", 1)[1].strip()
                        name = path.name
                        trust = TrustTier.TRUSTED if "ubuntu.com" in url else TrustTier.COMMUNITY
                        sources.append(
                            SourceMetadata(
                                type=SourceType.APT,
                                name=name,
                                url=url,
                                trust=trust,
                            )
                        )
            except OSError:
                pass
                
        # Deduplicate by name and URL
        seen = set()
        unique_sources = []
        for s in sources:
            key = (s.name, s.url)
            if key not in seen:
                seen.add(key)
                unique_sources.append(s)
                
        return unique_sources

    def get(self, host: HostInfo, source_type: SourceType, name: str) -> SourceMetadata | None:
        """Look up a single source."""
        for s in self.list_known_sources(host):
            if s.type == source_type and s.name == name:
                return s
        return None

    def verify_signature(
        self,
        host: HostInfo,
        source: SourceMetadata,
        artifact_path: str,
        expected_sha256: str | None = None,
    ) -> bool:
        """Verify an APT package by checking its hash.
        
        Standard Debian packages (.deb) are not individually signed with GPG.
        Instead, their hashes are verified against the signed repository manifest
        (Release/InRelease). Since `apt-get` verifies the repository GPG keys,
        our layer 5 validation asserts that the downloaded .deb file exactly
        matches the expected hash from the signed manifest.
        
        Args:
            host: Host context.
            source: Source the artifact came from.
            artifact_path: Path to the downloaded .deb file.
            expected_sha256: Hash extracted from `apt-get --print-uris`.
            
        Returns:
            True if the SHA-256 matches exactly.
            
        Raises:
            SourceVerificationError: If expected_sha256 is missing, because
                APT packages cannot be verified without it.
        """
        if source.type != SourceType.APT:
            return False
            
        if not expected_sha256:
            raise SourceVerificationError(
                "APT packages cannot be verified without an expected SHA256 "
                "checksum from a GPG-signed repository manifest."
            )
            
        if not os.path.exists(artifact_path):
            return False

        h = hashlib.sha256()
        try:
            with open(artifact_path, "rb") as f:
                for chunk in iter(lambda: f.read(4096 * 1024), b""):
                    h.update(chunk)
        except OSError:
            return False
            
        return h.hexdigest().lower() == expected_sha256.lower()
