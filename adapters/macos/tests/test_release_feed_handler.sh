#!/usr/bin/env bash
# Tests for adapters/macos/lib/handlers/release_feed.sh
# Spins up a fixture HTTP server on 127.0.0.1:$PORT
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ADAPTER_LIB="$SCRIPT_DIR/../lib"
FIXTURES="$SCRIPT_DIR/fixtures/release_feed"
PORT="${ASCENDO_TEST_HTTP_PORT:-8782}"

PASS=0; FAIL=0

assert_eq() {
    local name="$1" expected="$2" actual="$3"
    if [ "$expected" = "$actual" ]; then
        echo "PASS $name"; PASS=$((PASS+1))
    else
        echo "FAIL $name: expected='$expected', got='$actual'"; FAIL=$((FAIL+1))
    fi
}

# Spawn a one-shot Python http.server bound to FIXTURES dir
python3 -c "
import http.server, socketserver, os, sys
os.chdir('$FIXTURES')
socketserver.TCPServer.allow_reuse_address = True
httpd = socketserver.TCPServer(('127.0.0.1', $PORT), http.server.SimpleHTTPRequestHandler)
httpd.serve_forever()
" >/tmp/ascendo-test-http.log 2>&1 &
HTTP_PID=$!
trap 'kill "$HTTP_PID" 2>/dev/null || true' EXIT

# Wait for server up (up to 3 seconds)
for _ in 1 2 3 4 5 6; do
    if curl -fsS --max-time 1 "http://127.0.0.1:$PORT/feed.json" >/dev/null 2>&1; then
        break
    fi
    sleep 0.5
done

# shellcheck source=../lib/handlers/release_feed.sh
. "$ADAPTER_LIB/handlers/release_feed.sh"

CFG_HAPPY=$(cat <<EOF
{
  "slug": "warp",
  "bundle_id": "dev.warp.Warp-Stable",
  "display_name": "Warp",
  "handler": "release_feed",
  "release_feed": {
    "url": "http://127.0.0.1:$PORT/feed.json",
    "version_path": "latest.darwin.arm64.version",
    "download_path": "latest.darwin.arm64.url",
    "http_timeout_s": 5
  }
}
EOF
)

test_happy_path_emits_version() {
    local v
    v=$(release_feed_check "warp" "$CFG_HAPPY")
    assert_eq "test_happy_path_emits_version" "0.2026.05.08.00.00.01" "$v"
}

test_404_is_skipped() {
    local cfg
    cfg=$(printf '%s' "$CFG_HAPPY" | python3 -c '
import json, sys
d = json.loads(sys.stdin.read())
d["release_feed"]["url"] = d["release_feed"]["url"].replace("/feed.json", "/missing.json")
print(json.dumps(d))
')
    local out rc
    out=$(release_feed_check "warp" "$cfg" 2>&1) ; rc=$?
    [ "$out" = "" ] || { echo "FAIL test_404_is_skipped: out='$out'"; FAIL=$((FAIL+1)); return; }
    [ "$rc" -ne 0 ] || { echo "FAIL test_404_is_skipped: expected non-zero rc"; FAIL=$((FAIL+1)); return; }
    echo "PASS test_404_is_skipped"; PASS=$((PASS+1))
}

test_malformed_json_is_skipped() {
    local cfg
    cfg=$(printf '%s' "$CFG_HAPPY" | python3 -c '
import json, sys
d = json.loads(sys.stdin.read())
d["release_feed"]["url"] = d["release_feed"]["url"].replace("/feed.json", "/malformed.txt")
print(json.dumps(d))
')
    local out rc
    out=$(release_feed_check "warp" "$cfg" 2>&1) ; rc=$?
    if [ "$out" = "" ] && [ "$rc" -ne 0 ]; then
        echo "PASS test_malformed_json_is_skipped"; PASS=$((PASS+1)); return
    fi
    echo "FAIL test_malformed_json_is_skipped: out='$out' rc=$rc"; FAIL=$((FAIL+1))
}

test_missing_path_is_skipped() {
    local cfg
    cfg=$(printf '%s' "$CFG_HAPPY" | python3 -c '
import json, sys
d = json.loads(sys.stdin.read())
d["release_feed"]["version_path"] = "no.such.path"
print(json.dumps(d))
')
    local out rc
    out=$(release_feed_check "warp" "$cfg" 2>&1) ; rc=$?
    if [ "$out" = "" ] && [ "$rc" -ne 0 ]; then
        echo "PASS test_missing_path_is_skipped"; PASS=$((PASS+1)); return
    fi
    echo "FAIL test_missing_path_is_skipped: out='$out' rc=$rc"; FAIL=$((FAIL+1))
}

test_arch_mismatch_is_skipped() {
    local cfg
    cfg=$(printf '%s' "$CFG_HAPPY" | python3 -c '
import json, sys
d = json.loads(sys.stdin.read())
d["release_feed"]["arch_path"] = "latest.darwin.arm64.arch"
d["release_feed"]["expected_arch"] = "x86_64"
print(json.dumps(d))
')
    local out rc
    out=$(release_feed_check "warp" "$cfg" 2>&1) ; rc=$?
    if [ "$out" = "" ] && [ "$rc" -ne 0 ]; then
        echo "PASS test_arch_mismatch_is_skipped"; PASS=$((PASS+1)); return
    fi
    echo "FAIL test_arch_mismatch_is_skipped: out='$out' rc=$rc"; FAIL=$((FAIL+1))
}

test_arch_match_succeeds() {
    local cfg
    cfg=$(printf '%s' "$CFG_HAPPY" | python3 -c '
import json, sys
d = json.loads(sys.stdin.read())
d["release_feed"]["arch_path"] = "latest.darwin.arm64.arch"
d["release_feed"]["expected_arch"] = "arm64"
print(json.dumps(d))
')
    local v
    v=$(release_feed_check "warp" "$cfg")
    assert_eq "test_arch_match_succeeds" "0.2026.05.08.00.00.01" "$v"
}

test_happy_path_emits_version
test_404_is_skipped
test_malformed_json_is_skipped
test_missing_path_is_skipped
test_arch_mismatch_is_skipped
test_arch_match_succeeds

echo
echo "$PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
