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
        # Single batched `brew info --cask --json=v2 <all installed casks>`
        # call (was: one call per cask — ~10s on a typical Mac with 30
        # casks). Bash 3.2 compatible.
        _brew_tokens=$(brew list --cask 2>/dev/null | tr '\n' ' ')
        if [ -n "$_brew_tokens" ]; then
            # shellcheck disable=SC2086 — intentional word-split on tokens
            _brew_bids=$(brew info --cask --json=v2 $_brew_tokens 2>/dev/null | python3 -c '
import json, sys
try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(0)
out = set()
for cask in data.get("casks", []):
    for art in cask.get("artifacts", []):
        if isinstance(art, dict) and "uninstall" in art:
            for u in art["uninstall"]:
                for bid in (u.get("quit") or []):
                    out.add(bid)
                for bid in (u.get("pkgutil") or []):
                    out.add(bid)
print(",".join(sorted(out)))
')
            ASCENDO_WEB_BREW_CASKS="$_brew_bids"
        else
            ASCENDO_WEB_BREW_CASKS=""
        fi
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
# Echoes "brew", "mas", "softwareupdate", "ineligible", or "" (unowned).
#
# "ineligible" covers things that look like .app bundles but aren't real
# user-updatable apps:
#   - Chrome / Google Drive web shortcuts (com.google.drivefs.shortcuts.*,
#     com.google.Chrome.app.*) — they just open a URL in the browser
#   - MDM-managed shims (com.microsoft.wdav.shim) — IT pushes updates
#   - Setapp wrappers, Wine prefixes, Crossover bottle apps, etc.
# These never have a real candidate version to probe.
_owned_by() {
    local bid="$1" app_dir="${2:-}"
    case ",${ASCENDO_WEB_BREW_CASKS:-}," in (*",$bid,"*) printf 'brew'; return ;; esac
    case ",${ASCENDO_WEB_MAS_BUNDLE_IDS:-}," in (*",$bid,"*) printf 'mas'; return ;; esac
    case ",${ASCENDO_WEB_APPLE_BUNDLES:-}," in (*",$bid,"*) printf 'softwareupdate'; return ;; esac
    case "$bid" in com.apple.*) printf 'softwareupdate'; return ;; esac
    # Ineligible-bundle pattern check. Each pattern is a glob against the
    # full bundle id. Add new patterns to ASCENDO_WEB_INELIGIBLE_PATTERNS
    # (comma-separated) to extend without code change.
    case "$bid" in
        com.google.drivefs.shortcuts.*) printf 'ineligible'; return ;;
        com.google.Chrome.app.*)        printf 'ineligible'; return ;;
        com.microsoft.wdav.shim)        printf 'ineligible'; return ;;
        com.microsoft.wdav.*.shim)      printf 'ineligible'; return ;;
        # Ascendo updates via `git pull` against KasprowiczM/ascendo, not
        # via the web app updater. Filter the bundle out so it never
        # surfaces in the web inventory.
        dev.ascendo.*)                  printf 'ineligible'; return ;;
    esac
    if [ -n "${ASCENDO_WEB_INELIGIBLE_PATTERNS:-}" ]; then
        local IFS=','
        for pat in $ASCENDO_WEB_INELIGIBLE_PATTERNS; do
            case "$bid" in $pat) printf 'ineligible'; return ;; esac
        done
        unset IFS
    fi
    # _MASReceipt is the definitive marker for App Store-installed apps.
    # `mas list` returns numeric track IDs not bundle IDs, so checking
    # the receipt directly closes the gap.
    if [ -n "$app_dir" ] && [ -d "$app_dir/Contents/_MASReceipt" ]; then
        printf 'mas'; return
    fi
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

    # Output format: <handler>\t<source>\t<extracted_url_or_id>
    # The third field carries the actual SUFeedURL / KSProductID so the
    # downstream synthesized config has the data the handler needs.
    if [ -n "$sufeed" ]; then
        printf 'sparkle\tSUFeedURL\t%s\n' "$sufeed"
        return 0
    fi
    if [ -n "$kspid" ]; then
        printf 'keystone\tKSProductID\t%s\n' "$kspid"
        return 0
    fi
    if [ -d "$app/Contents/Frameworks/Squirrel.framework" ]; then
        printf 'squirrel\tSquirrel.framework\t\n'
        return 0
    fi
    if find "$app/Contents/Frameworks" -maxdepth 4 -name "ShipIt" 2>/dev/null | grep -q .; then
        printf 'squirrel\tShipIt\t\n'
        return 0
    fi
    printf 'builtin\tnone\t\n'
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

    abs_path="$APPS_ROOT/$app_dir"
    owned=$(_owned_by "$bid" "$abs_path")
    if [ -n "$owned" ] && [ "$INCLUDE_OWNED" != "1" ]; then
        continue
    fi

    cls=$(_classify "$abs_path")
    # Parse three TAB-separated fields: <handler>\t<source>\t<extracted>
    handler=$(printf '%s' "$cls" | awk -F'\t' '{print $1}')
    source_field=$(printf '%s' "$cls" | awk -F'\t' '{print $2}')
    extracted=$(printf '%s' "$cls" | awk -F'\t' '{print $3}')

    python3 - "$bid" "$abs_path" "$ver" "$name" "$handler" "$source_field" "$owned" "$extracted" <<'PY'
import json, sys, signal
signal.signal(signal.SIGPIPE, signal.SIG_DFL)
bid, path, ver, name, handler, src, owned, extracted = sys.argv[1:9]
out = {
    "bundle_id": bid,
    "app_path": path,
    "version": ver or "",
    "display_name": name,
    "fingerprint_handler": handler,
    "fingerprint_source": src,
    "owned_by": owned or None,
}
# Carry the extracted URL/ID through so check.sh's synthetic config has
# what the handler needs (SUFeedURL for sparkle, KSProductID for keystone).
if handler == "sparkle" and extracted:
    out["appcast_url"] = extracted
elif handler == "keystone" and extracted:
    out["ksadmin_product_id"] = extracted
try:
    print(json.dumps(out, separators=(",", ":")))
except BrokenPipeError:
    pass
PY
done
exit 0
