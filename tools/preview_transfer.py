"""Render the Structured Transfer screens from saved course scans, without network or credential calls."""
import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
os.environ['BPA_SIMPLE_PREVIEW'] = '1'
import copy
import sys
import tempfile
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from PySide6.QtWidgets import QApplication, QMainWindow
from PySide6.QtGui import QFont, QFontDatabase
import panels.transfer_panel as transfer_panel
import transfer_outline as o
from transfer_runner import load_plan
import gui_styles

app = QApplication([])
if not QFontDatabase.families():
    for name in ('segoeui.ttf', 'segoeuib.ttf', 'seguisb.ttf', 'consola.ttf', 'seguisym.ttf', 'seguiemj.ttf'):
        QFontDatabase.addApplicationFont(str(Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts' / name))
app.setStyle('Fusion')
app.setFont(QFont('Segoe UI', 10))
out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / 'transfer-investigation' / 'ui'
out.mkdir(parents=True, exist_ok=True)
saved = load_plan(ROOT / 'transfer-investigation' / 'guided-workshop-plan.json')
transfer_panel.DATA_DIR = Path(tempfile.mkdtemp())


def shoot(theme):
    gui_styles.set_theme(theme)
    app.setStyleSheet(gui_styles.get_stylesheet())
    window = QMainWindow(); window.chromium_ready = True; window.load_config = lambda: {}
    panel = transfer_panel.TransferPanel(window)
    window.setCentralWidget(panel); window.resize(1487, 1000); window.show()

    def grab(name):
        for _ in range(6): app.processEvents()
        window.grab().save(str(out / f'{theme}-{name}.png'))
    panel.connect.source.setText(f"https://learn.okanagancollege.ca/d2l/home/{saved['source']['id']}")
    panel.connect.target.setText(f"https://learn.okanagancollege.ca/d2l/home/{saved['target']['id']}")
    panel.go(0); grab('1-connect')
    panel._scan = (copy.deepcopy(saved['source']), copy.deepcopy(saved['target'])); panel.go(1)
    panel.start.pattern.setCurrentIndex(panel.start.pattern.findText('EA105 workshop layout')); grab('2-start')
    panel._primary_clicked(); grab('3-arrange')
    waiting = o.unplaced(panel._plan)
    if waiting: panel.arrange.select_row(waiting[0]['id']); grab('3-arrange-selected')
    first = panel._plan['outline'][0]
    panel.arrange.select_node(first['children'][1]['id']); grab('3-arrange-outline-selected')
    panel.arrange.only_waiting.setChecked(True); grab('3-arrange-to-place')
    panel.arrange.only_waiting.setChecked(False)
    panel._secondary_clicked()
    panel.go(3); panel.styling.radios['strip_bpa'][1].setChecked(True); grab('4-styling')
    panel.go(4); grab('5-create')
    window.resize(820, 900); panel.go(2); grab('6-narrow-arrange')
    panel.go(4); grab('6-narrow-create')
    panel._timer.stop(); window.close()


for theme in ('light', 'dark'):
    shoot(theme)
print('Saved screenshots in', out)
