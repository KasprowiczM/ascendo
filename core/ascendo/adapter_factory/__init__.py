"""OS detection + adapter selection.

- detector.detect_os() → "linux" | "windows" | "macos"
- factory.get_package_manager(os, name) → IPackageManager implementation
- factory.get_scheduler(os) → IScheduler implementation
- factory.get_snapshot_provider(os) → ISnapshot implementation
- factory.get_elevation(os) → IElevation implementation

Implementation in M2.
"""
