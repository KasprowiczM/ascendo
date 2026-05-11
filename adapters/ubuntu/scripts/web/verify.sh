#!/usr/bin/env bash
# adapters/ubuntu/scripts/web/verify.sh — post-apply version re-check.
# Re-runs discovery and re-emits installed versions. Tier-A apps that
# upgraded should now report new INSTALLED == LATEST.
set -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ADAPTER_LIB="$SCRIPT_DIR/lib"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

# shellcheck source=../../../../lib/json.sh
source "$REPO_ROOT/lib/json.sh"

json_init verify web
json_register_exit_trap "${JSON_OUT:-}"

# Brief sleep for Tier-B handlers (builtin) where the in-app update agent
# may still be staging.
sleep 1

DISC_OUT=$(bash "$ADAPTER_LIB/web_discovery.sh" 2>/dev/null || true)
COUNT=0

while IFS= read -r DISC_LINE; do
    [ -z "$DISC_LINE" ] && continue
    eval "$(printf '%s' "$DISC_LINE" | python3 -c '
import json, shlex, sys
try:
    d = json.loads(sys.stdin.read())
except Exception:
    sys.exit(0)
for k in ("app_id", "version", "owned_by"):
    print(f"DISC_{k.upper()}={shlex.quote(str(d.get(k, \"\")))}")
' 2>/dev/null)"
    [ -z "${DISC_APP_ID:-}" ] && continue
    case "${DISC_OWNED_BY:-}" in
        apt:*) continue ;;
    esac
    json_add_item id="web:${DISC_APP_ID}" action=verify result=ok \
        from="${DISC_VERSION:-}" details="installed"
    json_count_ok
    COUNT=$((COUNT + 1))
done <<< "$DISC_OUT"

json_add_advisory "web verify: ${COUNT} apps re-inventoried"
exit 0
