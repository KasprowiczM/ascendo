#!/usr/bin/env bats
# Tests for lib/json.sh — JSON sidecar emitter contract.
#
# Run with:
#   bats tests/bash/test_json_emit.bats

REPO="$(cd "$(dirname "${BATS_TEST_FILENAME}")/../.." && pwd)"

setup() {
    export TMPHOME
    TMPHOME="$(mktemp -d)"
    cd "$TMPHOME"
    # shellcheck source=/dev/null
    source "${REPO}/lib/json.sh"
}

teardown() {
    rm -rf "$TMPHOME"
}

@test "json_init creates buffer dir and meta" {
    json_init check apt
    [ -d "$JSON_BUFDIR" ]
    [ -f "$JSON_BUFDIR/meta.json" ]
    [ -f "$JSON_BUFDIR/items.jsonl" ]
    grep -q '"kind": "check"' "$JSON_BUFDIR/meta.json"
    grep -q '"category": "apt"' "$JSON_BUFDIR/meta.json"
}

@test "json_add_item appends well-formed JSON line" {
    json_init check apt
    json_add_item id=test action=upgrade result=ok from=1 to=2 duration_ms=42
    line="$(cat "$JSON_BUFDIR/items.jsonl")"
    [ -n "$line" ]
    echo "$line" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); assert d['id']=='test' and d['result']=='ok' and d['duration_ms']==42"
}

@test "json_add_diag rejects invalid level" {
    json_init check apt
    run python3 "${REPO}/lib/_json_emit.py" add-diag --bufdir "$JSON_BUFDIR" --level bogus --code TEST --msg hi
    [ "$status" -ne 0 ]
}

@test "json_finalize produces schema-valid sidecar" {
    json_init check apt
    json_count_ok
    json_count_warn 2
    json_add_item id=foo action=upgrade result=ok
    json_add_diag warn DRIFT-CODE "drift detected"
    json_set_needs_reboot 1
    out="$TMPHOME/out.json"
    json_finalize 0 "$out"
    [ -f "$out" ]
    python3 "${REPO}/tests/validate_phase_json.py" "$out"
}

@test "json_finalize is idempotent" {
    json_init check apt
    json_count_ok
    out="$TMPHOME/out.json"
    json_finalize 0 "$out"
    # second call must not error and must not recreate the buffer
    json_finalize 0 "$out"
    [ -f "$out" ]
}

@test "exit-trap finalizes when JSON_OUT set" {
    out="$TMPHOME/trap.json"
    JSON_OUT="$out" bash -c "
        source '${REPO}/lib/json.sh'
        json_init check apt
        json_register_exit_trap \"\$JSON_OUT\"
        json_count_ok
        json_add_item id=trap action=present result=ok
        exit 0
    "
    [ -f "$out" ]
    grep -q '"id": "trap"' "$out"
}

@test "schema rejects invalid kind" {
    cat > "$TMPHOME/bad.json" <<'EOF'
{
  "schema": "ascendo/v1",
  "kind": "BOGUS",
  "category": "apt",
  "host": "h",
  "started_at": "2026-04-29T00:00:00Z",
  "ended_at": "2026-04-29T00:00:01Z",
  "exit_code": 0,
  "summary": {"ok":0,"warn":0,"err":0},
  "items": [],
  "diagnostics": [],
  "needs_reboot": false
}
EOF
    run python3 "${REPO}/tests/validate_phase_json.py" "$TMPHOME/bad.json"
    [ "$status" -ne 0 ]
}
