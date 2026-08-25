# PyInstaller spec — macOS .app bundle.
# Build with: pyinstaller installer/brightspace_automator_mac.spec --noconfirm
from pathlib import Path

ROOT = Path(SPECPATH).parent

SRC_MODULES = [
    "ai_styler", "app_version", "automator", "browser", "config",
    "style_migrator", "unit_collector", "chromium_setup", "icon_art", "single_instance",
    "update_checker",
]

# CI writes BUILD_VERSION (the exact release tag) before invoking PyInstaller so
# update_checker can compare its own build against the latest GitHub release.
# Optional for local/manual builds — update_checker degrades gracefully if absent.
extra_datas = []
if (ROOT / "BUILD_VERSION").exists():
    extra_datas.append((str(ROOT / "BUILD_VERSION"), "."))
if (ROOT / "BUILD_COMMIT").exists():
    extra_datas.append((str(ROOT / "BUILD_COMMIT"), "."))

a = Analysis(
    [str(ROOT / "gui.py")],
    pathex=[str(ROOT), str(ROOT / "src")],
    binaries=[],
    datas=[
        (str(ROOT / "templates" / "style_reference.html"), "templates"),
        (str(ROOT / "prompts"), "prompts"),
        *extra_datas,
    ],
    hiddenimports=[
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
