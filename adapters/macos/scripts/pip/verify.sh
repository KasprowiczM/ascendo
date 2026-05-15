#!/usr/bin/env bash
# adapters/macos/scripts/pip/verify.sh -- post-apply re-check
# Re-runs the same logic as check.sh; if any expected pip-installed
# package is still missing or still outdated, status=failed for that item.
#
# Read-only. Bash 3.2 compatible.
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
        *) printf 'verify.sh: unknown arg: %s\n' "$1" >&2; exit 2 ;;
    esac
done
[ -z "$RUN_ID" ] || [ -z "$TRIGGER" ] || [ -z "$PROFILE_NAME" ] || [ -z "$OUTPUT_DIR" ] && { printf 'verify.sh: missing args\n' >&2; exit 2; }

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

json_init "verify" "pip" "$RUN_ID" "$TRIGGER" "$PROFILE_NAME" \
          "pip" "$TOOL_VERSION" \
          "$HOST_NAME" "macos" "$HOST_OS_VERSION" "$HOST_ARCH" \
          "$HOST_USER" "$HOST_IS_ELEVATED"
trap 'json_save_on_exit "$OUTPUT_DIR"' EXIT

in_filter() {
    [ -z "$FILTER" ] && return 0
    case ",$FILTER," in (*",$1,"*) return 0 ;; (*) return 1 ;; esac
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

    # Brew-managed packages must not be judged against the PyPI candidate:
    #   * pip/setuptools/wheel on brew Python have no RECORD file
    #     (Homebrew bottles them) so pip self-upgrade is impossible.
    #   * any other brew-owned formula (e.g. uv) is deferred to brew so
    #     pip never shadows /opt/homebrew/bin/<pkg>.
    # check.sh / plan.sh / apply.sh all pin the candidate to the installed
    # version for these (Sesja 50/72/73). verify.sh MUST mirror the SAME
    # two-tier guard — without the general brew-owned branch a brew-
    # deferred package whose PyPI release is newer (uv: 0.11.13 installed,
    # 0.11.14 on PyPI) is wrongly reported as a verify failure, dragging
    # an otherwise-green pip verify to `partial`.
    if [ -n "$INSTALLED" ] && [ -n "$LATEST" ] && [ "$INSTALLED" != "$LATEST" ]; then
        case "$(_ascendo_pip_flavour "$PIP_BIN"):$PKG" in
            brew:pip|brew:setuptools|brew:wheel)
                LATEST="$INSTALLED"
                ;;
            brew:*)
                if ascendo_pip_brew_owns "$PKG"; then
                    LATEST="$INSTALLED"
                fi
                ;;
        esac
    fi

    # Verify is success when installed!="" AND (latest=="" OR installed==latest).
    # Anything else is failure.
    STATUS="failed"
    if [ -n "$INSTALLED" ]; then
        if [ -z "$LATEST" ] || [ "$INSTALLED" = "$LATEST" ]; then
            STATUS="success"
        fi
    fi
    json_add_item "$DISPLAY" "$INSTALLED" "$LATEST" "$STATUS" "pip" "$METHOD"
done < <(ascendo_pip_manifest_lines)

exit 0
