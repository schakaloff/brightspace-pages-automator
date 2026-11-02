"""Capture the native saved-transfer UI using isolated sample records only."""
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication
import gui_styles
import h5p_progress
from h5p_progress import H5PRun, saved_runs
from h5p_history import set_run_removed
from panels.h5p_panel import H5PPanel
from panels.h5p_saved_transfers import SavedH5PTransfersDialog


def main():
    output = ROOT / 'test-results' / 'h5p-saved-transfers'
    directory = output / 'sample-records'
    directory.mkdir(parents=True, exist_ok=True)
    h5p_progress.RUNS_DIR = directory
    if not saved_runs(directory, include_removed=True):
        samples = [
            ('Nick Usatenco Moodle Migration Sample', '25707', '187689', 10, 9, 'paused'),
            ('CDA in Lieu of CPE (NDAEB)', '23840', '166972', 4, 4, 'complete'),
            ('Moodle Migration Sample', '25565', '187689', 0, 0, 'ready'),
            ('Nick Usatenco Moodle Migration Sample', '25707', '187689', 10, 8, 'paused'),
            ('Nick Usatenco Moodle Migration Sample', '25707', '187689', 0, 0, 'incomplete'),
            ('Biology · Introduction and foundations', '25401', '185001', 0, 0, 'empty'),
            ('Nick Usatenco Moodle Migration Sample', '25707', '187689', 10, 0, 'paused'),
        ]
        for index, (title, course, source, total, placed, state) in enumerate(samples):
            run = H5PRun.create(f'https://bs.example/d2l/home/{course}',
                f'https://moodle.example/course/view.php?id={source}', course, directory=directory)
            run.data['course_name'] = title
            run.data['updated'] = f'2026-10-{27-index:02d}T14:35:12+00:00'
            run.data['status'] = 'complete' if state == 'complete' else 'paused'
            if state != 'incomplete':
                run.data['moodle_items'] = [dict(type='SECTION', name='Week 1')]
            if state == 'ready':
                run.data['moodle_items'] += [dict(type='EXTERNAL', name=f'Practice {i}', hint='h5p') for i in range(10)]
            run.data['activities'] = [dict(key=f'practice-{i}', filename=f'practice-{i}.h5p',
                name=f'Practice {i}', should_grade=False, moodle_section='Week 1', bs_module_id='2',
                bs_module_title='Week 1', upload='confirmed' if i < placed else 'ready',
                insertion='confirmed' if i < placed else 'ready') for i in range(total)]
            run.save(touch=False)
    # Reset only these dedicated sample records after a previous preview run.
    for run in saved_runs(directory, include_removed=True):
        if run.data.get('removed_at'):
            set_run_removed(run.data['id'], False, directory)
    app = QApplication([])
    app.setStyle('Fusion')
    if os.name == 'nt':
        for font in ('segoeui.ttf', 'segoeuib.ttf'):
            QFontDatabase.addApplicationFont(str(Path(os.environ['WINDIR']) / 'Fonts' / font))
    app.setFont(QFont('Segoe UI', 10))
    window = MagicMock()
    window.load_config.return_value = dict(h5p_moodle_url='https://moodle.example/course/view.php?id=187689',
        h5p_bs_url='https://bs.example/d2l/home/25707')

    def capture(widget, name):
        for _ in range(4):
            app.processEvents()
        widget.grab().save(str(output / name))

    for theme in ('light', 'dark'):
        gui_styles.set_theme(theme)
        app.setStyleSheet(gui_styles.get_stylesheet() + gui_styles.get_modern_stylesheet())
        panel = H5PPanel(window)
        panel._poll_timer.stop()
        panel.resize(960, 1000)
        panel.show()
        capture(panel, f'01-setup-{theme}.png')
        panel.close()
        dialog = SavedH5PTransfersDialog(directory)
        dialog.show()
        capture(dialog, f'02-library-{theme}.png')
        dialog.table.selectRow(0)
        capture(dialog, f'02-library-selected-{theme}.png')
        dialog.resize(560, 560)
        capture(dialog, f'03-narrow-{theme}.png')
        dialog.resize(920, 650)
        dialog.search.setText('25707')
        capture(dialog, f'04-search-{theme}.png')
        dialog.table.selectRow(0)
        dialog.remove.click()
        capture(dialog, f'05-removed-undo-{theme}.png')
        dialog.filter.setCurrentIndex(dialog.filter.findData('removed'))
        capture(dialog, f'06-restore-{theme}.png')
        dialog.undo.click()
        dialog.search.setText('No such course')
        capture(dialog, f'07-empty-{theme}.png')
        dialog.close()
    print(output)


if __name__ == '__main__':
    main()
