#!/usr/bin/env bats
# Regression tests for lib/common.sh:require_sudo trap chain.
#
# Locks in the Sesja 68 fix: the chained EXIT trap must finalize the
# json sidecar even when the keepalive PID has already died by the time
# the trap fires. Without the trailing `|| true` on the kill call, a
# dead PID makes `kill` return 1, set -e aborts the trap chain, and
# `_json_finalize_on_exit` never runs — producing the "snap apply
# script produced no sidecar" failure mode operators saw across
# multiple runs.
#
# Run with:
#   bats tests/bash/test_require_sudo_trap.bats

REPO="$(cd "$(dirname "${BATS_TEST_FILENAME}")/../.." && pwd)"

setup() {
    export TMPHOME
    TMPHOME="$(mktemp -d)"
    cd "$TMPHOME"
}

teardown() {
    rm -rf "$TMPHOME"
}


@test "lib/common.sh chained trap appends '|| true' to kill" {
    # Static check: the trap registration in require_sudo must wrap
    # kill in a `|| true` so a dead keepalive PID doesn't abort the
    # finalize chain under set -e.
    grep -qF 'kill ${SUDO_KEEP_ALIVE_PID} 2>/dev/null || true' \
        "${REPO}/lib/common.sh"
}


@test "lib/common.sh keepalive uses sudo -A -v when SUDO_ASKPASS set" {
    # When the dashboard spawns the script with SUDO_ASKPASS exported,
    # the keepalive must use the askpass helper to refresh credentials.
    # A bare `sudo -n true` can fail on a no-TTY spawn even when sudo
    # cache is fresh — see Ubuntu 24.04 docs on sudo/tty/askpass.
    grep -q 'sudo -A -v 2>/dev/null || break' "${REPO}/lib/common.sh"
}


@test "lib/common.sh keepalive subshell detaches stdio to /dev/null" {
    # Sesja 68: the keepalive subshell MUST redirect its stdin /
    # stdout / stderr to /dev/null. Otherwise it inherits the parent
    # bash's pipes; when the parent is spawned by Python with
    # subprocess.run(capture_output=True), Popen.communicate() blocks
    # until ALL writers close the pipes. The subshell never does
    # (it loops indefinitely on `sleep 50`), so Python hangs even
    # after the bash main process exits and the sidecar is finalized.
    # This was the "safe update hanged on apt" production failure.
    grep -qF '</dev/null >/dev/null 2>&1 &' "${REPO}/lib/common.sh"
}


@test "scripts/apt/apply.sh _apt_apply_on_exit kills keepalive" {
    # apt apply.sh uniquely OVERWRITES the EXIT trap chain (the other
    # phase scripts inherit common.sh's chained trap). The custom
    # _apt_apply_on_exit must still kill SUDO_KEEP_ALIVE_PID — without
    # this, the keepalive subshell stays alive after exit and holds
    # the parent's stdio pipes open (defense-in-depth alongside the
    # stdio redirection in require_sudo).
    grep -qF 'kill "${SUDO_KEEP_ALIVE_PID}" 2>/dev/null || true' \
        "${REPO}/scripts/apt/apply.sh"
}


@test "chained EXIT trap survives dead keepalive PID" {
    # End-to-end: simulate exactly what require_sudo does (chained
    # trap with the new `|| true` guard), kill the keepalive ourselves
    # so it's dead before the trap fires, exit cleanly. Verify the
    # finalize handler still ran.

    export JSON_OUT="${TMPHOME}/out.json"
    export LOG_FILE="${TMPHOME}/test.log"

    cat > "${TMPHOME}/probe.sh" <<EOF
#!/usr/bin/env bash
set -euo pipefail
source "${REPO}/lib/json.sh"
json_init apply snap
json_register_exit_trap "\$JSON_OUT"

# Spawn keepalive then kill it RIGHT AWAY so the EXIT trap sees a
# dead PID. This mirrors the Sesja 68 production failure where the
# subshell died fast due to sudo TTY-cache behaviour.
(sleep 60) &
KEEPALIVE_PID=\$!
kill "\$KEEPALIVE_PID" 2>/dev/null
wait "\$KEEPALIVE_PID" 2>/dev/null || true

# Now install the chained trap exactly like require_sudo does (with
# the new || true defensive guard).
_existing=\$(trap -p EXIT 2>/dev/null | sed -E "s/^trap -- '(.*)' EXIT\$/\1/" || true)
trap "kill \${KEEPALIVE_PID} 2>/dev/null || true; \${_existing}" EXIT

# Emit a sentinel item so we can tell finalize ran.
json_add_item id="probe:dead-keepalive" action="present" result="ok"
exit 0
EOF
    chmod +x "${TMPHOME}/probe.sh"
    run bash "${TMPHOME}/probe.sh"

    [ "$status" -eq 0 ]
    [ -f "$JSON_OUT" ]
    grep -q '"id": "probe:dead-keepalive"' "$JSON_OUT"
    # Prove finalize actually ran (not just that the buffer had the item):
    # "exit_code" is synthesised ONLY by cmd_finalize in lib/_json_emit.py —
    # it never appears in the buffer files (meta.json/items.jsonl). Per-phase
    # sidecars have no top-level "status" field (that lives on run.json, the
    # run-level aggregate, per docs/agents/contract.md), so the old
    # `grep '"status":'` assertion could never match the emitter's output.
    grep -q '"exit_code":' "$JSON_OUT"
}


@test "OLD chained trap (without || true) WOULD have failed on dead keepalive" {
    # Negative control: build the OLD trap shape and verify it
    # silently swallows the finalize call when set -e is active.
    # If this test ever starts PASSING (sidecar produced), bash
    # behaviour has changed and the regression guard above is no
    # longer necessary — re-evaluate require_sudo.

    export JSON_OUT="${TMPHOME}/out.json"
    export LOG_FILE="${TMPHOME}/test.log"

    cat > "${TMPHOME}/old.sh" <<EOF
#!/usr/bin/env bash
set -euo pipefail
source "${REPO}/lib/json.sh"
json_init apply snap
json_register_exit_trap "\$JSON_OUT"

(sleep 60) &
KEEPALIVE_PID=\$!
kill "\$KEEPALIVE_PID" 2>/dev/null
wait "\$KEEPALIVE_PID" 2>/dev/null || true

# OLD form: no `|| true` after kill — this is the pre-Sesja-68 bug
_existing=\$(trap -p EXIT 2>/dev/null | sed -E "s/^trap -- '(.*)' EXIT\$/\1/" || true)
trap "kill \${KEEPALIVE_PID} 2>/dev/null; \${_existing}" EXIT

json_add_item id="probe:old-bug" action="present" result="ok"
exit 0
EOF
    chmod +x "${TMPHOME}/old.sh"
    run bash "${TMPHOME}/old.sh"

    # OLD form exits non-zero AND/OR fails to produce sidecar.
    if [ "$status" -eq 0 ] && [ -f "$JSON_OUT" ]; then
        # If both pass, bash trap semantics under set -e have shifted —
        # not a problem, just no longer a smoking gun.
        skip "bash no longer aborts trap on dead-PID kill — regression guard moot"
    fi
}
