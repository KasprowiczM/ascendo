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

# M5.7.4: version_regex/version_replace transform raw version string.
# Fixture warp.json publishes 'v0.2026.05.06.15.42.stable_02'; the regex
# strips 'v' prefix and '.stable_' infix to give '0.2026.05.06.15.42.02'.
test_version_regex_transforms_raw_version() {
    local cfg
    cfg=$(python3 -c "
import json
d = {
  'slug': 'warp',
  'bundle_id': 'dev.warp.Warp-Stable',
  'display_name': 'Warp',
  'handler': 'release_feed',
  'release_feed': {
    'url': 'http://127.0.0.1:$PORT/warp.json',
    'version_path': 'stable.version',
    'version_regex': r'^v(.+)\\.stable_(.+)\$',
    'version_replace': r'\\1.\\2',
    'http_timeout_s': 5,
  },
}
print(json.dumps(d))
")
    local v
    v=$(release_feed_check "warp" "$cfg")
    assert_eq "test_version_regex_transforms_raw_version" \
        "0.2026.05.06.15.42.02" "$v"
}

# M5.7.4: when the regex doesn't match, fall back to the raw value
# rather than failing the probe — vendor format change degrades to
# raw detection rather than silently breaking.
test_version_regex_no_match_returns_probe_broken() {
    # W2 (audit): a configured version_regex that does NOT match must
    # fail-loud (probe_broken, rc=28, empty output) — NOT silently degrade
    # to the raw version. This used to assert the raw fallback; that was the
    # exact dishonest behaviour the W2 fix removed.
    local cfg
    cfg=$(python3 -c "
import json
d = {
  'slug': 'warp',
  'bundle_id': 'dev.warp.Warp-Stable',
  'display_name': 'Warp',
  'handler': 'release_feed',
  'release_feed': {
    'url': 'http://127.0.0.1:$PORT/warp.json',
    'version_path': 'stable.version',
    'version_regex': r'^X(.+)Y\$',
    'version_replace': r'\\1',
    'http_timeout_s': 5,
  },
}
print(json.dumps(d))
")
    local v rc
    v=$(release_feed_check "warp" "$cfg") ; rc=$?
    if [ -z "$v" ] && [ "$rc" -eq 28 ]; then
        echo "PASS test_version_regex_no_match_returns_probe_broken"
        PASS=$((PASS+1))
    else
        echo "FAIL test_version_regex_no_match_returns_probe_broken: v='$v' rc=$rc (want empty + rc=28)"
        FAIL=$((FAIL+1))
    fi
}

# M5.7.5: download_asset_pattern selects from GitHub Releases assets[]
# by name regex. Used by Brave (universal DMG buried in ~30 platform
# assets including arm64-only binaries that won't run if installed by
# mistake on x86_64 hardware).
test_pick_asset_url_matches_first_qualifying_asset() {
    local body url
    body=$(/usr/bin/curl -fsS --max-time 5 "http://127.0.0.1:$PORT/github_release.json")
    url=$(_rf_pick_asset_url "$body" '^Brave-Browser-universal\.dmg$')
    assert_eq "test_pick_asset_url_matches_first_qualifying_asset" \
        "https://example.test/brave/Brave-Browser-universal.dmg" "$url"
}

test_pick_asset_url_no_match_returns_empty_nonzero() {
    local body url rc
    body=$(/usr/bin/curl -fsS --max-time 5 "http://127.0.0.1:$PORT/github_release.json")
    url=$(_rf_pick_asset_url "$body" '^Definitely-Not-Real\.dmg$') ; rc=$?
    if [ -z "$url" ] && [ "$rc" -ne 0 ]; then
        echo "PASS test_pick_asset_url_no_match_returns_empty_nonzero"
        PASS=$((PASS+1))
        return
    fi
    echo "FAIL test_pick_asset_url_no_match: url='$url' rc=$rc"
    FAIL=$((FAIL+1))
}

test_pick_asset_url_malformed_json_returns_empty_nonzero() {
    local url rc
    url=$(_rf_pick_asset_url 'this is not json' '.*\.dmg$') ; rc=$?
    if [ -z "$url" ] && [ "$rc" -ne 0 ]; then
        echo "PASS test_pick_asset_url_malformed_json_returns_empty_nonzero"
        PASS=$((PASS+1))
        return
    fi
    echo "FAIL test_pick_asset_url_malformed_json: url='$url' rc=$rc"
    FAIL=$((FAIL+1))
}

test_yml_files_dmg_picks_dmg_not_zip() {
    local yml url rc
    yml='version: 4.17.1
files:
  - url: ledger-live-desktop-4.17.1-mac.zip
    sha512: AAA
  - url: ledger-live-desktop-4.17.1-mac.blockmap
    sha512: BBB
  - url: ledger-live-desktop-4.17.1-mac.dmg
    sha512: CCC
'
    url=$(_rf_walk_json "$yml" "files[dmg].url") ; rc=$?
    assert_eq "test_yml_files_dmg_picks_dmg_not_zip" "ledger-live-desktop-4.17.1-mac.dmg" "$url"
    [ "$rc" -eq 0 ] || { echo "FAIL test_yml_files_dmg_picks_dmg_not_zip rc=$rc"; FAIL=$((FAIL+1)); }
}

test_yml_files_dmg_sha512_pairs_with_dmg() {
    local yml hash rc
    yml='version: 4.17.1
files:
  - url: ledger-live-desktop-4.17.1-mac.zip
    sha512: AAA
  - url: ledger-live-desktop-4.17.1-mac.blockmap
    sha512: BBB
  - url: ledger-live-desktop-4.17.1-mac.dmg
    sha512: CCC
'
    hash=$(_rf_walk_json "$yml" "files[dmg].sha512") ; rc=$?
    assert_eq "test_yml_files_dmg_sha512_pairs_with_dmg" "CCC" "$hash"
    [ "$rc" -eq 0 ] || { echo "FAIL test_yml_files_dmg_sha512_pairs_with_dmg rc=$rc"; FAIL=$((FAIL+1)); }
}

test_happy_path_emits_version
test_404_is_skipped
test_malformed_json_is_skipped
test_missing_path_is_skipped
test_arch_mismatch_is_skipped
test_arch_match_succeeds
test_version_regex_transforms_raw_version
test_version_regex_no_match_returns_probe_broken
test_pick_asset_url_matches_first_qualifying_asset
test_pick_asset_url_no_match_returns_empty_nonzero
test_pick_asset_url_malformed_json_returns_empty_nonzero
test_yml_files_dmg_picks_dmg_not_zip
test_yml_files_dmg_sha512_pairs_with_dmg

echo
echo "$PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
