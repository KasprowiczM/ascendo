"""UTF-8 structured logging with cross-OS file rotation (60-day default).

- factory.py — get_logger(name) returns configured Logger instance
- formatters.py — human-readable + JSON (line-based, for log aggregation)
- rotation.py — daily rotation, 60-day retention (configurable)

ALL log files: UTF-8 no BOM. ALL log paths: pathlib.Path.

Special handling for PowerShell encoding traps:
- Console output forced to UTF-8 (prevents `…` ellipsis 3-byte mis-decode)
- File writes use explicit `[System.IO.File]::AppendAllText(..., UTF8)` from PS lib
"""
