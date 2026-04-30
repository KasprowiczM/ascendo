"""ascendo-ubuntu — Tier 1 adapter implementing core interfaces for Linux.

Implements:
- AptPackageManager(IPackageManager)
- SnapPackageManager(IPackageManager)
- FlatpakPackageManager(IPackageManager)
- LinuxbrewPackageManager(IPackageManager)
- FwupdDriverProvider(IDriverProvider)
- NvidiaDriverProvider(IDriverProvider) — Linux NVIDIA via apt
- SystemdScheduler(IScheduler)
- TimeshiftSnapshot(ISnapshot)
- EtckeeperSnapshot(ISnapshot)  # fallback
- LinuxSudoElevation(IElevation)
- AptUpdater(IOSUpdater) — apt full-upgrade
- DpkgSigVerifier(ICodeSigningVerifier)

Each class invokes `scripts/<category>/<phase>.sh` via subprocess and parses
the resulting JSON v1 sidecar.

Package implementation arrives in M2-M3.
"""
