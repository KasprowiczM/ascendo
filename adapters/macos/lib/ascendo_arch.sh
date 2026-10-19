#!/usr/bin/env bash
# adapters/macos/lib/ascendo_arch.sh -- architecture / Rosetta awareness (Task 17, Spec F)
#
# Port of macOS_updates lib/platform.sh mac_update_rosetta_installed /
# mac_update_app_architecture. Bash 3.2. Read-only: nothing here installs.
#
#   ascendo_rosetta_installed          rc 0 when Rosetta 2 is present
#       ASCENDO_ROSETTA_PRESENT=1|0    test seam (skips the real probes)
#       ASCENDO_ROSETTA_DIR            override of /Library/Apple/usr/share/rosetta
#   ascendo_app_architecture <app>     prints universal|arm64|x86_64-only|unknown
#   ascendo_is_intel_only <app>        rc 0 when the bundle is x86_64-only
#   ascendo_rosetta_needed <app>       rc 0 when Intel-only AND Apple silicon AND no Rosetta
#       ASCENDO_HOST_ARCH              test seam for `uname -m`

ascendo_rosetta_installed() {
    case "${ASCENDO_ROSETTA_PRESENT:-}" in
        1) return 0 ;;
        0) return 1 ;;
    esac
    pgrep -q oahd 2>/dev/null && return 0
    [ -d "${ASCENDO_ROSETTA_DIR:-/Library/Apple/usr/share/rosetta}" ]
}

ascendo_app_architecture() {
    local _app="$1" _exe_name="" _exe="" _f _archs=""
    [ -d "$_app" ] || { echo "unknown"; return 0; }
    if [ -f "$_app/Contents/Info.plist" ]; then
        _exe_name="$(defaults read "$_app/Contents/Info" CFBundleExecutable 2>/dev/null || true)"
    fi
    if [ -n "$_exe_name" ] && [ -f "$_app/Contents/MacOS/$_exe_name" ]; then
        _exe="$_app/Contents/MacOS/$_exe_name"
    elif [ -d "$_app/Contents/MacOS" ]; then
        for _f in "$_app/Contents/MacOS"/*; do
            if [ -f "$_f" ]; then _exe="$_f"; break; fi
        done
    fi
    [ -n "$_exe" ] && [ -f "$_exe" ] || { echo "unknown"; return 0; }
    _archs="$(lipo -archs "$_exe" 2>/dev/null || true)"
    case "$_archs" in
        *arm64*x86_64*|*x86_64*arm64*|*arm64*i386*|*i386*arm64*) echo "universal" ;;
        *arm64*)                                                 echo "arm64" ;;
        *x86_64*|*i386*)                                         echo "x86_64-only" ;;
        *)                                                       echo "unknown" ;;
    esac
}

ascendo_is_intel_only() {
    [ "$(ascendo_app_architecture "$1")" = "x86_64-only" ]
}

ascendo_rosetta_needed() {
    [ "${ASCENDO_HOST_ARCH:-$(uname -m 2>/dev/null)}" = "arm64" ] || return 1
    ascendo_is_intel_only "$1" || return 1
    ! ascendo_rosetta_installed
}
