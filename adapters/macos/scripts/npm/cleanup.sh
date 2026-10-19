#!/usr/bin/env bash
# adapters/macos/scripts/npm/cleanup.sh -- prune npm cache + old vendor CLI versions
#
# Version retention (Task 17, Spec P7): keeps the ACTIVE version plus ONE newest
# previous for Codex releases and cursor-agent versions, and removes agy.<n>.old
# (only when `agy --version` runs). Symlinks are canonicalised; a link that
# resolves outside its root aborts pruning for that vendor. Cleanup phase only;
# never on --dry-run. Opt out: ASCENDO_KEEP_CLI_VERSIONS=1 (or the upstream alias
# MAC_UPDATE_KEEP_CLI_VERSIONS=1). Logic: lib/cli_retention.py.
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
        *) printf 'cleanup.sh: unknown arg: %s\n' "$1" >&2; exit 2 ;;
    esac
done
[ -z "$RUN_ID" ] || [ -z "$TRIGGER" ] || [ -z "$PROFILE_NAME" ] || [ -z "$OUTPUT_DIR" ] && { printf 'cleanup.sh: missing args\n' >&2; exit 2; }

HOST_NAME="$(scutil --get LocalHostName 2>/dev/null || hostname -s 2>/dev/null || echo unknown)"
HOST_OS_VERSION="$(sw_vers -productVersion 2>/dev/null || echo unknown)"
HOST_ARCH="$(uname -m 2>/dev/null || echo unknown)"
HOST_USER="$(whoami 2>/dev/null || echo unknown)"
HOST_IS_ELEVATED="false"; [ "${EUID:-$(id -u)}" -eq 0 ] && HOST_IS_ELEVATED="true"

NPM_BIN="$(ascendo_npm_npm_bin)"
TOOL_VERSION="unknown"
[ -n "$NPM_BIN" ] && TOOL_VERSION="$("$NPM_BIN" --version 2>/dev/null || echo unknown)"

json_init "cleanup" "npm" "$RUN_ID" "$TRIGGER" "$PROFILE_NAME" \
          "npm" "$TOOL_VERSION" \
          "$HOST_NAME" "macos" "$HOST_OS_VERSION" "$HOST_ARCH" \
          "$HOST_USER" "$HOST_IS_ELEVATED"
trap 'json_save_on_exit "$OUTPUT_DIR"' EXIT

_keep_versions=0
{ [ "${ASCENDO_KEEP_CLI_VERSIONS:-0}" = "1" ] || [ "${MAC_UPDATE_KEEP_CLI_VERSIONS:-0}" = "1" ]; } && _keep_versions=1

if [ "$DRY_RUN" = "true" ]; then
    json_add_item "npm:cache:clean" "" "" "planned" "npm" "cache"
    [ "$_keep_versions" -eq 1 ] || json_add_item "prune:cli-versions" "" "" "planned" "npm" "prune"
    exit 0
fi

if [ -n "$NPM_BIN" ] && [ -x "$NPM_BIN" ]; then
    _ascendo_npm_invoke "$NPM_BIN" cache clean --force >/dev/null 2>&1 || true
    json_add_item "npm:cache:clean" "" "" "success" "npm" "cache"
else
    json_add_message "info" "npm not installed; cache cleanup skipped"
fi

# -- vendor CLI version retention (Spec P7) -------------------------------------
if [ "$_keep_versions" -eq 1 ]; then
    json_add_message "info" "CLI version pruning disabled (ASCENDO_KEEP_CLI_VERSIONS=1)"
else
    _agy_ok=0
    if [ -x "$HOME/.local/bin/agy" ] && "$HOME/.local/bin/agy" --version </dev/null >/dev/null 2>&1; then
        _agy_ok=1
    fi
    _prune_err="$(mktemp 2>/dev/null || echo /dev/null)"
    _pruned="$(PYTHONPATH="$ADAPTER_LIB${PYTHONPATH:+:$PYTHONPATH}" python3 - "$HOME" "$_agy_ok" <<'PY' 2>"$_prune_err"
import sys
from cli_retention import prune_all
for name, count, mb in prune_all(sys.argv[1], is_agy_working=sys.argv[2] == "1"):
    if count > 0:
        print(f"{count}|{name}|{mb}")
PY
)" || {
        json_add_message "warn" "CLI version pruning failed: $(tail -n 3 "$_prune_err" 2>/dev/null | tr '\n' ' ' | cut -c1-300)"
        _pruned=""
    }
    [ "$_prune_err" = "/dev/null" ] || rm -f "$_prune_err"
    if [ -n "$_pruned" ]; then
        while IFS='|' read -r _cnt _name _mb; do
            [ -n "$_cnt" ] || continue
            json_add_item "prune:${_name}" "" "" "success" "npm" "prune"
            json_add_message "info" "Removed ${_cnt} old ${_name} versions (freed ${_mb} MB)"
        done <<< "$_pruned"
    fi
fi

exit 0
