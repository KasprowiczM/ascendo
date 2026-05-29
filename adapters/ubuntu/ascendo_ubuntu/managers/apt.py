"""AptManager - IPackageManager wrapping ``scripts/apt/<phase>.sh``."""
from __future__ import annotations

import logging
import os
import subprocess
from collections.abc import Iterable
from typing import ClassVar

from ascendo.interfaces.package_manager import ManagerError
from ascendo.models.host import HostInfo
from ascendo.models.package import SourceType
from ascendo.models.run import Phase, RunInfo
from ascendo.models.sidecar import Sidecar

from ._base import BashPhaseManager

_log = logging.getLogger(__name__)


class AptManager(BashPhaseManager):
    """Advanced Package Tool (system packages on Debian/Ubuntu).

    Bridges to the legacy 5 phase scripts under top-level ``scripts/apt/``.
    """

    CATEGORY: ClassVar[SourceType] = SourceType.APT
    SCRIPT_DIR: ClassVar[str] = "apt"
    DISPLAY_NAME: ClassVar[str] = "Advanced Package Tool (apt)"
    AVAILABILITY_BINARIES: ClassVar[tuple[str, ...]] = ("apt-get", "apt")

    def run_phase(
        self,
        phase: Phase,
        run: RunInfo,
        host: HostInfo,
        *,
        item_filter: Iterable[str] | None = None,
    ) -> Sidecar:
        if phase == Phase.APPLY and not run.dry_run:
            self._verify_apt_signatures(host)
        return super().run_phase(phase, run, host, item_filter=item_filter)

    def _verify_apt_signatures(self, host: HostInfo) -> None:
        """P1 verification: extract hashes, dry-run download, and verify."""
        try:
            res = subprocess.run(
                ["apt-get", "--print-uris", "-y", "upgrade"],
                capture_output=True, text=True, check=False,
            )
        except OSError as e:
            _log.warning(f"Failed to run apt-get --print-uris: {e}")
            return
            
        expected_hashes: dict[str, str] = {}
        for line in res.stdout.splitlines():
            line = line.strip()
            if not line.startswith("'"):
                continue
            parts = line.split()
            if len(parts) >= 4 and parts[-1].startswith("SHA256:"):
                filename = parts[1]
                sha = parts[-1].split(":", 1)[1]
                expected_hashes[filename] = sha
                
        if not expected_hashes:
            return

        env = dict(os.environ)
        if self._elevation is not None:
            build_env = getattr(self._elevation, "build_subprocess_env", None)
            if callable(build_env):
                env = dict(build_env())
        env.setdefault("DEBIAN_FRONTEND", "noninteractive")
        env.setdefault("APT_LISTCHANGES_FRONTEND", "none")

        _log.info(f"Downloading {len(expected_hashes)} packages for pre-verification...")
        try:
            dl_res = subprocess.run(
                ["sudo", "-E", "apt-get", "--download-only", "-y", "upgrade"],
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            if dl_res.returncode != 0:
                _log.warning(f"apt-get download-only failed: {dl_res.stderr}")
        except OSError as e:
            _log.warning(f"Failed to run apt-get download-only: {e}")

        # Now verify them
        from .source import UbuntuSource
        from ascendo.interfaces.source import SourceMetadata, TrustTier
        
        source_manager = UbuntuSource()
        dummy_source = SourceMetadata(type=SourceType.APT, name="apt-upgrade", trust=TrustTier.TRUSTED)
        
        cache_dir = "/var/cache/apt/archives"
        for filename, expected_sha in expected_hashes.items():
            path = os.path.join(cache_dir, filename)
            if not os.path.exists(path):
                _log.warning(f"Package {filename} not found in cache for verification.")
                continue
            try:
                valid = source_manager.verify_signature(
                    host, dummy_source, path, expected_sha256=expected_sha
                )
                if not valid:
                    raise ManagerError(f"Signature/hash verification failed for {filename}")
            except Exception as e:
                raise ManagerError(f"Source verification error for {filename}: {e}")
