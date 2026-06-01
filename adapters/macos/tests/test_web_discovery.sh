#!/usr/bin/env bash
# Tests for adapters/macos/lib/web_discovery.sh
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ADAPTER_LIB="$SCRIPT_DIR/../lib"
FIXTURES="$SCRIPT_DIR/fixtures/discovery/Applications"

PASS=0; FAIL=0

assert_eq() {
    local name="$1" expected="$2" actual="$3"
    if [ "$expected" = "$actual" ]; then
        echo "PASS $name"
        PASS=$((PASS + 1))
    else
        echo "FAIL $name: expected=$expected, got=$actual"
        FAIL=$((FAIL + 1))
    fi
}

run_disc() {
    # Filter out the W10 DISCOVERY_OK/FAILED sentinel lines so the app-line
    # counts below stay about the JSON records only.
    ASCENDO_WEB_APPS_ROOT="$FIXTURES" \
    ASCENDO_WEB_BREW_CASKS="${1:-}" \
    ASCENDO_WEB_MAS_BUNDLE_IDS="${2:-}" \
    ASCENDO_WEB_APPLE_BUNDLES="${3:-}" \
    ASCENDO_WEB_INCLUDE_OWNED="${4:-0}" \
    bash "$ADAPTER_LIB/web_discovery.sh" --emit-json | grep -v '^DISCOVERY_'
}

extract_field() {
    # Args: bundle_id, field
    python3 -c "
import json, sys
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    d = json.loads(line)
    if d.get('bundle_id') == '$1':
        v = d.get('$2', '')
        print(v if v is not None else '')
        break
"
}

test_emits_one_line_per_app() {
    # 4 original orphans + FakeBrewByApp + FakeOrphan = 6
    # FakeMasReceipt and FakeApple/FakeShortcut auto-classify as
    # mas/softwareupdate/ineligible and are filtered. Apple is filtered
    # before _MASReceipt check; com.apple.* prefix wins.
    # With empty env vars: only FakeBrewByApp + Sparkle/Keystone/Squirrel/Orphan
    # are unowned. FakeMasReceipt -> mas (filtered), FakeApple -> softwareupdate
    # (filtered), FakeShortcut -> ineligible (filtered).
    local count
    count=$(run_disc "" "" "" "" | wc -l | tr -d ' ')
    assert_eq "test_emits_one_line_per_app" "5" "$count"
}

test_classifies_sparkle() {
    local handler
    handler=$(run_disc | extract_field "com.fixture.fakesparkle" "fingerprint_handler")
    assert_eq "test_classifies_sparkle" "sparkle" "$handler"
}

test_classifies_keystone() {
    local handler
    handler=$(run_disc | extract_field "com.fixture.fakekeystone" "fingerprint_handler")
    assert_eq "test_classifies_keystone" "keystone" "$handler"
}

test_classifies_squirrel() {
    local handler
    handler=$(run_disc | extract_field "com.fixture.fakesquirrel" "fingerprint_handler")
    assert_eq "test_classifies_squirrel" "squirrel" "$handler"
}

test_orphan_falls_to_builtin() {
    local handler
    handler=$(run_disc | extract_field "com.fixture.fakeorphan" "fingerprint_handler")
    assert_eq "test_orphan_falls_to_builtin" "builtin" "$handler"
}

test_brew_excluded_from_output() {
    local count
    count=$(run_disc "com.fixture.fakeorphan" "" "" "" | wc -l | tr -d ' ')
    assert_eq "test_brew_excluded_from_output" "4" "$count"
}

test_mas_excluded_from_output() {
    local count
    count=$(run_disc "" "com.fixture.fakesparkle" "" "" | wc -l | tr -d ' ')
    assert_eq "test_mas_excluded_from_output" "4" "$count"
}

test_emits_version() {
    local version
    version=$(run_disc | extract_field "com.fixture.fakesparkle" "version")
    assert_eq "test_emits_version" "1.2.3" "$version"
}

test_emits_owned_by_brew() {
    local owned
    owned=$(run_disc "com.fixture.fakeorphan" "" "" "1" \
            | python3 -c '
import json, sys
for line in sys.stdin:
    d = json.loads(line.strip()) if line.strip() else None
    if d and d.get("bundle_id") == "com.fixture.fakeorphan":
        print(d.get("owned_by") or "null")
        break
')
    assert_eq "test_emits_owned_by_brew" "brew" "$owned"
}

test_brew_app_filename_match() {
    # Cask metadata may not always include a bundle id (some casks only
    # ship an `app:` artifact). Match by app filename via "app:Foo.app"
    # entries in ASCENDO_WEB_BREW_CASKS.
    local count
    count=$(run_disc "app:FakeBrewByApp.app" "" "" "" | wc -l | tr -d ' ')
    assert_eq "test_brew_app_filename_match" "4" "$count"
}

test_brew_app_filename_owned_by_field() {
    local owned
    owned=$(run_disc "app:FakeBrewByApp.app" "" "" "1" \
            | python3 -c '
import json, sys
for line in sys.stdin:
    line = line.strip()
    if not line: continue
    d = json.loads(line)
    if d.get("bundle_id") == "org.fixture.brew.byapp":
        print(d.get("owned_by") or "null")
        break
')
    assert_eq "test_brew_app_filename_owned_by_field" "brew" "$owned"
}

test_mas_receipt_auto_filters() {
    # FakeMasReceipt has Contents/_MASReceipt/, so even without
    # ASCENDO_WEB_MAS_BUNDLE_IDS set it should be filtered as mas.
    local count
    count=$(run_disc "" "" "" "1" | python3 -c '
import json, sys
for line in sys.stdin:
    line = line.strip()
    if not line: continue
    d = json.loads(line)
    if d.get("bundle_id") == "com.fixture.fakemas":
        print(d.get("owned_by") or "null")
        break
')
    assert_eq "test_mas_receipt_auto_filters" "mas" "$count"
}

test_apple_prefix_filtered() {
    # com.apple.* gets softwareupdate
    local owned
    owned=$(run_disc "" "" "" "1" | python3 -c '
import json, sys
for line in sys.stdin:
    line = line.strip()
    if not line: continue
    d = json.loads(line)
    if d.get("bundle_id") == "com.apple.fixture":
        print(d.get("owned_by") or "null")
        break
')
    assert_eq "test_apple_prefix_filtered" "softwareupdate" "$owned"
}

test_drivefs_shortcut_ineligible() {
    local owned
    owned=$(run_disc "" "" "" "1" | python3 -c '
import json, sys
for line in sys.stdin:
    line = line.strip()
    if not line: continue
    d = json.loads(line)
    if d.get("bundle_id") == "com.google.drivefs.shortcuts.docs":
        print(d.get("owned_by") or "null")
        break
')
    assert_eq "test_drivefs_shortcut_ineligible" "ineligible" "$owned"
}

test_user_ineligible_pattern_extension() {
    # ASCENDO_WEB_INELIGIBLE_PATTERNS lets the operator extend the
    # ineligible list without a code change.
    local owned
    owned=$(ASCENDO_WEB_INELIGIBLE_PATTERNS="com.fixture.fakesparkle" \
            run_disc "" "" "" "1" | python3 -c '
import json, sys
for line in sys.stdin:
    line = line.strip()
    if not line: continue
    d = json.loads(line)
    if d.get("bundle_id") == "com.fixture.fakesparkle":
        print(d.get("owned_by") or "null")
        break
')
    assert_eq "test_user_ineligible_pattern_extension" "ineligible" "$owned"
}

test_brew_match_takes_precedence_over_mas_receipt() {
    # Some homebrew casks ship apps that ALSO carry a stale _MASReceipt
    # (rehosted from MAS originals). brew ownership should win.
    local owned
    owned=$(run_disc "com.fixture.fakemas" "" "" "1" | python3 -c '
import json, sys
for line in sys.stdin:
    line = line.strip()
    if not line: continue
    d = json.loads(line)
    if d.get("bundle_id") == "com.fixture.fakemas":
        print(d.get("owned_by") or "null")
        break
')
    assert_eq "test_brew_match_takes_precedence_over_mas_receipt" "brew" "$owned"
}

test_emits_one_line_per_app
test_classifies_sparkle
test_classifies_keystone
test_classifies_squirrel
test_orphan_falls_to_builtin
test_brew_excluded_from_output
test_mas_excluded_from_output
test_emits_version
test_emits_owned_by_brew
test_brew_app_filename_match
test_brew_app_filename_owned_by_field
test_mas_receipt_auto_filters
test_apple_prefix_filtered
test_drivefs_shortcut_ineligible
test_user_ineligible_pattern_extension
test_brew_match_takes_precedence_over_mas_receipt

echo
echo "$PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
