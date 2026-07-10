"""Dev launcher: restarts gui.py automatically when any .py file changes."""
import subprocess
import sys
import time
from pathlib import Path

WATCH_DIR = Path(__file__).parent
ENTRY = [sys.executable, str(WATCH_DIR / "gui.py")]
POLL_INTERVAL = 1  # seconds


def get_mtimes():
    files = list(WATCH_DIR.glob("*.py")) + list((WATCH_DIR / "src").rglob("*.py"))
    return {f: f.stat().st_mtime for f in files}


if __name__ == "__main__":
    print(f"Watching {WATCH_DIR} for .py changes  (Ctrl+C to stop)")
    proc = subprocess.Popen(ENTRY)
    mtimes = get_mtimes()

    try:
        while True:
            time.sleep(POLL_INTERVAL)
            new_mtimes = get_mtimes()
            changed = [f.name for f, t in new_mtimes.items() if mtimes.get(f) != t]
            if changed:
                print(f"-- changed: {changed} -- restarting gui.py --")
                mtimes = new_mtimes
                if proc.poll() is None:
                    proc.terminate()
                    proc.wait()
                proc = subprocess.Popen(ENTRY)
    except KeyboardInterrupt:
        if proc.poll() is None:
            proc.terminate()
        print("\nStopped.")
