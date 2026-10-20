"""Single source of truth for Ascendo version.

Bumped via `scripts/bump-version.sh`. All other locations (Tauri config,
pyproject.toml, packaging manifests) read from here at build time.

Versioning: SemVer 2.0.0 / PEP 440 (Python betas use the `bN` suffix)
- 0.x.y — pre-1.0, breaking changes possible
- 1.0.0b1 — first production beta (git tag `v1.0-beta`)
- 1.0.0 — stable API, semver promise
- 1.0.1 — in-app self-update (check + one-click upgrade)
- 1.0.2 — port macOS_updates hardening (brew, native CLIs, inventory)
- 1.0.3 — inventory alignment + last-run repairs
- 1.0.4 — lock web apps to macOS_updates channels
- 1.1.0 — port macOS_updates v1.4.2→v1.5.4 parity
- 1.1.1 — CI repair + macOS_updates v1.5.5 parity
- 1.5.5 — numer wyrównany z macOS_updates v1.5.5 (bez zmian łamiących)
"""

__version__ = "1.5.5"
