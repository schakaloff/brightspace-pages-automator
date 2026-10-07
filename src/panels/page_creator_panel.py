"""Title-list-first authoring of a batch of Brightspace pages."""

import asyncio
import queue
import re
import threading
import webbrowser

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QIcon, QPixmap, QTransform
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QDialog, QDialogButtonBox, QFrame, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QPlainTextEdit, QProgressBar, QPushButton,
    QScrollArea, QSizePolicy, QSplitter, QStackedWidget,
    QTableWidget, QTableWidgetItem, QTextBrowser, QToolButton, QVBoxLayout, QWidget,
)

from bulk_page_creator import (
    PageDraft, Section, course_id_from_url, create_course_pages, load_sections,
    render_content, validate_drafts,
)
from gui_log import LogWidget
import gui_styles
from panels._shared import NoScrollComboBox, friendly_error


class PageCreatorPanel(QWidget):
    def __init__(self, main_window, parent=None):
        super().__init__(parent)
        self._mw = main_window
        self._queue = queue.Queue()
        self._stop_event = threading.Event()
        self._rows = []
        self._busy = False
        self._creating = False
        self._editing = False
        self._loaded_course_id = ""
        self._build()
        self._restore()
        self.refresh_theme()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._poll)
        self._timer.start(100)

    def _build(self):
        self.setProperty("design", "modern")
        self.setObjectName("page_creator")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        root.addWidget(self._scroll)
        body = QWidget()
        self._scroll.setWidget(body)
        layout = QVBoxLayout(body)
        layout.setContentsMargins(24, 24, 24, 20)
        layout.setSpacing(16)

        def label(text, role="dim"):
            widget = QLabel(text)
            widget.setProperty("role", role)
            widget.setWordWrap(True)
            return widget

        def surface():
            frame = QFrame()
            frame.setProperty("role", "surface")
            inner = QVBoxLayout(frame)
            inner.setContentsMargins(16, 14, 16, 14)
            inner.setSpacing(10)
            return frame, inner

        heading = QVBoxLayout()
        heading.setSpacing(4)
        heading.addWidget(label("Create pages in bulk", "page-heading"))
        heading.addWidget(label("Turn a list of titles into a section of ready-to-edit pages."))
        layout.addLayout(heading)

        destination, destination_layout = surface()
        destination_layout.addWidget(label("Destination", "section-heading"))

        url_row = QHBoxLayout()
        self._url = QLineEdit()
        self._url.setObjectName("page_creator_course_url")
        self._url.setPlaceholderText("Brightspace course or section URL")
        self._url.setAccessibleName("Brightspace course or section URL")
        self._url.textChanged.connect(self._invalidate_sections)
        self._load = QPushButton("Load sections")
        self._load.setProperty("variant", "secondary")
        self._load.clicked.connect(self._load_clicked)
        url_row.addWidget(self._url, 1)
        url_row.addWidget(self._load)
        destination_layout.addLayout(url_row)
        self._section = NoScrollComboBox()
        self._section.setObjectName("page_creator_section")
        self._section.setPlaceholderText("Select the destination section")
        self._section.setAccessibleName("Destination section")
        self._section.currentIndexChanged.connect(self._update_actions)
        section_row = QHBoxLayout()
        section_row.addWidget(self._section, 1)
        self._open_section = QPushButton("Open section")
        self._open_section.setProperty("variant", "ghost")
        self._open_section.clicked.connect(self._open_section_clicked)
        section_row.addWidget(self._open_section)
        destination_layout.addLayout(section_row)
        layout.addWidget(destination)

        self._splitter = QSplitter(Qt.Orientation.Horizontal)
        self._splitter.setChildrenCollapsible(False)
        self._splitter.setHandleWidth(12)
        self._splitter.setMinimumHeight(340)
        self._splitter.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Ignored)
        page_list, list_layout = surface()
        page_list.setMinimumWidth(210)
        list_heading = QHBoxLayout()
        list_heading.addWidget(label("Your pages", "section-heading"), 1)
        self._page_total = label("0 pages", "hint")
        list_heading.addWidget(self._page_total)
        list_layout.addLayout(list_heading)

        tools = QHBoxLayout()
        self._paste = QPushButton("Paste titles…")
        self._paste.clicked.connect(self._paste_titles)
        self._add = QPushButton("Add page")
        self._add.clicked.connect(lambda: self._append_titles([""]))
        tools.addWidget(self._paste)
        tools.addWidget(self._add)
        tools.addStretch()
        self._paste.setProperty("variant", "secondary")
        self._add.setProperty("variant", "ghost")
        list_layout.addLayout(tools)

        self._remove = QPushButton("Remove")
        self._remove.setToolTip("Remove this row from the draft. Created Brightspace pages stay in the course.")
        self._remove.clicked.connect(self._remove_row)
        self._clear_all = QPushButton("Clear all")
        self._clear_all.setObjectName("page_creator_clear_all")
        self._clear_all.setToolTip("Clear every row from this list. Pages already created in Brightspace stay in your course.")
        self._clear_all.clicked.connect(self._clear_all_rows)
        self._up = QPushButton()
        self._up.setAccessibleName("Move page up")
        self._up.setToolTip("Move page up")
        self._up.clicked.connect(lambda: self._move_row(-1))
        self._down = QPushButton()
        self._down.setAccessibleName("Move page down")
        self._down.setToolTip("Move page down")
        self._down.clicked.connect(lambda: self._move_row(1))
        row_tools = QHBoxLayout()
        row_tools.setSpacing(4)
        row_tools.addWidget(self._remove)
        row_tools.addWidget(self._clear_all)
        row_tools.addStretch()
        for button in (self._remove, self._clear_all, self._up, self._down):
            button.setProperty("variant", "ghost")
        for button in (self._up, self._down):
            button.setFixedWidth(34)
            row_tools.addWidget(button)

        self._list_stack = QStackedWidget()
        empty = QWidget()
        empty_layout = QVBoxLayout(empty)
        empty_layout.setContentsMargins(8, 20, 8, 20)
        empty_layout.addStretch()
        empty_layout.addWidget(label("Your pages start here", "section-heading"))
        empty_layout.addWidget(label("Paste one title per line, or add a page to start writing."))
        empty_layout.addStretch()
        self._list_stack.addWidget(empty)
        self._table = QTableWidget(0, 2)
        self._table.setObjectName("page_creator_pages")
        self._table.setHorizontalHeaderLabels(["Page title", "Status"])
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._table.setColumnWidth(1, 105)
        self._table.setShowGrid(False)
        self._table.setCornerButtonEnabled(False)
        self._table.verticalHeader().setDefaultSectionSize(44)
        self._table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        self._table.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self._table.horizontalHeader().setFixedHeight(38)
        self._table.currentCellChanged.connect(self._selection_changed)
        self._table.itemChanged.connect(self._title_changed)
        self._list_stack.addWidget(self._table)
        list_layout.addWidget(self._list_stack, 1)
        list_layout.addLayout(row_tools)
        list_layout.addWidget(label("Drag the divider to resize. Use the arrows to reorder.", "hint"))
        self._splitter.addWidget(page_list)

        editor_widget, editor_layout = surface()
        editor_widget.setMinimumWidth(230)
        editor_heading = QHBoxLayout()
        self._editor_heading = label("Page editor", "section-heading")
        editor_heading.addWidget(self._editor_heading, 1)
        self._preview = QPushButton("Preview")
        self._preview.setProperty("variant", "ghost")
        self._preview.clicked.connect(self._preview_clicked)
        editor_heading.addWidget(self._preview)
        editor_layout.addLayout(editor_heading)
        editor_layout.addWidget(label("Page title", "field-label"))
        self._title = QLineEdit()
        self._title.setObjectName("page_creator_title")
        self._title.setPlaceholderText("Select or add a page")
        self._title.setAccessibleName("Selected page title")
        self._title.textChanged.connect(self._editor_title_changed)
        editor_layout.addWidget(self._title)
        format_row = QHBoxLayout()
        format_row.addWidget(label("Content", "field-label"), 1)
        self._format = NoScrollComboBox()
        self._format.addItem("Plain text", "text")
        self._format.addItem("HTML", "html")
        self._format.setAccessibleName("Content format")
        self._format.currentIndexChanged.connect(self._content_changed)
        format_row.addWidget(self._format)
        editor_layout.addLayout(format_row)
        self._content = QPlainTextEdit()
        self._content.setObjectName("page_creator_content")
        self._content.setAccessibleName("Selected page content")
        self._content.setPlaceholderText("Optional: paste or write this page's content. Leave it empty to create a blank page.")
        self._content.textChanged.connect(self._content_changed)
        editor_layout.addWidget(self._content, 1)
        editor_layout.addWidget(label("Content is optional. Leave it empty for a blank page.", "hint"))
        self._splitter.addWidget(editor_widget)
        self._splitter.setStretchFactor(0, 2)
        self._splitter.setStretchFactor(1, 3)
        self._splitter.setSizes([340, 480])
        layout.addWidget(self._splitter, 1)

        footer, footer_layout = surface()
        footer_layout.setSpacing(4)
        actions = QHBoxLayout()
        options = QVBoxLayout()
        options.setSpacing(6)
        self._hidden = QCheckBox("Hide new pages from students")
        self._hidden.setChecked(True)
        options.addWidget(self._hidden)
        self._count = QLabel()
        self._count.setProperty("role", "dim")
        options.addWidget(self._count)
        self._create = QPushButton("Create pages")
        self._create.setObjectName("page_creator_create_btn")
        self._create.clicked.connect(self._create_clicked)
        self._stop = QPushButton("Stop")
        self._stop.setProperty("variant", "secondary")
        self._stop.setToolTip("Finish the current request, then stop before the next page.")
        self._stop.clicked.connect(self._stop_clicked)
        actions.addLayout(options, 1)
        actions.addWidget(self._stop)
        actions.addWidget(self._create)
        footer_layout.addLayout(actions)
        self._progress = QProgressBar()
        self._progress.setTextVisible(False)
        self._progress.hide()
        footer_layout.addWidget(self._progress)
        layout.addWidget(footer)

        activity_row = QHBoxLayout()
        self._activity_toggle = QToolButton()
        self._activity_toggle.setText("Activity")
        self._activity_toggle.setProperty("variant", "ghost")
        self._activity_toggle.setCheckable(True)
        self._activity_toggle.setArrowType(Qt.ArrowType.RightArrow)
        self._activity_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self._activity_toggle.toggled.connect(self._toggle_activity)
        activity_row.addWidget(self._activity_toggle)
        self._activity_summary = label("Drafts are saved locally when you close the app.", "hint")
        self._activity_summary.setObjectName("page_creator_activity_summary")
        self._activity_summary.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        activity_row.addWidget(self._activity_summary, 1)
        layout.addLayout(activity_row)
        self._log = LogWidget()
        self._log.setFixedHeight(130)
        self._log.hide()
        layout.addWidget(self._log)
        self._selection_changed(-1, 0, -1, 0)

    def _toggle_activity(self, expanded):
        self._log.setVisible(expanded)
        self._activity_toggle.setArrowType(Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)

    def refresh_theme(self):
        theme = "light" if gui_styles.is_light() else "dark"
        down = QPixmap(gui_styles.modern_asset(f"arrow-down-{theme}.png"))
        self._down.setIcon(QIcon(down))
        self._up.setIcon(QIcon(down.transformed(QTransform().rotate(180))))
        c = gui_styles.modern_colors()
        self._log.set_log_colors({"info": c["text"], "success": c["success"],
                                  "error": c["error"], "warning": c["warning"],
                                  "dim": c["muted"], "detail": c["muted"]})

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # At narrow window sizes each card gets the full content width. The
        # outer scroll area keeps every field and action reachable vertically.
        stacked = self.width() < 740
        orientation = Qt.Orientation.Vertical if stacked else Qt.Orientation.Horizontal
        if self._splitter.orientation() != orientation:
            self._splitter.setOrientation(orientation)
            self._splitter.setMinimumHeight(720 if stacked else 340)
            self._splitter.setSizes([320, 400] if stacked else [340, 480])

    def _append_log(self, message, level="info"):
        self._log.append_log(message, level)
        if level in ("detail", "dim"):
            return
        self._activity_summary.setText(message.split(" — https://", 1)[0])
        self._activity_summary.setProperty("state", level)
        self._activity_summary.style().unpolish(self._activity_summary)
        self._activity_summary.style().polish(self._activity_summary)
        if level in ("error", "warning"):
            self._activity_toggle.setChecked(True)
            QTimer.singleShot(0, lambda: self._scroll.ensureWidgetVisible(self._log))

    def _restore(self):
        cfg = self._mw.load_config() if hasattr(self._mw, "load_config") else {}
        self._url.setText(cfg.get("page_creator_url") or cfg.get("chk_bs_url") or "")
        saved = cfg.get("page_creator_draft", {})
        self._hidden.setChecked(saved.get("hidden", True))
        for row in saved.get("rows", []):
            if not isinstance(row, dict) or not isinstance(row.get("title"), str):
                continue
            state = row.get("state", "pending")
            if state not in ("pending", "created", "review"):
                state = "review"
            if saved.get("active") and state == "pending":
                state = "review"
            self._rows.append({"title": row["title"], "content": str(row.get("content", "")),
                               "format": row.get("format", "text"), "state": state,
                               "url": str(row.get("url", ""))})
        self._refresh_table()
        if any(row["state"] == "review" for row in self._rows):
            self._append_log("A previous run needs review. Check those pages in Brightspace before removing their draft rows or starting another batch.", "warning")

    def save_state(self):
        if hasattr(self._mw, "save_config"):
            self._mw.save_config({"page_creator_url": self._url.text().strip(),
                "page_creator_draft": {"rows": [dict(row) for row in self._rows],
                                       "hidden": self._hidden.isChecked(),
                                       "active": self._creating}})

    def _credentials(self):
        return {key: getattr(self._mw, key, "") for key in
                ("bs_username", "bs_password", "sso_email", "sso_password")}

    def _invalidate_sections(self):
        self._loaded_course_id = ""
        self._section.clear()
        self._update_actions()

    def _append_titles(self, titles):
        if self._busy:
            return
        first = len(self._rows)
        self._rows.extend({"title": title.strip(), "content": "", "format": "text",
                           "state": "pending", "url": ""} for title in titles)
        self._refresh_table(first)

    def _paste_titles(self):
        dialog = QDialog(self)
        dialog.setProperty("design", "modern")
        dialog.setWindowTitle("Paste page titles")
        dialog.resize(500, 420)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(14)
        layout.addWidget(QLabel("One title per line. Blank lines are ignored."))
        field = QPlainTextEdit()
        field.setPlaceholderText("Introduction\nLearning objectives\nLesson 1\nPractice activity")
        layout.addWidget(field)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Add pages")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setProperty("variant", "secondary")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._append_titles([line.strip() for line in field.toPlainText().splitlines() if line.strip()])

    def _refresh_table(self, selected=None):
        if selected is None:
            selected = self._table.currentRow()
        self._table.blockSignals(True)
        self._table.setRowCount(len(self._rows))
        for index, row in enumerate(self._rows):
            title = QTableWidgetItem(row["title"])
            title.setToolTip(row["title"])
            if row["state"] != "pending":
                title.setFlags(title.flags() & ~Qt.ItemFlag.ItemIsEditable)
            status = QTableWidgetItem()
            status.setData(Qt.ItemDataRole.AccessibleTextRole,
                           {"pending": "Draft", "created": "Created", "review": "Check course"}[row["state"]])
            status.setFlags(status.flags() & ~Qt.ItemFlag.ItemIsEditable)
            status.setToolTip(row["url"] or ("Check Brightspace before retrying. Remove this row after reviewing the outcome." if row["state"] == "review" else ""))
            self._table.setItem(index, 0, title)
            self._table.setItem(index, 1, status)
            holder = QWidget()
            holder.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            badge_layout = QHBoxLayout(holder)
            badge_layout.setContentsMargins(6, 0, 6, 0)
            badge = QLabel({"pending": "Draft", "created": "Created", "review": "Review"}[row["state"]])
            badge.setProperty("role", "status")
            badge.setProperty("state", row["state"])
            badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            badge_layout.addWidget(badge, 0, Qt.AlignmentFlag.AlignVCenter)
            self._table.setCellWidget(index, 1, holder)
        self._table.blockSignals(False)
        self._list_stack.setCurrentIndex(1 if self._rows else 0)
        self._page_total.setText(f"{len(self._rows)} page{'s' if len(self._rows) != 1 else ''}")
        if self._rows:
            selected = max(0, min(selected, len(self._rows) - 1))
            self._table.setCurrentCell(selected, 0)
        else:
            selected = -1
        self._selection_changed(selected, 0, -1, 0)

    def _selection_changed(self, row, _column, _previous_row, _previous_column):
        self._editing = True
        data = self._rows[row] if 0 <= row < len(self._rows) else None
        self._title.setText(data["title"] if data else "")
        self._editor_heading.setText(f"Page {row + 1} of {len(self._rows)}" if data else "Page editor")
        self._content.setPlainText(data["content"] if data else "")
        self._format.setCurrentIndex(self._format.findData(data["format"]) if data else 0)
        self._editing = False
        self._update_actions()

    def _content_changed(self):
        row = self._table.currentRow()
        if self._editing or self._busy or row < 0 or self._rows[row]["state"] != "pending":
            return
        self._rows[row]["content"] = self._content.toPlainText()
        self._rows[row]["format"] = self._format.currentData()

    def _title_changed(self, item):
        if item.column() == 0 and not self._busy and self._rows[item.row()]["state"] == "pending":
            self._rows[item.row()]["title"] = item.text()
            if item.row() == self._table.currentRow():
                self._title.blockSignals(True)
                self._title.setText(item.text())
                self._title.blockSignals(False)
            self._update_actions()

    def _editor_title_changed(self, text):
        row = self._table.currentRow()
        if self._editing or self._busy or row < 0 or self._rows[row]["state"] != "pending":
            return
        self._rows[row]["title"] = text
        self._table.blockSignals(True)
        self._table.item(row, 0).setText(text)
        self._table.item(row, 0).setToolTip(text)
        self._table.blockSignals(False)
        self._update_actions()

    def _remove_row(self):
        row = self._table.currentRow()
        if not self._busy and row >= 0:
            self._rows.pop(row)
            self._refresh_table(row)
            self.save_state()

    def _clear_all_rows(self):
        if self._busy or not self._rows:
            return
        count = len(self._rows)
        self._rows.clear()
        self._refresh_table()
        self.save_state()
        self._append_log(f"Cleared {count} page(s) from the list. Brightspace pages stay in the course.")

    def _move_row(self, offset):
        row = self._table.currentRow()
        other = row + offset
        if not self._busy and row >= 0 and 0 <= other < len(self._rows):
            self._rows[row], self._rows[other] = self._rows[other], self._rows[row]
            self._refresh_table(other)

    def _preview_clicked(self):
        row = self._table.currentRow()
        if row < 0:
            return
        data = self._rows[row]
        dialog = QDialog(self)
        dialog.setProperty("design", "modern")
        dialog.setWindowTitle(data["title"] or "Page preview")
        dialog.resize(650, 480)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(14)
        preview = QTextBrowser()
        preview.setOpenLinks(False)
        preview.setHtml(render_content(PageDraft(data["title"], data["content"], data["format"])))
        layout.addWidget(preview)
        note = QLabel("Content preview. Brightspace may render HTML styles differently.")
        note.setProperty("role", "dim")
        layout.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.exec()

    def _open_section_clicked(self):
        section = self._section.currentData()
        if isinstance(section, Section) and self._loaded_course_id:
            webbrowser.open(f"https://learn.okanagancollege.ca/d2l/le/lessons/{self._loaded_course_id}/units/{section.id}")

    def _update_actions(self, *_args):
        if not hasattr(self, "_create"):
            return
        ready = sum(row["state"] == "pending" for row in self._rows)
        created = sum(row["state"] == "created" for row in self._rows)
        review = any(row["state"] == "review" for row in self._rows)
        self._count.setText(f"{ready} ready · {created} created" + (" · Check course" if review else ""))
        self._create.setText(f"Create {ready} page{'s' if ready != 1 else ''}")
        self._create.setEnabled(not self._busy and bool(self._loaded_course_id) and
                                self._section.currentData() is not None and ready > 0 and not review)
        self._open_section.setEnabled(bool(self._loaded_course_id) and self._section.currentData() is not None)
        row = self._table.currentRow()
        editable = 0 <= row < len(self._rows) and self._rows[row]["state"] == "pending" and not self._busy
        self._content.setReadOnly(not editable)
        self._title.setReadOnly(not editable)
        self._format.setEnabled(editable)
        self._preview.setEnabled(row >= 0 and not self._busy)
        self._remove.setEnabled(row >= 0 and not self._busy)
        self._clear_all.setEnabled(bool(self._rows) and not self._busy)
        self._up.setEnabled(row > 0 and not self._busy)
        self._down.setEnabled(0 <= row < len(self._rows) - 1 and not self._busy)
        self._stop.setEnabled(self._creating and not self._stop_event.is_set())
        self._stop.setVisible(self._creating)

    def _set_busy(self, busy, creating=False):
        self._busy, self._creating = busy, creating
        self._progress.setVisible(creating)
        if creating:
            self._progress.setMaximum(sum(row["state"] == "pending" for row in self._rows))
            self._progress.setValue(0)
        for control in (self._url, self._load, self._section, self._paste,
                        self._add, self._table, self._hidden):
            control.setEnabled(not busy)
        self._update_actions()

    def _run_worker(self, action, kind):
        def worker():
            try:
                self._queue.put((kind, asyncio.run(action())))
            except Exception as exc:
                self._queue.put(("error", exc))
        threading.Thread(target=worker, daemon=True).start()

    def _worker_log(self, message, level="info"):
        self._queue.put(("log", (message, level)))

    def _load_clicked(self):
        if self._busy:
            return
        if not self._mw.chromium_ready:
            self._append_log("Browser engine is still installing. Please wait.", "warning")
            return
        url = self._url.text().strip()
        try:
            course_id = course_id_from_url(url)
        except ValueError as exc:
            self._append_log(str(exc), "warning")
            return
        self._invalidate_sections()
        self._set_busy(True)
        credentials = self._credentials()
        async def action():
            return course_id, await load_sections(url, credentials, self._worker_log)
        self._run_worker(action, "loaded")

    def _create_clicked(self):
        if self._busy or any(row["state"] == "review" for row in self._rows):
            return
        url = self._url.text().strip()
        section = self._section.currentData()
        indices = [index for index, row in enumerate(self._rows) if row["state"] == "pending"]
        drafts = [PageDraft(self._rows[index]["title"], self._rows[index]["content"],
                            self._rows[index]["format"]) for index in indices]
        try:
            if course_id_from_url(url) != self._loaded_course_id or not isinstance(section, Section):
                raise ValueError("Load sections and choose a destination first.")
            validate_drafts(drafts)
        except ValueError as exc:
            self._append_log(str(exc), "warning")
            return
        if not self._mw.chromium_ready:
            self._append_log("Browser engine is still installing. Please wait.", "warning")
            return
        self._stop_event.clear()
        self._set_busy(True, creating=True)
        self.save_state()
        self._append_log(f"Creating {len(drafts)} pages in {section.path}…", "info")
        credentials, hidden = self._credentials(), self._hidden.isChecked()
        async def action():
            result = await create_course_pages(url, section, drafts, credentials, hidden,
                self._stop_event, lambda index, item: self._queue.put(("progress", (indices[index], item))),
                self._worker_log)
            return indices, result
        self._run_worker(action, "done")

    def _stop_clicked(self):
        self._stop_event.set()
        self._append_log("Stopping after the current request. Waiting pages will stay in the draft.", "info")
        self._update_actions()

    def _mark_created(self, index, item):
        self._rows[index]["state"] = "created"
        self._rows[index]["url"] = item.url

    def _poll(self):
        try:
            while True:
                kind, payload = self._queue.get_nowait()
                if kind == "log":
                    self._append_log(*payload)
                elif kind == "loaded":
                    self._loaded_course_id, sections = payload
                    for section in sections:
                        self._section.addItem(section.path, section)
                    # A pasted section URL should select that section automatically.
                    match = re.search(r"/units/(\d+)(?:[/?#]|$)", self._url.text())
                    if match:
                        for index, section in enumerate(sections):
                            if section.id == match.group(1):
                                self._section.setCurrentIndex(index)
                                break
                    elif sections:
                        self._section.setCurrentIndex(-1)
                    self._set_busy(False)
                    self._append_log(f"Loaded {len(sections)} sections. Choose where to put the pages.", "success")
                elif kind == "progress":
                    index, item = payload
                    self._mark_created(index, item)
                    self._progress.setValue(self._progress.value() + 1)
                    self._refresh_table()
                    self.save_state()
                    self._append_log(f"Created: {item.draft.title} — {item.url}", "success")
                elif kind == "done":
                    indices, result = payload
                    for index, item in zip(indices, result.created):
                        self._mark_created(index, item)
                    if result.failed_index is not None:
                        self._rows[indices[result.failed_index]]["state"] = "review"
                        self._append_log(f"Stopped: {result.error} Check that page in Brightspace before retrying; then remove its draft row. Later rows have not been sent.", "error")
                    else:
                        self._append_log(f"{'Stopped' if result.stopped else 'Done'}: {len(result.created)} page(s) created.", "success")
                    self._set_busy(False)
                    self._refresh_table()
                    self.save_state()
                elif kind == "error":
                    message, detail = friendly_error(payload)
                    self._append_log(message, "error")
                    if detail != message:
                        self._append_log(detail, "detail")
                    # Preflight failures leave the batch editable. Creation errors
                    # are returned as BatchResult, with the attempted row identified.
                    self._set_busy(False)
                    self.save_state()
        except queue.Empty:
            pass
