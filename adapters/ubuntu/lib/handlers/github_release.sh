# adapters/ubuntu/lib/handlers/github_release.sh
# GitHub Releases API + Linux asset handler.
#
# Functions:
#   github_release_check <app_id> <config_json>  -> echoes latest version
#   github_release_apply <app_id> <config_json>  -> downloads + installs
#
# config_json keys (under github_release sub-table):
#   repo:           "owner/repo"
#   asset_pattern:  Python regex on asset name
#   prerelease:     bool (default false)
#   strip_v_prefix: bool (default true)
#
# ASCENDO_WEB_GH_RELEASE_OVERRIDE: test hook — points to a JSON file used
#                                  in place of the live GitHub API call.

_gh_get() {
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
        sub = data.get("github_release") or {}
        v = sub.get(key) if isinstance(sub, dict) else None
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

_gh_fetch_release() {
    local repo="$1" prerelease="$2" timeout="$3"
    if [ -n "${ASCENDO_WEB_GH_RELEASE_OVERRIDE:-}" ]; then
        cat "$ASCENDO_WEB_GH_RELEASE_OVERRIDE"
        return 0
    fi
    local url
    if [ "$prerelease" = "true" ]; then
        url="https://api.github.com/repos/${repo}/releases?per_page=5"
    else
        url="https://api.github.com/repos/${repo}/releases/latest"
    fi
    local hdr=()
    if [ -n "${GITHUB_TOKEN:-}" ]; then
        case "$GITHUB_TOKEN" in
            ghp_*|github_pat_*|ghs_*)
                hdr=(-H "Authorization: Bearer $GITHUB_TOKEN") ;;
        esac
    fi
    curl -fsSL --max-time "$timeout" \
        -H "Accept: application/vnd.github+json" \
        "${hdr[@]}" \
        "$url" 2>/dev/null
}

github_release_check() {
    local app_id="$1" cfg="$2"
    local repo asset_pattern prerelease strip_v timeout
    repo=$(printf '%s' "$cfg" | _gh_get "github_release.repo")
    asset_pattern=$(printf '%s' "$cfg" | _gh_get "github_release.asset_pattern")
    prerelease=$(printf '%s' "$cfg" | _gh_get "github_release.prerelease")
    strip_v=$(printf '%s' "$cfg" | _gh_get "github_release.strip_v_prefix")
    timeout=$(printf '%s' "$cfg" | _gh_get "github_release.http_timeout_s")
    [ -z "$timeout" ] && timeout=8
    [ -z "$prerelease" ] && prerelease="false"
    [ -z "$strip_v" ] && strip_v="true"

    [ -z "$repo" ] && { echo ""; return 22; }

    local body
    body=$(_gh_fetch_release "$repo" "$prerelease" "$timeout")
    if [ -z "$body" ]; then
        # Detect rate-limit by re-running with -i
        echo "__GH_RATE_LIMITED__"
        return 25
    fi

    # If the response is a list (releases?per_page=5), take the first
    # non-prerelease (or first if prerelease=true).
    local tag
    tag=$(BODY="$body" PRERELEASE="$prerelease" python3 -c '
import json, os, sys
body = os.environ["BODY"]
prerelease = os.environ["PRERELEASE"] == "true"
try:
    data = json.loads(body)
except Exception:
    sys.exit(28)
if isinstance(data, dict):
    if data.get("message", "").startswith("API rate limit"):
        print("__GH_RATE_LIMITED__"); sys.exit(0)
    print(data.get("tag_name", "")); sys.exit(0)
if isinstance(data, list):
    for r in data:
        if not isinstance(r, dict): continue
        if not prerelease and r.get("prerelease"): continue
        if r.get("draft"): continue
        print(r.get("tag_name", "")); sys.exit(0)
sys.exit(28)
')
    [ -z "$tag" ] && { echo ""; return 28; }
    if [ "$tag" = "__GH_RATE_LIMITED__" ]; then
        echo "__GH_RATE_LIMITED__"; return 0
    fi
    if [ "$strip_v" = "true" ]; then
        tag=$(printf '%s' "$tag" | sed -E 's/^v//')
    fi
    echo "$tag"
    return 0
}

github_release_apply() {
    local app_id="$1" cfg="$2"
    local repo asset_pattern prerelease timeout
    repo=$(printf '%s' "$cfg" | _gh_get "github_release.repo")
    asset_pattern=$(printf '%s' "$cfg" | _gh_get "github_release.asset_pattern")
    prerelease=$(printf '%s' "$cfg" | _gh_get "github_release.prerelease")
    timeout=$(printf '%s' "$cfg" | _gh_get "github_release.http_timeout_s")
    [ -z "$timeout" ] && timeout=8
    [ -z "$prerelease" ] && prerelease="false"

    [ -z "$repo" ] && return 22
    [ -z "$asset_pattern" ] && return 22

    local body
    body=$(_gh_fetch_release "$repo" "$prerelease" "$timeout")
    [ -z "$body" ] && return 25

    local dl_url
    dl_url=$(BODY="$body" PAT="$asset_pattern" PRERELEASE="$prerelease" python3 -c '
import json, os, re, sys
body = os.environ["BODY"]; pat = os.environ["PAT"]
prerelease = os.environ["PRERELEASE"] == "true"
try:
    data = json.loads(body)
except Exception:
    sys.exit(28)
try:
    rx = re.compile(pat)
except re.error:
    sys.exit(28)
def pick(release):
    for a in release.get("assets") or []:
        if not isinstance(a, dict): continue
        if rx.search(a.get("name", "")):
            return a.get("browser_download_url", "")
    return ""
if isinstance(data, dict):
    u = pick(data)
    if u: print(u); sys.exit(0)
    sys.exit(28)
if isinstance(data, list):
    for r in data:
        if not isinstance(r, dict): continue
        if not prerelease and r.get("prerelease"): continue
        if r.get("draft"): continue
        u = pick(r)
        if u: print(u); sys.exit(0)
sys.exit(28)
')
    [ -z "$dl_url" ] && return 28
    case "$dl_url" in
        https://*) ;;
        *) return 32 ;;
    esac
    _web_install_artifact "$app_id" "$dl_url"
}
