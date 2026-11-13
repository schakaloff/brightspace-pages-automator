"""Second card on the H5P panel: add .h5p files from this computer to one Brightspace unit."""
import re
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QFrame, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QCheckBox,
    QPushButton, QFileDialog, QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView)

from h5p_handler import H5PHandler


def parse_unit_url(url: str) -> tuple[str, str]:
    """(course id, unit id) from a Brightspace lessons URL."""
    match = re.search(r'/lessons/(\d+)/units/(\d+)', url)
    if not match:
        raise ValueError('Paste a Brightspace unit URL, like …/d2l/le/lessons/<course>/units/<unit>.')
    return match.group(1), match.group(2)


def collect_files(rows: list[tuple[str, str]]) -> list[dict]:
    """Validate (path, title) rows into runner files; raises ValueError with a plain message."""
    if not rows:
        raise ValueError('Choose at least one .h5p file.')
    files, seen = [], set()
    for path, title in rows:
        title = title.strip()
        ok, why = H5PHandler.validate_h5p_package(Path(path))
        if not ok:
            raise ValueError(f'{Path(path).name}: {why}.')
        if not title:
            raise ValueError(f'{Path(path).name} needs a page title.')
        key = H5PHandler._norm_title(title)
        if key in seen:
            raise ValueError(f'Two files would be named "{title}". Give each page its own title.')
        seen.add(key)
        files.append({'path': Path(path), 'title': title})
    return files


class H5PManualCard(QFrame):
    """Emits run_requested(unit_url, files, grade) once the choices pass validation."""
    run_requested = Signal(str, list, bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setProperty('role', 'surface')
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)
        heading = QLabel('Add H5P files from this computer')
        heading.setProperty('role', 'section-heading')
        hint = QLabel('Upload .h5p files and place each on its own new page in one Brightspace unit. No Moodle course needed.')
        hint.setProperty('role', 'dim')
        hint.setWordWrap(True)
        layout.addWidget(heading)
        layout.addWidget(hint)

        self._unit_entry = QLineEdit()
        self._unit_entry.setAccessibleName('Brightspace unit URL')
        self._unit_entry.setPlaceholderText('https://learn.okanagancollege.ca/d2l/le/lessons/…/units/…')
        label = QLabel('Brightspace unit')
        label.setProperty('role', 'field-label')
        label.setBuddy(self._unit_entry)
        layout.addWidget(label)
        layout.addWidget(self._unit_entry)

        self._table = QTableWidget(0, 2)
        self._table.setAccessibleName('H5P files to add')
        self._table.setHorizontalHeaderLabels(['File', 'Page title (editable)'])
        self._table.verticalHeader().hide()
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setMinimumHeight(120)
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.setColumnWidth(0, 260)
        layout.addWidget(self._table)

        buttons = QHBoxLayout()
        self._browse = QPushButton('Choose .h5p files…')
        self._browse.clicked.connect(self._choose)
        self._remove = QPushButton('Remove selected')
        self._remove.setProperty('variant', 'secondary')
        self._remove.clicked.connect(self._remove_selected)
        buttons.addWidget(self._browse)
        buttons.addWidget(self._remove)
        buttons.addStretch()
        layout.addLayout(buttons)

        self._grade = QCheckBox('Add each to the gradebook')
        self._grade.setToolTip('The app checks afterwards that a gradebook item is linked to each H5P.')
        layout.addWidget(self._grade)
        self._message = QLabel('')
        self._message.setProperty('role', 'hint')
        self._message.setWordWrap(True)
        layout.addWidget(self._message)
        self._run = QPushButton('Add to Brightspace')
        self._run.clicked.connect(self._submit)
        layout.addWidget(self._run, 0, Qt.AlignmentFlag.AlignLeft)

    def _choose(self):
        paths, _ = QFileDialog.getOpenFileNames(self, 'Choose H5P files', '', 'H5P packages (*.h5p)')
        have = {self._table.item(r, 0).data(Qt.ItemDataRole.UserRole) for r in range(self._table.rowCount())}
        for path in paths:
            if path in have:
                continue
            row = self._table.rowCount()
            self._table.insertRow(row)
            name = QTableWidgetItem(Path(path).name)
            name.setData(Qt.ItemDataRole.UserRole, path)
            name.setFlags(name.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self._table.setItem(row, 0, name)
            self._table.setItem(row, 1, QTableWidgetItem(Path(path).stem))

    def _remove_selected(self):
        for row in sorted({i.row() for i in self._table.selectedIndexes()}, reverse=True):
            self._table.removeRow(row)

    def _submit(self):
        try:
            parse_unit_url(self._unit_entry.text())
            rows = [(self._table.item(r, 0).data(Qt.ItemDataRole.UserRole), self._table.item(r, 1).text())
                    for r in range(self._table.rowCount())]
            files = collect_files(rows)
        except ValueError as exc:
            self._message.setText(str(exc))
            return
        self._message.setText('')
        self.run_requested.emit(self._unit_entry.text().strip(), files, self._grade.isChecked())

    def set_busy(self, busy: bool):
        for widget in (self._unit_entry, self._table, self._browse, self._remove, self._grade, self._run):
            widget.setEnabled(not busy)
