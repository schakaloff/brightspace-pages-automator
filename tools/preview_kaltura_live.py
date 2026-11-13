"""Render production Kaltura widgets with isolated sample journals, no browsers."""
import copy
import argparse
import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
from pathlib import Path
import sys
from types import SimpleNamespace
from contextlib import ExitStack
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtGui import QFont, QFontDatabase
import gui_styles
from kaltura_progress import KalturaRun
from panels.kaltura_panel import KalturaPanel
from panels.kaltura_saved_transfers import SavedKalturaTransfersDialog
from preview_kaltura_workflow import SAMPLES


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--shell', action='store_true')
    args = parser.parse_args()
    output = ROOT / 'test-results' / 'kaltura-workflow'
    if args.shell:
        output = output / 'app'
    directory = output / 'sample-records'
    directory.mkdir(parents=True, exist_ok=True)
    app = QApplication([])
    app.setStyle('Fusion')
    for name in ('segoeui.ttf', 'segoeuib.ttf', 'seguisym.ttf'):
        QFontDatabase.addApplicationFont(str(Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts' / name))
    app.setFont(QFont('Segoe UI', 10))
    run = KalturaRun.create('https://brightspace.example/d2l/home/23840',
                           'https://mymoodle.okanagan.bc.ca/course/view.php?id=166972', directory)
    run.capture_scan([dict(name=n, section_name=s, entry_id=id_, moodle_url=run.data['moodle_url']) for n,s,id_ in SAMPLES], [])
    run.data.update(course_name='Clinical Skills · Fall 2026', modules=[
        dict(id='1',title='Welcome'),dict(id='2',title='Week 1 · Foundations')])
    for i,row in enumerate(run.data['rows']):
        row.update(selected=i < 7, destination={'id':'1'} if i < 2 else {'id':'2'} if i < 5 else {'new':'Week 2 · Practice'} if i < 7 else None)
    run.data['rows'][0].update(state='already_present', topic_id='501')
    plan = copy.deepcopy(run.data)
    resources = ExitStack()
    window = None
    if args.shell:
        sys.path.insert(0, str(ROOT))
        import keyring
        import update_checker
        from gui import MainWindow
        for name in ('get_password', 'set_password', 'delete_password'):
            resources.enter_context(patch.object(keyring, name, lambda *args: None))
        resources.enter_context(patch.object(update_checker, 'get_update_diagnostics', lambda: {}))
        for name in ('_set_window_icon', '_load_api_key', '_start_chromium_check', '_start_update_check',
                     '_show_welcome_if_needed', '_refresh_update_diagnostics'):
            resources.enter_context(patch.object(MainWindow, name, lambda self: None))
        resources.enter_context(patch.object(MainWindow, 'load_config', lambda self: dict(welcomed=True)))
        resources.enter_context(patch.object(MainWindow, 'save_config', lambda self, data: None))
        resources.enter_context(patch.object(MainWindow, 'closeEvent', lambda self, event: event.accept()))
        window = MainWindow()
        window._stack.setCurrentWidget(window._kaltura)
        window._sidebar.set_active(6)
    def capture(widget, name, size):
        target = window if window is not None and widget is panel else widget
        target.resize(*size)
        target.show()
        for _ in range(5):
            app.processEvents()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        if window is not None and widget is panel and ('setup' in name or 'review' in name):
            print(f'{name}: window={window.width()}, panel={panel.width()}, minimum={panel.minimumSizeHint().width()}')
        assert target.grab().save(str(output/name))
    for theme in ('light','dark'):
        gui_styles.set_theme(theme)
        app.setStyleSheet(gui_styles.get_stylesheet())
        stub=SimpleNamespace(load_config=lambda:dict(kaltura_bs_url=run.data['bs_url'],kaltura_moodle_url=run.data['moodle_url']),save_config=lambda data:None)
        panel=window._kaltura if window else KalturaPanel(stub)
        panel._moodle_url.setText(run.data['moodle_url'])
        panel._bs_url.setText(run.data['bs_url'])
        if window:
            window.set_theme(theme)
        panel._progress_directory=directory
        panel._poll_timer.stop()
        for width,height in ((1040,920),(640,720)):
            panel._task_active=False
            panel._new_transfer()
            capture(panel,f'{theme}-{width}-setup.png',(width,height))
            panel._task_active=True
            panel._run_btn.setEnabled(False)
            panel._run_btn.setText('Connecting…')
            panel._update_progress(copy.deepcopy(plan))
            panel._set_stage(0)
            panel._status_line.setText('Sign in to Kaltura')
            panel._status_hint.setText('Finish sign-in or MFA in the Kaltura browser. Automation continues when the entries list is ready.')
            panel._services['Brightspace'].setText('Connected')
            panel._services['Kaltura'].setText('Your attention needed')
            panel._show_callback=lambda service:None
            panel._browser_row.show()
            capture(panel,f'{theme}-{width}-connect.png',(width,height))
            panel._show_review(copy.deepcopy(plan))
            panel._scroll.verticalScrollBar().setValue(0)
            capture(panel,f'{theme}-{width}-review.png',(width,height))
            data=copy.deepcopy(plan)
            data['modules'].append(dict(id='3',title='Week 2 · Practice'))
            for row in data['rows']:
                if (row.get('destination') or {}).get('new'): row['destination']={'id':'3'}
            data['rows'][1].update(state='created',attempted=True,topic_id='502')
            data['rows'][2].update(state='pending',attempted=True)
            panel._run_data=data
            panel._set_stage(2)
            panel._render_rows(False)
            panel._update_progress(data)
            panel._status_line.setText('Checking saved page: Preparing the workspace')
            panel._status_hint.setText('The Save action finished. Checking the destination before counting the page as created.')
            capture(panel,f'{theme}-{width}-create.png',(width,height))
            data['status']='paused'
            for index in (2,3,4): data['rows'][index].update(state='created',attempted=True,topic_id=str(502+index))
            data['rows'][5].update(state='needs_check',attempted=True,problem='The browser closed after Save. Check this destination before creating another copy.')
            data['rows'][6].update(state='ready')
            panel._task_active=False
            panel._run_data=data
            panel._show_results()
            capture(panel,f'{theme}-{width}-results.png',(width,height))
        if not window:
            panel.close()
        run.data=copy.deepcopy(data)
        run.save()
        library=SavedKalturaTransfersDialog(directory)
        library.table.selectRow(0)
        capture(library,f'{theme}-library.png',(920,700))
        capture(library,f'{theme}-library-narrow.png',(640,620))
        library.remove.click()
        library.filter.setCurrentIndex(library.filter.findData('removed'))
        library.table.selectRow(0)
        capture(library,f'{theme}-removed.png',(920,700))
        library.undo.click()
        library.close()
    print(output)
    if window:
        window.close()
    resources.close()


if __name__=='__main__': main()
