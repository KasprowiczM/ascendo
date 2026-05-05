#!/usr/bin/env bash
# =============================================================================
# bin/validate-macos.sh -- Ascendo macOS-side validation harness
# =============================================================================
# Run AFTER `bash bin/install-dev-macos.sh`.
#
# Verifies (in order):
#   1. python3 -m ascendo --help / version / doctor all exit 0
#   2. Five-phase brew contract: check / plan / apply --dry-run / verify /
#      cleanup --dry-run each produces a sidecar with schema=ascendo/v1,
#      phase=<expected>, category=brew
#   3. Dashboard smoke: /version + /health, POST /runs/async + status poll,
#      clean teardown
#   4-7. (reserved for future stages)
#   8. mas + dashboard askpass round-trip (M5.2): doctor reports mas,
#      five-phase mas contract, /elevation/auth + /elevation/status cycle
#   9. LaunchServices inventory (M5.3): doctor reports system_profiler,
#      list.sh end-to-end produces sidecar with 50+ apps, classification
#      distribution sanity (system/mas/brew/web), MacOSAdapter.inventory()
#      Python wrapper
#  10. softwareupdate (M5.4): doctor reports softwareupdate,
#      five-phase softwareupdate contract via CLI (check/plan/verify/cleanup
#      + apply --dry-run; real apply EXCLUDED — would reboot the Mac)
#  11. Time Machine read-only (M5.4): doctor reports tmutil,
#      TimeMachineSnapshot.list() end-to-end (>=0 snapshots; no lower bound)
#  12. launchd scheduler round-trip (M5.5): doctor reports launchctl,
#      install + list + trigger + remove an `ascendo-validate-test` agent
#
# Exits 0 on full success, 1 with [FAIL] count otherwise.
# Final line on success: ALL CHECKS PASSED.
#
# Flags:
#   --port <N>         dashboard port (default: 8765)
#   --skip-dashboard   skip dashboard tests
# =============================================================================
set -o pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$REPO_ROOT"

DASHBOARD_PORT="${DASHBOARD_PORT:-8765}"
SKIP_DASHBOARD=0
while [ $# -gt 0 ]; do
    case "$1" in
        --port)           DASHBOARD_PORT="$2"; shift 2 ;;
        --skip-dashboard) SKIP_DASHBOARD=1; shift ;;
        *) printf "validate-macos.sh: unknown arg: %s\n" "$1" >&2; exit 2 ;;
    esac
done

FAIL_COUNT=0
PASS_COUNT=0

step() { printf "\n==> %s\n" "$1"; }

result() {
    local name="$1"
    local ok="$2"
    local detail="${3:-}"
    if [ "$ok" = "1" ]; then
        printf "  [PASS] %s\n" "$name"
        if [ -n "$detail" ]; then
            printf "         %s\n" "$detail"
        fi
        PASS_COUNT=$((PASS_COUNT + 1))
    else
        printf "  [FAIL] %s\n" "$name" >&2
        if [ -n "$detail" ]; then
            printf "         %s\n" "$detail" >&2
        fi
        FAIL_COUNT=$((FAIL_COUNT + 1))
    fi
}

# ── 1. CLI ────────────────────────────────────────────────────────────────────
step "1. CLI checks"

if python3 -m ascendo --help >/dev/null 2>&1; then
    result "ascendo --help" 1
else
    result "ascendo --help" 0 "exit $?"
fi

if out=$(python3 -m ascendo version 2>&1); then
    result "ascendo version" 1 "$out"
else
    result "ascendo version" 0 "$out"
fi

if out=$(python3 -m ascendo doctor 2>&1); then
    result "ascendo doctor" 1 "$(printf '%s' "$out" | head -3)"
else
    result "ascendo doctor" 0 "$(printf '%s' "$out" | head -5)"
fi

# ── 2. Five-phase brew contract ───────────────────────────────────────────────
step "2. Five-phase brew contract"
RUNS_DIR=$(mktemp -d -t ascendo_validate_XXXXXX)
export PYTHONPATH="$REPO_ROOT/adapters/macos:$REPO_ROOT/core"

phase_check() {
    local phase="$1"
    shift
    local extra_args="$*"
    local out rc sidecar sc_phase sc_cat sc_schema

    # Build the command; --dry-run must be passed as a positional arg here
    if [ -n "$extra_args" ]; then
        out=$(python3 -m ascendo run --category brew --phase "$phase" \
              --runs-dir "$RUNS_DIR" $extra_args 2>&1)
        rc=$?
    else
        out=$(python3 -m ascendo run --category brew --phase "$phase" \
              --runs-dir "$RUNS_DIR" 2>&1)
        rc=$?
    fi

    if [ $rc -ne 0 ]; then
        result "brew/$phase" 0 "$(printf '%s' "$out" | tail -10)"
        return
    fi

    # Find the sidecar for this phase
    sidecar=$(find "$RUNS_DIR" -name "${phase}__brew.json" -type f 2>/dev/null | head -1)
    if [ -z "$sidecar" ] || [ ! -f "$sidecar" ]; then
        result "brew/$phase" 0 "no sidecar found in $RUNS_DIR"
        return
    fi

    sc_phase=$(python3 -c "import json; d=json.load(open('$sidecar')); print(d['phase'])" 2>/dev/null)
    sc_cat=$(python3 -c "import json; d=json.load(open('$sidecar')); print(d['category'])" 2>/dev/null)
    sc_schema=$(python3 -c "import json; d=json.load(open('$sidecar')); print(d['schema'])" 2>/dev/null)

    if [ "$sc_phase" = "$phase" ] && [ "$sc_cat" = "brew" ] && [ "$sc_schema" = "ascendo/v1" ]; then
        result "brew/$phase" 1 "sidecar=$(basename "$sidecar")"
    else
        result "brew/$phase" 0 \
            "sidecar shape wrong: phase=$sc_phase category=$sc_cat schema=$sc_schema"
    fi
}

phase_check check
phase_check plan
phase_check apply --dry-run
phase_check verify
phase_check cleanup --dry-run

# ── 3. Dashboard ──────────────────────────────────────────────────────────────
if [ "$SKIP_DASHBOARD" -eq 0 ]; then
    step "3. Dashboard"
    LOG=$(mktemp -t ascendo_dash_XXXXXX)

    # Start dashboard in background
    python3 -m ascendo dashboard --port "$DASHBOARD_PORT" >"$LOG" 2>&1 &
    DASH_PID=$!

    # Wait up to 10s for dashboard to bind
    _i=0
    while [ $_i -lt 10 ]; do
        if curl -sf "http://127.0.0.1:$DASHBOARD_PORT/version" >/dev/null 2>&1; then
            break
        fi
        sleep 1
        _i=$((_i + 1))
    done

    if curl -sf "http://127.0.0.1:$DASHBOARD_PORT/version" >/dev/null 2>&1; then
        result "GET /version" 1
    else
        result "GET /version" 0 "$(tail -10 "$LOG")"
    fi

    if curl -sf "http://127.0.0.1:$DASHBOARD_PORT/health" >/dev/null 2>&1; then
        result "GET /health" 1
    else
        result "GET /health" 0 "$(tail -5 "$LOG")"
    fi

    # POST /runs/async -- kick a check phase run, poll status until completed
    ASYNC_BODY='{"phases":["check"],"categories":["brew"]}'
    RUN_ID=$(curl -sf -X POST "http://127.0.0.1:$DASHBOARD_PORT/runs/async" \
                 -H "Content-Type: application/json" -d "$ASYNC_BODY" 2>/dev/null \
             | python3 -c "import json,sys; print(json.load(sys.stdin)['run_id'])" 2>/dev/null)

    if [ -z "$RUN_ID" ]; then
        result "POST /runs/async" 0 "could not parse run_id from response"
    else
        # Poll status up to 60s
        ASYNC_STATUS=""
        _j=0
        while [ $_j -lt 60 ]; do
            ASYNC_STATUS=$(curl -sf \
                "http://127.0.0.1:$DASHBOARD_PORT/runs/$RUN_ID/status" 2>/dev/null \
                | python3 -c "import json,sys; print(json.load(sys.stdin)['status'])" 2>/dev/null)
            if [ "$ASYNC_STATUS" = "completed" ] || [ "$ASYNC_STATUS" = "failed" ]; then
                break
            fi
            sleep 1
            _j=$((_j + 1))
        done

        if [ "$ASYNC_STATUS" = "completed" ]; then
            result "POST /runs/async + poll status" 1 "run_id=$RUN_ID"
        else
            result "POST /runs/async + poll status" 0 "final status=$ASYNC_STATUS"
        fi
    fi

    # Tear down dashboard
    kill "$DASH_PID" 2>/dev/null || true
    wait "$DASH_PID" 2>/dev/null || true
    rm -f "$LOG"
fi

# ── 8. mas + dashboard askpass round-trip ─────────────────────────────────────
step "8. mas + dashboard askpass (M5.2)"

# 8.1 doctor reports mas component
step "8.1 doctor reports mas component"
DOCTOR_OUT=$(python3 -m ascendo doctor 2>&1)
if printf '%s' "$DOCTOR_OUT" | grep -qE '^\s+mas\s+(ok|degraded|unavailable|error)'; then
    MAS_LINE=$(printf '%s' "$DOCTOR_OUT" | grep -E '^\s+mas\s+(ok|degraded|unavailable|error)' | head -1)
    result "8.1 doctor: mas component" 1 "$MAS_LINE"
    MAS_AVAILABLE=1
else
    result "8.1 doctor: mas component" 0 "mas not listed in doctor output"
    MAS_AVAILABLE=0
fi

# 8.2-8.6 five-phase mas contract (skip if mas not available)
mas_phase_check() {
    local label="$1"
    local phase="$2"
    shift 2
    local extra_args="$*"
    local out rc sidecar sc_phase sc_cat sc_schema
    local MAS_RUNS_DIR
    MAS_RUNS_DIR=$(mktemp -d -t ascendo_mas_validate_XXXXXX)

    if [ -n "$extra_args" ]; then
        out=$(python3 -m ascendo run --category mas --phase "$phase" \
              --runs-dir "$MAS_RUNS_DIR" $extra_args 2>&1)
        rc=$?
    else
        out=$(python3 -m ascendo run --category mas --phase "$phase" \
              --runs-dir "$MAS_RUNS_DIR" 2>&1)
        rc=$?
    fi

    if [ $rc -ne 0 ]; then
        result "$label" 0 "exit $rc: $(printf '%s' "$out" | tail -5)"
        rm -rf "$MAS_RUNS_DIR"
        return
    fi

    sidecar=$(find "$MAS_RUNS_DIR" -name "${phase}__mas.json" -type f 2>/dev/null | head -1)
    if [ -z "$sidecar" ] || [ ! -f "$sidecar" ]; then
        result "$label" 0 "no sidecar found in $MAS_RUNS_DIR"
        rm -rf "$MAS_RUNS_DIR"
        return
    fi

    sc_phase=$(python3 -c "import json; d=json.load(open('$sidecar')); print(d['phase'])" 2>/dev/null)
    sc_cat=$(python3 -c "import json; d=json.load(open('$sidecar')); print(d['category'])" 2>/dev/null)
    sc_schema=$(python3 -c "import json; d=json.load(open('$sidecar')); print(d['schema'])" 2>/dev/null)

    if [ "$sc_phase" = "$phase" ] && [ "$sc_cat" = "mas" ] && [ "$sc_schema" = "ascendo/v1" ]; then
        result "$label" 1 "sidecar=$(basename "$sidecar")"
    else
        result "$label" 0 "sidecar shape wrong: phase=$sc_phase category=$sc_cat schema=$sc_schema"
    fi
    rm -rf "$MAS_RUNS_DIR"
}

if [ "$MAS_AVAILABLE" -eq 1 ]; then
    mas_phase_check "8.2 mas check"          check
    mas_phase_check "8.3 mas plan"           plan
    mas_phase_check "8.4 mas apply --dry-run" apply --dry-run
    mas_phase_check "8.5 mas verify"         verify
    mas_phase_check "8.6 mas cleanup"        cleanup
else
    printf "  [skip] 8.2 mas check (mas not available)\n"
    printf "  [skip] 8.3 mas plan (mas not available)\n"
    printf "  [skip] 8.4 mas apply --dry-run (mas not available)\n"
    printf "  [skip] 8.5 mas verify (mas not available)\n"
    printf "  [skip] 8.6 mas cleanup (mas not available)\n"
fi

# 8.7 dashboard askpass round-trip (requires $SUDO_PW; skipped in CI)
step "8.7 dashboard askpass round-trip"
if [ -z "${SUDO_PW:-}" ]; then
    printf "  [skip] 8.7 dashboard askpass round-trip (SUDO_PW not set; export SUDO_PW=<password> to enable)\n"
else
    # Use env override or +100 offset (not +10) to avoid collision with stale
    # dashboards when --port is shifted near defaults.
    _ASKPASS_PORT="${ASCENDO_VALIDATE_ASKPASS_PORT:-$((DASHBOARD_PORT + 100))}"
    _ASKPASS_LOG=$(mktemp -t ascendo_dash8_XXXXXX)
    _ASKPASS_PID=""

    # Ensure dashboard is torn down on exit from this block
    # NOTE: this overrides any pre-existing EXIT trap; future stages must coordinate.
    _cleanup_askpass_dash() {
        if [ -n "$_ASKPASS_PID" ]; then
            kill "$_ASKPASS_PID" 2>/dev/null || true
            wait "$_ASKPASS_PID" 2>/dev/null || true
        fi
        rm -f "$_ASKPASS_LOG"
    }
    trap '_cleanup_askpass_dash' EXIT INT TERM

    # Start dashboard in background on a dedicated port
    python3 -m ascendo dashboard --port "$_ASKPASS_PORT" >"$_ASKPASS_LOG" 2>&1 &
    _ASKPASS_PID=$!

    # Wait up to 10s for dashboard to bind
    _ai=0
    while [ $_ai -lt 10 ]; do
        if curl -sf "http://127.0.0.1:$_ASKPASS_PORT/version" >/dev/null 2>&1; then
            break
        fi
        sleep 1
        _ai=$((_ai + 1))
    done

    if ! curl -sf "http://127.0.0.1:$_ASKPASS_PORT/version" >/dev/null 2>&1; then
        result "8.7 dashboard for askpass" 0 "dashboard did not bind on port $_ASKPASS_PORT"
    else
        # 8.7a GET /elevation/status -> registered: false
        STATUS_A=$(curl -sf "http://127.0.0.1:$_ASKPASS_PORT/elevation/status" 2>/dev/null \
            | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('registered','?'))" 2>/dev/null)
        if [ "$STATUS_A" = "False" ] || [ "$STATUS_A" = "false" ]; then
            result "8.7a GET /elevation/status (registered=false)" 1
        else
            result "8.7a GET /elevation/status (registered=false)" 0 "got registered=$STATUS_A"
        fi

        # 8.7b POST /elevation/auth -> 200
        # Use jq (already a hard dep) to build the JSON body safely — prevents
        # shell injection if SUDO_PW contains ", \, $, or backticks.
        AUTH_CODE=""
        if ! command -v jq >/dev/null 2>&1; then
            result "8.7b POST /elevation/auth" 0 "jq missing — needed for safe password JSON encoding"
        else
            _auth_body="$(jq -n --arg pw "$SUDO_PW" '{password: $pw}')"
            AUTH_CODE=$(curl -fsS -o /dev/null -w "%{http_code}" -X POST \
                "http://127.0.0.1:$_ASKPASS_PORT/elevation/auth" \
                -H "Content-Type: application/json" \
                -d "$_auth_body" 2>/dev/null)
            if [ "$AUTH_CODE" = "200" ]; then
                result "8.7b POST /elevation/auth" 1
            else
                result "8.7b POST /elevation/auth" 0 "HTTP $AUTH_CODE (wrong password or no elevation support)"
            fi
        fi

        # 8.7c GET /elevation/status -> registered: true
        STATUS_B=$(curl -sf "http://127.0.0.1:$_ASKPASS_PORT/elevation/status" 2>/dev/null \
            | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('registered','?'))" 2>/dev/null)
        if [ "$STATUS_B" = "True" ] || [ "$STATUS_B" = "true" ]; then
            result "8.7c GET /elevation/status (registered=true)" 1
        else
            result "8.7c GET /elevation/status (registered=true)" 0 "got registered=$STATUS_B"
        fi

        # 8.7d POST /runs/async (mas apply, dry_run=true) + poll status
        ASYNC_MAS_BODY='{"phases":["apply"],"categories":["mas"],"dry_run":true}'
        MAS_RUN_ID=$(curl -sf -X POST "http://127.0.0.1:$_ASKPASS_PORT/runs/async" \
            -H "Content-Type: application/json" -d "$ASYNC_MAS_BODY" 2>/dev/null \
            | python3 -c "import json,sys; print(json.load(sys.stdin)['run_id'])" 2>/dev/null)

        if [ -z "$MAS_RUN_ID" ]; then
            result "8.7d POST /runs/async (mas apply dry-run)" 0 "could not parse run_id"
        else
            MAS_ASYNC_STATUS=""
            _mj=0
            while [ $_mj -lt 60 ]; do
                MAS_ASYNC_STATUS=$(curl -sf \
                    "http://127.0.0.1:$_ASKPASS_PORT/runs/$MAS_RUN_ID/status" 2>/dev/null \
                    | python3 -c "import json,sys; print(json.load(sys.stdin)['status'])" 2>/dev/null)
                if [ "$MAS_ASYNC_STATUS" = "completed" ] || [ "$MAS_ASYNC_STATUS" = "failed" ]; then
                    break
                fi
                sleep 1
                _mj=$((_mj + 1))
            done
            if [ "$MAS_ASYNC_STATUS" = "completed" ]; then
                result "8.7d POST /runs/async + poll status" 1 "run_id=$MAS_RUN_ID"
            else
                result "8.7d POST /runs/async + poll status" 0 "final status=$MAS_ASYNC_STATUS"
            fi
        fi

        # 8.7e POST /elevation/invalidate
        INV_CODE=$(curl -sf -o /dev/null -w "%{http_code}" -X POST \
            "http://127.0.0.1:$_ASKPASS_PORT/elevation/invalidate" 2>/dev/null)
        if [ "$INV_CODE" = "200" ]; then
            result "8.7e POST /elevation/invalidate" 1
        else
            result "8.7e POST /elevation/invalidate" 0 "HTTP $INV_CODE"
        fi

        # 8.7f GET /elevation/status -> registered: false
        STATUS_C=$(curl -sf "http://127.0.0.1:$_ASKPASS_PORT/elevation/status" 2>/dev/null \
            | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('registered','?'))" 2>/dev/null)
        if [ "$STATUS_C" = "False" ] || [ "$STATUS_C" = "false" ]; then
            result "8.7f GET /elevation/status (registered=false after invalidate)" 1
        else
            result "8.7f GET /elevation/status (registered=false after invalidate)" 0 "got registered=$STATUS_C"
        fi
    fi

    _cleanup_askpass_dash
    trap - EXIT INT TERM
fi

# ── 9. LaunchServices inventory (M5.3) ───────────────────────────────────────
step "9. LaunchServices inventory (M5.3)"

# 9.1 doctor reports system_profiler component
step "9.1 doctor reports system_profiler component"
DOCTOR_INV_OUT="$(python3 -m ascendo doctor 2>&1)"
if printf '%s\n' "$DOCTOR_INV_OUT" | grep -qE '^\s+system_profiler\s+(ok|degraded|unavailable|error)'; then
    SP_LINE="$(printf '%s\n' "$DOCTOR_INV_OUT" | grep -E '^\s+system_profiler\s+' | head -1)"
    result "9.1 doctor: system_profiler component" 1 "$SP_LINE"
else
    result "9.1 doctor: system_profiler component" 0 "system_profiler not listed in doctor output"
fi

# 9.2 direct list.sh invocation (real system_profiler)
step "9.2 inventory list.sh end-to-end"
INV_DIR="$(mktemp -d "${TMPDIR:-/tmp}/ascendo-validate-inv-XXXXXX")"
INV_RID="$(uuidgen 2>/dev/null || python3 -c 'import uuid; print(uuid.uuid4())')"
INV_SC=""
if bash "$REPO_ROOT/adapters/macos/scripts/inventory/list.sh" \
        --run-id "$INV_RID" --trigger cli --profile default \
        --output-dir "$INV_DIR" >/dev/null 2>&1; then
    INV_SC="$INV_DIR/$INV_RID/check__inventory.json"
    if [ -f "$INV_SC" ]; then
        ITEM_COUNT="$(jq '.items | length' "$INV_SC")"
        if [ "$ITEM_COUNT" -ge 50 ]; then
            result "9.2 inventory list.sh end-to-end" 1 "$ITEM_COUNT apps enumerated"
        else
            result "9.2 inventory list.sh end-to-end" 0 "$ITEM_COUNT apps; expected >=50"
        fi
    else
        result "9.2 inventory list.sh end-to-end" 0 "no sidecar produced at $INV_SC"
    fi
else
    result "9.2 inventory list.sh end-to-end" 0 "script exited non-zero"
fi

# 9.3 classification distribution sanity
step "9.3 classification distribution"
if [ -n "$INV_SC" ] && [ -f "$INV_SC" ]; then
    SYS_N="$(jq '[.items[] | select(.source.type=="system")] | length' "$INV_SC")"
    MAS_N="$(jq '[.items[] | select(.source.type=="mas")] | length' "$INV_SC")"
    BREW_N="$(jq '[.items[] | select(.source.type=="brew")] | length' "$INV_SC")"
    WEB_N="$(jq '[.items[] | select(.source.type=="web")] | length' "$INV_SC")"
    if [ "$SYS_N" -ge 5 ] && [ "$MAS_N" -ge 1 ] && [ "$((BREW_N + WEB_N))" -ge 5 ]; then
        result "9.3 classification distribution" 1 \
            "system=$SYS_N mas=$MAS_N brew=$BREW_N web=$WEB_N"
    else
        result "9.3 classification distribution" 0 \
            "system=$SYS_N mas=$MAS_N brew=$BREW_N web=$WEB_N; want SYS>=5 MAS>=1 BREW+WEB>=5"
    fi
else
    result "9.3 classification distribution" 0 "no sidecar (9.2 failed)"
fi

# 9.4 Python wrapper end-to-end via the adapter
step "9.4 MacOSAdapter.inventory().list_installed()"
PY_INV_OUT="$(python3 -c "
from ascendo_macos.adapter import MacOSAdapter
a = MacOSAdapter()
host = a.detect_host()
inv = a.inventory()
pkgs = inv.list_installed(host)
print('inventory enumerated %d packages' % len(pkgs))
assert len(pkgs) >= 50, 'too few packages: %d' % len(pkgs)
" 2>&1)"
PY_INV_RC=$?
if [ "$PY_INV_RC" -eq 0 ]; then
    printf '         %s\n' "$PY_INV_OUT"
    result "9.4 MacOSAdapter.inventory() end-to-end" 1
else
    printf '%s\n' "$PY_INV_OUT" >&2
    result "9.4 MacOSAdapter.inventory() end-to-end" 0 "see above"
fi

# Cleanup Stage 9 temp dir
[ -n "${INV_DIR:-}" ] && [ -d "$INV_DIR" ] && rm -rf "$INV_DIR"

# ============================================================
# Stage 10 — softwareupdate (M5.4)
# ============================================================
step "10. softwareupdate (M5.4)"

# Capture doctor output ONCE for both Stage 10 + Stage 11 component grep.
DOCTOR_OUT_M54="$(python3 -m ascendo doctor 2>&1)"

# Step 10.1 — doctor reports softwareupdate component
step "10.1 doctor: softwareupdate component"
if printf '%s\n' "$DOCTOR_OUT_M54" | grep -qE '^[[:space:]]+softwareupdate[[:space:]]+(ok|degraded|unavailable|error)'; then
    printf '%s\n' "$DOCTOR_OUT_M54" | grep -E '^[[:space:]]+softwareupdate[[:space:]]+'
    result "10.1 doctor: softwareupdate component" 1
else
    result "10.1 doctor: softwareupdate component" 0 "no softwareupdate line in doctor output"
fi

# Stage 10 phase probes share one runs-dir
SU_DIR="$(mktemp -d "${TMPDIR:-/tmp}/ascendo-validate-su-XXXXXX")"

# Helper: run softwareupdate phase via CLI and assert the sidecar lands.
# CLI assigns its own run-id; we use `find` to detect any matching sidecar.
_su_phase_probe() {
    local _phase="$1"
    local _label="$2"
    shift 2
    step "$_label"
    if python3 -m ascendo run \
            --category softwareupdate --phase "$_phase" \
            --runs-dir "$SU_DIR" "$@" >/dev/null 2>&1; then
        if find "$SU_DIR" -name "${_phase}__softwareupdate.json" -print -quit 2>/dev/null | grep -q .; then
            result "$_label" 1 "sidecar=${_phase}__softwareupdate.json"
        else
            result "$_label" 0 "no sidecar produced"
        fi
    else
        result "$_label" 0 "CLI exit non-zero"
    fi
}

_su_phase_probe "check"   "10.2 softwareupdate check"
_su_phase_probe "plan"    "10.3 softwareupdate plan"
_su_phase_probe "verify"  "10.4 softwareupdate verify (soft no-op)"
_su_phase_probe "cleanup" "10.5 softwareupdate cleanup"

# Step 10.6 — apply --dry-run (NEVER invokes sudo on real Mac)
_su_phase_probe "apply" "10.6 softwareupdate apply --dry-run" --dry-run

# Cleanup Stage 10 temp dir
[ -n "${SU_DIR:-}" ] && [ -d "$SU_DIR" ] && rm -rf "$SU_DIR"

# ============================================================
# Stage 11 — Time Machine read-only (M5.4)
# ============================================================
step "11. Time Machine read-only (M5.4)"

# Step 11.1 — doctor reports tmutil component
step "11.1 doctor: tmutil component"
if printf '%s\n' "$DOCTOR_OUT_M54" | grep -qE '^[[:space:]]+tmutil[[:space:]]+(ok|degraded|unavailable|error)'; then
    printf '%s\n' "$DOCTOR_OUT_M54" | grep -E '^[[:space:]]+tmutil[[:space:]]+'
    result "11.1 doctor: tmutil component" 1
else
    result "11.1 doctor: tmutil component" 0 "no tmutil line in doctor output"
fi

# Step 11.2 — TimeMachineSnapshot.list() end-to-end
step "11.2 TimeMachineSnapshot.list() end-to-end"
PY_TM_OUT="$(python3 -c "
from ascendo_macos.adapter import MacOSAdapter
a = MacOSAdapter()
host = a.detect_host()
snap = a.snapshot()
snapshots = snap.list(host)
print('time machine: %d local snapshots' % len(snapshots))
# No lower bound -- fresh-install Mac may have no local snapshots yet.
" 2>&1)"
PY_TM_RC=$?
if [ "$PY_TM_RC" -eq 0 ]; then
    printf '         %s\n' "$PY_TM_OUT"
    result "11.2 TimeMachineSnapshot.list() end-to-end" 1
else
    printf '%s\n' "$PY_TM_OUT" >&2
    result "11.2 TimeMachineSnapshot.list() end-to-end" 0 "see above"
fi

# ============================================================
# Stage 12 — launchd scheduler round-trip (M5.5)
# ============================================================
step "12. launchd scheduler round-trip (M5.5)"

# Stage 12 cleanup helper. Run on script exit AND once at start so a
# failed prior run doesn't leak agents.
SCHED_TEST_NAME="ascendo-validate-test"
SCHED_TEST_PLIST="$HOME/Library/LaunchAgents/dev.ascendo.${SCHED_TEST_NAME}.plist"
SCHED_TEST_SIDECAR="$HOME/Library/Application Support/Ascendo/schedules/${SCHED_TEST_NAME}.json"
_cleanup_sched_test() {
    /bin/launchctl bootout "gui/$(id -u)/dev.ascendo.${SCHED_TEST_NAME}" >/dev/null 2>&1 || true
    rm -f "$SCHED_TEST_PLIST" "$SCHED_TEST_SIDECAR" 2>/dev/null || true
}
_cleanup_sched_test
trap _cleanup_sched_test EXIT

# Capture doctor once for the launchctl grep.
DOCTOR_OUT_M55="$(python3 -m ascendo doctor 2>&1)"

# 12.1 doctor reports launchctl
step "12.1 doctor: launchctl component"
if printf '%s\n' "$DOCTOR_OUT_M55" | grep -qE '^[[:space:]]+launchctl[[:space:]]+(ok|degraded|unavailable|error)'; then
    printf '%s\n' "$DOCTOR_OUT_M55" | grep -E '^[[:space:]]+launchctl[[:space:]]+'
    result "12.1 doctor: launchctl component" 1
else
    result "12.1 doctor: launchctl component" 0 "no launchctl line in doctor output"
fi

# 12.2 schedule install via CLI
step "12.2 schedule install (MINUTE 1, profile=quick)"
if python3 -m ascendo schedule install \
        --name "$SCHED_TEST_NAME" \
        --calendar "MINUTE 1" \
        --profile "quick" \
        >/dev/null 2>&1; then
    if [ -f "$SCHED_TEST_PLIST" ] && [ -f "$SCHED_TEST_SIDECAR" ]; then
        result "12.2 schedule install (MINUTE 1, profile=quick)" 1 "plist + sidecar written"
    else
        result "12.2 schedule install (MINUTE 1, profile=quick)" 0 "files missing after install"
    fi
else
    result "12.2 schedule install (MINUTE 1, profile=quick)" 0 "CLI exit non-zero"
fi

# 12.3 schedule list contains the new entry
step "12.3 schedule list contains entry"
if python3 -m ascendo schedule list 2>/dev/null | grep -q "$SCHED_TEST_NAME"; then
    result "12.3 schedule list contains entry" 1
else
    result "12.3 schedule list contains entry" 0 "entry not visible in list"
fi

# 12.4 schedule trigger
step "12.4 schedule trigger"
if python3 -m ascendo schedule trigger --name "$SCHED_TEST_NAME" >/dev/null 2>&1; then
    result "12.4 schedule trigger" 1
else
    result "12.4 schedule trigger" 0 "trigger exit non-zero"
fi

# 12.5 schedule remove
step "12.5 schedule remove"
if python3 -m ascendo schedule remove --name "$SCHED_TEST_NAME" >/dev/null 2>&1; then
    if [ ! -f "$SCHED_TEST_PLIST" ] && [ ! -f "$SCHED_TEST_SIDECAR" ]; then
        result "12.5 schedule remove" 1 "files cleaned up"
    else
        result "12.5 schedule remove" 0 "files left on disk after remove"
    fi
else
    result "12.5 schedule remove" 0 "CLI exit non-zero"
fi

# ── Summary ───────────────────────────────────────────────────────────────────
printf "\n"
if [ "$FAIL_COUNT" -eq 0 ]; then
    printf "ALL CHECKS PASSED. (%d/%d)\n" "$PASS_COUNT" "$PASS_COUNT"
    exit 0
else
    printf "FAILED %d / %d checks.\n" "$FAIL_COUNT" "$((PASS_COUNT + FAIL_COUNT))" >&2
    exit 1
fi
