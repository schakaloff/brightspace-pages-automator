"""
Shared icon artwork — a page-with-AI-spark mark on the app's OC teal brand
tile. Used for the in-app window icon, the splash screen (gui.py), and the
static installer icons (tools/build_icon.py) so they can never drift apart.

Colors are the exact OC_TEAL_MID / OC_TEAL_BG / OC_ORANGE values from
gui_styles.py (identical in both light and dark themes — the mark is a
fixed brand identity, not something that should flip with the theme toggle).

Drawn at 4x and downsampled with LANCZOS so edges stay smooth/anti-aliased
at every requested size, from a 16px taskbar icon up to a 256px installer
tile — unlike hard-edged shapes drawn straight at target size, which look
blocky once scaled.
"""
import math

_TEAL_TOP    = (0, 122, 128)   # OC_TEAL_MID  "#007a80"
_TEAL_BOTTOM = (0, 46, 48)     # OC_TEAL_BG   "#002e30"
_LETTER      = (255, 255, 255)
_ACCENT      = (255, 130, 4)   # OC_ORANGE    "#FF8204"


def _sparkle(draw, cx, cy, r, inner_ratio=0.42, fill=_ACCENT):
    pts = []
    for i in range(8):
        angle = math.radians(i * 45 - 90)
        rad = r if i % 2 == 0 else r * inner_ratio
        pts.append((cx + rad * math.cos(angle), cy + rad * math.sin(angle)))
    draw.polygon(pts, fill=fill)


def draw_app_icon(size: int):
    from PIL import Image, ImageDraw

    ss = 4  # supersample factor
    s = size * ss
    scale = s / 64
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))

    # Rounded tile, vertical teal gradient
    gradient = Image.new("RGBA", (1, s), (0, 0, 0, 0))
    for y in range(s):
        t = y / max(s - 1, 1)
        gradient.putpixel((0, y), tuple(
            int(_TEAL_TOP[i] + (_TEAL_BOTTOM[i] - _TEAL_TOP[i]) * t) for i in range(3)
        ) + (255,))
    gradient = gradient.resize((s, s))
    mask = Image.new("L", (s, s), 0)
    margin = s * 0.06
    ImageDraw.Draw(mask).rounded_rectangle(
        [margin, margin, s - margin, s - margin], radius=s * 0.22, fill=255,
    )
    img.paste(gradient, (0, 0), mask)

    draw = ImageDraw.Draw(img)

    # Page with a folded top-right corner
    doc = [
        (18 * scale, 13 * scale),
        (36 * scale, 13 * scale),
        (47 * scale, 24 * scale),
        (47 * scale, 51 * scale),
        (18 * scale, 51 * scale),
    ]
    draw.polygon(doc, fill=_LETTER)

    # Fold shadow — cut from the same gradient so it blends seamlessly
    fold_mask = Image.new("L", (s, s), 0)
    ImageDraw.Draw(fold_mask).polygon(
        [(36 * scale, 13 * scale), (36 * scale, 24 * scale), (47 * scale, 24 * scale)],
        fill=255,
    )
    img.paste(gradient, (0, 0), fold_mask)

    # AI-spark accents — the app restyles pages with AI, so a spark lands
    # right on the folded corner, plus a small echo for balance.
    _sparkle(draw, 49 * scale, 15 * scale, 10 * scale)
    _sparkle(draw, 15 * scale, 48 * scale, 5 * scale, inner_ratio=0.38)

    return img.resize((size, size), Image.LANCZOS)
