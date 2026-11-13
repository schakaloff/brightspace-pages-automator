"""Reproduce current UI orchestration defects without browsers or real writes."""
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'src'))
from PySide6.QtWidgets import QApplication, QMessageBox
from panels.kaltura_panel import KalturaPanel, KalturaState, NEW_MODULE_SENTINEL
from kaltura_categorizer import KalturaCategorizer


class ImmediateThread:
    def __init__(self, target, **kwargs):
        self.target = target

    def start(self):
        self.target()


def complete_stub(coroutine):
    """All replaced backend coroutines finish immediately; no event loop needed."""
    try:
        coroutine.send(None)
    except StopIteration as result:
        return result.value
    finally:
        coroutine.close()
    raise AssertionError('A real async operation unexpectedly reached the probe.')


def main():
    if not hasattr(KalturaPanel, "_start_login"):
        print("Legacy orchestration has been replaced. See tests/test_kaltura_workflow.py and tests/test_kaltura_workspace.py for current regression checks.")
        return
    app = QApplication([])
    stub = SimpleNamespace(load_config=lambda: {}, moodle_username='', moodle_password='',
        bs_username='', bs_password='', sso_email='', sso_password='', kmc_username='', kmc_password='')
    panel = KalturaPanel(stub)
    panel._poll_timer.stop()
    panel._moodle_url.setText('https://mymoodle.example/course/view.php?id=1')
    panel._bs_url.setText('https://bs.example/d2l/home/2')

    async def failed_login(*args, **kwargs):
        raise RuntimeError('Synthetic login failure')

    with patch('panels.kaltura_panel.threading.Thread', ImmediateThread), \
         patch('panels.kaltura_panel.asyncio.run', complete_stub), \
         patch.object(KalturaCategorizer, 'login_to_moodle', failed_login):
        panel._start_login()
        panel._poll_log()
    false_success = any('Moodle session saved' in text for text, tag in panel._log._entries)
    panel._populate_groups([
        dict(name='Selected video', section_name='A', entry_id='1_a'),
        dict(name='Unmapped video', section_name='B', entry_id='1_b'),
        dict(name='Unselected video', section_name='C', entry_id='1_c'),
    ])
    panel._populate_combos([dict(id='10', title='A')])
    groups = panel._results._groups
    groups[0].combo.setCurrentIndex(1)
    groups[0].set_all_checked(True)
    groups[1].set_all_checked(True)
    groups[2].combo.blockSignals(True)
    groups[2].combo.insertItem(1, 'New: Empty unit', (NEW_MODULE_SENTINEL, 'Empty unit'))
    groups[2].combo.setCurrentIndex(1)
    groups[2].combo.blockSignals(False)
    groups[2].set_all_checked(False)
    captured = {}

    async def modules(self, names, *args, **kwargs):
        captured['modules_requested'] = names
        return {'Empty unit': '30'}

    async def embed(self, entries, mapping, url, *args, **kwargs):
        captured.update(entries=[entry['name'] for entry in entries], mapping=mapping, bs_url=url)

    panel._state = KalturaState.RESULTS
    panel._bs_url.setText('https://bs.example/d2l/home/999')
    with patch('panels.kaltura_panel.threading.Thread', ImmediateThread), \
         patch('panels.kaltura_panel.asyncio.run', complete_stub), \
         patch.object(QMessageBox, 'warning', return_value=QMessageBox.StandardButton.Yes), \
         patch.object(KalturaCategorizer, 'create_modules', modules), \
         patch.object(KalturaCategorizer, 'embed_entries', embed):
        panel._start_create_pages()
        panel._poll_log()
    if 'entries' not in captured:
        raise AssertionError(dict(captured=captured, log=panel._log._entries))
    result = dict(login_failure_reports_session_saved=false_success,
        unselected_modules_reach_create=captured['modules_requested'],
        selected_entries=captured['entries'], resolved_mapping=captured['mapping'],
        changed_course_used_with_old_mapping=captured['bs_url'],
        primary_button_after_run=panel._primary_btn.text(),
        selections_remaining=panel._results.selected_count())
    assert false_success
    assert captured['modules_requested'] == ['Empty unit']
    assert 'Unmapped video' in captured['entries'] and 'B' not in captured['mapping']
    assert '/999' in captured['bs_url']
    Path(__file__).with_name('audit-probes.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result, indent=2))
    panel.close()


if __name__ == '__main__':
    main()
