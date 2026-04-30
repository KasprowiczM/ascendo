"""Single source of truth for Ascendo version.

Bumped via `scripts/bump-version.sh`. All other locations (Tauri config,
pyproject.toml, packaging manifests) read from here at build time.

Versioning: SemVer 2.0.0
- 0.x.y — pre-1.0, breaking changes possible
- 1.0.0 — stable API, semver promise
"""

__version__ = "0.0.1-dev"
