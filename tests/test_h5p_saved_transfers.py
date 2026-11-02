"""Saved history is readable, recoverable, and independent of course content."""
import sys
from unittest.mock import MagicMock

import pytest
from PySide6.QtCore import QItemSelectionModel, Qt

sys.path.insert(0, 'src')
from h5p_history import describe_run, set_run_removed
from h5p_progress import H5PRun, saved_runs
from panels.h5p_saved_transfers import SavedH5PTransfersDialog
from transfer_runner import PlanLock


def make_run(directory, title='Biology', kind='paused'):
    run = H5PRun.create('https://bs.example/d2l/home/101',
        'https://moodle.example/course/view.php?id=25', '101', directory=directory)
    run.data['course_name'] = title
    if kind != 'incomplete':
        run.data['moodle_items'] = [dict(type='SECTION', name='Week 1')]
    if kind in ('ready', 'paused', 'complete'):
        run.data['moodle_items'].append(dict(type='EXTERNAL', name='Practice', hint='h5p',
            href='https://moodle.example/mod/h5pactivity/view.php?id=12'))
    if kind in ('paused', 'complete'):
        run.data['activities'] = [dict(key='practice', filename='practice.h5p', name='Practice',
            should_grade=False, moodle_section='Week 1', bs_module_id='2', bs_module_title='Week 1',
            upload='confirmed', insertion='confirmed' if kind == 'complete' else 'pending')]
    if kind == 'complete':
        run.data['status'] = 'complete'
    run.save()
    return run


@pytest.mark.parametrize('kind,status', [('incomplete', 'Scan incomplete'),
    ('ready', 'Ready to review'), ('empty', 'No H5P activities'),
    ('paused', '1 remaining'), ('complete', 'Complete')])
def test_history_describes_actual_scan_and_transfer_state(tmp_path, kind, status):
    summary = describe_run(make_run(tmp_path, kind=kind))
    assert summary['status'] == status
    assert summary['route'] == 'Moodle 25 → Brightspace 101'
    if kind == 'ready':
        assert summary['progress'] == '1 activity found'


def test_remove_restore_keeps_journal_packages_and_original_saved_time(tmp_path):
    run = make_run(tmp_path)
    original = run.data.copy()
    packages = tmp_path / run.data['id'] / 'files'
    packages.mkdir(parents=True)
    (packages / 'practice.h5p').write_bytes(b'original package')
    set_run_removed(run.data['id'], True, tmp_path)
    assert saved_runs(tmp_path) == []
    assert len(saved_runs(tmp_path, include_removed=True)) == 1
    hidden = H5PRun.load(run.data['id'], tmp_path)
    assert hidden.data['updated'] == original['updated']
    assert hidden.data['activities'] == original['activities']
    assert (packages / 'practice.h5p').read_bytes() == b'original package'
    set_run_removed(run.data['id'], False, tmp_path)
    assert saved_runs(tmp_path)[0].data == original


def test_running_transfer_cannot_be_removed_or_restored(tmp_path):
    run = make_run(tmp_path)
    with PlanLock(run.path), pytest.raises(RuntimeError, match='already running'):
        set_run_removed(run.data['id'], True, tmp_path)
    assert len(saved_runs(tmp_path)) == 1
    with pytest.raises(ValueError):
        set_run_removed('../other', True, tmp_path)


def test_search_filter_and_continue_are_explicit(qtbot, tmp_path):
    biology = make_run(tmp_path, 'Biology', 'ready')
    make_run(tmp_path, 'Chemistry', 'complete')
    dialog = SavedH5PTransfersDialog(tmp_path)
    qtbot.addWidget(dialog)
    dialog.show()
    assert dialog.table.rowCount() == 2 and not dialog.open_button.isEnabled()
    dialog.filter.setCurrentIndex(dialog.filter.findData('complete'))
    assert dialog.table.rowCount() == 1 and dialog.table.item(0, 0).text().startswith('Chemistry')
    dialog.search.setText('Biology')
    assert dialog.table.rowCount() == 0 and not dialog.empty.isHidden()
    dialog.filter.setCurrentIndex(dialog.filter.findData('all'))
    assert dialog.table.rowCount() == 1
    qtbot.keyClick(dialog.search, Qt.Key.Key_Return)
    assert dialog.selected_run_id is None and dialog.isVisible()
    # Clicking the custom-painted course cell must still select the actual row.
    rect = dialog.table.visualItemRect(dialog.table.item(0, 0))
    qtbot.mouseClick(dialog.table.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())
    assert dialog.open_button.isEnabled()
    dialog.open_button.click()
    assert dialog.selected_run_id == biology.data['id']
    assert dialog.result() == dialog.DialogCode.Accepted


def test_bulk_remove_undo_and_restore_after_reopening(qtbot, tmp_path):
    runs = [make_run(tmp_path, title) for title in ('Biology', 'Chemistry')]
    dialog = SavedH5PTransfersDialog(tmp_path)
    qtbot.addWidget(dialog)
    for row in range(2):
        dialog.table.selectionModel().select(dialog.table.model().index(row, 0),
            QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)
    assert not dialog.open_button.isEnabled()
    dialog.remove.click()
    assert saved_runs(tmp_path) == [] and not dialog.table.isVisible()
    assert dialog.undo.isEnabled() and len(dialog._undo_ids) == 2
    dialog.undo.click()
    assert len(saved_runs(tmp_path)) == 2
    dialog.table.selectRow(0)
    removed_id = dialog.selected_runs()[0].data['id']
    dialog.remove.click()
    reopened = SavedH5PTransfersDialog(tmp_path)
    qtbot.addWidget(reopened)
    reopened.filter.setCurrentIndex(reopened.filter.findData('removed'))
    assert reopened.table.rowCount() == 1
    reopened.table.selectRow(0)
    assert not reopened.open_button.isEnabled()
    assert reopened.open_button.isHidden()
    assert reopened.remove.text() == 'Restore to list'
    reopened.remove.click()
    assert {run.data['id'] for run in saved_runs(tmp_path)} == {run.data['id'] for run in runs}
    assert not H5PRun.load(removed_id, tmp_path).data.get('removed_at')


def test_busy_entry_stays_visible_while_other_selected_entries_are_removed(qtbot, tmp_path):
    busy, idle = make_run(tmp_path, 'Busy'), make_run(tmp_path, 'Idle')
    dialog = SavedH5PTransfersDialog(tmp_path)
    qtbot.addWidget(dialog)
    dialog.table.selectAll()
    with PlanLock(busy.path):
        dialog.remove.click()
    assert [run.data['id'] for run in saved_runs(tmp_path)] == [busy.data['id']]
    assert H5PRun.load(idle.data['id'], tmp_path).data.get('removed_at')
    assert '1 could not be changed' in dialog.notice.text()


def test_panel_blocks_browsing_during_a_transfer_and_keeps_restore_access(qtbot, tmp_path, monkeypatch):
    import h5p_progress
    from panels.h5p_panel import H5PPanel
    monkeypatch.setattr(h5p_progress, 'RUNS_DIR', tmp_path)
    run = make_run(tmp_path)
    set_run_removed(run.data['id'], True, tmp_path)
    window = MagicMock()
    window.load_config.return_value = {}
    panel = H5PPanel(window)
    qtbot.addWidget(panel)
    assert panel._saved_count.text() == '0 saved'
    assert not panel._saved_card.isHidden() and panel._resume_btn.isEnabled()
    panel._task_active = True
    panel._refresh_saved_runs()
    assert not panel._resume_btn.isEnabled()
    panel._browse_saved_transfers()  # Must not launch a dialog while the worker is active.


@pytest.mark.parametrize('accepted', [True, False])
def test_panel_browse_resumes_only_an_explicitly_accepted_choice(qtbot, tmp_path, monkeypatch, accepted):
    from types import SimpleNamespace
    import h5p_progress
    import panels.h5p_saved_transfers as library_module
    from panels.h5p_panel import H5PPanel
    monkeypatch.setattr(h5p_progress, 'RUNS_DIR', tmp_path)
    run = make_run(tmp_path, kind='ready')
    run.data['grade_all'] = True
    run.save()
    dialog = SimpleNamespace(exec=lambda: int(accepted), selected_run_id=run.data['id'], deleteLater=MagicMock())
    monkeypatch.setattr(library_module, 'SavedH5PTransfersDialog', lambda *args: dialog)
    window = MagicMock()
    window.load_config.return_value = {}
    panel = H5PPanel(window)
    qtbot.addWidget(panel)
    panel._start_run = MagicMock()
    panel._resume_btn.click()
    if accepted:
        panel._start_run.assert_called_once_with(run.data['id'])
        assert panel._bs_entry.text() == run.data['bs_url']
        assert panel._moodle_entry.text() == run.data['moodle_url']
        assert panel._gradebook_cb.isChecked()
    else:
        panel._start_run.assert_not_called()
    dialog.deleteLater.assert_called_once()
