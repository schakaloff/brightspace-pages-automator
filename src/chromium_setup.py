"""
First-run Chromium presence check + installer.
Installers ship without the ~300MB Chromium binary; this module detects a
missing install and fetches it on first launch instead.
"""
import io
import sys


def is_chromium_installed() -> bool:
    from playwright.sync_api import sync_playwright

    p = sync_playwright().start()
    try:
        from pathlib import Path
        return Path(p.chromium.executable_path).exists()
    finally:
        p.stop()


def install_chromium(progress_cb) -> tuple[bool, str]:
    """
    Runs Playwright's own installer in-process (not via subprocess: a frozen
    build's sys.executable is the app itself, not a python interpreter, so
    `sys.executable -m playwright` would not work once packaged).
    """
    from playwright.__main__ import main as playwright_main

    old_argv = sys.argv
    old_stdout = sys.stdout
    buf = io.StringIO()

    class _Tee:
        def write(self, text):
            buf.write(text)
            for line in text.splitlines():
                if line.strip():
                    progress_cb(line)
            return len(text)

        def flush(self):
            pass

    sys.argv = ["playwright", "install", "chromium"]
    sys.stdout = _Tee()
    try:
        playwright_main()
        return True, ""
    except SystemExit as e:
        if e.code in (0, None):
            return True, ""
        return False, buf.getvalue()[-2000:]
    except Exception as e:
        return False, str(e)
    finally:
        sys.argv = old_argv
        sys.stdout = old_stdout
