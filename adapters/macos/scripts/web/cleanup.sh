#!/usr/bin/env bash
# =============================================================================
# adapters/macos/scripts/web/cleanup.sh -- prune download cache
# =============================================================================
# Prunes ~/Library/Caches/Ascendo/web/ of *.dmg / *.zip files older
# than 7 days. Idempotent. No-op when cache dir doesn't exist.
# =============================================================================
set -o pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ADAPTER_LIB="$SCRIPT_DIR/../../lib"

. "$ADAPTER_LIB/ascendo_json.sh"
. "$ADAPTER_LIB/ascendo_web.sh"

RUN_ID=""; TRIGGER=""; PROFILE_NAME=""; OUTPUT_DIR=""; DRY_RUN="false"; FILTER=""
while [ $# -gt 0 ]; do
    case "$1" in
        --run-id)     RUN_ID="$2";       shift 2 ;;
        --trigger)    TRIGGER="$2";      shift 2 ;;
        --profile)    PROFILE_NAME="$2"; shift 2 ;;
        --output-dir) OUTPUT_DIR="$2";   shift 2 ;;
        --dry-run)    DRY_RUN="true";    shift ;;
        --filter)     FILTER="$2";       shift 2 ;;
        *) printf 'cleanup.sh: unknown arg: %s\n' "$1" >&2; exit 2 ;;
    esac
done
if [ -z "$RUN_ID" ] || [ -z "$TRIGGER" ] || [ -z "$PROFILE_NAME" ] || [ -z "$OUTPUT_DIR" ]; then
    printf 'cleanup.sh: missing required args\n' >&2
    exit 2
fi

HOST_NAME="$(scutil --get LocalHostName 2>/dev/null || hostname -s 2>/dev/null || echo unknown)"
HOST_OS="macos"
HOST_OS_VERSION="$(sw_vers -productVersion 2>/dev/null || echo unknown)"
HOST_ARCH="$(uname -m 2>/dev/null || echo unknown)"
HOST_USER="$(whoami 2>/dev/null || echo unknown)"
if [ "${EUID:-$(id -u)}" -eq 0 ]; then HOST_IS_ELEVATED="true"; else HOST_IS_ELEVATED="false"; fi

json_init "cleanup" "web" "$RUN_ID" "$TRIGGER" "$PROFILE_NAME" \
          "ascendo-web" "0.1.0" \
          "$HOST_NAME" "$HOST_OS" "$HOST_OS_VERSION" "$HOST_ARCH" \
          "$HOST_USER" "$HOST_IS_ELEVATED"
trap 'json_save_on_exit "$OUTPUT_DIR"' EXIT

PRUNED=0
if [ -d "$ASCENDO_WEB_CACHE_DIR" ]; then
    while IFS= read -r f; do
        [ -z "$f" ] && continue
        if [ "$DRY_RUN" = "true" ]; then
            json_add_item "web:cache:$(/usr/bin/basename "$f")" "" "" "planned" "web" "cleanup"
        else
            /bin/rm -f "$f" && PRUNED=$((PRUNED + 1))
        fi
    done < <(/usr/bin/find "$ASCENDO_WEB_CACHE_DIR" -type f -mtime +7 \
                  \( -name '*.dmg' -o -name '*.zip' \) 2>/dev/null)
fi

json_add_message "info" "web cleanup: pruned ${PRUNED} cache files"
exit 0
