#!/usr/bin/env bash
# =============================================================================
# adapters/macos/scripts/pip/apply.sh -- mutating: install/upgrade pip CLIs
# =============================================================================
# For each manifest entry whose status is `missing` or `planned`:
#   * pip method: `pip install -U <package_name>` against the active pip,
#     with --break-system-packages OR --user added based on the active pip's
#     flavour (brew Python pip vs system Python pip vs venv pip).
#
# `--dry-run` short-circuits BEFORE any mutation: items emit as `planned`
# and the script exits 0.
#
# IMPORTANT — pip apply NEVER invokes sudo on macOS:
#
#   * pip on macOS should never write into /usr/lib or /Library/Frameworks
#     — that's reserved for the OS-supplied Python and modifying it can
#     break Apple subsystems. We always target the user site (or a brew/
#     venv-managed prefix that pip itself owns).
#   * If running under sudo for some reason (e.g. shared `--profile full`
#     with softwareupdate), this script still installs to user-site —
#     never as root.
#
# Bash 3.2 compatible.
# =============================================================================
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
        *) printf 'apply.sh: unknown arg: %s\n' "$1" >&2; exit 2 ;;
    esac
done
[ -z "$RUN_ID" ] || [ -z "$TRIGGER" ] || [ -z "$PROFILE_NAME" ] || [ -z "$OUTPUT_DIR" ] && { printf 'apply.sh: missing args\n' >&2; exit 2; }

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

json_init "apply" "pip" "$RUN_ID" "$TRIGGER" "$PROFILE_NAME" \
          "pip" "$TOOL_VERSION" \
          "$HOST_NAME" "macos" "$HOST_OS_VERSION" "$HOST_ARCH" \
          "$HOST_USER" "$HOST_IS_ELEVATED"
trap 'json_save_on_exit "$OUTPUT_DIR"' EXIT

in_filter() {
    [ -z "$FILTER" ] && return 0
    case ",$FILTER," in (*",$1,"*) return 0 ;; (*) return 1 ;; esac
}
classify() {
    local _installed="$1"
    local _latest="$2"
    [ -z "$_installed" ] && { printf 'missing'; return; }
    [ -z "$_latest" ] && { printf 'up_to_date'; return; }
    [ "$_installed" = "$_latest" ] && { printf 'up_to_date'; return; }
    local _lower
    _lower="$(printf '%s\n%s\n' "$_installed" "$_latest" | sort -V 2>/dev/null | head -n1 || true)"
    if [ -n "$_lower" ] && [ "$_lower" = "$_latest" ]; then
        printf 'up_to_date'; return
    fi
    printf 'planned'
}

# Prime the `pip list` cache once so per-package up_to_date guards
# don't spawn pip per manifest entry.
ascendo_pip_prime_installed_cache

# -- compute pip install flags ONCE ------------------------------------------
# The break-system-packages and --user flags depend on the active pip's
# flavour, not on per-package state. Compute once and reuse.
EXTRA_PIP_ARGS=""
_BSP="$(ascendo_pip_break_system_packages_arg)"
_USER="$(ascendo_pip_user_install_arg)"
# Mutually exclusive: brew Python rejects --user, system Python doesn't
# need --break-system-packages. _ascendo_pip_flavour returns at most one
# of the two so EXTRA_PIP_ARGS holds at most one flag.
if [ -n "$_BSP" ]; then EXTRA_PIP_ARGS="$_BSP"; fi
if [ -n "$_USER" ]; then EXTRA_PIP_ARGS="$_USER"; fi

apply_pip() {
    local _display="$1"
    local _pkg="$2"
    if [ "$DRY_RUN" = "true" ]; then
        json_add_item "$_display" "" "" "planned" "pip" "pip"
        return
    fi
    if [ -z "$PIP_BIN" ] || [ ! -x "$PIP_BIN" ]; then
        json_add_item "$_display" "" "" "failed" "pip" "pip"
        json_add_message "error" "pip not on PATH; cannot install $_display"
        return
    fi
    # Brew-managed Python ships pip / setuptools / wheel without a
    # RECORD file (Homebrew installs them via its own bottle, not via
    # pip), so `pip install -U pip` on a brew Python errors with
    # "uninstall-no-record-file". The upgrade path is `brew upgrade
    # python` — running pip against itself only burns time and emits
    # noise. Skip with a clear explanation.
    case "$(_ascendo_pip_flavour "$PIP_BIN"):$_pkg" in
        brew:pip|brew:setuptools|brew:wheel)
            local _new
            _new="$(ascendo_pip_installed_version "$_pkg")"
            json_add_item "$_display" "$_new" "$_new" "skipped" "pip" "pip"
            json_add_message "info" \
                "skipped pip-self-upgrade of '$_pkg' on brew Python (installed without RECORD; run 'brew upgrade python' to bump)"
            return
            ;;
        brew:*)
            # Cross-manager skip mirroring check.sh + plan.sh. When pip is
            # brew-flavoured AND brew owns this package as a formula, pip
            # would create /opt/homebrew/bin/<name> ahead of brew's own
            # link step — every subsequent `brew upgrade <pkg>` then fails
            # with "Target already exists" because pip occupies the path
            # (operator-reported uv 0.11.12→0.11.13 collision). Defer to
            # brew silently.
            if ascendo_pip_brew_owns "$_pkg"; then
                local _new
                _new="$(ascendo_pip_installed_version "$_pkg")"
                json_add_item "$_display" "$_new" "$_new" "skipped" "pip" "pip"
                json_add_message "info" \
                    "skipped pip upgrade of '$_pkg' — brew also owns this formula; upgrade flows through brew (preserves /opt/homebrew/bin/$_pkg)"
                return
            fi
            ;;
    esac
    # Sesja 50 fix — up_to_date guard. Without this, every apply ran
    # `pip install -U <pkg>` for every manifest entry regardless of
    # current version, producing the "Requirement already satisfied"
    # wall the operator was seeing. Mirrors the npm/apply guard.
    local _installed _latest _status
    _installed="$(ascendo_pip_installed_version "$_pkg" 2>/dev/null)"
    _latest="$(ascendo_pip_latest_version "$_pkg" 2>/dev/null)"
    _status="$(classify "$_installed" "$_latest")"
    if [ "$_status" = "up_to_date" ]; then
        json_add_item "$_display" "$_installed" "$_latest" "up_to_date" "pip" "pip"
        return
    fi
    _stream_emit ">>> pip install -U $_pkg ($_display)"
    # Capture pip's combined stdout+stderr to a temp file AND tee to the
    # live stream. On failure we slice the last 12 lines into the sidecar
    # message so the operator sees the real reason (PEP 668, missing build
    # wheel, network refusal, etc.) without log-diving.
    local _tmp_log
    _tmp_log="$(mktemp -t ascendo-pip-apply.XXXXXX 2>/dev/null || mktemp /tmp/ascendo-pip-apply.XXXXXX)"
    if [ -n "$EXTRA_PIP_ARGS" ]; then
        "$PIP_BIN" install -U "$_pkg" "$EXTRA_PIP_ARGS" --disable-pip-version-check 2>&1 \
            | tee "$_tmp_log" | _stream_tee >/dev/null
    else
        "$PIP_BIN" install -U "$_pkg" --disable-pip-version-check 2>&1 \
            | tee "$_tmp_log" | _stream_tee >/dev/null
    fi
    local _rc="${PIPESTATUS[0]:-1}"
    if [ "$_rc" -ne 0 ]; then
        json_add_item "$_display" "" "" "failed" "pip" "pip"
        # Last ~12 lines, trimmed to 1500 chars total (Pydantic Message has
        # a 4096-char cap; we stay well under). Replace tabs with spaces
        # and squash blank lines so the message reads cleanly in a UI.
        local _tail
        _tail="$(tail -n 12 "$_tmp_log" 2>/dev/null \
                 | tr '\t' ' ' \
                 | awk 'NF{print}' \
                 | head -c 1500 || true)"
        if [ -n "$_tail" ]; then
            json_add_message "error" "pip install -U $_pkg exited $_rc — last output: $_tail"
        else
            json_add_message "error" "pip install -U $_pkg exited $_rc (no output captured)"
        fi
        rm -f "$_tmp_log" 2>/dev/null
        return
    fi
    rm -f "$_tmp_log" 2>/dev/null
    # Bust the cache so post-install version lookup reads fresh state.
    _ASCENDO_PIP_LIST_CACHE=""
    _ASCENDO_PIP_LIST_CACHED=0
    local _new
    _new="$(ascendo_pip_installed_version "$_pkg")"
    json_add_item "$_display" "$_new" "$_new" "success" "pip" "pip"
}

# Total count for live-stream progress accounting.
TOTAL_ITEMS="$(ascendo_pip_manifest_lines | awk -F'|' 'NR > 1 && length($1) > 0 { c++ } END { print c+0 }')"
CURRENT_INDEX=0

if [ "$DRY_RUN" != "true" ] && [ "$TOTAL_ITEMS" -gt 0 ]; then
    _stream_progress 0 "pip apply: $TOTAL_ITEMS item(s)"
fi

# -- walk manifest ------------------------------------------------------------
# Process substitution (NOT `manifest | while`).
while IFS='|' read -r DISPLAY PKG METHOD DESC; do
    [ "$DISPLAY" = "display_name" ] && continue
    [ -z "$DISPLAY" ] && continue
    in_filter "$DISPLAY" || continue
    if [ "$DRY_RUN" != "true" ]; then
        CURRENT_INDEX=$(( CURRENT_INDEX + 1 ))
        _pct=0
        if [ "$TOTAL_ITEMS" -gt 0 ]; then
            _pct=$(( CURRENT_INDEX * 100 / TOTAL_ITEMS ))
        fi
        _stream_progress "$_pct" "pip: $DISPLAY ($METHOD)"
    fi
    case "$METHOD" in
        pip) apply_pip "$DISPLAY" "$PKG" ;;
        *) json_add_message "warn" "unknown method '$METHOD' for $DISPLAY; skipping" ;;
    esac
done < <(ascendo_pip_manifest_lines)

if [ "$DRY_RUN" != "true" ] && [ "$TOTAL_ITEMS" -gt 0 ]; then
    _stream_progress 100 "pip apply: done"
fi

exit 0
