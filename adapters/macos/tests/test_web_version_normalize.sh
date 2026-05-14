#!/usr/bin/env bash
# Regression tests for _normalize_version + _version_eq_loose +
# _web_resolve_bundle_path in adapters/macos/lib/ascendo_web.sh.
#
# Run: bash adapters/macos/tests/test_web_version_normalize.sh
# Exit 0 on all pass; exit 1 with diagnostic on first failure.
set -u
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
# shellcheck disable=SC1091
. "$REPO_ROOT/adapters/macos/lib/ascendo_web.sh"

PASS=0
FAIL=0

_check() {
    local label="$1" expected="$2" actual="$3"
    if [ "$expected" = "$actual" ]; then
        PASS=$((PASS + 1))
        printf '  [PASS] %s\n' "$label"
    else
        FAIL=$((FAIL + 1))
        printf '  [FAIL] %s\n         expected=%q  actual=%q\n' \
            "$label" "$expected" "$actual"
    fi
}

# ─── _normalize_version ─────────────────────────────────────────────
echo "_normalize_version"
_check "parens collapse to dots"        "7.0.0.77593"   "$(_normalize_version '7.0.0 (77593)')"
_check "plain stays"                    "1.2.3"         "$(_normalize_version '1.2.3')"
_check "alphanumeric build suffix"      "1.2.3.b4"      "$(_normalize_version '1.2.3 (b4)')"
_check "space → hyphen"                 "1.2.3-beta"    "$(_normalize_version '1.2.3 beta')"
_check "no-op preserves dots"           "125.0.0.0"     "$(_normalize_version '125.0.0.0')"

# ─── _version_eq_loose ─────────────────────────────────────────────
echo "_version_eq_loose"
if _version_eq_loose "7.0.0 (77593)" "7.0.0.77593"; then
    PASS=$((PASS + 1)); echo "  [PASS] zoom parens match dotted"
else
    FAIL=$((FAIL + 1)); echo "  [FAIL] zoom parens should match dotted"
fi
if _version_eq_loose "125.0" "125.0.0.0"; then
    PASS=$((PASS + 1)); echo "  [PASS] short version pads with zeros"
else
    FAIL=$((FAIL + 1)); echo "  [FAIL] 125.0 should equal 125.0.0.0"
fi
if _version_eq_loose "1.2.3" "1.2.3"; then
    PASS=$((PASS + 1)); echo "  [PASS] strict equal still passes"
else
    FAIL=$((FAIL + 1)); echo "  [FAIL] 1.2.3 should equal 1.2.3"
fi
if _version_eq_loose "1.2.3" "1.2.4"; then
    FAIL=$((FAIL + 1)); echo "  [FAIL] 1.2.3 ≠ 1.2.4 must report different"
else
    PASS=$((PASS + 1)); echo "  [PASS] real diff stays diff"
fi
if _version_eq_loose "148.0.7778.97" "148.0.7778.168"; then
    FAIL=$((FAIL + 1)); echo "  [FAIL] differing-tail should not match"
else
    PASS=$((PASS + 1)); echo "  [PASS] differing-tail stays diff"
fi

# ─── _should_skip_upgrade uses loose equality ──────────────────────
echo "_should_skip_upgrade loose"
if _should_skip_upgrade "7.0.0 (77593)" "7.0.0.77593"; then
    PASS=$((PASS + 1)); echo "  [PASS] zoom loose equality blocks reinstall"
else
    FAIL=$((FAIL + 1)); echo "  [FAIL] zoom loose equality should skip reinstall"
fi
if _should_skip_upgrade "125.0" "125.0.0.0"; then
    PASS=$((PASS + 1)); echo "  [PASS] gdrive loose equality blocks reinstall"
else
    FAIL=$((FAIL + 1)); echo "  [FAIL] gdrive 125.0/125.0.0.0 should skip"
fi
# Real outdated must NOT skip
if _should_skip_upgrade "1.0" "2.0"; then
    FAIL=$((FAIL + 1)); echo "  [FAIL] real outdated must not be skipped"
else
    PASS=$((PASS + 1)); echo "  [PASS] real outdated not skipped"
fi

# ─── _web_resolve_bundle_path fallback chain ───────────────────────
# Without mocking system_profiler we can only assert behavior shape:
#   - empty bundle_id + existing fallback path → returns fallback
#   - empty bundle_id + missing fallback path  → empty
#   - non-existent bundle_id + missing fallback path → empty
echo "_web_resolve_bundle_path"
TMPDIR_=$(mktemp -d)
trap 'rm -rf "$TMPDIR_"' EXIT
FAKE_APP="$TMPDIR_/FakeApp.app"
mkdir -p "$FAKE_APP/Contents"
result=$(_web_resolve_bundle_path "" "$FAKE_APP")
_check "empty bid returns fallback when exists" "$FAKE_APP" "$result"
result=$(_web_resolve_bundle_path "" "/nonexistent.app")
_check "empty bid + missing fallback → empty" "" "$result"
result=$(_web_resolve_bundle_path "io.absolutely.does.not.exist.zz" "/nonexistent.app")
_check "missing bid + missing fallback → empty" "" "$result"

# ─── Summary ───────────────────────────────────────────────────────
printf '\n'
printf '  %d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
