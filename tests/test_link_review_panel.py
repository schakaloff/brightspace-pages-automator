from dataclasses import replace
import sys

import pytest
from PySide6.QtWidgets import QApplication, QDialogButtonBox, QLineEdit, QLabel

sys.path.insert(0, "src")

from panels.link_review_panel import LinkReviewPanel


@pytest.fixture
def panel(qtbot):
    result = LinkReviewPanel()
    result.resize(960, 720)
    qtbot.addWidget(result)
    result.show()
    QApplication.processEvents()
    return result


def test_cancelled_editor_does_not_change_draft(panel, qtbot):
    before = replace(panel.links[0])
    panel._edit_link("notes")
    dialog = panel._active_dialog
    dialog._replacement.setText("https://example.test/other.pdf")
    dialog._radios["new"].setChecked(True)
    dialog.reject()
    assert panel.links[0] == before
    assert panel._active_dialog is None


def test_editor_changes_only_selected_link_and_review_shows_exact_url(panel):
    before = replace(panel.links[1])
    panel._edit_link("notes")
    dialog = panel._active_dialog
    dialog._replacement.setText("https://example.test/replacement.pdf")
    dialog._radios["new"].setChecked(True)
    dialog._save()
    assert panel.links[0].replacement == "https://example.test/replacement.pdf"
    assert panel.links[0].opening == "new"
    assert panel.links[1] == before
    panel._next()
    assert panel._pages.currentIndex() == 1
    text = " ".join(widget.text() for widget in panel._review.findChildren(QLabel))
    assert "https://example.test/replacement.pdf" in text
    assert "New tab" in text
    panel._back()
    assert panel.links[0].selected


def test_hidden_working_selection_is_preserved_and_applied_to_sample_only(panel):
    panel._toggle_working()
    panel._edit_link("guide")
    panel._active_dialog._radios["new"].setChecked(True)
    panel._active_dialog._save()
    panel._toggle_working()
    assert "guide" not in panel._checks
    assert panel.links[3].selected
    assert "1 hidden" in panel._count.text()
    panel._next()
    panel._next()
    assert panel.links[3].current_opening == "new"
    assert not any(link.selected for link in panel.links)
    assert "No course was changed" in panel._message.text()


def test_unresolved_selected_link_blocks_review(panel):
    panel._checks["reading"].setChecked(True)
    assert not panel._review_btn.isEnabled()
    panel._edit_link("reading")
    panel._active_dialog._replacement.setText("https://example.test/reading.pdf")
    panel._active_dialog._save()
    assert panel._review_btn.isEnabled()


def test_add_action_accepts_manual_link_then_opens_its_editor(panel):
    panel._manual_link()
    fields = panel._manual_dialog.findChildren(QLineEdit)
    for field, text in zip(fields, ["Extra.pdf", "Week 2", "https://example.test/missing.pdf"]):
        field.setText(text)
    actions = panel._manual_dialog.findChild(QDialogButtonBox)
    actions.button(QDialogButtonBox.StandardButton.Ok).click()
    assert panel.links[-1].title == "Extra.pdf"
    assert panel._active_dialog.draft.id == panel.links[-1].id
    panel._active_dialog.reject()


def test_reset_does_not_open_a_location_dialog(panel):
    panel._next()
    panel.reset_preview()
    assert panel._active_dialog is None
    assert not hasattr(panel, "_location_dialog")
    assert panel._review_btn.text() == "Review changes"
    assert sum(link.selected for link in panel.links) == 2


@pytest.mark.parametrize("width", [560, 960])
def test_small_layout_keeps_primary_action_visible_without_horizontal_scroll(panel, width):
    panel.resize(width, 560)
    QApplication.processEvents()
    assert panel._scroll.horizontalScrollBar().maximum() == 0
    assert panel.rect().contains(panel._review_btn.mapTo(panel, panel._review_btn.rect().center()))
    panel._scroll.ensureWidgetVisible(panel._checks["reading"])
    QApplication.processEvents()
    viewport = panel._scroll.viewport()
    assert viewport.rect().contains(panel._checks["reading"].mapTo(viewport, panel._checks["reading"].rect().center()))
    assert panel.links[0].selected


def test_regular_app_routes_between_preview_and_existing_checker(qtbot, monkeypatch):
    from gui import MainWindow
    for method in ("_set_window_icon", "_load_api_key", "_start_chromium_check",
                   "_start_update_check", "_show_welcome_if_needed"):
        monkeypatch.setattr(MainWindow, method, lambda self: None)
    monkeypatch.setattr(MainWindow, "load_config", lambda self: {"welcomed": True})
    monkeypatch.setattr(MainWindow, "save_config", lambda self, data: None)
    monkeypatch.setattr(MainWindow, "closeEvent", lambda self, event: event.accept())
    window = MainWindow()
    qtbot.addWidget(window)
    window._checker._preview_btn.click()
    assert window._stack.currentWidget() is window._link_review
    window._link_review._existing_btn.click()
    assert window._stack.currentWidget() is window._checker
