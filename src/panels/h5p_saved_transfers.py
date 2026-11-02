"""A searchable native library for continuing and tidying saved H5P work."""
from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDialog, QFrame,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPushButton, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QSizePolicy, QStyledItemDelegate,
    QStyleOptionViewItem, QStyle, QApplication)

import gui_styles
from h5p_history import describe_run, set_run_removed
from h5p_progress import saved_runs
from gui_tool_icons import tool_pixmap


class RowBackground(QStyledItemDelegate):
    """Keep selection chrome and accessible model text behind custom cell labels."""
    def paint(self, painter, option, index):
        option = QStyleOptionViewItem(option)
        self.initStyleOption(option, index)
        option.text = ''
        style = option.widget.style() if option.widget else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, option, painter, option.widget)


class CourseTitle(QLabel):
    """A compact title whose full text remains available to assistive tools."""
    def __init__(self, text):
        super().__init__()
        self.full_text = text
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setProperty('role', 'field-label')
        self.setAccessibleName(text)
        self.setToolTip(text)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.setText(self.fontMetrics().elidedText(self.full_text, Qt.TextElideMode.ElideRight, self.width()))


class SavedH5PTransfersDialog(QDialog):
    def __init__(self, directory=None, parent=None):
        super().__init__(parent)
        self.directory = directory
        self.selected_run_id = None
        self._undo_ids = []
        self.setObjectName('h5p_saved_transfers')
        self.setProperty('design', 'modern')
        self.setWindowTitle('Saved H5P transfers')
        self.resize(920, 700)
        self.setMinimumSize(560, 460)
        self.setStyleSheet(gui_styles.get_modern_stylesheet())
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 20)
        layout.setSpacing(14)
        heading = QHBoxLayout()
        heading.addWidget(self._label('Saved transfers', 'page-heading'))
        heading.addStretch()
        self.count = self._label('', 'status')
        heading.addWidget(self.count)
        layout.addLayout(heading)
        layout.addWidget(self._label('Find a course, pick up where you left off, or tidy up old scans.', 'dim'))
        filters = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText('Search course name or ID…')
        self.search.setAccessibleName('Search saved transfers by course name or ID')
        self.search.setClearButtonEnabled(True)
        self.search.addAction(QIcon(tool_pixmap('search', 16, gui_styles.modern_colors()['muted'])), QLineEdit.ActionPosition.LeadingPosition)
        filters.addWidget(self.search, 1)
        self.filter = QComboBox()
        self.filter.setAccessibleName('Filter saved transfers')
        for title, key in (('All saved', 'all'), ('Needs attention', 'attention'),
                           ('Complete', 'complete'), ('No H5P activities', 'empty'), ('Removed', 'removed')):
            self.filter.addItem(title, key)
        filters.addWidget(self.filter)
        layout.addLayout(filters)
        self.table = QTableWidget(0, 3)
        self.table.setAccessibleName('Saved H5P transfers. Select one to continue, or several to remove.')
        self.table.setHorizontalHeaderLabels(['Course', 'Progress', 'Last saved'])
        self.table.verticalHeader().hide()
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.table.setItemDelegateForColumn(0, RowBackground(self.table))
        self.table.setItemDelegateForColumn(1, RowBackground(self.table))
        header = self.table.horizontalHeader()
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column, width in ((1, 180), (2, 145)):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
            self.table.setColumnWidth(column, width)
        layout.addWidget(self.table, 1)
        self.empty = self._label('', 'dim')
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty.setMinimumHeight(130)
        layout.addWidget(self.empty, 1)
        self.selection = self._label('Select a transfer to continue. Ctrl/Shift selects several to remove.', 'hint')
        layout.addWidget(self.selection)
        self.notice_row = QFrame()
        notice = QHBoxLayout(self.notice_row)
        notice.setContentsMargins(0, 0, 0, 0)
        self.notice = self._label('', 'hint')
        notice.addWidget(self.notice, 1)
        self.undo = self._button('Undo', self._undo)
        self.undo.setProperty('variant', 'secondary')
        notice.addWidget(self.undo)
        self.notice_row.hide()
        layout.addWidget(self.notice_row)
        layout.addWidget(self._label('Removing an entry only changes this list. Course pages, H5P uploads and downloaded files are kept.', 'hint'))
        footer = QHBoxLayout()
        self.remove = self._button('Remove from list', self._change_removed)
        self.remove.setProperty('variant', 'secondary')
        footer.addWidget(self.remove)
        footer.addStretch()
        close = self._button('Close', self.reject)
        close.setProperty('variant', 'secondary')
        footer.addWidget(close)
        self.open_button = self._button('Continue transfer', self._open)
        # Enter in the search box must not accidentally start a transfer.
        self.open_button.setAutoDefault(False)
        footer.addWidget(self.open_button)
        layout.addLayout(footer)
        self.search.textChanged.connect(self._render)
        self.filter.currentIndexChanged.connect(self._render)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        self.table.itemDoubleClicked.connect(lambda *args: self._open())
        self._reload()

    @staticmethod
    def _label(text, role=None):
        label = QLabel(text)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setWordWrap(True)
        if role:
            label.setProperty('role', role)
        return label

    @staticmethod
    def _button(text, callback):
        button = QPushButton(text)
        button.setMinimumHeight(38)
        button.setAutoDefault(False)
        button.clicked.connect(callback)
        return button

    def _reload(self):
        self._runs = saved_runs(self.directory, include_removed=True)
        self._render()

    def _render(self, *args):
        query, category = self.search.text().casefold().strip(), self.filter.currentData()
        active = sum(not run.data.get('removed_at') for run in self._runs)
        self.count.setText(f'{len(self._runs) - active} removed' if category == 'removed' else f'{active} saved')
        self.table.setRowCount(0)
        self._visible = []
        for run in self._runs:
            info = describe_run(run)
            if info['removed'] != (category == 'removed'):
                continue
            if category not in ('all', 'removed') and info['category'] != category:
                continue
            if query and query not in info['search']:
                continue
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setRowHeight(row, 88)
            self._visible.append(run)
            # Keep semantic table text for screen readers and keyboard selection.
            values = (info['title'] + '\n' + info['route'], info['status'] + '\n' + info['progress'],
                      info['date'] + '\n' + info['time'] + '\n' + 'Transfer ' + run.data['id'][:6])
            for column, text in enumerate(values):
                item = QTableWidgetItem(text)
                item.setData(Qt.ItemDataRole.UserRole, run.data['id'])
                item.setToolTip('\n'.join((info['title'], info['route'], info['status'], info['progress'],
                    info['date'] + ' · ' + info['time'], 'Transfer ' + run.data['id'])))
                self.table.setItem(row, column, item)
            course = QFrame()
            course_layout = QVBoxLayout(course)
            course_layout.setContentsMargins(8, 9, 8, 9)
            course_layout.setSpacing(4)
            course_layout.addWidget(CourseTitle(info['title']))
            course_layout.addWidget(self._label(info['route'], 'hint'))
            course.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            self.table.setCellWidget(row, 0, course)
            progress = QFrame()
            progress_layout = QVBoxLayout(progress)
            progress_layout.setContentsMargins(8, 12, 8, 12)
            progress_layout.setSpacing(6)
            badge = self._label(info['status'], 'status')
            badge.setProperty('state', 'created' if info['category'] == 'complete' else
                'review' if info['category'] == 'attention' else '')
            progress_layout.addWidget(badge, 0, Qt.AlignmentFlag.AlignLeft)
            progress_layout.addWidget(self._label(info['progress'], 'hint'))
            progress.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            self.table.setCellWidget(row, 1, progress)
        self.empty.setText('No removed transfers.' if category == 'removed' and not query else
            'No transfers match. Try another course name or filter.' if query or category != 'all' else
            'Your saved list is clear. New scans will appear here.')
        self.table.setVisible(bool(self._visible))
        self.empty.setVisible(not self._visible)
        self._selection_changed()

    def selected_runs(self):
        return [self._visible[index.row()] for index in self.table.selectionModel().selectedRows()]

    def _selection_changed(self):
        selected = self.selected_runs()
        removed = self.filter.currentData() == 'removed'
        self.open_button.setEnabled(len(selected) == 1 and not removed)
        self.open_button.setVisible(not removed)
        self.open_button.setText('Review transfer' if len(selected) == 1 and
            describe_run(selected[0])['category'] in ('complete', 'empty') else 'Continue transfer')
        self.remove.setEnabled(bool(selected))
        self.remove.setText('Restore to list' if removed else 'Remove from list')
        self.remove.setIcon(QIcon() if removed else QIcon(tool_pixmap('trash', 16, gui_styles.modern_colors()['muted'])))
        self.selection.setText(f"{len(selected)} {'transfer' if len(selected) == 1 else 'transfers'} selected" if selected else
            'Select removed transfers to restore them to your saved list.' if removed else
            'Select a transfer to continue. Ctrl/Shift selects several to remove.')

    def _open(self):
        selected = self.selected_runs()
        if len(selected) == 1 and not selected[0].data.get('removed_at'):
            self.selected_run_id = selected[0].data['id']
            self.accept()

    def _apply(self, ids, removed):
        changed, failed = [], 0
        for run_id in ids:
            try:
                set_run_removed(run_id, removed, self.directory)
                changed.append(run_id)
            except (OSError, ValueError, RuntimeError):
                failed += 1
        self._reload()
        text = f"{len(changed)} {'transfer' if len(changed) == 1 else 'transfers'} {'removed from' if removed else 'restored to'} the list."
        if failed:
            text += f' {failed} could not be changed; check that they are not running in another window.'
        self.notice.setText(text)
        self.notice_row.show()
        return changed

    def _change_removed(self):
        removed = self.filter.currentData() != 'removed'
        changed = self._apply([run.data['id'] for run in self.selected_runs()], removed)
        self._undo_ids = changed if removed else []
        self.undo.setVisible(bool(self._undo_ids))

    def _undo(self):
        changed = self._apply(self._undo_ids, False)
        self._undo_ids = [id_ for id_ in self._undo_ids if id_ not in changed]
        self.undo.setVisible(bool(self._undo_ids))
