#!/usr/bin/env bash
# =============================================================================
# adapters/ubuntu/lib/web_discovery.sh — Linux web-app discovery
# =============================================================================
# Walks well-known AppImage / vendor-binary directories and emits one JSON
# line per discovered app:
#
#   {"app_id": "...", "path": "...", "version": "...",
#    "handler_hint": "appimage|opt_binary", "owned_by": "user|apt:<pkg>"}
#
# Test seam: ASCENDO_WEB_APPS_ROOT (default $HOME) — root directory whose
# children are scanned for AppImages.
#
# Output format: NDJSON (one JSON object per line). Empty output is a
# valid result (host has no web apps installed).
#
# Exit code: always 0. Errors writing to stderr (warnings only).
# =============================================================================
set -o pipefail

ROOT="${ASCENDO_WEB_APPS_ROOT:-$HOME}"

# Candidate directories where AppImages typically live (relative to ROOT
# unless absolute). Read-only scanning; no mutation.
APPIMAGE_DIRS=(
    "$ROOT/Applications"
    "$ROOT/Apps"
    "$ROOT/.local/share/AppImages"
    "$ROOT/Applications/AppImages"
    "$ROOT/Downloads/AppImages"
)

# Candidate /opt-style vendor binary directories. We only check existence
# + dpkg ownership; we do NOT walk these recursively (too many false
# positives). Each entry is "<path>:<app_id>".
OPT_BINARIES=(
    "/opt/brave.com/brave/brave:brave"
    "/opt/google/chrome/chrome:chrome"
    "/opt/megasync/lib/MEGAsync:megasync"
    "/opt/cursor/cursor:cursor"
    "/opt/Obsidian/obsidian:obsidian"
    "/opt/Joplin/joplin:joplin"
)

# _appimage_id <basename>
# Derive a stable app_id from an AppImage filename. Strips version
# suffixes like "-1.5.3" and the .AppImage extension. Lowercases.
_appimage_id() {
    local base="$1"
    # strip .AppImage extension
    local stem="${base%.AppImage}"
    # strip trailing version like -1.2.3 or -v1.2.3 or _1.2.3
    stem=$(printf '%s' "$stem" | sed -E 's/[-_]v?[0-9][0-9.]*([+-][A-Za-z0-9.]+)?$//')
    # collapse non-alnum to dash, lowercase
    printf '%s' "$stem" | tr '[:upper:]' '[:lower:]' | sed -E 's/[^a-z0-9]+/-/g; s/^-+|-+$//g'
}

# _appimage_version <path>
# Try to read the embedded version. Returns empty string if not supported.
_appimage_version() {
    local path="$1"
    [ -x "$path" ] || return 0
    local v
    # 5 second timeout — some AppImages spin up the whole app on --version
    v=$(timeout 5 "$path" --appimage-version 2>/dev/null) || true
    if [ -n "$v" ]; then
        # Strip whitespace + leading 'v'
        printf '%s' "$v" | head -1 | sed -E 's/^v//; s/[[:space:]]+$//'
        return 0
    fi
    # Some AppImages support --version directly
    v=$(timeout 5 "$path" --version 2>/dev/null | head -1) || true
    if [ -n "$v" ]; then
        # Common shape: "AppName 1.2.3" — pick the version-like token
        printf '%s' "$v" | grep -oE '[0-9]+\.[0-9]+(\.[0-9]+)?' | head -1
        return 0
    fi
    printf ''
}

# _dpkg_owner <path>
# Echo "apt:<pkg>" if dpkg owns the path; empty otherwise.
_dpkg_owner() {
    local path="$1"
    command -v dpkg >/dev/null 2>&1 || { printf ''; return 0; }
    local out
    out=$(dpkg -S "$path" 2>/dev/null | head -1)
    if [ -n "$out" ]; then
        # Format: "package-name: /path/to/file"
        local pkg="${out%%:*}"
        printf 'apt:%s' "$pkg"
    fi
}

# _emit <app_id> <path> <version> <handler_hint> <owned_by> [display_name]
_emit() {
    local app_id="$1" path="$2" version="$3" hh="$4" owned="$5" disp="${6:-}"
    APP_ID="$app_id" P="$path" V="$version" HH="$hh" OWNED="$owned" DISP="$disp" \
    python3 -c '
import json, os
out = {
    "app_id":           os.environ["APP_ID"],
    "path":             os.environ["P"],
    "version":          os.environ["V"],
    "handler_hint":     os.environ["HH"],
    "owned_by":         os.environ["OWNED"],
}
if os.environ["DISP"]:
    out["display_name"] = os.environ["DISP"]
print(json.dumps(out))
'
}

# ── Pass 1: walk AppImage dirs ────────────────────────────────────────────
for d in "${APPIMAGE_DIRS[@]}"; do
    [ -d "$d" ] || continue
    # Find at depth 1 only — don't recurse into subdirectories (avoids
    # picking up bundled AppImages inside other apps).
    while IFS= read -r ai; do
        [ -z "$ai" ] && continue
        base=$(basename "$ai")
        app_id=$(_appimage_id "$base")
        [ -z "$app_id" ] && continue
        ver=$(_appimage_version "$ai")
        owned=$(_dpkg_owner "$ai")
        [ -z "$owned" ] && owned="user"
        _emit "$app_id" "$ai" "$ver" "appimage" "$owned" "$base"
    done < <(find "$d" -maxdepth 2 -type f -iname '*.AppImage' 2>/dev/null)
done

# ── Pass 2: known /opt binaries ───────────────────────────────────────────
for entry in "${OPT_BINARIES[@]}"; do
    path="${entry%%:*}"
    app_id="${entry##*:}"
    [ -e "$path" ] || continue
    owned=$(_dpkg_owner "$path")
    [ -z "$owned" ] && owned="user"
    # Try to read version from a binary's --version output
    ver=""
    if [ -x "$path" ]; then
        ver=$(timeout 3 "$path" --version 2>/dev/null | head -1 \
              | grep -oE '[0-9]+\.[0-9]+(\.[0-9]+)?' | head -1) || true
    fi
    _emit "$app_id" "$path" "$ver" "opt_binary" "$owned"
done

exit 0
