#!/usr/bin/env bash
# =============================================================================
# scripts/secrets/migrate-to-libsecret.sh — Move .env.local entries into
# libsecret (gnome-keyring / kwallet via secret-tool).
#
# Idempotent. Skips entries that already exist in libsecret. Original
# .env.local is preserved with .bak_<timestamp> when --remove-source.
#
# Usage:
#   bash scripts/secrets/migrate-to-libsecret.sh                # dry-run
#   bash scripts/secrets/migrate-to-libsecret.sh --apply
#   bash scripts/secrets/migrate-to-libsecret.sh --apply --remove-source
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
source "${SCRIPT_DIR}/lib/common.sh"
source "${SCRIPT_DIR}/lib/secrets.sh"

ENV_FILE="${SCRIPT_DIR}/.env.local"
APPLY=0
REMOVE_SOURCE=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --apply)         APPLY=1 ;;
        --remove-source) REMOVE_SOURCE=1 ;;
        -h|--help)
            sed -n '4,15p' "$0"; exit 0 ;;
        *) print_error "unknown: $1"; exit 2 ;;
    esac
    shift
done

print_header "Secrets migration → libsecret"

if ! command -v secret-tool >/dev/null 2>&1; then
    print_error "secret-tool not installed. Run: sudo apt install libsecret-tools"
    exit 10
fi

if [[ ! -f "$ENV_FILE" ]]; then
    print_warn "no ${ENV_FILE} — nothing to migrate"
    exit 0
fi

# Parse lines
mapfile -t LINES < <(grep -E '^[A-Z_][A-Z0-9_]*=' "$ENV_FILE" || true)
if [[ ${#LINES[@]} -eq 0 ]]; then
    print_warn "${ENV_FILE} has no KEY=VALUE entries"
    exit 0
fi

migrated=0
skipped=0
failed=0
for line in "${LINES[@]}"; do
    name="${line%%=*}"
    val="${line#*=}"
    val="${val%\"}"; val="${val#\"}"
    val="${val%\'}"; val="${val#\'}"

    # Already in libsecret?
    if secret-tool lookup app ascendo name "$name" >/dev/null 2>&1; then
        print_info "${name}: already in libsecret — skipped"
        skipped=$((skipped + 1))
        continue
    fi

    if [[ $APPLY -eq 0 ]]; then
        print_info "[dry-run] would store: ${name} (${#val} chars)"
        migrated=$((migrated + 1))
        continue
    fi

    print_step "store ${name}"
    if printf '%s' "$val" | secret-tool store \
            --label="ascendo: ${name}" \
            app ascendo name "$name"; then
        print_ok
        migrated=$((migrated + 1))
    else
        print_error "failed"
        failed=$((failed + 1))
    fi
done

print_summary "Migration summary"
echo "  migrated: ${migrated}"
echo "  skipped : ${skipped}"
echo "  failed  : ${failed}"

if [[ $APPLY -eq 1 && $REMOVE_SOURCE -eq 1 && $failed -eq 0 ]]; then
    bak="${ENV_FILE}.bak_$(date +%Y%m%d_%H%M%S)"
    mv "$ENV_FILE" "$bak"
    print_info "moved ${ENV_FILE} → ${bak}"
fi

[[ $failed -eq 0 ]] || exit 1
