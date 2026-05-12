#!/usr/bin/env bash
# =============================================================================
# scripts/pip/apply.sh — Native pip user + pipx update.
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
source "${SCRIPT_DIR}/lib/common.sh"
source "${SCRIPT_DIR}/lib/detect.sh"
source "${SCRIPT_DIR}/lib/json.sh"

export INVENTORY_SILENT=1
json_init apply pip
json_register_exit_trap "${JSON_OUT:-}"

print_header "Python — apply (pip + pipx)"

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
PIPX_BIN=$(command -v pipx 2>/dev/null || true)
EXIT_RC=0

# ── 1. pip self upgrade (skip if PEP-668 / brew-managed) ─────────────────────
if [[ "${PY3}" == "${BREW_PREFIX:-/home/linuxbrew/.linuxbrew}"* ]]; then
    json_add_diag info PIP-EXTERNAL "brew Python — pip self-upgrade managed by brew"
elif "$PY3" -m pip install --quiet --upgrade pip 2>/dev/null; then
    json_add_item id="pip:self-upgrade" action="upgrade" result="ok"
    json_count_ok
else
    json_add_item id="pip:self-upgrade" action="upgrade" result="warn"
    json_count_warn
fi

# ── 2. Upgrade outdated user packages ────────────────────────────────────────
print_section "Upgrading pip user packages"
outdated=$("$PY3" -m pip list --user --outdated --format=json 2>/dev/null || echo '[]')
n=0
while IFS= read -r line; do
    [[ -z "$line" ]] && continue
    name=$(echo "$line" | awk -F'|' '{print $1}')
    cur=$(echo "$line"  | awk -F'|' '{print $2}')
    lat=$(echo "$line"  | awk -F'|' '{print $3}')
    [[ -z "$name" ]] && continue
    print_step "pip upgrade ${name}"
    _stream_emit ">>> pip install --user --upgrade ${name}"
    _pip_log="$(mktemp 2>/dev/null || echo /tmp/ascendo-pip-apply.$$)"
    if "$PY3" -m pip install --user --upgrade "$name" >"${_pip_log}" 2>&1; then
        [[ -s "${_pip_log}" && -n "${LOG_FILE:-}" ]] && cat "${_pip_log}" >> "${LOG_FILE}"
        [[ -s "${_pip_log}" && -n "${ASCENDO_STREAM_LOG:-}" ]] \
            && cat "${_pip_log}" >> "${ASCENDO_STREAM_LOG}" 2>/dev/null
        print_ok
        json_add_item id="pip:upgrade:${name}" action="upgrade" \
            from="${cur}" to="${lat}" result="ok"
        json_count_ok
    else
        _pip_rc=$?
        [[ -s "${_pip_log}" && -n "${LOG_FILE:-}" ]] && cat "${_pip_log}" >> "${LOG_FILE}"
        [[ -s "${_pip_log}" && -n "${ASCENDO_STREAM_LOG:-}" ]] \
            && cat "${_pip_log}" >> "${ASCENDO_STREAM_LOG}" 2>/dev/null
        print_warn "failed"
        json_add_item id="pip:upgrade:${name}" action="upgrade" \
            from="${cur}" to="${lat}" result="failed"
        _tail="$(_stderr_tail "${_pip_log}")"
        if [[ -n "$_tail" ]]; then
            json_add_diag warn PIP-UPGRADE-FAIL \
                "pip install --user --upgrade ${name} exited ${_pip_rc} — last output: ${_tail}"
        fi
        json_count_warn
        [[ $EXIT_RC -eq 0 ]] && EXIT_RC=1
    fi
    rm -f "${_pip_log}" 2>/dev/null
    n=$((n + 1))
done < <(echo "$outdated" | python3 -c "
import json, sys
try:
    for x in json.loads(sys.stdin.read() or '[]'):
        print(f\"{x['name']}|{x['version']}|{x['latest_version']}\")
except Exception:
    pass
")
[[ $n -eq 0 ]] && json_add_diag info PIP-CURRENT "all pip user packages up to date"

# ── 3. Install missing pip packages from config ──────────────────────────────
if [[ -f "$CONFIG_PIP" ]]; then
    user_names=$("$PY3" -m pip list --user --format=json 2>/dev/null | python3 -c "
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
        ver=$(printf '%s\n' "$user_names" | awk -v b="$base" 'BEGIN{IGNORECASE=1} $1==tolower(b){print $2; exit}')
        if [[ -n "$ver" ]]; then
            # Sesja 57: pass version into from= AND to= for SPA inventory.
            json_add_item id="pip:configured:${base}" action="present" \
                from="${ver}" to="${ver}" result="ok"
            json_count_ok
        else
            print_step "pip install --user ${pkg}"
            if "$PY3" -m pip install --quiet --user "$pkg" 2>/dev/null; then
                print_ok
                json_add_item id="pip:install:${base}" action="install" result="ok"
                json_count_ok
            else
                json_add_item id="pip:install:${base}" action="install" result="failed"
                json_count_warn
                [[ $EXIT_RC -eq 0 ]] && EXIT_RC=1
            fi
        fi
    done < <(parse_config_names_filtered pip "$CONFIG_PIP")
fi

# ── 4. pipx ───────────────────────────────────────────────────────────────────
if [[ -z "$PIPX_BIN" ]]; then
    json_add_diag info PIPX-MISSING "pipx not installed"
else
    print_step "pipx upgrade-all"
    pipx_out=$(run_as_user "${PIPX_BIN}" upgrade-all 2>&1) && pipx_rc=0 || pipx_rc=$?
    echo "$pipx_out" >> "${LOG_FILE}"
    if [[ $pipx_rc -ne 0 ]]; then
        print_warn "non-zero"
        json_add_item id="pipx:upgrade-all" action="upgrade" result="warn"
        json_count_warn
    else
        print_ok
        json_add_item id="pipx:upgrade-all" action="upgrade" result="ok"
        json_count_ok
        # Extract per-venv upgrades
        while IFS= read -r line; do
            if [[ "$line" == *"upgraded package"* || "$line" == *"is already at latest"* ]]; then
                pkg=$(echo "$line" | awk '{print $1}' | tr -d '"')
                [[ -n "$pkg" ]] && json_add_item id="pipx:upgrade:${pkg}" action="upgrade" result="ok"
            fi
        done <<< "$pipx_out"
    fi

    # Install missing pipx from config
    if [[ -f "$CONFIG_PIPX" ]]; then
        plist=$(run_as_user "${PIPX_BIN}" list --json 2>/dev/null || echo '{}')
        while IFS= read -r pkg; do
            [[ -z "$pkg" ]] && continue
            base="${pkg%%==*}"
            present=$(echo "$plist" | python3 -c "
import json, sys
try:
    d = json.loads(sys.stdin.read() or '{}')
    v = d.get('venvs', {}).get('$base', {})
    print(v.get('metadata',{}).get('main_package',{}).get('package_version') or '')
except Exception:
    print('')
")
            if [[ -n "$present" ]]; then
                ver="$present"
                # Sesja 57: bidirectional from=/to= so SPA inventory paints.
                json_add_item id="pipx:configured:${base}" action="present" \
                    from="${ver}" to="${ver}" result="ok"
                json_count_ok
            else
                print_step "pipx install ${pkg}"
                if run_as_user "${PIPX_BIN}" install "$pkg" >> "${LOG_FILE}" 2>&1; then
                    print_ok
                    json_add_item id="pipx:install:${base}" action="install" result="ok"
                    json_count_ok
                else
                    print_warn "failed"
                    json_add_item id="pipx:install:${base}" action="install" result="failed"
                    json_count_warn
                    [[ $EXIT_RC -eq 0 ]] && EXIT_RC=1
                fi
            fi
        done < <(parse_config_names_filtered pip "$CONFIG_PIPX")
    fi
fi

exit $EXIT_RC
