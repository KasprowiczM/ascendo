#!/usr/bin/env bash
# =============================================================================
# lib/secrets.sh — Secret access wrappers (libsecret with .env.local fallback).
#
# Public API:
#   secrets_get  <name>        prints value or empty (exit 0 if found)
#   secrets_set  <name> <val>  stores value (libsecret if available, else .env.local)
#   secrets_has  <name>        exit 0 if value retrievable
#   secrets_list               prints names of known secrets
#
# Storage tiers:
#   1. libsecret via secret-tool   (preferred; encrypted via gnome-keyring/kwallet)
#   2. .env.local KEY=VALUE        (fallback; chmod 0600)
#
# Schema attribute used: app=ascendo, name=<name>
# =============================================================================

SECRETS_PROJECT="ascendo"
SECRETS_ENV_FILE="${SECRETS_ENV_FILE:-${SCRIPT_DIR:-$(pwd)}/.env.local}"

_secrets_have_tool() { command -v secret-tool >/dev/null 2>&1; }

_secrets_env_get() {
    local name="$1"
    [[ -f "$SECRETS_ENV_FILE" ]] || return 1
    local line; line=$(grep -E "^${name}=" "$SECRETS_ENV_FILE" 2>/dev/null | head -1)
    [[ -z "$line" ]] && return 1
    # Strip key= and surrounding quotes
    local val="${line#${name}=}"
    val="${val%\"}"; val="${val#\"}"
    val="${val%\'}"; val="${val#\'}"
    printf '%s' "$val"
}

_secrets_env_set() {
    local name="$1" val="$2"
    mkdir -p "$(dirname "$SECRETS_ENV_FILE")"
    touch "$SECRETS_ENV_FILE"
    chmod 0600 "$SECRETS_ENV_FILE" 2>/dev/null || true
    if grep -qE "^${name}=" "$SECRETS_ENV_FILE" 2>/dev/null; then
        # In-place replace
        local tmp; tmp=$(mktemp)
        awk -v n="$name" -v v="$val" 'BEGIN{FS=OFS="="}
            $1==n { print n "=" v; next }
            { print }' "$SECRETS_ENV_FILE" > "$tmp"
        mv "$tmp" "$SECRETS_ENV_FILE"
        chmod 0600 "$SECRETS_ENV_FILE" 2>/dev/null || true
    else
        printf '%s=%s\n' "$name" "$val" >> "$SECRETS_ENV_FILE"
    fi
}

secrets_get() {
    local name="$1"
    if _secrets_have_tool; then
        local val
        val=$(secret-tool lookup app "$SECRETS_PROJECT" name "$name" 2>/dev/null || true)
        if [[ -n "$val" ]]; then
            printf '%s' "$val"
            return 0
        fi
    fi
    _secrets_env_get "$name"
}

secrets_set() {
    local name="$1" val="$2"
    if _secrets_have_tool; then
        printf '%s' "$val" | secret-tool store \
            --label="${SECRETS_PROJECT}: ${name}" \
            app "$SECRETS_PROJECT" name "$name"
        return $?
    fi
    _secrets_env_set "$name" "$val"
}

secrets_has() {
    local v; v=$(secrets_get "$1") || return 1
    [[ -n "$v" ]]
}

secrets_list() {
    if _secrets_have_tool; then
        secret-tool search --all app "$SECRETS_PROJECT" 2>/dev/null \
            | awk -F'= *' '/^attribute.name = /{print $2}' \
            | sort -u
    fi
    if [[ -f "$SECRETS_ENV_FILE" ]]; then
        grep -oE '^[A-Z_][A-Z0-9_]*=' "$SECRETS_ENV_FILE" 2>/dev/null \
            | tr -d '=' | sort -u
    fi
}
