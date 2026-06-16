"""
One-time export of the in-app lightning-bolt icon (see gui.py _set_window_icon)
to static assets/icon.ico and assets/icon.icns for installer branding.
Rerun manually if the design changes; not part of CI.
"""
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).parent.parent
ASSETS = ROOT / "assets"


def draw_icon(size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse([0, 0, size - 1, size - 1], fill="#0d9488")
    scale = size / 64
    bolt = [(x * scale, y * scale) for x, y in
            [(38, 6), (22, 34), (32, 34), (26, 58), (44, 28), (34, 28)]]
    draw.polygon(bolt, fill="#ffffff")
    return img


def main():
    ASSETS.mkdir(exist_ok=True)
    sizes = [16, 32, 48, 64, 128, 256]
    imgs = [draw_icon(s) for s in sizes]

    imgs[-1].save(ASSETS / "icon.ico", sizes=[(s, s) for s in sizes])
    imgs[-1].save(ASSETS / "icon.icns")

    print(f"Wrote {ASSETS / 'icon.ico'}")
    print(f"Wrote {ASSETS / 'icon.icns'}")


if __name__ == "__main__":
    main()
