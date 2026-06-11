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
