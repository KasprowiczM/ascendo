# adapters/macos/lib/handlers/github_dmg.sh
# GitHub Releases + DMG handler.
#
# Functions:
#   github_dmg_check <slug> <config_json>   -> echoes latest version or empty
#   github_dmg_apply <slug> <config_json>   -> exit 0 on success, non-0 on failure
#
# Depends on lib/ascendo_web.sh helpers:
#   _web_install_dmg
#
# config_json keys:
#   github_repo:    "owner/repo"     (required)
#   asset_pattern:  Python regex     (required) — matched against asset name
#   prerelease:     bool             (optional, default false)
#   app_path:       /Applications/X  (optional, override target install path)
#
# ASCENDO_WEB_GH_RELEASE_OVERRIDE: test hook — points to a JSON file used
#                                  in place of the live GH API call.
#
# Wire-shape tolerance for config_json mirrors sparkle.sh — we accept raw
# JSON, JSON-encoded JSON, and shell-doubled-escape forms. The local
# helper _gh_get is a near-clone of sparkle's _sparkle_get; consolidating
# both into ascendo_web.sh is deferred to milestone-final review.

# _gh_get <key>
#
# Reads JSON config from stdin; echoes the value of <key> (empty when
# absent or the input cannot be coerced into a dict). Tolerates raw
# JSON, JSON-encoded JSON, and shell-doubled-escape wire shapes.
#
# We pass the JSON via the ASCENDO_WEB_CFG environment variable and put
# the python source on stdin (heredoc) — that side-steps every
# bash/python escape-sequence interaction.
_gh_get() {
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

# _github_dmg_fetch_release <repo> <prerelease_flag>
#
# Echoes raw GitHub release JSON to stdout. When ASCENDO_WEB_GH_RELEASE_OVERRIDE
# is set we cat that file (test hook). Otherwise we hit the live GH API:
# /releases/latest for stable, /releases?per_page=5 when prereleases are
# allowed (so the picker can scan recent tags).
_github_dmg_fetch_release() {
    local repo="$1" prerelease="$2"
    if [ -n "${ASCENDO_WEB_GH_RELEASE_OVERRIDE:-}" ]; then
        /bin/cat "$ASCENDO_WEB_GH_RELEASE_OVERRIDE"
        return 0
    fi
    local url
    if [ "$prerelease" = "true" ]; then
        url="https://api.github.com/repos/${repo}/releases?per_page=5"
    else
        url="https://api.github.com/repos/${repo}/releases/latest"
    fi
    local hdr=()
    [ -n "${GITHUB_TOKEN:-}" ] && hdr=(-H "Authorization: token $GITHUB_TOKEN")
    /usr/bin/curl -fsSL --max-time 15 \
        -H "Accept: application/vnd.github+json" \
        "${hdr[@]}" "$url"
}

# _github_dmg_pick_release <prerelease_flag>
#
# Reads release JSON from stdin; echoes a single release object honouring
# the prerelease flag. Accepts both single-object (releases/latest) and
# array (releases?per_page=N) shapes. Output is empty when nothing
# qualifies (e.g. the only candidate is a prerelease and prerelease=false).
#
# The pipeline cannot use `python3 - <<'EOF'` because the heredoc binds
# stdin to the script source, dropping the upstream pipe. Pass the JSON
# via env var instead — the heredoc stays the python source.
_github_dmg_pick_release() {
    local prerelease="$1"
    local payload
    payload="$(cat)"
    ASCENDO_WEB_REL="$payload" ASCENDO_WEB_PRE="$prerelease" \
        /usr/bin/python3 <<'PY_EOF'
import json
import os
import sys

raw = os.environ.get("ASCENDO_WEB_REL", "")
allow_pre = os.environ.get("ASCENDO_WEB_PRE", "") == "true"
if not raw.strip():
    sys.exit(0)
try:
    data = json.loads(raw)
except Exception:
    sys.exit(0)
if isinstance(data, list):
    for rel in data:
        if rel.get("prerelease") and not allow_pre:
            continue
        print(json.dumps(rel))
        break
else:
    if data.get("prerelease") and not allow_pre:
        sys.exit(0)
    print(json.dumps(data))
PY_EOF
}

# github_dmg_check <slug> <config_json>
#
# Echoes the latest release version (tag_name with leading 'v' stripped
# when followed by a digit) or empty when no asset matches the pattern,
# the only candidate is a prerelease in stable-only mode, or the API call
# fails.
github_dmg_check() {
    local slug="$1" cfg="$2"
    local repo asset_pat prerelease
    repo=$(/usr/bin/printf '%s' "$cfg" | _gh_get github_repo)
    asset_pat=$(/usr/bin/printf '%s' "$cfg" | _gh_get asset_pattern)
    prerelease=$(/usr/bin/printf '%s' "$cfg" | _gh_get prerelease)
    [ "$prerelease" = "true" ] || prerelease="false"
    [ -z "$repo" ] && return 0

    local rel
    rel=$(_github_dmg_fetch_release "$repo" "$prerelease" 2>/dev/null \
        | _github_dmg_pick_release "$prerelease")
    [ -z "$rel" ] && return 0

    ASCENDO_WEB_REL="$rel" ASCENDO_WEB_PAT="$asset_pat" \
        /usr/bin/python3 <<'PY_EOF'
import json
import os
import re
import sys

raw = os.environ.get("ASCENDO_WEB_REL", "")
pat = os.environ.get("ASCENDO_WEB_PAT", "")
try:
    data = json.loads(raw)
except Exception:
    sys.exit(0)
matched = False
for asset in data.get("assets", []):
    if re.search(pat, asset.get("name", "")):
        matched = True
        break
if not matched:
    sys.exit(0)
tag = data.get("tag_name", "")
if tag.startswith("v") and len(tag) > 1 and tag[1].isdigit():
    tag = tag[1:]
print(tag)
PY_EOF
}

# github_dmg_apply <slug> <config_json>
#
# Resolves the matching arm64 asset in the latest (or latest-stable)
# release and hands its browser_download_url to _web_install_dmg.
# Exit codes:
#   0  — success
#   25 — github_repo missing in config
#   26 — no qualifying release found
#   27 — asset pattern matched nothing in the chosen release
github_dmg_apply() {
    local slug="$1" cfg="$2"
    local repo asset_pat prerelease app_path
    repo=$(/usr/bin/printf '%s' "$cfg" | _gh_get github_repo)
    asset_pat=$(/usr/bin/printf '%s' "$cfg" | _gh_get asset_pattern)
    prerelease=$(/usr/bin/printf '%s' "$cfg" | _gh_get prerelease)
    [ "$prerelease" = "true" ] || prerelease="false"
    app_path=$(/usr/bin/printf '%s' "$cfg" | _gh_get app_path)

    [ -z "$repo" ] && return 25

    local rel asset_url
    rel=$(_github_dmg_fetch_release "$repo" "$prerelease" 2>/dev/null \
        | _github_dmg_pick_release "$prerelease")
    [ -z "$rel" ] && return 26

    asset_url=$(ASCENDO_WEB_REL="$rel" ASCENDO_WEB_PAT="$asset_pat" \
        /usr/bin/python3 <<'PY_EOF'
import json
import os
import re
import sys

raw = os.environ.get("ASCENDO_WEB_REL", "")
pat = os.environ.get("ASCENDO_WEB_PAT", "")
try:
    data = json.loads(raw)
except Exception:
    sys.exit(0)
for asset in data.get("assets", []):
    if re.search(pat, asset.get("name", "")):
        print(asset.get("browser_download_url", ""))
        break
PY_EOF
    )
    [ -z "$asset_url" ] && return 27

    _web_install_dmg "$slug" "$asset_url" "$app_path"
}
