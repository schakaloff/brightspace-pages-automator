# gui.py
import json
import os
import queue
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QHBoxLayout, QStackedWidget,
    QMessageBox,
)
from PySide6.QtCore import Qt, QTimer, QEventLoop
from PySide6.QtGui import QIcon, QPixmap

MIN_SPLASH_MS = 600

import gui_styles
from gui_sidebar import Sidebar, StepButton
from gui_icons import make_icon
from app_version import APP_VERSION
from gui_splash import StartupSplash

VERSION = APP_VERSION
_CONFIG_PATH = Path(__file__).parent / "user_config.json"


def _resource_path(*parts) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
    return base.joinpath(*parts)


def _load_saved_theme() -> str:
    try:
        return json.loads(_CONFIG_PATH.read_text(encoding="utf-8")).get("theme", "dark")
    except Exception:
        return "dark"
class MainWindow(QMainWindow):
    def __init__(self, splash: StartupSplash | None = None):
        super().__init__()
        self._splash = splash
        self.setWindowTitle("Brightspace Pages Automator")
        self.setMinimumSize(720, 560)
        self.resize(1120, 800)
        self._claude_key   = ""
        self._claude_model = ""
        self._chromium_ready = False
        self._set_window_icon()
        self._report_progress("Loading interface…")
        self._build_ui()
        self._report_progress("Loading credentials…")
        self._load_api_key()
        self._start_chromium_check()
        self._start_update_check()
        saved_theme = self.load_config().get("theme", "dark")
        self.set_theme(saved_theme)
        self._report_progress("Ready")

    def _report_progress(self, message: str):
        if self._splash is None:
            return
        stages = {"Loading interface…": 0.15, "Loading panels…": 0.4,
                  "Loading credentials…": 0.8, "Ready": 1.0}
        self._splash.set_stage(message, stages.get(message, self._splash.progress))
        QApplication.instance().processEvents()

    # ── Window icon (PIL → QPixmap) ──────────────────────────
    def _set_window_icon(self):
        try:
            from icon_art import draw_app_icon
            from PIL.ImageQt import ImageQt
            # Multiple native sizes so Windows picks a sharp match for every
            # context (title bar, taskbar, Alt+Tab, jumbo icons on high-DPI
            # displays) instead of stretching a single 64px bitmap.
            icon = QIcon()
            for size in (16, 24, 32, 48, 64, 128, 256, 512):
                icon.addPixmap(QPixmap.fromImage(ImageQt(draw_app_icon(size))))
            self.setWindowIcon(icon)
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
            (1, "checker", "Check & fix"),
            (6, "kaltura", "Media"),
            (7, "restyle", "Build & style"),
        ])
        for button in self._sidebar._step_buttons.values():
            button.set_show_dot(False)
        self._sidebar.step_clicked.connect(self._on_step)
        self._sidebar.settings_clicked.connect(self._on_settings)
        root.addWidget(self._sidebar)

        self._stack = QStackedWidget()
        self._stack.setObjectName("content")
        root.addWidget(self._stack, 1)

        # Parented to the central widget so it floats over whichever page is
        # showing — see gui_update_badge for why this is not in the layout.
        from gui_update_badge import UpdateBadge
        self._update_badge = UpdateBadge(central)
        self._update_badge.clicked_with_update.connect(self._on_update_badge_clicked)
        self._update_badge.show()

        # Panels imported lazily to keep imports fast
        self._report_progress("Loading panels…")
        from panels.checker_panel import CheckerPanel
        from panels.collector_panel import CollectorPanel
        from panels.restyle_panel import RestylePanel
        from panels.settings_panel import SettingsPanel
        from panels.kaltura_panel import KalturaPanel
        from panels.h5p_panel import H5PPanel
        from panels.cleanup_panel import CleanupPanel
        from panels.page_creator_panel import PageCreatorPanel
        from panels.workflow_hub import WorkflowHub

        self._checker   = CheckerPanel(self)
        self._collector = CollectorPanel(self)
        self._restyle   = RestylePanel(self)
        self._kaltura   = KalturaPanel(self)
        self._h5p       = H5PPanel(self)
        self._cleanup   = CleanupPanel(self)
        self._page_creator = PageCreatorPanel(self)
        self._settings  = SettingsPanel(self)
        # The sidebar holds three stable areas. New tools become a card in a
        # hub instead of another sidebar entry, so the sidebar never grows.
        self._media_hub = WorkflowHub(
            "Media",
            "Move interactive and video content from Moodle into Brightspace. "
            "Both tools use the course URLs from Check & fix.",
            [
                ("h5p", "H5P activities", "Download H5P activities from Moodle and place them in the matching Brightspace units."),
                ("kaltura", "Kaltura videos", "Find Moodle's Kaltura videos and create matching Brightspace pages."),
            ],
        )
        from panels.workflow_hub import BuildStyleHub
        self._build_hub = BuildStyleHub()

        for panel in (self._checker, self._collector, self._restyle,
                      self._kaltura, self._h5p, self._settings,
                      self._build_hub, self._media_hub, self._cleanup,
                      self._page_creator):
            self._stack.addWidget(panel)  # indices 0-9

        for n in (1, 6, 7):
            self._sidebar.set_step_state(n, StepButton.PENDING)

        # Cross-panel wiring
        self._checker.continue_next.connect(lambda: self._on_step(2))
        self._collector.continue_next.connect(lambda: self._on_step(3))
        self._settings.api_key_changed.connect(self._set_api_key)
        self._settings.model_changed.connect(self._set_model)
        self._checker.open_tool.connect(self._open_tool)
        self._build_hub.tool_selected.connect(self._open_tool)
        self._media_hub.tool_selected.connect(self._open_tool)

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

    # Page number -> (stack index, sidebar area it belongs to).
    #   1 Check & fix   2 Collect   3 Restyle   4 Kaltura   5 H5P
    #   6 Media hub     7 Build & style hub   8 Cleanup   9 Create pages
    _PAGES = {1: (0, 1), 2: (1, 7), 3: (2, 7), 4: (3, 6), 5: (4, 6),
              6: (7, 6), 7: (6, 7), 8: (8, 7), 9: (9, 7)}

    def _on_step(self, n: int):
        page = self._PAGES.get(n)
        if page is not None:
            idx, area = page
            self._stack.setCurrentIndex(idx)
            self._sidebar.set_active(area)
            if n in (4, 5):
                self._carry_course_urls(n)
            # Pull over URLs saved by the Checker tab when Collector is shown.
            if n == 2 and hasattr(self._collector, "refresh_carryover"):
                self._collector.refresh_carryover()
            if n == 8 and not self._cleanup._url.text().strip():
                self._cleanup._url.setText(self._checker._bs_entry.text().strip())
            if n == 9 and not self._page_creator._url.text().strip():
                self._page_creator._url.setText(self._checker._bs_entry.text().strip())

    def _open_tool(self, name: str):
        target = {"collect": 2, "restyle": 3, "kaltura": 4,
                  "h5p": 5, "cleanup": 8, "create_pages": 9}.get(name)
        if target is not None:
            self._on_step(target)

    def _carry_course_urls(self, step: int):
        bs = self._checker._bs_entry.text().strip()
        moodle = self._checker._moodle_entry.text().strip()
        panel = self._kaltura if step == 4 else self._h5p
        bs_field = getattr(panel, "_bs_url", None) if step == 4 else panel._bs_entry
        moodle_field = getattr(panel, "_moodle_url", None) if step == 4 else panel._moodle_entry
        if bs and bs_field is not None:
            bs_field.setText(bs)
        if moodle and moodle_field is not None:
            moodle_field.setText(moodle)

    def _on_settings(self):
        self._stack.setCurrentIndex(5)
        self._sidebar.set_active(None)
        self._refresh_update_diagnostics()

    # ── Theme ────────────────────────────────────────────────
    def set_theme(self, name: str):
        gui_styles.set_theme(name)
        QApplication.instance().setStyleSheet(gui_styles.get_stylesheet())
        self._sidebar.refresh_theme()
        self._update_badge.refresh_theme()
        self._settings.mark_active_theme(name)
        self._page_creator.refresh_theme()
        self._checker.refresh_theme()
        self._build_hub.refresh_theme()
        # Refresh log widgets in each panel
        for panel in (self._checker, self._collector, self._restyle,
                      self._kaltura, self._h5p, self._cleanup):
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

        import keyring
        cfg = self.load_config()

        # Migrate legacy plaintext key to keyring if found
        legacy_key = cfg.get("claude_api_key", "")
        if legacy_key:
            try:
                keyring.set_password("BrightspacePagesAutomator_Claude", "api_key", legacy_key)
                cfg.pop("claude_api_key", None)
                self.save_config(cfg)
            except Exception as e:
                print(f"[config] Failed to migrate API key to keyring: {e}", flush=True)

        if not key:
            key = keyring.get_password("BrightspacePagesAutomator_Claude", "api_key") or ""

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
            if "claude_api_key" in existing:
                del existing["claude_api_key"]
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
    # Nothing here ever interrupts the user. A check result only lights up the
    # corner badge; the dialog opens when the badge is clicked and never before.
    _RECHECK_MS = 30 * 60 * 1000

    def _start_update_check(self):
        # Anything the installer helper reported after we exited is folded into
        # the update state first, so a failed install is visible in Settings
        # even though the check that follows will say "Up to date".
        try:
            from update_checker import consume_installer_result
            consume_installer_result()
        except Exception:
            pass

        self._update_q = queue.Queue()
        self._update_timer = QTimer(self)
        self._update_timer.timeout.connect(self._update_poll)
        self._update_timer.start(2000)

        # Releases can land while the app sits open for hours, so keep looking
        # instead of checking once at launch.
        self._recheck_timer = QTimer(self)
        self._recheck_timer.timeout.connect(self._run_update_check)
        self._recheck_timer.start(self._RECHECK_MS)

        self._run_update_check()

    def _run_update_check(self, force_install: bool = False):
        threading.Thread(
            target=self._update_worker, args=(force_install,), daemon=True
        ).start()

    def _update_worker(self, force_install: bool = False):
        from update_checker import check_for_update, get_update_diagnostics, record_update_result
        try:
            release = check_for_update(force_install=force_install)
        except Exception as e:
            record_update_result("Failed", f"Update check crashed: {e}")
            release = None
        self._update_q.put((force_install, release, get_update_diagnostics()))

    def _update_poll(self):
        try:
            force_install, release, diagnostics = self._update_q.get_nowait()
        except queue.Empty:
            return
        self._refresh_update_diagnostics(diagnostics)
        if force_install:
            if release:
                self._show_update_dialog(release)
            else:
                # The check records why it failed; telling someone to check
                # their connection when GitHub rate-limited the request (or a
                # certificate failed to verify) sends them after the wrong
                # problem entirely.
                message = diagnostics.get("fetch_user_message") or (
                    "Could not reach the latest installer. Check your internet "
                    "connection and try again."
                )
                status = diagnostics.get("fetch_http_status")
                detail = diagnostics.get("last_update_detail", "")
                box = QMessageBox(self)
                box.setIcon(QMessageBox.Icon.Warning)
                box.setWindowTitle("Update check failed")
                box.setText(message)
                if detail:
                    box.setDetailedText(
                        f"{detail}\n\n"
                        f"HTTP status: {status if status != '' else '(none)'}\n"
                        f"Updater log: {diagnostics.get('updater_log_path', '')}"
                    )
                box.exec()
            return
        # check_for_update returns None when up to date, offline, or running
        # from source. Offline must not clear a badge we already earned.
        if release or not self._update_badge.has_update():
            self._update_badge.set_release(release)

    def _refresh_update_diagnostics(self, diagnostics: dict | None = None):
        if diagnostics is None:
            try:
                from update_checker import get_update_diagnostics
                diagnostics = get_update_diagnostics()
            except Exception:
                diagnostics = {}
        self._update_badge.set_diagnostics(diagnostics)
        if hasattr(self, "_settings") and hasattr(self._settings, "refresh_update_diagnostics"):
            self._settings.refresh_update_diagnostics(diagnostics)

    def _on_update_badge_clicked(self):
        release = self._update_badge.release()
        if release:
            self._show_update_dialog(release)
        else:
            self._run_update_check(force_install=True)

    def _show_update_dialog(self, release: dict):
        from gui_dialogs import UpdateDialog
        dlg = UpdateDialog(release, self)
        dlg.exec()
        self._refresh_update_diagnostics()
        if dlg.install_started():
            # Close from the main window after the modal dialog returns. The
            # window owns closeEvent/save_state, and it outlives the dialog.
            QTimer.singleShot(300, self.close)

    # ── Overlay placement ────────────────────────────────────
    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._reposition_badge()

    def showEvent(self, event):
        super().showEvent(event)
        self._reposition_badge()

    def _reposition_badge(self):
        badge = getattr(self, "_update_badge", None)
        if badge is not None:
            badge.reposition()

    def closeEvent(self, event):
        if self._restyle._busy:
            QMessageBox.information(self, "Pages are being restyled",
                "Use Stop after active pages in Restyle and wait for the current pages to finish before closing the app.")
            event.ignore()
            return
        if self._page_creator._creating:
            QMessageBox.information(self, "Pages are being created",
                "Use Stop in Create pages in bulk and wait for the current request to finish before closing the app.")
            event.ignore()
            return
        for panel in (
            self._checker,
            self._collector,
            self._restyle,
            self._kaltura,
            self._h5p,
            self._cleanup,
            self._page_creator,
        ):
            try:
                panel.save_state()
            except Exception:
                pass
        self.save_config({
            "claude_model": self._claude_model,
        })

        import keyring
        if self._claude_key:
            keyring.set_password("BrightspacePagesAutomator_Claude", "api_key", self._claude_key)
        else:
            try:
                keyring.delete_password("BrightspacePagesAutomator_Claude", "api_key")
            except keyring.errors.PasswordDeleteError:
                pass

        event.accept()
        os._exit(0)


def _claim_app_mutex():
    """Hold the named mutex the Inno installer looks for via AppMutex.

    Without this the installer cannot tell the app is running, so it neither
    closes it nor waits for it, and silently fails to overwrite the locked .exe.
    Returns (handle, already_running); the handle must stay referenced.
    """
    from single_instance import claim_single_instance
    return claim_single_instance()


if __name__ == "__main__":
    if sys.platform == "win32" and sys.stdout is not None:
        sys.stdout.reconfigure(encoding="utf-8")

    app = QApplication(sys.argv)
    app.setStyle("Fusion")

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
    # Claimed after the HiDPI re-exec above so the handle belongs to the process
    # that actually sticks around. Held for the lifetime of the app: the
    # installer's AppMutex check is how it knows we are running.
    _app_mutex, _already_running = _claim_app_mutex()
    if _already_running:
        # Second copies are almost always accidental — a double-clicked icon, or
        # a relaunch racing the one the updater already started. Say so and stop
        # rather than opening a window that fights the first one over the log,
        # the session file and the browser.
        QMessageBox.information(
            None,
            "Brightspace Pages Automator",
            "Brightspace Pages Automator is already running.\n\n"
            "Switch to the open window instead of starting a second copy.",
        )
        sys.exit(0)

    gui_styles.set_theme(_load_saved_theme())
    app.setStyleSheet(gui_styles.get_stylesheet())

    splash = StartupSplash()
    splash.show()
    app.processEvents()
    _splash_shown_at = time.monotonic()

    win = MainWindow(splash=splash)

    remaining_ms = MIN_SPLASH_MS - (time.monotonic() - _splash_shown_at) * 1000
    if remaining_ms > 0:
        _wait_loop = QEventLoop()
        QTimer.singleShot(int(remaining_ms), _wait_loop.quit)
        _wait_loop.exec()

    splash.set_stage("Ready", 1.0)
    win.show()
    splash.finish(win)
    sys.exit(app.exec())
