#!/usr/bin/env bats
# W10 + W2 (audit ASCENDO_ULTRA_REVIEW_2 §4): macOS web-handler honesty.
#
# W10: web_discovery.sh must emit an explicit DISCOVERY_OK / DISCOVERY_FAILED
#      sentinel so a crashed discovery is distinguishable from "0 web apps".
# W2:  release_feed.sh _rf_apply_regex must fail-loud (rc=28) when a configured
#      version_regex does NOT match — never silently degrade to the raw value.

setup() {
    LIB="${BATS_TEST_DIRNAME}/../lib"
    TMP="$(mktemp -d)"
}

teardown() {
    rm -rf "$TMP"
}

# ── W10: discovery sentinels ─────────────────────────────────────────────────

@test "W10: discovery emits DISCOVERY_OK on a clean (empty) run, exit 0" {
    run env ASCENDO_WEB_APPS_ROOT="$TMP" bash "$LIB/web_discovery.sh" --emit-json
    [ "$status" -eq 0 ]
    [[ "$output" == *"DISCOVERY_OK"* ]]
}

@test "W10: missing apps root emits DISCOVERY_FAILED + nonzero exit" {
    run env ASCENDO_WEB_APPS_ROOT="$TMP/does-not-exist" bash "$LIB/web_discovery.sh" --emit-json
    [ "$status" -ne 0 ]
    [[ "$output" == *"DISCOVERY_FAILED"* ]]
    # And it must NOT look like a clean 0-app run.
    [[ "$output" != *"DISCOVERY_OK"* ]]
}

# ── W2: release_feed regex fail-loud ─────────────────────────────────────────

@test "W2: version_regex that does not match returns probe_broken (rc=28), empty" {
    source "$LIB/handlers/release_feed.sh"
    run _rf_apply_regex "v0.2026.05.06.stable_02" '^X(.+)Y$' '\1'
    [ "$status" -eq 28 ]
    [ -z "$output" ]
}

@test "W2: version_regex that matches transforms the value (rc=0)" {
    source "$LIB/handlers/release_feed.sh"
    run _rf_apply_regex "v1.2.stable_3" '^v(.+)\.stable_(.+)$' '\1.\2'
    [ "$status" -eq 0 ]
    [ "$output" = "1.2.3" ]
}

@test "W2: empty version_regex passes the value through unchanged (rc=0)" {
    source "$LIB/handlers/release_feed.sh"
    run _rf_apply_regex "1.2.3" "" ""
    [ "$status" -eq 0 ]
    [ "$output" = "1.2.3" ]
}
