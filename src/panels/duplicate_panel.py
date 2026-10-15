"""Select content, configure copies, review a live tree, then create."""
from __future__ import annotations

import asyncio
import copy
import queue
import threading
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QSize
from PySide6.QtGui import QIcon, QColor, QBrush
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QTreeWidget, QTreeWidgetItem, QSplitter, QFrame, QScrollArea, QComboBox, QSpinBox,
    QCheckBox, QDialog, QPlainTextEdit, QFileDialog, QSizePolicy, QAbstractSpinBox, QHeaderView)

from bulk_page_creator import course_id_from_url
from config import USERDATA_DIR
from content_duplicate import (make_copy_plan, selected_roots, copy_nodes, content_kind, copy_notes,
                               scan_copy_course, apply_copy, load_copy_plan)
from transfer_plan import all_objects
from transfer_runner import save_plan, with_transfer_browser
from gui_tool_icons import tool_pixmap
from panels._shared import NoScrollComboBox
from panels.duplicate_styles import colors, stylesheet


class DuplicatePanel(QWidget):
    def __init__(self, main_window, parent=None, data_dir=None):
        super().__init__(parent)
        self._mw = main_window
        self._data_dir = Path(data_dir) if data_dir else USERDATA_DIR / 'content-copies'
        self._course = None; self._plan = None; self._path = None
        self._busy = False; self._creating = False; self._loading = False
        self._queue = queue.Queue(); self._stop = threading.Event()
        self._items = {}; self._draft_id = None; self._compact = None
        self._name_roots = ()
        self._build(); self.refresh_theme()
        self._timer = QTimer(self); self._timer.timeout.connect(self._poll); self._timer.start(100)
        config = main_window.load_config() if hasattr(main_window, 'load_config') else {}
        self._url.setText(config.get('duplicate_course_url', ''))
        path = config.get('duplicate_plan_path')
        if path and Path(path).exists():
            try: self._restore_plan(load_copy_plan(path), Path(path))
            except Exception: self._status.setText('The saved copy plan could not be restored. Load the course to start again.')
        self._update_actions()

    @staticmethod
    def label(text, role='muted'):
        result = QLabel(text); result.setProperty('role', role)
        result.setWordWrap(True); result.setMinimumWidth(0); result.setTextFormat(Qt.TextFormat.PlainText)
        return result

    @staticmethod
    def line():
        result = QFrame(); result.setProperty('role', 'line'); result.setFixedHeight(1)
        return result

    def button(self, text, handler, variant=''):
        result = QPushButton(text); result.setProperty('variant', variant)
        result.setCursor(Qt.CursorShape.PointingHandCursor); result.clicked.connect(handler)
        return result

    def _build(self):
        self.setObjectName('duplicate_panel'); self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        root = QVBoxLayout(self); root.setContentsMargins(0, 0, 0, 0); root.setSpacing(0)
        header = QWidget(); row = QHBoxLayout(header); row.setContentsMargins(30, 23, 28, 22)
        heading = QVBoxLayout(); heading.setSpacing(6)
        heading.addWidget(self.label('Duplicate content', 'heading'))
        self._course_name = self.label('Load a course to choose sections and pages.')
        heading.addWidget(self._course_name); row.addLayout(heading, 1)
        self._url = QLineEdit(); self._url.setPlaceholderText('Brightspace course or section URL')
        self._url.setAccessibleName('Course URL'); self._url.setMinimumWidth(0); self._url.setMaximumWidth(420)
        self._url.textChanged.connect(self._update_actions)
        self._load = self.button('Load course', self._load_clicked)
        row.addWidget(self._url, 1); row.addWidget(self._load)
        root.addWidget(header); root.addWidget(self.line())
        self._split = QSplitter(); self._split.setChildrenCollapsible(False); self._split.setHandleWidth(1)
        left = QWidget(); box = QVBoxLayout(left); box.setContentsMargins(30, 25, 26, 20); box.setSpacing(18)
        box.addWidget(self.label('Choose what to copy', 'section'))
        self._search = QLineEdit(); self._search.setPlaceholderText('Find a section or page'); self._search.setAccessibleName('Search course content')
        self._search.textChanged.connect(self._filter); box.addWidget(self._search)
        self._source = QTreeWidget(); self._source.setHeaderHidden(True); self._source.setColumnCount(1)
        self._source.setIndentation(26); self._source.setIconSize(QSize(22, 22))
        self._source.setAccessibleName('Sections and pages to copy'); self._source.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._source.setMinimumWidth(0); self._source.itemChanged.connect(self._selection_changed)
        box.addWidget(self._source, 1)
        self._open_saved = self.button('Open saved copy plan', self._open_plan)
        box.addWidget(self._open_saved, 0, Qt.AlignmentFlag.AlignLeft)
        right_scroll = QScrollArea(); right_scroll.setWidgetResizable(True); right_scroll.setFrameShape(QFrame.Shape.NoFrame)
        right_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        right = QWidget(); settings = QVBoxLayout(right); settings.setContentsMargins(28, 25, 27, 20); settings.setSpacing(12)
        settings.addWidget(self.label('Copy settings', 'section'))
        settings.addWidget(self.label('Choose where and how many.')); settings.addSpacing(8)
        settings.addWidget(self.label('Destination', 'field'))
        self._destination = NoScrollComboBox(); self._destination.setAccessibleName('Destination for the copies')
        self._destination.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self._destination.setMinimumContentsLength(8); self._destination.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._destination.addItem('Course home', None); self._destination.currentIndexChanged.connect(self._refresh_preview)
        settings.addWidget(self._destination); settings.addSpacing(3)
        count = QHBoxLayout(); count.addWidget(self.label('Copies', 'field')); count.addStretch()
        self._minus = self.button('−', lambda: self._copies.setValue(self._copies.value() - 1), 'stepper')
        self._copies = QSpinBox(); self._copies.setRange(1, 20); self._copies.setValue(1); self._copies.setFixedWidth(66)
        self._copies.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons); self._copies.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._copies.setAccessibleName('Number of copies'); self._copies.valueChanged.connect(self._refresh_preview)
        self._plus = self.button('+', lambda: self._copies.setValue(self._copies.value() + 1), 'stepper')
        self._minus.setAccessibleName('Fewer copies'); self._plus.setAccessibleName('More copies')
        for control in (self._minus, self._copies, self._plus): count.addWidget(control)
        settings.addLayout(count); settings.addSpacing(4)
        settings.addWidget(self.label('Copy name', 'field'))
        self._name = QLineEdit(); self._name.setPlaceholderText('Select content to name the copy')
        self._name.setAccessibleName('Copy name'); self._name.textChanged.connect(self._refresh_preview); settings.addWidget(self._name)
        self._include = QCheckBox('Include everything inside'); self._include.setChecked(True)
        self._include.toggled.connect(self._refresh_preview); settings.addWidget(self._include)
        settings.addSpacing(8); settings.addWidget(self.line()); settings.addSpacing(6)
        settings.addWidget(self.label('Resulting structure', 'section'))
        settings.addWidget(self.label('This is what your new copies will contain.'))
        self._destination_hint = self.label('Choose content on the left.')
        settings.addWidget(self._destination_hint)
        self._preview = QTreeWidget(); self._preview.setObjectName('copy_structure_preview')
        self._preview.setHeaderHidden(True); self._preview.setColumnCount(1)
        self._preview.setIndentation(20); self._preview.setAccessibleName('Resulting structure preview')
        self._preview.setMinimumHeight(260); self._preview.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        settings.addWidget(self._preview, 1)
        settings.addWidget(self.label('Activity shortcuts use the existing assignment, quiz or other activity.'))
        right_scroll.setWidget(right)
        self._split.addWidget(left); self._split.addWidget(right_scroll); self._split.setSizes([650, 470])
        self._left_pane, self._right_pane, self._right_content = left, right_scroll, right
        self._workspace_scroll = QScrollArea(); self._workspace_scroll.setWidgetResizable(True)
        self._workspace_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._workspace_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._workspace_scroll.setWidget(self._split)
        root.addWidget(self._workspace_scroll, 1); root.addWidget(self.line())
        footer = QWidget(); row = QHBoxLayout(footer); row.setContentsMargins(30, 17, 28, 18); row.setSpacing(16)
        self._status = self.label('Choose content. Originals stay in place. New copies start hidden.')
        row.addWidget(self._status, 1)
        self._clear = self.button('Clear selection', self._clear_selection)
        self._review = self.button('Review copies', self._review_clicked, 'primary')
        self._stop_button = self.button('Stop after current request', self._stop.set)
        self._stop_button.hide(); row.addWidget(self._stop_button)
        row.addWidget(self._clear); row.addWidget(self._review); root.addWidget(footer)

    def refresh_theme(self):
        self.setStyleSheet(stylesheet())
        search_icon = QIcon(tool_pixmap('search', 18, colors()['muted']))
        if self._search.actions(): self._search.actions()[0].setIcon(search_icon)
        else: self._search.addAction(search_icon, QLineEdit.ActionPosition.LeadingPosition)
        for button, name in ((self._minus, 'minus'), (self._plus, 'plus')):
            button.setText(''); button.setIcon(QIcon(tool_pixmap(name, 18, colors()['text'])))
        self._search.setPlaceholderText('Find a section or page')
        if self._course:
            for id_, item in self._items.items():
                node = next(n for n in all_objects(self._course['modules']) if str(n['Id']) == id_)
                item.setIcon(0, self._icon(node))
            self._refresh_preview()

    def _icon(self, node):
        kind = content_kind(node)
        name = 'folder' if kind == 'Section' else 'link' if kind in ('Link', 'Existing activity link') else 'file'
        return QIcon(tool_pixmap(name, 21, colors()['text']))

    def _set_course(self, course):
        self._loading = True; self._course = course; self._plan = None; self._draft_id = None; self._path = None
        self._name_roots = ()
        self._source.clear(); self._items.clear(); self._preview.clear(); self._destination.clear()
        self._destination.addItem('Course home', None); self._course_name.setText(course['name'])
        self._url.setText(f"https://learn.okanagancollege.ca/d2l/le/lessons/{course['id']}")
        self._url.setCursorPosition(0)
        def add(entries, parent=None, path=()):
            for node in entries:
                text = node['Title'] + ('\nExisting activity shortcut' if content_kind(node) == 'Existing activity link' else '')
                item = QTreeWidgetItem([text]); item.setData(0, Qt.ItemDataRole.UserRole, str(node['Id']))
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable); item.setCheckState(0, Qt.CheckState.Unchecked)
                item.setIcon(0, self._icon(node)); item.setToolTip(0, node['Title'] + '\n' + content_kind(node))
                if parent: parent.addChild(item)
                else: self._source.addTopLevelItem(item)
                self._items[str(node['Id'])] = item
                if node['Type'] == 0:
                    self._destination.addItem(' / '.join((*path, node['Title'])), str(node['Id']))
                    add(node.get('children', []), item, (*path, node['Title']))
        add(course['modules']); self._name.clear(); self._copies.setValue(1); self._include.setChecked(True)
        self._loading = False; self._load.setText('Reload course'); self._refresh_preview()

    def _selection_changed(self, item, _column):
        if self._loading or self._busy or self._plan and self._plan.get('run_signature'): return
        self._loading = True
        def descend(parent):
            for i in range(parent.childCount()):
                child = parent.child(i); child.setCheckState(0, item.checkState(0)); descend(child)
        descend(item); item.setExpanded(item.checkState(0) == Qt.CheckState.Checked)
        self._source.setCurrentItem(item)
        for source_item in self._items.values():
            source_item.setBackground(0, QBrush(QColor(colors()['selected'])) if source_item.checkState(0) == Qt.CheckState.Checked else QBrush())
        ids = self._selected()
        roots = selected_roots(self._course, ids) if ids else []
        root_ids = tuple(str(root['Id']) for root in roots)
        if root_ids != self._name_roots:
            self._name.setText(roots[0]['Title'][:140] + ' — Copy' if len(roots) == 1 else '')
        self._name_roots = root_ids
        self._name.setPlaceholderText('Original names + Copy' if len(roots) > 1 else 'Select content to name the copy')
        self._name.setEnabled(len(roots) == 1)
        self._loading = False; self._refresh_preview()

    def _selected(self):
        return [id_ for id_, item in self._items.items() if item.checkState(0) == Qt.CheckState.Checked]

    def _excluded(self):
        result = []
        for id_, item in self._items.items():
            if item.checkState(0) == Qt.CheckState.Checked: continue
            parent = item.parent()
            while parent:
                if parent.checkState(0) == Qt.CheckState.Checked:
                    result.append(id_); break
                parent = parent.parent()
        return result

    def _refresh_preview(self, *_):
        if self._loading or self._busy: return
        if self._plan and self._plan.get('run_signature'):
            self._paint_preview(); self._update_actions(); return
        self._preview.clear(); self._plan = None
        if self._course:
            try:
                plan = make_copy_plan(self._course, self._selected(), self._destination.currentData(), self._copies.value(),
                                      self._name.text(), self._include.isChecked(), self._excluded())
                if self._draft_id: plan['id'] = self._draft_id
                else: self._draft_id = plan['id']
                self._plan = plan; self._path = self._data_dir / (plan['id'] + '.json')
                self._paint_preview(); self._status.setText(f"{len(list(copy_nodes(plan)))} items to create. Originals stay in place. Copies start hidden.")
                save_plan(plan, self._path)
            except ValueError as exc: self._status.setText(str(exc))
        self._update_actions()

    def _paint_preview(self):
        self._preview.clear()
        if not self._plan: return
        self._preview.setColumnCount(2)
        self._preview.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._preview.header().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self._preview.setColumnWidth(1, 100)
        self._preview.setObjectName('copy_structure_preview')
        self._destination_hint.setText((self._destination.currentText() or 'Course home') + ' / New copies')
        def add(nodes, parent=None):
            for node in nodes:
                title = node['title']
                item = QTreeWidgetItem([title]); item.setIcon(0, self._icon(node['source']))
                total = len(node['children'])
                detail = f"{total} {'item' if total == 1 else 'items'}" if node['source']['Type'] == 0 else content_kind(node['source'])
                if total == 1 and content_kind(node['children'][0]['source']) == 'Existing activity link': detail = '1 activity link'
                item.setToolTip(0, title + '\n' + detail)
                if parent: parent.addChild(item)
                else: self._preview.addTopLevelItem(item)
                if parent and node['source']['Type'] == 0:
                    item.setText(1, detail); item.setForeground(1, QBrush(QColor(colors()['muted'])))
                else: item.setFirstColumnSpanned(True)
                add(node['children'], item); item.setExpanded(True)
        add(self._plan['roots'])

    def _filter(self, text):
        text = text.casefold().strip()
        def walk(item):
            children = [walk(item.child(i)) for i in range(item.childCount())]
            visible = not text or text in item.text(0).casefold() or any(children)
            item.setHidden(not visible)
            if text and any(children): item.setExpanded(True)
            return visible
        for i in range(self._source.topLevelItemCount()): walk(self._source.topLevelItem(i))

    def _same_course(self):
        try: return bool(self._course and course_id_from_url(self._url.text()) == self._course['id'])
        except ValueError: return False

    def _update_actions(self, *_):
        locked = bool(self._plan and self._plan.get('run_signature'))
        for widget in (self._source, self._destination, self._copies, self._plus, self._minus, self._name, self._include, self._clear):
            widget.setEnabled(bool(self._course) and not self._busy and not locked)
        if self._course and not locked and not self._busy:
            ids = self._selected()
            self._name.setEnabled(bool(ids) and len(selected_roots(self._course, ids)) == 1)
        self._url.setEnabled(not self._busy); self._load.setEnabled(not self._busy); self._open_saved.setEnabled(not self._busy)
        self._review.setEnabled(bool(self._plan) and not self._busy and self._same_course())
        self._review.setText('View report' if self._plan and self._plan.get('complete') else 'Review / resume' if locked else 'Review copies')
        self._stop_button.setVisible(self._busy); self._stop_button.setEnabled(self._busy)
        if self._course and not self._same_course(): self._status.setText('Course link changed. Load it before creating copies.')

    def _clear_selection(self):
        if self._busy: return
        self._loading = True
        for item in self._items.values(): item.setCheckState(0, Qt.CheckState.Unchecked)
        self._loading = False; self._name.clear(); self._refresh_preview()

    def _credentials(self):
        return {key: getattr(self._mw, key, '') for key in ('bs_username', 'bs_password', 'sso_email', 'sso_password')}

    def _worker(self, action, kind):
        self._busy = True; self._stop.clear(); self._update_actions()
        def run():
            try: self._queue.put((kind, asyncio.run(action())))
            except Exception as exc: self._queue.put(('error', str(exc).split('Call log:')[0].strip()))
        threading.Thread(target=run, daemon=True).start()

    def _log(self, message, level='info'): self._queue.put(('log', message))

    def _load_clicked(self):
        try:
            if not getattr(self._mw, 'chromium_ready', True): raise ValueError('The browser engine is still installing.')
            course_id = course_id_from_url(self._url.text())
        except ValueError as exc: self._status.setText(str(exc)); return
        url, credentials = self._url.text(), self._credentials()
        self._status.setText('Reading the course. Nothing is being copied yet.')
        self._worker(lambda: with_transfer_browser(url, credentials,
            lambda page: scan_copy_course(page, course_id, self._log, self._stop), self._log, self._stop), 'loaded')

    def _review_clicked(self):
        if not self._plan or not self._same_course(): return
        dialog = QDialog(self); dialog.setProperty('design', 'modern'); dialog.setWindowTitle('Review copies')
        dialog.resize(700, 660); layout = QVBoxLayout(dialog); layout.setContentsMargins(24, 24, 24, 24); layout.setSpacing(16)
        layout.addWidget(self.label('Copy into ' + self._course['name'], 'section'))
        text = QPlainTextEdit(); text.setReadOnly(True)
        lines = []
        def outline(nodes, depth=0):
            for node in nodes:
                lines.append('  ' * depth + node['title'] + (' [existing activity shortcut]' if content_kind(node['source']) == 'Existing activity link' else ''))
                outline(node['children'], depth + 1)
        outline(self._plan['roots'])
        text.setPlainText(self._plan.get('report') or '\n'.join(['Destination: ' + self._destination.currentText(), '', *lines, '', *copy_notes(self._plan)]))
        layout.addWidget(text, 1)
        if not self._plan.get('complete'):
            ack = QCheckBox('I reviewed these copies and want to create them in this course.')
            ack.setChecked(self._plan.get('reviewed', False)); layout.addWidget(ack)
            create = self.button('Resume saved copies' if self._plan.get('run_signature') else 'Create hidden copies', dialog.accept, 'primary')
            create.setEnabled(ack.isChecked()); ack.toggled.connect(create.setEnabled)
            actions = QHBoxLayout(); actions.addWidget(self.button('Cancel', dialog.reject)); actions.addStretch(); actions.addWidget(create)
            layout.addLayout(actions)
            if dialog.exec() == QDialog.DialogCode.Accepted:
                self._plan['reviewed'] = True; save_plan(self._plan, self._path); self._create_clicked()
        else:
            layout.addWidget(self.button('Close', dialog.reject)); dialog.exec()

    def _create_clicked(self):
        if not self._same_course() or not self._plan.get('reviewed'): return
        self._creating = True; plan, path = copy.deepcopy(self._plan), self._path
        url, credentials = self._url.text(), self._credentials()
        self._worker(lambda: with_transfer_browser(url, credentials,
            lambda page: apply_copy(page, plan, path, plan['course']['id'], self._log, self._stop), self._log, self._stop), 'created')

    def _poll(self):
        while not self._queue.empty():
            kind, value = self._queue.get()
            if kind == 'log': self._status.setText(value); continue
            self._busy = False; self._creating = False
            if kind == 'loaded': self._set_course(value)
            elif kind == 'created':
                self._plan = value; self._paint_preview(); self._status.setText('Copies verified. Open the report, then review and publish in Brightspace.')
            elif kind == 'error':
                if self._path and self._path.exists(): self._plan = load_copy_plan(self._path)
                self._status.setText(value)
            self.save_state(); self._update_actions()

    def _restore_plan(self, plan, path):
        self._set_course(plan['course']); self._loading = True
        for id_ in plan['selected']:
            if id_ in self._items: self._items[id_].setCheckState(0, Qt.CheckState.Checked)
        for id_ in plan['excluded']:
            if id_ in self._items: self._items[id_].setCheckState(0, Qt.CheckState.Unchecked)
        index = self._destination.findData(plan['destination']); self._destination.setCurrentIndex(max(0, index))
        self._copies.setValue(plan['copies']); self._name.setText(plan['name']); self._include.setChecked(plan['include_nested'])
        self._loading = False; self._plan = plan; self._path = path; self._draft_id = plan['id']
        self._name_roots = tuple(str(root['Id']) for root in selected_roots(self._course, plan['selected']))
        self._paint_preview(); self._status.setText('Saved copies restored. Resume this plan to avoid creating duplicates.')
        self._update_actions()

    def _open_plan(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Open saved copy plan', str(self._data_dir), 'JSON (*.json)')
        if not path: return
        try: self._restore_plan(load_copy_plan(path), Path(path)); self.save_state()
        except Exception as exc: self._status.setText(str(exc))

    def save_state(self):
        if self._plan and self._path and not self._busy: save_plan(self._plan, self._path)
        if hasattr(self._mw, 'save_config'):
            self._mw.save_config({'duplicate_course_url': self._url.text(), 'duplicate_plan_path': str(self._path) if self._path else ''})

    def resizeEvent(self, event):
        super().resizeEvent(event)
        compact = self.width() < 900
        if compact != self._compact:
            self._compact = compact
            self._split.setOrientation(Qt.Orientation.Vertical if compact else Qt.Orientation.Horizontal)
            right_height = max(860, self._right_content.sizeHint().height() + 24) if compact else 0
            self._split.setMinimumHeight(440 + right_height if compact else 0)
            self._left_pane.setMinimumHeight(440 if compact else 0)
            self._right_pane.setMinimumHeight(right_height)
            self._split.setSizes([440, right_height] if compact else [650, 470])
