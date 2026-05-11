# adapters/ubuntu/lib/handlers/appimage.sh
# AppImage handler — local introspection only.
#
# Functions:
#   appimage_check <app_id> <config_json>   -> echoes embedded version (or empty)
#   appimage_apply <app_id> <config_json>   -> Tier-B; emits trigger only
#
# This handler does NOT probe a remote feed. It only reads the version
# embedded in the AppImage via `<file> --appimage-version`. If the
# AppImage doesn't support that flag, the handler returns empty
# (caller treats that as Tier-B trigger-only).
#
# For real remote-update flows, use github_release or release_feed.
# Future: implement appimage-update (zsync) as the apply path.

_appimage_get() {
    local key="$1"
    local cfg
    cfg="$(cat)"
    ASCENDO_WEB_CFG="$cfg" ASCENDO_WEB_KEY="$key" python3 <<'PY_EOF'
import json, os, sys
raw = os.environ.get("ASCENDO_WEB_CFG", "")
key = os.environ.get("ASCENDO_WEB_KEY", "")
try:
    data = json.loads(raw)
except Exception:
    print(""); sys.exit(0)
if not isinstance(data, dict):
    print(""); sys.exit(0)
if "." in key:
    cur = data
    for part in key.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        else:
            cur = None; break
    v = cur
else:
    v = data.get(key)
    if v is None:
        sub = data.get("appimage") or {}
        v = sub.get(key) if isinstance(sub, dict) else None
if v is None or v is False:
    print("")
elif isinstance(v, (list, dict)):
    print(json.dumps(v))
else:
    print(v)
PY_EOF
}

appimage_check() {
    local app_id="$1" cfg="$2"
    local path
    path=$(printf '%s' "$cfg" | _appimage_get "appimage.path")
    # Caller (check.sh) may pass the discovered path via _discovered_path;
    # honour that when the registry didn't pin one.
    [ -z "$path" ] && path=$(printf '%s' "$cfg" | _appimage_get "_discovered_path")
    if [ -z "$path" ] || [ ! -x "$path" ]; then
        echo ""; return 22
    fi
    local v
    v=$(timeout 5 "$path" --appimage-version 2>/dev/null) || true
    if [ -n "$v" ]; then
        printf '%s' "$v" | head -1 | sed -E 's/^v//; s/[[:space:]]+$//'
        return 0
    fi
    echo ""
    return 28
}

appimage_apply() {
    local app_id="$1" cfg="$2"
    # Self-update via appimage-update / zsync is deferred. Surface as
    # trigger by returning success with no mutation.
    return 0
}
