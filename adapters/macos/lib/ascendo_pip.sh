#!/usr/bin/env bash
# =============================================================================
# adapters/macos/lib/ascendo_pip.sh -- pip / Python global CLI helpers
# =============================================================================
# Sourced by adapters/macos/scripts/pip/*.sh. Bash 3.2 compatible (no
# associative arrays, no mapfile, no readarray).
#
# Provides:
#   ascendo_pip_pip_bin                  prints path to active pip
#   ascendo_pip_python_bin               prints path to active python
#   ascendo_pip_outdated_json            emits `pip list --outdated --json`
#   ascendo_pip_prime_installed_cache    populates ls cache (one pip spawn)
#   ascendo_pip_installed_json           prints cached installed json
#   ascendo_pip_installed_version <pkg>  prints installed version of one pkg
#   ascendo_pip_latest_version <pkg>     queries PyPI JSON for latest
#   ascendo_pip_manifest_path            prints path to pip_global_clis.txt
#   ascendo_pip_manifest_lines           strips comments + blanks
#   ascendo_pip_break_system_packages_arg
#                                        emits --break-system-packages when
#                                        active pip is brew Python's pip
#                                        (PEP 668 guard); empty otherwise
#   ascendo_pip_user_install_arg         emits --user when active pip is a
#                                        plain system Python's pip (where
#                                        --user is the right idiom);
#                                        empty when pip is brew/pipx/venv
# =============================================================================
# shellcheck shell=bash

# -- binary discovery -------------------------------------------------------
# Resolution order (highest precedence first):
#   1. $ASCENDO_PYTHON_PIP_OVERRIDE  — test fixtures + power users
#   2. ~/.local/share/mac-update/python-tools/bin/pip — toolchain-local
#   3. /opt/homebrew/bin/pip3        — Apple Silicon Homebrew (Sesja 53)
#   4. /usr/local/bin/pip3           — Intel Homebrew + system installs
#   5. pip3 on PATH                  — fallback (rejects Xcode shim explicitly)
#   6. pip on PATH                   — last-ditch
#   7. ""  (caller treats as "pip unavailable")
#
# Sesja 53 fix: Tauri-launched dashboard inherits launchctl PATH which on
# macOS is just /usr/bin:/bin:/usr/sbin:/sbin. `command -v pip3` then
# resolves to /usr/bin/pip3 — the Xcode-shipped Python 3.9 pip stub.
# Operator saw all their pip-managed CLIs (poetry, ruff, mypy, etc.)
# silently install into ~/Library/Python/3.9/bin/ instead of the brew
# Python 3.14 site-packages where `ascendo` itself runs. Probing the
# canonical brew paths first AND rejecting the Xcode shim avoids the
# drift entirely.
#
# Returns path on stdout (or empty when nothing resolves).
ascendo_pip_pip_bin() {
    if [ -n "${ASCENDO_PYTHON_PIP_OVERRIDE:-}" ]; then
        printf '%s' "$ASCENDO_PYTHON_PIP_OVERRIDE"
        return 0
    fi
    local _toolchain_pip
    _toolchain_pip="${MAC_UPDATE_TOOLCHAIN_HOME:-$HOME/.local/share/mac-update}/python-tools/bin/pip"
    if [ -x "$_toolchain_pip" ]; then
        printf '%s' "$_toolchain_pip"
        return 0
    fi
    # Apple Silicon brew first, Intel brew second. These exist only on
    # machines where brew installed Python; on others we fall through.
    if [ -x "/opt/homebrew/bin/pip3" ]; then
        printf '%s' "/opt/homebrew/bin/pip3"; return 0
    fi
    if [ -x "/usr/local/bin/pip3" ]; then
        printf '%s' "/usr/local/bin/pip3"; return 0
    fi
    local _bin
    _bin="$(command -v pip3 2>/dev/null || true)"
    # Reject the Xcode shim. /usr/bin/pip3 always targets Apple's framework
    # Python 3.9, which is unmanaged by ascendo. Pip-installs into it leak
    # into ~/Library/Python/3.9/bin and never surface back to the operator.
    case "$_bin" in
        /usr/bin/pip3) _bin="" ;;
    esac
    if [ -n "$_bin" ]; then printf '%s' "$_bin"; return 0; fi
    _bin="$(command -v pip 2>/dev/null || true)"
    case "$_bin" in
        /usr/bin/pip) _bin="" ;;
    esac
    if [ -n "$_bin" ]; then printf '%s' "$_bin"; return 0; fi
    printf ''
}

# Companion to ascendo_pip_pip_bin: locate the matching python interpreter.
# Same Sesja 53 fix — prefer brew Python over Xcode's /usr/bin/python3 shim.
ascendo_pip_python_bin() {
    if [ -n "${ASCENDO_PYTHON_BIN_OVERRIDE:-}" ]; then
        printf '%s' "$ASCENDO_PYTHON_BIN_OVERRIDE"
        return 0
    fi
    if [ -x "/opt/homebrew/bin/python3" ]; then
        printf '%s' "/opt/homebrew/bin/python3"; return 0
    fi
    if [ -x "/usr/local/bin/python3" ]; then
        printf '%s' "/usr/local/bin/python3"; return 0
    fi
    local _bin
    _bin="$(command -v python3 2>/dev/null || true)"
    case "$_bin" in
        /usr/bin/python3) _bin="" ;;   # Xcode shim — reject
    esac
    if [ -n "$_bin" ]; then printf '%s' "$_bin"; return 0; fi
    _bin="$(command -v python 2>/dev/null || true)"
    case "$_bin" in
        /usr/bin/python) _bin="" ;;
    esac
    if [ -n "$_bin" ]; then printf '%s' "$_bin"; return 0; fi
    printf ''
}

# -- pip-flavour detection --------------------------------------------------
# Probe the active pip and return one of:
#   brew     — pip is shipped by a Homebrew-managed Python (PEP 668 active)
#   user     — pip is a plain system / framework python; --user is the idiom
#   venv     — pip lives in a virtualenv (no extra flag needed)
#   unknown  — nothing matched
#
# Used by ascendo_pip_break_system_packages_arg + ascendo_pip_user_install_arg.
# Bash 3.2: no functions return strings via stdout other than printf.
_ascendo_pip_flavour() {
    local _pip="$1"
    if [ -z "$_pip" ]; then printf 'unknown'; return; fi
    case "$_pip" in
        */homebrew/*|*/Homebrew/*|/opt/homebrew/*|/usr/local/Homebrew/*|/usr/local/Cellar/*)
            printf 'brew'
            return
            ;;
    esac
    # `pip --version` prints e.g.
    #   pip 24.0 from /opt/homebrew/lib/python3.12/site-packages/pip (python 3.12)
    # If the path contains homebrew/Cellar/Caskroom, treat as brew.
    local _ver
    _ver="$("$_pip" --version 2>/dev/null </dev/null || true)"
    case "$_ver" in
        *homebrew*|*Homebrew*|*Cellar*|*Caskroom*)
            printf 'brew'
            return
            ;;
        *.virtualenvs*|*\ \(venv*|*/venv/*|*/.venv/*)
            printf 'venv'
            return
            ;;
    esac
    # Fallback: assume system Python where --user is appropriate.
    printf 'user'
}

# Emits `--break-system-packages` when a brew-managed pip is active,
# empty otherwise. Caller does:
#   args=($pip install -U $pkg $(ascendo_pip_break_system_packages_arg))
ascendo_pip_break_system_packages_arg() {
    local _pip
    _pip="$(ascendo_pip_pip_bin)"
    case "$(_ascendo_pip_flavour "$_pip")" in
        brew) printf -- '--break-system-packages' ;;
        *) printf '' ;;
    esac
}

# Emits `--user` when active pip is a plain system Python's pip; empty
# when the active pip is brew/venv (where --user either doesn't apply or
# brew explicitly ignores it).
ascendo_pip_user_install_arg() {
    local _pip
    _pip="$(ascendo_pip_pip_bin)"
    case "$(_ascendo_pip_flavour "$_pip")" in
        user) printf -- '--user' ;;
        *) printf '' ;;
    esac
}

# -- pip queries ------------------------------------------------------------

# `pip list --outdated --format=json --disable-pip-version-check` to stdout.
# Empty `[]` on failure or no outdated. Stdin pinned to /dev/null so this
# never drains a parent pipe (e.g. `manifest | while read ...; do call ...`).
ascendo_pip_outdated_json() {
    local _pip
    _pip="$(ascendo_pip_pip_bin)"
    if [ -z "$_pip" ]; then printf '[]'; return 0; fi
    local _out
    _out="$("$_pip" list --outdated --format=json --disable-pip-version-check </dev/null 2>/dev/null || true)"
    if [ -z "$_out" ]; then printf '[]'; else printf '%s' "$_out"; fi
}

# Cache for `pip list --format=json`. Single spawn per script-run.
_ASCENDO_PIP_LIST_CACHE=""
_ASCENDO_PIP_LIST_CACHED=0

ascendo_pip_prime_installed_cache() {
    local _pip
    _pip="$(ascendo_pip_pip_bin)"
    if [ -z "$_pip" ]; then
        _ASCENDO_PIP_LIST_CACHE="[]"
        _ASCENDO_PIP_LIST_CACHED=1
        return 0
    fi
    _ASCENDO_PIP_LIST_CACHE="$("$_pip" list --format=json --disable-pip-version-check </dev/null 2>/dev/null || true)"
    if [ -z "$_ASCENDO_PIP_LIST_CACHE" ]; then _ASCENDO_PIP_LIST_CACHE="[]"; fi
    _ASCENDO_PIP_LIST_CACHED=1
}

# Print the cached `pip list --format=json` (priming on demand).
ascendo_pip_installed_json() {
    if [ "$_ASCENDO_PIP_LIST_CACHED" = "0" ]; then
        ascendo_pip_prime_installed_cache
    fi
    printf '%s' "$_ASCENDO_PIP_LIST_CACHE"
}

# Installed version of one package (or empty if not installed). Case-
# insensitive PyPI-name match — pip's own canonicalisation is more
# elaborate (PEP 503) but ascii_downcase covers every standard manifest
# package we ship.
#
# CRITICAL: do NOT add `</dev/null` to jq's stdin here — jq IS the
# consumer of the printf-piped cache. Adding /dev/null would make jq
# read nothing and emit "" for every lookup. The </dev/null guard is
# only for commands (pip, curl) that would otherwise drain the parent
# loop's stdin.
ascendo_pip_installed_version() {
    local _pkg="$1"
    [ -z "$_pkg" ] && return 0
    if [ "$_ASCENDO_PIP_LIST_CACHED" = "0" ]; then
        ascendo_pip_prime_installed_cache
    fi
    if ! command -v jq >/dev/null 2>&1; then return 0; fi
    printf '%s' "$_ASCENDO_PIP_LIST_CACHE" \
        | jq -r --arg p "$_pkg" \
            '.[] | select(.name | ascii_downcase == ($p | ascii_downcase)) | .version' \
            2>/dev/null \
        | head -n1 \
        || true
}

# Latest published version of one package, queried from PyPI's JSON API:
#   https://pypi.org/pypi/<pkg>/json -> .info.version
# 5s curl timeout. Empty on network failure. Stdin pinned to /dev/null
# to keep parent pipe loops intact.
ascendo_pip_latest_version() {
    local _pkg="$1"
    [ -z "$_pkg" ] && return 0
    if ! command -v curl >/dev/null 2>&1; then return 0; fi
    local _url="https://pypi.org/pypi/${_pkg}/json"
    local _json
    _json="$(curl -fsSL --max-time 5 "$_url" </dev/null 2>/dev/null || true)"
    if [ -z "$_json" ]; then return 0; fi
    if ! command -v jq >/dev/null 2>&1; then return 0; fi
    printf '%s' "$_json" \
        | jq -r '.info.version // empty' 2>/dev/null \
        || true
}

# -- manifest loader --------------------------------------------------------
# adapters/macos/config/pip_global_clis.txt — pipe-delimited:
#   display_name|package_name|method|description
# Lines starting with # and blank lines are skipped.
ascendo_pip_manifest_path() {
    local _self_dir
    _self_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    if [ -n "${ASCENDO_PIP_MANIFEST_OVERRIDE:-}" ]; then
        printf '%s' "$ASCENDO_PIP_MANIFEST_OVERRIDE"
        return 0
    fi
    printf '%s/../config/pip_global_clis.txt' "$_self_dir"
}

# Echoes manifest lines (one per pkg, # and blank stripped). Strips
# trailing inline-comment text after the last column too.
ascendo_pip_manifest_lines() {
    local _path
    _path="$(ascendo_pip_manifest_path)"
    if [ ! -f "$_path" ]; then return 0; fi
    sed -e 's/[[:space:]]*#.*$//' -e '/^[[:space:]]*$/d' "$_path"
}
