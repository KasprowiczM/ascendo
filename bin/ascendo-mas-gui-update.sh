#!/usr/bin/env bash
# bin/ascendo-mas-gui-update.sh -- thin entry point for the TOR-2 path
#
# Use case: `mas list` shows iPad-on-Apple-Silicon apps (UniFi, WiFiman,
# Picsart, etc.) that `mas upgrade` cannot touch. This script drives the
# App Store UI to install their pending updates.
#
# Idempotent. Safe to run after `mas upgrade`. First run prompts for
# Accessibility permission and opens System Settings → Privacy
# automatically.
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
exec bash "$SCRIPT_DIR/../adapters/macos/scripts/mas/gui_fallback.sh" "$@"
