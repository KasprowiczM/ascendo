"""Package classification — outdated / current / missing / unknown.

- classifier.py — given installed list + tracked list + available list, classify
- version_compare.py — SemVer + PEP 440 + dpkg + winget version normalizers
- evidence.py — local version evidence (registry, AppX, MSIX, brew info, dpkg-query)

Used by dashboard (`/apps/detect` endpoint) and CLI (`ascendo apps detect`).
"""
