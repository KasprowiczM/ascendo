#!/usr/bin/env bash
# adapters/macos/scripts/pip/cleanup.sh -- prune pip's wheel/HTTP cache.
# Idempotent — `pip cache purge` is safe to re-run; never fails the run.
# Bash 3.2 compatible.
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
        *) printf 'cleanup.sh: unknown arg: %s\n' "$1" >&2; exit 2 ;;
    esac
done
[ -z "$RUN_ID" ] || [ -z "$TRIGGER" ] || [ -z "$PROFILE_NAME" ] || [ -z "$OUTPUT_DIR" ] && { printf 'cleanup.sh: missing args\n' >&2; exit 2; }

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

json_init "cleanup" "pip" "$RUN_ID" "$TRIGGER" "$PROFILE_NAME" \
          "pip" "$TOOL_VERSION" \
          "$HOST_NAME" "macos" "$HOST_OS_VERSION" "$HOST_ARCH" \
          "$HOST_USER" "$HOST_IS_ELEVATED"
trap 'json_save_on_exit "$OUTPUT_DIR"' EXIT

if [ "$DRY_RUN" = "true" ]; then
    json_add_item "pip:cache:purge" "" "" "planned" "pip" "cache"
    exit 0
fi

if [ -n "$PIP_BIN" ] && [ -x "$PIP_BIN" ]; then
    "$PIP_BIN" cache purge --disable-pip-version-check >/dev/null 2>&1 || true
    json_add_item "pip:cache:purge" "" "" "success" "pip" "cache"
else
    json_add_message "info" "pip not installed; cache cleanup skipped"
fi

exit 0
