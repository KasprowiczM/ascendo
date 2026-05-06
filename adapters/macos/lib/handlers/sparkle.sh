# adapters/macos/lib/handlers/sparkle.sh
# Sparkle update handler.
#
# Functions:
#   sparkle_check <slug> <config_json>   -> echoes latest version or empty
#   sparkle_apply <slug> <config_json>   -> exit 0 on success, non-0 on failure
#
# Depends on lib/ascendo_web.sh helpers:
#   _web_extract_sparkle_latest_version, _web_extract_sparkle_enclosure_url,
#   _web_install_dmg, _web_run_apply_cli
#
# config_json is the per-app TOML row serialised as JSON. The handler
# tolerates several wire shapes:
#   - raw JSON object:     {"slug": "x", ...}
#   - JSON-encoded JSON:   "{\"slug\": \"x\", ...}"
#   - shell-doubled:       "{\\"slug\\": \\"x\\", ...}"  (test harness)
# All three round-trip through _sparkle_get to a dict before key lookup.

# _sparkle_get <key>
#
# Reads JSON config from stdin; echoes the value of <key> (empty when
# absent or the input cannot be coerced into a dict).
#
# We pass the JSON via the ASCENDO_WEB_CFG environment variable and put
# the python source on stdin (heredoc) — that side-steps every
# bash/python escape-sequence interaction.
_sparkle_get() {
    local key="$1"
    local cfg
    cfg="$(cat)"
    ASCENDO_WEB_CFG="$cfg" ASCENDO_WEB_KEY="$key" /usr/bin/python3 <<'PY_EOF'
import json
import os
import sys

raw = os.environ.get("ASCENDO_WEB_CFG", "")
key = os.environ.get("ASCENDO_WEB_KEY", "")


def _coerce(s):
    # 1. Plain JSON object/array.
    try:
        v = json.loads(s)
        if isinstance(v, str):
            # 2. Single-level JSON-encoded string of JSON.
            try:
                return json.loads(v)
            except Exception:
                return None
        return v
    except Exception:
        pass
    # 3. Shell-doubled escapes from naive f-string + repr quoting:
    #    "{\\"slug\\": \\"x\\"}"  -> strip outer quotes, collapse \\" -> ".
    import re
    t = s.strip()
    if len(t) >= 2 and t.startswith('"') and t.endswith('"'):
        t = t[1:-1]
    # Iteratively collapse backslash-escapes: '\\X' -> 'X' for any X.
    # Each pass collapses one level; depth cap of 4 keeps pathological
    # inputs from looping forever.
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
if v is None:
    print("")
elif isinstance(v, (list, dict)):
    print(json.dumps(v))
else:
    print(v)
PY_EOF
}

# sparkle_check
sparkle_check() {
    local slug="$1" cfg="$2"
    local appcast_url
    appcast_url=$(/usr/bin/printf '%s' "$cfg" | _sparkle_get appcast_url)
    [ -z "$appcast_url" ] && return 0
    /usr/bin/curl -fsSL --max-time 10 "$appcast_url" 2>/dev/null \
        | _web_extract_sparkle_latest_version
}

# sparkle_apply
sparkle_apply() {
    local slug="$1" cfg="$2"
    local cli_argv appcast_url app_path

    cli_argv=$(/usr/bin/printf '%s' "$cfg" | _sparkle_get apply_cli_argv)

    if [ -n "$cli_argv" ]; then
        _web_run_apply_cli "$slug" "$cli_argv"
        return $?
    fi

    appcast_url=$(/usr/bin/printf '%s' "$cfg" | _sparkle_get appcast_url)
    [ -z "$appcast_url" ] && return 25

    local enclosure_url
    enclosure_url=$(/usr/bin/curl -fsSL --max-time 10 "$appcast_url" 2>/dev/null \
        | _web_extract_sparkle_enclosure_url)
    [ -z "$enclosure_url" ] && return 26

    app_path=$(/usr/bin/printf '%s' "$cfg" | _sparkle_get app_path)

    _web_install_dmg "$slug" "$enclosure_url" "$app_path"
}
