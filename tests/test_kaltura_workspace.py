"""Native UI contracts: review/approval, back/stop and the shared library."""
import copy
import sys
from types import SimpleNamespace

from PySide6.QtCore import Qt, QPoint
from PySide6.QtTest import QTest
import pytest

sys.path.insert(0, 'src')
from kaltura_progress import KalturaRun, set_run_removed
from panels.kaltura_panel import KalturaPanel
from panels.kaltura_saved_transfers import SavedKalturaTransfersDialog

BS = 'https://brightspace.example/d2l/home/42'
MOODLE = 'https://mymoodle.okanagan.bc.ca/course/view.php?id=7'


def make_run(tmp_path):
    run = KalturaRun.create(BS, MOODLE, tmp_path)
    run.capture_scan([dict(entry_id='1_video', name='Video one', moodle_url=MOODLE, section_name='Week 1'),
                      dict(entry_id='1_other', name='Video two', moodle_url=MOODLE, section_name='Week 2')], [])
    run.data['modules'] = [dict(id='10', title='Week 1')]
    for row in run.data['rows']:
        row.update(selected=True, destination={'id': '10'})
    run.save()
    return run


@pytest.fixture
def panel(qtbot, tmp_path, monkeypatch):
    import panels.kaltura_workflow as workflow
    monkeypatch.setattr(workflow, 'saved_runs', lambda *args, **kwargs: [])
    item = KalturaPanel(SimpleNamespace(load_config=lambda: {}, save_config=lambda data: None))
    item._progress_directory = tmp_path
    item._poll_timer.stop()
    qtbot.addWidget(item)
    return item


def test_review_blocks_unmapped_approval_and_freezes_exact_snapshot(panel, tmp_path):
    run = make_run(tmp_path)
    run.data['rows'][1]['destination'] = None
    panel._task_active = True
    panel._show_review(copy.deepcopy(run.data))
    assert not panel._run_btn.isEnabled()
    assert 'Choose a destination' in panel._footer_hint.text()
    panel._results._groups[1].combo.setCurrentIndex(1)
    assert panel._run_btn.isEnabled()
    panel._primary()
    assert panel._review_answer['choices'][run.data['rows'][1]['key']]['destination'] == {'id': '10'}
    assert not panel._results._groups[1].combo.isEnabled()
    assert all(not check.isEnabled() for group in panel._results._groups for check, _ in group.video_checkboxes)


def test_back_waits_for_saved_worker_completion_and_keeps_review_draft(panel, tmp_path):
    run = make_run(tmp_path)
    panel._task_active = True
    panel._show_review(copy.deepcopy(run.data))
    panel._results._groups[0].set_all_checked(False)
    panel._go_back()
    assert panel._stop.is_set() and panel._task_active
    assert panel._back_pending and panel._stage == 1
    draft = None
    while not panel._drafts.empty(): draft = panel._drafts.get_nowait()
    assert not draft[run.data['rows'][0]['key']]['selected']
    panel._log_queue.put(('state', dict(stage='results', run=run.data)))
    panel._log_queue.put(('done', None))
    panel._poll_log()
    assert not panel._task_active and panel._stage == 0


def test_search_groups_and_keyboard_disclosure_do_not_change_choices(panel, tmp_path):
    run = make_run(tmp_path)
    panel._task_active = True
    panel._show_review(copy.deepcopy(run.data))
    before = panel._choices()
    group = panel._results._groups[0]
    group.expand_btn.setFocus()
    QTest.keyClick(group.expand_btn, Qt.Key.Key_Space)
    assert group.video_list_container.isHidden()
    panel._search.setText('1_other')
    assert group.isHidden() and not panel._results._groups[1].isHidden()
    assert panel._choices() == before


def test_wrapped_video_label_keeps_native_checkbox_click_behavior(panel, tmp_path):
    from panels.kaltura_panel import _VideoLabel
    run = make_run(tmp_path)
    panel._task_active = True
    panel._show_review(copy.deepcopy(run.data))
    group = panel._results._groups[0]
    check = group.video_checkboxes[0][0]
    title = group.findChildren(_VideoLabel)[0]
    assert check.isChecked()
    QTest.mouseClick(title, Qt.MouseButton.LeftButton)
    assert not check.isChecked()


def test_small_window_keeps_disclosure_and_destination_controls_inside_view(panel, tmp_path, qtbot):
    run = make_run(tmp_path)
    run.data['modules'][0]['title'] = 'A very long destination unit name ' * 5
    run.data['rows'][1]['destination'] = None
    panel._task_active = True
    panel._show_review(copy.deepcopy(run.data))
    panel.resize(552, 720)
    panel.show()
    qtbot.wait(20)
    assert all(step.geometry().right() < panel.width() for step in panel._steps)
    viewport = panel._scroll.viewport()
    for group in panel._results._groups:
        right = group.combo.mapTo(viewport, QPoint(group.combo.width(), 0)).x()
        assert right <= viewport.width()
        assert group.combo.width() >= 60


def test_results_report_actual_counts_and_uncertain_item_destination(panel, tmp_path):
    run = make_run(tmp_path)
    run.data['rows'][0].update(state='created', attempted=True, topic_id='123')
    run.data['rows'][1].update(state='needs_check', attempted=True, problem='Save response lost')
    panel._run_data = run.data
    panel._show_results()
    assert '1 created and verified' in panel._footer_hint.text()
    assert '1 need checking' in panel._footer_hint.text()
    assert panel._run_btn.isEnabled()
    assert not panel._issues_card.isHidden()
    assert panel._progress_bar.value() == 1


def test_kaltura_library_search_remove_undo_restore_preserves_journal(qtbot, tmp_path):
    run = make_run(tmp_path)
    dialog = SavedKalturaTransfersDialog(tmp_path)
    qtbot.addWidget(dialog)
    dialog.search.setText(run.data['id'])
    assert dialog.table.rowCount() == 1
    dialog.table.selectRow(0)
    dialog.remove.click()
    assert dialog.table.rowCount() == 0
    assert KalturaRun.load(run.data['id'], tmp_path).data['rows']
    dialog.undo.click()
    assert dialog.table.rowCount() == 1
    dialog.table.selectRow(0)
    dialog.remove.click()
    dialog.filter.setCurrentIndex(dialog.filter.findData('removed'))
    dialog.table.selectRow(0)
    assert dialog.remove.text() == 'Restore to list'
    dialog.remove.click()
    assert not KalturaRun.load(run.data['id'], tmp_path).data.get('removed_at')


def test_global_course_handoff_cannot_replace_an_active_or_reviewed_kaltura_target(panel):
    from gui import MainWindow
    panel._bs_url.setText(BS)
    checker = SimpleNamespace(_bs_entry=SimpleNamespace(text=lambda: 'https://brightspace.example/d2l/home/99'),
                              _moodle_entry=SimpleNamespace(text=lambda: MOODLE))
    window = SimpleNamespace(_checker=checker, _kaltura=panel)
    panel._task_active = True
    MainWindow._carry_course_urls(window, 4)
    assert panel._bs_url.text() == BS
    panel._task_active = False
    panel._stage = 3
    MainWindow._carry_course_urls(window, 4)
    assert panel._bs_url.text() == BS


def test_results_sentence_says_where_the_transfer_stands():
    from panels.kaltura_workflow import progress_sentence
    text = progress_sentence(dict(total=7, created=2, present=0, uncertain=5, failed=0, remaining=5, excluded=0))
    assert text.startswith('2 of 7 videos are in Brightspace. 5 were saved but not confirmed yet.')
    assert 'started' not in text


def test_check_retries_a_read_cut_off_by_navigation():
    import asyncio
    from kaltura_verification import _settled_read
    calls = []

    async def read():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError('Page.evaluate: Execution context was destroyed, most likely because of a navigation')
        return 'found'

    page = SimpleNamespace(wait_for_load_state=None)  # a broken wait must not stop the retry
    assert asyncio.run(_settled_read(page, read)) == 'found'
    assert len(calls) == 2
