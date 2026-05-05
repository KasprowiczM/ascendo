#!/usr/bin/env bash
# =============================================================================
# adapters/macos/scripts/npm/apply.sh -- mutating: bootstrap toolchain + upgrade CLIs
# =============================================================================
# For each manifest entry whose status is `missing` or `planned`:
#   * native-node:  install/upgrade Node via `n` under $TOOLCHAIN_HOME/node
#   * native-bun:   run the official bun installer (curl https://bun.sh/install)
#   * npm:          `npm install -g <package_name>` under $NPM_GLOBAL_PREFIX
#
# Bootstrap order:
#   1. Ensure $TOOLCHAIN_HOME/node/bin/node exists (install via brew first
#      if absent, then `npm install -g n`, then `n latest`).
#   2. Ensure $TOOLCHAIN_HOME/npm-global is configured as the npm prefix.
#   3. Walk manifest, install/upgrade per method.
#
# `--dry-run` short-circuits BEFORE any mutation: items emit as `planned`
# and the script exits 0.
# =============================================================================
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
        *) printf 'apply.sh: unknown arg: %s\n' "$1" >&2; exit 2 ;;
    esac
done
[ -z "$RUN_ID" ] || [ -z "$TRIGGER" ] || [ -z "$PROFILE_NAME" ] || [ -z "$OUTPUT_DIR" ] && { printf 'apply.sh: missing args\n' >&2; exit 2; }

HOST_NAME="$(scutil --get LocalHostName 2>/dev/null || hostname -s 2>/dev/null || echo unknown)"
HOST_OS_VERSION="$(sw_vers -productVersion 2>/dev/null || echo unknown)"
HOST_ARCH="$(uname -m 2>/dev/null || echo unknown)"
HOST_USER="$(whoami 2>/dev/null || echo unknown)"
HOST_IS_ELEVATED="false"; [ "${EUID:-$(id -u)}" -eq 0 ] && HOST_IS_ELEVATED="true"

NPM_BIN="$(ascendo_npm_npm_bin)"
TOOL_VERSION="unknown"
[ -n "$NPM_BIN" ] && TOOL_VERSION="$("$NPM_BIN" --version 2>/dev/null || echo unknown)"

json_init "apply" "npm" "$RUN_ID" "$TRIGGER" "$PROFILE_NAME" \
          "npm" "$TOOL_VERSION" \
          "$HOST_NAME" "macos" "$HOST_OS_VERSION" "$HOST_ARCH" \
          "$HOST_USER" "$HOST_IS_ELEVATED"
trap 'json_save_on_exit "$OUTPUT_DIR"' EXIT

in_filter() {
    [ -z "$FILTER" ] && return 0
    case ",$FILTER," in (*",$1,"*) return 0 ;; (*) return 1 ;; esac
}

# -- bootstrap npm-global prefix (idempotent, safe to repeat) -----------------
TOOLCHAIN_HOME="$(ascendo_npm_toolchain_home)"
NPM_GLOBAL_PREFIX="$(ascendo_npm_global_prefix)"
mkdir -p "$NPM_GLOBAL_PREFIX/bin" 2>/dev/null || true
if [ -n "$NPM_BIN" ] && [ -x "$NPM_BIN" ]; then
    "$NPM_BIN" config set prefix "$NPM_GLOBAL_PREFIX" >/dev/null 2>&1 || true
fi
export PATH="$NPM_GLOBAL_PREFIX/bin:$PATH"

# -- per-method handlers ------------------------------------------------------
apply_native_node() {
    local _display="$1"
    if [ "$DRY_RUN" = "true" ]; then
        json_add_item "$_display" "" "" "planned" "npm" "native-node"
        return
    fi
    _stream_emit ">>> bootstrapping native-node ($_display)"
    # Need an npm to install `n`; if absent, fall back to brew's node so
    # we have an npm at all.
    if [ -z "$NPM_BIN" ] || [ ! -x "$NPM_BIN" ]; then
        if command -v brew >/dev/null 2>&1; then
            brew install node 2>&1 | _stream_tee >/dev/null
            if [ "${PIPESTATUS[0]:-1}" -ne 0 ]; then
                json_add_item "$_display" "" "" "failed" "npm" "native-node"
                json_add_message "error" "node bootstrap failed (brew install node)"
                return
            fi
            NPM_BIN="$(command -v npm 2>/dev/null)"
        else
            json_add_item "$_display" "" "" "failed" "npm" "native-node"
            json_add_message "error" "no npm + no brew; cannot bootstrap Node"
            return
        fi
    fi
    # Install `n` to user prefix, then run `n lts` to put node in TOOLCHAIN_HOME.
    "$NPM_BIN" install -g n 2>&1 | _stream_tee >/dev/null
    if [ "${PIPESTATUS[0]:-1}" -ne 0 ]; then
        json_add_item "$_display" "" "" "failed" "npm" "native-node"
        json_add_message "error" "'npm install -g n' failed"
        return
    fi
    local _N="$NPM_GLOBAL_PREFIX/bin/n"
    [ -x "$_N" ] || _N="$(command -v n 2>/dev/null)"
    if [ -z "$_N" ] || [ ! -x "$_N" ]; then
        json_add_item "$_display" "" "" "failed" "npm" "native-node"
        json_add_message "error" "n CLI not on PATH after install"
        return
    fi
    N_PREFIX="$(ascendo_npm_n_prefix)" "$_N" lts 2>&1 | _stream_tee >/dev/null
    if [ "${PIPESTATUS[0]:-1}" -ne 0 ]; then
        json_add_item "$_display" "" "" "failed" "npm" "native-node"
        json_add_message "error" "'n lts' failed"
        return
    fi
    local _new="$(ascendo_npm_node_installed_version)"
    json_add_item "$_display" "$_new" "$_new" "success" "npm" "native-node"
}

apply_native_bun() {
    local _display="$1"
    if [ "$DRY_RUN" = "true" ]; then
        json_add_item "$_display" "" "" "planned" "npm" "native-bun"
        return
    fi
    _stream_emit ">>> bootstrapping native-bun ($_display)"
    if ! command -v curl >/dev/null 2>&1; then
        json_add_item "$_display" "" "" "failed" "npm" "native-bun"
        json_add_message "error" "curl missing; cannot bootstrap bun"
        return
    fi
    {
        BUN_INSTALL="$(ascendo_npm_bun_home)" curl -fsSL https://bun.sh/install \
            | BUN_INSTALL="$(ascendo_npm_bun_home)" bash 2>&1
    } | _stream_tee >/dev/null
    local _rc="${PIPESTATUS[0]:-1}"
    if [ "$_rc" -ne 0 ]; then
        json_add_item "$_display" "" "" "failed" "npm" "native-bun"
        json_add_message "error" "bun install script exited $_rc"
        return
    fi
    local _new="$(ascendo_npm_bun_installed_version)"
    json_add_item "$_display" "$_new" "$_new" "success" "npm" "native-bun"
}

apply_npm() {
    local _display="$1"
    local _pkg="$2"
    if [ "$DRY_RUN" = "true" ]; then
        json_add_item "$_display" "" "" "planned" "npm" "npm"
        return
    fi
    if [ -z "$NPM_BIN" ] || [ ! -x "$NPM_BIN" ]; then
        json_add_item "$_display" "" "" "failed" "npm" "npm"
        json_add_message "error" "npm not installed; bootstrap node first ($_display)"
        return
    fi
    _stream_emit ">>> npm install -g $_pkg ($_display)"
    "$NPM_BIN" install -g "$_pkg" 2>&1 | _stream_tee >/dev/null
    local _rc="${PIPESTATUS[0]:-1}"
    if [ "$_rc" -ne 0 ]; then
        json_add_item "$_display" "" "" "failed" "npm" "npm"
        json_add_message "error" "'npm install -g $_pkg' exited $_rc"
        return
    fi
    local _new="$(ascendo_npm_installed_version "$_pkg")"
    json_add_item "$_display" "$_new" "$_new" "success" "npm" "npm"
}

# Total count for live-stream progress accounting.
TOTAL_ITEMS="$(ascendo_npm_manifest_lines | awk -F'|' 'NR > 1 && length($1) > 0 { c++ } END { print c+0 }')"
CURRENT_INDEX=0

if [ "$DRY_RUN" != "true" ] && [ "$TOTAL_ITEMS" -gt 0 ]; then
    _stream_progress 0 "npm apply: $TOTAL_ITEMS item(s)"
fi

# -- walk manifest ------------------------------------------------------------
# Process substitution (not `manifest | while`) — see check.sh for the
# bug class this avoids.
while IFS='|' read -r DISPLAY PKG METHOD BREW CMD; do
    [ "$DISPLAY" = "display_name" ] && continue
    [ -z "$DISPLAY" ] && continue
    in_filter "$DISPLAY" || continue
    if [ "$DRY_RUN" != "true" ]; then
        CURRENT_INDEX=$(( CURRENT_INDEX + 1 ))
        _pct=0
        if [ "$TOTAL_ITEMS" -gt 0 ]; then
            _pct=$(( CURRENT_INDEX * 100 / TOTAL_ITEMS ))
        fi
        _stream_progress "$_pct" "npm: $DISPLAY ($METHOD)"
    fi
    case "$METHOD" in
        native-node) apply_native_node "$DISPLAY" ;;
        native-bun)  apply_native_bun  "$DISPLAY" ;;
        npm)         apply_npm "$DISPLAY" "$PKG"  ;;
        *) json_add_message "warn" "unknown method '$METHOD' for $DISPLAY; skipping" ;;
    esac
done < <(ascendo_npm_manifest_lines)

if [ "$DRY_RUN" != "true" ] && [ "$TOTAL_ITEMS" -gt 0 ]; then
    _stream_progress 100 "npm apply: done"
fi

exit 0
