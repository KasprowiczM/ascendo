"""ascendo-windows — Tier 1 adapter implementing core interfaces for Windows.

Implements:
- WingetPackageManager(IPackageManager)
- MsStorePackageManager(IPackageManager)
- PSWindowsUpdateOSUpdater(IOSUpdater)
- TaskSchedulerScheduler(IScheduler) — Windows schtasks.exe
- VolumeShadowCopySnapshot(ISnapshot) — Windows VSS
- WindowsUACElevation(IElevation) — UAC + ShellExecute "runas"
- AuthenticodeVerifier(ICodeSigningVerifier) — Get-AuthenticodeSignature
- WindowsHostInspector — registry, AppX, MSIX manifest version evidence

Each class invokes `scripts/<category>/<phase>.ps1` via subprocess and parses
the resulting JSON v1 sidecar.

Package implementation arrives in M2-M3.
"""
