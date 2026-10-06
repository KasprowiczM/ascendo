#!/usr/bin/env bash
# =============================================================================
# adapters/macos/scripts/npm/apply.sh -- mutating: bootstrap toolchain + upgrade CLIs
# =============================================================================
# For each manifest entry whose status is `missing` or `planned`:
#   * native-node:  install/upgrade Node via `n` under $TOOLCHAIN_HOME/node
#   * native-bun:   `bun upgrade` when installed; the downloaded official
#                   installer (https://bun.sh/install) only when bootstrapping
#   * npm:          `npm install -g <package_name>` under $NPM_GLOBAL_PREFIX
#   * native-installer: claude/codex/agy/cursor-agent -- `<cmd> update` when
#                   installed, else a downloaded (not piped) vendor installer;
#                   success needs a working `--version` (see apply_native_installer)
#   * self-update:  opencode -- NATIVE-FIRST (`opencode upgrade --method curl`,
#                   or the downloaded https://opencode.ai/install); the npm
#                   distribution is only a guarded fallback.
#
# Only-installed model (cli.md P5): a CLI / managed Node / bun that is not
# installed is `skipped` unless ASCENDO_BOOTSTRAP_CLI=1 or the row is named in
# --filter (ascendo_npm_bootstrap_allowed). Never `brew install node` unasked.
#
# Order (two passes over the manifest):
#   pass 1  native-installer rows + OpenCode native-first   (need no Node/npm)
#   pass 2  native-node, npm, self-update (OpenCode npm fallback), native-bun
#
# Every mutating child runs with stdin </dev/null (the manifest is read through
# a process substitution; an installer that reads stdin must not eat the rest
# of the manifest). Sidecar message tails go through ascendo_npm_sanitize_output.
#
# `--dry-run` short-circuits BEFORE any mutation: items emit as `planned`
# and the script exits 0.
# =============================================================================
set -o pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ADAPTER_LIB="$SCRIPT_DIR/../../lib"
. "$ADAPTER_LIB/ascendo_json.sh"
. "$ADAPTER_LIB/ascendo_npm.sh"

RUN_ID=""; TRIGGER=""; PROFILE_NAME=""; OUTPUT_DIR=""; DRY_RUN="false"; FILTER=""
while [ $# -gt 0 ]; do
    case "$1" in
        --run-id)     RUN_ID="$2";       shift 2 ;;
        --trigger)    TRIGGER="$2";      shift 2 ;;
        --profile)    PROFILE_NAME="$2"; shift 2 ;;
        --output-dir) OUTPUT_DIR="$2";   shift 2 ;;
        --dry-run)    DRY_RUN="true";    shift ;;
        --filter)     FILTER="$2";       shift 2 ;;
        *) printf 'apply.sh: unknown arg: %s\n' "$1" >&2; exit 2 ;;
    esac
done
[ -z "$RUN_ID" ] || [ -z "$TRIGGER" ] || [ -z "$PROFILE_NAME" ] || [ -z "$OUTPUT_DIR" ] && { printf 'apply.sh: missing args\n' >&2; exit 2; }

HOST_NAME="$(scutil --get LocalHostName 2>/dev/null || hostname -s 2>/dev/null || echo unknown)"
HOST_OS_VERSION="$(sw_vers -productVersion 2>/dev/null || echo unknown)"
HOST_ARCH="$(uname -m 2>/dev/null || echo unknown)"
HOST_USER="$(whoami 2>/dev/null || echo unknown)"
HOST_IS_ELEVATED="false"; [ "${EUID:-$(id -u)}" -eq 0 ] && HOST_IS_ELEVATED="true"

NPM_BIN="$(ascendo_npm_npm_bin)"
TOOL_VERSION="unknown"
[ -n "$NPM_BIN" ] && TOOL_VERSION="$(_ascendo_npm_invoke "$NPM_BIN" --version 2>/dev/null || echo unknown)"

# Convenience wrapper around _ascendo_npm_invoke that uses the
# script-scope NPM_BIN. Replaces bare `"$NPM_BIN" args` calls so we
# always invoke npm via its sibling node binary.
_run_npm() {
    _ascendo_npm_invoke "$NPM_BIN" "$@"
}

json_init "apply" "npm" "$RUN_ID" "$TRIGGER" "$PROFILE_NAME" \
          "npm" "$TOOL_VERSION" \
          "$HOST_NAME" "macos" "$HOST_OS_VERSION" "$HOST_ARCH" \
          "$HOST_USER" "$HOST_IS_ELEVATED"
trap 'json_save_on_exit "$OUTPUT_DIR"' EXIT

in_filter() {
    [ -z "$FILTER" ] && return 0
    case ",$FILTER," in (*",$1,"*) return 0 ;; (*) return 1 ;; esac
}

# classify <installed> <latest>  -> up_to_date | planned | missing
# Mirrors the rule used in check.sh: empty installed -> missing,
# empty latest (network probe failed) -> up_to_date (don't punish on
# probe failure), installed == latest -> up_to_date, semver-sort says
# installed >= latest -> up_to_date, otherwise planned.
classify() {
    local _installed="$1"
    local _latest="$2"
    if [ -z "$_installed" ]; then printf 'missing'; return; fi
    if [ -z "$_latest" ];    then printf 'up_to_date'; return; fi
    if [ "$_installed" = "$_latest" ]; then printf 'up_to_date'; return; fi
    # W11: Use Python version comparison instead of sort -V
    if python3 -c "import sys; from ascendo.utils.version import version_gt; sys.exit(0 if version_gt(sys.argv[1], sys.argv[2]) else 1)" "$_installed" "$_latest" 2>/dev/null; then
        printf 'up_to_date'; return
    fi
    printf 'planned'
}

# Prime the `npm ls -g` cache once so per-package lookups in apply_npm
# are O(1) jq queries instead of O(N) npm spawns. Saves ~100ms per pkg
# on the up_to_date guard.
ascendo_npm_prime_installed_cache

# -- bootstrap npm-global prefix (idempotent, safe to repeat) -----------------
# We deliberately do NOT write the prefix into ~/.npmrc (legacy bug:
# `npm config set prefix` did exactly that, and nvm refuses to load
# Node when ~/.npmrc carries `prefix=` or `globalconfig=`). Use
# NPM_CONFIG_PREFIX env var instead — precedence env > .npmrc — and
# scrub any stale lines from a prior install once per apply run so an
# old `prefix=` line written by another tool can't keep tripping nvm.
TOOLCHAIN_HOME="$(ascendo_npm_toolchain_home)"
NPM_GLOBAL_PREFIX="$(ascendo_npm_global_prefix)"
mkdir -p "$NPM_GLOBAL_PREFIX/bin" 2>/dev/null || true
ascendo_npm_scrub_npmrc || true
export NPM_CONFIG_PREFIX="$NPM_GLOBAL_PREFIX"

# Sesja 53 + 2026-08-25: extend PATH so native CLIs in ~/.local/bin win
# over npm-global stubs, the managed node prefix wins over nvm, and npm
# postinstall subshells can find node + bun. Order matches macOS_updates.
_pref_path="$(ascendo_npm_preferred_path 2>/dev/null || true)"
if [ -n "$_pref_path" ]; then
    export PATH="$_pref_path:$PATH"
fi


# -- shared helpers -------------------------------------------------------------
# Bounded, tab-free, credential-redacted tail of a captured log for sidecar
# messages (cli.md P8). Redact BEFORE capping so a secret is never cut in half.
_native_log_tail() {
    tail -n 12 "$1" 2>/dev/null | tr '\t' ' ' | awk 'NF{print}' \
        | ascendo_npm_sanitize_output | head -c 1500 || true
}

# Only-installed model (cli.md P5): the row is not installed and nobody asked
# for it, so it is skipped -- never `failed`, never installed behind the
# user's back.
_skip_not_installed() {   # display method [message]
    json_add_item "$1" "" "" "skipped" "npm" "$2"
    json_add_message "info" "${3:-$1 is not installed — skipping (set ASCENDO_BOOTSTRAP_CLI=1 or select it explicitly to install)}"
}

# -- per-method handlers ------------------------------------------------------
apply_native_node() {
    local _display="$1"
    if [ "$DRY_RUN" = "true" ]; then
        json_add_item "$_display" "" "" "planned" "npm" "native-node"
        return
    fi
    # Only-installed gate: the MANAGED toolchain node ($TOOLCHAIN_HOME/node) is
    # the only thing this row maintains. A system / nvm / brew node must never
    # trigger the managed bootstrap.
    if ! ascendo_npm_managed_node_installed && ! ascendo_npm_bootstrap_allowed "$_display"; then
        _skip_not_installed "$_display" "native-node" \
            "Managed Node toolchain not installed — skipping (set ASCENDO_BOOTSTRAP_CLI=1 or select '$_display' explicitly to bootstrap it)"
        return
    fi
    # Sesja 50 fix — up_to_date guard: skip the bootstrap if Node is
    # already at the latest LTS. Without this, every apply re-runs
    # `npm install -g n` + `n lts` even when nothing has changed.
    local _installed _latest _status
    _installed="$(ascendo_npm_node_installed_version 2>/dev/null)"
    _latest="$(ascendo_npm_node_latest_version 2>/dev/null)"
    _status="$(classify "$_installed" "$_latest")"
    if [ "$_status" = "up_to_date" ]; then
        json_add_item "$_display" "$_installed" "$_latest" "up_to_date" "npm" "native-node"
        return
    fi
    _stream_emit ">>> bootstrapping native-node ($_display)"
    # Need an npm to install `n`; if absent (bootstrap was asked for), fall back
    # to brew's node so we have an npm at all. Never reached unasked.
    if [ -z "$NPM_BIN" ] || [ ! -x "$NPM_BIN" ]; then
        if command -v brew >/dev/null 2>&1; then
            brew install node </dev/null 2>&1 | _stream_tee >/dev/null
            if [ "${PIPESTATUS[0]:-1}" -ne 0 ]; then
                json_add_item "$_display" "" "" "failed" "npm" "native-node"
                json_add_message "error" "node bootstrap failed (brew install node)"
                return
            fi
            NPM_BIN="$(command -v npm 2>/dev/null)"
        else
            json_add_item "$_display" "" "" "failed" "npm" "native-node"
            json_add_message "error" "no npm + no brew; cannot bootstrap Node"
            return
        fi
    fi
    # Install `n` to user prefix, then install the version the picker
    # resolved. check / plan / verify all derive their target from
    # ascendo_npm_node_latest_version; apply MUST install that same
    # version. Sesja 89: this used to hardcode `n lts`, which installed a
    # different version than the picker reported — on a Current-line box
    # apply downgraded Node to LTS and verify (recomputing the Current
    # target) failed every run. Install `$_latest`; fall back to `lts`
    # only when the picker returned nothing (offline / fresh box) so the
    # bootstrap path still works.
    _run_npm install -g n </dev/null 2>&1 | _stream_tee >/dev/null
    if [ "${PIPESTATUS[0]:-1}" -ne 0 ]; then
        json_add_item "$_display" "" "" "failed" "npm" "native-node"
        json_add_message "error" "'npm install -g n' failed"
        return
    fi
    local _N="$NPM_GLOBAL_PREFIX/bin/n"
    [ -x "$_N" ] || _N="$(command -v n 2>/dev/null)"
    if [ -z "$_N" ] || [ ! -x "$_N" ]; then
        json_add_item "$_display" "" "" "failed" "npm" "native-node"
        json_add_message "error" "n CLI not on PATH after install"
        return
    fi
    local _n_arg="$_latest"
    [ -z "$_n_arg" ] && _n_arg="lts"
    N_PREFIX="$(ascendo_npm_n_prefix)" "$_N" "$_n_arg" </dev/null 2>&1 | _stream_tee >/dev/null
    if [ "${PIPESTATUS[0]:-1}" -ne 0 ]; then
        json_add_item "$_display" "" "" "failed" "npm" "native-node"
        json_add_message "error" "'n $_n_arg' failed"
        return
    fi
    local _new="$(ascendo_npm_node_installed_version)"
    json_add_item "$_display" "$_new" "$_new" "success" "npm" "native-node"
}

# bun (cli.md row 17): `bun upgrade` when the managed bun exists; the vendor
# installer -- DOWNLOADED, never piped into a shell -- only when bootstrapping.
apply_native_bun() {
    local _display="$1"
    if [ "$DRY_RUN" = "true" ]; then
        json_add_item "$_display" "" "" "planned" "npm" "native-bun"
        return
    fi
    local _bun_home _bun _managed=0
    _bun_home="$(ascendo_npm_bun_home)"
    _bun="$_bun_home/bin/bun"
    ascendo_npm_bun_managed_installed && _managed=1
    if [ "$_managed" -eq 0 ] && ! ascendo_npm_bootstrap_allowed "$_display"; then
        _skip_not_installed "$_display" "native-bun"
        return
    fi
    # Sesja 50 fix — up_to_date guard: skip when bun is already at the latest
    # published GitHub release.
    local _installed _latest _status _tmp_log _rc _tail _new _inst_tmp
    _installed="$(ascendo_npm_bun_installed_version 2>/dev/null)"
    _latest="$(ascendo_npm_bun_latest_version 2>/dev/null)"
    _status="$(classify "$_installed" "$_latest")"
    if [ "$_status" = "up_to_date" ]; then
        json_add_item "$_display" "$_installed" "$_latest" "up_to_date" "npm" "native-bun"
        return
    fi
    _tmp_log="$(mktemp -t ascendo-bun.XXXXXX 2>/dev/null || mktemp /tmp/ascendo-bun.XXXXXX)"
    if [ "$_managed" -eq 1 ]; then
        _stream_emit ">>> bun upgrade ($_display)"
        ascendo_npm_run_with_timeout 300 "$_bun" upgrade >"$_tmp_log" 2>&1
        _rc=$?
    else
        _stream_emit ">>> bootstrapping native-bun ($_display)"
        if ! command -v curl >/dev/null 2>&1; then
            json_add_item "$_display" "" "" "failed" "npm" "native-bun"
            json_add_message "error" "curl missing; cannot bootstrap bun"
            rm -f "$_tmp_log" 2>/dev/null
            return
        fi
        _inst_tmp="$(mktemp "${TMPDIR:-/tmp}/ascendo-bun-installer.XXXXXX")" || _inst_tmp=""
        if [ -z "$_inst_tmp" ] || ! ascendo_npm_fetch_installer "https://bun.sh/install" "$_inst_tmp"; then
            [ -n "$_inst_tmp" ] && rm -f "$_inst_tmp" "${_inst_tmp}.part" 2>/dev/null
            json_add_item "$_display" "" "" "failed" "npm" "native-bun"
            json_add_message "error" "bun installer download failed (https://bun.sh/install)"
            rm -f "$_tmp_log" 2>/dev/null
            return
        fi
        ascendo_npm_run_with_timeout "$(ascendo_npm_native_installer_timeout)" \
            env BUN_INSTALL="$_bun_home" /bin/bash "$_inst_tmp" >"$_tmp_log" 2>&1
        _rc=$?
        rm -f "$_inst_tmp" 2>/dev/null
    fi
    _tail="$(_native_log_tail "$_tmp_log")"
    rm -f "$_tmp_log" 2>/dev/null
    if [ "$_rc" -ne 0 ]; then
        json_add_item "$_display" "$_installed" "" "failed" "npm" "native-bun"
        if [ "$_rc" -eq 124 ]; then
            json_add_message "error" "$_display: bun update timed out (exit 124)${_tail:+ -- $_tail}"
        else
            json_add_message "error" "$_display: bun update exited $_rc${_tail:+ -- $_tail}"
        fi
        return
    fi
    _new="$(ascendo_npm_bun_installed_version)"
    if [ -z "$_new" ]; then
        json_add_item "$_display" "$_installed" "" "failed" "npm" "native-bun"
        json_add_message "error" "$_display: bun update exited 0 but 'bun --version' prints nothing"
        return
    fi
    json_add_item "$_display" "$_new" "$_new" "success" "npm" "native-bun"
}

apply_npm() {
    local _display="$1"
    local _pkg="$2"
    local _cmd="${3:-}"
    if [ "$DRY_RUN" = "true" ]; then
        json_add_item "$_display" "" "" "planned" "npm" "npm"
        return
    fi
    # Only-installed gate (cli.md P5). No npm at all => nothing to maintain
    # (was `failed`); an npm package that is not in the managed prefix is not
    # ours to install unless it was asked for.
    if [ -z "$NPM_BIN" ] || [ ! -x "$NPM_BIN" ]; then
        if ascendo_npm_bootstrap_allowed "$_display"; then
            json_add_item "$_display" "" "" "failed" "npm" "npm"
            json_add_message "error" "npm not installed; bootstrap node first ($_display)"
        else
            _skip_not_installed "$_display" "npm" \
                "$_display: npm is not installed — skipping (set ASCENDO_BOOTSTRAP_CLI=1 or select it explicitly to bootstrap Node)"
        fi
        return
    fi
    if ! ascendo_npm_bootstrap_allowed "$_display"; then
        if [ "$_pkg" = "npm" ]; then
            ascendo_npm_managed_node_installed || {
                _skip_not_installed "$_display" "npm" \
                    "Managed Node toolchain not installed — skipping $_display (bootstrap to install)"
                return
            }
        elif ! ascendo_npm_pkg_installed "$_pkg" "$_cmd"; then
            _skip_not_installed "$_display" "npm"
            return
        fi
    fi
    # Sesja 50 fix — up_to_date guard. Without this, every apply ran
    # `npm install -g <pkg>` for every manifest entry regardless of
    # whether it was already at latest, generating a wall of
    # "Requirement already satisfied" output and ~1-3s of network
    # round-trips per package.
    local _installed _latest _status
    _installed="$(ascendo_npm_installed_version "$_pkg" 2>/dev/null)"
    _latest="$(ascendo_npm_latest_version "$_pkg" 2>/dev/null)"
    _status="$(classify "$_installed" "$_latest")"
    if [ "$_status" = "up_to_date" ]; then
        json_add_item "$_display" "$_installed" "$_latest" "up_to_date" "npm" "npm"
        return
    fi
    _stream_emit ">>> npm install -g $_pkg ($_display)"
    # Capture combined stdout+stderr to a temp log AND tee to live
    # stream — same pattern as pip apply, so a failure surfaces npm's
    # actual error (registry 404, EACCES, version conflict, etc.) in
    # the sidecar message instead of a bare exit code.
    local _tmp_log
    _tmp_log="$(mktemp -t ascendo-npm-apply.XXXXXX 2>/dev/null || mktemp /tmp/ascendo-npm-apply.XXXXXX)"
    _run_npm install -g "$_pkg" </dev/null 2>&1 | tee "$_tmp_log" | _stream_tee >/dev/null
    local _rc="${PIPESTATUS[0]:-1}"
    if [ "$_rc" -ne 0 ]; then
        json_add_item "$_display" "" "" "failed" "npm" "npm"
        local _tail
        _tail="$(_native_log_tail "$_tmp_log")"
        if [ -n "$_tail" ]; then
            json_add_message "error" "npm install -g $_pkg exited $_rc — last output: $_tail"
        else
            json_add_message "error" "npm install -g $_pkg exited $_rc (no output captured)"
        fi
        rm -f "$_tmp_log" 2>/dev/null
        return
    fi
    rm -f "$_tmp_log" 2>/dev/null
    # Bust the npm-ls cache so the post-install version reflects the
    # version we just installed, not the pre-install snapshot.
    _ASCENDO_NPM_LS_CACHE=""
    _ASCENDO_NPM_LS_CACHED=0
    local _new="$(ascendo_npm_installed_version "$_pkg")"
    if [ -z "$_new" ]; then
        json_add_item "$_display" "" "" "failed" "npm" "npm"
        json_add_message "error" "$_display: npm install -g $_pkg exited 0 but the package is not listed afterwards"
        return
    fi
    # `npm ls` reads package.json, so an npm-12 stub whose postinstall was
    # skipped looks installed while the binary cannot run: measure the binary.
    if [ -n "$_cmd" ] && [ -x "$NPM_GLOBAL_PREFIX/bin/$_cmd" ] \
       && ! ascendo_npm_report_cli_version_or_fail "$_display" "$NPM_GLOBAL_PREFIX/bin/$_cmd" >/dev/null; then
        json_add_item "$_display" "" "" "failed" "npm" "npm"
        json_add_message "error" "$_display: installed but '$_cmd --version' fails (postinstall skipped?)"
        return
    fi
    json_add_item "$_display" "$_new" "$_new" "success" "npm" "npm"
}

# Record the outcome of a native CLI update/install after a verified version.
_native_record_success() {   # display before after
    if [ -n "$2" ] && [ "$2" = "$3" ]; then
        json_add_item "$1" "$3" "$3" "up_to_date" "npm" "native-installer"
    else
        json_add_item "$1" "$2" "$3" "success" "npm" "native-installer"
    fi
}

# Native CLIs (claude, codex, agy, cursor-agent; macOS_updates T10/H4/H5):
#   0. locate the CLI with ascendo_npm_native_command_path (~/.local/bin, then
#      PATH) -- the SAME locator check/verify use, so a PATH-only CLI is
#      updated in place and never gets a second copy. Not found => only-installed
#      gate (skipped unless bootstrap allowed / named in --filter).
#      claude/codex: only a realpath inside the vendor's own install root counts
#      as native (cli_duplicates.supported_native_path, F4); an npm wrapper or a
#      Homebrew binary there is retained untouched (skipped).
#   1. installed  -> `<cmd> update`, never the install script (claude's
#      install.sh runs a TUI that ignores SIGTERM and hung the whole pipeline).
#      A failed update of a CLI that still runs is reported failed for claude
#      and for any CLI that lives outside ~/.local/bin; the others fall through
#      to step 2.
#   2. missing / broken -> vendor installer, DOWNLOADED to a temp file (https
#      only, size cap, non-empty) and exec'd with per-vendor env + args, stdin
#      </dev/null, wall-clock bound; never `curl | sh`.
# Success needs a post-action `--version` that prints a digit-bearing version.
apply_native_installer() {
    local _display="$1"
    local _cmd="$2"
    local _url _path _local _upd _env _bargs _timeout _before _after _rc _tail _tmp_log _installer_tmp _runs=0
    _url="$(ascendo_npm_native_install_url "$_cmd")" || _url=""
    if [ "$DRY_RUN" = "true" ]; then
        json_add_item "$_display" "" "" "planned" "npm" "native-installer"
        return
    fi
    if [ -z "$_url" ]; then
        json_add_item "$_display" "" "" "failed" "npm" "native-installer"
        json_add_message "error" "no native-installer URL for command '$_cmd' ($_display)"
        return
    fi
    _local="$(ascendo_npm_local_bin)/$_cmd"
    _path="$(ascendo_npm_native_command_path "$_cmd")"
    _upd="$(ascendo_npm_native_update_cmd "$_cmd")"
    _env="$(ascendo_npm_native_installer_env "$_cmd")"
    _bargs="$(ascendo_npm_native_bootstrap_args "$_cmd")"
    _timeout="$(ascendo_npm_native_installer_timeout)"
    _before=""
    if [ -n "$_path" ]; then
        _before="$(ascendo_npm_native_installed_version "$_display" "$_cmd")"
        case "$_cmd" in
            claude|codex)
                if ! ascendo_npm_native_path_supported "$_cmd" "$_path"; then
                    json_add_item "$_display" "$_before" "" "skipped" "npm" "native-installer"
                    json_add_message "warn" "$_display: $_path has an unverified installation origin (not a native $_cmd install); retaining existing distribution"
                    return
                fi
                ;;
        esac
    elif ! ascendo_npm_bootstrap_allowed "$_display"; then
        _skip_not_installed "$_display" "native-installer"
        return
    fi
    _tmp_log="$(mktemp -t ascendo-native-install.XXXXXX 2>/dev/null || mktemp /tmp/ascendo-native-install.XXXXXX)"

    # -- 1. installed: the CLI's own update subcommand ------------------------
    if [ -n "$_path" ] && [ -x "$_path" ] && [ -n "$_upd" ]; then
        _stream_emit ">>> $_display: $_cmd $_upd"
        # $_env / $_upd are unquoted on purpose (empty or KEY=VAL / one word).
        # The runner pins the child's stdin to /dev/null.
        # shellcheck disable=SC2086
        ascendo_npm_run_with_timeout "$_timeout" env $_env "$_path" $_upd >"$_tmp_log" 2>&1
        _rc=$?
        _tail="$(_native_log_tail "$_tmp_log")"
        if [ "$_rc" -eq 0 ]; then
            if _after="$(ascendo_npm_report_cli_version_or_fail "$_display" "$_path")"; then
                _native_record_success "$_display" "$_before" "$_after"
                rm -f "$_tmp_log" 2>/dev/null
                return
            fi
            _tail="'$_cmd $_upd' exited 0 but '$_cmd --version' does not print a version${_tail:+ -- $_tail}"
        elif [ "$_rc" -eq 124 ]; then
            _tail="'$_cmd $_upd' timed out (exit 124)${_tail:+ -- $_tail}"
        else
            _tail="'$_cmd $_upd' exited $_rc${_tail:+ -- $_tail}"
        fi
        ascendo_npm_report_cli_version_or_fail "$_display" "$_path" >/dev/null && _runs=1
        if { [ "$_cmd" = "claude" ] && [ "$_runs" -eq 1 ]; } || [ "$_path" != "$_local" ]; then
            # Never re-run install.sh over a working claude (TUI hang class),
            # and never drop a second copy next to a CLI that lives elsewhere
            # (PATH-only / non-vendor-root), whether or not it still runs.
            json_add_item "$_display" "$_before" "" "failed" "npm" "native-installer"
            json_add_message "error" "$_display: $_tail"
            rm -f "$_tmp_log" 2>/dev/null
            return
        fi
        json_add_message "warn" "$_display: $_tail -- falling back to the vendor installer"
    fi

    # -- 2. bootstrap / fallback: downloaded installer ------------------------
    if ! command -v curl >/dev/null 2>&1; then
        json_add_item "$_display" "$_before" "" "failed" "npm" "native-installer"
        json_add_message "error" "curl missing; cannot run native installer for $_display"
        rm -f "$_tmp_log" 2>/dev/null
        return
    fi
    _stream_emit ">>> native-installer $_display ($_url)"
    _installer_tmp="$(mktemp "${TMPDIR:-/tmp}/ascendo-installer.XXXXXX")" || _installer_tmp=""
    if [ -z "$_installer_tmp" ] || ! ascendo_npm_fetch_installer "$_url" "$_installer_tmp"; then
        [ -n "$_installer_tmp" ] && rm -f "$_installer_tmp" "${_installer_tmp}.part" 2>/dev/null
        json_add_item "$_display" "$_before" "" "failed" "npm" "native-installer"
        json_add_message "error" "native installer for $_display: download failed ($_url)"
        rm -f "$_tmp_log" 2>/dev/null
        return
    fi
    # shellcheck disable=SC2086
    ascendo_npm_run_with_timeout "$_timeout" env $_env /bin/sh "$_installer_tmp" $_bargs >"$_tmp_log" 2>&1
    _rc=$?
    rm -f "$_installer_tmp" 2>/dev/null
    _tail="$(_native_log_tail "$_tmp_log")"
    rm -f "$_tmp_log" 2>/dev/null
    if [ "$_rc" -ne 0 ]; then
        json_add_item "$_display" "$_before" "" "failed" "npm" "native-installer"
        if [ "$_rc" -eq 124 ]; then
            json_add_message "error" "native installer for $_display timed out (exit 124) -- ${_tail:-no output}"
        else
            json_add_message "error" "native installer for $_display exited $_rc -- ${_tail:-no output}"
        fi
        return
    fi
    # Vendor installers drop the binary in ~/.local/bin; verify exactly that
    # file, not whatever happens to be on PATH (a PATH-only copy would make a
    # failed install look like success).
    if [ ! -x "$_local" ]; then
        json_add_item "$_display" "$_before" "" "failed" "npm" "native-installer"
        json_add_message "error" "$_display: installer exited 0 but no '$_cmd' binary appeared in $(ascendo_npm_local_bin)"
        return
    fi
    if ! _after="$(ascendo_npm_report_cli_version_or_fail "$_display" "$_local")"; then
        json_add_item "$_display" "$_before" "" "failed" "npm" "native-installer"
        json_add_message "error" "$_display: installer exited 0 but '$_cmd --version' does not print a version"
        return
    fi
    _native_record_success "$_display" "$_before" "$_after"
}

# -- OpenCode: native-first (macOS_updates update_opencode_native_first) ---------
# Pass 1, before Node/npm. State is handed to pass 2 (apply_self_update), which
# owns the guarded npm fallback and the final item for this row.
OPENCODE_NATIVE_STATE=""    # "" (not attempted) | failed | older | unverified | brew
OPENCODE_NATIVE_MSG=""
OPENCODE_ROW_HANDLED=0      # 1 => item already emitted (native verified + published, or dry run)

_UNVERIFIED_MSG="OpenCode user-local command has an unverified installation origin; retaining existing distribution (reason=unverified_origin)"
_BREW_MSG="OpenCode is managed by Homebrew -- skipping (update it with brew; set ASCENDO_BOOTSTRAP_CLI=1 or select opencode-cli explicitly to install a native copy)"

_opencode_native_stop() {   # state message
    OPENCODE_NATIVE_STATE="$1"
    OPENCODE_NATIVE_MSG="$2"
    return 1
}

apply_opencode_native() {
    local _display="$1"
    local _local _native _old_path _old_version="" _fb_path _fb_version="" _tmp_log _installer_tmp
    local _desc _rc _tail _after _installed_native=0
    OPENCODE_NATIVE_STATE=""; OPENCODE_NATIVE_MSG=""
    if [ "$DRY_RUN" = "true" ]; then
        json_add_item "$_display" "" "" "planned" "npm" "self-update"
        OPENCODE_ROW_HANDLED=1
        return 0
    fi
    _local="$(ascendo_npm_local_bin)/opencode"
    _native="$(ascendo_npm_opencode_native_bin)"
    # T8-c FIRST, before anything is executed: a user-local `opencode` that is
    # not provably the vendor's native install (npm wrapper link, Homebrew link,
    # user file, dangling link) is retained as is -- never run (not even
    # --version), never replaced, and the native symlink cannot be published
    # over it anyway.
    if [ -e "$_local" ] || [ -L "$_local" ]; then
        if [ -x "$_local" ] && ascendo_npm_native_path_supported opencode "$_local"; then
            _native="$_local"
        elif [ "$(ascendo_npm_opencode_origin "$_local")" = "brew" ]; then
            _opencode_native_stop brew "$_BREW_MSG"
            return 1
        else
            _opencode_native_stop unverified "$_UNVERIFIED_MSG"
            return 1
        fi
    fi
    # Only a command of KNOWN origin (native / managed npm / Homebrew) is ever
    # resolved or run.
    _old_path="$(ascendo_npm_opencode_resolve_path)"
    if [ -z "$_old_path" ]; then
        if ! ascendo_npm_bootstrap_allowed "$_display"; then
            if [ -n "$(ascendo_npm_opencode_unverified_path)" ]; then
                _opencode_native_stop unverified "$_UNVERIFIED_MSG"
                return 1
            fi
            # Only-installed gate: nothing installed and nobody asked => nothing
            # to do (pass 2 reports the skip).
            return 0
        fi
    elif [ "$(ascendo_npm_opencode_origin "$_old_path")" = "brew" ] \
         && ! ascendo_npm_bootstrap_allowed "$_display"; then
        # Homebrew owns it: never install a native copy next to it unasked.
        _opencode_native_stop brew "$_BREW_MSG"
        return 1
    fi
    if [ -n "$_old_path" ]; then
        _old_version="$(ascendo_npm_report_cli_version_or_fail "$_display" "$_old_path")" || true
    fi
    [ "$_old_version" = "?" ] && _old_version=""

    # The distribution a successful native install must not regress: the first
    # existing command that is not the native one.
    _fb_path="$_old_path"
    if [ "$_fb_path" = "$_local" ] || [ "$_fb_path" = "$_native" ]; then
        _fb_path="$(ascendo_npm_global_bin)/opencode"
        [ -x "$_fb_path" ] || _fb_path=""
    fi
    if [ -n "$_fb_path" ]; then
        _fb_version="$(ascendo_npm_report_cli_version_or_fail "$_display" "$_fb_path")" || _fb_version=""
        [ "$_fb_version" = "?" ] && _fb_version=""
    fi

    _tmp_log="$(mktemp -t ascendo-opencode.XXXXXX 2>/dev/null || mktemp /tmp/ascendo-opencode.XXXXXX)"
    if [ -x "$_native" ]; then
        _installed_native=1
        _desc="opencode upgrade --method curl"
        _stream_emit ">>> $_display: $_desc"
        # The runner pins the child's stdin to /dev/null.
        ascendo_npm_run_with_timeout 300 "$_native" upgrade --method curl >"$_tmp_log" 2>&1
        _rc=$?
    else
        _desc="opencode native installer"
        if ! command -v curl >/dev/null 2>&1; then
            rm -f "$_tmp_log" 2>/dev/null
            _opencode_native_stop failed "curl missing; cannot run the native installer for $_display"
            return 1
        fi
        _stream_emit ">>> native-installer $_display (https://opencode.ai/install)"
        _installer_tmp="$(mktemp "${TMPDIR:-/tmp}/ascendo-opencode-installer.XXXXXX")" || _installer_tmp=""
        if [ -z "$_installer_tmp" ] \
           || ! ascendo_npm_fetch_installer "$(ascendo_npm_native_install_url opencode)" "$_installer_tmp"; then
            [ -n "$_installer_tmp" ] && rm -f "$_installer_tmp" "${_installer_tmp}.part" 2>/dev/null
            rm -f "$_tmp_log" 2>/dev/null
            _opencode_native_stop failed "native installer for $_display: download failed (https://opencode.ai/install)"
            return 1
        fi
        # Hide any other opencode (npm / brew) during the migration: the vendor
        # script exits early if ANY opencode on PATH already has the requested
        # release. --no-modify-path: never edit the user's shell profile.
        ascendo_npm_run_with_timeout "$(ascendo_npm_native_installer_timeout)" \
            env PATH="/usr/bin:/bin:/usr/sbin:/sbin" /bin/bash "$_installer_tmp" --no-modify-path \
            >"$_tmp_log" 2>&1
        _rc=$?
        rm -f "$_installer_tmp" 2>/dev/null
    fi
    _tail="$(_native_log_tail "$_tmp_log")"
    rm -f "$_tmp_log" 2>/dev/null
    if [ "$_rc" -ne 0 ]; then
        if [ "$_rc" -eq 124 ]; then
            _opencode_native_stop failed "$_desc timed out (exit 124)${_tail:+ -- $_tail}"
        else
            _opencode_native_stop failed "$_desc exited $_rc${_tail:+ -- $_tail}"
        fi
        return 1
    fi
    if ! _after="$(ascendo_npm_report_cli_version_or_fail "$_display" "$_native")"; then
        _opencode_native_stop failed "$_desc exited 0 but '$_native --version' does not print a version"
        return 1
    fi
    if [ -n "$_fb_version" ] && ascendo_npm_version_newer "$_fb_version" "$_after"; then
        _opencode_native_stop older \
            "OpenCode native release ($_after) is older than the installed fallback ($_fb_version); retaining the existing distribution"
        return 1
    fi
    if [ "$_native" != "$_local" ]; then
        # `ln -s` without -f refuses a raced-in user file, dangling links included.
        mkdir -p "$(ascendo_npm_local_bin)" 2>/dev/null || true
        if [ ! -e "$_local" ] && [ ! -L "$_local" ]; then
            if ! ln -s "$_native" "$_local" 2>/dev/null; then
                _opencode_native_stop failed "OpenCode: could not publish $_local -> $_native"
                return 1
            fi
        else
            _opencode_native_stop unverified \
                "OpenCode user-local command has an unverified installation origin; retaining existing distribution"
            return 1
        fi
    fi
    export PATH="$(ascendo_npm_local_bin):$(ascendo_npm_global_bin):$PATH"
    hash -r 2>/dev/null || true
    if [ "$(command -v opencode 2>/dev/null)" != "$_local" ]; then
        _opencode_native_stop failed "OpenCode: 'opencode' on PATH does not resolve to $_local after the native install"
        return 1
    fi
    OPENCODE_ROW_HANDLED=1
    if [ -n "$_old_version" ] && [ "$_old_version" = "$_after" ] && [ "$_installed_native" -eq 0 ]; then
        json_add_message "info" "$_display: native install active ($_native, $_after); the previous distribution was left in place"
    fi
    if [ -n "$_old_version" ] && [ "$_old_version" = "$_after" ]; then
        json_add_item "$_display" "$_after" "$_after" "up_to_date" "npm" "self-update"
    else
        json_add_item "$_display" "$_old_version" "$_after" "success" "npm" "self-update"
    fi
    return 0
}

# Pass 2 `self-update` row. OpenCode: native result first (pass 1), then the
# npm distribution as a GUARDED fallback -- only an npm install that already
# exists in the managed prefix, never the native command, never a bootstrap of
# another distribution. Any other self-updating CLI: `<cmd> update`.
apply_self_update() {
    local _display="$1" _pkg="$2" _cmd="$3"
    local _command_path _sub="update" _args="" _before="" _verified="" _ok=0 _rc _rrc _tail _tmp_log
    if [ "$_cmd" = "opencode" ]; then
        [ "$OPENCODE_ROW_HANDLED" -eq 1 ] && return
        if ! ascendo_npm_opencode_npm_wrapper_exists; then
            case "$OPENCODE_NATIVE_STATE" in
                failed)
                    json_add_item "$_display" "" "" "failed" "npm" "self-update"
                    json_add_message "error" "$_display: $OPENCODE_NATIVE_MSG"
                    ;;
                older|unverified|brew)
                    json_add_item "$_display" "" "" "skipped" "npm" "self-update"
                    json_add_message "warn" "$_display: $OPENCODE_NATIVE_MSG"
                    ;;
                *)
                    _skip_not_installed "$_display" "self-update"
                    ;;
            esac
            return
        fi
        _command_path="$(ascendo_npm_global_bin)/opencode"
        _sub="upgrade"; _args="--method npm"
        if [ -n "$OPENCODE_NATIVE_MSG" ]; then
            json_add_message "warn" "$_display: $OPENCODE_NATIVE_MSG -- updating the existing npm install instead"
        fi
    else
        if [ "$DRY_RUN" = "true" ]; then
            json_add_item "$_display" "" "" "planned" "npm" "self-update"
            return
        fi
        _command_path="$(ascendo_npm_native_command_path "$_cmd")"
        if [ -z "$_command_path" ]; then
            _skip_not_installed "$_display" "self-update"
            return
        fi
    fi
    _before="$(ascendo_npm_report_cli_version_or_fail "$_display" "$_command_path")" || _before=""
    [ "$_before" = "?" ] && _before=""
    _stream_emit ">>> $_display: $_cmd $_sub $_args"
    _tmp_log="$(mktemp -t ascendo-self-update.XXXXXX 2>/dev/null || mktemp /tmp/ascendo-self-update.XXXXXX)"
    # Some self-updaters shell out to `npm install -g` internally: pin the
    # prefix and PATH for the child only (subshell; never write ~/.npmrc). The
    # runner pins the child's stdin to /dev/null.
    (
        export npm_config_prefix="$NPM_GLOBAL_PREFIX" NPM_CONFIG_PREFIX="$NPM_GLOBAL_PREFIX"
        export PATH="$(ascendo_npm_local_bin):$(ascendo_npm_global_bin):$(ascendo_npm_n_prefix)/bin:$PATH"
        if [ "$_cmd" = "opencode" ]; then export NPM_CONFIG_ALLOW_SCRIPTS="opencode-ai"; fi
        # shellcheck disable=SC2086
        ascendo_npm_run_with_timeout 300 "$_command_path" $_sub $_args
    ) >"$_tmp_log" 2>&1
    _rc=$?
    [ "$_rc" -eq 0 ] && _ok=1
    # The command path is deliberately NOT re-resolved for opencode: the npm
    # fallback must never end up measuring / touching the native command.
    if [ "$_cmd" != "opencode" ]; then
        _command_path="$(ascendo_npm_native_command_path "$_cmd")"
        [ -n "$_command_path" ] || _command_path=""
    fi
    if ! _verified="$(ascendo_npm_report_cli_version_or_fail "$_display" "$_command_path")"; then
        _verified=""
        if [ "$_cmd" = "opencode" ]; then
            # A broken stub (npm 12 skipped the postinstall) is repaired in
            # place -- only because the npm install already exists.
            ascendo_npm_repair_broken_opencode "$NPM_GLOBAL_PREFIX" "$_command_path" >>"$_tmp_log" 2>&1
            _rrc=$?
            if [ "$_rrc" -eq 2 ]; then
                # The npm install existed (precondition) and `opencode upgrade`
                # ran; the binary is gone afterwards: a failed mutation.
                _tail="$(_native_log_tail "$_tmp_log")"
                rm -f "$_tmp_log" 2>/dev/null
                json_add_item "$_display" "$_before" "" "failed" "npm" "self-update"
                json_add_message "error" "$_display: the OpenCode binary disappeared after '$_cmd $_sub $_args'${_tail:+ -- $_tail}"
                return
            fi
            if [ "$_rrc" -eq 0 ] && _verified="$(ascendo_npm_report_cli_version_or_fail "$_display" "$_command_path")"; then
                _ok=1
            else
                _verified=""
            fi
        fi
    fi
    _tail="$(_native_log_tail "$_tmp_log")"
    rm -f "$_tmp_log" 2>/dev/null
    if [ "$_ok" -eq 1 ] && [ -n "$_verified" ]; then
        if [ -n "$_before" ] && [ "$_before" = "$_verified" ]; then
            json_add_item "$_display" "$_verified" "$_verified" "up_to_date" "npm" "self-update"
        else
            json_add_item "$_display" "$_before" "$_verified" "success" "npm" "self-update"
        fi
        return
    fi
    json_add_item "$_display" "$_before" "" "failed" "npm" "self-update"
    if [ "$_rc" -eq 124 ]; then
        json_add_message "error" "$_display: '$_cmd $_sub $_args' timed out (exit 124)${_tail:+ -- $_tail}"
    elif [ "$_rc" -ne 0 ]; then
        json_add_message "error" "$_display: '$_cmd $_sub $_args' exited $_rc${_tail:+ -- $_tail}"
    else
        json_add_message "error" "$_display: '$_cmd $_sub $_args' exited 0 but '$_cmd --version' does not print a version${_tail:+ -- $_tail}"
    fi
}

# Total count for live-stream progress accounting.
TOTAL_ITEMS="$(ascendo_npm_manifest_lines | awk -F'|' 'NR > 1 && length($1) > 0 { c++ } END { print c+0 }')"
CURRENT_INDEX=0

if [ "$DRY_RUN" != "true" ] && [ "$TOTAL_ITEMS" -gt 0 ]; then
    _stream_progress 0 "npm apply: $TOTAL_ITEMS item(s)"
fi

_progress_tick() {   # display method
    [ "$DRY_RUN" = "true" ] && return 0
    CURRENT_INDEX=$(( CURRENT_INDEX + 1 ))
    local _pct=0
    if [ "$TOTAL_ITEMS" -gt 0 ]; then
        _pct=$(( CURRENT_INDEX * 100 / TOTAL_ITEMS ))
    fi
    _stream_progress "$_pct" "npm: $1 ($2)"
}

# -- walk manifest: two passes ------------------------------------------------
# Pass 1: native-installer rows + OpenCode native-first (they need no Node/npm,
#         so a missing / broken managed toolchain cannot block them).
# Pass 2: native-node, npm, self-update, native-bun.
# Process substitution (not `manifest | while`) — see check.sh for the
# bug class this avoids; every mutating child additionally gets </dev/null.
_walk_pass() {   # 1 | 2
    local _pass="$1"
    while IFS='|' read -r DISPLAY PKG METHOD BREW CMD; do
        [ "$DISPLAY" = "display_name" ] && continue
        [ -z "$DISPLAY" ] && continue
        in_filter "$DISPLAY" || continue
        if [ "$_pass" = "1" ]; then
            case "$METHOD" in
                native-installer)
                    _progress_tick "$DISPLAY" "$METHOD"
                    apply_native_installer "$DISPLAY" "$CMD"
                    ;;
                self-update)
                    if [ "$CMD" = "opencode" ]; then
                        apply_opencode_native "$DISPLAY" || true
                    fi
                    ;;
            esac
            continue
        fi
        case "$METHOD" in
            native-node) _progress_tick "$DISPLAY" "$METHOD"; apply_native_node "$DISPLAY" ;;
            native-bun)  _progress_tick "$DISPLAY" "$METHOD"; apply_native_bun  "$DISPLAY" ;;
            npm)         _progress_tick "$DISPLAY" "$METHOD"; apply_npm "$DISPLAY" "$PKG" "$CMD" ;;
            self-update) _progress_tick "$DISPLAY" "$METHOD"; apply_self_update "$DISPLAY" "$PKG" "$CMD" ;;
            native-installer) ;;   # handled in pass 1
            *) json_add_message "warn" "unknown method '$METHOD' for $DISPLAY; skipping" ;;
        esac
    done < <(ascendo_npm_manifest_lines)
}

# Pass 2 order matters inside the pass: `bun` last, node before npm packages.
# (Manifest order is node, npm, ... bun, which is exactly that.)
_walk_pass 1
_walk_pass 2

if [ "$DRY_RUN" != "true" ] && [ "$TOTAL_ITEMS" -gt 0 ]; then
    _stream_progress 100 "npm apply: done"
fi

exit 0
