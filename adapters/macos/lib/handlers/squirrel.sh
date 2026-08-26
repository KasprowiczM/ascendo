# adapters/macos/lib/handlers/squirrel.sh
# Squirrel.Mac auto-on-relaunch handler.
#
# Apply = open -a "$app_path". App self-updates in the background on launch.
# Verify (in scripts/web/verify.sh) sleeps 30s then re-reads version.

# _squirrel_get <key> — heredoc-via-env JSON parser (same pattern as
# sparkle/_gh_get/_keystone_get; consolidation deferred to milestone-final).
_squirrel_get() {
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

# squirrel_check — Squirrel.Mac is opaque from outside the app process; the
# latest version is only known after the app self-updates on relaunch. So
# we always echo empty (signals "skipped, will trigger via apply").
squirrel_check() {
    local slug="$1" cfg="$2"
    return 0
}

# squirrel_apply — Just open the app. Squirrel runs in-process and downloads
# the new version in the background; the next time the user quits + relaunches
# they'll be on the new version.
squirrel_apply() {
    local slug="$1" cfg="$2"
    local app_path
    app_path=$(/usr/bin/printf '%s' "$cfg" | _squirrel_get app_path)
    if [ -z "$app_path" ]; then
        local display_name
        display_name=$(/usr/bin/printf '%s' "$cfg" | _squirrel_get display_name)
        app_path="/Applications/${display_name}.app"
    fi
    # Hidden launch matches macOS_updates silent_launch_app (`open -gjF`).
    # Safe mode still launches hidden — that is the Squirrel update channel.
    if [ -d "$app_path" ]; then
        /usr/bin/env open -gjF "$app_path" 2>/dev/null \
            || /usr/bin/env open -gj "$app_path" 2>/dev/null \
            || /usr/bin/env open -a "$app_path"
        return $?
    fi
    /usr/bin/env open -gjF -a "$app_path" 2>/dev/null \
        || /usr/bin/env open -gj -a "$app_path" 2>/dev/null \
        || /usr/bin/env open -a "$app_path"
}
