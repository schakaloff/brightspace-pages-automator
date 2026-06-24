# gui.py
import json
import os
import queue
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from PySide6.QtWidgets import QApplication, QMainWindow, QWidget, QHBoxLayout, QStackedWidget
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QIcon, QPixmap

from gui_styles import APP_STYLESHEET, BG
from gui_sidebar import Sidebar, StepButton
from gui_icons import make_icon

VERSION = "0.8.0"
_CONFIG_PATH = Path(__file__).parent / "user_config.json"


def _resource_path(*parts) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
    return base.joinpath(*parts)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Brightspace Pages Automator")
        self.setMinimumSize(960, 760)
        self.resize(1100, 800)
        self._gemini_key   = ""
        self._chromium_ready = False
        self._set_window_icon()
        self._build_ui()
        self._load_api_key()
        self._start_chromium_check()
        self._start_update_check()

    # ── Window icon (PIL → QPixmap) ──────────────────────────
    def _set_window_icon(self):
        try:
            from icon_art import draw_app_icon
            from PIL.ImageQt import ImageQt
            img = draw_app_icon(64)
            self.setWindowIcon(QIcon(QPixmap.fromImage(ImageQt(img))))
        except Exception:
            pass

    # ── UI ───────────────────────────────────────────────────
    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._sidebar = Sidebar([
            (1, "checker", "Checker"),
            (2, "collect", "Collect"),
            (3, "restyle", "Restyle"),
        ])
        self._sidebar.step_clicked.connect(self._on_step)
        self._sidebar.settings_clicked.connect(self._on_settings)
        root.addWidget(self._sidebar)

        self._stack = QStackedWidget()
        self._stack.setObjectName("content")
        root.addWidget(self._stack, 1)

        # Panels imported lazily to keep imports fast
        from gui_panels import CheckerPanel, CollectorPanel, RestylePanel, SettingsPanel

        self._checker   = CheckerPanel(self)
        self._collector = CollectorPanel(self)
        self._restyle   = RestylePanel(self)
        self._settings  = SettingsPanel(self)

        for panel in (self._checker, self._collector, self._restyle, self._settings):
            self._stack.addWidget(panel)  # indices 0-3

        # Cross-panel wiring
        self._checker.step_success.connect(lambda: self._unlock_step(2))
        self._checker.continue_next.connect(lambda: self._on_step(2))
        self._collector.step_success.connect(lambda: self._unlock_step(3))
        self._collector.continue_next.connect(lambda: self._on_step(3))
        self._settings.api_key_changed.connect(self._set_api_key)

        self._on_step(1)

    def _on_step(self, n: int):
        idx = {1: 0, 2: 1, 3: 2}.get(n)
        if idx is not None:
            self._stack.setCurrentIndex(idx)
            self._sidebar.set_active(n)

    def _on_settings(self):
        self._stack.setCurrentIndex(3)
        self._sidebar.set_active(None)

    def _unlock_step(self, n: int):
        self._sidebar.set_step_state(n, StepButton.PENDING)

    # ── Credentials (delegated to SettingsPanel) ─────────────
    @property
    def bs_username(self) -> str:
        return self._settings.bs_username

    @property
    def bs_password(self) -> str:
        return self._settings.bs_password

    @property
    def sso_email(self) -> str:
        return self._settings.sso_email

    @property
    def sso_password(self) -> str:
        return self._settings.sso_password

    @property
    def moodle_username(self) -> str:
        return self._settings.moodle_username

    @property
    def moodle_password(self) -> str:
        return self._settings.moodle_password

    # ── Gemini API key ───────────────────────────────────────
    @property
    def gemini_api_key(self) -> str:
        return self._gemini_key

    def _set_api_key(self, key: str):
        self._gemini_key = key

    def _load_api_key(self):
        key = ""
        try:
            from api_config import GEMINI_API_KEY as k
            key = k
        except ImportError:
            pass
        if not key:
            try:
                key = json.loads(_CONFIG_PATH.read_text()).get("gemini_api_key", "")
            except Exception:
                pass
        self._gemini_key = key
        self._settings.set_api_key(key)

    # ── Config helpers ───────────────────────────────────────
    def load_config(self) -> dict:
        try:
            return json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def save_config(self, data: dict):
        try:
            existing = self.load_config()
            existing.update(data)
            _CONFIG_PATH.write_text(json.dumps(existing, indent=2), encoding="utf-8")
        except Exception as e:
            print(f"[config] save failed: {e}", flush=True)

    # ── Chromium check ───────────────────────────────────────
    @property
    def chromium_ready(self) -> bool:
        return self._chromium_ready

    def _start_chromium_check(self):
        self._chromium_q = queue.Queue()
        self._chromium_timer = QTimer(self)
        self._chromium_timer.timeout.connect(self._chromium_poll)
        self._chromium_timer.start(150)
        threading.Thread(target=self._chromium_worker, daemon=True).start()

    def _chromium_worker(self):
        from chromium_setup import is_chromium_installed, install_chromium
        if is_chromium_installed():
            self._chromium_q.put(("ready", None))
            return
        self._chromium_q.put(("need_install", None))
        ok, err = install_chromium(
            progress_cb=lambda line: self._chromium_q.put(("progress", line))
        )
        self._chromium_q.put(("done", (ok, err)))

    def _chromium_poll(self):
        try:
            while True:
                kind, payload = self._chromium_q.get_nowait()
                if kind == "ready":
                    self._chromium_ready = True
                elif kind == "need_install":
                    self._show_chromium_dialog()
                elif kind == "progress":
                    if hasattr(self, "_chromium_log"):
                        self._chromium_log.append_log(payload, "info")
                elif kind == "done":
                    ok, err = payload
                    if hasattr(self, "_chromium_dlg"):
                        self._chromium_dlg.accept()
                    if ok:
                        self._chromium_ready = True
                    else:
                        from PySide6.QtWidgets import QMessageBox
                        QMessageBox.critical(self, "Chromium setup failed",
                            f"Could not download the browser engine:\n{err}\n\n"
                            "Check your internet connection and restart.")
        except queue.Empty:
            pass

    def _show_chromium_dialog(self):
        from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel
        from gui_log import LogWidget
        dlg = QDialog(self)
        dlg.setWindowTitle("Setting up browser engine")
        dlg.setFixedSize(480, 300)
        dlg.setModal(True)
        layout = QVBoxLayout(dlg)
        layout.addWidget(QLabel("Downloading browser engine (one-time setup)..."))
        log = LogWidget()
        layout.addWidget(log)
        self._chromium_dlg = dlg
        self._chromium_log = log
        dlg.show()

    # ── Update check ─────────────────────────────────────────
    def _start_update_check(self):
        self._update_q = queue.Queue()
        self._update_timer = QTimer(self)
        self._update_timer.timeout.connect(self._update_poll)
        self._update_timer.start(2000)
        threading.Thread(target=self._update_worker, daemon=True).start()

    def _update_worker(self):
        from update_checker import check_for_update
        release = check_for_update()
        if not release:
            return
        if self.load_config().get("skipped_update_tag") == release["tag"]:
            return
        self._update_q.put(release)

    def _update_poll(self):
        try:
            release = self._update_q.get_nowait()
            self._show_update_dialog(release)
            self._update_timer.stop()
        except queue.Empty:
            pass

    def _show_update_dialog(self, release: dict):
        from gui_dialogs import UpdateDialog
        dlg = UpdateDialog(release, self)
        dlg.exec()

    def closeEvent(self, event):
        self.save_config({
            "gemini_api_key": self._gemini_key,
        })
        super().closeEvent(event)


if __name__ == "__main__":
    if sys.platform == "win32" and sys.stdout is not None:
        sys.stdout.reconfigure(encoding="utf-8")
    app = QApplication(sys.argv)
    app.setStyleSheet(APP_STYLESHEET)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())
