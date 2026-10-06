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
#   ascendo_npm_bootstrap_allowed       only-installed gate (env or --filter)
#   ascendo_npm_sanitize_output         redact tokens/URL credentials in tails
#   ascendo_npm_native_path_supported   realpath is the vendor's own install
#   ascendo_npm_opencode_*              OpenCode native-first helpers
#   ascendo_npm_repair_broken_opencode  repair an existing npm-12 opencode stub
# =============================================================================
# shellcheck shell=bash

[ -n "${_ASCENDO_PROC_LOADED:-}" ] || . "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/ascendo_proc.sh"

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
# ASCENDO_NPM_MANIFEST (test seam) overrides the shipped manifest so tests can
# drive check/apply/verify with a one-row manifest and fake CLIs.
ascendo_npm_manifest_path() {
    if [ -n "${ASCENDO_NPM_MANIFEST:-}" ]; then
        printf '%s' "$ASCENDO_NPM_MANIFEST"
        return 0
    fi
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
#   * If installed major is STRICTLY GREATER than the latest LTS major,
#     the user is genuinely ahead on the Current line. Return the latest
#     Current release (so display matches reality).
#   * Otherwise (installed major <= latest LTS major — on the active LTS
#     line or an older line) return the latest LTS. A user sitting on the
#     current LTS (installed major == LTS major) must NOT be pushed onto
#     the pre-LTS Current line: apply only ever installs the picker's
#     target, so a Current target it can't keep would verify-fail every
#     run (Sesja 89). Use `-gt`, not `-ge`.
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
                if [ "$_installed_major" -gt "$_lts_major" ] 2>/dev/null; then
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
        if [ "$_installed_major" -gt "$_lts_major" ] 2>/dev/null; then
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

# -- native-installer helpers (ported from macOS_updates 2026-08-24) --------
# Standalone vendor install scripts drop binaries in ~/.local/bin. The
# previous npm-global stubs (especially Claude Code) shadowed the real
# binary and reported a version the user never ran.

ascendo_npm_local_bin() {
    printf '%s/.local/bin' "${HOME}"
}

# Vendor install URL for a native-installer command name.
# Empty stdout + return 1 when the command is unknown.
ascendo_npm_native_install_url() {
    case "$1" in
        claude) printf '%s\n' "https://claude.ai/install.sh" ;;
        codex)  printf '%s\n' "https://chatgpt.com/codex/install.sh" ;;
        agy)    printf '%s\n' "https://antigravity.google/cli/install.sh" ;;
        agent)  printf '%s\n' "https://cursor.com/install" ;;
        opencode) printf '%s\n' "https://opencode.ai/install" ;;
        *) return 1 ;;
    esac
}

# Resolve a native CLI: ~/.local/bin first, then PATH. Never the
# npm-global prefix — that is where the broken stubs used to live.
ascendo_npm_native_command_path() {
    local _cmd="$1"
    local _p
    _p="$(ascendo_npm_local_bin)/$_cmd"
    if [ -e "$_p" ] && [ -x "$_p" ]; then
        printf '%s' "$_p"
        return 0
    fi
    command -v "$_cmd" 2>/dev/null || true
}

# Pick the version token out of one `--version` output line, per CLI:
#   claude-code  "2.1.5 (Claude Code)"  -> first token
#   codex-cli    "codex-cli 0.153.4"    -> last token (first is the product name)
#   others       first token when it carries a digit, else the last token
# A leading "v" is stripped. No digit check here (see report_cli_version_or_fail).
_ascendo_npm_native_parse_version() {
    local _display="$1" _line="$2" _first _last _v
    _first="$(printf '%s' "$_line" | awk '{print $1}')"
    _last="$(printf '%s' "$_line" | awk '{print $NF}')"
    case "$_display" in
        claude-code) _v="$_first" ;;
        codex-cli)   _v="$_last" ;;
        *)
            case "$_first" in
                *[0-9]*) _v="$_first" ;;
                *)       _v="$_last" ;;
            esac
            ;;
    esac
    printf '%s' "$_v" | sed 's/^v//'
}

# Installed version of a native-installer CLI (or empty if missing).
# display_name matches config/npm_global_clis.txt.
ascendo_npm_native_installed_version() {
    local _display="$1"
    local _cmd="$2"
    local _path _line=""
    _path="$(ascendo_npm_native_command_path "$_cmd")"
    [ -n "$_path" ] || return 0
    case "$_display" in
        claude-code) _line="$("$_path" -v </dev/null 2>/dev/null | head -1)" ;;
        *)           _line="$("$_path" --version </dev/null 2>/dev/null | head -1)" ;;
    esac
    _ascendo_npm_native_parse_version "$_display" "$_line"
}

# Honest verification: exit 0 from an installer/updater is not success. The
# binary must run `--version` (or `-v`) and print a digit-bearing version; a
# product name alone (`codex-cli`) is not a version. Prints the version and
# returns 0, or prints `?` and returns 1.
ascendo_npm_report_cli_version_or_fail() {   # <display> <path>
    local _display="$1" _path="$2" _line="" _v
    if [ ! -x "$_path" ]; then printf '?\n'; return 1; fi
    if _line="$("$_path" --version </dev/null 2>/dev/null)"; then
        :
    elif _line="$("$_path" -v </dev/null 2>/dev/null)"; then
        :
    else
        printf '?\n'; return 1
    fi
    _line="$(printf '%s\n' "$_line" | head -1)"
    _v="$(_ascendo_npm_native_parse_version "$_display" "$_line")"
    case "$_v" in
        *[0-9]*) printf '%s\n' "$_v"; return 0 ;;
    esac
    printf '?\n'; return 1
}

# Self-update subcommand of an already-installed native CLI.
ascendo_npm_native_update_cmd() {
    case "$1" in
        claude|agy|codex|agent) printf 'update' ;;
        *) printf '' ;;
    esac
}

# Extra environment (KEY=VAL, unquoted on purpose) for the vendor installer /
# updater. codex prompts on /dev/tty without CODEX_NON_INTERACTIVE=1.
ascendo_npm_native_installer_env() {
    case "$1" in
        codex) printf 'CODEX_NON_INTERACTIVE=1' ;;
        *)     printf '' ;;
    esac
}

# Per-vendor arguments for the downloaded bootstrap script (never a shared one).
ascendo_npm_native_bootstrap_args() {
    case "$1" in
        claude) printf 'latest' ;;
        codex)  printf -- '--release latest' ;;
        *)      printf '' ;;
    esac
}

# Wall-clock bound (seconds) for one installer / `<cmd> update` run. Default
# 360; env ASCENDO_NATIVE_INSTALLER_TIMEOUT (legacy: ASCENDO_NPM_NATIVE_INSTALL_
# TIMEOUT, MAC_UPDATE_NATIVE_INSTALLER_TIMEOUT). Non-numeric or below the floor
# (60 s; ASCENDO_NATIVE_INSTALLER_TIMEOUT_FLOOR is a test seam) => 360.
ascendo_npm_native_installer_timeout() {
    local _raw="${ASCENDO_NATIVE_INSTALLER_TIMEOUT:-${ASCENDO_NPM_NATIVE_INSTALL_TIMEOUT:-${MAC_UPDATE_NATIVE_INSTALLER_TIMEOUT:-360}}}"
    local _floor="${ASCENDO_NATIVE_INSTALLER_TIMEOUT_FLOOR:-60}"
    case "$_floor" in ''|*[!0-9]*) _floor=60 ;; esac
    case "$_raw" in ''|*[!0-9]*) echo 360; return 0 ;; esac
    if [ "$_raw" -lt "$_floor" ]; then echo 360; else echo "$_raw"; fi
}

# Download a vendor install script to a file: https only (also on redirects),
# size cap, bounded time, non-empty. Never executes anything. rc 1 on failure
# and no dest is left behind.
ascendo_npm_fetch_installer() {   # url dest [timeout_s=60] [max_bytes=10485760]
    local _url="$1" _dest="$2" _t="${3:-60}" _max="${4:-10485760}" _tmp
    [ -n "$_url" ] && [ -n "$_dest" ] || return 1
    case "$_url" in https://*) ;; *) return 1 ;; esac
    _tmp="${_dest}.part"
    rm -f "$_tmp" "$_dest"
    curl -fsSL --proto =https --proto-redir =https --max-filesize "$_max" \
         --max-time "$_t" --retry 2 --retry-delay 1 -o "$_tmp" "$_url" \
         </dev/null 2>/dev/null || { rm -f "$_tmp"; return 1; }
    [ -s "$_tmp" ] || { rm -f "$_tmp"; return 1; }
    [ "$(wc -c < "$_tmp" | tr -d ' ')" -le "$_max" ] || { rm -f "$_tmp"; return 1; }
    mv "$_tmp" "$_dest"
}

# Colon-separated PATH prefix: ~/.local/bin, managed npm-global, managed
# node (unless ASCENDO_NVM_OWNS_NODE / MAC_UPDATE_NVM_OWNS_NODE), bun, brew.
# Mirrors macOS_updates: the managed prefix wins over nvm; native CLIs
# in ~/.local/bin win over npm stubs.
ascendo_npm_preferred_path() {
    local _local _npmg _node _bun _p _parts=""
    _local="$(ascendo_npm_local_bin)"
    _npmg="$(ascendo_npm_global_prefix)/bin"
    _node="$(ascendo_npm_n_prefix)/bin"
    _bun="$(ascendo_npm_bun_home)/bin"
    [ -d "$_local" ] && _parts="$_local"
    [ -d "$_npmg" ] && _parts="${_parts:+$_parts:}$_npmg"
    case "${ASCENDO_NVM_OWNS_NODE:-${MAC_UPDATE_NVM_OWNS_NODE:-}}" in
        1|true|yes) ;;
        *) [ -d "$_node" ] && _parts="${_parts:+$_parts:}$_node" ;;
    esac
    [ -d "$_bun" ] && _parts="${_parts:+$_parts:}$_bun"
    # ASCENDO_NPM_BREW_DIRS (colon-separated; set-but-empty = none) is a test
    # seam so a hermetic test PATH is not re-prefixed with the real Homebrew bin.
    local _brew_dirs="${ASCENDO_NPM_BREW_DIRS-/opt/homebrew/bin:/usr/local/bin}"
    local _oifs="$IFS"
    IFS=:
    for _p in $_brew_dirs; do
        [ -n "$_p" ] && [ -d "$_p" ] && _parts="${_parts:+$_parts:}$_p"
    done
    IFS="$_oifs"
    printf '%s' "$_parts"
}

# Bound a command to $1 seconds. Thin wrapper over the shared runner in
# ascendo_proc.sh (GNU timeout --kill-after, else process-group TERM->KILL).
# Exit 124 when the deadline is hit.
ascendo_npm_run_with_timeout() {
    ascendo_run_with_timeout "$@"
}

# -- only-installed gate (cli.md P5) -----------------------------------------
# A dashboard run is TTY-less and a per-app "Update" passes --filter, so an
# explicitly selected missing CLI is an explicit install request; everything
# else that is missing is skipped. ASCENDO_BOOTSTRAP_CLI=1 (legacy
# MAC_UPDATE_BOOTSTRAP_CLI) is the global opt-in.

# ascendo_npm_bootstrap_allowed <display_name>   (reads script-scope $FILTER)
ascendo_npm_bootstrap_allowed() {
    case "${ASCENDO_BOOTSTRAP_CLI:-${MAC_UPDATE_BOOTSTRAP_CLI:-0}}" in
        1|true|yes) return 0 ;;
    esac
    [ -n "${FILTER:-}" ] || return 1
    case ",${FILTER}," in
        *",$1,"*) return 0 ;;
    esac
    return 1
}

# Managed Node = $TOOLCHAIN_HOME/node (NOT a system / nvm / brew node).
ascendo_npm_managed_node_installed() { [ -x "$(ascendo_npm_n_prefix)/bin/node" ]; }

# ascendo_npm_pkg_installed <package> <command>: present in the managed prefix.
ascendo_npm_pkg_installed() {
    local _g
    _g="$(ascendo_npm_global_prefix)"
    [ -d "$_g/lib/node_modules/$1" ] || [ -x "$_g/bin/$2" ]
}

ascendo_npm_bun_managed_installed() { [ -x "$(ascendo_npm_bun_home)/bin/bun" ]; }

# -- recorded-output hygiene (cli.md P8) ---------------------------------------
# Filter for every output tail that is put into a sidecar message: auth /
# token / password lines and URL credentials are replaced by a marker, every
# other line is capped at 500 characters. (awk body from macOS_updates
# update_npm_cli.sh sanitize_npm_stderr.)
ascendo_npm_sanitize_output() {
    awk '{ lower=tolower($0)
      if (lower ~ /(_authtoken|_auth[[:space:]]*[:=]|authorization[[:space:]]*[:=]|npm_token[[:space:]]*[:=]|password[[:space:]]*[:=]|token[[:space:]]*[:=])/) { print "[REDACTED sensitive diagnostic line]"; next }
      if ($0 ~ /https?:\/\/[^[:space:]\/@]+@/) { print "[REDACTED URL credentials]"; next }
      print substr($0, 1, 500) }'
}

# -- native-origin proof + version relation (python helpers in this dir) -------
_ascendo_npm_lib_dir() {
    cd "$(dirname "${BASH_SOURCE[0]}")" && pwd
}

# ascendo_npm_native_path_supported <command> <executable>
# True only when the executable's realpath is the vendor's own install root
# (cli_duplicates.supported_native_path). An npm wrapper symlinked into
# ~/.local/bin, a Homebrew binary or an arbitrary file is NOT native.
ascendo_npm_native_path_supported() {
    local _lib
    _lib="$(_ascendo_npm_lib_dir)"
    PYTHONPATH="$_lib${PYTHONPATH:+:$PYTHONPATH}" python3 -c '
import sys
from cli_duplicates import supported_native_path
sys.exit(0 if supported_native_path(sys.argv[1], sys.argv[2], sys.argv[3]) else 1)
' "$HOME" "$1" "$2" </dev/null 2>/dev/null
}

# ascendo_npm_version_newer <a> <b>: rc 0 only when a is provably newer than b
# (brew_casks.py cmp; unparseable / empty => rc 1, i.e. never "newer").
ascendo_npm_version_newer() {
    [ -n "${1:-}" ] && [ -n "${2:-}" ] || return 1
    [ "$(python3 "$(_ascendo_npm_lib_dir)/brew_casks.py" cmp "$1" "$2" </dev/null 2>/dev/null)" = "newer" ]
}

# -- OpenCode (native-first; cli.md P6 as amended by upstream v1.5.3/v1.5.4) ---
ascendo_npm_global_bin() { printf '%s/bin' "$(ascendo_npm_global_prefix)"; }

# The vendor installer's own location (its install dir is not configurable).
ascendo_npm_opencode_native_bin() { printf '%s/.opencode/bin/opencode' "${HOME}"; }

# Existing OpenCode, in upstream's resolve order: ~/.local/bin, PATH, managed
# npm bin, ~/.opencode/bin. Empty when none exists (never bootstraps).
ascendo_npm_opencode_resolve_path() {
    local _p
    _p="$(ascendo_npm_native_command_path opencode)"
    if [ -n "$_p" ]; then printf '%s' "$_p"; return 0; fi
    for _p in "$(ascendo_npm_global_bin)/opencode" "$(ascendo_npm_opencode_native_bin)"; do
        if [ -x "$_p" ]; then printf '%s' "$_p"; return 0; fi
    done
    return 0
}

# The npm distribution may be updated by `opencode upgrade --method npm` only
# when it already exists in the managed prefix (never bootstrapped).
ascendo_npm_opencode_npm_wrapper_exists() {
    [ -d "$(ascendo_npm_global_prefix)/lib/node_modules/opencode-ai" ] \
        && [ -x "$(ascendo_npm_global_bin)/opencode" ]
}

# Resolved command of a `self-update` row (empty when it is not installed).
ascendo_npm_self_update_command_path() {   # cmd
    case "$1" in
        opencode) ascendo_npm_opencode_resolve_path ;;
        *)        ascendo_npm_native_command_path "$1" ;;
    esac
}

# Version of a `self-update` row's resolved command, read by RUNNING it.
# Empty when the command is absent or does not print a version (broken stub).
ascendo_npm_self_update_installed_version() {   # display cmd
    local _path _v
    _path="$(ascendo_npm_self_update_command_path "$2")"
    [ -n "$_path" ] || return 0
    if _v="$(ascendo_npm_report_cli_version_or_fail "$1" "$_path")"; then
        printf '%s' "$_v"
    fi
    return 0
}

# Repair an already-installed OpenCode npm stub in the managed prefix only
# (npm 12 skips the postinstall that fetches the real binary). Ported from
# macOS_updates native_installers.sh repair_broken_opencode.
# Returns 0 repaired, 1 failed, 2 not installed (never bootstrap here).
ascendo_npm_repair_broken_opencode() {   # prefix binary
    local _prefix="$1" _binary="$2" _npm_bin="" _rc=1 _pkg _node
    [ -x "$_binary" ] || return 2
    { [ -n "$_prefix" ] && [ -d "$_prefix" ]; } || return 1
    _npm_bin="$_prefix/bin/npm"
    [ -x "$_npm_bin" ] || _npm_bin="$(ascendo_npm_npm_bin)"
    [ -n "$_npm_bin" ] || return 1
    # npm 12's allow-scripts list blocks the postinstall even with
    # ignore-scripts=false; scope the exception to this child (no ~/.npmrc).
    NPM_CONFIG_IGNORE_SCRIPTS=false \
        _ascendo_npm_invoke "$_npm_bin" install -g --prefix "$_prefix" \
        --allow-scripts=opencode-ai opencode-ai@latest </dev/null
    _rc=$?
    if [ "$_rc" -eq 0 ] && "$_binary" --version </dev/null >/dev/null 2>&1; then
        return 0
    fi
    _pkg="$_prefix/lib/node_modules/opencode-ai"
    if [ -x "$_prefix/bin/node" ]; then _node="$_prefix/bin/node"; else _node="$(ascendo_npm_node_bin)"; fi
    if [ -n "$_node" ] && [ -f "$_pkg/postinstall.mjs" ]; then
        (cd "$_pkg" && "$_node" postinstall.mjs </dev/null) || return 1
        return 0
    fi
    return "$_rc"
}
