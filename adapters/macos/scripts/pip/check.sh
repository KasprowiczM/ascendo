#!/usr/bin/env bash
# =============================================================================
# adapters/macos/scripts/pip/check.sh -- read-only pip global-CLI inventory
# =============================================================================
# For each entry in adapters/macos/config/pip_global_clis.txt:
#   * pip method: install state via cached `pip list --format=json`,
#     latest via PyPI's https://pypi.org/pypi/<pkg>/json endpoint.
# Items emit as up_to_date when installed==latest, planned when
# installed<latest, missing when not installed at all.
#
# Read-only. No mutation. Never invokes sudo. Bash 3.2 compatible.
# =============================================================================
set -o pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ADAPTER_LIB="$SCRIPT_DIR/../../lib"

# shellcheck source=../../lib/ascendo_json.sh
. "$ADAPTER_LIB/ascendo_json.sh"
# shellcheck source=../../lib/ascendo_pip.sh
. "$ADAPTER_LIB/ascendo_pip.sh"

# -- arg parsing ---------------------------------------------------------------
RUN_ID=""; TRIGGER=""; PROFILE_NAME=""; OUTPUT_DIR=""; DRY_RUN="false"; FILTER=""
while [ $# -gt 0 ]; do
    case "$1" in
        --run-id)     RUN_ID="$2";       shift 2 ;;
        --trigger)    TRIGGER="$2";      shift 2 ;;
        --profile)    PROFILE_NAME="$2"; shift 2 ;;
        --output-dir) OUTPUT_DIR="$2";   shift 2 ;;
        --dry-run)    DRY_RUN="true";    shift ;;
        --filter)     FILTER="$2";       shift 2 ;;
        *) printf 'check.sh: unknown arg: %s\n' "$1" >&2; exit 2 ;;
    esac
done
if [ -z "$RUN_ID" ] || [ -z "$TRIGGER" ] || [ -z "$PROFILE_NAME" ] || [ -z "$OUTPUT_DIR" ]; then
    printf 'check.sh: missing required args\n' >&2
    exit 2
fi

# -- host info -----------------------------------------------------------------
HOST_NAME="$(scutil --get LocalHostName 2>/dev/null || hostname -s 2>/dev/null || echo unknown)"
HOST_OS="macos"
HOST_OS_VERSION="$(sw_vers -productVersion 2>/dev/null || echo unknown)"
HOST_ARCH="$(uname -m 2>/dev/null || echo unknown)"
HOST_USER="$(whoami 2>/dev/null || echo unknown)"
if [ "${EUID:-$(id -u)}" -eq 0 ]; then HOST_IS_ELEVATED="true"; else HOST_IS_ELEVATED="false"; fi

# -- tool info -----------------------------------------------------------------
PIP_BIN="$(ascendo_pip_pip_bin)"
TOOL_VERSION="unknown"
if [ -n "$PIP_BIN" ]; then
    # `pip --version` prints e.g. `pip 24.0 from .../pip (python 3.12)`.
    # Take the second token as the version number.
    TOOL_VERSION="$("$PIP_BIN" --version 2>/dev/null </dev/null | awk '{print $2}' || echo unknown)"
    [ -z "$TOOL_VERSION" ] && TOOL_VERSION="unknown"
fi

# -- init sidecar --------------------------------------------------------------
json_init "check" "pip" "$RUN_ID" "$TRIGGER" "$PROFILE_NAME" \
          "pip" "$TOOL_VERSION" \
          "$HOST_NAME" "$HOST_OS" "$HOST_OS_VERSION" "$HOST_ARCH" \
          "$HOST_USER" "$HOST_IS_ELEVATED"
trap 'json_save_on_exit "$OUTPUT_DIR"' EXIT

# -- filter helper -------------------------------------------------------------
in_filter() {
    [ -z "$FILTER" ] && return 0
    case ",$FILTER," in
        (*",$1,"*) return 0 ;;
        (*) return 1 ;;
    esac
}

# -- classify (installed, latest) -> status ------------------------------------
# Returns one of: missing | planned | up_to_date.
# When installed sorts AFTER latest in semver order (e.g. user runs a
# pre-release ahead of stable), report up_to_date instead of planned —
# applying would be a no-op or downgrade.
classify() {
    local _installed="$1"
    local _latest="$2"
    if [ -z "$_installed" ]; then printf 'missing'; return; fi
    if [ -z "$_latest" ];    then printf 'up_to_date'; return; fi
    if [ "$_installed" = "$_latest" ]; then printf 'up_to_date'; return; fi
    local _lower
    # W11: Use Python version comparison instead of sort -V
    if /usr/bin/python3 -c "import sys; from ascendo.utils.version import version_gt; sys.exit(0 if version_gt(sys.argv[1], sys.argv[2]) else 1)" "$_installed" "$_latest" 2>/dev/null; then
        printf 'up_to_date'; return
    fi
    printf 'planned'
}

# -- walk manifest -------------------------------------------------------------
COUNT_PLANNED=0
COUNT_UTD=0
COUNT_MISSING=0

if [ -z "$PIP_BIN" ]; then
    json_add_message "warn" "pip not on PATH; reporting all manifest entries as missing"
fi

# Prime cache once so per-package lookups are O(1) jq calls.
ascendo_pip_prime_installed_cache

# Process substitution (NOT `manifest | while`) — the loop body spawns
# curl/jq freely and a piped manifest would lose lines after the first
# subshell drained its stdin.
while IFS='|' read -r DISPLAY PKG METHOD DESC; do
    if [ "$DISPLAY" = "display_name" ]; then continue; fi
    [ -z "$DISPLAY" ] && continue
    in_filter "$DISPLAY" || continue

    INSTALLED=""
    LATEST=""

    case "$METHOD" in
        pip)
            INSTALLED="$(ascendo_pip_installed_version "$PKG")"
            LATEST="$(ascendo_pip_latest_version "$PKG")"
            ;;
        *)
            json_add_message "warn" "unknown method '$METHOD' for $DISPLAY; skipping"
            continue
            ;;
    esac

    STATUS="$(classify "$INSTALLED" "$LATEST")"
    # Brew-managed pip / setuptools / wheel cannot be self-upgraded by
    # pip (no RECORD file). Reclassify as up_to_date so the user
    # doesn't see them flapping between "planned" and "skipped" runs.
    # `brew upgrade python` is the upgrade path; surface it via an
    # info message so the dashboard can show it once.
    if [ "$STATUS" = "planned" ]; then
        case "$(_ascendo_pip_flavour "$(ascendo_pip_pip_bin)"):$PKG" in
            brew:pip|brew:setuptools|brew:wheel)
                STATUS="up_to_date"
                # Pin candidate to installed so the dashboard's
                # _classify overlay (spa_real.py) doesn't re-flip
                # status back to "outdated" via _version_gt — without
                # this, the overlay sees installed=26.1 candidate=26.1.1
                # and overrides our up_to_date verdict.
                LATEST="$INSTALLED"
                json_add_message "info" "$PKG is brew-managed; upgrade via 'brew upgrade python', not pip"
                ;;
            brew:*)
                # Cross-manager skip: pip is brew-flavoured AND brew owns
                # this package as a formula (e.g. uv, ruff, black,
                # poetry, mypy). pip install would race brew for
                # /opt/homebrew/bin/<name>, breaking brew link. Defer to
                # brew. Pinning LATEST=INSTALLED so the dashboard overlay
                # doesn't re-flip to outdated.
                if ascendo_pip_brew_owns "$PKG"; then
                    STATUS="up_to_date"
                    LATEST="$INSTALLED"
                    json_add_message "info" "$PKG is brew-owned (formula); pip defers — upgrade flows through brew category"
                fi
                ;;
        esac
    fi
    case "$STATUS" in
        planned)    COUNT_PLANNED=$((COUNT_PLANNED + 1)) ;;
        up_to_date) COUNT_UTD=$((COUNT_UTD + 1)) ;;
        missing)    COUNT_MISSING=$((COUNT_MISSING + 1)) ;;
    esac

    json_add_item "$DISPLAY" "$INSTALLED" "$LATEST" "$STATUS" "pip" "$METHOD"
done < <(ascendo_pip_manifest_lines)

json_add_message "info" "pip: ${COUNT_PLANNED} outdated, ${COUNT_UTD} up-to-date, ${COUNT_MISSING} missing"
exit 0
