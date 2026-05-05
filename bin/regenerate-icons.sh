#!/usr/bin/env bash
# Regenerate the Tauri icon set from app/frontend/assets/logo-mark.svg
# (the dark-mode variant: lime bars on ink-900 background).
#
# Usage:  bash bin/regenerate-icons.sh
#
# Requires ImageMagick (`magick` or legacy `convert`). Bash 3.2 compatible.
set -e
cd "$(dirname "$0")/.."

SVG="app/frontend/assets/logo-mark.svg"
OUT="ui/desktop-tauri/src-tauri/icons"

if [ ! -f "$SVG" ]; then
  echo "error: $SVG not found" >&2
  exit 1
fi
if [ ! -d "$OUT" ]; then
  echo "error: $OUT directory missing" >&2
  exit 1
fi

if command -v magick >/dev/null 2>&1; then
  IM=magick
elif command -v convert >/dev/null 2>&1; then
  IM=convert
else
  echo "error: ImageMagick not found. Install with:" >&2
  echo "  brew install imagemagick      # macOS"  >&2
  echo "  apt-get install imagemagick   # Debian/Ubuntu" >&2
  echo "  winget install ImageMagick.ImageMagick   # Windows" >&2
  exit 1
fi

echo "rendering icons from $SVG -> $OUT/"
$IM -background none -depth 8 -define png:color-type=6 -define png:bit-depth=8 "$SVG" -resize 32x32   "$OUT/32x32.png"
$IM -background none -depth 8 -define png:color-type=6 -define png:bit-depth=8 "$SVG" -resize 128x128 "$OUT/128x128.png"
$IM -background none -depth 8 -define png:color-type=6 -define png:bit-depth=8 "$SVG" -resize 256x256 "$OUT/128x128@2x.png"
$IM -background none -depth 8 -define png:color-type=6 -define png:bit-depth=8 "$SVG" -resize 512x512 "$OUT/icon.png"
$IM -background none -depth 8 -define png:color-type=6 -define png:bit-depth=8 "$SVG" -define icon:auto-resize=256,128,96,64,48,32,16 "$OUT/icon.ico"

# macOS .icns -- only buildable on macOS (iconutil ships with Xcode CLT).
# On Linux/Windows we silently skip; the existing .icns in git stays intact
# and the Tauri build on macOS will still embed the right icon.
if command -v iconutil >/dev/null 2>&1; then
  echo "rendering icon.icns via iconutil"
  TMP_ICONSET=$(mktemp -d)
  ICONSET="$TMP_ICONSET/Ascendo.iconset"
  mkdir -p "$ICONSET"
  $IM -background none -depth 8 -define png:color-type=6 -define png:bit-depth=8 "$SVG" -resize 16x16     "$ICONSET/icon_16x16.png"
  $IM -background none -depth 8 -define png:color-type=6 -define png:bit-depth=8 "$SVG" -resize 32x32     "$ICONSET/icon_16x16@2x.png"
  $IM -background none -depth 8 -define png:color-type=6 -define png:bit-depth=8 "$SVG" -resize 32x32     "$ICONSET/icon_32x32.png"
  $IM -background none -depth 8 -define png:color-type=6 -define png:bit-depth=8 "$SVG" -resize 64x64     "$ICONSET/icon_32x32@2x.png"
  $IM -background none -depth 8 -define png:color-type=6 -define png:bit-depth=8 "$SVG" -resize 128x128   "$ICONSET/icon_128x128.png"
  $IM -background none -depth 8 -define png:color-type=6 -define png:bit-depth=8 "$SVG" -resize 256x256   "$ICONSET/icon_128x128@2x.png"
  $IM -background none -depth 8 -define png:color-type=6 -define png:bit-depth=8 "$SVG" -resize 256x256   "$ICONSET/icon_256x256.png"
  $IM -background none -depth 8 -define png:color-type=6 -define png:bit-depth=8 "$SVG" -resize 512x512   "$ICONSET/icon_256x256@2x.png"
  $IM -background none -depth 8 -define png:color-type=6 -define png:bit-depth=8 "$SVG" -resize 512x512   "$ICONSET/icon_512x512.png"
  $IM -background none -depth 8 -define png:color-type=6 -define png:bit-depth=8 "$SVG" -resize 1024x1024 "$ICONSET/icon_512x512@2x.png"
  iconutil -c icns "$ICONSET" -o "$OUT/icon.icns"
  rm -rf "$TMP_ICONSET"
else
  echo "note: iconutil not found (non-macOS host) -- skipping icon.icns regeneration"
fi

echo "done."
ls -la "$OUT/"
