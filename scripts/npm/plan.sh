#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
source "${SCRIPT_DIR}/lib/common.sh"
source "${SCRIPT_DIR}/lib/detect.sh"
source "${SCRIPT_DIR}/lib/json.sh"

json_init plan npm
json_register_exit_trap "${JSON_OUT:-}"

detect_package_managers
if [[ -z "${NPM_BIN:-}" ]]; then
    json_add_diag info NPM-MISSING "npm not found"
    exit 0
fi

# Same as check but treated as plan (priority CLIs always re-installed @latest)
out=$(run_as_user "${NPM_BIN}" outdated -g --json 2>/dev/null || echo '{}')
n=0
while IFS= read -r line; do
    [[ -z "$line" ]] && continue
    name=$(echo "$line" | awk -F'|' '{print $1}')
    cur=$(echo "$line" | awk -F'|' '{print $2}')
    lat=$(echo "$line" | awk -F'|' '{print $3}')
    [[ -z "$name" ]] && continue
    json_add_item id="npm:upgrade:${name}" action="upgrade" \
        from="${cur}" to="${lat}" result="noop"
    n=$((n + 1))
done < <(echo "$out" | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
    for k, v in d.items():
        print(f\"{k}|{v.get('current','')}|{v.get('latest','')}\")
except Exception:
    pass
" 2>/dev/null)

# Priority AI CLIs always force-installed @latest in apply
for pkg in "@anthropic-ai/claude-code" "@google/gemini-cli" "@openai/codex"; do
    # Sesja 57: include current version in from= so SPA inventory shows
    # cur (else this plan row paints with installed=null). Resolves
    # via npm_pkg_version when available; falls back to empty otherwise.
    _cur=$(npm_pkg_version "$pkg" 2>/dev/null || true)
    json_add_item id="npm:force-latest:${pkg}" action="reinstall" \
        from="${_cur}" to="latest" result="noop"
    n=$((n + 1))
done

json_add_diag info NPM-PLAN-SIZE "${n} npm action(s) planned"
exit 0
