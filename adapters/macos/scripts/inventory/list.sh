#!/usr/bin/env bash
# =============================================================================
# adapters/macos/scripts/inventory/list.sh -- read-only app inventory (M5.3)
# =============================================================================
# Enumerates all installed macOS applications via system_profiler and emits
# one ascendo/v1 sidecar at <output-dir>/<run-id>/check__inventory.json on
# every code path via EXIT trap.
#
# Classification rules (applied in order; first match wins):
#   Rule 1 (SYSTEM): path starts with /System/Applications/
#   Rule 2 (MAS):    app _name appears in `mas list` output (requires mas CLI)
#   Rule 3 (MAS):    obtained_from == mac_app_store
#   Rule 4 (BREW):   lowercased-name-with-hyphens matches a `brew list --cask` token
#   Rule 5 (WEB):    everything else (default)
#
# Env overrides (for testing):
#   SP_BIN    path to system_profiler binary (default: /usr/sbin/system_profiler)
#   MAS_BIN   path to mas CLI (default: resolved via command -v mas)
#   BREW_BIN  path to brew CLI (default: resolved via command -v brew)
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
HOST_ARCH="$(uname -m 2>/dev/null || echo unknown)"
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
    json_add_message "error" "system_profiler failed (exit $SP_RC): $SP_OUT"
    json_add_item "inventory:system-profiler-error" "" "" "failed" "inventory"
    exit 30
fi

# -- classify_app helper -------------------------------------------------------
# Returns one of: system | mas | brew | web
# Args: <app_path> <app_name> <obtained_from>
classify_app() {
    local _path="$1"
    local _name="$2"
    local _obtained="$3"

    # Rule 1: Apple system apps
    case "$_path" in
        /System/Applications/*) printf 'system\n'; return ;;
    esac

    # Rule 2: name appears in mas list output (pipe-delimited sentinels)
    if [ -n "$MAS_NAMES" ]; then
        case "|$MAS_NAMES" in
            *"|$_name|"*) printf 'mas\n'; return ;;
        esac
    fi

    # Rule 3: system_profiler reports obtained_from = mac_app_store
    if [ "$_obtained" = "mac_app_store" ]; then
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
printf '%s' "$SP_OUT" | jq -r '
    .SPApplicationsDataType[]
    | "\(.path // "")\t\(._name // "")\t\(.version // "")\t\(.obtained_from // "")"
' | while IFS="" read -r _line; do
    app_path="${_line%%"$TAB"*}"
    _rest="${_line#*"$TAB"}"
    app_name="${_rest%%"$TAB"*}"
    _rest2="${_rest#*"$TAB"}"
    app_ver="${_rest2%%"$TAB"*}"
    app_obtained="${_rest2#*"$TAB"}"

    # Skip malformed entries (empty path or empty name)
    [ -n "$app_path" ] || continue
    [ -n "$app_name" ] || continue

    src_type="$(classify_app "$app_path" "$app_name" "$app_obtained")"

    # id = app name; current_version = installed version; target_version = ""
    # source_type = classification result; source_feed = bundle path
    json_add_item "$app_name" "$app_ver" "" "up_to_date" "$src_type" "$app_path"
done

exit 0
