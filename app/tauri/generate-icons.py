#!/usr/bin/env python3
"""Generate placeholder icons for the Tauri build (pure stdlib, no Pillow).

Writes RGBA PNGs at 32x32, 128x128, 128x128@2x (256x256), 256x256, 512x512,
and the wildcard icon.png + icon.ico/icon.icns stubs that bundler expects.

Replace with proper artwork later by overwriting these files.
"""
from __future__ import annotations

import struct
import sys
import zlib
from pathlib import Path

# Ascendo brand: deep indigo background, light accent square,
# small bright accent dot in lower-right. All hand-coded so we don't need
# Pillow / imagemagick.

BG       = (0x12, 0x18, 0x2c, 0xff)   # near-black indigo
ACCENT_1 = (0x7a, 0xa6, 0xff, 0xff)   # blue
ACCENT_2 = (0x34, 0xc2, 0x70, 0xff)   # green (status dot)


def _png_chunk(tag: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + tag
        + data
        + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    )


def write_png(path: Path, size: int) -> None:
    """Render a stylised square logo with a centred lighter square + dot."""
    s = size
    pad_outer = max(2, s // 8)
    pad_inner = max(1, s // 4)
    dot_size = max(2, s // 5)
    dot_margin = max(1, s // 12)

    rows: list[bytes] = []
    for y in range(s):
        row = bytearray()
        row.append(0)  # PNG filter byte: None
        for x in range(s):
            # Default: indigo background
            r, g, b, a = BG
            # Outer rounded-ish square: just a plain square here
            in_outer = pad_outer <= x < s - pad_outer and pad_outer <= y < s - pad_outer
            in_inner = pad_inner <= x < s - pad_inner and pad_inner <= y < s - pad_inner
            if in_inner:
                r, g, b, a = ACCENT_1
            elif in_outer:
                # subtle frame: blend background and accent
                r = (BG[0] + ACCENT_1[0]) // 2
                g = (BG[1] + ACCENT_1[1]) // 2
                b = (BG[2] + ACCENT_1[2]) // 2
                a = 0xff
            # Status dot in lower-right
            dx = x - (s - dot_margin - dot_size + dot_size // 2)
            dy = y - (s - dot_margin - dot_size + dot_size // 2)
            if dx * dx + dy * dy <= (dot_size // 2) ** 2:
                r, g, b, a = ACCENT_2
            row.extend([r, g, b, a])
        rows.append(bytes(row))

    raw = b"".join(rows)
    compressed = zlib.compress(raw, 9)

    ihdr = struct.pack(">IIBBBBB", s, s, 8, 6, 0, 0, 0)
    png = (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", compressed)
        + _png_chunk(b"IEND", b"")
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(png)


def main(argv: list[str]) -> int:
    here = Path(__file__).resolve().parent
    icons_dir = here / "src-tauri" / "icons"
    icons_dir.mkdir(parents=True, exist_ok=True)

    sizes = {
        "32x32.png":      32,
        "128x128.png":    128,
        "128x128@2x.png": 256,
        "icon.png":       256,
        # Tauri bundler may also probe these names
        "icon-256.png":   256,
        "icon-512.png":   512,
    }
    for name, size in sizes.items():
        target = icons_dir / name
        if "--force" not in argv and target.exists() and target.stat().st_size > 0:
            print(f"  keep {name}")
            continue
        write_png(target, size)
        print(f"  wrote {name} ({size}x{size}, {target.stat().st_size} B)")

    # Linux-only build (deb + appimage) doesn't need icon.icns / icon.ico,
    # and zero-byte stubs make the Tauri bundler choke. Remove if present.
    for name in ("icon.icns", "icon.ico"):
        target = icons_dir / name
        if target.exists():
            target.unlink()
            print(f"  removed stale {name}")

    print(f"\n✔ icons in {icons_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
