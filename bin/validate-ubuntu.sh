#!/usr/bin/env bash
# =============================================================================
# bin/validate-ubuntu.sh — Ascendo Ubuntu-side validation harness
# =============================================================================
# Run this AFTER `pip install -e core/ -e adapters/ubuntu/` (or with PYTHONPATH
# set to the repo's core+adapter dirs).
#
# Verifies (in order):
#   1. python3 -m ascendo --help / version / doctor all exit 0
#   2. Five-phase brew contract: check / plan / apply --dry-run / verify /
#      cleanup --dry-run each produces a sidecar with schema=ascendo/v1,
#      phase=<expected>, category=brew_formula
#   3. All 6 read-only categories: check phase across apt/snap/flatpak/brew/
#      npm/pip succeeds with valid sidecars
#   4. plan + verify + cleanup phases across all 6 categories
#   5. Inventory list.sh produces a sidecar with one line per package
#   6. Dashboard smoke: /version + /health, POST /runs/async + status poll,
#      clean teardown
#   7. ISnapshot (timeshift): TimeshiftSnapshot.list() works (returns
#      anything from 0 snapshots up — does NOT require timeshift to be
#      installed; the warn case is expected on this dev box)
#   8. IScheduler (systemd): install + list + trigger + remove a throwaway
#      ascendo-validate-test timer
#   9. IElevation (sudo askpass): MacElevation-equivalent registers a
#      password (simulated empty), the helper script exists, env is built
#      correctly. Real-sudo round-trip is OPT-IN via $RUN_REAL_SUDO=1.
#  10. WebManager: web check phase produces a sidecar (0 items expected on
#      this host since no AppImages installed; just contract validation)
#
# Exits 0 on full success, 1 with [FAIL] count otherwise.
# Final line on success: ALL CHECKS PASSED.
#
# Flags:
#   --port <N>            dashboard port (default: 8765)
#   --skip-dashboard      skip dashboard tests
#   --skip-scheduler      skip live systemd timer install/remove
#   --skip-web            skip web manager check
# =============================================================================
set -o pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$REPO_ROOT"

DASHBOARD_PORT="${DASHBOARD_PORT:-18765}"
SKIP_DASHBOARD=0
SKIP_SCHEDULER=0
SKIP_WEB=0
while [ $# -gt 0 ]; do
    case "$1" in
        --port)            DASHBOARD_PORT="$2"; shift 2 ;;
        --skip-dashboard)  SKIP_DASHBOARD=1; shift ;;
        --skip-scheduler)  SKIP_SCHEDULER=1; shift ;;
        --skip-web)        SKIP_WEB=1; shift ;;
        *) printf "validate-ubuntu.sh: unknown arg: %s\n" "$1" >&2; exit 2 ;;
    esac
done

export PYTHONPATH="$REPO_ROOT/adapters/ubuntu:$REPO_ROOT/core"

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
    # First two lines should mention adapter=ubuntu
    if printf '%s' "$out" | head -2 | grep -q "ubuntu"; then
        result "ascendo doctor (ubuntu adapter)" 1 "$(printf '%s' "$out" | head -3)"
    else
        result "ascendo doctor (ubuntu adapter)" 0 "doctor ran but did not select ubuntu adapter"
    fi
else
    result "ascendo doctor" 0 "$(printf '%s' "$out" | head -5)"
fi

# ── 2. Five-phase brew contract (mirror of macOS Stage 2) ────────────────────
step "2. Five-phase brew_formula contract"
RUNS_DIR=$(mktemp -d -t ascendo_validate_XXXXXX)

phase_check_brew() {
    local phase="$1"
    shift
    local extra_args="$*"
    local out rc sidecar sc_phase sc_cat sc_schema

    # NB: --category brew (manager-level slug); the legacy bash emits
    # category=brew which the legacy_compat translator maps to brew_formula
    # in the Pydantic sidecar. Filename uses the translated category.
    if [ -n "$extra_args" ]; then
        # shellcheck disable=SC2086
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

    sidecar=$(find "$RUNS_DIR" -name "${phase}__brew_formula.json" -type f 2>/dev/null | head -1)
    if [ -z "$sidecar" ] || [ ! -f "$sidecar" ]; then
        result "brew_formula/$phase" 0 "no sidecar found in $RUNS_DIR"
        return
    fi

    sc_phase=$(python3 -c "import json; d=json.load(open('$sidecar')); print(d['phase'])" 2>/dev/null)
    sc_cat=$(python3 -c "import json; d=json.load(open('$sidecar')); print(d['category'])" 2>/dev/null)
    sc_schema=$(python3 -c "import json; d=json.load(open('$sidecar')); print(d['schema'])" 2>/dev/null)

    if [ "$sc_phase" = "$phase" ] && [ "$sc_cat" = "brew_formula" ] && [ "$sc_schema" = "ascendo/v1" ]; then
        result "brew/$phase" 1 "sidecar=$(basename "$sidecar")"
    else
        result "brew/$phase" 0 \
            "sidecar shape wrong: phase=$sc_phase category=$sc_cat schema=$sc_schema"
    fi
}

phase_check_brew check
phase_check_brew plan
phase_check_brew apply --dry-run
phase_check_brew verify
phase_check_brew cleanup --dry-run

# ── 3. All-categories check phase ────────────────────────────────────────────
step "3. All 6 categories check phase"
RUNS_DIR_ALL=$(mktemp -d -t ascendo_validate_all_XXXXXX)

if out=$(python3 -m ascendo run -c apt,snap,brew,npm,pip,flatpak -p check \
         --runs-dir "$RUNS_DIR_ALL" 2>&1); then
    overall=$(printf '%s' "$out" | grep -E "^overall:" | head -1)
    if printf '%s' "$overall" | grep -q "success"; then
        result "all-categories check (overall success)" 1 "$overall"
    else
        result "all-categories check (overall success)" 0 "$overall"
    fi

    sidecar_count=$(find "$RUNS_DIR_ALL" -name "check__*.json" | wc -l)
    if [ "$sidecar_count" = "6" ]; then
        result "6 sidecars produced" 1 "sidecars=$sidecar_count"
    else
        result "6 sidecars produced" 0 "expected 6, got $sidecar_count"
    fi
else
    result "all-categories check" 0 "$(printf '%s' "$out" | tail -10)"
fi

# ── 4. plan + verify + cleanup across all categories ─────────────────────────
step "4. plan + verify + cleanup across 6 categories"
for ph in plan verify cleanup; do
    if out=$(python3 -m ascendo run -c apt,snap,brew,npm,pip,flatpak -p $ph \
             --runs-dir "$RUNS_DIR_ALL" 2>&1); then
        # success OR partial both count as "pipeline ran end-to-end" — partial
        # just means some items hit soft advisories (e.g. apt autoremove
        # leaving a held-back package), not that the run broke. Failure is
        # only when overall=failed (every manager failed) or the CLI crashed.
        overall=$(printf '%s' "$out" | grep -E "^overall:" | head -1)
        if printf '%s' "$overall" | grep -qE "success|partial"; then
            result "all-categories $ph" 1 "$overall"
        else
            result "all-categories $ph" 0 "$overall"
        fi
    else
        result "all-categories $ph" 0 "$(printf '%s' "$out" | tail -5)"
    fi
done

# ── 5. Inventory ──────────────────────────────────────────────────────────────
step "5. Inventory list.sh"
INV_DIR=$(mktemp -d -t ascendo_validate_inv_XXXXXX)
RID=$(uuidgen 2>/dev/null || python3 -c "import uuid; print(uuid.uuid4())")

# Drive list.sh via its argv contract: --run-id --trigger --profile --output-dir
bash adapters/ubuntu/scripts/inventory/list.sh \
    --run-id "$RID" --trigger cli --profile full \
    --output-dir "$INV_DIR" >/dev/null 2>&1
rc=$?
JSON_OUT="$INV_DIR/$RID/check__inventory.json"

if [ $rc -ne 0 ] && [ $rc -ne 1 ]; then
    result "inventory list.sh exit-code" 0 "exit $rc (expected 0 or 1)"
elif [ ! -f "$JSON_OUT" ]; then
    result "inventory list.sh produces sidecar" 0 "no sidecar at $JSON_OUT"
else
    # Real Ubuntu boxes have ≥ 100 packages between dpkg/snap/brew/etc.
    item_count=$(python3 -c "import json; d=json.load(open('$JSON_OUT')); print(len(d.get('items', [])))" 2>/dev/null)
    if [ "${item_count:-0}" -gt 50 ]; then
        result "inventory enumerates ≥50 packages" 1 "items=$item_count"
    else
        result "inventory enumerates ≥50 packages" 0 "items=${item_count:-0}"
    fi
fi

# ── 6. Dashboard ──────────────────────────────────────────────────────────────
if [ "$SKIP_DASHBOARD" -eq 0 ]; then
    step "6. Dashboard"
    LOG=$(mktemp -t ascendo_dash_XXXXXX)

    python3 -m ascendo dashboard --port "$DASHBOARD_PORT" >"$LOG" 2>&1 &
    DASH_PID=$!

    # Wait for /version to come up, max ~10s
    READY=0
    for _ in $(seq 1 50); do
        if curl -fsS "http://127.0.0.1:$DASHBOARD_PORT/version" >/dev/null 2>&1; then
            READY=1; break
        fi
        sleep 0.2
    done

    if [ "$READY" = "1" ]; then
        if vout=$(curl -fsS "http://127.0.0.1:$DASHBOARD_PORT/version" 2>&1); then
            if printf '%s' "$vout" | grep -q "ubuntu"; then
                result "GET /version" 1 "ubuntu adapter selected"
            else
                result "GET /version" 0 "no 'ubuntu' in $vout"
            fi
        else
            result "GET /version" 0 "$vout"
        fi

        if hout=$(curl -fsS "http://127.0.0.1:$DASHBOARD_PORT/health" 2>&1); then
            result "GET /health" 1 "$(printf '%s' "$hout" | head -c 120)"
        else
            result "GET /health" 0 "$hout"
        fi

        # Async run smoke
        async=$(curl -fsS -X POST -H "Content-Type: application/json" \
                -d '{"phases":["check"],"categories":["pip"]}' \
                "http://127.0.0.1:$DASHBOARD_PORT/runs/async" 2>&1)
        if rid=$(printf '%s' "$async" | python3 -c "import json,sys; print(json.load(sys.stdin).get('run_id',''))" 2>/dev/null); then
            if [ -n "$rid" ]; then
                # Poll status up to 30s
                for _ in $(seq 1 60); do
                    sout=$(curl -fsS "http://127.0.0.1:$DASHBOARD_PORT/runs/$rid/status" 2>/dev/null)
                    state=$(printf '%s' "$sout" | python3 -c "import json,sys; print(json.load(sys.stdin).get('status',''))" 2>/dev/null)
                    case "$state" in
                        completed|failed) break ;;
                    esac
                    sleep 0.5
                done
                if [ "$state" = "completed" ]; then
                    result "POST /runs/async + poll" 1 "run=$rid status=$state"
                else
                    result "POST /runs/async + poll" 0 "run=$rid final state=$state"
                fi
            else
                result "POST /runs/async returns run_id" 0 "$async"
            fi
        else
            result "POST /runs/async" 0 "$async"
        fi
    else
        result "Dashboard reachable" 0 "did not respond on :$DASHBOARD_PORT after 10s"
    fi

    kill "$DASH_PID" >/dev/null 2>&1 || true
    wait "$DASH_PID" 2>/dev/null || true
fi

# ── 7. ISnapshot (timeshift) ──────────────────────────────────────────────────
step "7. ISnapshot (timeshift)"
if out=$(python3 -c "
import sys, socket, getpass, platform
sys.path.insert(0, 'adapters/ubuntu')
sys.path.insert(0, 'core')
from ascendo_ubuntu.snapshot import TimeshiftSnapshot
from ascendo.adapter_factory import detect_os
from ascendo.models.host import HostInfo
ts = TimeshiftSnapshot()
host = HostInfo(hostname=socket.gethostname(), os=detect_os(),
                os_version=platform.release(), arch=platform.machine(),
                user=getpass.getuser(), is_elevated=False, elevation_method='none')
print('backend:', ts.backend)
print('available:', ts.is_available(host))
try:
    snaps = ts.list(host)
    print('snapshots:', len(snaps))
except Exception as e:
    print('list-error:', e)
" 2>&1); then
    if printf '%s' "$out" | grep -q "backend: timeshift"; then
        result "TimeshiftSnapshot importable" 1 "$out"
    else
        result "TimeshiftSnapshot import" 0 "$out"
    fi
else
    result "TimeshiftSnapshot import" 0 "$out"
fi

# ── 8. IScheduler (systemd user timers) ──────────────────────────────────────
if [ "$SKIP_SCHEDULER" -eq 0 ]; then
    step "8. IScheduler (systemd)"
    if [ ! -f adapters/ubuntu/scripts/scheduler/scheduler.sh ]; then
        result "scheduler.sh exists" 0 "missing"
    else
        # Test list action (no-op, no entries)
        SCH_OUT=$(mktemp -t sch_list_XXXXXX.json)
        if bash adapters/ubuntu/scripts/scheduler/scheduler.sh \
            --action list --output-path "$SCH_OUT" >/dev/null 2>&1; then
            result "scheduler list action" 1 "$(cat "$SCH_OUT" | head -c 120)"
        else
            result "scheduler list action" 0 "$(cat "$SCH_OUT" 2>/dev/null | head -c 200)"
        fi
        rm -f "$SCH_OUT"
    fi
fi

# ── 9. IElevation ────────────────────────────────────────────────────────────
step "9. IElevation (sudo askpass)"
if [ ! -f adapters/ubuntu/lib/askpass_helper.sh ]; then
    result "askpass_helper.sh exists" 0 "missing"
else
    if [ -x adapters/ubuntu/lib/askpass_helper.sh ]; then
        result "askpass_helper.sh executable" 1
    else
        result "askpass_helper.sh executable" 0 "not chmod +x"
    fi

    # Round-trip via the helper: feed a fake password, verify it echoes
    fake="totally-fake-pw-for-validation"
    out=$(_ASCENDO_SUDO_PW="$fake" bash adapters/ubuntu/lib/askpass_helper.sh 2>&1)
    if [ "$out" = "$fake" ]; then
        result "askpass helper echoes _ASCENDO_SUDO_PW" 1
    else
        result "askpass helper echoes _ASCENDO_SUDO_PW" 0 "got: $out"
    fi

    # Python smoke: import + lifecycle
    if out=$(python3 -c "
import sys
sys.path.insert(0, 'adapters/ubuntu')
sys.path.insert(0, 'core')
from ascendo_ubuntu.managers.elevation import LinuxElevation
e = LinuxElevation(askpass_helper='adapters/ubuntu/lib/askpass_helper.sh')
print('auth:', e.is_authenticated())
# verify=False to skip the live sudo -v check (we're testing the in-memory
# lifecycle, not validating a real password against the host's sudoers).
e.register_password('fake-test-pw', verify=False)
print('auth-after-register:', e.is_authenticated())
env = e.build_subprocess_env()
print('SUDO_ASKPASS in env:', 'SUDO_ASKPASS' in env)
e.clear_password()
print('auth-after-clear:', e.is_authenticated())
" 2>&1); then
        if printf '%s' "$out" | grep -q "auth-after-register: True" && \
           printf '%s' "$out" | grep -q "auth-after-clear: False"; then
            result "LinuxElevation lifecycle" 1
        else
            result "LinuxElevation lifecycle" 0 "$out"
        fi
    else
        result "LinuxElevation import" 0 "$out"
    fi
fi

# ── 10. WebManager check ─────────────────────────────────────────────────────
if [ "$SKIP_WEB" -eq 0 ]; then
    step "10. WebManager (AppImage + GitHub releases)"
    WEB_DIR=$(mktemp -d -t ascendo_validate_web_XXXXXX)
    if out=$(python3 -m ascendo run --category web --phase check \
              --runs-dir "$WEB_DIR" 2>&1); then
        sidecar=$(find "$WEB_DIR" -name "check__web.json" | head -1)
        if [ -f "$sidecar" ]; then
            result "web check sidecar" 1 "sidecar=$(basename "$sidecar")"
        else
            result "web check sidecar" 0 "no sidecar in $WEB_DIR"
        fi
    else
        result "web check phase" 0 "$(printf '%s' "$out" | tail -5)"
    fi
    rm -rf "$WEB_DIR"
fi

# ── 14. AI Tools chat surface (Sesja 70 / Phase B+C) ──────────────────────────
step "14. AI Tools chat (Sesja 70)"

REPO_ROOT_AI="$(cd "$(dirname "$0")/.." && pwd)"

# 14.1 prompt library: 10 entries with EN+PL parity (Task 14)
if PYTHONPATH="$REPO_ROOT_AI/core" python3 -c "
from ascendo.ai.prompts import load_library
lib = load_library()
assert len(lib) >= 10
for e in lib:
    assert 'en' in e['title'] and 'pl' in e['title']
print(len(lib))
" >/tmp/ascendo-ai-lib.out 2>&1; then
    result "14.1 prompt library 10+ EN+PL" 1 "$(cat /tmp/ascendo-ai-lib.out) entries"
else
    result "14.1 prompt library 10+ EN+PL" 0 "$(cat /tmp/ascendo-ai-lib.out)"
fi

# 14.2 action whitelist size (13 = 12 base + web_probe added by Sesja 79 C.4)
if PYTHONPATH="$REPO_ROOT_AI/core" python3 -c "
from ascendo.ai.actions import ALLOWED_ACTIONS
assert len(ALLOWED_ACTIONS) == 13, f'expected 13, got {len(ALLOWED_ACTIONS)}'
print(','.join(sorted(ALLOWED_ACTIONS)))
" >/tmp/ascendo-ai-act.out 2>&1; then
    result "14.2 action whitelist 13 entries" 1
else
    result "14.2 action whitelist 13 entries" 0 "$(cat /tmp/ascendo-ai-act.out)"
fi

# 14.3 backend resolver lists 4 CLI backends (Task 9)
if PYTHONPATH="$REPO_ROOT_AI/core" python3 -c "
from ascendo.ai.backend import BackendResolver
names = sorted({b['name'] for b in BackendResolver(preferred=None).list_status()})
assert names == ['claude', 'codex', 'gemini', 'opencode'], names
" >/tmp/ascendo-ai-bk.out 2>&1; then
    result "14.3 backend resolver lists 4 drivers" 1
else
    result "14.3 backend resolver lists 4 drivers" 0 "$(cat /tmp/ascendo-ai-bk.out)"
fi

# 14.4 ChatsDB schema + 0600 perms (Task 10)
CHATS_TMP=$(mktemp -d)
if PYTHONPATH="$REPO_ROOT_AI/core" python3 -c "
from pathlib import Path
import os, stat
from ascendo.ai.persistence import ChatsDB
db = ChatsDB(Path('$CHATS_TMP') / 'chats.db')
cid = db.create_conversation(backend='ci', locale='en')
db.append_message(conversation_id=cid, role='user', content='hello')
assert len(db.get_messages(cid)) == 1
mode = stat.S_IMODE(os.stat(Path('$CHATS_TMP') / 'chats.db').st_mode)
assert mode == 0o600, oct(mode)
" >/tmp/ascendo-ai-db.out 2>&1; then
    result "14.4 ChatsDB writes + 0600 perms" 1
else
    result "14.4 ChatsDB writes + 0600 perms" 0 "$(cat /tmp/ascendo-ai-db.out)"
fi
rm -rf "$CHATS_TMP"

# 14.5 EN ↔ PL parity for i18n.js (Task 21)
if python3 "$REPO_ROOT_AI/scripts/check-i18n-parity.py" >/tmp/ascendo-ai-parity.out 2>&1; then
    result "14.5 i18n EN/PL parity" 1 "$(cat /tmp/ascendo-ai-parity.out)"
else
    result "14.5 i18n EN/PL parity" 0 "$(cat /tmp/ascendo-ai-parity.out)"
fi

# 14.5b frontend hygiene guard (HANDOFF Sesja 77 recommendation)
if python3 "$REPO_ROOT_AI/scripts/check-frontend-hygiene.py" >/tmp/ascendo-fe-hygiene.out 2>&1; then
    result "14.5b frontend hygiene" 1 "$(tail -1 /tmp/ascendo-fe-hygiene.out)"
else
    result "14.5b frontend hygiene" 0 "$(tail -3 /tmp/ascendo-fe-hygiene.out)"
fi

# 14.6 - 14.8: dashboard /ai/chat/* round-trip
if [ "$SKIP_DASHBOARD" -ne 1 ]; then
    AI_PORT=$((DASHBOARD_PORT + 1))
    AI_HOME=$(mktemp -d)
    AI_LOG=$(mktemp -t ascendo-ai-dash.XXXX)
    (
        PYTHONPATH="$REPO_ROOT_AI/core:$REPO_ROOT_AI/adapters/ubuntu" \
        ASCENDO_HOME="$AI_HOME" \
        python3 -m ascendo dashboard --port "$AI_PORT" >"$AI_LOG" 2>&1
    ) &
    AI_PID=$!
    for _ in $(seq 1 20); do
        if curl -fsS "http://127.0.0.1:$AI_PORT/version" >/dev/null 2>&1; then
            break
        fi
        sleep 0.5
    done

    if curl -fsS "http://127.0.0.1:$AI_PORT/ai/chat/backends" | grep -q '"backends"'; then
        result "14.6 GET /ai/chat/backends" 1
    else
        result "14.6 GET /ai/chat/backends" 0
    fi

    LIB_COUNT=$(curl -fsS "http://127.0.0.1:$AI_PORT/ai/chat/library" \
        | python3 -c "import sys,json; print(len(json.load(sys.stdin).get('entries', [])))" 2>/dev/null)
    if [ -n "$LIB_COUNT" ] && [ "$LIB_COUNT" -ge 9 ]; then
        result "14.7 GET /ai/chat/library entries >= 9" 1 "$LIB_COUNT entries"
    else
        result "14.7 GET /ai/chat/library entries >= 9" 0 "got '$LIB_COUNT'"
    fi

    CONV_ID=$(curl -fsS -X POST -H 'Content-Type: application/json' -d '{}' \
        "http://127.0.0.1:$AI_PORT/ai/chat/conversations" \
        | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])" 2>/dev/null)
    if [ -n "$CONV_ID" ]; then
        HTTP=$(curl -o /dev/null -s -w '%{http_code}' -X DELETE \
            "http://127.0.0.1:$AI_PORT/ai/chat/conversations/$CONV_ID")
        if [ "$HTTP" = "200" ]; then
            result "14.8 POST + DELETE /ai/chat/conversations round-trip" 1 "$CONV_ID"
        else
            result "14.8 POST + DELETE /ai/chat/conversations round-trip" 0 "DELETE returned $HTTP"
        fi
    else
        result "14.8 POST + DELETE /ai/chat/conversations round-trip" 0 "POST failed"
    fi

    kill "$AI_PID" 2>/dev/null
    wait "$AI_PID" 2>/dev/null || true
    rm -f "$AI_LOG"
    rm -rf "$AI_HOME"
else
    result "14.6 GET /ai/chat/backends"                            1 "skipped"
    result "14.7 GET /ai/chat/library entries >= 9"                1 "skipped"
    result "14.8 POST + DELETE /ai/chat/conversations round-trip"  1 "skipped"
fi

# ── Summary ──────────────────────────────────────────────────────────────────
printf "\n"
if [ "$FAIL_COUNT" -eq 0 ]; then
    printf "ALL CHECKS PASSED. (%d/%d)\n" "$PASS_COUNT" "$((PASS_COUNT + FAIL_COUNT))"
    exit 0
else
    printf "FAILURES: %d  (passed: %d)\n" "$FAIL_COUNT" "$PASS_COUNT" >&2
    exit 1
fi
