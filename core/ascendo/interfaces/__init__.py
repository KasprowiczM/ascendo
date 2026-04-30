"""Abstract interfaces (Protocol-based) that adapters implement.

These are the architectural firewall: core depends ONLY on these protocols,
never on concrete adapter implementations.

Interfaces:
- IPackageManager — install/upgrade/remove packages (apt, winget, brew, ...)
- IPackageSource — resolve, fetch, validate packages (signature checks)
- IDriverProvider — driver/firmware updates (fwupd, dcu-cli, NVIDIA)
- IScheduler — schedule periodic runs (systemd, launchd, Task Scheduler)
- ISnapshot — system state snapshots (timeshift, Time Machine, VSS)
- IPluginLoader — discover, validate, load plugins
- IDevSync — overlay management (GitHub + cloud provider)
- INotifier — info/warn/error/progress to user
- IElevation — sudo (POSIX) / UAC (Windows) abstraction
- IOSUpdater — OS-level updates (apt full-upgrade, softwareupdate, PSWindowsUpdate)
- ICodeSigningVerifier — Authenticode (Win), spctl (mac), GPG (Linux)

See `docs/architecture/0005-six-layer-architecture.md` for layering rules.
"""

# Stub — real interfaces are added in M2 (Core skeleton milestone).
# See HANDOFF.md "Next Steps" for current state.
