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

import gui_styles
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
        self.setMinimumSize(720, 560)
        self.resize(860, 640)
        self._claude_key   = ""
        self._claude_model = ""
        self._chromium_ready = False
        self._set_window_icon()
        self._build_ui()
        self._load_api_key()
        self._start_chromium_check()
        self._start_update_check()
        saved_theme = self.load_config().get("theme", "dark")
        self.set_theme(saved_theme)

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
            (4, "kaltura", "Kaltura"),
            (2, "collect", "Collect"),
            (None, None, "Optional"),
            (3, "restyle", "Restyle"),
            (5, "h5p", "H5P"),
        ])
        self._sidebar.step_clicked.connect(self._on_step)
        self._sidebar.settings_clicked.connect(self._on_settings)
        root.addWidget(self._sidebar)

        self._stack = QStackedWidget()
        self._stack.setObjectName("content")
        root.addWidget(self._stack, 1)

        # Panels imported lazily to keep imports fast
        from panels.checker_panel import CheckerPanel
        from panels.collector_panel import CollectorPanel
        from panels.restyle_panel import RestylePanel
        from panels.settings_panel import SettingsPanel
        from panels.kaltura_panel import KalturaPanel
        from panels.h5p_panel import H5PPanel

        self._checker   = CheckerPanel(self)
        self._collector = CollectorPanel(self)
        self._restyle   = RestylePanel(self)
        self._kaltura   = KalturaPanel(self)
        self._h5p       = H5PPanel(self)
        self._settings  = SettingsPanel(self)

        for panel in (self._checker, self._collector, self._restyle, self._kaltura, self._h5p, self._settings):
            self._stack.addWidget(panel)  # indices 0-5

        # All steps start unlocked — users can navigate freely
        for n in (1, 2, 3, 4, 5):
            self._sidebar.set_step_state(n, StepButton.PENDING)

        # Cross-panel wiring
        self._checker.step_success.connect(lambda: self._sidebar.set_step_state(1, StepButton.DONE))
        self._checker.continue_next.connect(lambda: self._on_step(2))
        self._collector.step_success.connect(lambda: self._sidebar.set_step_state(2, StepButton.DONE))
        self._collector.continue_next.connect(lambda: self._on_step(3))
        self._settings.api_key_changed.connect(self._set_api_key)
        self._settings.model_changed.connect(self._set_model)

        self._on_step(1)
        self._show_welcome_if_needed()

    def _show_welcome_if_needed(self):
        if self.load_config().get("welcomed"):
            return
        from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton
        import webbrowser, os
        dlg = QDialog(self)
        dlg.setWindowTitle("Welcome")
        dlg.setFixedSize(460, 380)
        dlg.setModal(True)
        v = QVBoxLayout(dlg)
        v.setContentsMargins(32, 28, 32, 24)
        v.setSpacing(0)

        title = QLabel("Welcome to Brightspace Pages Automator")
        title.setProperty("role", "header")
        title.setWordWrap(True)
        v.addWidget(title)
        v.addSpacing(10)

        desc = QLabel(
            "This tool automates migrating Moodle course content into Brightspace — "
            "checking content, collecting unit pages, and restyling them with OC brand themes."
        )
        desc.setProperty("role", "dim")
        desc.setWordWrap(True)
        v.addWidget(desc)
        v.addSpacing(20)

        steps_lbl = QLabel("Quick start:")
        steps_lbl.setStyleSheet("font-weight:700;font-size:13px;")
        v.addWidget(steps_lbl)
        v.addSpacing(8)

        for n, text in [
            ("1", "Go to Settings → save your Brightspace, SSO, and Moodle credentials"),
            ("2", "Go to Settings → add your Gemini API key (needed for Collect & Restyle)"),
            ("3", "Use Checker to compare courses and download missing files"),
            ("4", "Use Collect → Restyle to scrape and restyle unit pages"),
        ]:
            row = QHBoxLayout()
            row.setSpacing(10)
            badge = QLabel(n)
            badge.setFixedSize(22, 22)
            badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            badge.setStyleSheet(
                "background:#005F63;color:#fff;border-radius:11px;"
                "font-size:11px;font-weight:700;"
            )
            lbl = QLabel(text)
            lbl.setWordWrap(True)
            lbl.setProperty("role", "dim")
            row.addWidget(badge, 0, Qt.AlignmentFlag.AlignTop)
            row.addWidget(lbl, 1)
            v.addLayout(row)
            v.addSpacing(6)

        v.addStretch()

        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        guide_path = Path(__file__).parent / "WORKFLOW_GUIDE.html"
        guide_btn = QPushButton("Open Full Guide")
        guide_btn.setProperty("variant", "secondary")
        guide_btn.setFixedHeight(38)
        guide_btn.clicked.connect(
            lambda: webbrowser.open(f"file:///{str(guide_path).replace(os.sep, '/')}")
        )
        btn_row.addWidget(guide_btn)

        start_btn = QPushButton("Get Started")
        start_btn.setFixedHeight(38)
        def _dismiss():
            self.save_config({"welcomed": True})
            dlg.accept()
        start_btn.clicked.connect(_dismiss)
        btn_row.addWidget(start_btn)

        v.addLayout(btn_row)
        dlg.exec()

    def _on_step(self, n: int):
        idx = {1: 0, 2: 1, 3: 2, 4: 3, 5: 4}.get(n)
        if idx is not None:
            self._stack.setCurrentIndex(idx)
            self._sidebar.set_active(n)
            # Pull over URLs saved by the Checker tab when Collector is shown.
            if n == 2 and hasattr(self._collector, "refresh_carryover"):
                self._collector.refresh_carryover()

    def _on_settings(self):
        self._stack.setCurrentIndex(5)
        self._sidebar.set_active(None)

    # ── Theme ────────────────────────────────────────────────
    def set_theme(self, name: str):
        gui_styles.set_theme(name)
        QApplication.instance().setStyleSheet(gui_styles.get_stylesheet())
        self._sidebar.refresh_theme()
        self._settings.mark_active_theme(name)
        # Refresh log widgets in each panel
        for panel in (self._checker, self._collector, self._restyle, self._kaltura, self._h5p):
            for log in panel.findChildren(type(self._checker)):
                pass  # panels refresh via stylesheet
        from gui_log import LogWidget
        for log in self.findChildren(LogWidget):
            log.refresh_theme()
        self.save_config({"theme": name})

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

    @property
    def kmc_username(self) -> str:
        return self._settings.kmc_username

    @property
    def kmc_password(self) -> str:
        return self._settings.kmc_password

    # ── Claude API key / model ────────────────────────────────
    @property
    def claude_api_key(self) -> str:
        return self._claude_key

    @property
    def claude_model(self) -> str:
        return self._claude_model

    def _set_api_key(self, key: str):
        self._claude_key = key

    def _set_model(self, model: str):
        self._claude_model = model

    def _load_api_key(self):
        key = ""
        try:
            from api_config import CLAUDE_API_KEY as k
            key = k
        except ImportError:
            pass
        cfg = self.load_config()
        if not key:
            key = cfg.get("claude_api_key", "")
        self._claude_key = key
        self._settings.set_api_key(key)

        from ai_styler import DEFAULT_MODEL
        self._claude_model = cfg.get("claude_model", DEFAULT_MODEL)
        self._settings.set_model(self._claude_model)

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
        for panel in (
            self._checker,
            self._collector,
            self._restyle,
            self._kaltura,
            self._h5p,
        ):
            try:
                panel.save_state()
            except Exception:
                pass
        self.save_config({
            "claude_api_key": self._claude_key,
            "claude_model": self._claude_model,
        })
        event.accept()
        os._exit(0)


if __name__ == "__main__":
    if sys.platform == "win32" and sys.stdout is not None:
        sys.stdout.reconfigure(encoding="utf-8")

    app = QApplication(sys.argv)

    # HiDPI fix: detect DPR/logical-DPI mismatch and re-launch with correct scale
    if "QT_SCALE_FACTOR" not in os.environ:
        _s = app.primaryScreen()
        _dpr = _s.devicePixelRatio()
        _ldpi = _s.logicalDotsPerInch()
        if _dpr >= 1.5 and _ldpi < 120:
            os.environ["QT_SCALE_FACTOR"] = "1.5"
            app.quit()
            del app
            os.execv(sys.executable, [sys.executable] + sys.argv)
    app.setStyleSheet(gui_styles.get_stylesheet())
    win = MainWindow()
    win.show()
    sys.exit(app.exec())
