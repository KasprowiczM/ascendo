#!/usr/bin/env bash
# adapters/ubuntu/scripts/web/cleanup.sh — prune cached AppImages older than 7d.
set -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ADAPTER_LIB="$SCRIPT_DIR/lib"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

# shellcheck source=../../../../lib/json.sh
source "$REPO_ROOT/lib/json.sh"
# shellcheck source=../../lib/ascendo_web.sh
source "$ADAPTER_LIB/ascendo_web.sh"

json_init cleanup web
json_register_exit_trap "${JSON_OUT:-}"

CACHE=$(_web_cache_dir)
DRY_RUN="${ORCH_DRY_RUN:-0}"
PRUNED=0

if [ -d "$CACHE" ]; then
    while IFS= read -r f; do
        [ -z "$f" ] && continue
        BASE=$(basename "$f")
        if [ "$DRY_RUN" = "1" ]; then
            json_add_item id="web-cache:${BASE}" action=remove result=ok details="dry-run"
        else
            rm -f "$f" 2>/dev/null && json_add_item id="web-cache:${BASE}" action=remove result=ok \
                || json_add_item id="web-cache:${BASE}" action=remove result=warn
        fi
        json_count_ok
        PRUNED=$((PRUNED + 1))
    done < <(find "$CACHE" -type f -mtime +7 2>/dev/null)
fi

json_add_advisory "web cleanup: pruned ${PRUNED} cached files >7d"
exit 0
