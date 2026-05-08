# adapters/macos/lib/handlers/omaha.sh
# Google Omaha update protocol handler (POST + XML or JSON body).
#
# Functions:
#   omaha_check <slug> <config_json>  -> echoes candidate version
#                                          or empty + non-zero rc
#   omaha_apply <slug> <config_json>  -> defers to keystone_apply
#                                          (Tier-B trigger semantics)
#
# Configuration lives under the [app.omaha] sub-table:
#   endpoint         (HTTPS URL)  Required. Vendor's Omaha service URL.
#                                 - Google products: https://update.googleapis.com/service/update2
#                                 - Comet (Perplexity): https://www.perplexity.ai/rest/browser/update2
#   appid            (string)     Required. Vendor-assigned application id.
#                                 Examples: "com.google.drivefs",
#                                 "com.google.geminimacos", "ai.perplexity.comet".
#   protocol         (string)     Optional. "3.0" (default; XML)
#                                 or "4.0" (JSON; used by Comet).
#   tag              (string)     Optional. Channel tag for Google's
#                                 Omaha service (e.g. "m1-prod" for
#                                 Gemini, "stable" for Chrome).
#   brand            (string)     Optional. 4-char brand code
#                                 (e.g. "GGLG"). Defaults to empty.
#   http_timeout_s   (int)        Optional. Default 8.
#
# The handler probes the candidate version by sending a fresh-install
# request (version="0.0.0.0") so the response carries the latest
# manifest version. Apply is intentionally Tier-B — daemons (Keystone,
# CometUpdater) own the actual install path; we only surface the
# candidate version to the operator.

# _omaha_get <key>  — same heredoc-via-env pattern as keystone/sparkle.
# Walks dotted keys into the omaha sub-table.
_omaha_get() {
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
    # 3. Shell-doubled escapes from naive f-string + repr quoting (test harness):
    #    "{\\"slug\\": \\"x\\"}"  -> strip outer quotes, collapse \\" -> ".
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
        sub = data.get("omaha") or {}
        v = sub.get(key) if isinstance(sub, dict) else None

if v is None or v is False:
    print("")
elif isinstance(v, (list, dict)):
    print(json.dumps(v))
else:
    print(v)
PY_EOF
}

# _omaha_probe <endpoint> <appid> <protocol> <tag> <brand> <timeout>
#
# Builds a fresh-install update request (version="0.0.0.0") and parses
# the candidate version out of the response.
#
# Exit codes:
#   0  — success, version on stdout
#   25 — network failure (curl non-zero)
#   27 — body unparseable
#   28 — response carried "noupdate" or no version field
#   29 — appid unknown / status=error
_omaha_probe() {
    local endpoint="$1" appid="$2" protocol="$3" tag="$4" brand="$5" timeout="$6"

    ASCENDO_OMAHA_ENDPOINT="$endpoint" \
    ASCENDO_OMAHA_APPID="$appid" \
    ASCENDO_OMAHA_PROTOCOL="$protocol" \
    ASCENDO_OMAHA_TAG="$tag" \
    ASCENDO_OMAHA_BRAND="$brand" \
    ASCENDO_OMAHA_TIMEOUT="$timeout" \
    /usr/bin/python3 <<'PY_EOF'
import json
import os
import sys
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET

endpoint = os.environ["ASCENDO_OMAHA_ENDPOINT"]
appid = os.environ["ASCENDO_OMAHA_APPID"]
protocol = os.environ.get("ASCENDO_OMAHA_PROTOCOL") or "3.0"
tag = os.environ.get("ASCENDO_OMAHA_TAG") or ""
brand = os.environ.get("ASCENDO_OMAHA_BRAND") or ""
try:
    timeout = float(os.environ.get("ASCENDO_OMAHA_TIMEOUT") or "8")
except ValueError:
    timeout = 8.0


def _xml_escape(s: str) -> str:
    return (s.replace("&", "&amp;")
             .replace("<", "&lt;")
             .replace(">", "&gt;")
             .replace('"', "&quot;")
             .replace("'", "&apos;"))


def _build_v3_xml() -> bytes:
    # Fresh-install probe: version=0.0.0.0 so the server returns the
    # latest available manifest. Static, deterministic UUIDs so a
    # vendor analytics pipeline can dedupe / rate-limit our probes.
    tag_attr = f' tag="{_xml_escape(tag)}"' if tag else ""
    brand_attr = f' brand="{_xml_escape(brand)}"' if brand else ' brand=""'
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<request protocol="3.0" updater="Ascendo" updaterversion="0.4.3" '
        'ismachine="0" '
        'sessionid="{ascendo-omaha-probe-session-0001}" '
        'installsource="ondemand" '
        'requestid="{ascendo-omaha-probe-request-0001}">'
        '<os platform="mac" version="14.0" arch="arm64"/>'
        f'<app appid="{_xml_escape(appid)}" version="0.0.0.0" '
        f'lang="en-US"{brand_attr} client=""{tag_attr}>'
        '<updatecheck/>'
        '</app>'
        '</request>'
    )
    return body.encode("utf-8")


def _build_v4_json() -> bytes:
    body = {
        "request": {
            "protocol": "4.0",
            "updater": "Ascendo",
            "updaterversion": "0.4.3",
            "ismachine": False,
            "sessionid": "{ascendo-omaha-probe-session-0001}",
            "requestid": "{ascendo-omaha-probe-request-0001}",
            "os": {"platform": "mac", "arch": "arm64", "version": "14.0"},
            "apps": [
                {
                    "appid": appid,
                    "version": "0.0.0.0",
                    "lang": "en-US",
                    "updatecheck": {},
                }
            ],
        }
    }
    if tag:
        body["request"]["apps"][0]["tag"] = tag
    if brand:
        body["request"]["apps"][0]["brand"] = brand
    return json.dumps(body).encode("utf-8")


if protocol == "4.0":
    req_body = _build_v4_json()
    content_type = "application/json"
else:
    # 3.0 (default — Google's Omaha service)
    req_body = _build_v3_xml()
    content_type = "text/xml"

req = urllib.request.Request(
    endpoint,
    data=req_body,
    headers={
        "Content-Type": content_type,
        "User-Agent": "Ascendo/0.4.3 (Omaha probe)",
    },
    method="POST",
)
try:
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        # 2 MiB cap (T3 mitigation — same as release_feed)
        raw = resp.read(2 * 1024 * 1024)
except (urllib.error.URLError, TimeoutError, ConnectionError):
    sys.exit(25)
except Exception:
    sys.exit(25)

if not raw:
    sys.exit(27)


def _parse_v3_xml(body: bytes) -> str:
    """Walk Omaha 3.0 XML response. Return version or '' on noupdate.
    Raises ET.ParseError on bad XML.
    """
    root = ET.fromstring(body)
    # response > app > updatecheck
    for app in root.findall("app"):
        if app.get("appid", "").lower() != appid.lower():
            continue
        status = app.get("status", "")
        if status and status != "ok":
            return ""  # status=error-unknownApplication etc.
        uc = app.find("updatecheck")
        if uc is None:
            return ""
        uc_status = uc.get("status", "")
        if uc_status == "noupdate":
            return ""
        # status=ok: version sits at manifest/@version
        manifest = uc.find("manifest")
        if manifest is not None:
            v = manifest.get("version", "")
            if v:
                return v
        # Fallback: some Omaha 3.1 responses use updatecheck/@version
        v = uc.get("version", "")
        return v or ""
    return ""


def _parse_v4_json(body: bytes) -> str:
    """Walk Omaha 4.0 JSON response."""
    text = body.decode("utf-8", errors="replace")
    # Chromium ships a CSRF-safety prefix; strip it.
    if text.startswith(")]}'\n"):
        text = text[5:]
    elif text.startswith(")]}'"):
        text = text[4:].lstrip()
    data = json.loads(text)
    resp = data.get("response", {})
    apps = resp.get("apps", [])
    for app in apps:
        if app.get("appid", "").lower() != appid.lower():
            continue
        status = app.get("status", "")
        if status and status != "ok":
            return ""
        uc = app.get("updatecheck", {})
        uc_status = uc.get("status", "")
        if uc_status == "noupdate":
            return ""
        # Omaha 4.0 emits the candidate as updatecheck.nextversion
        v = uc.get("nextversion") or ""
        if v:
            return v
        # Some 4.0 responses might still use manifest.version
        manifest = uc.get("manifest", {})
        if isinstance(manifest, dict):
            v = manifest.get("version", "")
            if v:
                return v
    return ""


try:
    if protocol == "4.0":
        version = _parse_v4_json(raw)
    else:
        version = _parse_v3_xml(raw)
except (ET.ParseError, json.JSONDecodeError, UnicodeDecodeError):
    sys.exit(27)
except Exception:
    sys.exit(27)

if not version:
    sys.exit(28)

print(version)
sys.exit(0)
PY_EOF
}

omaha_check() {
    local slug="$1" cfg="$2"

    local endpoint appid protocol tag brand timeout
    endpoint=$(printf '%s' "$cfg" | _omaha_get "omaha.endpoint")
    appid=$(printf '%s' "$cfg" | _omaha_get "omaha.appid")
    protocol=$(printf '%s' "$cfg" | _omaha_get "omaha.protocol")
    tag=$(printf '%s' "$cfg" | _omaha_get "omaha.tag")
    brand=$(printf '%s' "$cfg" | _omaha_get "omaha.brand")
    timeout=$(printf '%s' "$cfg" | _omaha_get "omaha.http_timeout_s")
    [ -z "$timeout" ] && timeout=8
    [ -z "$protocol" ] && protocol="3.0"

    if [ -z "$endpoint" ] || [ -z "$appid" ]; then
        echo ""
        return 22
    fi

    local version rc
    version=$(_omaha_probe "$endpoint" "$appid" "$protocol" "$tag" "$brand" "$timeout")
    rc=$?
    if [ "$rc" -ne 0 ]; then
        echo ""
        return "$rc"
    fi
    echo "$version"
    return 0
}

# omaha_apply — Tier-B trigger semantics. The vendor's update agent
# (Keystone for Google products, CometUpdater for Comet) owns the
# actual install. If the registry entry also carries
# ksadmin_product_id we delegate to keystone_apply; otherwise we
# open the app so its in-process updater can run on next launch.
omaha_apply() {
    local slug="$1" cfg="$2"

    local product_id app_path display_name
    product_id=$(printf '%s' "$cfg" | _omaha_get ksadmin_product_id)

    if [ -n "$product_id" ]; then
        # Delegate to keystone_apply (sourced alongside us by
        # check.sh / apply.sh — the loader sources every handler).
        if command -v keystone_apply >/dev/null 2>&1; then
            keystone_apply "$slug" "$cfg"
            return $?
        fi
    fi

    display_name=$(printf '%s' "$cfg" | _omaha_get display_name)
    app_path=$(printf '%s' "$cfg" | _omaha_get app_path)
    [ -z "$app_path" ] && app_path="/Applications/${display_name}.app"
    /usr/bin/env open -a "$app_path" 2>/dev/null
    return 0
}
