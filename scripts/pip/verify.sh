#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
source "${SCRIPT_DIR}/lib/common.sh"
source "${SCRIPT_DIR}/lib/detect.sh"
source "${SCRIPT_DIR}/lib/json.sh"

json_init verify pip
json_register_exit_trap "${JSON_OUT:-}"

detect_package_managers
PY3=""
for c in "${BREW_PREFIX:-/home/linuxbrew/.linuxbrew}/bin/python3" /usr/bin/python3; do
    [[ -x "$c" ]] && PY3="$c" && break
done
if [[ -z "$PY3" ]]; then
    json_add_diag info PIP-MISSING "python3 not found"
    exit 0
fi

CONFIG_PIP="${SCRIPT_DIR}/config/pip-packages.list"
CONFIG_PIPX="${SCRIPT_DIR}/config/pipx-packages.list"
EXIT_RC=0

# Pip user packages
if [[ -f "$CONFIG_PIP" ]]; then
    installed=$($PY3 -m pip list --user --format=json 2>/dev/null | python3 -c "
import json, sys
try:
    for p in json.loads(sys.stdin.read() or '[]'):
        print(p['name'].lower() + '\t' + p.get('version',''))
except Exception:
    pass
")
    while IFS= read -r pkg; do
        [[ -z "$pkg" ]] && continue
        base="${pkg%%==*}"
        ver=$(printf '%s\n' "$installed" | awk -v b="$base" 'BEGIN{IGNORECASE=1} $1==tolower(b){print $2; exit}')
        if [[ -n "$ver" ]]; then
            # Sesja 57: pass installed version into both from= and to=
            # so the inventory row shows cur and candidate correctly.
            json_add_item id="pip:installed:${base}" action="present" \
                from="${ver}" to="${ver}" result="ok"
            json_count_ok
        else
            json_add_item id="pip:installed:${base}" action="present" result="failed"
            json_add_diag warn PIP-MISSING "configured pip user package missing: ${base}"
            json_count_warn
            EXIT_RC=1
        fi
    done < <(parse_config_names "$CONFIG_PIP")
fi

# Pipx
PIPX_BIN=$(command -v pipx 2>/dev/null || true)
if [[ -n "$PIPX_BIN" && -f "$CONFIG_PIPX" ]]; then
    plist=$(run_as_user "${PIPX_BIN}" list --json 2>/dev/null || echo '{}')
    while IFS= read -r pkg; do
        [[ -z "$pkg" ]] && continue
        base="${pkg%%==*}"
        ver=$(echo "$plist" | python3 -c "
import json, sys
try:
    d = json.loads(sys.stdin.read() or '{}')
    v = d.get('venvs', {}).get('$base', {})
    print(v.get('metadata',{}).get('main_package',{}).get('package_version') or '')
except Exception:
    print('')
")
        if [[ -n "$ver" ]]; then
            # Sesja 57: bidirectional from=/to= so SPA inventory paints.
            json_add_item id="pipx:installed:${base}" action="present" \
                from="${ver}" to="${ver}" result="ok"
            json_count_ok
        else
            json_add_item id="pipx:installed:${base}" action="present" result="failed"
            json_add_diag warn PIPX-MISSING "configured pipx package missing: ${base}"
            json_count_warn
            EXIT_RC=1
        fi
    done < <(parse_config_names "$CONFIG_PIPX")
fi

exit $EXIT_RC
