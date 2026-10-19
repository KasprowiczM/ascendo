#!/usr/bin/env bash
# =============================================================================
# adapters/macos/scripts/softwareupdate/apply.sh -- mutating macOS OS update phase
# =============================================================================
# The mutating softwareupdate phase. Always invokes
# `sudo -A softwareupdate -i ... -R --verbose`. The -R flag is MANDATORY:
# it sets the boot metadata that triggers the update on restart. Without -R,
# updates download but never apply. Battle-tested wisdom from legacy
# /Users/mk/Dev_Env/Ascendo/update_system.sh ("WAŻNE: flaga -R …
# jest wymagana"; "BŁĄD: sudo reboot to surowy restart jądra").
#
# Unattended / cold-cache safety (Task 16): after the sudo warm-up a gate
# (_ascendo_sudo_gate) defers EVERY label softly -- `skipped` + SUDO_UNAVAILABLE,
# exit 0, no install command run -- when ASCENDO_NO_SUDO=1 (even with a hot
# cache) or no askpass helper is wired and `sudo -n true` fails. With the gate
# open and no askpass, installs use `sudo -n` (a lapsed cache fails, never
# prompts). The orchestrator also runs this apply LAST, and skips it when
# brew/mas/npm/web apply failed (SYSTEM_UPDATE_DEFERRED).
#
# Strategy A (sudo -A always): if no SUDO_ASKPASS helper is set, sudo -A
# fails fast — CLI users must `sudo -v` to prime credentials before
# invoking apply, OR set SUDO_ASKPASS=/path/to/helper. The dashboard flow
# sets SUDO_ASKPASS via SoftwareUpdateManager._build_env (mirrors mas).
#
# Args:
#   --run-id ID --trigger TRIG --profile NAME --output-dir DIR
#   [--dry-run]            emits planned items; NEVER invokes sudo
#   [--all]                also install updates that are not marked Recommended
#                          (still label based: NEVER `-ia`, never a bare `-r`)
#   [--filter LABEL]       repeatable; a value may also be a comma-separated list.
#                          Only pending labels EXACTLY matching an entry are
#                          installed; other entries are ignored. Labels are kept
#                          whole (they contain spaces).
#
# Install policy (Ascendo parity with macOS_updates, Spec A):
#   * updates are classified by su_classify.py against `sw_vers`:
#       SAME/OTHER -> eligible; MAJOR (newer macOS release) -> NEVER installed,
#       item `skipped` + ASCENDO-ACTION-REQUIRED reason=major_upgrade, unless
#       ASCENDO_ALLOW_MAJOR_UPGRADE=1. If `sw_vers` fails every OS update is
#       blocked ("installed macOS version unknown"), even with the allow flag.
#   * non-restart labels: one `sudo softwareupdate -i "<label>" -R --verbose
#     --include-config-data` call PER label, first; then a post-install
#     `-l` remeasure decides success (still listed -> failed; `-l` failed ->
#     skipped "state unknown", never success).
#   * restart labels: ONE batched `-i "<L1>" "<L2>" -R ...` call, last. Deferred
#     (`skipped`, reason needs_restart) under the safe/quick profile or
#     ASCENDO_SAFE_MODE=true, even when the category is pinned explicitly.
#   * `-R` is on EVERY install call. No timeout wraps an install (a restart
#     label can legitimately take very long); only the `-l` probes are bounded.
#
# Reboot-survival + honest status:
#   The sidecar is rebuilt from in-memory state after every step (_su_flush);
#   anything not yet finished is on disk as `planned` (truthful default). Just
#   BEFORE the batched restart call the sidecar is saved with needs_reboot set
#   and JSON_FINALIZED=1 (EXIT trap becomes a no-op), so it survives a restart
#   that kills this process. If the process survives (softwareupdate -R
#   normally restarts AFTER it exits) the true outcome is written over it:
#   rc 0 -> `triggered` ("staged with -R, completes after reboot" -- restart
#   updates stay listed until the reboot, so success cannot be measured here;
#   verify.sh promotes it to `success` once the label is gone), else `failed`.
#
# Exit codes (per docs/agents/contract.md):
#   0  success (or nothing eligible: everything skipped)
#   2  bad usage (missing required args / unknown flag)
#   20 apply-fail-known (a label failed to install / is still pending)
#   30 apply-fail-unknown (softwareupdate -l discovery failed or timed out)
#   75 needs reboot (success, but operator must reboot)
# =============================================================================
set -o pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ADAPTER_LIB="$SCRIPT_DIR/../../lib"

# shellcheck source=../../lib/ascendo_json.sh
. "$ADAPTER_LIB/ascendo_json.sh"
# shellcheck source=../../lib/ascendo_softwareupdate.sh
. "$ADAPTER_LIB/ascendo_softwareupdate.sh"
# shellcheck source=../../lib/ascendo_arch.sh
. "$ADAPTER_LIB/ascendo_arch.sh"

# -- arg parsing ---------------------------------------------------------------
RUN_ID=""
TRIGGER=""
PROFILE_NAME=""
OUTPUT_DIR=""
DRY_RUN=0
ALL=0
FILTER_LABELS=""   # newline-delimited; each entry is one whole label
FILTER_RAW=""      # the filter values as given, for messages

# --filter is repeatable. A value is kept whole (labels contain spaces) AND, when
# it contains commas, also split so the legacy "L1,L2" form keeps working. The
# script only ever installs pending labels EXACTLY equal to an entry.
_add_filter() {
    local _v="$1" _part _rest
    [ -n "$_v" ] || return 0
    FILTER_RAW="${FILTER_RAW}${FILTER_RAW:+,}${_v}"
    FILTER_LABELS="${FILTER_LABELS}${FILTER_LABELS:+
}${_v}"
    case "$_v" in
        *,*)
            _rest="$_v"
            while [ -n "$_rest" ]; do
                _part="${_rest%%,*}"
                case "$_rest" in
                    *,*) _rest="${_rest#*,}" ;;
                    *)   _rest="" ;;
                esac
                [ -n "$_part" ] && FILTER_LABELS="${FILTER_LABELS}
${_part}"
            done
            ;;
    esac
}

while [ $# -gt 0 ]; do
    case "$1" in
        --run-id)     RUN_ID="$2";       shift 2 ;;
        --trigger)    TRIGGER="$2";      shift 2 ;;
        --profile)    PROFILE_NAME="$2"; shift 2 ;;
        --output-dir) OUTPUT_DIR="$2";   shift 2 ;;
        --dry-run)    DRY_RUN=1;         shift ;;
        --all)        ALL=1;             shift ;;
        --filter)     _add_filter "$2";  shift 2 ;;
        *) printf 'apply.sh: unknown arg: %s\n' "$1" >&2; exit 2 ;;
    esac
done

if [ -z "$RUN_ID" ] || [ -z "$TRIGGER" ] || [ -z "$PROFILE_NAME" ] || [ -z "$OUTPUT_DIR" ]; then
    printf 'apply.sh: missing required args (--run-id, --trigger, --profile, --output-dir)\n' >&2
    exit 2
fi

# -- host info -----------------------------------------------------------------
HOST_NAME="$(scutil --get ComputerName 2>/dev/null || hostname 2>/dev/null || echo unknown)"
HOST_OS="macos"
HOST_OS_VERSION="$(sw_vers -productVersion 2>/dev/null || echo unknown)"
HOST_ARCH="$(uname -m 2>/dev/null || echo unknown)"
HOST_USER="$(whoami 2>/dev/null || echo unknown)"
if [ "${EUID:-$(id -u)}" -eq 0 ]; then
    HOST_IS_ELEVATED="true"
else
    HOST_IS_ELEVATED="false"
fi

# -- tool info -----------------------------------------------------------------
SU_BIN="${SU_BIN:-/usr/sbin/softwareupdate}"
SU_VER="$("$SU_BIN" --help 2>/dev/null | head -1 || echo unknown)"
if [ -z "$SU_VER" ]; then
    SU_VER="unknown"
fi

# -- init sidecar --------------------------------------------------------------
json_init "apply" "softwareupdate" "$RUN_ID" "$TRIGGER" "$PROFILE_NAME" \
          "softwareupdate" "$SU_VER" \
          "$HOST_NAME" "$HOST_OS" "$HOST_OS_VERSION" "$HOST_ARCH" \
          "$HOST_USER" "$HOST_IS_ELEVATED"

# Install EXIT trap AFTER json_init (bufdir is now valid).
# NOTE: this trap is bypassed in the real-apply path via JSON_FINALIZED=1
# after a manual json_save (reboot-survival; see below).
trap 'json_save_on_exit "$OUTPUT_DIR"' EXIT

# -- sudo invocation helper ----------------------------------------------------
# `_ascendo_sudo` (ascendo_json.sh) picks `-A` for the SPA-askpass flow and
# plain `sudo` for the TTY-PAM / Touch-ID-only flow. _ascendo_sudo_warm has
# warmed the timestamp so plain sudo doesn't reprompt.
_sudo_softwareupdate() {
    _ascendo_sudo "$SU_BIN" "$@"
}


# Rosetta (Task 17, Spec F): `softwareupdate --install-rosetta --agree-to-license`
# runs ONLY with ASCENDO_INSTALL_ROSETTA=1 (explicit opt-in = the licence consent),
# only under the full profile (never safe/quick/ASCENDO_SAFE_MODE), only on Apple
# silicon without Rosetta, and only behind the same sudo gate as every other root
# step. Success needs a post-install measurement (Rosetta now present).
ROS_STATUS=""; ROS_TEXT=""; ROS_LEVEL="info"; ROS_CODE=""
_rosetta_step() {
    [ "${ASCENDO_INSTALL_ROSETTA:-0}" = "1" ] || return 0
    [ "${ASCENDO_HOST_ARCH:-$HOST_ARCH}" = "arm64" ] || return 0
    case "$PROFILE_NAME" in safe|quick) return 0 ;; esac
    [ "${ASCENDO_SAFE_MODE:-}" = "true" ] && return 0
    ascendo_rosetta_installed && return 0
    if [ "$DRY_RUN" -eq 1 ]; then
        ROS_STATUS="planned"; ROS_TEXT="rosetta:install: dry-run: Rosetta would be installed"
        json_add_item "rosetta:install" "" "" "planned" "softwareupdate"
        json_add_message "info" "$ROS_TEXT"
        return 0
    fi
    _ascendo_sudo_warm || true
    if ! _ascendo_sudo_gate; then
        ROS_STATUS="skipped"; ROS_LEVEL="warn"; ROS_CODE="$ASCENDO_SUDO_GATE_CODE"
        ROS_TEXT="rosetta:install: skipped -- ${ASCENDO_SUDO_GATE_TEXT} (${ASCENDO_SUDO_GATE_CODE})"
    else
        ASCENDO_SUDO_NONINTERACTIVE=1 _ascendo_sudo "$SU_BIN" --install-rosetta --agree-to-license </dev/null >/dev/null 2>&1
        if ascendo_rosetta_installed; then
            ROS_STATUS="success"; ROS_TEXT="rosetta:install: Rosetta installed (ASCENDO_INSTALL_ROSETTA=1)"
        else
            ROS_STATUS="failed"; ROS_LEVEL="error"
            ROS_TEXT="rosetta:install: softwareupdate --install-rosetta did not result in Rosetta being present"
        fi
    fi
    json_add_item "rosetta:install" "" "" "$ROS_STATUS" "softwareupdate"
    json_add_message "$ROS_LEVEL" "$ROS_TEXT" "$ROS_CODE"
}
_rosetta_step

# -- discover pending updates via softwareupdate -l --include-config-data ------
# (C locale + bounded: see ascendo_softwareupdate.sh). A failed/timed-out probe
# is a failure of the phase, never an empty (== "nothing to do") result.
ascendo_su_list
if [ "$SU_RC" -ne 0 ]; then
    if [ "$SU_RC" -eq 124 ]; then
        json_add_message "error" "softwareupdate -l timed out after ${ASCENDO_SU_LIST_TIMEOUT:-300}s"
    else
        json_add_message "error" "softwareupdate -l failed (exit $SU_RC): $SU_OUT"
    fi
    json_add_item "softwareupdate:list-error" "" "" "failed" "softwareupdate"
    exit 30
fi

CUR_OS="$(ascendo_su_current_os)"
SU_RECORDS="$(ascendo_su_records "$SU_OUT" "$CUR_OS")" || {
    json_add_message "error" "softwareupdate listing could not be parsed (su_classify.py failed)"
    json_add_item "softwareupdate:list-error" "" "" "failed" "softwareupdate"
    exit 30
}

# A non-empty listing that yields no record (and does not say "No new software
# available") is UNKNOWN state: report it, install nothing.
if ascendo_su_listing_unparseable "$SU_OUT" "$SU_RECORDS"; then
    json_add_message "warn" "softwareupdate -l returned a listing that could not be parsed; pending update state unknown -- nothing installed"
    json_add_item "softwareupdate:list-unknown" "" "" "skipped" "softwareupdate"
    exit 0
fi

# Empty case: nothing to upgrade. Exit cleanly with zero items.
if [ -z "$SU_RECORDS" ]; then
    json_add_message "info" "softwareupdate -l reports no pending updates"
    exit 0
fi

# -- state (bash 3.2: newline-delimited lists, US-separated fields) -----------
NL='
'
US="$ASCENDO_SU_US"
NEEDS_REBOOT=0
RES=""        # final items:   label US cur US tgt US status
MSGS=""       # messages:      level US text
NR_TODO=""    # non-restart labels not yet installed:  label US version
NR_AWAIT=""   # non-restart labels installed (rc 0), awaiting the remeasure
RS_TODO=""    # restart labels (batched, last):        label US version
FAILED_COUNT=0

_res_add() { RES="${RES}${RES:+$NL}$1${US}$2${US}$3${US}$4"; }
_msg_add() { MSGS="${MSGS}${MSGS:+$NL}$1${US}$2"; }

# _in_filter LABEL -- no filter selects everything; else EXACT match on an entry.
_in_filter() {
    [ -n "$FILTER_LABELS" ] || return 0
    local _entry
    while IFS= read -r _entry; do
        [ "$_entry" = "$1" ] && return 0
    done <<< "$FILTER_LABELS"
    return 1
}

# _list_has LIST ITEM -- exact whole-line membership.
_list_has() {
    local _line
    while IFS= read -r _line; do
        [ "$_line" = "$2" ] && return 0
    done <<< "$1"
    return 1
}

# Restart labels are deferred under safe/quick profiles even when the category
# is pinned explicitly (profile is passed by the manager as --profile).
DEFER_RESTART=0
case "$PROFILE_NAME" in
    safe|quick) DEFER_RESTART=1 ;;
esac
[ "${ASCENDO_SAFE_MODE:-}" = "true" ] && DEFER_RESTART=1

# -- selection -----------------------------------------------------------------
MATCHED=0
while IFS="$US" read -r _kind _label _title _ver _restart _recommended; do
    [ -n "$_label" ] || continue
    _in_filter "$_label" || continue
    MATCHED=$(( MATCHED + 1 ))
    _is_restart="$_restart"

    if [ "$_kind" = "MAJOR" ]; then
        _is_restart=1     # a macOS upgrade always restarts
        if ascendo_su_blocked "$_kind" "$CUR_OS"; then
            _res_add "$_label" "$CUR_OS" "$_ver" "skipped"
            _msg_add "warn" "$(ascendo_su_blocked_message "$_label" "$CUR_OS" "$_ver")"
            continue
        fi
    fi

    if [ -z "$FILTER_LABELS" ] && [ "$ALL" -eq 0 ] && [ "$_recommended" != "1" ]; then
        _res_add "$_label" "$_ver" "$_ver" "skipped"
        _msg_add "info" "${_label}: skipped -- not marked Recommended (use --all, or pin it with --filter)"
        continue
    fi

    if [ "$_is_restart" = "1" ]; then
        if [ "$DEFER_RESTART" -eq 1 ]; then
            _res_add "$_label" "$_ver" "$_ver" "skipped"
            _msg_add "info" "${_label}: skipped -- restart required; deferred under the '${PROFILE_NAME}' profile (run the full profile to install) | $(ascendo_action_required_token needs_restart "?" "$_ver")"
            continue
        fi
        RS_TODO="${RS_TODO}${RS_TODO:+$NL}${_label}${US}${_ver}"
    else
        NR_TODO="${NR_TODO}${NR_TODO:+$NL}${_label}${US}${_ver}"
    fi
done <<< "$SU_RECORDS"

if [ "$MATCHED" -eq 0 ]; then
    if [ -n "$FILTER_LABELS" ]; then
        json_add_message "info" "nothing selected: none of the filter entries (${FILTER_RAW}) is a pending softwareupdate label"
    else
        json_add_message "info" "no pending updates after filter"
    fi
    exit 0
fi

# -- sidecar (re)builder --------------------------------------------------------
# Rebuilds the whole sidecar buffer from the state lists: final results first,
# then everything still to be done as `planned` (truthful default on disk if we
# are killed / the Mac reboots mid-run).
_su_flush() {
    local _l _c _t _s _lvl _txt _v
    if [ -n "${JSON_BUFDIR:-}" ] && [ -d "$JSON_BUFDIR" ]; then
        rm -rf "$JSON_BUFDIR"
    fi
    json_init "apply" "softwareupdate" "$RUN_ID" "$TRIGGER" "$PROFILE_NAME" \
              "softwareupdate" "$SU_VER" \
              "$HOST_NAME" "$HOST_OS" "$HOST_OS_VERSION" "$HOST_ARCH" \
              "$HOST_USER" "$HOST_IS_ELEVATED"
    # The Rosetta step ran before the state lists existed: re-emit its outcome.
    if [ -n "$ROS_STATUS" ]; then
        json_add_item "rosetta:install" "" "" "$ROS_STATUS" "softwareupdate"
        json_add_message "$ROS_LEVEL" "$ROS_TEXT" "$ROS_CODE"
    fi
    if [ -n "$RES" ]; then
        while IFS="$US" read -r _l _c _t _s; do
            json_add_item "$_l" "$_c" "$_t" "$_s" "softwareupdate"
        done <<< "$RES"
    fi
    for _list in "$NR_TODO" "$NR_AWAIT" "$RS_TODO"; do
        [ -n "$_list" ] || continue
        while IFS="$US" read -r _l _v; do
            json_add_item "$_l" "$_v" "$_v" "planned" "softwareupdate"
        done <<< "$_list"
    done
    if [ -n "$MSGS" ]; then
        while IFS="$US" read -r _lvl _txt; do
            json_add_message "$_lvl" "$_txt"
        done <<< "$MSGS"
    fi
    if [ "$NEEDS_REBOOT" -eq 1 ]; then
        json_set_needs_reboot true
    fi
}

# -- dry-run path: emit planned items, NEVER invoke sudo ----------------------
if [ "$DRY_RUN" -eq 1 ]; then
    [ -n "$RS_TODO" ] && NEEDS_REBOOT=1
    _msg_add "info" "dry-run: no real upgrades performed"
    _su_flush
    json_save "$OUTPUT_DIR"
    exit 0
fi

# Nothing eligible (everything skipped): report and stop before touching sudo.
if [ -z "$NR_TODO" ] && [ -z "$RS_TODO" ]; then
    _su_flush
    json_save "$OUTPUT_DIR"
    exit 0
fi

# -- real apply path -----------------------------------------------------------
# Touch-ID-first sudo warming (Sesja 34). See ascendo_json.sh for the
# rationale: native macOS auth dialog presents Touch ID first when
# pam_tid is configured, password fallback otherwise. After this
# returns, the `sudo` invocations below use the warmed timestamp.
_ascendo_sudo_warm || true   # rc 1 = unattended + cold cache (sets ASCENDO_NO_SUDO)

# Post-warm authorization gate (F1). Root work proceeds only when
# ASCENDO_NO_SUDO is unset AND an askpass helper is wired or `sudo -n true`
# succeeds. Otherwise every eligible label is deferred SOFTLY: `skipped` with a
# SUDO_UNAVAILABLE reason, exit 0, and no sudo install command is ever
# invoked (a Touch-ID sheet mid-run, or a hard `failed` per label, is worse).
if ! _ascendo_sudo_gate; then
    for _list in "$NR_TODO" "$RS_TODO"; do
        [ -n "$_list" ] || continue
        while IFS="$US" read -r _label _ver; do
            [ -n "$_label" ] || continue
            _res_add "$_label" "$_ver" "$_ver" "skipped"
            _msg_add "warn" "${_label}: skipped -- ${ASCENDO_SUDO_GATE_TEXT} (${ASCENDO_SUDO_GATE_CODE})"
        done <<< "$_list"
    done
    NR_TODO=""
    RS_TODO=""
    NEEDS_REBOOT=0
    _su_flush
    json_save "$OUTPUT_DIR"
    exit 0
fi
# From here on every sudo call is non-interactive (`sudo -n` unless an askpass
# helper is wired): a lapsed cache must fail the install, never prompt.
export ASCENDO_SUDO_NONINTERACTIVE=1

mkdir -p "$OUTPUT_DIR/$RUN_ID" 2>/dev/null || true
_log_path="$OUTPUT_DIR/$RUN_ID/apply__softwareupdate.log"

_count_lines() { [ -n "$1" ] && printf '%s\n' "$1" | wc -l | tr -d ' ' || echo 0; }
_total_labels=$(( $(_count_lines "$NR_TODO") + $(_count_lines "$RS_TODO") ))
_stream_progress 5 "softwareupdate apply: $_total_labels update(s)"

# Everything eligible on disk as `planned` BEFORE the first install (a crash /
# kill mid-install then leaves a truthful sidecar). The next _su_flush re-arms
# the EXIT trap through json_init.
_su_flush
json_save "$OUTPUT_DIR"

# 1. Non-restart labels: ONE install call per label, each with -R. stdin is
#    detached so an install can never swallow the label list being iterated.
_done_labels=0
_NR_ITER="$NR_TODO"
if [ -n "$_NR_ITER" ]; then
    while IFS="$US" read -r _label _ver; do
        [ -n "$_label" ] || continue
        NR_TODO="$(printf '%s\n' "$NR_TODO" | sed '1d')"
        _stream_emit ">>> sudo softwareupdate -i \"$_label\" -R --verbose --include-config-data"
        _sudo_softwareupdate -i "$_label" -R --verbose --include-config-data </dev/null 2>&1 \
            | tee -a "$_log_path" | _stream_tee >/dev/null
        _RC="${PIPESTATUS[0]}"
        _done_labels=$(( _done_labels + 1 ))
        if [ "$_RC" -eq 0 ]; then
            NR_AWAIT="${NR_AWAIT}${NR_AWAIT:+$NL}${_label}${US}${_ver}"
        else
            _res_add "$_label" "$_ver" "$_ver" "failed"
            _msg_add "error" "${_label}: softwareupdate -i failed (rc=${_RC})"
            FAILED_COUNT=$(( FAILED_COUNT + 1 ))
        fi
        _stream_progress $(( 5 + 85 * _done_labels / (_total_labels > 0 ? _total_labels : 1) )) "softwareupdate apply: ${_done_labels}/${_total_labels}"
        _su_flush
    done <<< "$_NR_ITER"
fi

# 2. Post-install remeasure for the non-restart labels: success only when the
#    label is really gone from `softwareupdate -l`. A failed probe is UNKNOWN
#    (skipped + reason), never success.
if [ -n "$NR_AWAIT" ]; then
    _known=1
    ascendo_su_list || _known=0
    _still=""
    if [ "$_known" -eq 1 ]; then
        _post_recs="$(ascendo_su_records "$SU_OUT" "$CUR_OS")" || _known=0
        if [ "$_known" -eq 1 ] && ascendo_su_listing_unparseable "$SU_OUT" "$_post_recs"; then
            _known=0      # garbage listing is not proof the label is gone
            SU_RC="unparseable"
        fi
    fi
    if [ "$_known" -eq 1 ] && [ -n "$_post_recs" ]; then
        while IFS="$US" read -r _pk _plabel _rest; do
            [ -n "$_plabel" ] && _still="${_still}${_still:+$NL}${_plabel}"
        done <<< "$_post_recs"
    fi
    while IFS="$US" read -r _label _ver; do
        [ -n "$_label" ] || continue
        if [ "$_known" -eq 0 ]; then
            _res_add "$_label" "$_ver" "$_ver" "skipped"
            _msg_add "warn" "${_label}: installed (rc=0) but the post-install state is unknown -- softwareupdate -l failed (rc=${SU_RC}); not reported as success"
        elif _list_has "$_still" "$_label"; then
            _res_add "$_label" "$_ver" "$_ver" "failed"
            _msg_add "error" "${_label}: still pending after install"
            FAILED_COUNT=$(( FAILED_COUNT + 1 ))
        else
            _res_add "$_label" "$_ver" "$_ver" "success"
        fi
    done <<< "$NR_AWAIT"
    NR_AWAIT=""
    _su_flush
fi

# 3. Restart labels: ONE batched call, LAST. Save the sidecar (planned +
#    needs_reboot) BEFORE it so it survives a restart that kills this process;
#    JSON_FINALIZED=1 makes the EXIT trap a no-op until the next rebuild.
if [ -n "$RS_TODO" ]; then
    set --
    while IFS="$US" read -r _label _ver; do
        [ -n "$_label" ] && set -- "$@" "$_label"
    done <<< "$RS_TODO"
    NEEDS_REBOOT=1
    _su_flush
    json_save "$OUTPUT_DIR"
    JSON_FINALIZED=1
    _stream_emit ">>> sudo softwareupdate -i (batched, restart) $* -R --verbose --include-config-data"
    _sudo_softwareupdate -i "$@" -R --verbose --include-config-data </dev/null 2>&1 \
        | tee -a "$_log_path" | _stream_tee >/dev/null
    _RC="${PIPESTATUS[0]}"

    # The process survived softwareupdate -R (it normally restarts AFTER we
    # exit): record the true outcome. Restart updates stay listed until the
    # reboot, so no post-action measurement is possible here: rc 0 means only
    # "staged", reported as `triggered` (never `success`). verify.sh upgrades it
    # to `success` once the label is gone from `softwareupdate -l`.
    while IFS="$US" read -r _label _ver; do
        [ -n "$_label" ] || continue
        if [ "$_RC" -eq 0 ]; then
            _res_add "$_label" "$_ver" "$_ver" "triggered"
            _msg_add "info" "${_label}: staged with -R, completes after reboot"
        else
            _res_add "$_label" "$_ver" "$_ver" "failed"
            _msg_add "error" "${_label}: softwareupdate -i failed (rc=${_RC})"
            FAILED_COUNT=$(( FAILED_COUNT + 1 ))
        fi
    done <<< "$RS_TODO"
    RS_TODO=""
    [ "$_RC" -eq 0 ] || NEEDS_REBOOT=0
fi

_stream_progress 100 "softwareupdate apply: done"
_su_flush
json_save "$OUTPUT_DIR"

# Exit-code logic
if [ "$FAILED_COUNT" -gt 0 ]; then
    exit 20   # apply-fail-known
fi
if [ "$NEEDS_REBOOT" -eq 1 ]; then
    exit 75   # NEEDS_REBOOT (success, but operator must reboot)
fi
exit 0
