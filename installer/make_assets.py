"""Generate the NSIS installer branding art in the app's cyberpunk style.

Colors, wordmark and texture mirror src/ps5_image_forge/webui/app.css:
deep-navy ground, neon-cyan primary, magenta accent block, faint grid and
scanlines. Outputs 24-bit BMPs (NSIS requires BMP3/no-alpha) plus a multi-size
installer icon.

Usage:
    python installer/make_assets.py [--version 0.7.4]

Requires Pillow. Run once before makensis; the build script does this for you.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

# --- palette (from app.css :root) -----------------------------------------
BG = (7, 11, 20)          # --bg   #070b14
BG2 = (11, 17, 32)        # --bg2  #0b1120
PANEL = (13, 21, 38)      # --panel-solid
CYAN = (0, 229, 255)      # --cyan
CYAN_DIM = (14, 127, 140)  # --cyan-dim
MAGENTA = (255, 45, 149)  # --magenta
TEXT = (200, 230, 239)    # --text
TEXT_DIM = (93, 122, 140)  # --text-dim
OK = (61, 255, 143)       # --ok

ASSETS = Path(__file__).resolve().parent / "assets"

# Techy condensed fonts, best-first; falls back to Pillow's bitmap font.
FONT_DIR = Path("C:/Windows/Fonts")
FONT_CANDIDATES = [
    "bahnschrift.ttf",   # Bahnschrift (condensed, techy) — Win10/11
    "consolab.ttf",      # Consolas Bold
    "consola.ttf",       # Consolas
    "seguisb.ttf",       # Segoe UI Semibold
    "arialbd.ttf",       # Arial Bold
]


def load_font(size: int) -> ImageFont.FreeTypeFont:
    for name in FONT_CANDIDATES:
        p = FONT_DIR / name
        if p.exists():
            try:
                return ImageFont.truetype(str(p), size)
            except OSError:
                continue
    return ImageFont.load_default()


def lerp(a, b, t):
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


def vgradient(size, top, bottom):
    """Vertical gradient image."""
    w, h = size
    img = Image.new("RGB", size)
    px = img.load()
    for y in range(h):
        row = lerp(top, bottom, y / max(1, h - 1))
        for x in range(w):
            px[x, y] = row
    return img


def add_grid(img, step=40, color=CYAN, alpha=12):
    """Faint neon grid, like the app's background lattice."""
    w, h = img.size
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)
    line = color + (alpha,)
    for x in range(0, w, step):
        d.line([(x, 0), (x, h)], fill=line, width=1)
    for y in range(0, h, step):
        d.line([(0, y), (w, y)], fill=line, width=1)
    return Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")


def add_scanlines(img, gap=3, alpha=40):
    """Horizontal CRT scanlines."""
    w, h = img.size
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)
    for y in range(0, h, gap):
        d.line([(0, y), (w, y)], fill=(0, 0, 0, alpha), width=1)
    return Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")


def add_top_glow(img, color=PANEL):
    """Radial-ish lift at the top, echoing the app's header glow."""
    w, h = img.size
    glow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(glow)
    cx = w // 2
    d.ellipse([cx - w, -h, cx + w, int(h * 0.55)], fill=color + (90,))
    glow = glow.filter(ImageFilter.GaussianBlur(w // 3))
    return Image.alpha_composite(img.convert("RGBA"), glow).convert("RGB")


def glow_text(base, xy, text, font, fill, glow, anchor="la",
              radius=6, spacing=0, tracking=0):
    """Draw text with a neon halo. `tracking` adds letter-spacing (px)."""
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    _draw_tracked(d, xy, text, font, glow + (255,), anchor, tracking)
    layer = layer.filter(ImageFilter.GaussianBlur(radius))
    out = Image.alpha_composite(base.convert("RGBA"), layer)
    d2 = ImageDraw.Draw(out)
    _draw_tracked(d2, xy, text, font, fill + (255,), anchor, tracking)
    return out.convert("RGB")


def _draw_tracked(draw, xy, text, font, fill, anchor, tracking):
    """Draw text with per-character tracking; supports centered anchors."""
    if not tracking:
        draw.text(xy, text, font=font, fill=fill, anchor=anchor)
        return
    widths = [draw.textlength(ch, font=font) for ch in text]
    total = sum(widths) + tracking * (len(text) - 1)
    x, y = xy
    if anchor[0] == "m":
        x -= total / 2
    elif anchor[0] == "r":
        x -= total
    va = anchor[1] if len(anchor) > 1 else "a"
    for ch, w in zip(text, widths):
        draw.text((x, y), ch, font=font, fill=fill, anchor="l" + va)
        x += w + tracking


# --- wordmark: "PS5 IMAGE ▮ FORGE" ----------------------------------------

def draw_wordmark_stacked(img, cx, top, scale=1.0):
    """Vertical lockup for the tall side banner."""
    f_small = load_font(int(18 * scale))
    f_big = load_font(int(30 * scale))
    y = top
    # magenta accent block (the ▮ separator, promoted to a mark)
    block_w, block_h = int(30 * scale), int(30 * scale)
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.rectangle([cx - block_w // 2, y, cx + block_w // 2, y + block_h],
                fill=MAGENTA + (255,))
    layer = layer.filter(ImageFilter.GaussianBlur(5))
    img = Image.alpha_composite(img.convert("RGBA"), layer).convert("RGB")
    d = ImageDraw.Draw(img)
    d.rectangle([cx - block_w // 2, y, cx + block_w // 2, y + block_h],
                fill=MAGENTA)
    y += block_h + int(22 * scale)
    for word in ("PS5", "IMAGE", "FORGE"):
        img = glow_text(img, (cx, y), word, f_big, CYAN, CYAN,
                        anchor="ma", radius=6, tracking=int(4 * scale))
        y += int(34 * scale)
    return img, y


def build_welcome(version: str):
    """164x314 side banner for the Welcome/Finish pages."""
    W, H = 164, 314
    img = vgradient((W, H), BG2, BG)
    img = add_top_glow(img)
    img = add_grid(img, step=34, alpha=10)

    img, y = draw_wordmark_stacked(img, W // 2, 44, scale=1.0)

    d = ImageDraw.Draw(img)
    # thin cyan rule
    ry = y + 8
    d.line([(28, ry), (W - 28, ry)], fill=CYAN_DIM, width=1)
    # tagline
    f_tag = load_font(11)
    img = glow_text(img, (W // 2, ry + 16), "MOUNT-FREE  BUILDER",
                    f_tag, TEXT_DIM, PANEL, anchor="ma", radius=2, tracking=1)

    # bottom: version + faux status line
    d = ImageDraw.Draw(img)
    f_ver = load_font(13)
    d.rectangle([0, H - 34, W, H], fill=BG)
    d.line([(0, H - 34), (W, H - 34)], fill=CYAN_DIM, width=1)
    d.ellipse([16, H - 22, 24, H - 14], fill=OK)
    img = glow_text(img, (30, H - 24), f"v{version}", f_ver, CYAN, PANEL,
                    anchor="la", radius=2, tracking=1)
    img = add_scanlines(img, gap=3, alpha=34)
    return img


def build_header(version: str):
    """150x57 header strip (right-aligned on interior pages)."""
    W, H = 150, 57
    img = vgradient((W, H), PANEL, BG)
    img = add_grid(img, step=18, alpha=14)
    f = load_font(15)
    f2 = load_font(15)
    # two-line wordmark with the magenta block between IMAGE and FORGE
    img = glow_text(img, (W - 12, 9), "PS5 IMAGE", f, CYAN, CYAN,
                    anchor="ra", radius=4, tracking=1)
    d = ImageDraw.Draw(img)
    # magenta accent block
    d.rectangle([W - 12 - 10, 33, W - 12, 47], fill=MAGENTA)
    img = glow_text(img, (W - 26, 30), "FORGE", f2, CYAN, CYAN,
                    anchor="ra", radius=4, tracking=1)
    # left accent bar
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, 3, H], fill=CYAN)
    img = add_scanlines(img, gap=3, alpha=30)
    return img


def build_icon():
    """Emblem icon: neon-bordered dark panel with the PS5 monogram."""
    S = 256
    img = vgradient((S, S), BG2, BG)
    img = add_grid(img, step=32, alpha=14)
    img = add_top_glow(img, color=PANEL)
    d = ImageDraw.Draw(img)

    m = 24
    rect = [m, m, S - m, S - m]
    # neon cyan border with glow
    glow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(glow).rounded_rectangle(rect, radius=34,
                                           outline=CYAN + (255,), width=6)
    glow = glow.filter(ImageFilter.GaussianBlur(9))
    img = Image.alpha_composite(img.convert("RGBA"), glow).convert("RGB")
    d = ImageDraw.Draw(img)
    d.rounded_rectangle(rect, radius=34, outline=CYAN, width=4,
                        fill=PANEL)

    # monogram: "PS5" with magenta accent block
    f = load_font(96)
    img = glow_text(img, (S // 2 - 6, S // 2 - 8), "PS5", f, CYAN, CYAN,
                    anchor="mm", radius=10, tracking=2)
    d = ImageDraw.Draw(img)
    bw = 74
    d.rectangle([S // 2 - bw // 2, S - 78, S // 2 + bw // 2, S - 62],
                fill=MAGENTA)
    img = add_scanlines(img, gap=4, alpha=26)
    return img


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", default="0.0.0")
    args = ap.parse_args()

    ASSETS.mkdir(parents=True, exist_ok=True)

    welcome = build_welcome(args.version)
    welcome.save(ASSETS / "welcome.bmp", format="BMP")

    header = build_header(args.version)
    header.save(ASSETS / "header.bmp", format="BMP")

    icon = build_icon()
    icon.save(ASSETS / "installer.ico", format="ICO",
              sizes=[(256, 256), (128, 128), (64, 64), (48, 48),
                     (32, 32), (16, 16)])
    # a magenta-tinted variant for the uninstaller
    unicon = icon.copy()
    unicon.save(ASSETS / "uninstall.ico", format="ICO",
                sizes=[(256, 256), (64, 64), (48, 48), (32, 32), (16, 16)])

    print(f"Assets written to {ASSETS} (version {args.version})")
    for p in sorted(ASSETS.iterdir()):
        print(f"  {p.name:16} {p.stat().st_size:>8,} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
