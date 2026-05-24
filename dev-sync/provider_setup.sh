#!/usr/bin/env bash
# =============================================================================
# provider_setup.sh — Configure dev-sync private overlay provider
#
# Ascendo policy:
# - GitHub stores tracked project files.
# - Provider stores only Git-ignored private overlay files.
# - Prefer rclone for Proton Drive on Linux; local provider is supported for a
#   manually mounted/synced folder.
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG_FILE="${SCRIPT_DIR}/.dev_sync_config.json"
PROJECT_NAME="$(basename "${SCRIPT_DIR}")"

BOLD='\033[1m'; GREEN='\033[32m'; RED='\033[31m'; YELLOW='\033[33m'; BLUE='\033[34m'; RESET='\033[0m'

info() { printf "  %s\n" "$*"; }
ok() { printf "${GREEN}✓${RESET} %s\n" "$*"; }
warn() { printf "${YELLOW}!${RESET} %s\n" "$*"; }
err() { printf "${RED}✗${RESET} %s\n" "$*" >&2; }
header() { printf "\n${BOLD}${BLUE}%s${RESET}\n" "$1"; printf '%.0s─' {1..60}; printf '\n'; }

have_rclone() { command -v rclone >/dev/null 2>&1; }
list_rclone_remotes() { rclone listremotes 2>/dev/null || true; }

detect_local_proton_path() {
    for path in \
        "$HOME/Proton Drive" \
        "$HOME/ProtonDrive" \
        "$HOME/Proton Drive - Personal" \
        "$HOME/Proton Drive - Business" \
        "$HOME/Library/CloudStorage"/*Proton*; do
        [ -d "$path" ] && { printf '%s\n' "$path"; return 0; }
    done
    return 1
}

json_escape() {
    python3 -c 'import json,sys; print(json.dumps(sys.argv[1])[1:-1])' "$1"
}

write_config() {
    local provider="$1" provider_path="$2" remote="$3" remote_path="$4" project="$5" proton_root="$6"
    cat > "$CONFIG_FILE" <<EOF_JSON
{
  "project_name": "$(json_escape "$project")",
  "provider": "$(json_escape "$provider")",
  "provider_path": "$(json_escape "$provider_path")",
  "rclone_remote": "$(json_escape "$remote")",
  "rclone_remote_path": "$(json_escape "$remote_path")",
  "proton_project_root": "$(json_escape "$proton_root")",
  "exclude_patterns": [],
  "include_always": []
}
EOF_JSON
}

main() {
    header "Dev Sync Provider Setup"
    info "Project: ${PROJECT_NAME}"
    info "Config : ${CONFIG_FILE}"
    echo
    info "Use rclone for Proton Drive on Linux unless you have a verified local Proton Drive folder."
    info "Run 'rclone config' first if your Proton remote is not configured."

    local proton_path=""
    proton_path="$(detect_local_proton_path || true)"

    echo
    echo "Providers:"
    if have_rclone; then
        printf "  ${GREEN}1)${RESET} rclone remote      ${GREEN}available${RESET}\n"
    else
        printf "  ${RED}1)${RESET} rclone remote      ${RED}not installed${RESET}\n"
    fi
    if [ -n "$proton_path" ]; then
        printf "  ${GREEN}2)${RESET} local Proton path  ${GREEN}%s${RESET}\n" "$proton_path"
    else
        printf "  ${YELLOW}2)${RESET} local Proton path  not auto-detected\n"
    fi
    printf "  ${BLUE}3)${RESET} local/custom path\n"
    echo

    local choice provider provider_path remote remote_path project proton_root
    read -r -p "Select provider [1-3]: " choice
    provider=""; provider_path=""; remote=""; remote_path=""; proton_root=""

    case "${choice// /}" in
        1)
            have_rclone || { err "rclone is not installed"; return 1; }
            provider="rclone"
            echo "Configured rclone remotes:"
            list_rclone_remotes
            echo
            read -r -p "Enter rclone remote name [protondrive]: " remote
            remote="${remote:-protondrive}"
            remote="${remote%:}"
            read -r -p "Enter path inside remote [Dev_Env]: " remote_path
            remote_path="${remote_path:-Dev_Env}"
            ;;
        2)
            if [ -z "$proton_path" ]; then
                read -r -p "Enter full local Proton Drive path: " proton_path
            fi
            [ -d "$proton_path" ] || { err "Path does not exist: $proton_path"; return 1; }
            provider="protondrive"
            provider_path="$proton_path"
            proton_root="$proton_path/Dev_Env/$PROJECT_NAME"
            ;;
        3)
            provider="local"
            read -r -p "Enter full local provider folder path: " provider_path
            [ -d "$provider_path" ] || { err "Path does not exist: $provider_path"; return 1; }
            ;;
        *)
            err "Invalid provider choice"
            return 1
            ;;
    esac

    read -r -p "Project folder name in provider [${PROJECT_NAME}]: " project
    project="${project:-$PROJECT_NAME}"

    header "Configuration Summary"
    info "Provider          : $provider"
    [ -n "$provider_path" ] && info "Provider path     : $provider_path"
    [ -n "$remote" ] && info "rclone remote     : $remote"
    [ -n "$remote_path" ] && info "rclone remote path: $remote_path"
    [ -n "$proton_root" ] && info "Proton project root: $proton_root"
    info "Project folder    : $project"
    echo
    warn "This writes only .dev_sync_config.json. It does not upload or delete files."
    read -r -p "Save configuration? [y/N]: " confirm
    case "$confirm" in
        y|Y|yes|YES) ;;
        *) err "Configuration cancelled"; return 1 ;;
    esac

    write_config "$provider" "$provider_path" "$remote" "$remote_path" "$project" "$proton_root"
    ok "Configuration saved"
    echo
    info "Next commands:"
    info "bash dev-sync-export.sh --dry-run --verbose"
    info "bash dev-sync-export.sh"
    info "bash dev-sync-verify-full.sh"
}

main "$@"
