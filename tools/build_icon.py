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
    sizes = [16, 32, 48, 64, 128, 256]
    imgs = [draw_app_icon(s) for s in sizes]

    imgs[-1].save(ASSETS / "icon.ico", sizes=[(s, s) for s in sizes])
    imgs[-1].save(ASSETS / "icon.icns")

    print(f"Wrote {ASSETS / 'icon.ico'}")
    print(f"Wrote {ASSETS / 'icon.icns'}")


if __name__ == "__main__":
    main()
