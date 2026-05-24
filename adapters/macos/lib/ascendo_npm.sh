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

# Path to the user's ~/.npmrc (override-able for tests).
ascendo_npm_npmrc_path() {
    printf '%s' "${ASCENDO_NPMRC_PATH:-$HOME/.npmrc}"
}

# Strip `prefix=` and `globalconfig=` lines from ~/.npmrc.
#
# Why: writing the npm-global prefix to ~/.npmrc is incompatible with
# nvm — every nvm shell-init logs a warning and refuses to load the
# Node it would otherwise pick. We use NPM_CONFIG_PREFIX env var
# instead (precedence: env > .npmrc), but a stale prefix line written
# by an earlier Ascendo run, by `npm config set prefix` issued
# manually, or by a third-party tool would still trigger the warning.
# Mirror the legacy macOS toolkit's `remove_npmrc_prefix` (cf.
# Ascendo/update_npm_cli.sh:77-89) and scrub on every apply.
#
# Idempotent and safe when ~/.npmrc is absent.
ascendo_npm_scrub_npmrc() {
    local _npmrc
    _npmrc="$(ascendo_npm_npmrc_path)"
    [ -f "$_npmrc" ] || return 0
    local _tmp
    _tmp="$(mktemp "${TMPDIR:-/tmp}/ascendo_npmrc.XXXXXX")" || return 1
    # `grep -Ev` exits 1 when ALL input lines match the filter (i.e.
    # the result would be empty); treat that as success so the empty
    # output still replaces the original file.
    /usr/bin/grep -Ev '^[[:space:]]*(prefix|globalconfig)[[:space:]]*=' \
        "$_npmrc" > "$_tmp" 2>/dev/null || true
    if ! mv "$_tmp" "$_npmrc"; then
        rm -f "$_tmp"
        return 1
    fi
    # An empty .npmrc is functionally equivalent to no .npmrc; remove
    # so subsequent tooling (nvm, npm itself) doesn't even open it.
    [ -s "$_npmrc" ] || rm -f "$_npmrc"
    return 0
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

# _ascendo_npm_invoke <npm_bin> <args...>
# Run npm via its sibling node binary AND with our toolchain's
# NPM_CONFIG_PREFIX exported. Two bugs this fixes (Sesja 48):
#
#   1. npm script uses `#!/usr/bin/env node` so it picks up whichever
#      `node` is first on PATH. On boxes with nvm/asdf/volta installed,
#      npm gets executed by a *different* node version than the one it
#      was bundled with — `wrapModuleLoad` cjs/loader errors on every
#      `npm install -g <pkg>` call.
#
#   2. Without explicit NPM_CONFIG_PREFIX, npm picks up its prefix from
#      ~/.npmrc or its own bundled config (which on nvm boxes points
#      at nvm's per-version prefix). `npm ls -g` then returns empty
#      for our toolchain-installed packages because npm is looking at
#      the wrong prefix entirely. Setting NPM_CONFIG_PREFIX inside this
#      helper means every check / apply / verify call is consistent.
_ascendo_npm_invoke() {
    local _npm="$1"; shift
    [ -z "$_npm" ] && return 1
    # Pin the prefix to our toolchain so `npm ls -g`, `npm install -g`,
    # `npm cache clean` etc. all agree on where global packages live.
    # Per-call, not exported, so we don't poison sibling commands.
    local _prefix="$(ascendo_npm_global_prefix)"
    # Resolve sibling node: <prefix>/bin/node lives next to <prefix>/bin/npm.
    local _node_dir="$(dirname "$_npm")"
    local _node="$_node_dir/node"
    if [ -x "$_node" ] && [ -f "$_npm" ]; then
        # NPM_BIN may be a symlink to lib/node_modules/npm/bin/npm-cli.js
        local _npm_cli="$_npm"
        if [ -L "$_npm" ]; then
            local _t
            _t="$(/usr/bin/readlink "$_npm")"
            case "$_t" in
                /*) _npm_cli="$_t" ;;
                *)  _npm_cli="$_node_dir/$_t" ;;
            esac
        fi
        NPM_CONFIG_PREFIX="$_prefix" "$_node" "$_npm_cli" "$@"
        return $?
    fi
    NPM_CONFIG_PREFIX="$_prefix" "$_npm" "$@"
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
    _out="$(_ascendo_npm_invoke "$_npm" outdated -g --json </dev/null 2>/dev/null || true)"
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
    _ASCENDO_NPM_LS_CACHE="$(_ascendo_npm_invoke "$_npm" ls -g --depth=0 --json </dev/null 2>/dev/null || true)"
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
    # NOTE: do NOT redirect jq's stdin from /dev/null here — it must read
    # the piped cache JSON. The </dev/null guard belongs on commands that
    # would otherwise drain a parent loop's stdin (npm/curl), not on jq
    # when jq IS the consumer of the pipe.
    printf '%s' "$_ASCENDO_NPM_LS_CACHE" \
        | jq -r --arg pkg "$_pkg" '.dependencies[$pkg].version // empty' \
              2>/dev/null \
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
    _ascendo_npm_invoke "$_npm" view "$_pkg" version </dev/null 2>/dev/null || true
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

# Latest applicable Node version. Tracks BOTH tracks (LTS + Current)
# and returns whichever is appropriate for what's installed:
#
#   * If installed major >= latest LTS major, the user is on Current.
#     Return the latest Current release (so display matches reality).
#   * Otherwise return latest LTS (stable users see the LTS line).
#
# Pre-fix this helper always returned latest LTS. On a box running
# Node 26.1.0 (Current), the check sidecar showed cur=26.1.0,
# tgt=24.15.0 (latest LTS) and classified as up_to_date because
# installed > target. Visually confusing — looked like "installed
# newer than candidate" bug.
#
# Tries `n` first (fast, local), falls back to nodejs.org/dist/index.json.
# Empty stdout when both paths fail.
ascendo_npm_node_latest_version() {
    # Resolve installed major so we can pick the right track.
    local _installed _installed_major _lts _current
    _installed="$(ascendo_npm_node_installed_version 2>/dev/null)"
    _installed_major="${_installed%%.*}"

    local _n="$(ascendo_npm_n_prefix)/bin/n"
    if [ -x "$_n" ]; then
        _lts="$(N_PREFIX="$(ascendo_npm_n_prefix)" "$_n" --lts 2>/dev/null | head -n1 || true)"
        _current="$(N_PREFIX="$(ascendo_npm_n_prefix)" "$_n" --latest 2>/dev/null | head -n1 || true)"
        # Pick by installed-major heuristic. If we don't know the
        # installed version (fresh box, no node), fall back to LTS.
        if [ -n "$_lts" ] || [ -n "$_current" ]; then
            if [ -n "$_installed_major" ] && [ -n "$_lts" ] && [ -n "$_current" ]; then
                local _lts_major="${_lts%%.*}"
                if [ "$_installed_major" -ge "$_lts_major" ] 2>/dev/null; then
                    printf '%s' "$_current"
                else
                    printf '%s' "$_lts"
                fi
                return 0
            fi
            # Only one of the two probes returned data — use it.
            [ -n "$_lts" ] && { printf '%s' "$_lts"; return 0; }
            [ -n "$_current" ] && { printf '%s' "$_current"; return 0; }
        fi
    fi
    # Network fallback. Don't fail if curl/jq missing.
    if ! command -v curl >/dev/null 2>&1; then return 0; fi
    if ! command -v jq >/dev/null 2>&1; then return 0; fi
    # Probe nodejs.org for BOTH the latest LTS and the absolute latest
    # release. Same picker logic as the `n`-based branch above.
    local _json
    _json="$(curl -fsSL --max-time 5 'https://nodejs.org/dist/index.json' 2>/dev/null || true)"
    [ -z "$_json" ] && return 0
    _lts="$(printf '%s' "$_json" | jq -r 'map(select(.lts != false))[0].version // empty' 2>/dev/null | sed 's/^v//')"
    _current="$(printf '%s' "$_json" | jq -r '.[0].version // empty' 2>/dev/null | sed 's/^v//')"
    if [ -n "$_installed_major" ] && [ -n "$_lts" ] && [ -n "$_current" ]; then
        local _lts_major="${_lts%%.*}"
        if [ "$_installed_major" -ge "$_lts_major" ] 2>/dev/null; then
            printf '%s' "$_current"
        else
            printf '%s' "$_lts"
        fi
        return 0
    fi
    [ -n "$_lts" ] && { printf '%s' "$_lts"; return 0; }
    [ -n "$_current" ] && { printf '%s' "$_current"; return 0; }
    return 0
}
