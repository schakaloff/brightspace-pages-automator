"""Render Settings/Collector with in-memory config and credentials only."""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from PySide6.QtGui import QFont, QFontDatabase, QImage, QPainter
from PySide6.QtWidgets import QApplication, QScrollArea
import keyring
import update_checker
import gui_styles
from panels.settings_panel import SettingsPanel
from panels.collector_panel import CollectorPanel

CONFIG = {"welcomed": True, "sso_email": "instructor@example.edu", "moodle_username": "instructor", "bs_login_method": "sso"}
STORE = {("BrightspacePagesAutomator_SSO", "instructor@example.edu"): "example-only", ("BrightspacePagesAutomator_Moodle", "instructor"): "example-only"}
keyring.get_password = lambda service, user: STORE.get((service, user))
keyring.set_password = lambda service, user, password: STORE.update({(service, user): password})
keyring.delete_password = lambda service, user: STORE.pop((service, user), None)
DIAGNOSTICS = {"current_version": "Example build", "current_build": "preview", "install_path": "C:/Example/Brightspace Automator", "update_channel": "stable", "update_branch": "main", "last_update_result": "Not checked in this preview", "updater_log_path": "C:/Example/updater.log", "setup_log_path": "C:/Example/installer.log"}
update_checker.get_update_diagnostics = lambda: dict(DIAGNOSTICS)


class PreviewWindow:
    chromium_ready = False
    def load_config(self):
        return dict(CONFIG)
    def save_config(self, values):
        CONFIG.update(values)
    def set_theme(self, theme):
        gui_styles.set_theme(theme)
        app.setStyleSheet(gui_styles.get_stylesheet())
        settings.mark_active_theme(theme)
    def _run_update_check(self, **kwargs):
        pass


app = QApplication([])
app.setStyle("Fusion")
for name in ("segoeui.ttf", "segoeuib.ttf", "consola.ttf"):
    QFontDatabase.addApplicationFont(str(Path("C:/Windows/Fonts") / name))
app.setFont(QFont("Segoe UI", 10))
gui_styles.set_theme("dark")
app.setStyleSheet(gui_styles.get_stylesheet())
out = ROOT / "test-results" / "settings-design"
out.mkdir(parents=True, exist_ok=True)
settings = SettingsPanel(PreviewWindow())


def settle():
    for _ in range(4):
        app.processEvents()


def capture(widget, name):
    settle()
    widget.grab().save(str(out / name))


settings.show()
for theme in ("dark", "light"):
    gui_styles.set_theme(theme)
    app.setStyleSheet(gui_styles.get_stylesheet())
    settings.mark_active_theme(theme)
    for width, height in ((1487, 1058), (900, 720), (560, 640)):
        settings.resize(width, height)
        for index, name in enumerate(("accounts", "ai", "appearance", "help")):
            settings._tabs.setCurrentIndex(index)
            capture(settings, f"{theme}-{width}x{height}-{name}.png")
        print(f"{theme} requested {width}x{height}, actual {settings.width()}x{settings.height()}")
    settings.resize(900, 720)
    settings._tabs.setCurrentIndex(0)
    settings._toggle_account_form("moodle")
    capture(settings, f"{theme}-moodle-edit.png")
    settings._toggle_account_form("moodle")
    settings._tabs.setCurrentIndex(3)
    settings._troubleshooting_btn.setChecked(True)
    capture(settings, f"{theme}-troubleshooting.png")
    settings._troubleshooting_btn.setChecked(False)
settings.hide()

collector = CollectorPanel(PreviewWindow())
collector._unit_entry.setText("https://learn.example.edu/d2l/le/lessons/123/units/1")
collector._use_moodle_names.setChecked(True)
collector._moodle_entry.setText("https://mymoodle.okanagan.bc.ca/course/view.php?id=99")
collector.show()
for theme in ("dark", "light"):
    gui_styles.set_theme(theme)
    app.setStyleSheet(gui_styles.get_stylesheet())
    collector.refresh_theme()
    for width, height in ((1080, 1050), (720, 640)):
        collector.resize(width, height)
        for expanded in (False, True):
            collector._adv_btn.setChecked(expanded)
            capture(collector, f"{theme}-collector-{width}-{'expanded' if expanded else 'collapsed'}.png")
collector.hide()

# Verify the integration in the actual app shell while suppressing all startup
# tasks and retaining the in-memory credential store/configuration above.
from gui import MainWindow
for method in ("_set_window_icon", "_load_api_key", "_start_chromium_check", "_start_update_check", "_show_welcome_if_needed"):
    setattr(MainWindow, method, lambda self: None)
MainWindow.load_config = lambda self: dict(CONFIG)
MainWindow.save_config = lambda self, values: CONFIG.update(values)
MainWindow.closeEvent = lambda self, event: event.accept()
MainWindow._refresh_update_diagnostics = lambda self, diagnostics=None: self._settings.refresh_update_diagnostics(DIAGNOSTICS)
window = MainWindow()
window._on_settings()
window.resize(1487, 1058)
window.show()
for theme in ("dark", "light"):
    window.set_theme(theme)
    capture(window, f"{theme}-app-settings.png")
window.close()

reference_path = ROOT.parent / "settings-ux-review" / "settings-concept.png"
if reference_path.exists():
    source = QImage(str(reference_path))
    implementation = QImage(str(out / "dark-1487x1058-accounts.png"))
    combined = QImage(source.width() + implementation.width(), max(source.height(), implementation.height()), QImage.Format.Format_RGB32)
    combined.fill(0x10161f)
    painter = QPainter(combined)
    painter.drawImage(0, 0, source)
    painter.drawImage(source.width(), 0, implementation)
    painter.end()
    combined.save(str(out / "comparison-accounts.png"))
    source = source.copy(50, 420, 1380, 335)
    implementation = implementation.copy(25, 302, 1120, 330)
    combined = QImage(source.width() + implementation.width(), 335, QImage.Format.Format_RGB32)
    combined.fill(0x10161f)
    painter = QPainter(combined)
    painter.drawImage(0, 0, source)
    painter.drawImage(source.width(), 0, implementation)
    painter.end()
    combined.save(str(out / "comparison-form.png"))
print(out)
