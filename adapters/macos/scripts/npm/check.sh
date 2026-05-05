#!/usr/bin/env bash
# =============================================================================
# adapters/macos/scripts/npm/check.sh -- read-only npm/Node/Bun inventory
# =============================================================================
# For each entry in adapters/macos/config/npm_global_clis.txt:
#   * native-node:  reports installed Node version + latest LTS (via `n`)
#   * native-bun:   reports installed bun --version + latest GitHub release
#   * npm:          reports installed via `npm ls -g`, latest via `npm view`
# Items emit as up_to_date when installed==latest, planned when installed<latest,
# missing when not installed at all.
# =============================================================================
set -o pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ADAPTER_LIB="$SCRIPT_DIR/../../lib"

# shellcheck source=../../lib/ascendo_json.sh
. "$ADAPTER_LIB/ascendo_json.sh"
# shellcheck source=../../lib/ascendo_npm.sh
. "$ADAPTER_LIB/ascendo_npm.sh"

# -- arg parsing ---------------------------------------------------------------
RUN_ID=""; TRIGGER=""; PROFILE_NAME=""; OUTPUT_DIR=""; DRY_RUN="false"; FILTER=""
while [ $# -gt 0 ]; do
    case "$1" in
        --run-id)     RUN_ID="$2";       shift 2 ;;
        --trigger)    TRIGGER="$2";      shift 2 ;;
        --profile)    PROFILE_NAME="$2"; shift 2 ;;
        --output-dir) OUTPUT_DIR="$2";   shift 2 ;;
        --dry-run)    DRY_RUN="true";    shift ;;
        --filter)     FILTER="$2";       shift 2 ;;
        *) printf 'check.sh: unknown arg: %s\n' "$1" >&2; exit 2 ;;
    esac
done
if [ -z "$RUN_ID" ] || [ -z "$TRIGGER" ] || [ -z "$PROFILE_NAME" ] || [ -z "$OUTPUT_DIR" ]; then
    printf 'check.sh: missing required args\n' >&2
    exit 2
fi

# -- host info -----------------------------------------------------------------
HOST_NAME="$(scutil --get LocalHostName 2>/dev/null || hostname -s 2>/dev/null || echo unknown)"
HOST_OS="macos"
HOST_OS_VERSION="$(sw_vers -productVersion 2>/dev/null || echo unknown)"
HOST_ARCH="$(uname -m 2>/dev/null || echo unknown)"
HOST_USER="$(whoami 2>/dev/null || echo unknown)"
if [ "${EUID:-$(id -u)}" -eq 0 ]; then HOST_IS_ELEVATED="true"; else HOST_IS_ELEVATED="false"; fi

# -- tool info -----------------------------------------------------------------
NPM_BIN="$(ascendo_npm_npm_bin)"
TOOL_VERSION="unknown"
if [ -n "$NPM_BIN" ]; then
    TOOL_VERSION="$("$NPM_BIN" --version 2>/dev/null || echo unknown)"
fi

# -- init sidecar --------------------------------------------------------------
json_init "check" "npm" "$RUN_ID" "$TRIGGER" "$PROFILE_NAME" \
          "npm" "$TOOL_VERSION" \
          "$HOST_NAME" "$HOST_OS" "$HOST_OS_VERSION" "$HOST_ARCH" \
          "$HOST_USER" "$HOST_IS_ELEVATED"
trap 'json_save_on_exit "$OUTPUT_DIR"' EXIT

# -- filter helper -------------------------------------------------------------
in_filter() {
    [ -z "$FILTER" ] && return 0
    case ",$FILTER," in
        (*",$1,"*) return 0 ;;
        (*) return 1 ;;
    esac
}

# -- classify (installed, latest) -> status ------------------------------------
# Returns one of: missing | planned | up_to_date.
# When installed != latest but installed sorts AFTER latest in semver
# order (user runs Node Current vs LTS, or a release-candidate ahead of
# stable), report up_to_date instead of planned — applying would be a
# no-op or a downgrade.
classify() {
    local _installed="$1"
    local _latest="$2"
    if [ -z "$_installed" ]; then printf 'missing'; return; fi
    if [ -z "$_latest" ];    then printf 'up_to_date'; return; fi
    if [ "$_installed" = "$_latest" ]; then printf 'up_to_date'; return; fi
    # sort -V is a GNU extension; macOS BSD sort supports it from 10.13+.
    # If unavailable, fall through to "planned" (the safe default).
    local _lower
    _lower="$(printf '%s\n%s\n' "$_installed" "$_latest" | sort -V 2>/dev/null | head -n1 || true)"
    if [ -n "$_lower" ] && [ "$_lower" = "$_latest" ]; then
        printf 'up_to_date'; return
    fi
    printf 'planned'
}

# -- walk manifest -------------------------------------------------------------
COUNT_PLANNED=0
COUNT_UTD=0
COUNT_MISSING=0

# Prime the `npm ls -g` cache once so per-package lookups are O(1) jq calls
# instead of O(N) npm spawns.
ascendo_npm_prime_installed_cache

# Skip the header row (first non-comment/non-blank line). Manifest is
# pipe-delimited: display_name|package_name|method|brew_formula|command.
# Using process substitution `< <(...)` (NOT `manifest | while`) so the
# loop body can spawn npm/jq freely without draining the manifest pipe
# stdin. The previous pipe form caused only 4 of 9 entries to be
# processed because npm view's stdin handling was eating manifest lines.
while IFS='|' read -r DISPLAY PKG METHOD BREW CMD; do
    # Skip header
    if [ "$DISPLAY" = "display_name" ]; then continue; fi
    [ -z "$DISPLAY" ] && continue
    in_filter "$DISPLAY" || continue

    INSTALLED=""
    LATEST=""

    case "$METHOD" in
        native-node)
            INSTALLED="$(ascendo_npm_node_installed_version)"
            LATEST="$(ascendo_npm_node_latest_version)"
            ;;
        native-bun)
            INSTALLED="$(ascendo_npm_bun_installed_version)"
            LATEST="$(ascendo_npm_bun_latest_version)"
            ;;
        npm)
            INSTALLED="$(ascendo_npm_installed_version "$PKG")"
            LATEST="$(ascendo_npm_latest_version "$PKG")"
            ;;
        *)
            json_add_message "warn" "unknown method '$METHOD' for $DISPLAY; skipping"
            continue
            ;;
    esac

    STATUS="$(classify "$INSTALLED" "$LATEST")"
    case "$STATUS" in
        planned)    COUNT_PLANNED=$((COUNT_PLANNED + 1)) ;;
        up_to_date) COUNT_UTD=$((COUNT_UTD + 1)) ;;
        missing)    COUNT_MISSING=$((COUNT_MISSING + 1)) ;;
    esac

    json_add_item "$DISPLAY" "$INSTALLED" "$LATEST" "$STATUS" "npm" "$METHOD"
done < <(ascendo_npm_manifest_lines)

json_add_message "info" "npm: ${COUNT_PLANNED} outdated, ${COUNT_UTD} up-to-date, ${COUNT_MISSING} missing"
exit 0
