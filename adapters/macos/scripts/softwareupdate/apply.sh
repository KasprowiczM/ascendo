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
# Strategy A (sudo -A always): if no SUDO_ASKPASS helper is set, sudo -A
# fails fast — CLI users must `sudo -v` to prime credentials before
# invoking apply, OR set SUDO_ASKPASS=/path/to/helper. The dashboard flow
# sets SUDO_ASKPASS via SoftwareUpdateManager._build_env (mirrors mas).
#
# Args:
#   --run-id ID --trigger TRIG --profile NAME --output-dir DIR
#   [--dry-run]            emits planned items; NEVER invokes sudo
#   [--all]                use -ia (all available) instead of -ir (recommended only)
#   [--filter LABEL]       single-label apply: `sudo -A softwareupdate -i <LABEL> -R --verbose`
#                          (NOT comma-separated like mas's --filter; softwareupdate
#                           takes one label per -i arg, MVP supports one label only)
#
# Reboot-survival + post-apply reconciliation:
#   Apply may reboot the Mac mid-run when any update has Action: restart.
#   We pre-emit "planned" items + call json_save BEFORE invoking sudo so the
#   sidecar persists across the reboot. JSON_FINALIZED is set to 1 to make
#   the first EXIT trap a no-op.
#
#   When sudo returns normally (the typical case — softwareupdate -R only
#   reboots AFTER its process exits, mediated by launchd), we re-init the
#   buffer and re-emit items with their TRUE post-apply status: ``success``
#   on RC=0, ``failed`` on non-zero. The re-emit overwrites the same
#   sidecar path so the operator sees accurate state once apply finishes
#   without rebooting.
#
#   If sudo IS killed mid-stream by a forced reboot, the original "planned"
#   sidecar is what survives — verify phase reconciles by re-running
#   softwareupdate -l. "planned" beats "success" as a default-on-disk
#   state because it's truthful: we asked for the update but didn't
#   confirm it landed.
#
# Exit codes (per docs/agents/contract.md):
#   0  success
#   2  bad usage (missing required args / unknown flag)
#   20 apply-fail-known (sudo softwareupdate non-zero exit)
#   30 apply-fail-unknown (softwareupdate -l discovery failed)
#   75 needs reboot (success, but operator must reboot)
# =============================================================================
set -o pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ADAPTER_LIB="$SCRIPT_DIR/../../lib"

# shellcheck source=../../lib/ascendo_json.sh
. "$ADAPTER_LIB/ascendo_json.sh"

# -- arg parsing ---------------------------------------------------------------
RUN_ID=""
TRIGGER=""
PROFILE_NAME=""
OUTPUT_DIR=""
DRY_RUN=0
ALL=0
FILTER_LABEL=""

while [ $# -gt 0 ]; do
    case "$1" in
        --run-id)     RUN_ID="$2";       shift 2 ;;
        --trigger)    TRIGGER="$2";      shift 2 ;;
        --profile)    PROFILE_NAME="$2"; shift 2 ;;
        --output-dir) OUTPUT_DIR="$2";   shift 2 ;;
        --dry-run)    DRY_RUN=1;         shift ;;
        --all)        ALL=1;             shift ;;
        --filter)     FILTER_LABEL="$2"; shift 2 ;;
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

# -- discover pending updates via softwareupdate -l ----------------------------
SU_RC=0
SU_OUT="$("$SU_BIN" -l 2>&1)" || SU_RC=$?
if [ "$SU_RC" -ne 0 ]; then
    json_add_message "error" "softwareupdate -l failed (exit $SU_RC): $SU_OUT"
    json_add_item "softwareupdate:list-error" "" "" "failed" "softwareupdate"
    exit 30
fi

# Empty case: nothing to upgrade. Exit cleanly with zero items.
if printf '%s\n' "$SU_OUT" | grep -q "No new software available\."; then
    json_add_message "info" "softwareupdate -l reports no pending updates"
    exit 0
fi

# -- helper: filter check (--filter LABEL matches a single label exactly) -----
in_filter() {
    [ -z "$FILTER_LABEL" ] && return 0
    [ "$1" = "$FILTER_LABEL" ] && return 0
    return 1
}

# -- parse pending updates into space-separated TARGET_LABELS + TARGET_PAIRS --
# Each pair is "<label>|<version>|<needs_restart>" where needs_restart is 1/0.
# Use here-string (<<<) so the loop runs in current shell, not subshell.
NEEDS_REBOOT=0
TARGET_LABELS=""
TARGET_PAIRS=""
CURRENT_LABEL=""

while IFS= read -r line; do
    case "$line" in
        '* Label: '*)
            CURRENT_LABEL="${line#'* Label: '}"
            ;;
        "	"*)
            [ -n "$CURRENT_LABEL" ] || continue
            attrs="${line#	}"
            version="$(printf '%s\n' "$attrs" | sed -n 's/.*Version: \([^,]*\),.*/\1/p' | head -1)"
            version="${version# }"
            version="${version% }"
            item_restart=0
            if printf '%s\n' "$attrs" | grep -q "Action: restart"; then
                item_restart=1
                NEEDS_REBOOT=1
            fi
            if in_filter "$CURRENT_LABEL"; then
                TARGET_LABELS="$TARGET_LABELS $CURRENT_LABEL"
                TARGET_PAIRS="$TARGET_PAIRS $CURRENT_LABEL|$version|$item_restart"
            fi
            CURRENT_LABEL=""
            ;;
    esac
done <<< "$SU_OUT"

# strip leading whitespace
TARGET_LABELS="${TARGET_LABELS# }"
TARGET_PAIRS="${TARGET_PAIRS# }"

if [ -z "$TARGET_LABELS" ]; then
    if [ -n "$FILTER_LABEL" ]; then
        json_add_message "warn" "filter LABEL '$FILTER_LABEL' did not match any pending update"
    else
        json_add_message "info" "no pending updates after filter"
    fi
    exit 0
fi

# -- dry-run path: emit planned items, NEVER invoke sudo ----------------------
if [ "$DRY_RUN" -eq 1 ]; then
    for _pair in $TARGET_PAIRS; do
        _label="$(printf '%s' "$_pair" | awk -F'|' '{print $1}')"
        _ver="$(printf '%s' "$_pair" | awk -F'|' '{print $2}')"
        json_add_item "$_label" "$_ver" "$_ver" "planned" "softwareupdate"
    done
    if [ "$NEEDS_REBOOT" -eq 1 ]; then
        json_set_needs_reboot true
    fi
    json_add_message "info" "dry-run: no real upgrades performed"
    exit 0
fi

# -- real apply path -----------------------------------------------------------
# Touch-ID-first sudo warming (Sesja 34). See ascendo_json.sh for the
# rationale: native macOS auth dialog presents Touch ID first when
# pam_tid is configured, password fallback otherwise. After this
# returns, the `sudo -A` invocation below uses the warmed timestamp.
_ascendo_sudo_warm

# 1. Build sudo argv based on flags
SU_ARGV=""
if [ -n "$FILTER_LABEL" ]; then
    # Single-label apply: -i <LABEL> (no -a/-r flag; softwareupdate disambiguates by label)
    SU_ARGV="-i $FILTER_LABEL -R --verbose"
elif [ "$ALL" -eq 1 ]; then
    SU_ARGV="-i -a -R --verbose"
else
    # Default: recommended only
    SU_ARGV="-i -r -R --verbose"
fi

# 2. Pre-emit PLANNED items BEFORE sudo (reboot survival; truthful default).
for _pair in $TARGET_PAIRS; do
    _label="$(printf '%s' "$_pair" | awk -F'|' '{print $1}')"
    _ver="$(printf '%s' "$_pair" | awk -F'|' '{print $2}')"
    json_add_item "$_label" "$_ver" "$_ver" "planned" "softwareupdate"
done

# 3. Set top-level needs_reboot flag if any item required restart.
if [ "$NEEDS_REBOOT" -eq 1 ]; then
    json_set_needs_reboot true
fi

# 4. Save sidecar to disk BEFORE sudo invocation (sidecar must persist across
#    a mid-apply reboot). Disable EXIT trap save by setting JSON_FINALIZED=1.
json_save "$OUTPUT_DIR"
JSON_FINALIZED=1

# 5. Invoke sudo. tee both stdout + stderr to a log file alongside the sidecar
#    AND through _stream_tee so the dashboard SSE endpoint sees every line in
#    real time.
mkdir -p "$OUTPUT_DIR/$RUN_ID" 2>/dev/null || true
_log_path="$OUTPUT_DIR/$RUN_ID/apply__softwareupdate.log"

_total_labels=0
for _pair in $TARGET_PAIRS; do
    _total_labels=$(( _total_labels + 1 ))
done
if [ "$_total_labels" -gt 0 ]; then
    _stream_progress 5 "softwareupdate apply: $_total_labels update(s)"
fi
_stream_emit ">>> sudo softwareupdate $SU_ARGV"

# Word-splitting on $SU_ARGV is intentional + safe — values are controlled
# (literal flags + filter label which we sanitize to a single word).
# shellcheck disable=SC2086
_sudo_softwareupdate $SU_ARGV 2>&1 | tee -a "$_log_path" | _stream_tee >/dev/null
_RC="${PIPESTATUS[0]}"
_stream_progress 100 "softwareupdate apply: done (rc=$_RC)"

# 6. Post-apply reconciliation: re-init buffer + re-emit items with true
#    status (success on RC=0, failed otherwise), then overwrite the sidecar.
#    If we get here, the process survived sudo — so the operator deserves
#    accurate state on disk instead of the stale "planned" pre-emit.
if [ "$_RC" -eq 0 ]; then
    _final_status="success"
else
    _final_status="failed"
fi

json_init "apply" "softwareupdate" "$RUN_ID" "$TRIGGER" "$PROFILE_NAME" \
          "softwareupdate" "$SU_VER" \
          "$HOST_NAME" "$HOST_OS" "$HOST_OS_VERSION" "$HOST_ARCH" \
          "$HOST_USER" "$HOST_IS_ELEVATED"

# The trap was already armed before the first json_save (which set
# JSON_FINALIZED=1). json_init resets JSON_FINALIZED to 0, so re-arm
# the EXIT trap so a crash between here and the post-apply json_save
# still produces SOMETHING on disk.
trap 'json_save_on_exit "$OUTPUT_DIR"' EXIT

for _pair in $TARGET_PAIRS; do
    _label="$(printf '%s' "$_pair" | awk -F'|' '{print $1}')"
    _ver="$(printf '%s' "$_pair" | awk -F'|' '{print $2}')"
    json_add_item "$_label" "$_ver" "$_ver" "$_final_status" "softwareupdate"
done

if [ "$NEEDS_REBOOT" -eq 1 ]; then
    json_set_needs_reboot true
fi

if [ "$_RC" -ne 0 ]; then
    json_add_message "error" "sudo softwareupdate exited $_RC"
fi

json_save "$OUTPUT_DIR"

# 7. Exit-code logic
if [ "$NEEDS_REBOOT" -eq 1 ] && [ "$_RC" -eq 0 ]; then
    exit 75   # NEEDS_REBOOT (success, but operator must reboot)
fi

if [ "$_RC" -ne 0 ]; then
    exit 20   # apply-fail-known
fi

exit 0
