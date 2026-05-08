#!/usr/bin/env bash
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
PYTHONPATH="$REPO_ROOT/core:$REPO_ROOT/adapters/macos"
export PYTHONPATH

CLI="$REPO_ROOT/adapters/macos/lib/web_registry.py"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
make_fixture() {
    local tmp
    tmp=$(mktemp)
    cat > "$tmp" <<'EOF'
schema = "ascendo-web-apps/v2"

[[apps]]
slug = "brave"
bundle_id = "com.brave.Browser"
display_name = "Brave Browser"
handler = "sparkle"
appcast_url = "https://updates.bravesoftware.com/sparkle/Browser/stable/appcast.xml"
EOF
    echo "$tmp"
}

# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
test_list_bundle_ids() {
    local tmp_reg
    tmp_reg=$(make_fixture)

    local out
    out=$(python3 "$CLI" --shipped "$tmp_reg" --list-bundle-ids 2>&1)
    local rc=$?
    rm -f "$tmp_reg"

    [ $rc -eq 0 ] || { echo "FAIL test_list_bundle_ids: rc=$rc, out=$out"; return 1; }
    [ "$out" = "com.brave.Browser" ] || { echo "FAIL test_list_bundle_ids: out='$out'"; return 1; }
    echo "PASS test_list_bundle_ids"
}

test_get_app_by_bundle_id() {
    local tmp_reg
    tmp_reg=$(make_fixture)

    local out
    out=$(python3 "$CLI" --shipped "$tmp_reg" --get-app-by-bundle-id com.brave.Browser 2>&1)
    local rc=$?
    rm -f "$tmp_reg"

    [ $rc -eq 0 ] || { echo "FAIL test_get_app_by_bundle_id: rc=$rc, out=$out"; return 1; }
    echo "$out" | python3 -c 'import json,sys; d=json.loads(sys.stdin.read()); assert d["slug"]=="brave"' \
        || { echo "FAIL test_get_app_by_bundle_id: malformed json output: $out"; return 1; }
    echo "PASS test_get_app_by_bundle_id"
}

test_get_app_by_bundle_id_not_found() {
    local tmp_reg
    tmp_reg=$(make_fixture)

    local out
    out=$(python3 "$CLI" --shipped "$tmp_reg" --get-app-by-bundle-id com.nobody.App 2>&1)
    local rc=$?
    rm -f "$tmp_reg"

    [ $rc -eq 2 ] || { echo "FAIL test_get_app_by_bundle_id_not_found: expected rc=2, got rc=$rc"; return 1; }
    echo "PASS test_get_app_by_bundle_id_not_found"
}

test_mutually_exclusive_rejects_two_flags() {
    local tmp_reg
    tmp_reg=$(make_fixture)

    python3 "$CLI" --shipped "$tmp_reg" --list-bundle-ids --list-slugs 2>/dev/null
    local rc=$?
    rm -f "$tmp_reg"

    [ $rc -ne 0 ] || { echo "FAIL test_mutually_exclusive_rejects_two_flags: expected non-zero rc"; return 1; }
    echo "PASS test_mutually_exclusive_rejects_two_flags"
}

# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------
failures=0
test_list_bundle_ids        || failures=$((failures+1))
test_get_app_by_bundle_id   || failures=$((failures+1))
test_get_app_by_bundle_id_not_found || failures=$((failures+1))
test_mutually_exclusive_rejects_two_flags || failures=$((failures+1))

if [ $failures -eq 0 ]; then
    echo "ALL TESTS PASSED"
    exit 0
else
    echo "$failures test(s) FAILED"
    exit 1
fi
