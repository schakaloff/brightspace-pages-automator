"""
One-time export of the in-app "BP" icon (see src/icon_art.py) to static
assets/icon.ico and assets/icon.icns for installer branding.
Rerun manually if the design changes; not part of CI.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))

from icon_art import draw_app_icon

ASSETS = ROOT / "assets"


def main():
    ASSETS.mkdir(exist_ok=True)
    # .ico caps at 256px natively (Pillow silently drops larger entries), so
    # that's already the ceiling for the Windows icon.
    ico_sizes = [16, 32, 48, 64, 128, 256]
    ico_imgs = [draw_app_icon(s) for s in ico_sizes]
    ico_imgs[-1].save(ASSETS / "icon.ico", sizes=[(s, s) for s in ico_sizes])

    # .icns supports up to 1024px (512pt @2x retina); Pillow auto-derives the
    # full mip chain from a single source image, so render at that size
    # instead of reusing the 256px .ico source.
    draw_app_icon(1024).save(ASSETS / "icon.icns")

    print(f"Wrote {ASSETS / 'icon.ico'}")
    print(f"Wrote {ASSETS / 'icon.icns'}")


if __name__ == "__main__":
    main()
