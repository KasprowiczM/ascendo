# adapters/ubuntu/lib/ascendo_web.sh
# Shared helpers for Linux web phase scripts.

# _web_is_running <pattern>  -- True if any process matches `pgrep -f`.
_web_is_running() {
    local pat="$1"
    [ -z "$pat" ] && return 1
    pgrep -f "$pat" >/dev/null 2>&1
}

# _version_gt <a> <b> -- True if version a > b (semver-ish, sort -V).
_version_gt() {
    local a="$1" b="$2"
    [ -z "$a" ] && return 1
    [ -z "$b" ] && return 0
    [ "$a" = "$b" ] && return 1
    local top
    top=$(printf '%s\n%s\n' "$a" "$b" | sort -V | tail -1)
    [ "$top" = "$a" ]
}

# _web_cache_dir -- echoes ~/.cache/ascendo/web (created on demand).
_web_cache_dir() {
    local d="${XDG_CACHE_HOME:-$HOME/.cache}/ascendo/web"
    mkdir -p "$d" 2>/dev/null
    printf '%s' "$d"
}

# _web_install_artifact <app_id> <url>
# Download the artefact at <url>, then dispatch by extension:
#   *.AppImage      -> chmod +x, move to ~/Applications/<basename>
#   *.deb           -> sudo -A apt install -y <file>
#   *.tar.gz/.tgz   -> emit message; user must extract manually (Tier-B trigger)
# Echoes installed path on success.
_web_install_artifact() {
    local app_id="$1" url="$2"
    local cache target
    cache=$(_web_cache_dir)
    local base
    base=$(printf '%s' "$url" | sed -E 's|.*/||; s|\?.*$||')
    [ -z "$base" ] && base="${app_id}.bin"
    target="$cache/$base"

    # Download (curl with retries)
    if ! curl -fsSL --max-time 600 --retry 2 -o "$target.partial" "$url" 2>/dev/null; then
        return 25
    fi
    mv -f "$target.partial" "$target"

    case "$base" in
        *.AppImage|*.appimage)
            chmod +x "$target" 2>/dev/null
            local app_dir="$HOME/Applications"
            mkdir -p "$app_dir" 2>/dev/null
            local installed="$app_dir/$base"
            mv -f "$target" "$installed"
            chmod +x "$installed"
            printf '%s\n' "$installed"
            return 0
            ;;
        *.deb)
            if ! command -v apt >/dev/null 2>&1; then
                echo "_web_install_artifact: apt not on PATH; .deb cannot be installed" >&2
                return 30
            fi
            local sudo_argv=(sudo)
            [ -n "${SUDO_ASKPASS:-}" ] && sudo_argv+=(-A)
            if "${sudo_argv[@]}" apt install -y "$target" >/dev/null 2>&1; then
                printf '%s\n' "$target"
                return 0
            fi
            return 30
            ;;
        *.tar.gz|*.tgz|*.tar.xz|*.zip)
            # Tier-B trigger — too many possible extraction layouts.
            # Leave the file in cache and emit a trigger.
            printf '%s\n' "$target"
            return 0
            ;;
        *)
            # Unknown extension — leave it in cache, treat as trigger.
            printf '%s\n' "$target"
            return 0
            ;;
    esac
}
