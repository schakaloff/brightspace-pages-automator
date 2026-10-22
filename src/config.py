import os
import json
import sys
from pathlib import Path

if os.name == "nt":
    USERDATA_DIR = Path(os.environ["APPDATA"]) / "BrightspaceAutomator"
else:
    USERDATA_DIR = Path.home() / ".local" / "share" / "BrightspaceAutomator"

USERDATA_DIR.mkdir(parents=True, exist_ok=True)


def gui_config_path(legacy: Path) -> Path:
    """Packaged app preferences must survive replacing a signed app bundle."""
    if not getattr(sys, 'frozen', False):
        return legacy
    target = USERDATA_DIR / 'user_config.json'
    if not target.exists() and legacy.is_file():
        try:
            data = json.loads(legacy.read_text(encoding='utf-8'))
            if isinstance(data, dict):
                target.write_text(json.dumps(data, indent=2), encoding='utf-8')
        except (OSError, ValueError):
            pass
    return target

# Shared with brightspace-quiz-automator — one login works for both tools
SESSION_FILE = str(USERDATA_DIR / "session.json")

SCREENSHOTS_DIR = USERDATA_DIR / "page_automator_screenshots"
SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)

# Must match AppMutex in installer/windows.iss exactly. The running app holds a
# mutex with this name so the installer can tell it is up and close it before
# replacing files. Changing one without the other silently breaks updates.
APP_MUTEX_NAME = "BrightspacePagesAutomator.SingleInstance"

# There is deliberately no relaunch switch here any more. Setup used to reopen
# the app itself via a [Run] entry gated on /RELAUNCH while the updater helper
# reopened it too — two paths racing to start the same app, which is how users
# ended up with two windows after an update. The helper in src/update_installer.py
# is now the only thing that relaunches, and it does so whether Setup succeeded
# or failed.
