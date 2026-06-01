#!/usr/bin/env bash
# =============================================================================
# adapters/ubuntu/scripts/web/check.sh — Linux web-app outdated probe
# =============================================================================
# Iterates the merged registry × discovery; for each match dispatches to
# <handler>_check, classifying into planned / up_to_date / skipped.
#
# Reads from env (set by lib/orchestrator.sh contract):
#   JSON_OUT    — sidecar path
#   LOG_FILE    — plain log path
#   ORCH_DRY_RUN, ORCH_PROFILE — informational only
# =============================================================================
set -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ADAPTER_LIB="$SCRIPT_DIR/lib"
ADAPTER_CONFIG="$SCRIPT_DIR/config"

# Legacy lib for json.sh emitter (sidecar contract).
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
# shellcheck source=../../../../lib/json.sh
source "$REPO_ROOT/lib/json.sh"

# Adapter-local helpers + handlers
# shellcheck source=../../lib/ascendo_web.sh
source "$ADAPTER_LIB/ascendo_web.sh"
for h in github_release release_feed appimage builtin; do
    # shellcheck source=../../lib/handlers/github_release.sh
    source "$ADAPTER_LIB/handlers/${h}.sh"
done

REG_PATH="${ASCENDO_WEB_REGISTRY_PATH:-$ADAPTER_CONFIG/web_apps.toml}"
USER_REG="${ASCENDO_WEB_USER_REGISTRY_PATH:-$HOME/.config/ascendo/web_apps.toml}"
[ -f "$USER_REG" ] || USER_REG=""
REG_SHIM="$ADAPTER_LIB/web_registry.py"

json_init check web
json_register_exit_trap "${JSON_OUT:-}"

EXIT_RC=0

# Validate registry first
_reg_args=(--shipped "$REG_PATH")
[ -n "$USER_REG" ] && _reg_args+=(--user-override "$USER_REG")
if ! python3 "$REG_SHIM" "${_reg_args[@]}" --validate 2>/tmp/.ascendo_web_reg_err >/dev/null; then
    json_add_diag error WEB-REG-INVALID "registry validation failed: $(cat /tmp/.ascendo_web_reg_err)"
    json_count_err
    rm -f /tmp/.ascendo_web_reg_err
    exit 2
fi
rm -f /tmp/.ascendo_web_reg_err

COUNT_PLANNED=0
COUNT_UTD=0
COUNT_SKIPPED=0

# Discovery: walk web apps on disk.
# W10 (audit §4): a crashed discovery must NOT read as "0 web apps, all up to
# date". web_discovery.sh emits a trailing `DISCOVERY_OK<TAB>count` sentinel on
# a clean walk and `DISCOVERY_FAILED<TAB>reason` + non-zero exit on a
# precondition failure. Distinguish the three outcomes here.
DISC_RC=0
DISC_OUT=$(bash "$ADAPTER_LIB/web_discovery.sh" 2>/dev/null) || DISC_RC=$?

if printf '%s\n' "$DISC_OUT" | grep -q '^DISCOVERY_FAILED'; then
    _disc_reason=$(printf '%s\n' "$DISC_OUT" | grep '^DISCOVERY_FAILED' | head -1 | cut -f2-)
    json_add_diag error WEB-DISCOVERY-FAILED "web discovery failed: ${_disc_reason:-unknown}"
    json_count_err
    exit 2
fi
if [ "$DISC_RC" -ne 0 ] || ! printf '%s\n' "$DISC_OUT" | grep -q '^DISCOVERY_OK'; then
    json_add_diag error WEB-DISCOVERY-INCOMPLETE \
        "web discovery did not complete (rc=$DISC_RC, no DISCOVERY_OK sentinel); reporting failure instead of a misleading '0 apps up to date'"
    json_count_err
    exit 2
fi

# Strip the TAB-prefixed sentinel lines before processing app rows.
DISC_OUT=$(printf '%s\n' "$DISC_OUT" | grep -vE '^DISCOVERY_(OK|FAILED)')

# Track which app_ids we've already emitted (avoid double-counting from
# discovery + registry passes).
EMITTED=""

# Pass 1: discovery → registry merge
if [ -n "$DISC_OUT" ]; then
    while IFS= read -r DISC_LINE; do
        [ -z "$DISC_LINE" ] && continue

        # Parse discovery row
        eval "$(printf '%s' "$DISC_LINE" | python3 -c '
import json, os, shlex, sys
try:
    d = json.loads(sys.stdin.read())
except Exception:
    sys.exit(0)
for k in ("app_id", "path", "version", "handler_hint", "owned_by"):
    print(f"DISC_{k.upper()}={shlex.quote(str(d.get(k, \"\")))}")
print(f"DISC_DISP={shlex.quote(str(d.get(\"display_name\", \"\")))}")
' 2>/dev/null)"

        [ -z "${DISC_APP_ID:-}" ] && continue

        # Skip apt-owned binaries — they flow through the apt manager.
        case "${DISC_OWNED_BY:-}" in
            apt:*)
                continue
                ;;
        esac

        # Try to find a matching registry entry by app_id, then by hint.
        CFG=$(python3 "$REG_SHIM" "${_reg_args[@]}" --get-app "$DISC_APP_ID" 2>/dev/null || true)
        if [ -z "$CFG" ]; then
            # Try discovery-hint matching against path basename
            CFG=$(python3 "$REG_SHIM" "${_reg_args[@]}" \
                  --match-discovery-hint "$(basename "$DISC_PATH")" 2>/dev/null || true)
        fi

        if [ -z "$CFG" ]; then
            # Discovered but not registered — emit as info skipped.
            json_add_item id="web:${DISC_APP_ID}" action=upgrade result=skipped \
                from="${DISC_VERSION:-}" details="discovered ${DISC_HANDLER_HINT}, no registry match"
            json_count_warn
            COUNT_SKIPPED=$((COUNT_SKIPPED + 1))
            EMITTED="$EMITTED $DISC_APP_ID"
            continue
        fi

        # Re-key emit by registry app_id (normalises naming).
        REG_APP_ID=$(printf '%s' "$CFG" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("app_id",""))')
        HANDLER=$(printf '%s' "$CFG" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("handler",""))')

        # Inject discovered path into cfg so appimage_check can use it
        CFG=$(printf '%s' "$CFG" | DISC_PATH="$DISC_PATH" python3 -c '
import json, os, sys
d = json.load(sys.stdin)
d["_discovered_path"] = os.environ.get("DISC_PATH", "")
print(json.dumps(d))
')

        INSTALLED="${DISC_VERSION:-}"
        LATEST=$("${HANDLER}_check" "$REG_APP_ID" "$CFG" 2>/dev/null || true)
        LATEST=$(printf '%s' "$LATEST" | tr -d '[:space:]')

        EMITTED="$EMITTED $REG_APP_ID"

        if [ "$LATEST" = "__GH_RATE_LIMITED__" ]; then
            json_add_item id="web:${REG_APP_ID}" action=upgrade result=warn \
                from="$INSTALLED" details="GitHub API rate-limited"
            json_count_warn
            COUNT_SKIPPED=$((COUNT_SKIPPED + 1))
            continue
        fi

        if [ -z "$LATEST" ]; then
            case "$HANDLER" in
                builtin)
                    json_add_item id="web:${REG_APP_ID}" action=upgrade result=skipped \
                        from="$INSTALLED" details="trigger-only (manual update)"
                    ;;
                appimage)
                    json_add_item id="web:${REG_APP_ID}" action=upgrade result=skipped \
                        from="$INSTALLED" details="AppImage has no embedded version"
                    ;;
                *)
                    json_add_item id="web:${REG_APP_ID}" action=upgrade result=skipped \
                        from="$INSTALLED" details="${HANDLER} probe returned empty"
                    ;;
            esac
            json_count_warn
            COUNT_SKIPPED=$((COUNT_SKIPPED + 1))
            continue
        fi

        if [ -z "$INSTALLED" ] || [ "$INSTALLED" = "$LATEST" ] || ! _version_gt "$LATEST" "$INSTALLED"; then
            json_add_item id="web:${REG_APP_ID}" action=upgrade result=ok \
                from="$INSTALLED" to="$LATEST"
            json_count_ok
            COUNT_UTD=$((COUNT_UTD + 1))
        else
            json_add_item id="web:${REG_APP_ID}" action=upgrade result=ok \
                from="$INSTALLED" to="$LATEST" details="planned"
            json_count_ok
            COUNT_PLANNED=$((COUNT_PLANNED + 1))
        fi
    done <<< "$DISC_OUT"
fi

# Pass 2 (registry-only / "not installed locally") was REMOVED in Sesja 56
# per operator request: web inventory should only show apps actually
# present on this system. Registry entries that don't match any
# discovery row are silently skipped — operators discover the candidate
# only AFTER installing the app, when discovery picks it up. Opt back in
# by setting ASCENDO_WEB_INCLUDE_UNINSTALLED=1.
if [ "${ASCENDO_WEB_INCLUDE_UNINSTALLED:-0}" = "1" ]; then
    while IFS= read -r REG_APP_ID; do
        [ -z "$REG_APP_ID" ] && continue
        case " $EMITTED " in
            *" $REG_APP_ID "*) continue ;;
        esac

        CFG=$(python3 "$REG_SHIM" "${_reg_args[@]}" --get-app "$REG_APP_ID" 2>/dev/null || true)
        [ -z "$CFG" ] && continue
        HANDLER=$(printf '%s' "$CFG" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("handler",""))')

        LATEST=$("${HANDLER}_check" "$REG_APP_ID" "$CFG" 2>/dev/null || true)
        LATEST=$(printf '%s' "$LATEST" | tr -d '[:space:]')

        if [ -z "$LATEST" ] || [ "$LATEST" = "__GH_RATE_LIMITED__" ]; then
            json_add_item id="web:${REG_APP_ID}" action=upgrade result=skipped \
                details="not installed locally"
        else
            json_add_item id="web:${REG_APP_ID}" action=upgrade result=skipped \
                to="$LATEST" details="not installed locally; latest=$LATEST"
        fi
        json_count_warn
        COUNT_SKIPPED=$((COUNT_SKIPPED + 1))
    done < <(python3 "$REG_SHIM" "${_reg_args[@]}" --list-app-ids 2>/dev/null)
fi

json_add_advisory "web: ${COUNT_PLANNED} outdated, ${COUNT_UTD} up_to_date, ${COUNT_SKIPPED} skipped"
exit $EXIT_RC
