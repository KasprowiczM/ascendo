#!/usr/bin/env bash
# adapters/macos/scripts/npm/plan.sh -- side-effect-free planning phase.
# Identical to check.sh in this manager (no separate "plan" semantics for
# npm/bun beyond what check produces). Symlink-style delegation; we
# duplicate the body so the sidecar's `phase` field reads `plan`.
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
        *) printf 'plan.sh: unknown arg: %s\n' "$1" >&2; exit 2 ;;
    esac
done
[ -z "$RUN_ID" ] || [ -z "$TRIGGER" ] || [ -z "$PROFILE_NAME" ] || [ -z "$OUTPUT_DIR" ] && { printf 'plan.sh: missing args\n' >&2; exit 2; }

HOST_NAME="$(scutil --get LocalHostName 2>/dev/null || hostname -s 2>/dev/null || echo unknown)"
HOST_OS_VERSION="$(sw_vers -productVersion 2>/dev/null || echo unknown)"
HOST_ARCH="$(uname -m 2>/dev/null || echo unknown)"
HOST_USER="$(whoami 2>/dev/null || echo unknown)"
HOST_IS_ELEVATED="false"; [ "${EUID:-$(id -u)}" -eq 0 ] && HOST_IS_ELEVATED="true"

NPM_BIN="$(ascendo_npm_npm_bin)"
TOOL_VERSION="unknown"
[ -n "$NPM_BIN" ] && TOOL_VERSION="$("$NPM_BIN" --version 2>/dev/null || echo unknown)"

json_init "plan" "npm" "$RUN_ID" "$TRIGGER" "$PROFILE_NAME" \
          "npm" "$TOOL_VERSION" \
          "$HOST_NAME" "macos" "$HOST_OS_VERSION" "$HOST_ARCH" \
          "$HOST_USER" "$HOST_IS_ELEVATED"
trap 'json_save_on_exit "$OUTPUT_DIR"' EXIT

in_filter() {
    [ -z "$FILTER" ] && return 0
    case ",$FILTER," in (*",$1,"*) return 0 ;; (*) return 1 ;; esac
}
classify() {
    local _installed="$1"
    local _latest="$2"
    [ -z "$_installed" ] && { printf 'missing'; return; }
    [ -z "$_latest" ] && { printf 'up_to_date'; return; }
    [ "$_installed" = "$_latest" ] && { printf 'up_to_date'; return; }
    # W11: Use Python version comparison instead of sort -V
    if python3 -c "import sys; from ascendo.utils.version import version_gt; sys.exit(0 if version_gt(sys.argv[1], sys.argv[2]) else 1)" "$_installed" "$_latest" 2>/dev/null; then
        printf 'up_to_date'; return
    fi
    printf 'planned'
}

# Process substitution (not `manifest | while`) — see check.sh for the
# bug class this avoids (npm draining the manifest pipe stdin).
ascendo_npm_prime_installed_cache
while IFS='|' read -r DISPLAY PKG METHOD BREW CMD; do
    [ "$DISPLAY" = "display_name" ] && continue
    [ -z "$DISPLAY" ] && continue
    in_filter "$DISPLAY" || continue

    INSTALLED=""; LATEST=""
    case "$METHOD" in
        native-node) INSTALLED="$(ascendo_npm_node_installed_version)"; LATEST="$(ascendo_npm_node_latest_version)" ;;
        native-bun)  INSTALLED="$(ascendo_npm_bun_installed_version)";  LATEST="$(ascendo_npm_bun_latest_version)" ;;
        native-installer)
            INSTALLED="$(ascendo_npm_native_installed_version "$DISPLAY" "$CMD")"
            case "$PKG" in
                *@*|*/*) LATEST="$(ascendo_npm_latest_version "$PKG")" ;;
                *) LATEST="" ;;
            esac
            ;;
        npm)         INSTALLED="$(ascendo_npm_installed_version "$PKG")"; LATEST="$(ascendo_npm_latest_version "$PKG")" ;;
        *) continue ;;
    esac
    STATUS="$(classify "$INSTALLED" "$LATEST")"
    # Only emit items that would CHANGE in apply (planned + missing) — plan
    # phase is "what would change", not full inventory.
    case "$STATUS" in
        planned|missing) json_add_item "$DISPLAY" "$INSTALLED" "$LATEST" "$STATUS" "npm" "$METHOD" ;;
    esac
done < <(ascendo_npm_manifest_lines)

exit 0
