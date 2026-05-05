#!/usr/bin/env bash
# adapters/macos/scripts/npm/verify.sh -- post-apply re-check
# Re-runs the same logic as check.sh; if any expected CLI is still missing
# or still outdated, status=failed for that item.
set -o pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ADAPTER_LIB="$SCRIPT_DIR/../../lib"
. "$ADAPTER_LIB/ascendo_json.sh"
. "$ADAPTER_LIB/ascendo_npm.sh"

RUN_ID=""; TRIGGER=""; PROFILE_NAME=""; OUTPUT_DIR=""; DRY_RUN="false"; FILTER=""
while [ $# -gt 0 ]; do
    case "$1" in
        --run-id)     RUN_ID="$2";       shift 2 ;;
        --trigger)    TRIGGER="$2";      shift 2 ;;
        --profile)    PROFILE_NAME="$2"; shift 2 ;;
        --output-dir) OUTPUT_DIR="$2";   shift 2 ;;
        --dry-run)    DRY_RUN="true";    shift ;;
        --filter)     FILTER="$2";       shift 2 ;;
        *) printf 'verify.sh: unknown arg: %s\n' "$1" >&2; exit 2 ;;
    esac
done
[ -z "$RUN_ID" ] || [ -z "$TRIGGER" ] || [ -z "$PROFILE_NAME" ] || [ -z "$OUTPUT_DIR" ] && { printf 'verify.sh: missing args\n' >&2; exit 2; }

HOST_NAME="$(scutil --get LocalHostName 2>/dev/null || hostname -s 2>/dev/null || echo unknown)"
HOST_OS_VERSION="$(sw_vers -productVersion 2>/dev/null || echo unknown)"
HOST_ARCH="$(uname -m 2>/dev/null || echo unknown)"
HOST_USER="$(whoami 2>/dev/null || echo unknown)"
HOST_IS_ELEVATED="false"; [ "${EUID:-$(id -u)}" -eq 0 ] && HOST_IS_ELEVATED="true"

NPM_BIN="$(ascendo_npm_npm_bin)"
TOOL_VERSION="unknown"
[ -n "$NPM_BIN" ] && TOOL_VERSION="$("$NPM_BIN" --version 2>/dev/null || echo unknown)"

json_init "verify" "npm" "$RUN_ID" "$TRIGGER" "$PROFILE_NAME" \
          "npm" "$TOOL_VERSION" \
          "$HOST_NAME" "macos" "$HOST_OS_VERSION" "$HOST_ARCH" \
          "$HOST_USER" "$HOST_IS_ELEVATED"
trap 'json_save_on_exit "$OUTPUT_DIR"' EXIT

in_filter() {
    [ -z "$FILTER" ] && return 0
    case ",$FILTER," in (*",$1,"*) return 0 ;; (*) return 1 ;; esac
}

ascendo_npm_prime_installed_cache
while IFS='|' read -r DISPLAY PKG METHOD BREW CMD; do
    [ "$DISPLAY" = "display_name" ] && continue
    [ -z "$DISPLAY" ] && continue
    in_filter "$DISPLAY" || continue

    INSTALLED=""; LATEST=""
    case "$METHOD" in
        native-node) INSTALLED="$(ascendo_npm_node_installed_version)"; LATEST="$(ascendo_npm_node_latest_version)" ;;
        native-bun)  INSTALLED="$(ascendo_npm_bun_installed_version)";  LATEST="$(ascendo_npm_bun_latest_version)" ;;
        npm)         INSTALLED="$(ascendo_npm_installed_version "$PKG")"; LATEST="$(ascendo_npm_latest_version "$PKG")" ;;
        *) continue ;;
    esac

    # Verify is success when installed!="" AND (latest=="" OR installed==latest).
    # Anything else is failure.
    STATUS="failed"
    if [ -n "$INSTALLED" ]; then
        if [ -z "$LATEST" ] || [ "$INSTALLED" = "$LATEST" ]; then
            STATUS="success"
        fi
    fi
    json_add_item "$DISPLAY" "$INSTALLED" "$LATEST" "$STATUS" "npm" "$METHOD"
done < <(ascendo_npm_manifest_lines)

exit 0
