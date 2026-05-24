#!/usr/bin/env bash
# =============================================================================
# scripts/notify.sh — Desktop notification helper
#
# Sends a desktop notification after update-all.sh completes.
# Called automatically by the Stop hook in .claude/settings.json,
# or by update-all.sh directly.
#
# Usage: ./scripts/notify.sh [--reboot] [--errors N] [--time "1m 30s"]
# =============================================================================

REBOOT_REQUIRED=0
ERROR_COUNT=0
ELAPSED=""
TITLE="Ubuntu Updates"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --reboot)  REBOOT_REQUIRED=1 ;;
        --errors)  shift; ERROR_COUNT="$1" ;;
        --time)    shift; ELAPSED="$1" ;;
        --title)   shift; TITLE="$1" ;;
    esac
    shift
done

# ── Compose message ───────────────────────────────────────────────────────────
if [[ $REBOOT_REQUIRED -eq 1 ]]; then
    ICON="system-restart"
    URGENCY="normal"
    MSG="System updated successfully."
    [[ -n "$ELAPSED" ]] && MSG+=" (${ELAPSED})"
    MSG+="\n\n⚠ REBOOT REQUIRED to activate new kernel/driver modules."
    TITLE="Ubuntu Updates — Reboot Required"
elif [[ "$ERROR_COUNT" -gt 0 ]]; then
    ICON="dialog-warning"
    URGENCY="normal"
    MSG="Updates completed with ${ERROR_COUNT} warning(s)."
    [[ -n "$ELAPSED" ]] && MSG+=" (${ELAPSED})"
    MSG+="\nCheck logs for details."
else
    ICON="software-update-available"
    URGENCY="low"
    MSG="All packages updated successfully."
    [[ -n "$ELAPSED" ]] && MSG+=" (${ELAPSED})"
fi

# ── Send notification ─────────────────────────────────────────────────────────
# Try multiple notification methods for compatibility

if command -v notify-send &>/dev/null; then
    # GNOME/GTK desktop notification
    notify-send \
        --urgency="$URGENCY" \
        --icon="$ICON" \
        --app-name="Ascendo" \
        --expire-time=10000 \
        "$TITLE" \
        "$MSG" 2>/dev/null || true

elif command -v zenity &>/dev/null && [[ -n "${DISPLAY:-}" ]]; then
    # Fallback: zenity dialog
    zenity --info \
        --title="$TITLE" \
        --text="$MSG" \
        --timeout=10 2>/dev/null || true

else
    # Terminal fallback
    echo ""
    echo "=== ${TITLE} ==="
    echo "$MSG" | sed 's/\\n/\n/g'
    echo ""
fi

# ── Optional remote notification channels (best-effort, non-fatal) ───────────
# Reads endpoint URLs from settings.json (UI Settings panel) and falls back
# to env vars so unattended/CI runs can route notifications too.

SETTINGS_FILE="${XDG_CONFIG_HOME:-$HOME/.config}/ascendo/settings.json"
_get_setting() {
    # _get_setting <jq-path> — prints value or empty
    [[ -f "$SETTINGS_FILE" ]] || return 0
    python3 -c "
import json, sys
try:
    d = json.load(open('${SETTINGS_FILE}'))
except Exception:
    sys.exit(0)
keys = '$1'.split('.')
v = d
for k in keys:
    if not isinstance(v, dict): sys.exit(0)
    v = v.get(k)
    if v is None: sys.exit(0)
print(v)
" 2>/dev/null
}

NTFY_URL="${UA_NTFY_URL:-$(_get_setting notifications.ntfy_url)}"
SLACK_URL="${UA_SLACK_WEBHOOK:-$(_get_setting notifications.slack_webhook)}"
EMAIL_TO="${UA_EMAIL_TO:-$(_get_setting notifications.email_to)}"
TG_BOT="${UA_TELEGRAM_BOT:-$(_get_setting notifications.telegram_bot_token)}"
TG_CHAT="${UA_TELEGRAM_CHAT:-$(_get_setting notifications.telegram_chat_id)}"

# ntfy.sh — simplest: POST plaintext body
if [[ -n "$NTFY_URL" ]] && command -v curl >/dev/null 2>&1; then
    curl -fsS -X POST \
        -H "Title: ${TITLE}" \
        -H "Priority: ${URGENCY}" \
        -d "$(printf '%b' "$MSG")" \
        "$NTFY_URL" >/dev/null 2>&1 || true
fi

# Slack incoming webhook
if [[ -n "$SLACK_URL" ]] && command -v curl >/dev/null 2>&1; then
    SLACK_BODY="$(printf '%b' "$MSG")"
    SLACK_PAYLOAD=$(SLACK_TITLE="$TITLE" SLACK_BODY="$SLACK_BODY" python3 -c '
import json, os
title = os.environ.get("SLACK_TITLE", "")
body  = os.environ.get("SLACK_BODY", "")
print(json.dumps({"text": f"*{title}*\n{body}"}))
' 2>/dev/null)
    [[ -n "$SLACK_PAYLOAD" ]] && curl -fsS -X POST \
        -H 'content-type: application/json' \
        --data "$SLACK_PAYLOAD" \
        "$SLACK_URL" >/dev/null 2>&1 || true
fi

# Email via local mailx/sendmail
if [[ -n "$EMAIL_TO" ]]; then
    if command -v mail >/dev/null 2>&1; then
        printf '%b\n' "$MSG" | mail -s "$TITLE" "$EMAIL_TO" >/dev/null 2>&1 || true
    elif command -v sendmail >/dev/null 2>&1; then
        {
            printf 'Subject: %s\n' "$TITLE"
            printf 'To: %s\n\n'    "$EMAIL_TO"
            printf '%b\n' "$MSG"
        } | sendmail "$EMAIL_TO" >/dev/null 2>&1 || true
    fi
fi

# Telegram bot
if [[ -n "$TG_BOT" && -n "$TG_CHAT" ]] && command -v curl >/dev/null 2>&1; then
    TG_MSG="$(printf '*%s*\n%b' "$TITLE" "$MSG")"
    curl -fsS -X POST \
        "https://api.telegram.org/bot${TG_BOT}/sendMessage" \
        --data-urlencode "chat_id=${TG_CHAT}" \
        --data-urlencode "text=${TG_MSG}" \
        --data-urlencode "parse_mode=Markdown" >/dev/null 2>&1 || true
fi
