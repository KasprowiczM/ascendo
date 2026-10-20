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
shopt -s checkhash 2>/dev/null || true
if [ ! -f "$_ASCENDO_JSON_EMIT" ]; then
    echo "ascendo_json.sh: missing helper $_ASCENDO_JSON_EMIT" >&2
    return 1 2>/dev/null || exit 1
fi

# Bounded process runner (ascendo_run_with_timeout) for every phase script.
[ -f "${ASCENDO_JSON_DIR}/ascendo_proc.sh" ] && . "${ASCENDO_JSON_DIR}/ascendo_proc.sh"

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

# ascendo_action_required_token <reason> [current] [candidate]
#   Prints the machine-readable ACTION-REQUIRED token (one line, no newline
#   issues) that core/ascendo/orchestrator/report.py parses -- for ANY
#   category -- into the "Action required" section of REPORT.md and the
#   /runs/{id}/action-required endpoint.
#
#   Contract (keep in sync with report.ACTION_RE):
#     token   = ASCENDO-ACTION-REQUIRED reason=<r> cur=<c> cand=<d>
#     message = "<item id>: <human text> | <token>"      (json_add_message)
#   * <r> is a short machine code without spaces: major_upgrade,
#     ipad_pending, xcode_license, cask_orphan, cask_needs_admin,
#     needs_restart, app_in_use, self_update, no_silent_path, probe_broken.
#   * <c>/<d> are versions without spaces; "?" (default) means unknown.
#   * The item id is everything before the first ": " of the message; the
#     id must therefore not contain ": ".
#
#   Usage:
#     json_add_message "info" \
#         "${_id}: skipped -- app in use | $(ascendo_action_required_token app_in_use "$cur" "$cand")"
ascendo_action_required_token() {
    local _reason="${1:-unknown}"
    local _cur="${2:-?}"
    local _cand="${3:-?}"
    # Versions never contain whitespace; collapse any that do so the
    # \S+ parser on the other side stays deterministic.
    _reason="$(printf '%s' "$_reason" | tr -s '[:space:]' '_')"
    _cur="$(printf '%s' "$_cur" | tr -s '[:space:]' '_')"
    _cand="$(printf '%s' "$_cand" | tr -s '[:space:]' '_')"
    [ -n "$_cur" ] || _cur="?"
    [ -n "$_cand" ] || _cand="?"
    printf 'ASCENDO-ACTION-REQUIRED reason=%s cur=%s cand=%s' "$_reason" "$_cur" "$_cand"
}

# ascendo_status_token <code>
#   Prints the machine-readable status token that core/ascendo/orchestrator/
#   report.py (STATUS_RE) parses. Vocabulary (macOS_updates internet_status):
#     current_verified current_vendor updated update_available behind
#     needs_restart rollout_hold feed_stale unverified error_soft ...
#   Contract: message = "<item id>: <human text> | ASCENDO-STATUS code=<code>"
#   and the same <code> goes in json_add_message's third argument.
ascendo_status_token() {
    local _code
    _code="$(printf '%s' "${1:-unknown}" | tr -s '[:space:]' '_')"
    printf 'ASCENDO-STATUS code=%s' "$_code"
}

# ascendo_version_rel <candidate> <installed>
#   Three-way compare through the core helper version_cmp3: prints
#   newer|equal|older|unknown. Unparseable or empty input is `unknown`
#   (never a string-order guess), so callers can report skipped +
#   unknown_version instead of planning or "up-to-date"-ing on garbage.
ascendo_version_rel() {
    local _core
    _core="$(cd "${ASCENDO_JSON_DIR}/../../../core" 2>/dev/null && pwd)"
    PYTHONPATH="${_core:+$_core}${PYTHONPATH:+:$PYTHONPATH}" python3 -c '
import sys
from ascendo.utils.version import version_cmp3
r = version_cmp3(sys.argv[1], sys.argv[2])
print({1: "newer", 0: "equal", -1: "older"}.get(r, "unknown"))
' "${1:-}" "${2:-}" 2>/dev/null || printf 'unknown\n'
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
#     sudo -A softwareupdate -i "<label>" -R --verbose 2>&1 | _stream_tee
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

# =============================================================================
# Touch-ID-first sudo warming (Sesja 34)
# =============================================================================
# Apply scripts on macOS that need root (`mas`, `softwareupdate`) currently
# rely on `sudo -A` + a SUDO_ASKPASS helper that supplies a password the
# user typed once into the dashboard's elevation modal. That works, but it
# bypasses PAM entirely, so even when the user has `auth sufficient
# pam_tid.so` configured in /etc/pam.d/sudo_local, Touch ID never gets a
# chance — the password is the FIRST credential probe.
#
# The user wants the inverse: try Touch ID first, fall back to password.
# Cleanest macOS-native path: invoke `sudo -v` via osascript with
# administrator privileges. macOS's authorization-services dialog does
# pam_tid first when configured, and falls back to password if the
# user opts out / fingerprint fails.
#
# After this helper returns (whether via Touch ID or askpass cache), the
# subsequent `sudo -A` calls in the script use the warmed sudo
# timestamp (5-min validity window) and don't prompt again.
#
# Usage:
#     _ascendo_sudo_warm   # before the first `sudo -A …` in apply.sh
#
# Returns 0 (best-effort), except in an UNATTENDED run with a cold cache: rc 1
# and ASCENDO_NO_SUDO=1 exported. Apply scripts then run _ascendo_sudo_gate.
# Apply scripts MUST still handle the
# possibility that sudo will fail — this only improves the credential-
# entry UX, doesn't guarantee elevation.
# _ascendo_sudo <argv...>
#
# Wrapper that picks `sudo -A` (askpass-driven, dashboard SPA flow) or
# plain `sudo` (TTY-PAM, Touch-ID-first flow) based on SUDO_ASKPASS.
#
# Why: `sudo -A` requires SUDO_ASKPASS pointing at an executable; if it
# isn't set, sudo errors out. The dashboard sets it after the user types
# their password into the SPA modal, but operators who skipped the modal
# (PAM Touch-ID workflow) have no askpass — they want plain `sudo` so
# the kernel falls through PAM and uses pam_tid.so.
#
# Apply scripts MUST `_ascendo_sudo_warm` before calling this so the
# credentials are pre-cached; this helper assumes the timestamp is hot.
_ascendo_sudo() {
    # ASCENDO_NO_SUDO=1 (policy, or exported by an unattended warm-up): refuse
    # without ever invoking sudo. Callers map rc 77 to a soft deferral.
    if [ "${ASCENDO_NO_SUDO:-0}" = "1" ]; then
        return 77
    fi
    # NB: bare `sudo`, not `/usr/bin/sudo`, so test fixtures can shadow
    # via PATH (the existing fake_sudo pattern in test_apply_*_script.py).
    if [ -n "${SUDO_ASKPASS:-}" ] && [ -x "${SUDO_ASKPASS}" ]; then
        sudo -A "$@"
    elif [ "${ASCENDO_SUDO_NONINTERACTIVE:-0}" = "1" ]; then
        # The apply gate (_ascendo_sudo_gate) proved the timestamp is hot:
        # `-n` makes a lapsed cache FAIL instead of prompting on a TTY (or
        # stalling in a background pgroup).
        sudo -n "$@"
    else
        sudo "$@"
    fi
}

# _ascendo_sudo_prefix
#   Prints the sudo command prefix `_ascendo_sudo` would use ("sudo -A" when an
#   askpass helper is wired, else "sudo"), so a caller can hand the whole argv
#   to an EXECUTABLE-only wrapper such as `ascendo_run_with_timeout`
#   (which must not be given a shell function). Word-split it unquoted:
#       ascendo_run_with_timeout 1800 $(_ascendo_sudo_prefix) env X=1 cmd ...
_ascendo_sudo_prefix() {
    if [ "${ASCENDO_NO_SUDO:-0}" = "1" ]; then
        printf 'false'     # refuses: the wrapped command never runs
    elif [ -n "${SUDO_ASKPASS:-}" ] && [ -x "${SUDO_ASKPASS}" ]; then
        printf 'sudo -A'
    elif [ "${ASCENDO_SUDO_NONINTERACTIVE:-0}" = "1" ]; then
        printf 'sudo -n'
    else
        printf 'sudo'
    fi
}

# _ascendo_sudo_unattended -- 0 for a scheduled / explicitly unattended run
#   (TRIGGER=scheduler or ASCENDO_UNATTENDED=1): there is nobody to approve a
#   Touch ID sheet or type a password, so sudo may only be used if the cache
#   is already hot.
_ascendo_sudo_unattended() {
    [ "${TRIGGER:-}" = "scheduler" ] || [ "${ASCENDO_UNATTENDED:-0}" = "1" ]
}

# _ascendo_sudo_gate -- post-warm authorization gate for root-only apply steps.
#   Returns 0 when root work may proceed. Returns 1 (a SOFT deferral, never a
#   failure) and sets ASCENDO_SUDO_GATE_CODE / ASCENDO_SUDO_GATE_TEXT when:
#     * ASCENDO_NO_SUDO=1 (policy: skip ALL root work, even with a hot cache --
#       also exported by _ascendo_sudo_warm in unattended mode), or
#     * no askpass helper is wired AND `sudo -n true` fails (cold cache).
#   The criterion is the cache/askpass state AFTER the warm-up, NOT "no tty":
#   the TTY-less Touch-ID flow (`sudo -v </dev/null`, Sesja 81) leaves a hot
#   cache and must keep working. NB: a TTY-less ticket is keyed on the PARENT
#   pid, so `sudo -n` only works for direct children of the warming script;
#   apply steps therefore use `-n` ONLY in UNATTENDED runs (interactive installs
#   use plain `sudo`, which can re-prompt / Touch ID without a tty).
ASCENDO_SUDO_GATE_CODE=""
ASCENDO_SUDO_GATE_TEXT=""
_ascendo_sudo_gate() {
    ASCENDO_SUDO_GATE_CODE=""
    ASCENDO_SUDO_GATE_TEXT=""
    if [ "${ASCENDO_NO_SUDO:-0}" = "1" ]; then
        if _ascendo_sudo_unattended; then
            ASCENDO_SUDO_GATE_CODE="SUDO_UNAVAILABLE_UNATTENDED"
            ASCENDO_SUDO_GATE_TEXT="needs administrator; skipped in unattended run -- open Ascendo and run apply"
        else
            ASCENDO_SUDO_GATE_CODE="SUDO_UNAVAILABLE"
            ASCENDO_SUDO_GATE_TEXT="ASCENDO_NO_SUDO=1: root-only steps are skipped -- unset it and re-run to install"
        fi
        return 1
    fi
    if [ -n "${SUDO_ASKPASS:-}" ] && [ -x "${SUDO_ASKPASS}" ]; then
        return 0
    fi
    if sudo -n true </dev/null >/dev/null 2>&1; then
        return 0
    fi
    ASCENDO_SUDO_GATE_CODE="SUDO_UNAVAILABLE"
    ASCENDO_SUDO_GATE_TEXT="administrator authorization is not available -- authenticate interactively and re-run"
    return 1
}

_ascendo_sudo_warm() {
    # Unattended (scheduled) run: never prompt. Use sudo only if the cache is
    # already hot; otherwise switch ALL root work off for this process tree and
    # tell the caller (rc 1) so it can defer softly. Placed before the
    # test-fixture opt-out: it is non-interactive by construction (`sudo -n`).
    if _ascendo_sudo_unattended; then
        if sudo -n -v </dev/null 2>/dev/null; then
            return 0
        fi
        export ASCENDO_NO_SUDO=1
        return 1
    fi
    # Test-fixture opt-out: pytest exports PYTEST_CURRENT_TEST for every
    # test it runs; ASCENDO_SUDO_WARM_DISABLE is the explicit operator
    # escape hatch.
    if [ -n "${PYTEST_CURRENT_TEST:-}" ] || [ -n "${ASCENDO_SUDO_WARM_DISABLE:-}" ]; then
        return 0
    fi
    # 0. Already authed in the last ~5 min? Done — no prompt at all.
    if sudo -n -v 2>/dev/null; then
        return 0
    fi
    # 0b. SUDO_ASKPASS already wired (user pre-authenticated via the
    #     dashboard's /elevation/auth modal — MacElevation registered an
    #     askpass helper that echoes the cached password). The subsequent
    #     `sudo -A` calls will pick up the helper directly. No prompt at
    #     all is needed here, and trying to warm via TTY would surface a
    #     duplicate Touch ID dialog on top of the SPA-side cache.
    if [ -n "${SUDO_ASKPASS:-}" ] && [ -x "${SUDO_ASKPASS}" ]; then
        return 0
    fi

    # 1. PAM path — Touch ID first, password fallback. `sudo -v` runs
    #    sudo's PAM stack, which respects /etc/pam.d/sudo (-> sudo_local
    #    on Sonoma+). With `auth sufficient pam_tid.so` configured, the
    #    macOS biometric subsystem presents the Touch ID sheet ITSELF and
    #    `sufficient` short-circuits before any password module — so a
    #    successful tap needs NEITHER a controlling TTY NOR an askpass
    #    helper. pam_tid does not use the PAM conversation function /
    #    stdin / a TTY at all; only the password *fallback* does.
    #
    #    The earlier `[ -e /dev/tty ]` gate was wrong: the /dev/tty device
    #    node always exists, but a process spawned by the dashboard
    #    (`ascendo web start`, Tauri sidecar, Ascendo.app) has no
    #    controlling terminal, so the forced `</dev/tty` redirect failed
    #    ("Device not configured") and Touch ID never got a chance — every
    #    apply fell through to the osascript SecurityAgent password popup
    #    (Authorization Services, `system.privilege.admin` — a DIFFERENT
    #    auth rule that does NOT consult pam_tid). That was the
    #    "osascript keeps asking for a password" regression.
    if [ "$(uname -s)" = "Darwin" ]; then
        # 1a. TTY-independent: let pam_tid show the Touch ID sheet. stdin
        #     from /dev/null so sudo never blocks on an inherited pipe;
        #     no `-A` so no askpass is consulted. If Touch ID is
        #     unavailable/declined sudo would need a password and, with
        #     no TTY+no askpass, fails cleanly — the correct fallback
        #     signal (the operator should use the dashboard password
        #     modal, i.e. the SUDO_ASKPASS path handled in step 0b).
        if sudo -v </dev/null 2>/dev/null; then
            return 0
        fi
        # 1b. Real controlling terminal present (operator launched from a
        #     shell): route the prompt to it so an in-terminal Touch ID
        #     sheet or the password fallback is actually visible. Probe by
        #     trying to OPEN /dev/tty (not `[ -e ]`, which is always true).
        if ( : >/dev/tty ) 2>/dev/null && sudo -v </dev/tty 2>/dev/tty; then
            return 0
        fi
    else
        # Linux / other Unix: sudo -v on the inherited TTY.
        sudo -v && return 0 || true
    fi

    # 2. Last-resort GUI fallback — STRICTLY opt-in, never auto-enabled.
    #    `osascript ... with administrator privileges` uses Authorization
    #    Services (`system.privilege.admin`), a different auth rule from
    #    sudo's PAM stack: it does NOT consult pam_tid.so, so it is
    #    PASSWORD-ONLY even when Touch ID is configured. It exists only
    #    for a genuinely headless macOS box (no Touch ID, no password
    #    modal) where an operator has explicitly accepted that trade-off
    #    via `export ASCENDO_SUDO_ALLOW_GUI=1`. The dashboard no longer
    #    sets this automatically (that was the root-cause regression).
    #    Default: stay silent and let the subsequent sudo raise its own
    #    error so the operator knows to enable Touch ID or use the modal.
    if [ "$(uname -s)" = "Darwin" ] \
       && [ "${ASCENDO_SUDO_ALLOW_GUI:-0}" = "1" ] \
       && [ -z "${ASCENDO_SUDO_NO_GUI:-}" ] \
       && command -v osascript >/dev/null 2>&1; then
        osascript -e 'do shell script "/usr/bin/sudo -v" with administrator privileges' \
            >/dev/null 2>&1 || true
    fi
    sudo -n -v 2>/dev/null || true
    return 0
}
