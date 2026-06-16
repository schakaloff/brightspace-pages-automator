# PyInstaller spec — macOS .app bundle.
# Build with: pyinstaller installer/brightspace_automator_mac.spec --noconfirm
from pathlib import Path

from PyInstaller.utils.hooks import collect_all

ROOT = Path(SPECPATH).parent

SRC_MODULES = [
    "ai_styler", "automator", "browser", "config",
    "style_migrator", "unit_collector", "chromium_setup", "icon_art",
]

collect_datas, collect_binaries, collect_hidden = [], [], []
for pkg in ("customtkinter", "CTkMessagebox"):
    d, b, h = collect_all(pkg)
    collect_datas += d
    collect_binaries += b
    collect_hidden += h

a = Analysis(
    [str(ROOT / "gui.py")],
    pathex=[str(ROOT), str(ROOT / "src")],
    binaries=collect_binaries,
    datas=[
        (str(ROOT / "templates" / "style_reference.html"), "templates"),
        *collect_datas,
    ],
    hiddenimports=[
        "customtkinter",
        "CTkMessagebox",
        "google.genai",
        "google.genai.errors",
        "bs4",
        "lxml",
        "lxml.etree",
        "PIL",
        "PIL.Image",
        "PIL.ImageDraw",
        "playwright",
        "playwright.__main__",
        "playwright.sync_api",
        "playwright.async_api",
        *SRC_MODULES,
        *collect_hidden,
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="BrightspacePagesAutomator",
    debug=False,
    strip=False,
    upx=False,
    console=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="BrightspacePagesAutomator",
)

app = BUNDLE(
    coll,
    name="Brightspace Pages Automator.app",
    icon=str(ROOT / "assets" / "icon.icns"),
    bundle_identifier="com.brightspacepagesautomator.app",
)
