"""Cross-cutting utilities used by core and adapters.

- hashing.py — sha256_file, files_match (size + hash compare)
- paths.py — XDG/AppData/Library/Application Support resolver per OS
- tempdir.py — mktemp + cleanup trap (port from macOS lesson)
- encoding.py — UTF-8 console enforcement, ellipsis-safe parsing
- json_emit.py — Python helper for JSON v1 sidecar (called from bash/PS via subprocess)

These are explicitly cross-OS. No OS-specific imports allowed here.
"""
