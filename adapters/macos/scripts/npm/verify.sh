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
[ -n "$NPM_BIN" ] && TOOL_VERSION="$(_ascendo_npm_invoke "$NPM_BIN" --version 2>/dev/null || echo unknown)"

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
        native-installer)
            INSTALLED="$(ascendo_npm_native_installed_version "$DISPLAY" "$CMD")"
            case "$PKG" in
                *@*|*/*) LATEST="$(ascendo_npm_latest_version "$PKG")" ;;
                *) LATEST="" ;;
            esac
            ;;
        self-update)
            # T8-c beats T8-d: never execute an unverified command; Homebrew-owned
            # is not verified by the npm category unless explicitly asked.
            _su_state="$(ascendo_npm_self_update_state "$CMD")"
            if [ "$_su_state" = "unverified" ]; then
                json_add_item "$DISPLAY" "" "" "skipped" "npm" "$METHOD"
                json_add_message "info" "$DISPLAY: unverified installation origin; retaining existing distribution (reason=unverified_origin)"
                continue
            fi
            if [ "$_su_state" = "brew" ] && ! ascendo_npm_bootstrap_allowed "$DISPLAY"; then
                json_add_item "$DISPLAY" "" "" "skipped" "npm" "$METHOD"
                json_add_message "info" "$DISPLAY: managed by Homebrew (updates flow through brew)"
                continue
            fi
            INSTALLED="$(ascendo_npm_self_update_installed_version "$DISPLAY" "$CMD")"; LATEST="$(ascendo_npm_latest_version "$PKG")" ;;
        npm)         INSTALLED="$(ascendo_npm_installed_version "$PKG")"; LATEST="$(ascendo_npm_latest_version "$PKG")" ;;
        *) continue ;;
    esac

    # Only-installed model (cli.md P5): something that is not installed and was
    # not asked for was never going to be installed -- skipped, not failed.
    # A self-update command that is present but silent is a real failure.
    _present_broken=0
    if [ "$METHOD" = "self-update" ] && [ -z "$INSTALLED" ] \
       && [ -n "$(ascendo_npm_self_update_command_path "$CMD")" ]; then
        _present_broken=1
    fi
    if [ -z "$INSTALLED" ] && [ "$_present_broken" -eq 0 ] \
       && ! ascendo_npm_bootstrap_allowed "$DISPLAY"; then
        json_add_item "$DISPLAY" "" "$LATEST" "skipped" "npm" "$METHOD"
        json_add_message "info" "$DISPLAY is not installed — skipping (set ASCENDO_BOOTSTRAP_CLI=1 or select it explicitly to install)"
        continue
    fi

    # Verify is success when installed!="" AND (latest=="" OR installed==latest
    # OR installed is provably newer). Anything else is failure.
    STATUS="failed"
    if [ -n "$INSTALLED" ]; then
        if [ -z "$LATEST" ] || [ "$INSTALLED" = "$LATEST" ] \
           || ascendo_npm_version_newer "$INSTALLED" "$LATEST"; then
            STATUS="success"
        fi
    fi
    if [ "$_present_broken" -eq 1 ]; then
        json_add_message "error" "$DISPLAY: '$CMD' is present but '$CMD --version' does not print a version"
    fi
    # A native CLI that is present must actually run `--version` and print a
    # digit-bearing version; a bare product name ("codex-cli") is not one.
    if [ "$METHOD" = "native-installer" ] && [ -n "$INSTALLED" ]; then
        _native_path="$(ascendo_npm_native_command_path "$CMD")"
        if [ -z "$_native_path" ] \
           || ! ascendo_npm_report_cli_version_or_fail "$DISPLAY" "$_native_path" >/dev/null; then
            STATUS="failed"
            json_add_message "error" "$DISPLAY: '$CMD --version' does not print a version"
        fi
    fi
    json_add_item "$DISPLAY" "$INSTALLED" "$LATEST" "$STATUS" "npm" "$METHOD"
done < <(ascendo_npm_manifest_lines)

exit 0
