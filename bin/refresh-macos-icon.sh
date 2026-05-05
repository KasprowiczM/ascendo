#!/usr/bin/env bash
# bin/refresh-macos-icon.sh -- flush macOS icon caches so a rebuilt
# Ascendo.app shows its NEW icon in Cmd+Tab / Dock / Finder immediately.
# =============================================================================
# macOS aggressively caches app-bundle icons; even after `npm run tauri build`
# refreshes the .app on disk, the Dock and the Cmd+Tab switcher continue
# rendering the OLD icon until the kernel/IconServices caches are evicted.
#
# This script:
#   1. touches every Ascendo.app it finds in the standard install locations
#      (so Launch Services notices the bundle changed)
#   2. clears IconServices caches under /private/var/folders (per-user)
#   3. restarts Dock + Finder to repopulate their icon caches
#
# Usage:
#   bash bin/refresh-macos-icon.sh              # full refresh (uses sudo)
#   bash bin/refresh-macos-icon.sh --no-sudo    # skip the sudo lines
#   bash bin/refresh-macos-icon.sh --help
#
# Bash 3.2 compatible. Idempotent -- safe to re-run.
# =============================================================================
set -e

USE_SUDO=1
while [ $# -gt 0 ]; do
    case "$1" in
        --no-sudo) USE_SUDO=0; shift ;;
        --help|-h)
            sed -n '2,21p' "$0"; exit 0 ;;
        # Tolerate inline shell comments from copy-paste.
        \#*) shift ;;
        *) echo "unknown flag: $1" >&2; exit 2 ;;
    esac
done

if [ "$(uname -s)" != "Darwin" ]; then
    echo "error: this script is macOS-only (uname=$(uname -s))" >&2
    exit 1
fi

step() { printf "==> %s\n" "$*"; }
note() { printf "    %s\n" "$*"; }

# 1. Touch every Ascendo.app we can find so Launch Services re-reads metadata.
TOUCHED=0
for APP in /Applications/Ascendo.app "$HOME/Applications/Ascendo.app"; do
    if [ -d "$APP" ]; then
        step "touching $APP"
        if [ "$USE_SUDO" = "1" ] && [ ! -w "$APP" ]; then
            sudo /usr/bin/touch "$APP" || note "touch failed (continuing)"
        else
            /usr/bin/touch "$APP" || note "touch failed (continuing)"
        fi
        TOUCHED=$((TOUCHED + 1))
    fi
done

# Also touch any dev-build .app sitting in the Tauri bundle dir.
DEV_APP="ui/desktop-tauri/src-tauri/target/release/bundle/macos/Ascendo.app"
if [ -d "$DEV_APP" ]; then
    step "touching $DEV_APP (dev build)"
    /usr/bin/touch "$DEV_APP" || note "touch failed (continuing)"
    TOUCHED=$((TOUCHED + 1))
fi

if [ "$TOUCHED" = "0" ]; then
    note "no Ascendo.app found in standard locations -- continuing with cache flush anyway"
fi

# 2. Clear per-user IconServices caches.
step "clearing IconServices caches under /private/var/folders"
if [ "$USE_SUDO" = "1" ]; then
    sudo /usr/bin/find /private/var/folders \
        -name 'com.apple.iconservices' -type d -prune -exec rm -rf {} + 2>/dev/null || true
    sudo /usr/bin/find /private/var/folders \
        -name 'com.apple.dock.iconcache' -delete 2>/dev/null || true
else
    /usr/bin/find /private/var/folders \
        -name 'com.apple.iconservices' -type d -prune -exec rm -rf {} + 2>/dev/null || true
    /usr/bin/find /private/var/folders \
        -name 'com.apple.dock.iconcache' -delete 2>/dev/null || true
fi

# 3. Re-register the .app with Launch Services (lsregister) -- forces a fresh
#    icon read on next Dock/Finder query.
LSREG=/System/Library/Frameworks/CoreServices.framework/Versions/A/Frameworks/LaunchServices.framework/Versions/A/Support/lsregister
if [ -x "$LSREG" ]; then
    for APP in /Applications/Ascendo.app "$HOME/Applications/Ascendo.app" "$DEV_APP"; do
        if [ -d "$APP" ]; then
            step "re-registering $APP with Launch Services"
            "$LSREG" -f "$APP" 2>/dev/null || note "lsregister returned non-zero (non-fatal)"
        fi
    done
fi

# 4. Restart Dock + Finder so they pick up the fresh icons.
step "restarting Dock and Finder (UI flicker is expected)"
killall Dock 2>/dev/null || note "Dock was not running"
killall Finder 2>/dev/null || note "Finder was not running"

step "done. Cmd+Tab to Ascendo to verify the new icon."
note "if it still shows the old icon, log out and back in (full IconServices reset)."
