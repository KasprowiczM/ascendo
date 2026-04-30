"""Phase runner — dispatches phase scripts and processes JSON sidecars.

Components:
- runner.PhaseRunner — invokes adapter scripts with standard flags
  (--run-id, --json-out, --log, --profile, --config, --dry-run)
- lock.AscendoLock — cross-OS process lock (fcntl on POSIX, msvcrt on Windows)
- exec.subprocess_safe — subprocess wrapper with timeout, encoding, JSON parse
- retry.exponential_backoff — retry policy for flaky adapters

Implemented in M2 (Core skeleton).
"""
