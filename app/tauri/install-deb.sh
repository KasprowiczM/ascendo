#!/usr/bin/env bash
# =============================================================================
# app/tauri/install-deb.sh — Install the most recently built .deb.
#
# Resolves the deb path to absolute (apt rejects bare relative paths without
# a leading ./), removes any older package built under different productName,
# then runs `sudo apt install`.
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

DEB=$(find "${SCRIPT_DIR}/src-tauri/target" -iname '*aktualizacje*.deb' 2>/dev/null \
        | xargs -d '\n' -r ls -t 2>/dev/null \
        | head -1)
if [[ -z "$DEB" ]]; then
    echo "no .deb found under ${SCRIPT_DIR}/src-tauri/target — run 'bash build.sh' first" >&2
    exit 1
fi
DEB_ABS="$(readlink -f "$DEB")"
echo "Found: $DEB_ABS"

# Remove older package variants if installed
for legacy in ascendo-skin Ascendo ubuntu_aktualizacje; do
    if dpkg -s "$legacy" >/dev/null 2>&1; then
        echo "── removing legacy package: $legacy"
        sudo apt-get remove --purge -y "$legacy" || true
    fi
done

echo
echo "── sudo apt install"
sudo apt install -y "$DEB_ABS"

echo
echo "✔ installed. Launch with:"
echo "    ascendo"
echo "  or open from GNOME Activities (search 'ascendo')."
