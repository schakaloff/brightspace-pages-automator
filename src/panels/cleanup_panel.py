"""Review and remove selected Brightspace Content topics."""

import asyncio
import queue
import threading

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from content_cleanup import (
    ContentTopic, course_id_from_url, load_course_topics, remove_course_topics,
)
from gui_log import LogWidget
from panels._shared import _form_label, _section_header, friendly_error


class CleanupPanel(QWidget):
    def __init__(self, main_window, parent=None):
        super().__init__(parent)
        self._mw = main_window
        self._queue: queue.Queue = queue.Queue()
        self._topics: list[ContentTopic] = []
        self._loaded_course_id = ""
        self._busy = False
        self._build()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._poll)
        self._timer.start(100)

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 28, 32, 20)
        layout.setSpacing(8)

        layout.addWidget(_section_header("Content Cleanup"))
        description = QLabel(
            "Select one or more topics to remove from the course Content outline. "
            "The linked files and activities stay in the course. Units cannot be selected."
        )
        description.setProperty("role", "dim")
        description.setWordWrap(True)
        layout.addWidget(description)
        layout.addSpacing(10)

        layout.addWidget(_form_label("BRIGHTSPACE COURSE URL"))
        url_row = QHBoxLayout()
        self._url = QLineEdit()
        self._url.setObjectName("cleanup_course_url")
        self._url.setPlaceholderText(
            "https://learn.okanagancollege.ca/d2l/le/content/<course id>/home"
        )
        self._url.textChanged.connect(self._invalidate_outline)
        self._load = QPushButton("Load Content")
        self._load.setObjectName("cleanup_load_btn")
        self._load.clicked.connect(self._load_clicked)
        url_row.addWidget(self._url, 1)
        url_row.addWidget(self._load)
        layout.addLayout(url_row)

        filter_row = QHBoxLayout()
        self._filter = QLineEdit()
        self._filter.setObjectName("cleanup_filter")
        self._filter.setPlaceholderText("Find a topic or unit…")
        self._filter.textChanged.connect(self._apply_filter)
        self._select_visible = QPushButton("Select visible")
        self._select_visible.clicked.connect(self._select_visible_rows)
        self._clear = QPushButton("Clear selection")
        self._clear.clicked.connect(self._clear_selection)
        filter_row.addWidget(self._filter, 1)
        filter_row.addWidget(self._select_visible)
        filter_row.addWidget(self._clear)
        layout.addLayout(filter_row)

        self._table = QTableWidget(0, 3)
        self._table.setObjectName("cleanup_topics")
        self._table.setHorizontalHeaderLabels(["Select", "Content topic", "Unit"])
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().hide()
        header = self._table.horizontalHeader()
        header.setStretchLastSection(True)
        self._table.setColumnWidth(0, 65)
        self._table.setColumnWidth(1, 280)
        self._table.cellClicked.connect(self._toggle_row)
        layout.addWidget(self._table, 1)

        action_row = QHBoxLayout()
        self._count = QLabel("Load a course to see its topics.")
        self._count.setProperty("role", "dim")
        self._remove = QPushButton("Remove selected from Content")
        self._remove.setObjectName("cleanup_remove_btn")
        self._remove.setProperty("variant", "danger")
        self._remove.clicked.connect(self._remove_clicked)
        self._remove.setEnabled(False)
        action_row.addWidget(self._count, 1)
        action_row.addWidget(self._remove)
        layout.addLayout(action_row)

        self._log = LogWidget()
        self._log.setMaximumHeight(115)
        layout.addWidget(self._log)

        cfg = self._mw.load_config() if hasattr(self._mw, "load_config") else {}
        self._url.setText(cfg.get("cleanup_bs_url") or cfg.get("chk_bs_url") or "")

    def save_state(self):
        if hasattr(self._mw, "save_config"):
            self._mw.save_config({"cleanup_bs_url": self._url.text().strip()})

    def _credentials(self):
        return {
            "bs_username": self._mw.bs_username,
            "bs_password": self._mw.bs_password,
            "sso_email": self._mw.sso_email,
            "sso_password": self._mw.sso_password,
        }

    def _invalidate_outline(self):
        if self._busy:
            return
        self._topics = []
        self._loaded_course_id = ""
        self._table.setRowCount(0)
        self._update_selection()

    def _set_busy(self, busy):
        self._busy = busy
        self._url.setEnabled(not busy)
        self._load.setEnabled(not busy)
        self._table.setEnabled(not busy)
        self._filter.setEnabled(not busy)
        self._select_visible.setEnabled(not busy)
        self._clear.setEnabled(not busy)
        self._update_selection()

    def _load_clicked(self):
        if self._busy:
            return
        if not self._mw.chromium_ready:
            self._log.append_log("Browser engine is still installing. Please wait.", "warning")
            return
        url = self._url.text().strip()
        try:
            course_id = course_id_from_url(url)
        except ValueError as exc:
            self._log.append_log(str(exc), "warning")
            return
        self._invalidate_outline()
        self.save_state()
        self._set_busy(True)
        self._log.append_log("Opening Brightspace and loading the Content outline…", "info")
        credentials = self._credentials()

        def worker():
            try:
                topics = asyncio.run(load_course_topics(url, credentials))
                self._queue.put(("loaded", (course_id, topics)))
            except Exception as exc:
                self._queue.put(("error", exc))

        threading.Thread(target=worker, daemon=True).start()

    def _display_topics(self, topics):
        self._topics = topics
        self._table.setRowCount(len(topics))
        for row, topic in enumerate(topics):
            check = QCheckBox()
            check.setObjectName(f"cleanup_check_{topic.id}")
            check.setAccessibleName(f"Select {topic.title} in {topic.unit_path}")
            check.stateChanged.connect(self._update_selection)
            self._table.setCellWidget(row, 0, check)
            title = QTableWidgetItem(topic.title)
            title.setFlags(Qt.ItemFlag.ItemIsEnabled)
            unit = QTableWidgetItem(topic.unit_path)
            unit.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self._table.setItem(row, 1, title)
            self._table.setItem(row, 2, unit)
            self._table.item(row, 1).setToolTip(f"Topic ID: {topic.id}")
        self._apply_filter()

    def _apply_filter(self):
        term = self._filter.text().casefold().strip()
        for row, topic in enumerate(self._topics):
            self._table.setRowHidden(
                row, bool(term and term not in f"{topic.title} {topic.unit_path}".casefold())
            )
        self._update_selection()

    def _select_visible_rows(self):
        for row in range(len(self._topics)):
            if not self._table.isRowHidden(row):
                self._table.cellWidget(row, 0).setChecked(True)
        self._update_selection()

    def _toggle_row(self, row, _column):
        check = self._table.cellWidget(row, 0)
        if check and not self._busy:
            check.setChecked(not check.isChecked())

    def _clear_selection(self):
        for row in range(len(self._topics)):
            self._table.cellWidget(row, 0).setChecked(False)
        self._update_selection()

    def _selected_topics(self):
        return [
            topic for row, topic in enumerate(self._topics)
            if self._table.cellWidget(row, 0).isChecked()
        ]

    def _update_selection(self, _item=None):
        count = len(self._selected_topics())
        self._count.setText(f"{count} selected of {len(self._topics)} topics")
        self._remove.setEnabled(not self._busy and count > 0)

    def _remove_clicked(self):
        if self._busy:
            return
        selected = self._selected_topics()
        if not selected or course_id_from_url(self._url.text()) != self._loaded_course_id:
            self._log.append_log("Reload the course outline before removing topics.", "warning")
            return
        preview = "\n".join(f"• {topic.unit_path} / {topic.title}" for topic in selected[:12])
        if len(selected) > 12:
            preview += f"\n… and {len(selected) - 12} more"
        box = QMessageBox(self)
        box.setWindowTitle("Remove Content topics")
        box.setIcon(QMessageBox.Icon.Warning)
        box.setText(f"Remove {len(selected)} selected topic(s) from the Content outline?")
        box.setInformativeText(preview + "\n\nLinked files and activities will stay in the course.")
        box.setDetailedText("\n".join(
            f"{topic.unit_path} / {topic.title} (topic {topic.id})" for topic in selected
        ))
        box.setStandardButtons(QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Yes)
        box.setDefaultButton(QMessageBox.StandardButton.Cancel)
        if box.exec() != QMessageBox.StandardButton.Yes:
            return

        url = self._url.text().strip()
        credentials = self._credentials()
        self._set_busy(True)
        self._log.append_log(f"Checking and removing {len(selected)} Content topic(s)…", "info")

        def worker():
            try:
                result = asyncio.run(remove_course_topics(
                    url, selected, credentials,
                    lambda done, total, topic: self._queue.put(
                        ("progress", (done, total, topic.title))
                    ),
                ))
                self._queue.put(("removed", result))
            except Exception as exc:
                self._queue.put(("error", exc))

        threading.Thread(target=worker, daemon=True).start()

    def _poll(self):
        try:
            while True:
                kind, payload = self._queue.get_nowait()
                if kind == "loaded":
                    self._loaded_course_id, topics = payload
                    self._display_topics(topics)
                    self._log.append_log(f"Loaded {len(topics)} Content topic(s).", "success")
                    self._set_busy(False)
                elif kind == "progress":
                    done, total, title = payload
                    self._log.append_log(f"Removed {done}/{total}: {title}", "success")
                elif kind == "removed":
                    removed_ids = {item.id for item in payload.removed}
                    self._display_topics([
                        item for item in self._topics if item.id not in removed_ids
                    ])
                    self._set_busy(False)
                    if payload.failed:
                        self._log.append_log(
                            f"Stopped at {payload.failed.title}: {payload.error}. "
                            f"{len(payload.removed)} removed. Reload before trying again.", "error"
                        )
                        self._loaded_course_id = ""
                        self._remove.setEnabled(False)
                    else:
                        self._log.append_log(
                            f"Done: {len(payload.removed)} topic(s) removed from Content.", "success"
                        )
                elif kind == "error":
                    message, detail = friendly_error(payload)
                    self._log.append_log(message, "error")
                    if detail != message:
                        self._log.append_log(detail, "detail")
                    self._set_busy(False)
                    self._loaded_course_id = ""
                    self._remove.setEnabled(False)
        except queue.Empty:
            pass
