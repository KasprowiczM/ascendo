#!/usr/bin/env bash
# =============================================================================
# adapters/macos/scripts/mas/verify.sh -- read-only Mac App Store verify phase
# =============================================================================
# Reads sibling apply__mas.json, re-runs mas_outdated_json, and marks each
# apply success item as verify success or failed depending on whether it is
# still outdated. Soft no-op (exit 0, success sidecar) when apply sidecar
# is absent (verify can run after check-only).
#
# Args:
#   --run-id ID --trigger TRIG --profile NAME --output-dir DIR
#   [--dry-run]   (no-op; accepted for build_argv parity)
#
# Exit codes:
#   0  success (even on per-item verify failures; status encoded in sidecar)
#   2  bad usage (missing required args / unknown flag)
# =============================================================================
set -o pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ADAPTER_LIB="$SCRIPT_DIR/../../lib"

# shellcheck source=../../lib/ascendo_json.sh
. "$ADAPTER_LIB/ascendo_json.sh"
# shellcheck source=../../lib/ascendo_mas.sh
. "$ADAPTER_LIB/ascendo_mas.sh"

# -- arg parsing ---------------------------------------------------------------
RUN_ID=""
TRIGGER=""
PROFILE_NAME=""
OUTPUT_DIR=""
DRY_RUN=0

while [ $# -gt 0 ]; do
    case "$1" in
        --run-id)     RUN_ID="$2";       shift 2 ;;
        --trigger)    TRIGGER="$2";      shift 2 ;;
        --profile)    PROFILE_NAME="$2"; shift 2 ;;
        --output-dir) OUTPUT_DIR="$2";   shift 2 ;;
        --dry-run)    DRY_RUN=1;         shift ;;
        *) printf 'verify.sh: unknown arg: %s\n' "$1" >&2; exit 2 ;;
    esac
done

if [ -z "$RUN_ID" ] || [ -z "$TRIGGER" ] || [ -z "$PROFILE_NAME" ] || [ -z "$OUTPUT_DIR" ]; then
    printf 'verify.sh: missing required args (--run-id, --trigger, --profile, --output-dir)\n' >&2
    exit 2
fi

# -- host info -----------------------------------------------------------------
HOST_NAME="$(scutil --get ComputerName 2>/dev/null || hostname 2>/dev/null || echo unknown)"
HOST_OS="macos"
HOST_OS_VERSION="$(sw_vers -productVersion 2>/dev/null || echo unknown)"
HOST_ARCH="$(uname -m 2>/dev/null || echo unknown)"
HOST_USER="$(whoami 2>/dev/null || echo unknown)"
if [ "${EUID:-$(id -u)}" -eq 0 ]; then
    HOST_IS_ELEVATED="true"
else
    HOST_IS_ELEVATED="false"
fi

# -- tool info -----------------------------------------------------------------
MAS_VER="$("$MAS_BIN" version 2>/dev/null || echo unknown)"
if [ -z "$MAS_VER" ]; then
    MAS_VER="unknown"
fi

# -- init sidecar --------------------------------------------------------------
json_init "verify" "mas" "$RUN_ID" "$TRIGGER" "$PROFILE_NAME" \
          "mas" "$MAS_VER" \
          "$HOST_NAME" "$HOST_OS" "$HOST_OS_VERSION" "$HOST_ARCH" \
          "$HOST_USER" "$HOST_IS_ELEVATED"

# Install EXIT trap AFTER json_init (bufdir is now valid)
trap 'json_save_on_exit "$OUTPUT_DIR"' EXIT

# -- locate sibling apply sidecar ----------------------------------------------
APPLY_SIDECAR="${OUTPUT_DIR}/${RUN_ID}/apply__mas.json"

if [ ! -f "$APPLY_SIDECAR" ]; then
    # Soft no-op: verify can run after check-only. Emit info and exit 0.
    json_add_message "info" "apply__mas.json not found; no apply sidecar to verify against. Skipping."
    exit 0
fi

# -- sign-in probe (must be signed in to re-query) ----------------------------
if ! mas_signed_in; then
    json_add_message "error" "Not signed into Mac App Store. Cannot re-query outdated status."
    exit 0
fi

# -- extract apply success items (ids where status == "success") ---------------
APPLY_SUCCESS_IDS="$(python3 - "$APPLY_SIDECAR" <<'PYEOF'
import sys, json
try:
    data = json.loads(open(sys.argv[1]).read())
    ids = [i["id"] for i in data.get("items", []) if i.get("status") == "success"]
    print("\n".join(ids))
except Exception:
    pass
PYEOF
)"

if [ -z "$APPLY_SUCCESS_IDS" ]; then
    json_add_message "info" "No successful apply items found in apply__mas.json; nothing to verify."
    exit 0
fi

# -- re-run outdated query to build still-outdated set -------------------------
OUTDATED_JSON="$(mas_outdated_json)"

# Build space-delimited string of still-outdated ids (Bash 3.2-safe, no arrays)
STILL_OUTDATED=" $(printf '%s' "$OUTDATED_JSON" | jq -r '.[].id' 2>/dev/null | tr '\n' ' ') "

# -- emit one verify item per apply success id ---------------------------------
printf '%s\n' "$APPLY_SUCCESS_IDS" | while IFS= read -r _id; do
    [ -n "$_id" ] || continue
    # Look up the resolved version from apply sidecar
    RESOLVED="$(python3 - "$APPLY_SIDECAR" "$_id" <<'PYEOF'
import sys, json
try:
    data = json.loads(open(sys.argv[1]).read())
    for i in data.get("items", []):
        if i["id"] == sys.argv[2]:
            print(i.get("resolved_version") or i.get("target_version") or "")
            break
except Exception:
    pass
PYEOF
)"
    case "$STILL_OUTDATED" in
        (*" $_id "*) json_add_item "$_id" "" "" "failed" "mas" ;;
        (*)          json_add_item "$_id" "" "$RESOLVED" "success" "mas" ;;
    esac
done

exit 0
