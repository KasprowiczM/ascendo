# adapters/macos/lib/handlers/builtin.sh
# Built-in updater handler — open the app and tell the user to use its
# Help → Check for Updates flow.
#
# This is the catch-all handler for apps that ship their own updater but
# don't expose an automation surface (no Sparkle feed, no Keystone product
# id, no Squirrel auto-on-relaunch). Examples: Zoom, Discord (older), some
# Adobe apps. The best Ascendo can do is open the app and surface a hint.

# _builtin_get <key> — heredoc-via-env JSON parser (same pattern as
# sparkle/_gh_get/_keystone_get/_squirrel_get).
_builtin_get() {
    local key="$1"
    local cfg
    cfg="$(cat)"
    ASCENDO_WEB_CFG="$cfg" ASCENDO_WEB_KEY="$key" /usr/bin/python3 <<'PY_EOF'
import json
import os
import re
import sys

raw = os.environ.get("ASCENDO_WEB_CFG", "")
key = os.environ.get("ASCENDO_WEB_KEY", "")


def _coerce(s):
    try:
        v = json.loads(s)
        if isinstance(v, str):
            try:
                return json.loads(v)
            except Exception:
                return None
        return v
    except Exception:
        pass
    t = s.strip()
    if len(t) >= 2 and t.startswith('"') and t.endswith('"'):
        t = t[1:-1]
    for _ in range(4):
        try:
            return json.loads(t)
        except Exception:
            pass
        new_t = re.sub(r'\\(.)', r'\1', t)
        if new_t == t:
            break
        t = new_t
    try:
        return json.loads(t)
    except Exception:
        return None


data = _coerce(raw)
if not isinstance(data, dict):
    print("")
    sys.exit(0)

v = data.get(key)
if v is None or v is False:
    print("")
elif v is True:
    print("true")
elif isinstance(v, (list, dict)):
    print(json.dumps(v))
else:
    print(v)
PY_EOF
}

# builtin_check — No way to peek at the in-app updater from outside the
# app process; always echo empty (signals "skipped, manual trigger needed").
builtin_check() {
    local slug="$1" cfg="$2"
    return 0
}

# builtin_apply — Open the app and emit an info instruction to stderr so
# the operator knows to use the in-app Help → Check for Updates flow.
# We exit 0 because we did everything we could (the app is open).
builtin_apply() {
    local slug="$1" cfg="$2"
    local app_path update_url
    app_path=$(/usr/bin/printf '%s' "$cfg" | _builtin_get app_path)
    update_url=$(/usr/bin/printf '%s' "$cfg" | _builtin_get update_url)
    if [ -z "$app_path" ]; then
        local display_name
        display_name=$(/usr/bin/printf '%s' "$cfg" | _builtin_get display_name)
        app_path="/Applications/${display_name}.app"
    fi
    /usr/bin/env open -a "$app_path"
    if [ -n "$update_url" ]; then
        echo "Open the app's Help menu and run Check for Updates ($update_url)" >&2
    else
        echo "Open the app's Help menu and run Check for Updates" >&2
    fi
}
