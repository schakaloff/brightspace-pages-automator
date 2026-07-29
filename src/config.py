import os
from pathlib import Path

if os.name == "nt":
    USERDATA_DIR = Path(os.environ["APPDATA"]) / "BrightspaceAutomator"
else:
    USERDATA_DIR = Path.home() / ".local" / "share" / "BrightspaceAutomator"

USERDATA_DIR.mkdir(parents=True, exist_ok=True)

# Shared with brightspace-quiz-automator — one login works for both tools
SESSION_FILE = str(USERDATA_DIR / "session.json")

SCREENSHOTS_DIR = USERDATA_DIR / "page_automator_screenshots"
SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)

# Must match AppMutex in installer/windows.iss exactly. The running app holds a
# mutex with this name so the installer can tell it is up and close it before
# replacing files. Changing one without the other silently breaks updates.
APP_MUTEX_NAME = "BrightspacePagesAutomator.SingleInstance"

# Switch the self-updater passes to the installer. The installer's normal launch
# entry is skipifsilent, so a silent update would never reopen the app; a Run
# entry gated on this flag is what actually brings it back.
# Inno reads this via {param:RELAUNCH|no}, which needs the =yes form — a bare
# /RELAUNCH would expand to an empty string and never match.
RELAUNCH_SWITCH = "/RELAUNCH=yes"
