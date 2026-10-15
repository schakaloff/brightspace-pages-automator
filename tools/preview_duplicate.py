"""Render the actual native duplication panel offline; no browser or course writes."""
import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from PySide6.QtWidgets import QApplication, QMainWindow
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtCore import Qt
import gui_styles
from panels.duplicate_panel import DuplicatePanel


def preview_course():
    def module(id_, title, children=()): return {'Id': id_, 'Title': title, 'Type': 0, 'children': list(children), 'IsHidden': False}
    def topic(id_, title, type_, url): return {'Id': id_, 'Title': title, 'Type': 1, 'ActivityType': type_, 'TopicType': 3 if type_ != 1 else 1, 'Url': url, 'IsHidden': False}
    return {'id': '25707', 'name': 'EA105 Sandbox', 'root': '/content/enforced/25707-preview/', 'modules': [
        module(10, 'Orientation and Successful Online Learning'), module(20, 'Anxiety in the Workplace'),
        module(30, 'Physical Health and Safety', [module(31, 'Before Class'), module(32, 'Class Materials', [
            topic(33, 'Session Overview', 1, '/content/enforced/25707-preview/session.html'),
            topic(34, 'Safety slides.pptx', 1, '/content/enforced/25707-preview/slides.pptx'),
            topic(35, 'First aid video', 2, 'https://example.org/video')]),
            module(36, 'After Class', [topic(37, 'Physical Health and Safety Assignment', 3, '/d2l/le/lessons/25707/topics/37')])]),
        module(40, 'Supports in Schools')]}


if __name__ == '__main__':
    app = QApplication([]); app.setStyle('Fusion')
    if not QFontDatabase.families():
        for name in ('segoeui.ttf', 'segoeuib.ttf'):
            QFontDatabase.addApplicationFont('C:/Windows/Fonts/' + name)
    app.setFont(QFont('Segoe UI', 11))
    out = ROOT / 'duplicate-preview'; out.mkdir(exist_ok=True)
    for theme in ('dark', 'light'):
        gui_styles.set_theme(theme); app.setStyleSheet(gui_styles.get_stylesheet())
        window = QMainWindow(); window.chromium_ready = True; window.load_config = lambda: {}
        panel = DuplicatePanel(window, data_dir=out / 'plans'); window.setCentralWidget(panel)
        window.resize(1213, 1058); window.show(); panel._set_course(preview_course())
        panel._items['30'].setCheckState(0, Qt.CheckState.Checked)
        panel._items['32'].setExpanded(True); panel._items['36'].setExpanded(True)
        for _ in range(4): app.processEvents()
        window.grab().save(str(out / (theme + '-desktop.png')))
        window.resize(760, 700)
        for _ in range(4): app.processEvents()
        window.grab().save(str(out / (theme + '-narrow.png')))
        panel._workspace_scroll.verticalScrollBar().setValue(panel._workspace_scroll.verticalScrollBar().maximum())
        for _ in range(4): app.processEvents()
        window.grab().save(str(out / (theme + '-narrow-settings.png')))
        panel._timer.stop(); window.close()
    print('Native panel screenshots saved to', out)
