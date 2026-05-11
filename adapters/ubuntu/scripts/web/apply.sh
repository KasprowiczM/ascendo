#!/usr/bin/env bash
# adapters/ubuntu/scripts/web/apply.sh — install pending web-app updates.
set -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ADAPTER_LIB="$SCRIPT_DIR/lib"
ADAPTER_CONFIG="$SCRIPT_DIR/config"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

# shellcheck source=../../../../lib/json.sh
source "$REPO_ROOT/lib/json.sh"
# shellcheck source=../../lib/ascendo_web.sh
source "$ADAPTER_LIB/ascendo_web.sh"
for h in github_release release_feed appimage builtin; do
    source "$ADAPTER_LIB/handlers/${h}.sh"
done

REG_PATH="${ASCENDO_WEB_REGISTRY_PATH:-$ADAPTER_CONFIG/web_apps.toml}"
USER_REG="${ASCENDO_WEB_USER_REGISTRY_PATH:-$HOME/.config/ascendo/web_apps.toml}"
[ -f "$USER_REG" ] || USER_REG=""
REG_SHIM="$ADAPTER_LIB/web_registry.py"
DRY_RUN="${ORCH_DRY_RUN:-0}"

json_init apply web
json_register_exit_trap "${JSON_OUT:-}"

_reg_args=(--shipped "$REG_PATH")
[ -n "$USER_REG" ] && _reg_args+=(--user-override "$USER_REG")

# Re-run discovery + registry merge to find what's planned.
DISC_OUT=$(bash "$ADAPTER_LIB/web_discovery.sh" 2>/dev/null || true)
COUNT_OK=0
COUNT_FAIL=0

if [ -z "$DISC_OUT" ]; then
    json_add_advisory "no web apps discovered"
    exit 0
fi

while IFS= read -r DISC_LINE; do
    [ -z "$DISC_LINE" ] && continue
    eval "$(printf '%s' "$DISC_LINE" | python3 -c '
import json, os, shlex, sys
try:
    d = json.loads(sys.stdin.read())
except Exception:
    sys.exit(0)
for k in ("app_id", "path", "version", "owned_by"):
    print(f"DISC_{k.upper()}={shlex.quote(str(d.get(k, \"\")))}")
' 2>/dev/null)"

    [ -z "${DISC_APP_ID:-}" ] && continue
    case "${DISC_OWNED_BY:-}" in
        apt:*) continue ;;
    esac

    CFG=$(python3 "$REG_SHIM" "${_reg_args[@]}" --get-app "$DISC_APP_ID" 2>/dev/null || true)
    [ -z "$CFG" ] && CFG=$(python3 "$REG_SHIM" "${_reg_args[@]}" \
                          --match-discovery-hint "$(basename "$DISC_PATH")" 2>/dev/null || true)
    [ -z "$CFG" ] && continue

    REG_APP_ID=$(printf '%s' "$CFG" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("app_id",""))')
    HANDLER=$(printf '%s' "$CFG" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("handler",""))')
    DEFER_RUNNING=$(printf '%s' "$CFG" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("defer_if_running","") or "")')

    CFG=$(printf '%s' "$CFG" | DISC_PATH="$DISC_PATH" python3 -c '
import json, os, sys
d = json.load(sys.stdin); d["_discovered_path"] = os.environ.get("DISC_PATH",""); print(json.dumps(d))
')

    INSTALLED="${DISC_VERSION:-}"
    LATEST=$("${HANDLER}_check" "$REG_APP_ID" "$CFG" 2>/dev/null || true)
    LATEST=$(printf '%s' "$LATEST" | tr -d '[:space:]')

    # up-to-date guard
    if [ -n "$LATEST" ] && [ "$LATEST" != "__GH_RATE_LIMITED__" ]; then
        if [ -z "$INSTALLED" ] || [ "$INSTALLED" = "$LATEST" ] || ! _version_gt "$LATEST" "$INSTALLED"; then
            json_add_item id="web:${REG_APP_ID}" action=upgrade result=ok \
                from="$INSTALLED" to="$LATEST" details="up_to_date"
            json_count_ok
            COUNT_OK=$((COUNT_OK + 1))
            continue
        fi
    fi

    # defer-if-running
    if [ "$DEFER_RUNNING" = "True" ] || [ "$DEFER_RUNNING" = "true" ]; then
        if _web_is_running "$REG_APP_ID"; then
            json_add_item id="web:${REG_APP_ID}" action=upgrade result=skipped \
                from="$INSTALLED" details="deferred (app running)"
            json_count_warn
            continue
        fi
    fi

    if [ "$DRY_RUN" = "1" ]; then
        json_add_item id="web:${REG_APP_ID}" action=upgrade result=ok \
            from="$INSTALLED" to="$LATEST" details="dry-run: would apply via $HANDLER"
        json_count_ok
        COUNT_OK=$((COUNT_OK + 1))
        continue
    fi

    # real apply
    if "${HANDLER}_apply" "$REG_APP_ID" "$CFG" >/dev/null 2>&1; then
        json_add_item id="web:${REG_APP_ID}" action=upgrade result=ok \
            from="$INSTALLED" to="$LATEST" details="applied via $HANDLER"
        json_count_ok
        COUNT_OK=$((COUNT_OK + 1))
    else
        rc=$?
        json_add_item id="web:${REG_APP_ID}" action=upgrade result=err \
            from="$INSTALLED" to="$LATEST" details="$HANDLER apply failed rc=$rc"
        json_count_err
        COUNT_FAIL=$((COUNT_FAIL + 1))
    fi
done <<< "$DISC_OUT"

json_add_advisory "web apply: ${COUNT_OK} ok, ${COUNT_FAIL} failed"
[ "$COUNT_FAIL" -gt 0 ] && exit 20 || exit 0
