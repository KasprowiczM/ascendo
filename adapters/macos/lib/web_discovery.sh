#!/usr/bin/env bash
# adapters/macos/lib/web_discovery.sh
#
# Walks $ASCENDO_WEB_APPS_ROOT (default /Applications), reads each .app's
# Info.plist, and emits a JSON line per bundle to stdout.
#
# Each line:
#   {
#     "bundle_id": "com.example.foo",
#     "app_path": "/Applications/Foo.app",
#     "version": "1.2.3",
#     "display_name": "Foo",
#     "fingerprint_handler": "sparkle|keystone|squirrel|builtin",
#     "fingerprint_source": "SUFeedURL|KSProductID|Squirrel.framework|ShipIt|none",
#     "owned_by": "brew|mas|softwareupdate|null"
#   }
#
# Apps owned by other managers (brew/mas/softwareupdate) are excluded
# unless ASCENDO_WEB_INCLUDE_OWNED=1 (test/debug only).
#
# Ownership inputs (comma-separated, no spaces):
#   ASCENDO_WEB_BREW_CASKS       -- bundle IDs from `brew list --cask`
#   ASCENDO_WEB_MAS_BUNDLE_IDS   -- bundle IDs from `mas list`
#   ASCENDO_WEB_APPLE_BUNDLES    -- bundle IDs signed by Apple
#
# When the inputs aren't set externally, the script auto-populates from
# the actual brew tool. mas + Apple ownership lists default to empty
# (computing them is expensive; com.apple.* prefix is checked inline).
# Tests override via the env vars.
set -o pipefail

APPS_ROOT="${ASCENDO_WEB_APPS_ROOT:-/Applications}"
INCLUDE_OWNED="${ASCENDO_WEB_INCLUDE_OWNED:-0}"

usage() {
    printf 'usage: web_discovery.sh --emit-json\n' >&2
    exit 2
}

case "${1:-}" in
    --emit-json) ;;
    *) usage ;;
esac

# -- Ownership signals -------------------------------------------------------

if [ -z "${ASCENDO_WEB_BREW_CASKS+x}" ]; then
    if command -v brew >/dev/null 2>&1; then
        _brew_bids=$(brew list --cask 2>/dev/null | while IFS= read -r token; do
            [ -z "$token" ] && continue
            brew info --cask --json=v2 "$token" 2>/dev/null | python3 -c '
import json, sys
try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(0)
for cask in data.get("casks", []):
    for art in cask.get("artifacts", []):
        if isinstance(art, dict) and "uninstall" in art:
            for u in art["uninstall"]:
                for bid in (u.get("quit") or []):
                    print(bid)
                for bid in (u.get("pkgutil") or []):
                    print(bid)
'
        done | sort -u | tr '\n' ',')
        ASCENDO_WEB_BREW_CASKS="${_brew_bids%,}"
    else
        ASCENDO_WEB_BREW_CASKS=""
    fi
fi

if [ -z "${ASCENDO_WEB_MAS_BUNDLE_IDS+x}" ]; then
    ASCENDO_WEB_MAS_BUNDLE_IDS=""
fi

if [ -z "${ASCENDO_WEB_APPLE_BUNDLES+x}" ]; then
    ASCENDO_WEB_APPLE_BUNDLES=""
fi

# _owned_by <bundle_id>
# Echoes "brew", "mas", "softwareupdate", or "" (unowned).
_owned_by() {
    local bid="$1"
    case ",${ASCENDO_WEB_BREW_CASKS:-}," in (*",$bid,"*) printf 'brew'; return ;; esac
    case ",${ASCENDO_WEB_MAS_BUNDLE_IDS:-}," in (*",$bid,"*) printf 'mas'; return ;; esac
    case ",${ASCENDO_WEB_APPLE_BUNDLES:-}," in (*",$bid,"*) printf 'softwareupdate'; return ;; esac
    case "$bid" in com.apple.*) printf 'softwareupdate'; return ;; esac
    printf ''
}

# -- Classifier --------------------------------------------------------------

# _classify <app_path>
# Echoes "<handler>\t<source>"
_classify() {
    local app="$1"
    local plist="$app/Contents/Info.plist"

    local sufeed kspid
    sufeed=$(/usr/libexec/PlistBuddy -c "Print :SUFeedURL" "$plist" 2>/dev/null || true)
    kspid=$(/usr/libexec/PlistBuddy -c "Print :KSProductID" "$plist" 2>/dev/null || true)

    if [ -n "$sufeed" ]; then
        printf 'sparkle\tSUFeedURL\n'
        return 0
    fi
    if [ -n "$kspid" ]; then
        printf 'keystone\tKSProductID\n'
        return 0
    fi
    if [ -d "$app/Contents/Frameworks/Squirrel.framework" ]; then
        printf 'squirrel\tSquirrel.framework\n'
        return 0
    fi
    if find "$app/Contents/Frameworks" -maxdepth 4 -name "ShipIt" 2>/dev/null | grep -q .; then
        printf 'squirrel\tShipIt\n'
        return 0
    fi
    printf 'builtin\tnone\n'
}

# -- Walk --------------------------------------------------------------------

cd "$APPS_ROOT" 2>/dev/null || exit 0

for app_dir in *.app; do
    [ -d "$app_dir" ] || continue
    plist="$app_dir/Contents/Info.plist"
    [ -f "$plist" ] || continue

    bid=$(/usr/libexec/PlistBuddy -c "Print :CFBundleIdentifier" "$plist" 2>/dev/null || true)
    [ -z "$bid" ] && continue
    ver=$(/usr/libexec/PlistBuddy -c "Print :CFBundleShortVersionString" "$plist" 2>/dev/null || true)
    name=$(/usr/libexec/PlistBuddy -c "Print :CFBundleName" "$plist" 2>/dev/null || true)
    [ -z "$name" ] && name="${app_dir%.app}"

    owned=$(_owned_by "$bid")
    if [ -n "$owned" ] && [ "$INCLUDE_OWNED" != "1" ]; then
        continue
    fi

    abs_path="$APPS_ROOT/$app_dir"
    cls=$(_classify "$abs_path")
    handler="${cls%%	*}"
    source_field="${cls##*	}"

    python3 - "$bid" "$abs_path" "$ver" "$name" "$handler" "$source_field" "$owned" <<'PY'
import json, sys, signal
signal.signal(signal.SIGPIPE, signal.SIG_DFL)
bid, path, ver, name, handler, src, owned = sys.argv[1:8]
out = {
    "bundle_id": bid,
    "app_path": path,
    "version": ver or "",
    "display_name": name,
    "fingerprint_handler": handler,
    "fingerprint_source": src,
    "owned_by": owned or None,
}
try:
    print(json.dumps(out, separators=(",", ":")))
except BrokenPipeError:
    pass
PY
done
exit 0
