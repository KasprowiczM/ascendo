# adapters/ubuntu/lib/handlers/release_feed.sh
# Generic JSON-over-HTTPS update probe (Linux port).
#
# Functions:
#   release_feed_check <app_id> <config_json>  -> echoes candidate version
#   release_feed_apply <app_id> <config_json>  -> downloads + extracts/installs
#                                                  (Tier-A iff download_path
#                                                  or download_asset_pattern set)
#
# config_json is the per-app TOML row serialised as JSON.

# _rf_get <key> -- read JSON config from stdin; echo value at <key>.
# Supports dotted keys that walk into "release_feed.*" sub-table.
_rf_get() {
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
    print("")
    sys.exit(0)
if not isinstance(data, dict):
    print("")
    sys.exit(0)
if "." in key:
    cur = data
    for part in key.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        else:
            cur = None
            break
    v = cur
else:
    v = data.get(key)
    if v is None:
        rf = data.get("release_feed") or {}
        v = rf.get(key) if isinstance(rf, dict) else None
if v is None or v is False:
    print("")
elif isinstance(v, (list, dict)):
    print(json.dumps(v))
else:
    print(v)
PY_EOF
}

# _rf_walk_json <body> <dotted_path>
_rf_walk_json() {
    python3 - "$1" "$2" <<'PY'
import json, re, sys
body = sys.argv[1]
path = sys.argv[2]
try:
    data = json.loads(body)
except Exception:
    sys.exit(27)
parts = re.findall(r'[A-Za-z0-9_-]+|\[\d+\]', path)
cur = data
for p in parts:
    if p.startswith('[') and p.endswith(']'):
        idx = int(p[1:-1])
        if not isinstance(cur, list) or idx >= len(cur):
            sys.exit(28)
        cur = cur[idx]
    else:
        if not isinstance(cur, dict) or p not in cur:
            sys.exit(28)
        cur = cur[p]
if cur is None:
    sys.exit(28)
print(cur)
PY
}

# _rf_apply_regex <raw> <pattern> <replace>
_rf_apply_regex() {
    local raw="$1" pattern="$2" replace="$3"
    ASCENDO_RF_RAW="$raw" ASCENDO_RF_PATTERN="$pattern" ASCENDO_RF_REPLACE="$replace" \
    python3 <<'PY_EOF'
import os, re, sys
raw = os.environ.get("ASCENDO_RF_RAW", "")
pattern = os.environ.get("ASCENDO_RF_PATTERN", "")
replace = os.environ.get("ASCENDO_RF_REPLACE", "")
if not pattern:
    print(raw); sys.exit(0)
try:
    out = re.sub(pattern, replace, raw)
except re.error:
    print(raw); sys.exit(0)
print(out if out != raw else raw)
PY_EOF
}

# _rf_pick_asset_url <body> <pattern>
_rf_pick_asset_url() {
    ASCENDO_RF_BODY="$1" ASCENDO_RF_PAT="$2" python3 <<'PY_EOF'
import json, os, re, sys
body = os.environ.get("ASCENDO_RF_BODY", "")
pat = os.environ.get("ASCENDO_RF_PAT", "")
try:
    data = json.loads(body)
except Exception:
    sys.exit(28)
assets = data.get("assets") if isinstance(data, dict) else None
if not isinstance(assets, list):
    sys.exit(28)
try:
    rx = re.compile(pat)
except re.error:
    sys.exit(28)
for a in assets:
    if not isinstance(a, dict):
        continue
    name = a.get("name", "")
    if rx.search(name):
        url = a.get("browser_download_url", "")
        if url:
            print(url); sys.exit(0)
sys.exit(28)
PY_EOF
}

release_feed_check() {
    local app_id="$1" cfg="$2"
    local url version_path timeout version_regex version_replace format
    url=$(printf '%s' "$cfg" | _rf_get "release_feed.url")
    version_path=$(printf '%s' "$cfg" | _rf_get "release_feed.version_path")
    timeout=$(printf '%s' "$cfg" | _rf_get "release_feed.http_timeout_s")
    version_regex=$(printf '%s' "$cfg" | _rf_get "release_feed.version_regex")
    version_replace=$(printf '%s' "$cfg" | _rf_get "release_feed.version_replace")
    format=$(printf '%s' "$cfg" | _rf_get "release_feed.format")
    [ -z "$timeout" ] && timeout=8
    [ -z "$format" ] && format="json"

    if [ -z "$url" ]; then echo ""; return 22; fi
    if [ "$format" = "json" ] && [ -z "$version_path" ]; then echo ""; return 22; fi
    if [ "$format" = "text" ] && [ -z "$version_regex" ]; then echo ""; return 22; fi

    local body
    body=$(curl -fsSL --max-time "$timeout" "$url" 2>/dev/null)
    [ $? -ne 0 ] || [ -z "$body" ] && { echo ""; return 25; }

    # 2 MiB cap
    body=$(printf '%s' "$body" | head -c 2097152)

    if [ "$format" = "text" ]; then
        local extracted
        extracted=$(_rf_apply_regex "$body" "$version_regex" "$version_replace")
        if [ -z "$extracted" ] || [ "$extracted" = "$body" ]; then
            echo ""; return 28
        fi
        printf '%s' "$extracted" | awk 'NR==1{$1=$1; print}'
        return 0
    fi

    local version
    version=$(_rf_walk_json "$body" "$version_path")
    local rc=$?
    if [ "$rc" -ne 0 ]; then echo ""; return "$rc"; fi
    if [ -z "$version" ]; then echo ""; return 28; fi

    if [ -n "$version_regex" ]; then
        version=$(_rf_apply_regex "$version" "$version_regex" "$version_replace")
    fi
    echo "$version"
    return 0
}

release_feed_apply() {
    local app_id="$1" cfg="$2"
    local url download_path download_asset_pattern timeout
    url=$(printf '%s' "$cfg" | _rf_get "release_feed.url")
    download_path=$(printf '%s' "$cfg" | _rf_get "release_feed.download_path")
    download_asset_pattern=$(printf '%s' "$cfg" | _rf_get "release_feed.download_asset_pattern")
    timeout=$(printf '%s' "$cfg" | _rf_get "release_feed.http_timeout_s")
    [ -z "$timeout" ] && timeout=8

    if [ -z "$download_path" ] && [ -z "$download_asset_pattern" ]; then
        # Trigger-only — just emit success without doing anything.
        return 0
    fi

    local body
    body=$(curl -fsSL --max-time "$timeout" "$url" 2>/dev/null) || return 25

    local dl_url
    if [ -n "$download_asset_pattern" ]; then
        dl_url=$(_rf_pick_asset_url "$body" "$download_asset_pattern") || return 28
    else
        dl_url=$(_rf_walk_json "$body" "$download_path") || return 28
    fi
    [ -z "$dl_url" ] && return 28

    # URL-encode + resolve relative URLs
    dl_url=$(BASE="$url" REL="$dl_url" python3 -c '
import os
from urllib.parse import urlparse, urlunparse, urljoin, quote
joined = urljoin(os.environ["BASE"], os.environ["REL"])
u = urlparse(joined)
print(urlunparse((u.scheme, u.netloc, quote(u.path, safe="/%"),
                  u.params, u.query, u.fragment)))
') || return 32
    case "$dl_url" in
        https://*) ;;
        *) return 32 ;;
    esac

    # Delegate to shared installer (tar/deb/AppImage detection)
    _web_install_artifact "$app_id" "$dl_url"
}
