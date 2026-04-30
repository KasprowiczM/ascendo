"""Elevation abstraction — sudo (POSIX) / UAC (Windows).

Per-OS implementations:
- linux_sudo.LinuxSudoElevation — sudo + askpass + cache (port from existing bash)
- macos_sudo.MacOSSudoElevation — sudo + osascript prompt fallback
- windows_uac.WindowsUACElevation — ShellExecute "runas" + manifest detection

All implement IElevation:
- is_elevated() → bool
- request_elevation(reason) → Token
- run_elevated(cmd_args, token) → ProcessResult
- cache_status() → CacheStatus

CRITICAL: cmd_args is always a list (never shell string concat). Each adapter
maintains its own allowlist of permitted elevated commands (defense in depth).
"""
