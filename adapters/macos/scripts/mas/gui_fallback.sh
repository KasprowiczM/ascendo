#!/usr/bin/env bash
# =============================================================================
# adapters/macos/scripts/mas/gui_fallback.sh -- TOR-2 App Store GUI automation
# =============================================================================
# Ports the Aktualizacje_MAC TOR-2 path for iPad-on-Apple-Silicon apps that
# `mas` cannot touch (UniFi, WiFiman, Picsart, etc. — `mas` officially does
# not support iPad apps; documented vendor limitation). Drives the App Store
# UI via AppleScript: opens Updates pane → clicks "Update All" → falls back
# to per-row "Update" buttons → quits the visible window when done.
#
# Args:
#   [--check-permission]   probe AX permission only; print "ok"/"denied"; exit 0/2
#   [--quiet]              suppress progress output
#   [--open-prefs]         open Privacy/Accessibility pane on AX denial (default ON)
#   [--no-open-prefs]      don't auto-open the pane (CI mode)
#
# Exit codes:
#   0  success — Update All clicked, individual updates clicked, OR no updates found
#   2  Accessibility permission missing (operator action required)
#   3  AppleScript error (App Store wouldn't open / structural failure)
#   4  bad usage
# =============================================================================
set -o pipefail

CHECK_ONLY=0
QUIET=0
OPEN_PREFS=1
while [ $# -gt 0 ]; do
    case "$1" in
        --check-permission) CHECK_ONLY=1; shift ;;
        --quiet|-q)         QUIET=1; shift ;;
        --open-prefs)       OPEN_PREFS=1; shift ;;
        --no-open-prefs)    OPEN_PREFS=0; shift ;;
        --help|-h)
            sed -n '2,18p' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *) printf "gui_fallback.sh: unknown arg: %s\n" "$1" >&2; exit 4 ;;
    esac
done

say() { [ "$QUIET" = 0 ] && printf "%s\n" "$1"; }

# ── 1. Probe Accessibility permission ──────────────────────────────────────
AX_TEST="$(osascript -e 'tell application "System Events" to return name of first process whose frontmost is true' 2>&1)"
if printf '%s' "$AX_TEST" | grep -qiE 'not allowed|assistive|accessibility|access'; then
    if [ "$CHECK_ONLY" = 1 ]; then
        say "denied"
        exit 2
    fi
    say "ERROR: Accessibility permission missing for this terminal."
    say "       Grant via System Settings → Privacy & Security → Accessibility."
    say "       Add: Terminal.app, Warp.app, iTerm.app, or whichever you ran this from."
    if [ "$OPEN_PREFS" = 1 ]; then
        say "Opening Privacy → Accessibility pane …"
        open "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility" >/dev/null 2>&1 || true
    fi
    exit 2
fi
if [ "$CHECK_ONLY" = 1 ]; then
    say "ok"
    exit 0
fi

# ── 2. Drive App Store via AppleScript ─────────────────────────────────────
# Three passes in order: Update All (top-level), Update All (deep walk),
# per-item Update buttons. Mirrors the Aktualizacje_MAC TOR-2 logic. The
# AppleScript is held verbatim in this file so a future audit can diff it
# against the reference without spelunking through Python/Pydantic layers.
say "Opening App Store Updates …"
AS_RESULT="$(osascript 2>&1 <<'APPLESCRIPT'
tell application "App Store" to activate
delay 2
open location "macappstores://showUpdatesPage"
delay 6

tell application "System Events"
    tell process "App Store"
        set frontmost to true
        delay 2

        -- PASS 1: shallow scan for "Update All"
        set candidates to {}
        try
            set candidates to candidates & (buttons of window 1)
        end try
        try
            repeat with grp in (groups of window 1)
                try
                    set candidates to candidates & (buttons of grp)
                end try
            end repeat
        end try
        repeat with btn in candidates
            try
                if name of btn contains "Update All" then
                    click btn
                    delay 2
                    return "UPDATE_ALL_CLICKED"
                end if
            end try
        end repeat

        -- PASS 2: deep walk
        set found to false
        try
            set allElems to entire contents of window 1
            repeat with elem in allElems
                try
                    if class of elem is button then
                        if name of elem contains "Update All" then
                            click elem
                            set found to true
                            delay 2
                            exit repeat
                        end if
                    end if
                end try
            end repeat
        end try
        if found then return "UPDATE_ALL_DEEP"

        -- PASS 3: per-row Update buttons (locale-aware)
        set updateCount to 0
        try
            set allElems to entire contents of window 1
            repeat with elem in allElems
                try
                    if class of elem is button then
                        set btnName to name of elem
                        if btnName is "Update" or btnName is "Aktualizuj" or btnName is "Aktualisieren" or btnName is "Actualizar" or btnName is "Aggiorna" or btnName is "Atualizar" or btnName is "Actualiser" or btnName contains "Update" then
                            click elem
                            set updateCount to updateCount + 1
                            delay 1
                        end if
                    end if
                end try
            end repeat
        end try
        if updateCount > 0 then
            return "INDIVIDUAL_UPDATES:" & updateCount
        end if

        return "NO_UPDATES_FOUND"
    end tell
end tell
APPLESCRIPT
)"

# ── 3. Interpret + report ──────────────────────────────────────────────────
case "$AS_RESULT" in
    *"not allowed"*|*assistive*|*Accessibility*)
        say "WARN: Accessibility permission revoked mid-run."
        exit 2
        ;;
    UPDATE_ALL_CLICKED|UPDATE_ALL_DEEP)
        say "OK: clicked 'Update All' — installs continue in background."
        # Hide window so it doesn't linger; quit would cancel installs.
        osascript -e 'tell application "System Events" to set visible of process "App Store" to false' >/dev/null 2>&1 || true
        exit 0
        ;;
    INDIVIDUAL_UPDATES:*)
        COUNT="${AS_RESULT#INDIVIDUAL_UPDATES:}"
        say "OK: clicked $COUNT individual Update button(s)."
        osascript -e 'tell application "System Events" to set visible of process "App Store" to false' >/dev/null 2>&1 || true
        exit 0
        ;;
    NO_UPDATES_FOUND)
        say "OK: App Store reports no updates available."
        osascript -e 'tell application "App Store" to quit' >/dev/null 2>&1 || true
        exit 0
        ;;
    *)
        say "ERROR: AppleScript returned unexpected output:"
        say "$AS_RESULT"
        exit 3
        ;;
esac
