"""Restart gui.py on code changes; stop watching when the app exits."""
import subprocess
import sys
from pathlib import Path

WATCH_DIR = Path(__file__).parent
ENTRY = [sys.executable, str(WATCH_DIR / "gui.py")]
POLL_INTERVAL = 1  # seconds


def get_mtimes():
    files = list(WATCH_DIR.glob("*.py")) + list((WATCH_DIR / "src").rglob("*.py"))
    return {f: f.stat().st_mtime for f in files}


def stop_app(proc):
    """Stop and reap our app, including its Windows launcher/browser children."""
    if proc.poll() is not None:
        return
    if sys.platform == "win32":
        # A venv's python.exe can be a launcher for another Python process.
        # Terminating only the launcher would leave the actual app running.
        subprocess.run(
            ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    else:
        proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


def main():
    print(f"Watching {WATCH_DIR} for .py changes  (close the app or press Ctrl+C to stop)")
    proc = None

    try:
        mtimes = get_mtimes()
        proc = subprocess.Popen(ENTRY)
        while True:
            try:
                returncode = proc.wait(timeout=POLL_INTERVAL)
            except subprocess.TimeoutExpired:
                pass  # Still open: check whether a saved edit needs a restart.
            else:
                print(f"App exited (code {returncode}). Stopped watching.")
                return returncode

            new_mtimes = get_mtimes()
            changed = [f.name for f, t in new_mtimes.items() if mtimes.get(f) != t]
            if changed:
                # Closing the app takes priority over a simultaneous file edit.
                returncode = proc.poll()
                if returncode is not None:
                    print(f"App exited (code {returncode}). Stopped watching.")
                    return returncode
                print(f"-- changed: {changed} -- restarting gui.py --")
                mtimes = new_mtimes
                stop_app(proc)
                proc = subprocess.Popen(ENTRY)
    except KeyboardInterrupt:
        print("\nStopped.")
        return 0
    finally:
        if proc is not None:
            stop_app(proc)


if __name__ == "__main__":
    sys.exit(main())
