"""Cross-OS system snapshot facade.

Per-OS implementations:
- timeshift.TimeshiftSnapshot — Linux primary (rsync mode)
- etckeeper.EtckeeperSnapshot — Linux fallback (etc/ only)
- timemachine.TimeMachineSnapshot — macOS (READ-ONLY: list only, can't create via API)
- vss_snapshot.VolumeShadowCopySnapshot — Windows (VSS via wmic)
- manual.ManualSnapshot — cross-OS fallback (copy critical configs to ~/.ascendo/snapshots/)

All implement ISnapshot.

Note: macOS Time Machine snapshots cannot be created by third-party tools
through public API (Apple restriction). On macOS we list available local
snapshots via `tmutil listlocalsnapshots /` and rely on system auto-snaps.
"""
