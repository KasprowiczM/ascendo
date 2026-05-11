#!/usr/bin/env bash
# =============================================================================
# adapters/ubuntu/scripts/inventory/list.sh -- read-only Linux package inventory
# =============================================================================
# Enumerates installed packages across six Ubuntu/Linux sources and emits a
# single ascendo/v1 sidecar at <output-dir>/<run-id>/check__inventory.json
# covering items[] from every source that's present.
#
# Sources enumerated (each gracefully skipped if its tool is missing):
#   1. apt/dpkg     -- `dpkg-query -W -f='${Package}\t${Version}\t${Status}\n'`
#                      filtered to status `install ok installed`
#   2. snap         -- `snap list` (skip header row)
#   3. flatpak      -- `flatpak list --columns=application,version`
#   4. brew         -- Linuxbrew at /home/linuxbrew/.linuxbrew or user $HOME
#                      `brew list --formula --versions` + `brew list --cask --versions`
#   5. npm globals  -- `npm list -g --depth=0 --json` (parsed via python)
#   6. pip globals  -- `pip3 list --format=json` and (if separate) brew-pip
#
# Per-tool 10s timeout via /usr/bin/timeout (skipped silently if absent).
# Bash 4+ assumed (Ubuntu 20.04+ ships bash 5).
#
# Env overrides for tests (each takes a path to a fake binary; empty = skip):
#   DPKG_BIN, SNAP_BIN, FLATPAK_BIN, BREW_BIN, NPM_BIN, PIP_BIN, TIMEOUT_BIN
#
# Args:
#   --run-id ID --trigger TRIG --profile NAME --output-dir DIR
#   [--dry-run]   (no-op for inventory; accepted for IPC parity)
#
# Exit codes (per docs/agents/contract.md):
#   0   success (sidecar emitted, even if some sources had errors)
#   2   bad usage (missing required args / unknown flag)
#   30  unrecoverable (Python missing -- can't synthesise sidecar)
# =============================================================================
set -u
set -o pipefail

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
        *) printf 'list.sh: unknown arg: %s\n' "$1" >&2; exit 2 ;;
    esac
done

if [ -z "$RUN_ID" ] || [ -z "$TRIGGER" ] || [ -z "$PROFILE_NAME" ] || [ -z "$OUTPUT_DIR" ]; then
    printf 'list.sh: missing required args (--run-id, --trigger, --profile, --output-dir)\n' >&2
    exit 2
fi

# -- host info -----------------------------------------------------------------
HOST_NAME="$(hostname 2>/dev/null || echo unknown)"
HOST_OS="linux_other"
HOST_OS_VERSION="unknown"
if [ -r /etc/os-release ]; then
    # shellcheck disable=SC1091
    . /etc/os-release 2>/dev/null || true
    case "${ID:-}" in
        ubuntu) HOST_OS="linux_ubuntu" ;;
        debian) HOST_OS="linux_debian" ;;
        fedora) HOST_OS="linux_fedora" ;;
        arch)   HOST_OS="linux_arch"   ;;
    esac
    HOST_OS_VERSION="${VERSION_ID:-unknown}"
fi
HOST_ARCH="$(uname -m 2>/dev/null || echo unknown)"
HOST_USER="$(whoami 2>/dev/null || echo unknown)"
if [ "${EUID:-$(id -u 2>/dev/null || echo 1000)}" -eq 0 ]; then
    HOST_IS_ELEVATED="true"
else
    HOST_IS_ELEVATED="false"
fi

# -- resolve binaries (env-overridable for tests) ------------------------------
# Convention mirrors macOS list.sh: explicit empty value = disabled,
# unset = autodiscover.
_resolve_bin() {
    # _resolve_bin VARNAME defaultname [defaultname2 ...]
    local _varname="$1"
    shift
    if [ "${!_varname+set}" = "set" ]; then
        # explicit override
        if [ -n "${!_varname}" ] && [ -x "${!_varname}" ]; then
            printf '%s' "${!_varname}"
        fi
        return 0
    fi
    local _candidate
    for _candidate in "$@"; do
        if command -v "$_candidate" >/dev/null 2>&1; then
            command -v "$_candidate"
            return 0
        fi
    done
}

DPKG_RESOLVED="$(_resolve_bin DPKG_BIN dpkg-query)"
SNAP_RESOLVED="$(_resolve_bin SNAP_BIN snap)"
FLATPAK_RESOLVED="$(_resolve_bin FLATPAK_BIN flatpak)"
BREW_RESOLVED="$(_resolve_bin BREW_BIN brew)"
NPM_RESOLVED="$(_resolve_bin NPM_BIN npm)"
PIP_RESOLVED="$(_resolve_bin PIP_BIN pip3 pip)"
TIMEOUT_RESOLVED="$(_resolve_bin TIMEOUT_BIN timeout)"

# -- helper: run a command with optional 10s timeout ---------------------------
_run_with_timeout() {
    if [ -n "$TIMEOUT_RESOLVED" ]; then
        "$TIMEOUT_RESOLVED" 10 "$@"
    else
        "$@"
    fi
}

# -- collect items into a TSV staging file -------------------------------------
# Format per line (TAB-separated):
#   id<TAB>name<TAB>source_type<TAB>current_version<TAB>vendor<TAB>feed
# id is "<source>:<name>" so it's globally unique across sources.
STAGE_FILE="$(mktemp -t ascendo-inv-stage.XXXXXX)"
MSG_FILE="$(mktemp -t ascendo-inv-msgs.XXXXXX)"
trap 'rm -f "$STAGE_FILE" "$MSG_FILE"' EXIT

_emit_item() {
    # _emit_item id name source_type current_version vendor feed
    printf '%s\t%s\t%s\t%s\t%s\t%s\n' \
        "${1//$'\t'/ }" "${2//$'\t'/ }" "${3//$'\t'/ }" \
        "${4//$'\t'/ }" "${5//$'\t'/ }" "${6//$'\t'/ }" \
        >>"$STAGE_FILE"
}

_emit_msg() {
    # _emit_msg level text
    printf '%s\t%s\n' "$1" "${2//$'\t'/ }" >>"$MSG_FILE"
}

# -- 1. apt/dpkg ---------------------------------------------------------------
APT_COUNT=0
if [ -n "$DPKG_RESOLVED" ]; then
    DPKG_OUT="$(_run_with_timeout "$DPKG_RESOLVED" -W \
        -f='${Package}\t${Version}\t${Status}\t${Maintainer}\n' 2>/dev/null || true)"
    while IFS=$'\t' read -r _pkg _ver _status _maint; do
        [ -z "$_pkg" ] && continue
        # Status field is a 3-tuple "want eflag status"; we want the third token = "installed".
        case "$_status" in
            *\ installed) ;;
            *) continue ;;
        esac
        _emit_item "apt:$_pkg" "$_pkg" "apt" "$_ver" "$_maint" "dpkg"
        APT_COUNT=$((APT_COUNT + 1))
    done <<<"$DPKG_OUT"
    _emit_msg "info" "apt: ${APT_COUNT} packages enumerated via dpkg-query"
else
    _emit_msg "info" "apt: dpkg-query not found on PATH (skipped)"
fi

# -- 2. snap -------------------------------------------------------------------
SNAP_COUNT=0
if [ -n "$SNAP_RESOLVED" ]; then
    SNAP_OUT="$(_run_with_timeout "$SNAP_RESOLVED" list 2>/dev/null || true)"
    # Header: Name  Version  Rev  Tracking  Publisher  Notes
    _seen_header=0
    while IFS= read -r _line; do
        [ -z "$_line" ] && continue
        if [ "$_seen_header" -eq 0 ]; then
            _seen_header=1
            continue
        fi
        # Parse whitespace-separated columns; awk is the safest tool here.
        _name="$(printf '%s' "$_line" | awk '{print $1}')"
        _ver="$(printf '%s'  "$_line" | awk '{print $2}')"
        _publisher="$(printf '%s' "$_line" | awk '{print $5}')"
        [ -z "$_name" ] && continue
        _emit_item "snap:$_name" "$_name" "snap" "$_ver" "$_publisher" "snap"
        SNAP_COUNT=$((SNAP_COUNT + 1))
    done <<<"$SNAP_OUT"
    _emit_msg "info" "snap: ${SNAP_COUNT} snaps enumerated"
else
    _emit_msg "info" "snap: not installed (skipped)"
fi

# -- 3. flatpak ----------------------------------------------------------------
FLATPAK_COUNT=0
if [ -n "$FLATPAK_RESOLVED" ]; then
    # --columns gives us a deterministic TAB-separated output with no header.
    FLATPAK_OUT="$(_run_with_timeout "$FLATPAK_RESOLVED" list \
        --columns=application,version,origin 2>/dev/null || true)"
    while IFS=$'\t' read -r _appid _ver _origin; do
        [ -z "$_appid" ] && continue
        _emit_item "flatpak:$_appid" "$_appid" "flatpak" "$_ver" "$_origin" "flatpak"
        FLATPAK_COUNT=$((FLATPAK_COUNT + 1))
    done <<<"$FLATPAK_OUT"
    _emit_msg "info" "flatpak: ${FLATPAK_COUNT} apps enumerated"
else
    _emit_msg "info" "flatpak: not installed (skipped)"
fi

# -- 4. brew (Linuxbrew) -------------------------------------------------------
BREW_F_COUNT=0
BREW_C_COUNT=0
if [ -n "$BREW_RESOLVED" ]; then
    BREW_FORMULA_OUT="$(_run_with_timeout "$BREW_RESOLVED" list --formula --versions 2>/dev/null || true)"
    while IFS= read -r _line; do
        [ -z "$_line" ] && continue
        _name="$(printf '%s' "$_line" | awk '{print $1}')"
        _ver="$(printf '%s'  "$_line" | awk '{print $2}')"
        [ -z "$_name" ] && continue
        _emit_item "brew:$_name" "$_name" "brew_formula" "$_ver" "Homebrew" "homebrew/core"
        BREW_F_COUNT=$((BREW_F_COUNT + 1))
    done <<<"$BREW_FORMULA_OUT"

    BREW_CASK_OUT="$(_run_with_timeout "$BREW_RESOLVED" list --cask --versions 2>/dev/null || true)"
    while IFS= read -r _line; do
        [ -z "$_line" ] && continue
        _name="$(printf '%s' "$_line" | awk '{print $1}')"
        _ver="$(printf '%s'  "$_line" | awk '{print $2}')"
        [ -z "$_name" ] && continue
        _emit_item "brew:$_name" "$_name" "brew_cask" "$_ver" "Homebrew" "homebrew/cask"
        BREW_C_COUNT=$((BREW_C_COUNT + 1))
    done <<<"$BREW_CASK_OUT"
    _emit_msg "info" "brew: ${BREW_F_COUNT} formulae, ${BREW_C_COUNT} casks enumerated"
else
    _emit_msg "info" "brew: not installed (skipped)"
fi

# -- 5. npm globals ------------------------------------------------------------
NPM_COUNT=0
if [ -n "$NPM_RESOLVED" ]; then
    NPM_OUT="$(_run_with_timeout "$NPM_RESOLVED" list -g --depth=0 --json 2>/dev/null || true)"
    if [ -n "$NPM_OUT" ]; then
        # Parse JSON via python. Use -c (script as arg) so stdin is free
        # for the data. The heredoc + dash form `python3 - <<'PY'` collides
        # with `printf | python3` because both try to feed stdin: python's
        # `-` reads the script from stdin via the heredoc, then
        # `json.load(sys.stdin)` gets EOF and silently emits nothing.
        _parsed="$(printf '%s' "$NPM_OUT" | python3 -c '
import json, sys
try:
    data = json.loads(sys.stdin.read() or "{}")
except Exception:
    sys.exit(0)
deps = data.get("dependencies") or {}
for name, info in deps.items():
    ver = info.get("version", "") if isinstance(info, dict) else ""
    print(f"{name}\t{ver}")
' 2>/dev/null)" || _parsed=""
        while IFS=$'\t' read -r _name _ver; do
            [ -z "$_name" ] && continue
            _emit_item "npm:$_name" "$_name" "npm" "$_ver" "" "npm-global"
            NPM_COUNT=$((NPM_COUNT + 1))
        done <<<"$_parsed"
    fi
    _emit_msg "info" "npm: ${NPM_COUNT} globals enumerated"
else
    _emit_msg "info" "npm: not installed (skipped)"
fi

# -- 6. pip globals ------------------------------------------------------------
PIP_COUNT=0
if [ -n "$PIP_RESOLVED" ]; then
    PIP_OUT="$(_run_with_timeout "$PIP_RESOLVED" list --format=json 2>/dev/null || true)"
    if [ -n "$PIP_OUT" ]; then
        # Same heredoc-vs-pipe fix as the npm block above.
        _parsed="$(printf '%s' "$PIP_OUT" | python3 -c '
import json, sys
try:
    data = json.loads(sys.stdin.read() or "[]")
except Exception:
    sys.exit(0)
if not isinstance(data, list):
    sys.exit(0)
for entry in data:
    if not isinstance(entry, dict):
        continue
    name = entry.get("name") or ""
    ver = entry.get("version") or ""
    if name:
        print(f"{name}\t{ver}")
' 2>/dev/null)" || _parsed=""
        while IFS=$'\t' read -r _name _ver; do
            [ -z "$_name" ] && continue
            _emit_item "pip:$_name" "$_name" "pip" "$_ver" "" "pypi"
            PIP_COUNT=$((PIP_COUNT + 1))
        done <<<"$_parsed"
    fi
    _emit_msg "info" "pip: ${PIP_COUNT} packages enumerated"
else
    _emit_msg "info" "pip: not installed (skipped)"
fi

# -- assemble + emit sidecar ---------------------------------------------------
SIDECAR_DIR="$OUTPUT_DIR/$RUN_ID"
mkdir -p "$SIDECAR_DIR" 2>/dev/null || {
    printf 'list.sh: cannot create %s\n' "$SIDECAR_DIR" >&2
    exit 30
}
SIDECAR_PATH="$SIDECAR_DIR/check__inventory.json"

if ! command -v python3 >/dev/null 2>&1; then
    printf 'list.sh: python3 not on PATH; cannot synthesise sidecar\n' >&2
    exit 30
fi

# Pass everything via env vars to keep heredoc clean.
export ASC_RUN_ID="$RUN_ID"
export ASC_TRIGGER="$TRIGGER"
export ASC_PROFILE="$PROFILE_NAME"
export ASC_HOST_NAME="$HOST_NAME"
export ASC_HOST_OS="$HOST_OS"
export ASC_HOST_OS_VERSION="$HOST_OS_VERSION"
export ASC_HOST_ARCH="$HOST_ARCH"
export ASC_HOST_USER="$HOST_USER"
export ASC_HOST_IS_ELEVATED="$HOST_IS_ELEVATED"
export ASC_DRY_RUN="$DRY_RUN"
export ASC_STAGE_FILE="$STAGE_FILE"
export ASC_MSG_FILE="$MSG_FILE"
export ASC_OUT_PATH="$SIDECAR_PATH"

python3 - <<'PY' || exit 30
import json
import os
from datetime import datetime, timezone

now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

items = []
stage = os.environ.get("ASC_STAGE_FILE", "")
if stage and os.path.isfile(stage):
    with open(stage, "r", encoding="utf-8") as fh:
        for raw in fh:
            line = raw.rstrip("\n")
            if not line:
                continue
            parts = line.split("\t")
            while len(parts) < 6:
                parts.append("")
            iid, name, src_type, cur_ver, vendor, feed = parts[:6]
            if not iid or not name or not src_type:
                continue
            item = {
                "id": iid,
                "name": name,
                "category": "inventory",
                "source": {
                    "type": src_type,
                    "feed": feed or None,
                },
                "current_version": cur_ver or None,
                "target_version": cur_ver or None,
                "status": "up_to_date",
            }
            # vendor is collected by bash but kept out of the sidecar
            # `items[]` (Item model has no vendor field). The Python
            # adapter (UbuntuInventory._sidecar_to_packages) re-stitches
            # vendor onto Package only when it has a separate channel —
            # for now vendor is informational in the bash log only.
            _ = vendor
            items.append(item)

messages = []
msg_file = os.environ.get("ASC_MSG_FILE", "")
if msg_file and os.path.isfile(msg_file):
    with open(msg_file, "r", encoding="utf-8") as fh:
        for raw in fh:
            line = raw.rstrip("\n")
            if not line:
                continue
            parts = line.split("\t", 1)
            if len(parts) != 2:
                continue
            level, text = parts
            if level not in ("info", "warn", "error"):
                level = "info"
            messages.append({"level": level, "text": text})

total = len(items)
sidecar = {
    "schema": "ascendo/v1",
    "run": {
        "id": os.environ["ASC_RUN_ID"],
        "trigger": os.environ.get("ASC_TRIGGER", "cli"),
        "profile": os.environ.get("ASC_PROFILE", "default"),
        "dry_run": os.environ.get("ASC_DRY_RUN", "0") == "1",
        "started_at": now,
    },
    "host": {
        "hostname": os.environ.get("ASC_HOST_NAME", "unknown"),
        "os": os.environ.get("ASC_HOST_OS", "linux_other"),
        "os_version": os.environ.get("ASC_HOST_OS_VERSION", "unknown"),
        "arch": os.environ.get("ASC_HOST_ARCH", "unknown"),
        "user": os.environ.get("ASC_HOST_USER", "unknown"),
        "is_elevated": os.environ.get("ASC_HOST_IS_ELEVATED", "false") == "true",
        "elevation_method": "none",
    },
    "tool": {"name": "ubuntu-inventory", "version": "1.0"},
    "phase": "check",
    "category": "inventory",
    "started_at": now,
    "finished_at": now,
    "status": "success",
    "items": items,
    "summary": {
        "total": total,
        "success": 0,
        "up_to_date": total,
        "failed": 0,
        "skipped": 0,
        "planned": 0,
        "partial": 0,
        "exit_code": 0,
    },
    "messages": messages,
    "needs_reboot": False,
}

out_path = os.environ["ASC_OUT_PATH"]
with open(out_path, "w", encoding="utf-8") as fh:
    json.dump(sidecar, fh, indent=2)
PY

exit 0
