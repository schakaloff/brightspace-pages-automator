"""The H5P workspace view. Browser work stays in H5PPanel's worker thread."""
from collections import defaultdict

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QLineEdit, QCheckBox, QComboBox, QFrame, QScrollArea,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView, QProgressBar)

from gui_log import LogWidget
import gui_styles
from gui_tool_icons import tool_pixmap
from panels._shared import NoScrollComboBox


class H5PWorkspace(QWidget):
    def _label(self, text, role=None):
        label = QLabel(text)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setWordWrap(True)
        if role:
            label.setProperty('role', role)
        return label

    def _card(self):
        card = QFrame()
        card.setProperty('role', 'surface')
        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)
        return card, layout

    def _button(self, text, callback, secondary=False):
        button = QPushButton(text)
        button.setMinimumHeight(38)
        if secondary:
            button.setProperty('variant', 'secondary')
        button.clicked.connect(callback)
        return button

    def _build(self):
        self.setProperty('design', 'modern')
        self.setObjectName('h5p_workspace')
        self._plan = None
        self._run_data = None
        self._row_controls = {}
        self._review_wait = None
        self._resume_id = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 22, 24, 18)
        outer.setSpacing(14)
        outer.addWidget(self._label('H5P transfer', 'page-heading'))
        outer.addWidget(self._label('Bring Moodle activities into the right Brightspace units, with a plan you can review first.', 'dim'))
        steps = QHBoxLayout()
        steps.setSpacing(8)
        self._steps = []
        self._step_icons = []
        for text, icon_name in (('Connect', 'link'), ('Review', 'clipboard-list'),
                                ('Transfer', 'arrows-exchange'), ('Results', 'circle-check')):
            step = QFrame()
            step.setProperty('role', 'h5p-step')
            step.setAccessibleName(text)
            step.setMinimumWidth(0)
            step.setFixedHeight(72)
            item = QVBoxLayout(step)
            item.setContentsMargins(8, 8, 8, 8)
            item.setSpacing(6)
            item.setAlignment(Qt.AlignmentFlag.AlignCenter)
            icon = QLabel()
            icon.setFixedSize(24, 24)
            item.addWidget(icon, 0, Qt.AlignmentFlag.AlignHCenter)
            label = self._label(text, 'h5p-step-title')
            label.setWordWrap(False)
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            item.addWidget(label)
            self._steps.append(step)
            self._step_icons.append((icon, icon_name))
            steps.addWidget(step, 1)
        outer.addLayout(steps)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        outer.addWidget(scroll, 1)
        self._scroll = scroll
        body = QWidget()
        scroll.setWidget(body)
        content = QVBoxLayout(body)
        content.setContentsMargins(0, 0, 8, 0)
        content.setSpacing(14)

        self._course_card, course = self._card()
        title = QHBoxLayout()
        title.addWidget(self._label('Your courses', 'section-heading'))
        title.addStretch()
        self._change_btn = self._button('Change courses', self._new_transfer, True)
        self._change_btn.hide()
        title.addWidget(self._change_btn)
        course.addLayout(title)
        self._course_summary = self._label('', 'dim')
        self._course_summary.hide()
        course.addWidget(self._course_summary)
        self._connection = QWidget()
        form = QVBoxLayout(self._connection)
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(8)
        self._moodle_entry, self._bs_entry = QLineEdit(), QLineEdit()
        for title, field, hint in (
            ('From Moodle', self._moodle_entry, 'https://mymoodle.okanagan.bc.ca/course/view.php?id=…'),
            ('To Brightspace', self._bs_entry, 'https://learn.okanagancollege.ca/d2l/le/content/…/home')):
            label = self._label(title, 'field-label')
            label.setBuddy(field)
            field.setAccessibleName(title + ' course URL')
            field.setPlaceholderText(hint)
            form.addWidget(label)
            form.addWidget(field)
        self._gradebook_cb = QCheckBox('Add H5P activities to gradebook')
        self._gradebook_cb.setToolTip('Default for new activities. You can change each activity during review. Existing pages are kept as they are.')
        form.addWidget(self._gradebook_cb)
        form.addWidget(self._label('Find activities reads the course list. Downloads and course content changes start only after you review the plan.', 'hint'))
        course.addWidget(self._connection)
        content.addWidget(self._course_card)

        self._saved_card, saved = self._card()
        saved.addWidget(self._label('Continue a saved transfer', 'section-heading'))
        self._saved_courses = QComboBox()
        self._saved_courses.setMinimumContentsLength(12)
        self._saved_courses.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self._saved_courses.setAccessibleName('Saved H5P transfers')
        saved.addWidget(self._saved_courses)
        self._resume_btn = self._button('Open saved transfer', self._resume_run, True)
        self._saved_courses.currentIndexChanged.connect(self._saved_selection_changed)
        saved.addWidget(self._resume_btn)
        content.addWidget(self._saved_card)

        self._attention, attention = self._card()
        self._status_line = self._label('Ready to find your activities', 'section-heading')
        self._status_hint = self._label('Enter a source and destination course to get started.', 'dim')
        attention.addWidget(self._status_line)
        attention.addWidget(self._status_hint)
        self._progress_bar = QProgressBar()
        self._progress_bar.setTextVisible(False)
        self._progress_bar.setAccessibleName('Activities placed in Brightspace')
        self._progress_bar.hide()
        attention.addWidget(self._progress_bar)
        self._ready_btn = self._button('I’m on the correct course — continue', self._moodle_ready)
        self._ready_btn.hide()
        attention.addWidget(self._ready_btn)
        self._h5p_ready_btn = self._button('Continue with downloads', self._h5p_ready)
        self._h5p_skip_btn = self._button('Leave downloads for later', self._h5p_skip, True)
        for button in (self._h5p_ready_btn, self._h5p_skip_btn):
            button.hide()
            attention.addWidget(button)
        content.addWidget(self._attention)

        self._workspace, workspace = self._card()
        heading = QHBoxLayout()
        self._list_title = self._label('Review your activities', 'section-heading')
        heading.addWidget(self._list_title, 1)
        self._only_attention = QCheckBox('Needs attention only')
        self._only_attention.toggled.connect(self._filter_rows)
        heading.addWidget(self._only_attention)
        workspace.addLayout(heading)
        self._selection_summary = self._label('', 'dim')
        workspace.addWidget(self._selection_summary)
        self._table = QTableWidget(0, 5)
        self._table.setAccessibleName('H5P activities grouped by destination unit')
        self._table.setHorizontalHeaderLabels(['Move', 'Activity', 'Brightspace unit', 'Grade item', 'Status'])
        self._table.verticalHeader().hide()
        self._table.setShowGrid(False)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setMinimumHeight(260)
        header = self._table.horizontalHeader()
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        header.setMinimumSectionSize(55)
        for col, width in ((0, 55), (1, 220), (2, 235), (3, 110), (4, 235)):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.Fixed if col in (0, 3) else QHeaderView.ResizeMode.Interactive)
            self._table.setColumnWidth(col, width)
        header.setStretchLastSection(True)
        workspace.addWidget(self._table)
        self._filter_hint = self._label('No activities need attention.', 'dim')
        self._filter_hint.hide()
        workspace.addWidget(self._filter_hint)
        self._apply_section = self._button('Use this destination for the same Moodle section', self._apply_to_section, True)
        workspace.addWidget(self._apply_section)
        self._review_note = self._label('Selected activities may have Allow download enabled in Moodle. Missing files are uploaded to the H5P library, then placed on Brightspace pages. Existing pages and their grading stay unchanged.', 'hint')
        workspace.addWidget(self._review_note)
        self._workspace.hide()
        content.addWidget(self._workspace)
        self._details_btn = QPushButton('Show activity details')
        self._details_btn.setProperty('variant', 'ghost')
        self._details_btn.setCheckable(True)
        self._details_btn.toggled.connect(self._toggle_details)
        content.addWidget(self._details_btn, 0, Qt.AlignmentFlag.AlignLeft)
        self._log = LogWidget()
        self._log.setMinimumHeight(160)
        self._log.setMaximumHeight(260)
        self._log.hide()
        content.addWidget(self._log)
        content.addStretch()

        footer = QVBoxLayout()
        footer.setSpacing(8)
        self._footer_hint = self._label('Your progress is saved automatically.', 'hint')
        footer.addWidget(self._footer_hint)
        actions = QHBoxLayout()
        self._back_btn = self._button('← Back', self._go_back, True)
        actions.addWidget(self._back_btn)
        actions.addStretch()
        self._new_btn = self._button('New transfer', self._new_transfer, True)
        self._new_btn.hide()
        actions.addWidget(self._new_btn)
        self._stop_btn = self._button('Stop && save', self._stop_run, True)
        self._stop_btn.hide()
        actions.addWidget(self._stop_btn)
        self._run_btn = self._button('Find activities', self._primary_clicked)
        actions.addWidget(self._run_btn)
        footer.addLayout(actions)
        outer.addLayout(footer)
        cfg = self._mw.load_config() if hasattr(self._mw, 'load_config') else {}
        self._bs_entry.setText(cfg.get('h5p_bs_url', ''))
        self._moodle_entry.setText(cfg.get('h5p_moodle_url', ''))
        self._refresh_saved_runs()
        self._set_stage(0)

    def _set_stage(self, index):
        self._stage = index
        self._update_back_navigation()
        if index < 3:
            self._furthest_stage = max(getattr(self, '_furthest_stage', 0), index)
        for i, step in enumerate(self._steps):
            state = 'review' if i == index else 'created' if i < getattr(self, '_furthest_stage', 0) else ''
            step.setProperty('state', state)
            step.setAccessibleDescription('Current step' if i == index else 'Completed step' if state == 'created' else 'Upcoming step')
            for widget in (step, *step.findChildren(QLabel)):
                widget.style().unpolish(widget)
                widget.style().polish(widget)
                widget.update()
        self.refresh_theme()

    def refresh_theme(self):
        colors = gui_styles.modern_colors()
        for step, (icon, name) in zip(self._steps, self._step_icons):
            state = step.property('state')
            color = colors['focus'] if state == 'review' else colors['success'] if state == 'created' else colors['muted']
            icon.setPixmap(tool_pixmap(name, 24, color))

    def _toggle_details(self, checked):
        self._log.setVisible(checked)
        self._details_btn.setText('Hide activity details' if checked else 'Show activity details')
        if checked:
            self._scroll.ensureWidgetVisible(self._log)

    def _new_transfer(self):
        if self._task_active:
            return
        self._resume_id = None
        self._plan = self._run_data = None
        self._review_wait = None
        self._last_error = None
        self._result_summary = {}
        self._workspace.hide()
        self._connection.show()
        self._course_summary.hide()
        self._change_btn.hide()
        self._new_btn.hide()
        self._progress_bar.hide()
        for button in (self._stop_btn, self._ready_btn, self._h5p_ready_btn, self._h5p_skip_btn):
            button.hide()
        self._status_line.setText('Ready to find your activities')
        self._status_hint.setText('Enter a source and destination course to get started.')
        self._run_btn.setText('Find activities')
        self._run_btn.setEnabled(True)
        self._furthest_stage = 0
        self._set_stage(0)
        self._refresh_saved_runs()

    def _primary_clicked(self):
        if self._review_wait:
            from h5p_review import validate_choices
            try:
                choices = self._choices()
                validate_choices(self._plan, choices)
            except ValueError as exc:
                self._status_hint.setText(str(exc))
                return
            result, event = self._review_wait
            self._review_wait = None
            result[0] = choices
            self._table.setEnabled(False)
            self._apply_section.hide()
            self._run_btn.setText('Transferring…')
            self._run_btn.setEnabled(False)
            self._set_stage(2)
            event.set()
        elif not self._task_active:
            self._start_run(self._resume_id)

    def _choices(self):
        return [dict(key=key, selected=c['move'].isChecked(), module_id=c['unit'].currentData(),
                     should_grade=c['grade'].isChecked()) for key, c in self._row_controls.items()]

    def _show_plan(self, plan, result, event):
        self._plan = plan
        self._resume_id = plan['run_id']
        self._review_wait = result, event
        self._set_stage(1)
        self._connection.hide()
        self._course_summary.setText('Moodle: ' + (plan.get('moodle_course_name') or plan['moodle_url']) + '\nBrightspace: ' + plan['course_name'])
        self._course_summary.setToolTip(plan['moodle_url'] + '\n' + plan['bs_url'])
        self._course_summary.show()
        self._status_line.setText('Choose what moves and where it goes')
        self._status_hint.setText('This review has not changed course content. Check destinations and grading, then start the transfer.')
        self._list_title.setText('Review your activities')
        self._only_attention.setChecked(False)
        self._only_attention.hide()
        self._render_rows(plan['activities'], editable=True)
        self._workspace.show()
        self._apply_section.show()
        self._review_note.show()
        self._run_btn.setEnabled(True)
        self._update_selection()

    @staticmethod
    def _row_status(row, active=False):
        if row.get('name_check_required'):
            return ('Rename the saved page to match Moodle' if row.get('saved_topic_id') else
                    'Check the saved page title in Brightspace before retrying'), True
        if row.get('duplicate_title'):
            return 'Duplicate title · rename in Moodle, then start a new transfer', True
        if row.get('grade_attention'):
            return 'Check gradebook settings in Brightspace', True
        if row.get('already_present'):
            return 'Already in Brightspace', False
        if row.get('insertion') == 'confirmed':
            return 'Placed in Brightspace', False
        if row.get('problem'):
            return row['problem'], True
        if not row.get('selected', True):
            return 'Left for later', True
        if row.get('insertion') == 'pending':
            return ('Placing in Brightspace…' if active else 'Placement needs checking'), not active
        if row.get('upload') == 'pending':
            return ('Uploading to H5P library…' if active else 'Upload needs checking'), not active
        if row.get('upload') == 'confirmed':
            return 'In H5P library · placement next', not active
        if row.get('download') == 'pending':
            return 'Downloading from Moodle…', False
        if row.get('download') == 'failed':
            return 'Download needs a replacement or retry', True
        if row.get('download') == 'confirmed':
            return 'Downloaded · upload next', not active
        return 'Ready to transfer' if row.get('bs_module_id') else 'Choose a destination', not active

    def _render_rows(self, rows, editable=False):
        horizontal = self._table.horizontalScrollBar().value()
        vertical = self._table.verticalScrollBar().value()
        self._table.setEnabled(True)
        self._table.clearSpans()
        self._table.setRowCount(0)
        self._row_controls = {}
        self._display_rows = []
        self._table.setAccessibleName('H5P activities grouped by Moodle section' if editable else 'H5P activities grouped by destination unit')
        groups = defaultdict(list)
        for row in rows:
            # Source sections stay stable while the user changes destination choices.
            title = ('Moodle · ' + (row.get('moodle_section') or 'Unnamed section')) if editable else (row.get('bs_module_title') or 'Choose a destination')
            groups[title].append(row)
        for title, members in groups.items():
            group_index = self._table.rowCount()
            self._table.insertRow(group_index)
            self._table.setSpan(group_index, 0, 1, 5)
            count = f"{len(members)} {'activity' if len(members) == 1 else 'activities'}"
            item = QTableWidgetItem(f'{title}  ·  {count}')
            item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            font = item.font()
            font.setBold(True)
            item.setFont(font)
            self._table.setItem(group_index, 0, item)
            for row in members:
                index = self._table.rowCount()
                self._table.insertRow(index)
                self._table.setRowHeight(index, 76 if not editable else 64)
                name = QTableWidgetItem(row['name'])
                name.setToolTip(row['name'] + '\nMoodle section: ' + (row.get('moodle_section') or 'Unnamed section'))
                self._table.setItem(index, 1, name)
                status, attention = self._row_status(row, self._task_active and self._stage == 2)
                self._table.setItem(index, 4, QTableWidgetItem(status))
                details = '\n'.join((status,
                    'Download: ' + row.get('download', 'not checked'),
                    'H5P library: ' + row.get('upload', 'not checked'),
                    'Brightspace page: ' + row.get('insertion', 'not checked')))
                self._table.item(index, 4).setToolTip(details)
                self._display_rows.append((index, group_index, attention))
                if editable:
                    move, grade = QCheckBox(), QCheckBox()
                    move.setAccessibleName('Transfer ' + row['name'])
                    grade.setAccessibleName('Create grade item for ' + row['name'])
                    move.setChecked(row.get('selected', True))
                    grade.setChecked(row['should_grade'])
                    unit = NoScrollComboBox()
                    unit.setMinimumWidth(180)
                    unit.setAccessibleName('Destination for ' + row['name'])
                    unit.addItem('Choose a unit…', None)
                    for module in self._plan['modules']:
                        unit.addItem(module['title'], module['id'])
                    from rebuild_helpers import valid_section_name
                    from js_helpers import _norm
                    section = row.get('moodle_section', '')
                    if valid_section_name(section) and not any(_norm(m['title']) == _norm(section) for m in self._plan['modules']):
                        unit.addItem('Create unit: ' + section, 'new:' + section)
                    selected = unit.findData(str(row.get('bs_module_id')))
                    unit.setCurrentIndex(max(0, selected))
                    for col, widget in ((0, move), (2, unit), (3, grade)):
                        self._table.setCellWidget(index, col, widget)
                        blocked = row.get('already_present') or row.get('duplicate_title') or (
                            row.get('name_check_required') and (col != 0 or not row.get('saved_topic_id')))
                        widget.setEnabled(not blocked)
                    self._row_controls[row['key']] = dict(move=move, grade=grade, unit=unit, row=row, index=index)
                    move.toggled.connect(self._update_selection)
                    unit.currentIndexChanged.connect(self._update_selection)
                    unit.activated.connect(lambda value, i=index: self._table.setCurrentCell(i, 2))
                    grade.toggled.connect(self._update_selection)
                else:
                    self._table.setItem(index, 0, QTableWidgetItem(''))
                    self._table.setItem(index, 2, QTableWidgetItem(row.get('bs_module_title') or 'Not chosen'))
                    grade_text = 'Unchanged' if row.get('already_present') else 'Yes' if row['should_grade'] else 'No'
                    self._table.setItem(index, 3, QTableWidgetItem(grade_text))
        self._filter_rows()
        # Use one page scrollbar rather than trapping long activity lists in a small table.
        self._table.setMinimumHeight(min(700, 42 + sum(self._table.rowHeight(i) for i in range(self._table.rowCount()))))
        self._table.horizontalScrollBar().setValue(horizontal)
        self._table.verticalScrollBar().setValue(vertical)

    def _update_selection(self, *args):
        selected = [c for c in self._row_controls.values() if c['move'].isChecked()]
        missing = sum(not c['unit'].currentData() for c in selected)
        graded = sum(c['grade'].isChecked() for c in selected)
        self._selection_summary.setText(f'{len(selected)} selected · {graded} grade items · {missing} destinations to choose')
        all_present = bool(self._plan) and all(r.get('already_present') for r in self._plan['activities'])
        self._run_btn.setText('Finish review' if all_present else f'Transfer {len(selected)} activities')
        self._run_btn.setEnabled(self._review_wait is not None and not missing and (bool(selected) or all_present))
        for c in self._row_controls.values():
            c['unit'].setToolTip(c['unit'].currentText())
            if c['row'].get('name_check_required'):
                status = self._row_status(c['row'])[0]
            elif c['row'].get('duplicate_title'):
                status = 'Duplicate title · rename in Moodle, then start a new transfer'
            elif c['row'].get('already_present'):
                status = 'Already in Brightspace · grading unchanged'
            elif not c['move'].isChecked():
                status = 'Leave for later'
            else:
                status = 'Ready to transfer' if c['unit'].currentData() else 'Choose a destination'
            self._table.item(c['index'], 4).setText(status)

    def _apply_to_section(self):
        index = self._table.currentRow()
        current = next((c for c in self._row_controls.values() if c['index'] == index), None)
        if not current or not current['unit'].currentData():
            self._status_hint.setText('Select an activity row and choose its destination first.')
            return
        if not current['row']['moodle_section']:
            self._status_hint.setText('This activity has no named Moodle section. Choose destinations individually.')
            return
        for c in self._row_controls.values():
            if c['row']['moodle_section'] == current['row']['moodle_section'] and not any(c['row'].get(k) for k in ('already_present', 'duplicate_title', 'name_check_required')):
                c['unit'].setCurrentIndex(c['unit'].findData(current['unit'].currentData()))

    def _filter_rows(self):
        if not hasattr(self, '_display_rows'):
            return
        visible_groups = set()
        for index, group, attention in self._display_rows:
            visible = not self._only_attention.isChecked() or attention
            self._table.setRowHidden(index, not visible)
            if visible:
                visible_groups.add(group)
        for _, group, _ in self._display_rows:
            self._table.setRowHidden(group, group not in visible_groups)
        empty_filter = self._only_attention.isChecked() and not visible_groups
        self._table.setVisible(not empty_filter)
        self._filter_hint.setVisible(empty_filter)

    def _show_progress(self, data):
        self._run_data = data
        self._resume_id = data['id']
        if not data['activities'] or self._review_wait:
            return
        self._connection.hide()
        self._course_summary.setText('Moodle: ' + (data.get('moodle_course_name') or data['moodle_url']) + '\nBrightspace: ' + data['course_name'])
        self._course_summary.setToolTip(data['moodle_url'] + '\n' + data['bs_url'])
        self._course_summary.show()
        self._list_title.setText('Your activities')
        self._only_attention.show()
        self._render_rows(data['activities'])
        self._workspace.show()
        self._apply_section.hide()
        self._review_note.hide()
        complete = sum(r['insertion'] == 'confirmed' for r in data['activities'])
        total = len(data['activities'])
        self._selection_summary.setText(f'{complete} of {total} placed in Brightspace · {total - complete} remaining')
        self._progress_bar.setRange(0, total)
        self._progress_bar.setValue(complete)
        self._progress_bar.show()

    def _show_results(self, succeeded):
        if succeeded or (self._run_data and self._run_data.get('status') == 'complete'):
            self._furthest_stage = 3
        self._set_stage(3)
        self._review_wait = None
        if self._run_data:
            self._show_progress(self._run_data)
            summary = getattr(self, '_result_summary', {})
            remaining = sum(r['insertion'] != 'confirmed' for r in self._run_data['activities'])
            self._selection_summary.setText(f"{len(summary.get('h5p_inserted', []))} added this run · "
                f"{len(summary.get('h5p_already_present', []))} already present · {remaining} remaining")
            if not self._run_data['activities'] and self._plan:
                self._render_rows(self._plan['activities'])
                self._workspace.show()
                self._apply_section.hide()
                self._review_note.hide()
                self._only_attention.show()
                self._selection_summary.setText(f"{len(self._plan['activities'])} activities found · transfer not started")
        complete = bool(self._run_data and self._run_data.get('status') == 'complete')
        self._status_line.setText('Transfer complete' if complete or succeeded else 'Progress saved' if self._stop_flag[0] else 'Transfer needs attention')
        self._status_hint.setText('Open the activities in Brightspace to check playback. Existing pages keep their original grading.' if complete or succeeded else
            'Completed work is kept. Review the remaining activities to retry; existing pages and uploads are checked before anything is added.')
        self._run_btn.setText('Review saved transfer' if complete else 'Review remaining activities' if self._resume_id else 'Find activities')
        self._new_btn.show()
        self._change_btn.show()
        self._saved_card.hide()
        self._run_btn.setEnabled(True)
        if complete and any(r.get('grade_attention') for r in self._run_data['activities']):
            self._status_line.setText('Activities placed · check gradebook')
        if complete and not self._run_data['activities']:
            self._status_line.setText('No H5P activities found')
            self._status_hint.setText('This Moodle course has no H5P activities to transfer. You can start a new transfer for another course.')
            self._run_btn.setText('Check this course again')
