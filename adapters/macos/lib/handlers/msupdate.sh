# adapters/macos/lib/handlers/msupdate.sh
# Microsoft AutoUpdate handler.
# Wraps `msupdate --list` (check) and `sudo msupdate --install` (apply).
# One MS365 entry covers Word/Excel/PPT/Outlook/OneNote/Teams.

msupdate_check() {
    local slug="$1" cfg="$2"
    /usr/bin/command -v msupdate >/dev/null 2>&1 || return 0
    local out
    out=$(msupdate --list 2>/dev/null || true)
    if /usr/bin/printf '%s' "$out" | /usr/bin/grep -qiE 'pending|update available|update is available'; then
        echo "pending"
    fi
}

msupdate_apply() {
    local slug="$1" cfg="$2"
    /usr/bin/command -v msupdate >/dev/null 2>&1 || return 30
    sudo -A msupdate --install
}
