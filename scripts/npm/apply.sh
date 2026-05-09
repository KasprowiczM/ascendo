#!/usr/bin/env bash
# =============================================================================
# scripts/npm/apply.sh — Native global npm update.
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
source "${SCRIPT_DIR}/lib/common.sh"
source "${SCRIPT_DIR}/lib/detect.sh"
source "${SCRIPT_DIR}/lib/json.sh"

export INVENTORY_SILENT=1
json_init apply npm
json_register_exit_trap "${JSON_OUT:-}"

print_header "npm — apply"

detect_package_managers
if [[ -z "${NPM_BIN:-}" ]]; then
    json_add_diag info NPM-MISSING "npm not found"
    exit 0
fi

CONFIG_NPM="${SCRIPT_DIR}/config/npm-globals.list"
PRIORITY_AI_CLI_PKGS=(
    "@anthropic-ai/claude-code"
    "@google/gemini-cli"
    "@openai/codex"
)
EXIT_RC=0

# ── 1. Pre-update outdated snapshot for from→to items ────────────────────────
outdated=$(run_as_user "${NPM_BIN}" outdated -g --json 2>/dev/null || echo '{}')

# ── 2. npm update -g ──────────────────────────────────────────────────────────
print_step "npm update -g"
_stream_emit ">>> npm update -g"
if run_capture_as_user "${NPM_BIN}" update -g; then
    print_ok
    json_add_item id="npm:update" action="upgrade" result="ok"
    json_count_ok
else
    print_warn "npm update -g non-zero"
    json_add_item id="npm:update" action="upgrade" result="warn"
    _tail="$(_stderr_tail "${_LAST_RUN_OUT_FILE}")"
    if [[ -n "$_tail" ]]; then
        json_add_diag warn NPM-UPDATE-WARN \
            "npm update -g returned non-zero — last output: ${_tail}"
    else
        json_add_diag warn NPM-UPDATE-WARN "npm update -g returned non-zero"
    fi
    json_count_warn
    EXIT_RC=1
fi

# Per-package items from outdated snapshot
echo "$outdated" | python3 -c "
import json, sys
try:
    d = json.loads(sys.stdin.read() or '{}')
    for name, info in d.items():
        print(f\"{name}|{info.get('current','')}|{info.get('latest','')}\")
except Exception:
    pass
" 2>/dev/null | while IFS='|' read -r name cur lat; do
    [[ -z "$name" ]] && continue
    json_add_item id="npm:upgrade:${name}" action="upgrade" \
        from="${cur}" to="${lat}" result="ok"
done

# ── 3. Install missing from config ────────────────────────────────────────────
if [[ -f "$CONFIG_NPM" ]]; then
    while IFS= read -r pkg; do
        [[ -z "$pkg" ]] && continue
        if npm_pkg_installed "$pkg"; then
            ver=$(npm_pkg_version "$pkg")
            json_add_item id="npm:configured:${pkg}" action="present" \
                to="${ver}" result="ok"
            json_count_ok
        else
            print_step "npm install -g ${pkg}"
            _stream_emit ">>> npm install -g ${pkg}"
            if run_capture_as_user "${NPM_BIN}" install -g "$pkg"; then
                print_ok
                json_add_item id="npm:install:${pkg}" action="install" result="ok"
                json_count_ok
            else
                print_warn "install failed"
                json_add_item id="npm:install:${pkg}" action="install" result="failed"
                _tail="$(_stderr_tail "${_LAST_RUN_OUT_FILE}")"
                if [[ -n "$_tail" ]]; then
                    json_add_diag warn NPM-INSTALL-FAIL \
                        "npm install -g ${pkg} failed — last output: ${_tail}"
                else
                    json_add_diag warn NPM-INSTALL-FAIL \
                        "npm install -g ${pkg} failed"
                fi
                json_count_warn
                [[ $EXIT_RC -eq 0 ]] && EXIT_RC=1
            fi
        fi
    done < <(parse_config_names_filtered npm "$CONFIG_NPM")
fi

# ── 4. Force latest for priority AI CLIs ──────────────────────────────────────
for pkg in "${PRIORITY_AI_CLI_PKGS[@]}"; do
    print_step "npm install -g ${pkg}@latest"
    _stream_emit ">>> npm install -g ${pkg}@latest"
    if run_capture_as_user "${NPM_BIN}" install -g "${pkg}@latest"; then
        print_ok
        json_add_item id="npm:force-latest:${pkg}" action="reinstall" result="ok"
        json_count_ok
    else
        print_warn "failed"
        json_add_item id="npm:force-latest:${pkg}" action="reinstall" result="failed"
        _tail="$(_stderr_tail "${_LAST_RUN_OUT_FILE}")"
        if [[ -n "$_tail" ]]; then
            json_add_diag warn NPM-AI-CLI-FAIL \
                "failed to force-install ${pkg}@latest — last output: ${_tail}"
        else
            json_add_diag warn NPM-AI-CLI-FAIL \
                "failed to force-install ${pkg}@latest"
        fi
        json_count_warn
        [[ $EXIT_RC -eq 0 ]] && EXIT_RC=1
    fi
done

# ── 5. Audit (informational only) ────────────────────────────────────────────
audit=$(run_as_user "${NPM_BIN}" audit --global --json 2>/dev/null || echo '{}')
total=$(echo "$audit" | python3 -c "
import json, sys
try:
    d = json.loads(sys.stdin.read() or '{}')
    print(d.get('metadata',{}).get('vulnerabilities',{}).get('total', 0))
except Exception:
    print(0)
" 2>/dev/null || echo 0)
if [[ "${total:-0}" -gt 0 ]]; then
    json_add_diag warn NPM-AUDIT "${total} vulnerability/ies in global tree"
else
    json_add_diag info NPM-AUDIT "no vulnerabilities reported"
fi

exit $EXIT_RC
