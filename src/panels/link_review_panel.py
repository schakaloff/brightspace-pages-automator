"""Interactive preview of the new review flow, isolated from live course writes."""

from dataclasses import replace
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton,
    QFrame, QScrollArea, QCheckBox, QDialog, QLineEdit, QFileDialog,
    QDialogButtonBox, QRadioButton, QButtonGroup, QMenu, QStackedWidget,
)

from link_review import LinkDraft, OPEN_LABELS, sample_links, selected_changes, valid_web_url
from panels._shared import NoScrollComboBox


def label(text, role="dim"):
    widget = QLabel(text)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    widget.setWordWrap(True)
    widget.setProperty("role", role)
    return widget


def button(text, callback, variant="secondary"):
    widget = QPushButton(text)
    widget.setProperty("variant", variant)
    widget.setCursor(Qt.CursorShape.PointingHandCursor)
    widget.clicked.connect(callback)
    return widget


class LinkEditorDialog(QDialog):
    """Edit a copy so cancellation cannot alter the selected plan."""

    def __init__(self, draft: LinkDraft, parent=None):
        super().__init__(parent)
        self.setProperty("design", "modern")
        self.setWindowTitle("Edit link")
        self.setMinimumWidth(420)
        self.resize(540, 480)
        self.draft = replace(draft)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(14)
        layout.addWidget(label(draft.title, "section-heading"))
        layout.addWidget(label(f"From {draft.page}"))
        layout.addWidget(label("Current destination", "field-label"))
        self._current = QLineEdit(draft.current_url)
        self._current.setReadOnly(True)
        layout.addWidget(self._current)
        layout.addWidget(label("Replacement", "field-label"))
        self._kind = NoScrollComboBox()
        for title, value in (("Replacement URL", "url"), ("Matching Moodle file", "moodle"),
                             ("Choose a local file", "file")):
            self._kind.addItem(title, value)
        self._kind.setCurrentIndex(max(0, self._kind.findData(draft.replacement_kind)))
        layout.addWidget(self._kind)
        field = QHBoxLayout()
        self._replacement = QLineEdit(draft.replacement)
        self._replacement.setPlaceholderText("https://…")
        field.addWidget(self._replacement, 1)
        self._browse = button("Browse…", self._choose_file)
        field.addWidget(self._browse)
        layout.addLayout(field)
        self._kind.currentIndexChanged.connect(self._kind_changed)
        self._kind_changed()
        layout.addWidget(label("Open this link", "field-label"))
        self._opening = QButtonGroup(self)
        self._radios = {}
        for key, title in OPEN_LABELS.items():
            radio = QRadioButton(title)
            self._opening.addButton(radio)
            self._radios[key] = radio
            radio.setChecked(key == draft.opening)
            layout.addWidget(radio)
        self._error = label("", "error")
        layout.addWidget(self._error)
        actions = QDialogButtonBox(QDialogButtonBox.StandardButton.Save |
                                   QDialogButtonBox.StandardButton.Cancel)
        actions.accepted.connect(self._save)
        actions.rejected.connect(self.reject)
        actions.button(QDialogButtonBox.StandardButton.Cancel).setProperty("variant", "secondary")
        layout.addWidget(actions)

    def _kind_changed(self):
        local = self._kind.currentData() == "file"
        self._browse.setVisible(local)
        self._replacement.setReadOnly(local)
        self._replacement.setPlaceholderText("Choose a file…" if local else "https://…")

    def _choose_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Choose replacement file")
        if path:
            self._replacement.setText(path)

    def _save(self):
        value = self._replacement.text().strip()
        kind = self._kind.currentData()
        if value and kind != "file" and not valid_web_url(value):
            self._error.setText("Enter a complete http or https URL.")
            return
        if kind == "file" and value and not Path(value).is_file():
            self._error.setText("Choose a file that exists on this computer.")
            return
        updated = replace(self.draft, replacement=value, replacement_kind=kind,
                          opening=next(key for key, radio in self._radios.items() if radio.isChecked()))
        if not updated.ready:
            self._error.setText("Choose a replacement for this missing file.")
            return
        updated.selected = updated.changed
        self.draft = updated
        self.accept()


class LinkReviewPanel(QWidget):
    open_checker = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("link_review_panel")
        self.setProperty("design", "modern")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.links = sample_links()
        self._show_working = False
        self._location = "Introduction to Computing / Lesson 1 resources"
        self._source_url = ""
        self._target_url = ""
        self._scope = "Page"
        self._checks = {}
        self._active_dialog = None
        self._build()
        self._render_links()

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(34, 32, 34, 24)
        layout.setSpacing(14)
        self._scroll.setWidget(body)
        root.addWidget(self._scroll, 1)
        head = QHBoxLayout()
        self._heading = label("Review links", "page-heading")
        head.addWidget(self._heading, 1)
        self._steps = label("Find   →   Choose   →   Apply")
        self._steps.setWordWrap(False)
        head.addWidget(self._steps)
        layout.addLayout(head)
        context = QHBoxLayout()
        self._context = label(self._location)
        context.addWidget(self._context, 1)
        self._location_btn = button("Change location", self._change_location, "link")
        context.addWidget(self._location_btn)
        layout.addLayout(context)
        source = QHBoxLayout()
        self._source_btn = button("Source: Moodle", self._change_location, "link")
        source.addWidget(self._source_btn)
        source.addWidget(label("Preview · sample data", "hint"))
        source.addStretch()
        layout.addLayout(source)
        line = QFrame()
        line.setProperty("role", "review-rule")
        line.setFixedHeight(1)
        layout.addWidget(line)
        toolbar = QHBoxLayout()
        self._summary = label("3 links need attention", "section-heading")
        toolbar.addWidget(self._summary, 1)
        self._add_btn = button("Add action", self._add_action)
        toolbar.addWidget(self._add_btn)
        layout.addLayout(toolbar)

        self._pages = QStackedWidget()
        self._choose = QWidget()
        choose_layout = QVBoxLayout(self._choose)
        choose_layout.setContentsMargins(0, 0, 0, 0)
        choose_layout.setSpacing(0)
        self._list = QWidget()
        self._list_layout = QVBoxLayout(self._list)
        self._list_layout.setContentsMargins(0, 0, 0, 0)
        self._list_layout.setSpacing(0)
        choose_layout.addWidget(self._list)
        self._working_btn = button("Show working links", self._toggle_working, "link")
        choose_layout.addWidget(self._working_btn, 0, Qt.AlignmentFlag.AlignLeft)
        choose_layout.addStretch(1)
        self._pages.addWidget(self._choose)
        self._review = QWidget()
        self._review_layout = QVBoxLayout(self._review)
        self._review_layout.setContentsMargins(0, 0, 0, 0)
        self._review_layout.setSpacing(16)
        self._pages.addWidget(self._review)
        layout.addWidget(self._pages, 1)
        self._message = label("", "hint")
        self._message.hide()
        layout.addWidget(self._message)
        self._existing_btn = button("Open existing Checker", self.open_checker.emit, "link")
        layout.addWidget(self._existing_btn, 0, Qt.AlignmentFlag.AlignLeft)

        footer = QFrame()
        footer.setObjectName("link_review_footer")
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(34, 18, 34, 18)
        self._count = label("2 changes selected")
        footer_layout.addWidget(self._count, 1)
        self._back_btn = button("Back", self._back)
        footer_layout.addWidget(self._back_btn)
        self._review_btn = button("Review changes", self._next, "primary")
        footer_layout.addWidget(self._review_btn)
        root.addWidget(footer)

    def _clear(self, layout):
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()

    def _render_links(self):
        self._clear(self._list_layout)
        self._checks = {}
        header = QWidget()
        grid = QGridLayout(header)
        grid.setContentsMargins(8, 8, 8, 12)
        self._select_all = QCheckBox()
        self._select_all.setAccessibleName("Select visible links")
        visible = [link for link in self.links if link.broken or self._show_working]
        self._select_all.setChecked(bool(visible) and all(link.selected for link in visible))
        self._select_all.clicked.connect(self._select_visible)
        grid.addWidget(self._select_all, 0, 0)
        grid.addWidget(label("Link", "field-label"), 0, 1)
        compact = self.width() < 780
        if not compact:
            grid.addWidget(label("Proposed change", "field-label"), 0, 2)
        grid.setColumnMinimumWidth(0, 32)
        grid.setColumnStretch(1, 5)
        if not compact:
            grid.setColumnStretch(2, 4)
            grid.setColumnMinimumWidth(3, 110)
        self._list_layout.addWidget(header)
        for link in visible:
            row = QFrame()
            row.setProperty("role", "review-row")
            row.setProperty("selected", link.selected)
            row.setMinimumWidth(0)
            row_grid = QGridLayout(row)
            row_grid.setContentsMargins(8, 10, 8, 10)
            row_grid.setHorizontalSpacing(14)
            check = QCheckBox()
            check.setAccessibleName(f"Select {link.title}")
            check.setChecked(link.selected)
            check.toggled.connect(lambda checked, draft=link, widget=row: self._select(draft, widget, checked))
            self._checks[link.id] = check
            row_grid.addWidget(check, 0, 0, 2, 1)
            row_grid.addWidget(label(link.title, "link-title"), 0, 1)
            row_grid.addWidget(label(link.page, "hint"), 1, 1)
            edit = button("Edit" if link.ready else "Choose file",
                          lambda _checked=False, ident=link.id: self._edit_link(ident))
            edit.setAccessibleName(f"Edit {link.title}")
            if compact:
                row_grid.addWidget(label(link.description), 2, 1)
                row_grid.addWidget(edit, 0, 2, 2, 1)
                row_grid.setColumnStretch(1, 1)
            else:
                row_grid.addWidget(label(link.description), 0, 2, 2, 1)
                row_grid.addWidget(edit, 0, 3, 2, 1)
                row_grid.setColumnStretch(1, 5)
                row_grid.setColumnStretch(2, 4)
                row_grid.setColumnMinimumWidth(3, 110)
            row_grid.setColumnMinimumWidth(0, 32)
            self._list_layout.addWidget(row)
        self._update_selection()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if (event.oldSize().width() < 780) != (event.size().width() < 780) and hasattr(self, "_list_layout"):
            self._render_links()

    def _select(self, link, row, selected):
        link.selected = selected
        row.setProperty("selected", selected)
        row.style().unpolish(row)
        row.style().polish(row)
        self._update_selection()

    def _select_visible(self, selected):
        for link in self.links:
            if link.id in self._checks:
                self._checks[link.id].setChecked(selected)

    def _update_selection(self):
        selected = [link for link in self.links if link.selected]
        hidden = sum(link.id not in self._checks for link in selected)
        text = f"{len(selected)} change{'s' if len(selected) != 1 else ''} selected"
        if hidden:
            text += f" · {hidden} hidden"
        self._count.setText(text)
        valid = bool(selected) and all(link.ready and link.changed for link in selected)
        self._review_btn.setEnabled(valid)
        self._review_btn.setToolTip("Choose a replacement or opening change for each selected link." if not valid else "")
        count = sum(link.broken for link in self.links)
        self._summary.setText(f"{count} link{'s' if count != 1 else ''} {'needs' if count == 1 else 'need'} attention")
        if hasattr(self, "_select_all"):
            self._select_all.setChecked(bool(self._checks) and all(check.isChecked() for check in self._checks.values()))

    def _toggle_working(self):
        self._show_working = not self._show_working
        self._working_btn.setText("Hide working links" if self._show_working else "Show working links")
        self._render_links()

    def _edit_link(self, ident):
        draft = next(link for link in self.links if link.id == ident)
        dialog = LinkEditorDialog(draft, self)
        self._active_dialog = dialog
        def finished(result):
            if result == QDialog.DialogCode.Accepted:
                index = next(i for i, link in enumerate(self.links) if link.id == ident)
                self.links[index] = dialog.draft
                self._render_links()
            self._active_dialog = None
            dialog.deleteLater()
        dialog.finished.connect(finished)
        dialog.open()

    def _add_action(self):
        menu = QMenu(self)
        menu.addAction("Repair a specific link…", self._manual_link)
        menu.addAction("Change how working links open", self._show_working_links)
        menu.addSeparator()
        menu.addAction("Reset sample links", self.reset_preview)
        menu.popup(self._add_btn.mapToGlobal(self._add_btn.rect().bottomLeft()))
        self._menu = menu

    def _show_working_links(self):
        if not self._show_working:
            self._toggle_working()

    def _manual_link(self):
        dialog = QDialog(self)
        dialog.setProperty("design", "modern")
        dialog.setWindowTitle("Add a specific link")
        dialog.resize(500, 340)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(24, 24, 24, 24)
        fields = []
        for title, placeholder in (("Link title", "Reading.pdf"), ("Page", "Lesson 1 resources"),
                                   ("Current link URL", "https://…")):
            layout.addWidget(label(title, "field-label"))
            entry = QLineEdit()
            entry.setPlaceholderText(placeholder)
            layout.addWidget(entry)
            fields.append(entry)
        error = label("", "error")
        layout.addWidget(error)
        actions = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        actions.rejected.connect(dialog.reject)
        def add():
            title, page, url = [entry.text().strip() for entry in fields]
            if not title or not page or not valid_web_url(url):
                error.setText("Enter a title, page name and complete http or https link URL.")
                return
            ident = uuid4().hex
            self.links.append(LinkDraft(ident, title, page, url))
            self._render_links()
            dialog.accept()
            self._edit_link(ident)
        actions.accepted.connect(add)
        layout.addWidget(actions)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.open()
        self._manual_dialog = dialog

    def _change_location(self):
        dialog = QDialog(self)
        dialog.setProperty("design", "modern")
        dialog.setWindowTitle("Choose location")
        dialog.resize(560, 390)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(12)
        layout.addWidget(label("Preview context only; these URLs will not be scanned.", "hint"))
        scope = NoScrollComboBox()
        scope.addItems(["Course", "Section", "Page"])
        scope.setCurrentText(self._scope)
        layout.addWidget(scope)
        fields = []
        for title, value in (("Display name", self._location),
                             ("Brightspace course, section or page URL", self._target_url),
                             ("Moodle source URL (optional)", self._source_url)):
            layout.addWidget(label(title, "field-label"))
            entry = QLineEdit(value)
            layout.addWidget(entry)
            fields.append(entry)
        error = label("", "error")
        layout.addWidget(error)
        actions = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        actions.rejected.connect(dialog.reject)
        def save():
            name, target, source = [entry.text().strip() for entry in fields]
            if not name or any(url and not valid_web_url(url) for url in (target, source)):
                error.setText("Enter a display name and complete http or https URLs.")
                return
            self._scope, self._location = scope.currentText(), name
            self._target_url, self._source_url = target, source
            self._context.setText(f"{self._scope}: {name}")
            dialog.accept()
        actions.accepted.connect(save)
        layout.addWidget(actions)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.open()
        self._location_dialog = dialog

    def _next(self):
        if self._pages.currentIndex() == 1:
            self._apply_preview()
            return
        try:
            changes = selected_changes(self.links)
        except ValueError:
            return
        if not changes:
            return
        self._clear(self._review_layout)
        self._review_layout.addWidget(label("These changes apply only to the sample data.", "hint"))
        for draft in changes:
            frame = QFrame()
            frame.setProperty("role", "review-row")
            layout = QVBoxLayout(frame)
            layout.setContentsMargins(16, 16, 16, 16)
            layout.addWidget(label(f"{draft.title} · {draft.page}", "link-title"))
            layout.addWidget(label(f"From: {draft.current_url}"))
            if draft.replacement:
                layout.addWidget(label(f"To: {draft.replacement}"))
            layout.addWidget(label(f"Opening: {OPEN_LABELS[draft.opening]}"))
            self._review_layout.addWidget(frame)
        self._review_layout.addStretch()
        self._pages.setCurrentIndex(1)
        self._heading.setText("Review changes")
        self._steps.setText("Find   →   Choose   →   Apply")
        self._summary.setText("Confirm your selected changes")
        self._review_btn.setText("Apply to preview")
        self._add_btn.hide()
        self._location_btn.hide()
        self._source_btn.hide()

    def _back(self):
        if self._pages.currentIndex() == 0:
            self._change_location()
            return
        self._pages.setCurrentIndex(0)
        self._heading.setText("Review links")
        self._review_btn.setText("Review changes")
        self._add_btn.show()
        self._location_btn.show()
        self._source_btn.show()
        self._render_links()

    def _apply_preview(self):
        changes = selected_changes(self.links)
        for draft in changes:
            if draft.replacement:
                draft.current_url = draft.replacement
                draft.replacement = ""
                draft.broken = False
            if draft.opening != "keep":
                draft.current_opening = draft.opening
            draft.opening = "keep"
            draft.selected = False
        self._back()
        self._show_working_links()
        self._message.setText(f"Preview complete: {len(changes)} sample link(s) updated. No course was changed.")
        self._message.show()

    def reset_preview(self):
        if self._pages.currentIndex() == 1:
            self._back()
        self.links = sample_links()
        self._show_working = False
        self._working_btn.setText("Show working links")
        self._pages.setCurrentIndex(0)
        self._heading.setText("Review links")
        self._review_btn.setText("Review changes")
        self._message.hide()
        self._render_links()
