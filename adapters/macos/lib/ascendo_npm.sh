#!/usr/bin/env bash
# =============================================================================
# adapters/macos/lib/ascendo_npm.sh -- npm/Node/Bun helper functions
# =============================================================================
# Sourced by adapters/macos/scripts/npm/*.sh. Bash 3.2 compatible (no
# associative arrays, no mapfile, no readarray).
#
# Provides:
#   ascendo_npm_manifest_path           prints path to npm_global_clis.txt
#   ascendo_npm_load_manifest           sets MANIFEST_LINES (newline-delimited)
#   ascendo_npm_toolchain_home          prints $TOOLCHAIN_HOME (default ok)
#   ascendo_npm_npm_bin                 prints path to user-scope npm binary
#   ascendo_npm_node_bin                prints path to user-scope node binary
#   ascendo_npm_bun_bin                 prints path to bun binary
#   ascendo_npm_outdated_json           emits `npm outdated -g --json` output
#   ascendo_npm_installed_version       prints installed version of one pkg
#   ascendo_npm_latest_version          prints latest published version
#   ascendo_npm_bun_installed_version   prints bun --version (or empty)
# =============================================================================
# shellcheck shell=bash

# -- toolchain home ---------------------------------------------------------
# User-scope (no sudo). Mirrors legacy update_npm_cli.sh.
ascendo_npm_toolchain_home() {
    printf '%s' "${MAC_UPDATE_TOOLCHAIN_HOME:-$HOME/.local/share/mac-update}"
}

ascendo_npm_n_prefix() {
    printf '%s/node' "$(ascendo_npm_toolchain_home)"
}

ascendo_npm_global_prefix() {
    printf '%s/npm-global' "$(ascendo_npm_toolchain_home)"
}

ascendo_npm_bun_home() {
    printf '%s' "${BUN_INSTALL:-$HOME/.bun}"
}

# -- binary discovery -------------------------------------------------------
# Resolution order: user-scope toolchain first (preferred), then PATH,
# then fall back to brew/system. Returns "" when no binary found.

ascendo_npm_node_bin() {
    local _user="$(ascendo_npm_n_prefix)/bin/node"
    if [ -x "$_user" ]; then printf '%s' "$_user"; return 0; fi
    command -v node 2>/dev/null || true
}

ascendo_npm_npm_bin() {
    local _user="$(ascendo_npm_n_prefix)/bin/npm"
    if [ -x "$_user" ]; then printf '%s' "$_user"; return 0; fi
    command -v npm 2>/dev/null || true
}

ascendo_npm_bun_bin() {
    local _user="$(ascendo_npm_bun_home)/bin/bun"
    if [ -x "$_user" ]; then printf '%s' "$_user"; return 0; fi
    command -v bun 2>/dev/null || true
}

# -- manifest loader --------------------------------------------------------
# adapters/macos/config/npm_global_clis.txt — pipe-delimited:
#   display_name|package_name|method|brew_formula|command
# Lines starting with # and blank lines are skipped.
ascendo_npm_manifest_path() {
    local _self_dir
    _self_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    printf '%s/../config/npm_global_clis.txt' "$_self_dir"
}

# Echoes manifest lines (one per pkg, # and blank stripped).
ascendo_npm_manifest_lines() {
    local _path
    _path="$(ascendo_npm_manifest_path)"
    if [ ! -f "$_path" ]; then return 0; fi
    # Strip comments + blanks; preserve everything else.
    sed -e 's/[[:space:]]*#.*$//' -e '/^[[:space:]]*$/d' "$_path"
}

# -- npm queries ------------------------------------------------------------

# `npm outdated -g --json` → stdout. Empty {} on success-with-no-outdated;
# `npm outdated` exits 1 when there ARE outdated packages, so we always
# treat exit 0 OR 1 as success.
ascendo_npm_outdated_json() {
    local _npm
    _npm="$(ascendo_npm_npm_bin)"
    if [ -z "$_npm" ]; then printf '{}'; return 0; fi
    local _out
    # IMPORTANT: redirect stdin from /dev/null so this never drains a
    # parent's pipe (e.g. `manifest | while read ...; do call_this; done`
    # would otherwise lose the manifest stream after the first iteration).
    _out="$("$_npm" outdated -g --json </dev/null 2>/dev/null || true)"
    if [ -z "$_out" ]; then printf '{}'; else printf '%s' "$_out"; fi
}

# Cache for `npm ls -g`. The full ls is expensive; called once per
# script-run via ``ascendo_npm_prime_installed_cache`` and reused by
# ``ascendo_npm_installed_version``.
_ASCENDO_NPM_LS_CACHE=""
_ASCENDO_NPM_LS_CACHED=0

ascendo_npm_prime_installed_cache() {
    local _npm
    _npm="$(ascendo_npm_npm_bin)"
    if [ -z "$_npm" ]; then _ASCENDO_NPM_LS_CACHE="{}"; _ASCENDO_NPM_LS_CACHED=1; return 0; fi
    _ASCENDO_NPM_LS_CACHE="$("$_npm" ls -g --depth=0 --json </dev/null 2>/dev/null || true)"
    if [ -z "$_ASCENDO_NPM_LS_CACHE" ]; then _ASCENDO_NPM_LS_CACHE="{}"; fi
    _ASCENDO_NPM_LS_CACHED=1
}

# Installed version of one global package (or empty if not installed).
# Uses the cached `npm ls -g` JSON populated by
# ``ascendo_npm_prime_installed_cache`` to avoid running npm once per
# manifest entry (would be ~9 npm spawns for the default list).
ascendo_npm_installed_version() {
    local _pkg="$1"
    [ -z "$_pkg" ] && return 0
    if [ "$_ASCENDO_NPM_LS_CACHED" = "0" ]; then
        ascendo_npm_prime_installed_cache
    fi
    if ! command -v jq >/dev/null 2>&1; then return 0; fi
    printf '%s' "$_ASCENDO_NPM_LS_CACHE" \
        | jq -r --arg pkg "$_pkg" '.dependencies[$pkg].version // empty' \
              </dev/null 2>/dev/null \
        || true
}

# Latest published version of one package (registry query).
# `npm view` is per-call (no cheap batch API). Stdin pinned to /dev/null
# to keep parent pipes intact.
ascendo_npm_latest_version() {
    local _pkg="$1"
    local _npm
    _npm="$(ascendo_npm_npm_bin)"
    if [ -z "$_npm" ] || [ -z "$_pkg" ]; then return 0; fi
    "$_npm" view "$_pkg" version </dev/null 2>/dev/null || true
}

# Installed bun version (or empty).
ascendo_npm_bun_installed_version() {
    local _bun
    _bun="$(ascendo_npm_bun_bin)"
    if [ -z "$_bun" ]; then return 0; fi
    "$_bun" --version 2>/dev/null || true
}

# Latest bun version (GitHub releases redirect — fast HEAD via curl).
# Returns empty on network failure; the script handles that gracefully.
ascendo_npm_bun_latest_version() {
    if ! command -v curl >/dev/null 2>&1; then return 0; fi
    # Follow the latest-release redirect; the Location header points at
    # /releases/tag/bun-vX.Y.Z. Strip prefix and emit X.Y.Z.
    curl -sSI -L 'https://github.com/oven-sh/bun/releases/latest' 2>/dev/null \
        | awk 'BEGIN{IGNORECASE=1} /^location:/ {url=$2}
               END {n=split(url, a, "/"); v=a[n]; sub(/^bun-v/,"",v); sub(/\r$/,"",v); print v}' \
        || true
}

# Installed Node version (or empty).
ascendo_npm_node_installed_version() {
    local _node
    _node="$(ascendo_npm_node_bin)"
    if [ -z "$_node" ]; then return 0; fi
    "$_node" --version 2>/dev/null | sed 's/^v//' || true
}

# Latest Node LTS version via `n` (when installed) — best-effort.
ascendo_npm_node_latest_version() {
    local _n="$(ascendo_npm_n_prefix)/bin/n"
    if [ ! -x "$_n" ]; then return 0; fi
    N_PREFIX="$(ascendo_npm_n_prefix)" "$_n" --lts 2>/dev/null | head -n1 || true
}
