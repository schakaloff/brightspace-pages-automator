"""
Shared icon artwork — a "BP" monogram (Brightspace Pages Automator) on a
rounded indigo square. Used both for the in-app window icon (gui.py) and the
static installer icons (tools/build_icon.py) so they can never drift apart.
"""
from PIL import Image, ImageDraw

_BG = "#4338CA"      # indigo-700
_LETTER = "#FFFFFF"

# Segment rectangles on a 64x64 grid, scaled to the requested size at draw time.
_B_SEGMENTS = [
    (8, 14, 14, 50),   # spine
    (8, 14, 26, 20),   # top cap
    (20, 14, 26, 30),  # upper-right
    (8, 29, 24, 35),   # mid bar
    (20, 35, 26, 50),  # lower-right
    (8, 44, 26, 50),   # bottom cap
]
_P_SEGMENTS = [
    (34, 14, 40, 50),  # spine
    (34, 14, 52, 20),  # top cap
    (46, 14, 52, 35),  # right vertical
    (34, 29, 52, 35),  # mid bar (closes the loop)
]


def draw_app_icon(size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    scale = size / 64

    radius = 14 * scale
    draw.rounded_rectangle(
        [4 * scale, 4 * scale, (64 - 4) * scale, (64 - 4) * scale],
        radius=radius, fill=_BG,
    )
    for x0, y0, x1, y1 in _B_SEGMENTS + _P_SEGMENTS:
        draw.rectangle([x0 * scale, y0 * scale, x1 * scale, y1 * scale], fill=_LETTER)
    return img
