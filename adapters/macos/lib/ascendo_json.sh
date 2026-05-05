#!/usr/bin/env bash
# =============================================================================
# adapters/macos/lib/ascendo_json.sh -- bash wrapper for ascendo/v1 emitter
# =============================================================================
# Sourced by phase scripts:
#     . "$ADAPTER_LIB/ascendo_json.sh"
#
# API (mirrors AscendoJson.psm1 on Windows where possible):
#     json_init    <phase> <category> <run_id> <trigger> <profile> \
#                  <tool_name> <tool_version> \
#                  <host_name> <host_os> <host_os_version> <host_arch> \
#                  <host_user> <host_is_elevated>
#     json_add_item    <id> <current_version> <target_version> <status> \
#                      [source_type] [source_feed]
#     json_add_message <level> <text> [code]
#     json_set_needs_reboot <true|false>
#     json_count       <bucket: success|skipped|failed> [n]
#     json_save        <output_dir>
#         -> writes <output_dir>/<run_id>/<phase>__<category>.json
#         -> cleans up the bufdir tempdir
#     json_save_on_exit <output_dir>
#         -> trap helper; saves only if not yet finalized
#
# Bash 3.2-safe. State held in bufdir (tempdir) between calls; no globals
# beyond JSON_BUFDIR / JSON_PHASE / JSON_CATEGORY / JSON_RUN_ID /
# JSON_FINALIZED / JSON_LAST_EXIT_CODE.
# =============================================================================

# shellcheck disable=SC2155
if [ -z "${ASCENDO_JSON_DIR:-}" ]; then
    ASCENDO_JSON_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
fi
_ASCENDO_JSON_EMIT="${ASCENDO_JSON_DIR}/_json_emit.py"

if ! command -v python3 >/dev/null 2>&1; then
    echo "ascendo_json.sh: python3 is required" >&2
    return 1 2>/dev/null || exit 1
fi
if [ ! -f "$_ASCENDO_JSON_EMIT" ]; then
    echo "ascendo_json.sh: missing helper $_ASCENDO_JSON_EMIT" >&2
    return 1 2>/dev/null || exit 1
fi

JSON_BUFDIR=""
JSON_PHASE=""
JSON_CATEGORY=""
JSON_RUN_ID=""
JSON_FINALIZED=0
JSON_LAST_EXIT_CODE=0

_json_now_utc() { date -u +"%Y-%m-%dT%H:%M:%SZ"; }

_json_emit() { python3 "$_ASCENDO_JSON_EMIT" "$@"; }

# json_init <phase> <category> <run_id> <trigger> <profile_name>
#           <tool_name> <tool_version>
#           <host_name> <host_os> <host_os_version> <host_arch>
#           <host_user> <host_is_elevated>
json_init() {
    JSON_PHASE="$1"
    JSON_CATEGORY="$2"
    JSON_RUN_ID="$3"
    JSON_BUFDIR="$(mktemp -d "${TMPDIR:-/tmp}/ascendo_json_XXXXXX")"
    JSON_FINALIZED=0
    JSON_LAST_EXIT_CODE=0
    _json_emit init \
        --bufdir "$JSON_BUFDIR" \
        --phase "$1" \
        --category "$2" \
        --run-id "$3" \
        --trigger "$4" \
        --profile-name "$5" \
        --tool-name "$6" \
        --tool-version "$7" \
        --started-at "$(_json_now_utc)" \
        --host-name "${8:-unknown}" \
        --host-os "${9:-macos}" \
        --host-os-version "${10:-unknown}" \
        --host-arch "${11:-unknown}" \
        --host-user "${12:-unknown}" \
        --host-is-elevated "${13:-false}"
}

# json_add_item <id> <current_version> <target_version> <status>
#               [source_type] [source_feed]
json_add_item() {
    local _id="$1"
    local _cur="$2"
    local _tgt="$3"
    local _status="$4"
    local _src_type="${5:-}"
    local _src_feed="${6:-}"

    local args
    args=(add-item --bufdir "$JSON_BUFDIR" --id "$_id" --status "$_status")
    if [ -n "$_cur" ]; then
        args+=(--current-version "$_cur")
    fi
    if [ -n "$_tgt" ]; then
        args+=(--target-version "$_tgt")
    fi
    if [ -n "$_src_type" ]; then
        args+=(--source-type "$_src_type")
    fi
    if [ -n "$_src_feed" ]; then
        args+=(--source-feed "$_src_feed")
    fi
    _json_emit "${args[@]}"
}

# json_add_message <level> <text> [code]
json_add_message() {
    local _level="$1"
    local _text="$2"
    local _code="${3:-}"

    local args
    args=(add-message --bufdir "$JSON_BUFDIR" --level "$_level" --text "$_text")
    if [ -n "$_code" ]; then
        args+=(--code "$_code")
    fi
    _json_emit "${args[@]}"
}

# json_set_needs_reboot <true|false>
json_set_needs_reboot() {
    _json_emit set-flag --bufdir "$JSON_BUFDIR" --key needs_reboot --value "$1"
}

# json_count <bucket: success|skipped|failed> [n]
json_count() {
    local _bucket="$1"
    local _n="${2:-1}"
    _json_emit count --bufdir "$JSON_BUFDIR" --bucket "$_bucket" --n "$_n"
}

# json_save <output_dir>
# Writes <output_dir>/<run_id>/<phase>__<category>.json then removes bufdir.
json_save() {
    local _output_dir="$1"
    local _run_dir="${_output_dir}/${JSON_RUN_ID}"
    mkdir -p "$_run_dir"
    local _out="${_run_dir}/${JSON_PHASE}__${JSON_CATEGORY}.json"
    local _exit_code="${JSON_LAST_EXIT_CODE:-0}"
    _json_emit finalize \
        --bufdir "$JSON_BUFDIR" \
        --out "$_out" \
        --exit-code "$_exit_code" \
        --ended-at "$(_json_now_utc)"
    JSON_FINALIZED=1
    rm -rf "$JSON_BUFDIR"
}

# json_save_on_exit <output_dir>
# Designed for use in an EXIT trap:
#   trap 'json_save_on_exit "$OUTPUT_DIR"' EXIT
# Captures $? at invocation time, saves only if not yet finalized,
# then re-returns the original exit code so the shell process exits
# with the correct code.
json_save_on_exit() {
    local _rc="$?"
    JSON_LAST_EXIT_CODE="$_rc"
    if [ "$JSON_FINALIZED" -eq 0 ] && [ -n "$JSON_BUFDIR" ] && [ -d "$JSON_BUFDIR" ]; then
        json_save "$1" || true
    fi
    return "$_rc"
}

# =============================================================================
# Live-stream helpers (consumed by the dashboard SSE endpoint).
# =============================================================================
# When a phase script runs under ``ascendo dashboard`` (or any wrapper
# that exports $ASCENDO_STREAM_LOG), every apply mutation should be piped
# through ``_stream_tee`` so the SSE endpoint can show every line of
# stdout/stderr in the Run Center as it happens.
#
# Convention is best-effort: if $ASCENDO_STREAM_LOG is unset (CLI runs,
# unit tests, plain bash invocation), tee becomes a no-op (cat) and
# nothing is logged.
#
# Usage from inside an apply.sh:
#     brew upgrade --formula foo 2>&1 | _stream_tee
#     sudo -A softwareupdate -ir -R --verbose 2>&1 | _stream_tee
#     sudo -A mas upgrade $TARGET_IDS 2>&1 | _stream_tee
#
# The exit code of the original command is preserved via PIPESTATUS[0]
# (caller is responsible for reading it after the pipeline).
# =============================================================================

# _stream_tee — append every stdin line to $ASCENDO_STREAM_LOG and pass through.
# If $ASCENDO_STREAM_LOG is unset/empty, behaves like ``cat`` (no-op pipe).
_stream_tee() {
    if [ -n "${ASCENDO_STREAM_LOG:-}" ]; then
        # Best-effort: even if the file ends up unwritable (race on disk
        # full / log rotated), we keep the pipeline producing output.
        tee -a "$ASCENDO_STREAM_LOG" 2>/dev/null || cat
    else
        cat
    fi
}

# _stream_emit <line>  — write a single sentinel/marker line into the
#   stream log. Used for per-package "currently processing" markers and
#   ``>>> PROGRESS <pct> <label>`` sentinels that the SSE endpoint
#   promotes to first-class ``progress`` events.
_stream_emit() {
    if [ -n "${ASCENDO_STREAM_LOG:-}" ]; then
        printf '%s\n' "$*" >> "$ASCENDO_STREAM_LOG" 2>/dev/null || true
    fi
}

# _stream_progress <pct> <label> — emit a ``>>> PROGRESS <pct> <label>``
#   sentinel. <pct> should be in [0, 100]; <label> is free text.
_stream_progress() {
    local _pct="${1:-0}"
    shift 2>/dev/null || true
    local _label="$*"
    _stream_emit ">>> PROGRESS $_pct $_label"
}

# _stream_item <label> — emit a ``>>> ITEM <label>`` sentinel for the
#   "currently processing" indicator (no percentage).
_stream_item() {
    _stream_emit ">>> ITEM $*"
}
