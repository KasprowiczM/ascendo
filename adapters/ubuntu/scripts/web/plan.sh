#!/usr/bin/env bash
# adapters/ubuntu/scripts/web/plan.sh — read-only enumeration of pending updates.
# Same as check.sh but only emits items with planned status (or skipped).
set -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
# shellcheck source=../../../../lib/json.sh
source "$REPO_ROOT/lib/json.sh"

json_init plan web
json_register_exit_trap "${JSON_OUT:-}"

# Plan is just check + filter. Re-invoke check.sh but capture into a
# temporary sidecar then re-emit only planned items.
TMP_OUT=$(mktemp -t ascendo-web-plan-XXXXXX.json)
TMP_LOG=$(mktemp -t ascendo-web-plan-XXXXXX.log)
JSON_OUT="$TMP_OUT" LOG_FILE="$TMP_LOG" \
    bash "$SCRIPT_DIR/scripts/web/check.sh" >/dev/null 2>&1 || true

if [ -s "$TMP_OUT" ]; then
    python3 -c '
import json, sys
try:
    d = json.load(open(sys.argv[1]))
except Exception:
    sys.exit(0)
for it in d.get("items", []):
    det = it.get("details", "") or ""
    if "planned" in det:
        print(json.dumps(it))
' "$TMP_OUT" | while IFS= read -r ITEM; do
        ID=$(printf '%s' "$ITEM" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("id",""))')
        FROM=$(printf '%s' "$ITEM" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("from","") or "")')
        TO=$(printf '%s' "$ITEM" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("to","") or "")')
        json_add_item id="$ID" action=upgrade result=ok from="$FROM" to="$TO" details="planned"
        json_count_ok
    done
fi

rm -f "$TMP_OUT" "$TMP_LOG"
json_add_advisory "web plan: see check phase for full inventory"
exit 0
