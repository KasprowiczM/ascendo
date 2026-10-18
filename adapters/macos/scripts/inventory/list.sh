#!/usr/bin/env bash
# =============================================================================
# adapters/macos/scripts/inventory/list.sh -- read-only app inventory (M5.3)
# =============================================================================
# Enumerates all installed macOS applications via system_profiler and emits
# one ascendo/v1 sidecar at <output-dir>/<run-id>/check__inventory.json on
# every code path via EXIT trap.
#
# Scope (Spec 4): only real TOP-LEVEL apps of the app dirs are listed --
# ASCENDO_APP_DIRS (colon list, default /Applications:$HOME/Applications:
# /System/Applications). Nested helpers (Xcode.app/Contents/Applications/*,
# updater helpers under ~/Library or /Library, in-progress installer dirs,
# /Library/Printers, /System/Library/*), Ascendo itself and anything named in
# config/inventory_exclusions.txt (override: ASCENDO_INVENTORY_EXCLUSIONS) are
# dropped. With the default dirs, apps below $ASCENDO_HOME or this repo are
# dropped too.
#
# iPad / iOS-on-Mac apps (obtained_from=ios_app_store or a
# Wrapper/iTunesMetadata.plist) are filed under mas: id = itemName, version =
# bundleShortVersionString (identity reader shared with the mas scripts).
#
# Item identity: the display name -- the same key `mas list`/check use.
#
# Classification rules (applied in order; first match wins):
#   Rule 1 (SYSTEM): path starts with /System/Applications/
#   Rule 2 (MAS):    app _name appears in `mas list` output (requires mas CLI)
#   Rule 3 (MAS):    obtained_from in (mac_app_store, ios_app_store) or iPad wrapper
#   Rule 4 (BREW):   lowercased-name-with-hyphens matches a `brew list --cask` token
#   Rule 5 (WEB):    everything else (default)
#
# Env overrides (for testing):
#   SP_BIN    path to system_profiler binary (default: /usr/sbin/system_profiler)
#   MAS_BIN   path to mas CLI (default: resolved via command -v mas)
#   BREW_BIN  path to brew CLI (default: resolved via command -v brew)
#   ASCENDO_APP_DIRS              colon list of app dirs (see Scope)
#   ASCENDO_INVENTORY_EXCLUSIONS  exclusions file (default: config/inventory_exclusions.txt)
#   ASCENDO_ROSETTA_PRESENT       0|1 test seam for the Rosetta probe
#   ASCENDO_HOST_ARCH             test seam for `uname -m`
#
# Args:
#   --run-id ID --trigger TRIG --profile NAME --output-dir DIR
#   [--dry-run]   (no-op for inventory list; accepted for build_argv parity)
#
# Exit codes (per docs/agents/contract.md):
#   0   success
#   2   bad usage (missing required args / unknown flag)
#   30  system_profiler failure (apply-fail-unknown)
# =============================================================================
set -o pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ADAPTER_LIB="$SCRIPT_DIR/../../lib"

# shellcheck source=../../lib/ascendo_json.sh
. "$ADAPTER_LIB/ascendo_json.sh"

# -- arg parsing ---------------------------------------------------------------
RUN_ID=""
TRIGGER=""
PROFILE_NAME=""
OUTPUT_DIR=""
DRY_RUN=0

while [ $# -gt 0 ]; do
    case "$1" in
        --run-id)     RUN_ID="$2";       shift 2 ;;
        --trigger)    TRIGGER="$2";      shift 2 ;;
        --profile)    PROFILE_NAME="$2"; shift 2 ;;
        --output-dir) OUTPUT_DIR="$2";   shift 2 ;;
        --dry-run)    DRY_RUN=1;         shift ;;
        *) printf 'list.sh: unknown arg: %s\n' "$1" >&2; exit 2 ;;
    esac
done

if [ -z "$RUN_ID" ] || [ -z "$TRIGGER" ] || [ -z "$PROFILE_NAME" ] || [ -z "$OUTPUT_DIR" ]; then
    printf 'list.sh: missing required args (--run-id, --trigger, --profile, --output-dir)\n' >&2
    exit 2
fi

# -- host info -----------------------------------------------------------------
HOST_NAME="$(scutil --get ComputerName 2>/dev/null || hostname 2>/dev/null || echo unknown)"
HOST_OS="macos"
HOST_OS_VERSION="$(sw_vers -productVersion 2>/dev/null || echo unknown)"
HOST_ARCH="${ASCENDO_HOST_ARCH:-$(uname -m 2>/dev/null || echo unknown)}"
HOST_USER="$(whoami 2>/dev/null || echo unknown)"
if [ "${EUID:-$(id -u)}" -eq 0 ]; then
    HOST_IS_ELEVATED="true"
else
    HOST_IS_ELEVATED="false"
fi

# -- resolve binaries (env-overridable for tests) ------------------------------
SP_BIN="${SP_BIN:-/usr/sbin/system_profiler}"

MAS_BIN_RESOLVED=""
if [ "${MAS_BIN+set}" = "set" ]; then
    # Explicitly provided in env (even if empty = disabled)
    if [ -n "$MAS_BIN" ] && [ -x "$MAS_BIN" ]; then
        MAS_BIN_RESOLVED="$MAS_BIN"
    fi
else
    # Not in env at all -- discover from PATH
    if command -v mas >/dev/null 2>&1; then
        MAS_BIN_RESOLVED="$(command -v mas)"
    fi
fi

BREW_BIN_RESOLVED=""
if [ "${BREW_BIN+set}" = "set" ]; then
    # Explicitly provided in env (even if empty = disabled)
    if [ -n "$BREW_BIN" ] && [ -x "$BREW_BIN" ]; then
        BREW_BIN_RESOLVED="$BREW_BIN"
    fi
else
    # Not in env at all -- discover from PATH
    if command -v brew >/dev/null 2>&1; then
        BREW_BIN_RESOLVED="$(command -v brew)"
    fi
fi

# iPad identity reader (shared with the mas scripts). Sourced after the MAS_BIN
# resolution above because that file defaults MAS_BIN when unset/empty.
# shellcheck source=../../lib/ascendo_appstore_ios.sh
. "$ADAPTER_LIB/ascendo_appstore_ios.sh"

# -- scope: app dirs + exclusions ---------------------------------------------
APP_DIRS_EXPLICIT=0
if [ -n "${ASCENDO_APP_DIRS:-}" ]; then
    APP_DIRS="$ASCENDO_APP_DIRS"
    APP_DIRS_EXPLICIT=1
else
    APP_DIRS="/Applications:$HOME/Applications:/System/Applications"
fi
REPO_ROOT="$(cd "$SCRIPT_DIR/../../../.." 2>/dev/null && pwd)"
EXCLUSIONS_FILE="${ASCENDO_INVENTORY_EXCLUSIONS:-$SCRIPT_DIR/../../config/inventory_exclusions.txt}"

# is_excluded <app_path> -> rc 0 when the app must not be listed.
is_excluded() {
    local _p="$1" _b _pat
    _b="${_p##*/}"; _b="${_b%.app}"
    if [ -f "$EXCLUSIONS_FILE" ]; then
        while IFS= read -r _pat || [ -n "$_pat" ]; do
            case "$_pat" in ''|'#'*) continue ;; esac
            # shellcheck disable=SC2254  # the pattern is a glob on purpose
            case "$_b" in $_pat) return 0 ;; esac
        done < "$EXCLUSIONS_FILE"
    fi
    if [ "$APP_DIRS_EXPLICIT" -eq 0 ]; then
        if [ -n "${ASCENDO_HOME:-}" ]; then
            case "$_p" in "${ASCENDO_HOME%/}"/*) return 0 ;; esac
        fi
        [ -n "$REPO_ROOT" ] && case "$_p" in "$REPO_ROOT"/*) return 0 ;; esac
    fi
    return 1
}

rosetta_present() {
    case "${ASCENDO_ROSETTA_PRESENT:-}" in
        1) return 0 ;;
        0) return 1 ;;
    esac
    pgrep -q oahd 2>/dev/null && return 0
    [ -d /Library/Apple/usr/share/rosetta ]
}

# -- tool info -----------------------------------------------------------------
SP_VERSION="$("$SP_BIN" --version 2>/dev/null | head -1 || echo unknown)"
if [ -z "$SP_VERSION" ]; then
    SP_VERSION="unknown"
fi

# -- init sidecar --------------------------------------------------------------
json_init "check" "inventory" "$RUN_ID" "$TRIGGER" "$PROFILE_NAME" \
          "system_profiler" "$SP_VERSION" \
          "$HOST_NAME" "$HOST_OS" "$HOST_OS_VERSION" "$HOST_ARCH" \
          "$HOST_USER" "$HOST_IS_ELEVATED"

# Install EXIT trap AFTER json_init (bufdir is now valid)
trap 'json_save_on_exit "$OUTPUT_DIR"' EXIT

# -- warm up classification helpers --------------------------------------------
# MAS_NAMES: pipe-delimited app names from `mas list`.
# Empty if mas not installed; rule 2 simply never fires.
MAS_NAMES=""
if [ -n "$MAS_BIN_RESOLVED" ]; then
    # mas list output: "<id>  <name padded>  (version)"
    # Strip leading numeric id and trailing (version); trim trailing whitespace.
    MAS_NAMES="$("$MAS_BIN_RESOLVED" list 2>/dev/null \
        | sed -E 's/^[[:space:]]*[0-9]+[[:space:]]+//; s/[[:space:]]*\([^)]*\)[[:space:]]*$//; s/[[:space:]]+$//' \
        | tr '\n' '|')"
fi

# BREW_CASK_TOKENS: newline-separated cask token list.
BREW_CASK_TOKENS=""
if [ -n "$BREW_BIN_RESOLVED" ]; then
    BREW_CASK_TOKENS="$("$BREW_BIN_RESOLVED" list --cask 2>/dev/null || true)"
fi

# -- run system_profiler -------------------------------------------------------
SP_RC=0
SP_OUT="$("$SP_BIN" -json -detailLevel mini SPApplicationsDataType 2>&1)" || SP_RC=$?
if [ "$SP_RC" -ne 0 ]; then
    # A failed scan is a failed scan -- NOT a package: only the error message.
    json_add_message "error" "system_profiler failed (exit $SP_RC): $SP_OUT"
    exit 30
fi

# -- classify_app helper -------------------------------------------------------
# Returns one of: system | mas | brew | web
# Args: <app_path> <app_name> <obtained_from>
classify_app() {
    local _path="$1"
    local _name="$2"
    local _obtained="$3"

    # Rule 1: Apple system apps. system_profiler enumerates Apple bundles
    # from several paths, all of which we want classified as `system` (NOT
    # `web`, which is for user-installed third-party apps):
    #
    #   /System/Applications/*               — first-party apps (Calculator, Calendar)
    #   /System/Library/CoreServices/*       — agents (AddressBookManager, Dock)
    #   /System/Library/Frameworks/*         — framework-resident bundles
    #   /System/Library/PrivateFrameworks/*  — private framework bundles
    #   /System/iOSSupport/*                 — Mac Catalyst iOS shims
    #   /System/Volumes/Preboot/*            — boot-time helpers
    #   /Library/Apple/*                     — Apple-provided non-system bundles
    #   /usr/libexec/*                       — system service bundles
    #
    # Without these patterns, system_profiler's ~300 service bundles
    # (AccessibilityUIServer, AddressBookManager, AirPlayUIAgent, etc.)
    # pollute the `web` category and clutter the operator's inventory.
    case "$_path" in
        /System/Applications/*) printf 'system\n'; return ;;
        /System/Library/*)      printf 'system\n'; return ;;
        /System/iOSSupport/*)   printf 'system\n'; return ;;
        /System/Volumes/*)      printf 'system\n'; return ;;
        /Library/Apple/*)       printf 'system\n'; return ;;
        /usr/libexec/*)         printf 'system\n'; return ;;
    esac

    # Rule 2: name appears in mas list output (pipe-delimited sentinels)
    if [ -n "$MAS_NAMES" ]; then
        case "|$MAS_NAMES" in
            *"|$_name|"*) printf 'mas\n'; return ;;
        esac
    fi

    # Rule 3: system_profiler reports obtained_from = mac_app_store, or the app
    # is an iPad/iOS-on-Mac App Store app (filed under mas, never web).
    if [ "$_obtained" = "mac_app_store" ] || [ "$_obtained" = "ios_app_store" ] \
       || [ -f "$_path/Wrapper/iTunesMetadata.plist" ]; then
        printf 'mas\n'; return
    fi

    # Rule 4: brew cask token matches lowercased name with spaces -> hyphens
    if [ -n "$BREW_CASK_TOKENS" ]; then
        local _token
        _token="$(printf '%s' "$_name" | tr '[:upper:]' '[:lower:]' | tr ' ' '-')"
        if printf '%s\n' "$BREW_CASK_TOKENS" | grep -Fxq "$_token"; then
            printf 'brew\n'; return
        fi
    fi

    # Rule 5: default
    printf 'web\n'
}

# -- walk apps and emit items --------------------------------------------------
# jq emits one line per app with tab-separated fields: path, name, version,
# obtained_from. We read each line with IFS="" to avoid bash 3.2 issues with
# leading empty fields when path is "" (IFS-based splitting shifts fields).
# Manual substring extraction on the literal tab character is bash 3.2-safe.
TAB="$(printf '\t')"
printf '%s' "$SP_OUT" | jq -r --arg dirs "$APP_DIRS" '
    ($dirs | split(":") | map(select(length > 0) | rtrimstr("/"))) as $d
    | .SPApplicationsDataType[]
    | (.path // "") as $p
    | select($p != "")
    | select(any($d[]; . as $x
          | ($p | startswith($x + "/"))
            and (($p | ltrimstr($x + "/")) | test("^[^/]+\\.app$"))))
    | "\($p)\t\(._name // "")\t\(.version // "")\t\(.obtained_from // "")\t\(.arch_kind // "")"
' | while IFS="" read -r _line; do
    app_path="${_line%%"$TAB"*}"
    _rest="${_line#*"$TAB"}"
    app_name="${_rest%%"$TAB"*}"
    _rest2="${_rest#*"$TAB"}"
    app_ver="${_rest2%%"$TAB"*}"
    _rest3="${_rest2#*"$TAB"}"
    app_obtained="${_rest3%%"$TAB"*}"
    app_arch="${_rest3#*"$TAB"}"

    # Skip malformed entries (empty path or empty name)
    [ -n "$app_path" ] || continue
    [ -n "$app_name" ] || continue
    is_excluded "$app_path" && continue

    src_type="$(classify_app "$app_path" "$app_name" "$app_obtained")"

    # iPad / iOS-on-Mac wrapper: identity comes from iTunesMetadata.plist.
    if [ "$src_type" = "mas" ] && { [ "$app_obtained" = "ios_app_store" ] \
         || [ -f "$app_path/Wrapper/iTunesMetadata.plist" ]; }; then
        _ident="$(ascendo_ios_app_identity "$app_path" 2>/dev/null)" || _ident=""
        if [ -n "$_ident" ]; then
            _in="${_ident%%"$ASCENDO_IOS_US"*}"
            _iv="${_ident##*"$ASCENDO_IOS_US"}"
            [ -z "$_in" ] || app_name="$_in"
            [ -z "$_iv" ] || app_ver="$_iv"
        fi
        json_add_message "info" "${app_name}: iPad app - updated through the App Store app, not mas" "IPAD_APP"
    fi

    # Intel-only app on Apple silicon without Rosetta cannot launch.
    if [ "$app_arch" = "arch_i64" ] && [ "$HOST_ARCH" = "arm64" ] && ! rosetta_present; then
        json_add_message "warn" "${app_name}: Intel-only app and Rosetta is not installed - it will not launch" "INTEL_ONLY"
    fi

    # id = app name; current_version = installed version; target_version = ""
    # source_type = classification result; source_feed = bundle path
    json_add_item "$app_name" "$app_ver" "" "up_to_date" "$src_type" "$app_path"
done

exit 0
