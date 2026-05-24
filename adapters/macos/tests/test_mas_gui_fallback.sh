#!/usr/bin/env bash
# Smoke test for adapters/macos/scripts/mas/gui_fallback.sh
set -u
THIS_DIR="$(cd "$(dirname "$0")" && pwd)"
SCRIPT="$THIS_DIR/../scripts/mas/gui_fallback.sh"
[ -f "$SCRIPT" ] || { echo "FAIL: missing $SCRIPT"; exit 1; }
bash -n "$SCRIPT" || { echo "FAIL: bash -n"; exit 1; }
out="$(bash "$SCRIPT" --help 2>&1)"; case "$out" in *Accessibility*) ;; *) echo "FAIL: --help missing 'Accessibility'"; exit 1 ;; esac
bash "$SCRIPT" --check-permission --quiet; rc=$?; case "$rc" in 0|2) ;; *) echo "FAIL: --check-permission returned $rc"; exit 1 ;; esac
bash "$SCRIPT" --bogus-flag --quiet 2>/dev/null; rc=$?; [ "$rc" = "4" ] || { echo "FAIL: bad-arg returned $rc"; exit 1; }
echo "PASS: gui_fallback.sh smoke (4/4)"
