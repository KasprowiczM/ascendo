"""Single source of truth for Ascendo version.

Bumped via `scripts/bump-version.sh`. All other locations (Tauri config,
pyproject.toml, packaging manifests) read from here at build time.

Versioning: SemVer 2.0.0 / PEP 440 (Python betas use the `bN` suffix)
- 0.x.y — pre-1.0, breaking changes possible
- 1.0.0b1 — first production beta (git tag `v1.0-beta`)
- 1.0.0 — stable API, semver promise
- 1.0.1 — in-app self-update (check + one-click upgrade)
- 1.0.2 — port macOS_updates hardening (brew, native CLIs, inventory)
"""

__version__ = "1.0.2"
