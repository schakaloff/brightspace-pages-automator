"""Render H5P workspace states using fictitious courses; never launch a worker."""
import copy
import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
import sys
import threading
import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from PySide6.QtWidgets import QApplication, QMainWindow
from PySide6.QtGui import QFont, QFontDatabase
import gui_styles
import panels.h5p_panel as hp


def main():
    app = QApplication([])
    app.setStyle('Fusion')
    for name in ('segoeui.ttf', 'segoeuib.ttf', 'seguisb.ttf', 'consola.ttf', 'seguisym.ttf'):
        QFontDatabase.addApplicationFont(str(Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts' / name))
    app.setFont(QFont('Segoe UI', 10))
    parser = argparse.ArgumentParser()
    parser.add_argument('--shell', action='store_true')
    args = parser.parse_args()
    out = ROOT / 'test-results' / 'h5p-design'
    out.mkdir(parents=True, exist_ok=True)
    hp.saved_runs = lambda *args: []
    if args.shell:
        sys.path.insert(0, str(ROOT))
        import keyring
        import update_checker
        from gui import MainWindow
        keyring.get_password = lambda *args: None
        keyring.set_password = lambda *args: None
        keyring.delete_password = lambda *args: None
        update_checker.get_update_diagnostics = lambda: {}
        for method in ('_set_window_icon', '_load_api_key', '_start_chromium_check',
                       '_start_update_check', '_show_welcome_if_needed', '_refresh_update_diagnostics'):
            setattr(MainWindow, method, lambda self: None)
        MainWindow.load_config = lambda self: dict(welcomed=True)
        MainWindow.save_config = lambda self, data: None
        MainWindow.closeEvent = lambda self, event: event.accept()
        window = MainWindow()
        panel = window._h5p
        window._stack.setCurrentWidget(panel)
        window._sidebar.set_active(6)
        out = out / 'app'
        out.mkdir(exist_ok=True)
    else:
        window = QMainWindow()
        window.chromium_ready = True
        window.load_config = lambda: {}
        panel = hp.H5PPanel(window)
        window.setCentralWidget(panel)
    panel._poll_timer.stop()
    panel._start_run = lambda *args: None
    window.show()
    plan = dict(run_id='a' * 32, course_name='Medical Terminology — Fall 2026',
        moodle_course_name='Medical Terminology (Moodle)',
        bs_url='https://learn.okanagancollege.ca/d2l/home/12345',
        moodle_url='https://mymoodle.okanagan.bc.ca/course/view.php?id=12345',
        modules=[dict(id='1', title='Week 1 · Foundations'), dict(id='2', title='Week 2 · The human body')],
        activities=[])
    for i, (name, section, target, present) in enumerate([
        ('Abbreviations practice', 'Week 1 · Foundations', '1', False),
        ('Match the medical terms', 'Week 1 · Foundations', '1', True),
        ('Anatomy image hotspots', 'Week 2 · The human body', '2', False),
        ('Body systems: knowledge check', 'Week 2 · The human body', '2', False),
        ('Final practice activity', 'Week 3 · Review', None, False)]):
        plan['activities'].append(dict(key=str(i), filename=f'{i}.h5p', name=name,
            moodle_section=section, bs_module_id=target,
            bs_module_title=next((m['title'] for m in plan['modules'] if m['id'] == target), None),
            should_grade=i == 3, selected=not present, already_present=present,
            upload='ready', insertion='confirmed' if present else 'ready'))

    def capture(name):
        for _ in range(4): app.processEvents()
        window.grab().save(str(out / name))

    for theme in ('dark', 'light'):
        if args.shell:
            window.set_theme(theme)
        else:
            gui_styles.set_theme(theme)
            app.setStyleSheet(gui_styles.get_stylesheet())
            panel.refresh_theme()
        panel._log.refresh_theme()
        for width, height in (((1487, 1058), (1120, 800), (800, 720), (624, 640)) if args.shell else ((1120, 900), (800, 720), (620, 640))):
            window.resize(width, height)
            panel._task_active = False
            panel._new_transfer()
            capture(f'{theme}-{width}-connect.png')
            panel._task_active = True
            panel._set_stage(0)
            panel._status_line.setText('Check the Moodle course in the browser')
            panel._status_hint.setText('Sign in if needed and open the correct course or section. Then continue here to find its activities.')
            panel._ready_btn.show()
            panel._stop_btn.show()
            panel._run_btn.setText('Finding activities…')
            panel._run_btn.setEnabled(False)
            capture(f'{theme}-{width}-connect-waiting.png')
            panel._ready_btn.hide()
            panel._show_plan(copy.deepcopy(plan), [None], threading.Event())
            panel._scroll.verticalScrollBar().setValue(0)
            capture(f'{theme}-{width}-review.png')
            panel._review_wait = None
            panel._set_stage(2)
            panel._run_btn.setText('Transferring…')
            panel._run_btn.setEnabled(False)
            panel._stop_btn.show()
            run = dict(id=plan['run_id'], course_name=plan['course_name'], bs_url=plan['bs_url'],
                moodle_course_name=plan['moodle_course_name'],
                moodle_url=plan['moodle_url'], status='running', activities=copy.deepcopy(plan['activities']))
            run['activities'][0].update(download='confirmed', upload='confirmed', insertion='confirmed')
            run['activities'][2].update(download='confirmed', upload='pending')
            panel._show_progress(run)
            panel._status_line.setText('Transferring your activities')
            panel._status_hint.setText('Uploading Anatomy image hotspots to the H5P library. Your progress is saved automatically.')
            capture(f'{theme}-{width}-transfer.png')
            panel._task_active = False
            panel._stop_btn.hide()
            panel._result_summary = dict(h5p_inserted=[('Abbreviations practice', 'Week 1')],
                h5p_already_present=[('Match the medical terms', 'Week 1')])
            run['status'] = 'paused'
            run['activities'][2]['problem'] = 'Upload needs checking. Resume to check the library before retrying.'
            panel._show_results(False)
            capture(f'{theme}-{width}-results.png')
    window.close()
    print(out)


if __name__ == '__main__':
    main()
