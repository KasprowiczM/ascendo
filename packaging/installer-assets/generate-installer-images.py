"""Generate the installer banner/sidebar BMPs from the Ascendo brand mark.

NSIS expects:
- ``installer-banner.bmp``  — 150x57 px, used as the page header bitmap
- ``installer-sidebar.bmp`` — 164x314 px, used on the welcome / finish page

WiX MSI templates expect:
- ``installer-banner.bmp`` — 493x58 px, used at the top of every dialog
- ``installer-sidebar.bmp`` — 493x312 px, welcome / completion dialog sidebar

We produce **separate files** for each templating engine so a future
re-render of the brand mark only has to change this one script.

Run from repo root:

    python packaging/installer-assets/generate-installer-images.py

Output goes into ``packaging/installer-assets/`` so both build paths
(``bin/build-installer.ps1`` for NSIS, the WiX template for MSI) can
reference identical-looking images. Idempotent — re-running overwrites.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT_DIR = Path(__file__).resolve().parent

# Brand palette — same gradient as the SVG in branding/icon.svg.
GREEN = (34, 197, 94)    # #22c55e
BLUE = (14, 165, 233)    # #0ea5e9
DARK_BG = (14, 15, 18)   # #0e0f12
WHITE = (255, 255, 255)


def _gradient(size: tuple[int, int]) -> Image.Image:
    """Diagonal gradient green → blue, same direction as the SVG."""
    w, h = size
    img = Image.new("RGB", size, DARK_BG)
    for y in range(h):
        for x in range(w):
            t = (x + y) / (w + h - 2) if (w + h) > 2 else 0
            r = int(GREEN[0] + (BLUE[0] - GREEN[0]) * t)
            g = int(GREEN[1] + (BLUE[1] - GREEN[1]) * t)
            b = int(GREEN[2] + (BLUE[2] - GREEN[2]) * t)
            img.putpixel((x, y), (r, g, b))
    return img


def _try_font(size: int) -> ImageFont.ImageFont:
    """Prefer a real TTF when available; fall back to PIL's default bitmap."""
    for candidate in (
        "C:/Windows/Fonts/segoeuib.ttf",   # Segoe UI Bold (Win11 default)
        "C:/Windows/Fonts/segoeui.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ):
        if Path(candidate).is_file():
            try:
                return ImageFont.truetype(candidate, size)
            except OSError:
                continue
    return ImageFont.load_default()


def _draw_mark(draw: ImageDraw.ImageDraw, x: int, y: int, side: int) -> None:
    """Draw the Ascendo 'A' chevron mark inside an `side x side` box at (x,y)."""
    # The chevron path from branding/icon.svg, scaled.
    # SVG viewBox 0 0 64 64; path = M16 44 L32 22 L48 44, stroke-width 6.
    s = side / 64.0
    pts = [
        (x + 16 * s, y + 44 * s),
        (x + 32 * s, y + 22 * s),
        (x + 48 * s, y + 44 * s),
    ]
    draw.line(pts, fill=WHITE, width=max(2, int(6 * s)), joint="curve")


def make_banner_nsis() -> None:
    """150x57 — small header strip used on NSIS pages 2..n."""
    img = _gradient((150, 57))
    draw = ImageDraw.Draw(img)
    _draw_mark(draw, x=8, y=4, side=48)
    font = _try_font(22)
    draw.text((64, 14), "Ascendo", font=font, fill=WHITE)
    out = OUT_DIR / "installer-banner-nsis.bmp"
    img.save(out, "BMP")
    print(f"wrote {out}  ({img.size})")


def make_sidebar_nsis() -> None:
    """164x314 — welcome page sidebar."""
    img = _gradient((164, 314))
    draw = ImageDraw.Draw(img)
    _draw_mark(draw, x=20, y=24, side=124)
    font_big = _try_font(28)
    font_small = _try_font(14)
    draw.text((20, 168), "Ascendo", font=font_big, fill=WHITE)
    draw.text((20, 204), "Unified Updates", font=font_small, fill=WHITE)
    out = OUT_DIR / "installer-sidebar-nsis.bmp"
    img.save(out, "BMP")
    print(f"wrote {out}  ({img.size})")


def make_banner_wix() -> None:
    """493x58 — WiX dialog header."""
    img = _gradient((493, 58))
    draw = ImageDraw.Draw(img)
    _draw_mark(draw, x=12, y=5, side=48)
    font = _try_font(22)
    draw.text((68, 16), "Ascendo - Unified Updates", font=font, fill=WHITE)
    out = OUT_DIR / "installer-banner-wix.bmp"
    img.save(out, "BMP")
    print(f"wrote {out}  ({img.size})")


def make_sidebar_wix() -> None:
    """493x312 — WiX welcome dialog sidebar."""
    img = _gradient((493, 312))
    draw = ImageDraw.Draw(img)
    _draw_mark(draw, x=180, y=64, side=132)
    font_big = _try_font(36)
    font_small = _try_font(18)
    draw.text((132, 208), "Ascendo", font=font_big, fill=WHITE)
    draw.text((128, 254), "Unified Updates", font=font_small, fill=WHITE)
    out = OUT_DIR / "installer-sidebar-wix.bmp"
    img.save(out, "BMP")
    print(f"wrote {out}  ({img.size})")


if __name__ == "__main__":
    make_banner_nsis()
    make_sidebar_nsis()
    make_banner_wix()
    make_sidebar_wix()
