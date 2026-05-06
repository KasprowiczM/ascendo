#!/usr/bin/env bash
# =============================================================================
# adapters/macos/lib/ascendo_web.sh -- shared helpers for WebManager
# =============================================================================
# Sourced by adapters/macos/scripts/web/{check,plan,apply,verify,cleanup}.sh
# and lib/handlers/*.sh. Bash 3.2 compatible (no `local -A`, no mapfile,
# no readarray). Helpers do NOT call `set -e` themselves -- they're sourced
# and inherit shell options from their caller.
#
# Reserved exit codes (web-namespace):
#   20 download failure
#   21 hdiutil attach failed (couldn't determine mount point)
#   22 mount contained no .app bundle
#   23 spctl Gatekeeper assessment rejected the bundle
#   24 cp -R into /Applications failed even with sudo -A escalation
#   29 generic web-handler failure (reserved; not used here)
#
# Public helpers (all `_web_*` to signal private-ish, package-internal):
#   _web_ensure_cache_dir
#   _web_installed_version <app_path>
#   _version_gt <a> <b>
#   _web_is_running <bundle_id>
#   _web_extract_sparkle_latest_version          (reads stdin)
#   _web_extract_sparkle_enclosure_url           (reads stdin)
#   _web_download <url> <dest>
#   _web_verify_signature <app_path>
#   _web_install_dmg <slug> <dmg_url> <app_path>
#   _web_run_apply_cli <slug> <argv_json>
# =============================================================================
# shellcheck shell=bash

# ============================================================
# Cache directory
# ============================================================

if [ -z "${ASCENDO_WEB_CACHE_DIR:-}" ]; then
    ASCENDO_WEB_CACHE_DIR="${HOME}/Library/Caches/Ascendo/web"
fi
export ASCENDO_WEB_CACHE_DIR

_web_ensure_cache_dir() {
    mkdir -p "$ASCENDO_WEB_CACHE_DIR" 2>/dev/null || return 1
}

# ============================================================
# Version probes
# ============================================================

# _web_installed_version <app_path>
# Echoes CFBundleShortVersionString from the bundle's Info.plist, or empty
# when the bundle is missing / lacks the key. Exit 0 in both cases (missing
# is a valid "not installed" signal, not an error).
_web_installed_version() {
    local app_path="$1"
    if [ ! -d "$app_path" ]; then
        return 0
    fi
    /usr/bin/defaults read "$app_path/Contents/Info" \
        CFBundleShortVersionString 2>/dev/null || true
}

# _version_gt <a> <b>
# Exit 0 iff a > b (strict). Compares dotted version strings via sort -V.
# Equal versions return 1 (not strictly greater).
_version_gt() {
    local a="$1" b="$2"
    [ "$a" = "$b" ] && return 1
    local higher
    higher=$(printf '%s\n%s\n' "$a" "$b" | sort -V | tail -n 1)
    [ "$higher" = "$a" ]
}

# ============================================================
# Process probes
# ============================================================

# _web_is_running <bundle_id>
# Exit 0 iff a running app with this bundle id is registered with
# LaunchServices. Uses `lsappinfo` (the canonical macOS query tool for
# running GUI apps) so we don't need to inspect ps output (which would
# match any caller that happens to carry the bundle id as a literal in
# its command line, e.g. the helper-test fixture).
_web_is_running() {
    local bundle_id="$1"
    if ! command -v lsappinfo >/dev/null 2>&1; then
        return 1
    fi
    # Match `bundleID="<id>"` exactly (lsappinfo's per-app dump format) so
    # we don't false-positive on substring overlaps.
    local matches
    matches=$(lsappinfo list 2>/dev/null \
        | /usr/bin/grep -c "bundleID=\"$bundle_id\"")
    if [ "${matches:-0}" -gt 0 ]; then
        return 0
    fi
    return 1
}

# ============================================================
# Sparkle appcast parsing
# ============================================================

# _web_extract_sparkle_latest_version
# Reads stdin (appcast XML), echoes the FIRST sparkle:shortVersionString.
# Sparkle convention puts the latest item first.
_web_extract_sparkle_latest_version() {
    /usr/bin/grep -oE 'sparkle:shortVersionString="[^"]*"' \
        | /usr/bin/head -n 1 \
        | /usr/bin/sed -E 's/sparkle:shortVersionString="([^"]*)"/\1/'
}

# _web_extract_sparkle_enclosure_url
# Reads stdin, echoes the FIRST <enclosure url="..."> URL.
_web_extract_sparkle_enclosure_url() {
    /usr/bin/grep -oE 'url="https?://[^"]+"' \
        | /usr/bin/head -n 1 \
        | /usr/bin/sed -E 's/url="([^"]+)"/\1/'
}

# ============================================================
# Download + verify + install (DMG)
# ============================================================

# _web_download <url> <dest>
# Curl with progress streamed via _stream_emit when available (the helper
# from ascendo_json.sh is loaded by Run Center context; bare scripts won't
# have it, hence the command -v guard).
_web_download() {
    local url="$1" dest="$2"
    _web_ensure_cache_dir || return 1
    if command -v _stream_emit >/dev/null 2>&1; then
        _stream_emit info "downloading $url"
    fi
    /usr/bin/curl -fsSL --max-time 300 -o "$dest" "$url"
}

# _web_verify_signature <app_path>
# Exit 0 iff Gatekeeper accepts the bundle (notarised + signed). Stdout +
# stderr captured to caller for diagnostics.
_web_verify_signature() {
    local app_path="$1"
    /usr/sbin/spctl --assess --type execute --verbose "$app_path" 2>&1
}

# _web_install_dmg <slug> <dmg_url> <app_path>
# Full pipeline: download -> mount -> spctl -> cp -R -> xattr strip -> unmount.
# Re-tries cp -R with sudo -A on EACCES (askpass-cached elevation, the same
# pattern used by mas + softwareupdate apply scripts).
_web_install_dmg() {
    local slug="$1" url="$2" app_path="$3"
    local dmg="$ASCENDO_WEB_CACHE_DIR/${slug}.dmg"
    local mount_point=""
    local rc=0

    _web_download "$url" "$dmg" || return 20

    mount_point=$(/usr/bin/hdiutil attach -nobrowse -plist "$dmg" 2>/dev/null \
        | /usr/bin/grep -oE '/Volumes/[^<]+' \
        | /usr/bin/head -n 1)
    [ -z "$mount_point" ] && return 21

    local src_app
    src_app=$(/bin/ls -d "$mount_point"/*.app 2>/dev/null | /usr/bin/head -n 1)
    if [ -z "$src_app" ]; then
        /usr/bin/hdiutil detach -force "$mount_point" >/dev/null 2>&1 || true
        return 22
    fi

    if ! _web_verify_signature "$src_app" >/dev/null 2>&1; then
        /usr/bin/hdiutil detach -force "$mount_point" >/dev/null 2>&1 || true
        return 23
    fi

    if [ -z "$app_path" ]; then
        app_path="/Applications/$(/usr/bin/basename "$src_app")"
    fi

    local target_dir
    target_dir="$(dirname "$app_path")"
    if ! /bin/cp -R "$src_app" "$target_dir/" 2>/dev/null; then
        if ! /usr/bin/sudo -A /bin/cp -R "$src_app" "$target_dir/"; then
            rc=24
        fi
    fi

    /usr/bin/xattr -dr com.apple.quarantine "$app_path" 2>/dev/null || true

    /usr/bin/hdiutil detach -force "$mount_point" >/dev/null 2>&1 || true
    return $rc
}

# _web_run_apply_cli <slug> <argv_json>
# Eval JSON argv (list of strings) with timeout. Passes returncode through.
# Used by handlers that delegate to a CLI subcommand instead of DMG flow
# (e.g. Claude Code installs via curl|bash; Codex via npm; etc.).
_web_run_apply_cli() {
    local slug="$1" argv_json="$2"
    local timeout="${ASCENDO_WEB_APPLY_CLI_TIMEOUT:-60}"
    local cmd
    cmd=$(printf '%s' "$argv_json" \
        | /usr/bin/python3 -c '
import json, shlex, sys
argv = json.load(sys.stdin)
print(" ".join(shlex.quote(a) for a in argv))
')
    if command -v gtimeout >/dev/null 2>&1; then
        /usr/bin/env gtimeout "$timeout" /bin/sh -c "$cmd"
    else
        /bin/sh -c "$cmd" &
        local pid=$!
        ( sleep "$timeout"; kill "$pid" 2>/dev/null ) &
        wait "$pid"
    fi
}
