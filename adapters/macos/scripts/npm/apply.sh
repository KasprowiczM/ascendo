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
[ -n "$NPM_BIN" ] && TOOL_VERSION="$(_ascendo_npm_invoke "$NPM_BIN" --version 2>/dev/null || echo unknown)"

# Convenience wrapper around _ascendo_npm_invoke that uses the
# script-scope NPM_BIN. Replaces bare `"$NPM_BIN" args` calls so we
# always invoke npm via its sibling node binary.
_run_npm() {
    _ascendo_npm_invoke "$NPM_BIN" "$@"
}

json_init "apply" "npm" "$RUN_ID" "$TRIGGER" "$PROFILE_NAME" \
          "npm" "$TOOL_VERSION" \
          "$HOST_NAME" "macos" "$HOST_OS_VERSION" "$HOST_ARCH" \
          "$HOST_USER" "$HOST_IS_ELEVATED"
trap 'json_save_on_exit "$OUTPUT_DIR"' EXIT

in_filter() {
    [ -z "$FILTER" ] && return 0
    case ",$FILTER," in (*",$1,"*) return 0 ;; (*) return 1 ;; esac
}

# classify <installed> <latest>  -> up_to_date | planned | missing
# Mirrors the rule used in check.sh: empty installed -> missing,
# empty latest (network probe failed) -> up_to_date (don't punish on
# probe failure), installed == latest -> up_to_date, semver-sort says
# installed >= latest -> up_to_date, otherwise planned.
classify() {
    local _installed="$1"
    local _latest="$2"
    if [ -z "$_installed" ]; then printf 'missing'; return; fi
    if [ -z "$_latest" ];    then printf 'up_to_date'; return; fi
    if [ "$_installed" = "$_latest" ]; then printf 'up_to_date'; return; fi
    local _lower
    _lower="$(printf '%s\n%s\n' "$_installed" "$_latest" | sort -V 2>/dev/null | head -n1 || true)"
    if [ -n "$_lower" ] && [ "$_lower" = "$_latest" ]; then
        printf 'up_to_date'; return
    fi
    printf 'planned'
}

# Prime the `npm ls -g` cache once so per-package lookups in apply_npm
# are O(1) jq queries instead of O(N) npm spawns. Saves ~100ms per pkg
# on the up_to_date guard.
ascendo_npm_prime_installed_cache

# -- bootstrap npm-global prefix (idempotent, safe to repeat) -----------------
# We deliberately do NOT write the prefix into ~/.npmrc (legacy bug:
# `npm config set prefix` did exactly that, and nvm refuses to load
# Node when ~/.npmrc carries `prefix=` or `globalconfig=`). Use
# NPM_CONFIG_PREFIX env var instead — precedence env > .npmrc — and
# scrub any stale lines from a prior install once per apply run so an
# old `prefix=` line written by another tool can't keep tripping nvm.
TOOLCHAIN_HOME="$(ascendo_npm_toolchain_home)"
NPM_GLOBAL_PREFIX="$(ascendo_npm_global_prefix)"
mkdir -p "$NPM_GLOBAL_PREFIX/bin" 2>/dev/null || true
ascendo_npm_scrub_npmrc || true
export NPM_CONFIG_PREFIX="$NPM_GLOBAL_PREFIX"
export PATH="$NPM_GLOBAL_PREFIX/bin:$PATH"

# -- per-method handlers ------------------------------------------------------
apply_native_node() {
    local _display="$1"
    if [ "$DRY_RUN" = "true" ]; then
        json_add_item "$_display" "" "" "planned" "npm" "native-node"
        return
    fi
    # Sesja 50 fix — up_to_date guard: skip the bootstrap if Node is
    # already at the latest LTS. Without this, every apply re-runs
    # `npm install -g n` + `n lts` even when nothing has changed.
    local _installed _latest _status
    _installed="$(ascendo_npm_node_installed_version 2>/dev/null)"
    _latest="$(ascendo_npm_node_latest_version 2>/dev/null)"
    _status="$(classify "$_installed" "$_latest")"
    if [ "$_status" = "up_to_date" ]; then
        json_add_item "$_display" "$_installed" "$_latest" "up_to_date" "npm" "native-node"
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
    _run_npm install -g n 2>&1 | _stream_tee >/dev/null
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
    # Sesja 50 fix — up_to_date guard: skip the bun installer when
    # bun is already at the latest published GitHub release.
    local _installed _latest _status
    _installed="$(ascendo_npm_bun_installed_version 2>/dev/null)"
    _latest="$(ascendo_npm_bun_latest_version 2>/dev/null)"
    _status="$(classify "$_installed" "$_latest")"
    if [ "$_status" = "up_to_date" ]; then
        json_add_item "$_display" "$_installed" "$_latest" "up_to_date" "npm" "native-bun"
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
    # Sesja 50 fix — up_to_date guard. Without this, every apply ran
    # `npm install -g <pkg>` for every manifest entry regardless of
    # whether it was already at latest, generating a wall of
    # "Requirement already satisfied" output and ~1-3s of network
    # round-trips per package.
    local _installed _latest _status
    _installed="$(ascendo_npm_installed_version "$_pkg" 2>/dev/null)"
    _latest="$(ascendo_npm_latest_version "$_pkg" 2>/dev/null)"
    _status="$(classify "$_installed" "$_latest")"
    if [ "$_status" = "up_to_date" ]; then
        json_add_item "$_display" "$_installed" "$_latest" "up_to_date" "npm" "npm"
        return
    fi
    _stream_emit ">>> npm install -g $_pkg ($_display)"
    # Capture combined stdout+stderr to a temp log AND tee to live
    # stream — same pattern as pip apply, so a failure surfaces npm's
    # actual error (registry 404, EACCES, version conflict, etc.) in
    # the sidecar message instead of a bare exit code.
    local _tmp_log
    _tmp_log="$(mktemp -t ascendo-npm-apply.XXXXXX 2>/dev/null || mktemp /tmp/ascendo-npm-apply.XXXXXX)"
    _run_npm install -g "$_pkg" 2>&1 | tee "$_tmp_log" | _stream_tee >/dev/null
    local _rc="${PIPESTATUS[0]:-1}"
    if [ "$_rc" -ne 0 ]; then
        json_add_item "$_display" "" "" "failed" "npm" "npm"
        local _tail
        _tail="$(tail -n 12 "$_tmp_log" 2>/dev/null \
                 | tr '\t' ' ' \
                 | awk 'NF{print}' \
                 | head -c 1500 || true)"
        if [ -n "$_tail" ]; then
            json_add_message "error" "npm install -g $_pkg exited $_rc — last output: $_tail"
        else
            json_add_message "error" "npm install -g $_pkg exited $_rc (no output captured)"
        fi
        rm -f "$_tmp_log" 2>/dev/null
        return
    fi
    rm -f "$_tmp_log" 2>/dev/null
    # Bust the npm-ls cache so the post-install version reflects the
    # version we just installed, not the pre-install snapshot.
    _ASCENDO_NPM_LS_CACHE=""
    _ASCENDO_NPM_LS_CACHED=0
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
