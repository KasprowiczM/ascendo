# adapters/macos/lib/handlers/release_feed.sh
# Generic JSON-over-HTTPS update probe.
#
# Functions:
#   release_feed_check <slug> <config_json>  -> echoes candidate version
#                                                or empty + non-zero rc
#   release_feed_apply <slug> <config_json>  -> downloads + installs DMG
#                                                if download_path set,
#                                                else falls back to open -a
#
# Depends on lib/ascendo_web.sh helpers:
#   _web_install_dmg
#
# config_json is the per-app TOML row serialised as JSON.  The "release_feed"
# sub-table (url, version_path, optional download_path / arch_path /
# expected_arch / http_timeout_s) drives this handler.

# _rf_get <key>
#
# Reads JSON config from stdin; echoes the value at <key>.
# Supports dotted keys that walk into "release_feed.*" sub-table.
# Uses the same heredoc-via-env pattern as sparkle.sh to avoid
# bash/python quoting interactions.
_rf_get() {
    local key="$1"
    local cfg
    cfg="$(cat)"
    ASCENDO_WEB_CFG="$cfg" ASCENDO_WEB_KEY="$key" /usr/bin/python3 <<'PY_EOF'
import json, os, sys

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
        return None


data = _coerce(raw)
if not isinstance(data, dict):
    print("")
    sys.exit(0)

# Support dotted access into release_feed sub-table:
# "release_feed.url" => data["release_feed"]["url"]
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

# _rf_walk_json <json_body> <dotted_path>
#
# Walks a dotted path (e.g. "latest.darwin.arm64.version") through the
# supplied JSON body and prints the leaf value.
#
# Exit codes:
#   0  — success, value echoed on stdout
#   27 — body is not valid JSON
#   28 — path not found (key missing or null at some node)
_rf_walk_json() {
    /usr/bin/python3 - "$1" "$2" <<'PY'
import json, re, sys

body = sys.argv[1]
path = sys.argv[2]


def _parse_minimal_yaml(text: str):
    """Minimal Electron-builder latest-mac.yml parser.

    Handles the shape vendors actually publish:
        version: 7.16.0
        files:
          - url: Notion-7.16.0.dmg
            sha512: ...
            size: 124190392
          - url: Notion-7.16.0.zip
            sha512: ...
        path: Notion-7.16.0.zip
        releaseDate: '2026-05-05T21:02:49.644Z'

    Two-space indentation only. List items start with "  - ". Values are
    scalars (no nested mappings inside list items beyond one level — the
    canonical Electron-builder schema doesn't use them). Quoted strings
    are unquoted. NOT a full YAML parser; bails on anything unexpected.
    """
    data: dict = {}
    cur_list = None
    cur_list_item = None
    pending_key = None
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if not line or line.lstrip().startswith("#"):
            continue
        # Top-level key: value
        if not raw_line.startswith(" ") and ":" in line:
            k, _, v = line.partition(":")
            k = k.strip()
            v = v.strip()
            if v == "":
                # Could be the start of a list or nested map.
                pending_key = k
                cur_list = []
                cur_list_item = None
                data[k] = cur_list
                continue
            data[k] = _coerce_scalar(v)
            pending_key = None
            cur_list = None
            cur_list_item = None
            continue
        # 2-space indented list item start: "  - key: value"
        if raw_line.startswith("  - ") and pending_key is not None:
            cur_list_item = {}
            cur_list.append(cur_list_item)
            inner = raw_line[4:]
            if ":" in inner:
                k, _, v = inner.partition(":")
                cur_list_item[k.strip()] = _coerce_scalar(v.strip())
            continue
        # 4-space indented continuation key inside list item: "    key: value"
        if raw_line.startswith("    ") and cur_list_item is not None and ":" in line:
            inner = line.strip()
            k, _, v = inner.partition(":")
            cur_list_item[k.strip()] = _coerce_scalar(v.strip())
            continue
        # Anything else — give up, return what we have.
    return data


def _coerce_scalar(s: str):
    s = s.strip()
    if (s.startswith("'") and s.endswith("'")) or (s.startswith('"') and s.endswith('"')):
        return s[1:-1]
    return s


# Try JSON first; fall back to minimal YAML (Electron-builder shape).
try:
    data = json.loads(body)
except Exception:
    try:
        data = _parse_minimal_yaml(body)
        if not data:
            sys.exit(27)
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

release_feed_check() {
    local slug="$1" cfg="$2"

    local url version_path arch_path expected_arch timeout
    url=$(printf '%s' "$cfg" | _rf_get "release_feed.url")
    version_path=$(printf '%s' "$cfg" | _rf_get "release_feed.version_path")
    arch_path=$(printf '%s' "$cfg" | _rf_get "release_feed.arch_path")
    expected_arch=$(printf '%s' "$cfg" | _rf_get "release_feed.expected_arch")
    timeout=$(printf '%s' "$cfg" | _rf_get "release_feed.http_timeout_s")
    [ -z "$timeout" ] && timeout=8

    if [ -z "$url" ] || [ -z "$version_path" ]; then
        echo ""
        return 22
    fi

    local body http_rc
    body=$(/usr/bin/curl -fsSL --max-time "$timeout" "$url" 2>/dev/null)
    http_rc=$?
    if [ "$http_rc" -ne 0 ] || [ -z "$body" ]; then
        echo ""
        return 25
    fi

    # Cap response at 256 KiB (T3 mitigation)
    body=$(printf '%s' "$body" | /usr/bin/head -c 262144)

    # Optional arch sanity check
    if [ -n "$arch_path" ] && [ -n "$expected_arch" ]; then
        local actual_arch
        actual_arch=$(_rf_walk_json "$body" "$arch_path")
        local arch_rc=$?
        if [ "$arch_rc" -ne 0 ]; then
            echo ""
            return "$arch_rc"
        fi
        if [ "$actual_arch" != "$expected_arch" ]; then
            echo ""
            return 29
        fi
    fi

    local version
    version=$(_rf_walk_json "$body" "$version_path")
    local walk_rc=$?
    if [ "$walk_rc" -ne 0 ]; then
        echo ""
        return "$walk_rc"
    fi
    if [ -z "$version" ]; then
        echo ""
        return 28
    fi

    echo "$version"
    return 0
}

release_feed_apply() {
    local slug="$1" cfg="$2"

    local url download_path timeout
    url=$(printf '%s' "$cfg" | _rf_get "release_feed.url")
    download_path=$(printf '%s' "$cfg" | _rf_get "release_feed.download_path")
    timeout=$(printf '%s' "$cfg" | _rf_get "release_feed.http_timeout_s")
    [ -z "$timeout" ] && timeout=8

    if [ -z "$download_path" ]; then
        # No download_path configured — open the app so it can self-update
        local app_path display_name
        display_name=$(printf '%s' "$cfg" | _rf_get "display_name")
        app_path=$(printf '%s' "$cfg" | _rf_get "app_path")
        [ -z "$app_path" ] && app_path="/Applications/${display_name}.app"
        /usr/bin/env open -a "$app_path"
        return 0
    fi

    local body
    body=$(/usr/bin/curl -fsSL --max-time "$timeout" "$url" 2>/dev/null) || return 25

    local dmg_url
    dmg_url=$(_rf_walk_json "$body" "$download_path") || return 28
    [ -z "$dmg_url" ] && return 28

    # Electron-builder yml gives relative URLs (e.g. "Notion-7.16.0.dmg").
    # Resolve against the feed URL's parent directory.
    # Some vendors (Notion Calendar, Cursor via ToDesktop) ship filenames
    # with literal spaces — URL-encode the path component so curl accepts it.
    dmg_url=$(BASE="$url" REL="$dmg_url" python3 -c '
import os, sys
from urllib.parse import urlparse, urlunparse, urljoin, quote
base = os.environ["BASE"]
rel = os.environ["REL"]
# urljoin handles both absolute (returns rel as-is) and relative inputs.
joined = urljoin(base, rel)
u = urlparse(joined)
# Re-encode the path so spaces / non-ASCII become %-encoded. Preserve
# already-encoded sequences via safe="/%" so we do not double-encode.
safe_path = quote(u.path, safe="/%")
print(urlunparse((u.scheme, u.netloc, safe_path, u.params, u.query, u.fragment)))
') || return 32
    case "$dmg_url" in
        https://*) ;;
        http://*) return 32 ;;
        *) return 32 ;;
    esac

    # Delegate to the shared DMG installer helper (from ascendo_web.sh,
    # loaded by check.sh / apply.sh before sourcing handlers).
    _web_install_dmg "$slug" "$dmg_url"
}
