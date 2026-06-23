# PyInstaller spec — Windows onedir build.
# Build with: pyinstaller installer/brightspace_automator.spec --noconfirm
from pathlib import Path

from PyInstaller.utils.hooks import collect_all

ROOT = Path(SPECPATH).parent

SRC_MODULES = [
    "ai_styler", "automator", "browser", "config",
    "style_migrator", "unit_collector", "chromium_setup", "icon_art",
    "update_checker",
]

# CI writes BUILD_VERSION (the exact release tag) before invoking PyInstaller so
# update_checker can compare its own build against the latest GitHub release.
# Optional for local/manual builds — update_checker degrades gracefully if absent.
extra_datas = []
if (ROOT / "BUILD_VERSION").exists():
    extra_datas.append((str(ROOT / "BUILD_VERSION"), "."))

# customtkinter/CTkMessagebox ship theme JSON + image assets that PyInstaller's
# default analysis won't discover — collect_all pulls in their datas/binaries too.
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
        (str(ROOT / "prompts"), "prompts"),
        *extra_datas,
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

a.datas += Tree(str(ROOT / "assets"), prefix="assets")

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
    icon=str(ROOT / "assets" / "icon.ico"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="BrightspacePagesAutomator",
)
