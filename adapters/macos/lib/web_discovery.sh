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
#   ASCENDO_WEB_BREW_CASKS       -- bundle IDs OR app filenames (e.g.
#                                   "Inkscape.app") from `brew list --cask`
#                                   metadata. Either form matches.
#   ASCENDO_WEB_MAS_BUNDLE_IDS   -- bundle IDs from `mas list`
#   ASCENDO_WEB_APPLE_BUNDLES    -- bundle IDs signed by Apple
#
# When the inputs aren't set externally, the script auto-populates from
# the actual brew tool. mas + Apple ownership lists default to empty
# (computing them is expensive; com.apple.* prefix is checked inline).
# Tests override via the env vars.
#
# Optional opt-ins:
#   ASCENDO_WEB_DEEP_OWNERSHIP=1 -- run `codesign -dv` on each bundle to
#                                   detect Apple-signed system apps that
#                                   slipped past com.apple.* prefix
#                                   (e.g. /Applications/Safari.app, which
#                                   is symlinked from /System/Cryptexes/).
#                                   ~200ms per app; off by default.
#                                   Results cached per app_dir per run.
#   ASCENDO_WEB_INELIGIBLE_PATTERNS -- extra glob patterns (comma-sep) that
#                                   classify a bundle as "ineligible".
set -o pipefail

APPS_ROOT="${ASCENDO_WEB_APPS_ROOT:-/Applications}"
INCLUDE_OWNED="${ASCENDO_WEB_INCLUDE_OWNED:-0}"
DEEP_OWNERSHIP="${ASCENDO_WEB_DEEP_OWNERSHIP:-0}"

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
            # Extracts both BUNDLE IDS and APP FILENAMES from cask metadata.
            # _owned_by then matches against either, so we catch casks
            # that ship a .app artifact without an `uninstall` block (e.g.
            # Inkscape) AND casks with bundle-id-only metadata (e.g.
            # background services like blackhole-2ch).
            #
            # Bundle id sources (each may be string OR list — brew is
            # inconsistent):
            #   uninstall.quit       -- running-process bundle id
            #   uninstall.pkgutil    -- pkg receipt id (also a bundle id)
            #   uninstall.launchctl  -- launchd label
            #   zap.trash entries    -- ~/Library/.../<bundle>.plist paths
            #
            # App filename source: artifacts[].app[]
            # Intentional word-split on $_brew_tokens (space-separated cask tokens).
            # shellcheck disable=SC2086
            _brew_bids=$(brew info --cask --json=v2 $_brew_tokens 2>/dev/null | python3 -c '
import json, os, re, sys
try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(0)


def _flatten(value):
    """Yield strings from a value that brew may emit as str or list."""
    if value is None:
        return
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, str):
                yield item


def _bundle_from_plist_path(path):
    # ~/Library/Preferences/com.example.foo.plist -> com.example.foo
    base = os.path.basename(str(path))
    m = re.match(r"^([A-Za-z0-9._-]+)\.plist$", base)
    if m:
        cand = m.group(1)
        # crude bundle-id sanity: must contain a dot and look reverse-DNS
        if "." in cand and not cand.startswith("."):
            return cand
    return None


out = set()
for cask in data.get("casks", []):
    for art in cask.get("artifacts", []):
        if not isinstance(art, dict):
            continue
        # 1) App filenames (Inkscape.app, "Microsoft Word.app", ...)
        for app_name in _flatten(art.get("app")):
            if isinstance(app_name, str) and app_name.endswith(".app"):
                # store as e.g. "app:Inkscape.app"
                out.add("app:" + os.path.basename(app_name))
        # 2) Uninstall block — quit / pkgutil / launchctl bundle ids
        uninstall = art.get("uninstall")
        if isinstance(uninstall, list):
            for u in uninstall:
                if not isinstance(u, dict):
                    continue
                for key in ("quit", "pkgutil", "launchctl"):
                    for bid in _flatten(u.get(key)):
                        out.add(bid)
        # 3) Zap.trash plist paths
        zap = art.get("zap")
        if isinstance(zap, list):
            for z in zap:
                if not isinstance(z, dict):
                    continue
                for path in _flatten(z.get("trash")):
                    bid = _bundle_from_plist_path(path)
                    if bid:
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

# Codesign team-id cache (shell associative arrays would be nicer but
# bash 3.2 doesn't have them — so we use parallel arrays + a string
# search by app_dir). _CSIGN_PATHS holds NUL-terminated app_dirs and
# _CSIGN_TEAMS holds the matching team identifiers in the same order.
_CSIGN_PATHS=""
_CSIGN_TEAMS=""

# _codesign_team_id <app_dir>
# Echoes the TeamIdentifier as reported by `codesign -dv --verbose=2`.
# Apple-bundled apps report "not set"; third-party apps signed via the
# Apple Developer Program report a 10-character alphanumeric ID.
# Result is cached per app_dir for the lifetime of the discovery run.
_codesign_team_id() {
    local app="$1"
    [ -z "$app" ] && return 0
    # cache hit?
    case "$_CSIGN_PATHS" in
        *"|$app|"*)
            # parallel-array lookup: count entries before $app, return
            # that index from _CSIGN_TEAMS
            local before="${_CSIGN_PATHS%%|$app|*}"
            local n=0
            local rest="$before"
            while [ "${rest#*|}" != "$rest" ]; do
                n=$((n + 1))
                rest="${rest#*|}"
            done
            # team ids are stored space-separated; n is 1-based after
            # the leading sentinel
            printf '%s' "$_CSIGN_TEAMS" | awk -v idx="$n" '{print $idx}'
            return 0
            ;;
    esac
    local out tid
    out=$(codesign -dv --verbose=2 "$app" 2>&1 || true)
    tid=$(printf '%s\n' "$out" | awk -F'=' '/^TeamIdentifier=/ {print $2; exit}')
    [ -z "$tid" ] && tid="?"
    # encode spaces to underscore so awk-split-on-space remains stable
    case "$tid" in *" "*) tid=$(printf '%s' "$tid" | tr ' ' '_') ;; esac
    if [ -z "$_CSIGN_PATHS" ]; then
        _CSIGN_PATHS="|$app|"
        _CSIGN_TEAMS="$tid"
    else
        _CSIGN_PATHS="$_CSIGN_PATHS$app|"
        _CSIGN_TEAMS="$_CSIGN_TEAMS $tid"
    fi
    printf '%s' "$tid"
}

# _owned_by <bundle_id> [<app_dir>]
# Echoes "brew", "mas", "softwareupdate", "ineligible", or "" (unowned).
#
# Match precedence (first match wins):
#   1. brew cask: bundle id OR app filename match against ASCENDO_WEB_BREW_CASKS
#   2. MAS: bundle id in ASCENDO_WEB_MAS_BUNDLE_IDS OR _MASReceipt directory
#   3. Apple system: com.apple.* OR ASCENDO_WEB_APPLE_BUNDLES OR (opt-in)
#      codesign TeamIdentifier="not set" (= Apple-signed)
#   4. Ineligible patterns
#
# "ineligible" covers things that look like .app bundles but aren't real
# user-updatable apps:
#   - Chrome / Google Drive web shortcuts (com.google.drivefs.shortcuts.*,
#     com.google.Chrome.app.*) — they just open a URL in the browser
#   - MDM-managed shims (com.microsoft.wdav.shim) — IT pushes updates
#   - Setapp wrappers, Wine prefixes, Crossover bottle apps, etc.
#   - Ascendo itself (dev.ascendo.*) — updated via `git pull`
# These never have a real candidate version to probe.
_owned_by() {
    local bid="$1" app_dir="${2:-}"
    local app_basename=""
    [ -n "$app_dir" ] && app_basename=$(basename "$app_dir")

    # 1. brew cask — match by bundle id
    case ",${ASCENDO_WEB_BREW_CASKS:-}," in (*",$bid,"*) printf 'brew'; return ;; esac
    # ... or by app filename (e.g. "app:Inkscape.app")
    if [ -n "$app_basename" ]; then
        case ",${ASCENDO_WEB_BREW_CASKS:-}," in
            (*",app:$app_basename,"*) printf 'brew'; return ;;
        esac
    fi

    # 2. Mac App Store
    case ",${ASCENDO_WEB_MAS_BUNDLE_IDS:-}," in (*",$bid,"*) printf 'mas'; return ;; esac
    # _MASReceipt is the definitive marker for App Store-installed apps.
    # `mas list` returns numeric track IDs not bundle IDs, so checking
    # the receipt directly closes the gap.
    if [ -n "$app_dir" ] && [ -d "$app_dir/Contents/_MASReceipt" ]; then
        printf 'mas'; return
    fi

    # 3. Apple-bundled (system / softwareupdate)
    case ",${ASCENDO_WEB_APPLE_BUNDLES:-}," in (*",$bid,"*) printf 'softwareupdate'; return ;; esac
    case "$bid" in com.apple.*) printf 'softwareupdate'; return ;; esac
    # Optional: codesign team-id check. Apple-signed system bundles report
    # TeamIdentifier="not set" while every Developer-Program app reports a
    # 10-char alphanumeric. ~200ms per app; opt in with
    # ASCENDO_WEB_DEEP_OWNERSHIP=1 for situations where Apple ships a
    # bundle without a com.apple.* prefix (rare; documented for
    # /Applications/Safari.app symlink case).
    if [ "$DEEP_OWNERSHIP" = "1" ] && [ -n "$app_dir" ]; then
        local tid
        tid=$(_codesign_team_id "$app_dir")
        case "$tid" in
            "not_set") printf 'softwareupdate'; return ;;
        esac
    fi

    # 4. Ineligible-bundle patterns
    case "$bid" in
        com.google.drivefs.shortcuts.*) printf 'ineligible'; return ;;
        com.google.Chrome.app.*)        printf 'ineligible'; return ;;
        com.microsoft.wdav.shim)        printf 'ineligible'; return ;;
        com.microsoft.wdav.*.shim)      printf 'ineligible'; return ;;
        dev.ascendo.*)                  printf 'ineligible'; return ;;
    esac
    if [ -n "${ASCENDO_WEB_INELIGIBLE_PATTERNS:-}" ]; then
        local IFS=','
        for pat in $ASCENDO_WEB_INELIGIBLE_PATTERNS; do
            case "$bid" in $pat) printf 'ineligible'; return ;; esac
        done
        unset IFS
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
