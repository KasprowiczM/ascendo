"""ascendo-macos — Tier 1 adapter implementing core interfaces for macOS.

**Status: deferred to Milestone M5.** Skeleton package for forward-compat.

Will implement:
- SoftwareUpdateOSUpdater(IOSUpdater) — CRITICAL `-R` flag enforced
- MasPackageManager(IPackageManager) — `sudo mas upgrade` (CVE-2025-43411)
- BrewPackageManager(IPackageManager) — Homebrew formulas + casks
- InternetAppsPackageManager(IPackageManager) — DMG/Keystone/Sparkle/URL handlers
- LaunchdScheduler(IScheduler) — launchd plists in ~/Library/LaunchAgents/
- TimeMachineSnapshot(ISnapshot) — READ-ONLY (Apple API restriction)
- MacOSSudoElevation(IElevation) — sudo + osascript prompt fallback
- SpctlVerifier(ICodeSigningVerifier) — spctl --assess + pkgutil --check-signature

Implementation arrives in M5.
"""
