#!/usr/bin/env bash
# adapters/macos/scripts/pip/plan.sh -- side-effect-free planning phase.
# Same logic as check.sh, but emits ONLY items that would change in apply
# (planned + missing). The sidecar's `phase` field reads `plan`.
#
# Read-only. No mutation. Never invokes sudo. Bash 3.2 compatible.
set -o pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ADAPTER_LIB="$SCRIPT_DIR/../../lib"
. "$ADAPTER_LIB/ascendo_json.sh"
. "$ADAPTER_LIB/ascendo_pip.sh"

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

PIP_BIN="$(ascendo_pip_pip_bin)"
TOOL_VERSION="unknown"
if [ -n "$PIP_BIN" ]; then
    TOOL_VERSION="$("$PIP_BIN" --version 2>/dev/null </dev/null | awk '{print $2}' || echo unknown)"
    [ -z "$TOOL_VERSION" ] && TOOL_VERSION="unknown"
fi

json_init "plan" "pip" "$RUN_ID" "$TRIGGER" "$PROFILE_NAME" \
          "pip" "$TOOL_VERSION" \
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
    local _lower
    _lower="$(printf '%s\n%s\n' "$_installed" "$_latest" | sort -V 2>/dev/null | head -n1 || true)"
    if [ -n "$_lower" ] && [ "$_lower" = "$_latest" ]; then
        printf 'up_to_date'; return
    fi
    printf 'planned'
}

ascendo_pip_prime_installed_cache
while IFS='|' read -r DISPLAY PKG METHOD DESC; do
    [ "$DISPLAY" = "display_name" ] && continue
    [ -z "$DISPLAY" ] && continue
    in_filter "$DISPLAY" || continue

    INSTALLED=""; LATEST=""
    case "$METHOD" in
        pip) INSTALLED="$(ascendo_pip_installed_version "$PKG")"; LATEST="$(ascendo_pip_latest_version "$PKG")" ;;
        *) continue ;;
    esac
    STATUS="$(classify "$INSTALLED" "$LATEST")"
    # Brew-managed pip / setuptools / wheel cannot be self-upgraded —
    # the apply phase will skip them, so don't list them in the plan
    # either. Same rule as in check.sh.
    if [ "$STATUS" = "planned" ]; then
        case "$(_ascendo_pip_flavour "$(ascendo_pip_pip_bin)"):$PKG" in
            brew:pip|brew:setuptools|brew:wheel) continue ;;
        esac
    fi
    # Only emit items that would CHANGE in apply (planned + missing).
    case "$STATUS" in
        planned|missing) json_add_item "$DISPLAY" "$INSTALLED" "$LATEST" "$STATUS" "pip" "$METHOD" ;;
    esac
done < <(ascendo_pip_manifest_lines)

exit 0
