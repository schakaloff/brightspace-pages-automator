"""Render SCORM's real review UI without starting browser work."""
import os
import sys
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFont, QFontDatabase
from panels.scorm_panel import SCORMPanel
from scorm_packages import SCORMRun
import gui_styles


class PreviewWindow:
    def load_config(self): return {}
    def save_config(self, data): pass


app = QApplication([])
QFontDatabase.addApplicationFont('C:/Windows/Fonts/segoeui.ttf')
app.setFont(QFont('Segoe UI', 10))
gui_styles.set_theme('dark')
app.setStyleSheet(gui_styles.get_stylesheet())
panel = SCORMPanel(PreviewWindow())
run = SCORMRun.open('https://learn.okanagancollege.ca/d2l/le/lessons/23840',
                    'https://mymoodle.okanagan.bc.ca/course/view.php?id=166972')
panel._bs_entry.setText(run.data['bs_url'])
panel._moodle_entry.setText(run.data['moodle_url'])
panel._run = run
panel._modules = [dict(id=r['module_id'], title=r['module_title']) for r in run.data['activities'] if r.get('module_id')]
panel._show_rows()
panel._set_busy(False)
panel._log.append_log('Nine original ZIP packages downloaded and validated. Review destination units before transferring.', 'success')
panel.resize(900, 800)
panel.show()
app.processEvents()
panel.grab().save('test-results/scorm-media-preview.png')
print('Saved test-results/scorm-media-preview.png')
