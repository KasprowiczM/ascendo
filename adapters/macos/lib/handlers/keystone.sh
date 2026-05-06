# adapters/macos/lib/handlers/keystone.sh
# Google Keystone (GoogleSoftwareUpdate) handler.
#
# Functions:
#   keystone_check <slug> <config_json>   -> always echoes empty (Keystone opaque)
#   keystone_apply <slug> <config_json>   -> invokes ksadmin --update -productid <id>

# _keystone_get <key> — same heredoc-via-env pattern as sparkle/_gh_get.
_keystone_get() {
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

_keystone_find_ksadmin() {
    if /usr/bin/command -v ksadmin >/dev/null 2>&1; then
        /usr/bin/command -v ksadmin
        return 0
    fi
    local candidates=(
        "$HOME/Library/Google/GoogleSoftwareUpdate/GoogleSoftwareUpdate.bundle/Contents/Helpers/ksadmin"
        "/Library/Google/GoogleSoftwareUpdate/GoogleSoftwareUpdate.bundle/Contents/Helpers/ksadmin"
    )
    local p
    for p in "${candidates[@]}"; do
        if [ -x "$p" ]; then
            echo "$p"
            return 0
        fi
    done
    return 1
}

# keystone_check — Honest answer: Keystone introspection is opaque.
# Always echoes empty (signals "skipped, will trigger via apply").
keystone_check() {
    local slug="$1" cfg="$2"
    local ks
    ks=$(_keystone_find_ksadmin) || return 0
    return 0
}

# keystone_apply — Trigger the daemon update for the given product id.
keystone_apply() {
    local slug="$1" cfg="$2"
    local product_id
    product_id=$(/usr/bin/printf '%s' "$cfg" | _keystone_get ksadmin_product_id)
    [ -z "$product_id" ] && return 28

    local ks
    ks=$(_keystone_find_ksadmin) || return 29

    "$ks" --update -productid "$product_id" 2>&1
}
