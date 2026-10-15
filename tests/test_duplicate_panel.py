import copy
import sys
from pathlib import Path
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QMainWindow, QApplication, QDialog, QCheckBox, QPushButton
from PySide6.QtGui import QFont, QFontDatabase
from panels.duplicate_panel import DuplicatePanel
from content_duplicate import copy_nodes
from tests.test_content_duplicate import sample_course


@pytest.fixture
def panel(qtbot, tmp_path):
    if not QFontDatabase.families():
        QFontDatabase.addApplicationFont('C:/Windows/Fonts/segoeui.ttf')
    QApplication.instance().setFont(QFont('Segoe UI', 10))
    main = QMainWindow(); main.load_config = lambda: {}; main.chromium_ready = True
    result = DuplicatePanel(main, data_dir=tmp_path)
    qtbot.addWidget(result); result.resize(1200, 900); result.show()
    result._set_course(sample_course())
    yield result
    result._timer.stop()


def choose(panel): panel._items['10'].setCheckState(0, Qt.CheckState.Checked)


def test_structure_preview_tracks_names_counts_omissions_and_structure_only(panel):
    choose(panel)
    assert len(list(copy_nodes(panel._plan))) == 7
    panel._name.setText('My renamed workshop')
    panel._items['12'].setCheckState(0, Qt.CheckState.Unchecked)
    assert panel._plan['roots'][0]['title'] == 'My renamed workshop'
    assert len(list(copy_nodes(panel._plan))) == 6
    panel._copies.setValue(2)
    assert [n['title'] for n in panel._plan['roots']] == ['My renamed workshop', 'My renamed workshop (2)']
    assert panel._preview.topLevelItemCount() == 2
    panel._include.setChecked(False)
    assert len(list(copy_nodes(panel._plan))) == 2


def test_single_pages_require_named_destination_and_changed_course_disables_creation(panel):
    panel._items['11'].setCheckState(0, Qt.CheckState.Checked)
    assert not panel._review.isEnabled()
    panel._destination.setCurrentIndex(panel._destination.findData('30'))
    assert panel._review.isEnabled() and len(list(copy_nodes(panel._plan))) == 1
    panel._url.setText('https://learn.okanagancollege.ca/d2l/le/lessons/2')
    assert not panel._review.isEnabled()


def test_search_and_clear_do_not_create_or_remove_brightspace_content(panel):
    choose(panel); course = copy.deepcopy(panel._course)
    panel._search.setText('Handout')
    assert not panel._items['10'].isHidden() and panel._items['12'].isHidden()
    panel._search.clear(); panel._clear_selection()
    assert panel._plan is None and panel._preview.topLevelItemCount() == 0
    assert panel._course == course


def test_multiple_selected_roots_keep_individual_names(panel):
    choose(panel); panel._items['30'].setCheckState(0, Qt.CheckState.Checked)
    assert not panel._name.isEnabled()
    assert [n['title'] for n in panel._plan['roots']] == ['Workshop — Copy', 'Destination — Copy']


def test_saved_plan_resumes_and_active_plan_settings_are_frozen(panel):
    choose(panel); plan, path = copy.deepcopy(panel._plan), panel._path
    plan['run_signature'] = 'test signature'
    panel._restore_plan(plan, path)
    assert panel._review.isEnabled() and not panel._copies.isEnabled()
    assert panel._preview.topLevelItem(0).text(0) == 'Workshop — Copy'
    assert panel._selected() == plan['selected']


def test_restored_draft_keeps_custom_name_when_excluding_a_child(panel):
    choose(panel); panel._name.setText('My saved workshop')
    plan, path = copy.deepcopy(panel._plan), panel._path
    panel._restore_plan(plan, path)
    panel._items['12'].setCheckState(0, Qt.CheckState.Unchecked)
    assert panel._plan['roots'][0]['title'] == 'My saved workshop'
    assert len(list(copy_nodes(panel._plan))) == 6


def test_review_requires_confirmation_and_cancel_leaves_the_course_unchanged(panel, monkeypatch):
    choose(panel); created = []
    monkeypatch.setattr(panel, '_create_clicked', lambda: created.append(True))
    def cancel(): QApplication.activeModalWidget().reject()
    QTimer.singleShot(0, cancel); panel._review_clicked()
    assert not created and not panel._plan['reviewed']
    def confirm():
        dialog = QApplication.activeModalWidget()
        create = next(b for b in dialog.findChildren(QPushButton) if b.text() == 'Create hidden copies')
        assert not create.isEnabled()
        dialog.findChild(QCheckBox).setChecked(True)
        assert create.isEnabled(); create.click()
    QTimer.singleShot(0, confirm); panel._review_clicked()
    assert created == [True] and panel._plan['reviewed']


@pytest.mark.parametrize('width', [560, 760, 1200])
def test_responsive_workspace_keeps_actions_visible_without_horizontal_scroll(panel, width):
    choose(panel); panel.resize(width, 700); QApplication.processEvents()
    assert panel._workspace_scroll.horizontalScrollBar().maximum() == 0
    assert panel.rect().contains(panel._review.mapTo(panel, panel._review.rect().center()))
    assert panel.rect().contains(panel._load.mapTo(panel, panel._load.rect().center()))
    if width < 900: assert panel._workspace_scroll.verticalScrollBar().maximum() > 0
