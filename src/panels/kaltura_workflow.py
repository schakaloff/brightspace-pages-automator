"""Four-phase Kaltura workspace; all browser and journal work stays off Qt."""
import asyncio
import copy
import queue
import threading
from urllib.parse import urlsplit, parse_qs

from PySide6.QtCore import Qt, QTimer, Signal, QUrl
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (QWidget, QFrame, QLabel, QPushButton, QVBoxLayout, QHBoxLayout,
    QLineEdit, QCheckBox, QScrollArea, QProgressBar, QComboBox)

import gui_styles
from gui_log import LogWidget
from gui_tool_icons import tool_pixmap
from kaltura_progress import KalturaRun, saved_runs, validate_courses, VERIFIED
from panels._shared import (friendly_error, TechnicalDetailsToggle,
                            BROWSER_GUIDE_LABEL, BROWSER_GUIDE_HINT)


def label(text, role='dim'):
    item = QLabel(text)
    item.setTextFormat(Qt.TextFormat.PlainText)
    item.setWordWrap(True)
    item.setMinimumWidth(0)
    item.setProperty('role', role)
    return item


def button(text, callback, secondary=True):
    item = QPushButton(text.replace('&', '&&'))
    item.setMinimumHeight(38)
    item.setAutoDefault(False)
    if secondary:
        item.setProperty('variant', 'secondary')
    item.clicked.connect(callback)
    return item


def surface(title=None):
    item = QFrame()
    item.setProperty('role', 'surface')
    box = QVBoxLayout(item)
    box.setContentsMargins(20, 18, 20, 18)
    box.setSpacing(10)
    if title:
        box.addWidget(label(title, 'section-heading'))
    return item, box


def progress_sentence(counts):
    """Say in plain words where an unfinished transfer stands and what to do next."""
    done = counts['created'] + counts['present']
    waiting = counts['remaining'] - counts['uncertain'] - counts['failed']
    parts = [f"{done} of {counts['total']} videos are in Brightspace."]
    if counts['uncertain']:
        parts.append(f"{counts['uncertain']} {'was' if counts['uncertain'] == 1 else 'were'} saved but not confirmed yet.")
    if counts['failed']:
        parts.append(f"{counts['failed']} couldn't be created.")
    if waiting > 0:
        parts.append(f"{waiting} {'hasn’t' if waiting == 1 else 'haven’t'} been started.")
    parts.append('Choose Check & continue to confirm saved pages and finish the rest. Saved pages are not created again.')
    return ' '.join(parts)


class KalturaPanel(QWidget):
    back_requested = Signal()

    def __init__(self, main_window, parent=None):
        super().__init__(parent)
        self._mw = main_window
        self._log_queue = queue.Queue()
        self._drafts = queue.Queue()
        self._stop = threading.Event()
        self._task_active = False
        self._stage = 0
        self._run_data = None
        self._resume_id = None
        self._review_answer = None
        self._worker_loop = None
        self._show_callback = None
        self._back_pending = False
        self._rendering = False
        self._last_error = None
        self._progress_directory = None
        self._build()
        cfg = self._mw.load_config() if hasattr(self._mw, 'load_config') else {}
        self._moodle_url.setText(cfg.get('kaltura_moodle_url', ''))
        self._bs_url.setText(cfg.get('kaltura_bs_url', ''))
        self._watch.setChecked(cfg.get('kaltura_watch_browser', True))
        self._new_transfer()
        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._poll_log)
        self._poll_timer.start(100)

    def _build(self):
        from panels.kaltura_panel import KalturaResultsWidget
        self.setObjectName('kaltura_workspace')
        self.setProperty('design', 'modern')
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 22, 24, 18)
        outer.setSpacing(12)
        top = QHBoxLayout()
        top.addWidget(label('Kaltura transfer', 'page-heading'), 1)
        outer.addLayout(top)
        outer.addWidget(label('Create Brightspace pages for Moodle videos. Review every destination before making changes.'))
        steps = QHBoxLayout()
        self._steps, self._step_icons = [], []
        for title, icon in (('Connect', 'link'), ('Review', 'clipboard-list'), ('Create', 'movie'), ('Results', 'circle-check')):
            item = QFrame()
            item.setProperty('role', 'transfer-step')
            item.setFixedHeight(64)
            item.setMinimumWidth(0)
            box = QVBoxLayout(item)
            box.setContentsMargins(8, 6, 8, 6)
            box.setSpacing(4)
            image = QLabel()
            box.addWidget(image, 0, Qt.AlignmentFlag.AlignHCenter)
            box.addWidget(label(title, 'h5p-step-title'), 0, Qt.AlignmentFlag.AlignHCenter)
            steps.addWidget(item, 1)
            self._steps.append(item)
            self._step_icons.append((image, icon))
        outer.addLayout(steps)
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        self._content = QVBoxLayout(body)
        self._content.setContentsMargins(0, 0, 8, 0)
        self._content.setSpacing(14)
        self._scroll.setWidget(body)
        outer.addWidget(self._scroll, 1)

        self._setup, box = surface('Your courses')
        self._moodle_url, self._bs_url = QLineEdit(), QLineEdit()
        # Retain public field aliases used by the app's course handoff.
        self._moodle_entry, self._bs_entry = self._moodle_url, self._bs_url
        for title, field, hint in (
            ('From Moodle', self._moodle_url, 'https://mymoodle.okanagan.bc.ca/course/view.php?id=…'),
            ('To Brightspace', self._bs_url, 'https://learn.okanagancollege.ca/d2l/home/…')):
            caption = label(title, 'field-label')
            caption.setBuddy(field)
            field.setAccessibleName(title + ' course URL')
            field.setPlaceholderText(hint)
            box.addWidget(caption)
            box.addWidget(field)
        self._watch = QCheckBox(BROWSER_GUIDE_LABEL)
        self._watch.setToolTip(BROWSER_GUIDE_HINT)
        box.addWidget(self._watch)
        box.addWidget(label('Connect reads your courses and checks sign-in. Course content changes start only after you review and approve.', 'hint'))
        self._content.addWidget(self._setup)

        self._saved_card, saved = surface()
        saved_heading = QHBoxLayout()
        saved_heading.addWidget(label('Saved transfers', 'section-heading'))
        saved_heading.addStretch()
        self._saved_count = label('', 'status')
        saved_heading.addWidget(self._saved_count)
        saved.addLayout(saved_heading)
        self._saved_hint = label('Find a course, resume a transfer, or remove old scans.')
        saved.addWidget(self._saved_hint)
        self._saved_btn = button('Browse saved transfers', self._browse_saved)
        saved.addWidget(self._saved_btn)
        self._content.addWidget(self._saved_card)

        self._course_card, box = surface()
        self._course_title = label('', 'section-heading')
        self._course_route, self._course_stamp = label(''), label('', 'hint')
        box.addWidget(self._course_title)
        box.addWidget(self._course_route)
        stamp_row = QHBoxLayout()
        stamp_row.addWidget(self._course_stamp, 1)
        copy_id = button('Copy ID', self._copy_id)
        copy_id.setProperty('variant', 'ghost')
        copy_id.setMinimumHeight(24)
        stamp_row.addWidget(copy_id)
        box.addLayout(stamp_row)
        self._content.addWidget(self._course_card)

        self._attention, box = surface()
        self._status_line, self._status_hint = label('', 'section-heading'), label('')
        box.addWidget(self._status_line)
        box.addWidget(self._status_hint)
        self._connections = QWidget()
        connections = QVBoxLayout(self._connections)
        connections.setContentsMargins(0, 0, 0, 0)
        self._services = {}
        for name in ('Moodle', 'Brightspace', 'Kaltura'):
            row = QHBoxLayout()
            row.addWidget(label(name, 'field-label'), 1)
            value = label('Not checked', 'status')
            self._services[name] = value
            row.addWidget(value)
            connections.addLayout(row)
        box.addWidget(self._connections)
        self._browser_row = QWidget()
        brow = QHBoxLayout(self._browser_row)
        brow.setContentsMargins(0, 0, 0, 0)
        self._browser_service = QComboBox()
        self._browser_service.addItems(['Brightspace', 'Kaltura', 'Moodle'])
        self._browser_service.setAccessibleName('Automation browser to show')
        brow.addWidget(self._browser_service)
        brow.addWidget(button('Show browser', self._show_browser), 1)
        box.addWidget(self._browser_row)
        self._progress_bar = QProgressBar()
        self._progress_bar.setTextVisible(False)
        self._progress_bar.setAccessibleName('Verified Kaltura placements')
        box.addWidget(self._progress_bar)
        self._content.addWidget(self._attention)

        self._review_card, review = surface('Review videos and destinations')
        self._list_heading = review.itemAt(0).widget()
        self._review_summary = label('')
        review.addWidget(self._review_summary)
        self._stats = QWidget()
        stats = QHBoxLayout(self._stats)
        stats.setContentsMargins(0, 4, 0, 4)
        stats.setSpacing(8)
        self._stat_values = {}
        for key, title in (('selected', 'Videos selected'), ('pages', 'Pages to create'),
                           ('units', 'New units'), ('attention', 'Need attention')):
            tile = QFrame()
            tile.setProperty('role', 'stat-tile')
            tile.setMinimumWidth(0)
            box = QVBoxLayout(tile)
            box.setContentsMargins(12, 8, 12, 8)
            box.setSpacing(0)
            box.addWidget(label(title, 'hint'))
            value = label('0', 'stat-value')
            box.addWidget(value)
            self._stat_values[key] = value
            stats.addWidget(tile, 1)
        review.addWidget(self._stats)
        search_row = QHBoxLayout()
        self._search = QLineEdit()
        self._search.setPlaceholderText('Search videos, source sections or entry IDs…')
        self._search.setAccessibleName('Search Kaltura placements')
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._filter_groups)
        search_row.addWidget(self._search, 1)
        for text, expanded in (('Expand all', True), ('Collapse all', False)):
            item = button(text, lambda checked=False, e=expanded: self._results.set_all_expanded(e))
            item.setProperty('variant', 'ghost')
            search_row.addWidget(item)
        review.addLayout(search_row)
        self._results = KalturaResultsWidget()
        self._results.embed_inline()
        self._results.selected_count_changed.connect(self._choices_changed)
        review.addWidget(self._results)
        self._partial_ack = QCheckBox('I understand the source scan is incomplete; apply only the reviewed placements')
        self._partial_ack.toggled.connect(self._update_approval)
        review.addWidget(self._partial_ack)
        self._change_note = label('', 'hint')
        review.addWidget(self._change_note)
        self._content.addWidget(self._review_card)
        self._issues_card, self._issues_layout = surface('Needs attention')
        self._content.insertWidget(self._content.indexOf(self._review_card), self._issues_card)
        self._details_toggle = TechnicalDetailsToggle()
        self._log = LogWidget()
        self._log.setMinimumHeight(150)
        self._log.hide()
        self._details_toggle.toggled.connect(self._toggle_details)
        self._content.addWidget(self._details_toggle, 0, Qt.AlignmentFlag.AlignLeft)
        self._content.addWidget(self._log)
        self._content.addStretch()
        self._footer_hint = label('', 'hint')
        outer.addWidget(self._footer_hint)
        actions = QHBoxLayout()
        self._back_btn = button('Back', self._go_back)
        self._stop_btn = button('Stop & save', self._stop_run)
        self._new_btn = button('New transfer', self._new_transfer)
        self._retry_scan = button('Retry source scan', lambda: self._start_run(self._resume_id, rescan=True))
        for item in (self._back_btn, self._stop_btn, self._new_btn):
            actions.addWidget(item)
        actions.addStretch()
        self._run_btn = button('Connect & find videos', self._primary, False)
        actions.addWidget(self._run_btn)
        outer.addLayout(actions)
        # Retry is in the attention region so the action footer stays usable on laptops.
        self._issues_layout.addWidget(self._retry_scan)
        self.refresh_theme()

    def refresh_theme(self):
        colors = gui_styles.modern_colors()
        self._details_toggle.refresh_theme()
        for image, icon in self._step_icons:
            image.setPixmap(tool_pixmap(icon, 20, colors['muted']))
        self._results.refresh_theme()
        self._log.set_log_colors({key: colors['muted'] if key in ('dim', 'detail') else
            colors['error'] if key == 'error' else colors['success'] if key == 'success' else colors['text']
            for key in ('dim', 'detail', 'info', 'warning', 'success', 'error')})

    def apply_theme(self, colors):
        self.refresh_theme()

    def _toggle_details(self, shown):
        self._details_toggle.reveal_log(self._log, self._scroll, shown)

    def _set_stage(self, stage):
        self._stage = stage
        for index, item in enumerate(self._steps):
            item.setProperty('state', 'review' if index == stage else 'created' if index < stage and index < 2 else '')
            item.style().unpolish(item)
            item.style().polish(item)
            for child in item.findChildren(QLabel):
                child.style().unpolish(child)
                child.style().polish(child)
        self._setup.setVisible(stage == 0 and not self._task_active)
        self._saved_card.setVisible(stage == 0 and not self._task_active)
        self._course_card.setVisible(bool(self._run_data))
        self._connections.setVisible(stage == 0 and self._task_active)
        self._attention.setVisible(stage != 1)
        self._progress_bar.setVisible(stage in (2, 3) and bool(self._run_data))
        self._review_card.setVisible(stage in (1, 2, 3) and bool(self._run_data and self._run_data['rows']))
        self._stats.setVisible(stage == 1)
        self._partial_ack.setVisible(stage == 1 and not self._run_data['scan_complete'] if self._run_data else False)
        self._browser_row.setVisible(stage in (0, 2) and self._task_active and self._show_callback is not None)
        self._list_heading.setText('Video outcomes' if stage == 3 else 'Review videos and destinations')
        self._stop_btn.setVisible(self._task_active)
        self._new_btn.setVisible(not self._task_active and stage == 3)
        self._saved_btn.setEnabled(not self._task_active)

    def _new_transfer(self):
        if self._task_active:
            return
        self._run_data = None
        self._resume_id = None
        self._show_callback = None
        self._results.clear()
        self._set_stage(0)
        self._issues_card.hide()
        self._run_btn.setText('Connect && find videos')
        self._run_btn.setEnabled(True)
        self._back_btn.setEnabled(True)
        self._status_line.setText('Ready to connect')
        self._status_hint.setText('Enter the Moodle and Brightspace course URLs. Sign-in prompts will appear here when needed.')
        self._footer_hint.setText('Finding videos reads the courses. Review and approve before creating pages.')
        self._refresh_saved()

    def _refresh_saved(self):
        runs = saved_runs(self._progress_directory, include_removed=True)
        count = sum(not r.data.get('removed_at') for r in runs)
        self._saved_count.setText(f'{count} saved')
        self._saved_hint.setText('Find a course, resume a transfer, or remove old scans.' if count else
            'Your saved list is clear. You can restore removed entries in Browse saved transfers.' if runs else
            'No saved transfers yet. New scans will appear here.')
        self._saved_btn.setEnabled(not self._task_active)

    def _browse_saved(self):
        if self._task_active:
            return
        from panels.kaltura_saved_transfers import SavedKalturaTransfersDialog
        dialog = SavedKalturaTransfersDialog(self._progress_directory, self)
        accepted = dialog.exec()
        id_ = dialog.selected_run_id
        dialog.deleteLater()
        self._refresh_saved()
        if accepted and id_:
            run = KalturaRun.load(id_, self._progress_directory)
            self._moodle_url.setText(run.data['moodle_url'])
            self._bs_url.setText(run.data['bs_url'])
            if run.data['status'] == 'complete':
                self._run_data, self._resume_id = run.data, id_
                self._show_results()
            else:
                self._start_run(id_)

    def _copy_id(self):
        if self._run_data:
            QGuiApplication.clipboard().setText(self._run_data['id'])

    def save_state(self):
        if hasattr(self._mw, 'save_config'):
            data = dict(kaltura_watch_browser=self._watch.isChecked())
            try:
                bs, moodle, _ = validate_courses(self._bs_url.text().strip(), self._moodle_url.text().strip())
                data.update(kaltura_bs_url=bs, kaltura_moodle_url=moodle)
            except ValueError:
                pass  # Keep the last valid course pair rather than storing login URLs.
            self._mw.save_config(data)

    def _start_run(self, resume_id=None, *, rescan=False):
        if self._task_active:
            return
        try:
            bs, moodle, _ = validate_courses(self._bs_url.text().strip(), self._moodle_url.text().strip())
            if resume_id:
                saved = KalturaRun.load(resume_id, self._progress_directory)
                if (bs, moodle) != (saved.data['bs_url'], saved.data['moodle_url']):
                    raise ValueError('The saved transfer belongs to a different course pair.')
        except (ValueError, OSError) as exc:
            self._status_line.setText('Check the course URLs')
            self._status_hint.setText(str(exc))
            return
        self.save_state()
        self._task_active = True
        self._stop = threading.Event()
        self._drafts = queue.Queue()
        self._review_answer = None
        self._last_error = None
        self._back_pending = False
        self._show_callback = None
        self._resume_id = resume_id
        self._log.clear_log()
        self._partial_ack.setChecked(False)
        self._search.clear()
        for value in self._services.values():
            value.setText('Not checked')
            value.setProperty('state', '')
            value.style().unpolish(value)
            value.style().polish(value)
        self._set_stage(0)
        self._issues_card.hide()
        self._status_line.setText('Connecting your courses and Kaltura')
        self._status_hint.setText('Complete sign-in or MFA in the browser if prompted. No course content is changed before approval.')
        self._footer_hint.setText('Stop saves progress and closes the browsers. During a write, wait for the current save check.')
        self._run_btn.setEnabled(False)
        self._run_btn.setText('Connecting…')
        self._stop_btn.setText('Stop && save')
        self._stop_btn.setEnabled(True)
        self._new_btn.hide()
        credentials = {key: getattr(self._mw, key, '') for key in (
            'bs_username', 'bs_password', 'moodle_username', 'moodle_password',
            'sso_email', 'sso_password', 'kmc_username', 'kmc_password')}
        stop, events, drafts = self._stop, self._log_queue, self._drafts
        watch, directory = self._watch.isChecked(), self._progress_directory

        async def review(plan, save_draft):
            events.put(('review', plan))
            while True:
                latest = None
                while not drafts.empty():
                    latest = drafts.get_nowait()
                if latest is not None:
                    save_draft(latest)
                if stop.is_set():
                    return None
                if self._review_answer is not None:
                    return self._review_answer
                await asyncio.sleep(0.1)

        def worker():
            async def run():
                from kaltura_runner import run_kaltura
                self._worker_loop = asyncio.get_running_loop()
                await run_kaltura(bs, moodle, stop=stop, review=review,
                    emit=lambda data: events.put(('state', data)), log=lambda msg, tag='info': events.put(('log', (msg, tag))),
                    credentials=credentials, resume_id=resume_id, directory=directory, watch=watch, rescan=rescan)
            try:
                asyncio.run(run())
            except Exception as exc:
                message, detail = friendly_error(exc)
                events.put(('error', message))
                events.put(('log', (detail, 'error')))
            finally:
                self._worker_loop = None
                events.put(('done', None))
        self._worker_thread = threading.Thread(target=worker, daemon=True)
        self._worker_thread.start()

    def _choices(self):
        from panels.kaltura_panel import NEW_MODULE_SENTINEL
        result = {}
        for group in self._results._groups:
            value = group.combo.currentData()
            destination = ({'new': value[1]} if isinstance(value, tuple) and value[0] == NEW_MODULE_SENTINEL else
                           {'id': str(value)} if value not in (None, NEW_MODULE_SENTINEL) else None)
            for check, entry in group.video_checkboxes:
                result[entry['key']] = dict(selected=check.isChecked(), destination=destination)
        return result

    def _choices_changed(self, *args):
        if self._rendering or self._stage != 1:
            return
        self._drafts.put(self._choices())
        self._update_approval()

    def _update_approval(self, *args):
        if not self._run_data or self._stage != 1:
            return
        choices = self._choices()
        draft = KalturaRun('', copy.deepcopy(self._run_data))
        # Validation must be identical in the UI and runner, without writing a draft file.
        draft.save = lambda **kwargs: None
        try:
            draft.approve(choices, partial_ack=self._partial_ack.isChecked())
            error = ''
        except ValueError as exc:
            error = str(exc)
        selected = [r for r in self._run_data['rows'] if choices.get(r['key'], {}).get('selected')]
        new = sum(r['state'] not in VERIFIED for r in selected)
        units = {choices[r['key']]['destination']['new'] for r in selected
                 if (choices[r['key']].get('destination') or {}).get('new')}
        attention = sum(r['state'] in ('needs_check', 'pending', 'failed') or
                        (choices.get(r['key'], {}).get('selected') and not choices[r['key']].get('destination'))
                        for r in self._run_data['rows'])
        for key, value in (('selected', f"{len(selected)} of {len(choices)}"), ('pages', new),
                           ('units', len(units)), ('attention', attention)):
            self._stat_values[key].setText(str(value))
        page_count = f"{new} {'page' if new == 1 else 'pages'}"
        unit_count = f"{len(units)} new {'unit' if len(units) == 1 else 'units'}"
        self._run_btn.setText(f'Approve && create {page_count}' if new else 'Approve review')
        self._run_btn.setEnabled(not error and self._task_active and not self._stop.is_set())
        self._change_note.setText(error or f"Create {page_count} and {unit_count} in Brightspace {self._run_data['course_id']}. Already present pages are kept. No automatic undo.")
        self._footer_hint.setText(error or f'{len(selected)} selected · {new} to create · {unit_count} · {len(choices) - len(selected)} excluded · no automatic undo')

    def _render_rows(self, editable):
        from panels.kaltura_panel import NEW_MODULE_SENTINEL
        self._rendering = True
        try:
            self._results.set_entries(copy.deepcopy(self._run_data['rows']))
            self._results.set_modules(self._run_data['modules'])
            self._results.set_message('', False)
            for group in self._results._groups:
                entries = [r for _, r in group.video_checkboxes]
                destinations = [r['destination'] for r in entries if r.get('destination')]
                # Group mapping is the existing interaction. A saved verified
                # destination remains fixed; new placement uses a new plan.
                destination = next((r['destination'] for r in entries if r['state'] in VERIFIED),
                                   destinations[0] if destinations else None)
                group.combo.blockSignals(True)
                if destination and 'id' in destination:
                    index = group.combo.findData(str(destination['id']))
                    group.combo.setCurrentIndex(max(0, index))
                elif destination and 'new' in destination:
                    value = (NEW_MODULE_SENTINEL, destination['new'])
                    group.combo.insertItem(group.combo.count() - 1, 'New: ' + destination['new'], value)
                    group.combo.setCurrentIndex(group.combo.findData(value))
                group.combo.blockSignals(False)
                self._results._last_combo_index[group.combo] = group.combo.currentIndex()
                group.combo.setEnabled(editable and not any(r['state'] in VERIFIED for r in entries))
                group.group_checkbox.setEnabled(editable)
                for check, row in group.video_checkboxes:
                    check.setChecked(row['selected'])
                    check.setEnabled(editable and row['state'] not in VERIFIED)
                    check.setToolTip('\n'.join([row.get('problem') or row['name'],
                        *[s.get('url', s.get('name', '')) for s in row.get('sources', [])]]))
                group._sync_group_state()
                group.set_needs_review(any(r['state'] in ('needs_check', 'pending', 'failed') for r in entries) or not destination,
                                       no_match=not destination)
                group.expand_btn.setChecked(True)
        finally:
            self._rendering = False
        self._filter_groups()

    def _filter_groups(self, *args):
        query = self._search.text().strip().casefold()
        for group in self._results._groups:
            text = ' '.join(group.section_name + ' ' + row['name'] + ' ' + row['entry_id']
                            for _, row in group.video_checkboxes).casefold()
            group.setVisible(query in text)

    def _show_review(self, data):
        if self._stop.is_set():
            return
        self._run_data = data
        self._set_stage(1)
        self._render_rows(True)
        self._status_line.setText('Review videos and destinations')
        self._status_hint.setText('Repeated media in the same source section is grouped. Separate sections remain separate placement decisions. Review source locations in each video’s tooltip.')
        self._review_summary.setText(f"{len({r['entry_id'] for r in data['rows']})} unique videos · {len(data['rows'])} source-section placements" +
            (' · Source scan incomplete: review the unread pages above or retry the scan.' if not data['scan_complete'] else ''))
        self._render_issues()
        self._update_approval()

    def _render_issues(self):
        while self._issues_layout.count() > 1:
            item = self._issues_layout.takeAt(1)
            if item.widget() and item.widget() is not self._retry_scan:
                item.widget().hide()
                item.widget().deleteLater()
        # The retry control may have been detached in the cleanup above.
        self._issues_layout.addWidget(self._retry_scan)
        d = self._run_data or {}
        issues = list(d.get('scan_issues', []))
        for issue in issues:
            self._issues_layout.addWidget(label(issue['reason'] + '\n' + issue.get('url', ''), 'hint'))
        from kaltura_verification import INTERRUPTED_CHECK, _navigation_error
        for row in d.get('rows', []):
            if row.get('problem'):
                # Older saved transfers still hold the raw browser error.
                problem = INTERRUPTED_CHECK if _navigation_error(row['problem']) else row['problem']
                line = QHBoxLayout()
                line.addWidget(label(row['name'] + '\n' + problem, 'hint'), 1)
                destination = row.get('destination') or {}
                if destination.get('id'):
                    item = button('Open in Brightspace', lambda checked=False, id_=destination['id']: self._open_destination(id_))
                    item.setProperty('variant', 'ghost')
                    item.setMinimumHeight(30)
                    line.addWidget(item, 0, Qt.AlignmentFlag.AlignVCenter)
                holder = QWidget()
                holder.setLayout(line)
                line.setContentsMargins(0, 0, 0, 0)
                self._issues_layout.addWidget(holder)
        if d.get('error'):
            self._issues_layout.addWidget(label(d['error'], 'hint'))
        self._retry_scan.setVisible(bool(issues) and not self._task_active)
        self._issues_card.setVisible(bool(issues or d.get('error') or any(r.get('problem') for r in d.get('rows', []))))

    def _open_destination(self, module_id):
        parts = urlsplit(self._run_data['bs_url'])
        QDesktopServices.openUrl(QUrl(f'{parts.scheme}://{parts.netloc}/d2l/le/lessons/{self._run_data["course_id"]}/units/{module_id}'))

    def _primary(self):
        if self._stage == 1 and self._task_active:
            if not self._run_btn.isEnabled():
                return
            self._review_answer = dict(choices=self._choices(), partial_ack=self._partial_ack.isChecked())
            self._run_btn.setEnabled(False)
            self._run_btn.setText('Applying approved plan…')
            # Freeze the reviewed snapshot immediately, before the worker consumes it.
            self._rendering = True
            for group in self._results._groups:
                group.combo.setEnabled(False)
                group.group_checkbox.setEnabled(False)
                for check, _ in group.video_checkboxes:
                    check.setEnabled(False)
            self._rendering = False
        elif not self._task_active:
            self._start_run(self._resume_id if self._stage == 3 else None)

    def _stop_run(self):
        if not self._task_active:
            return
        if self._stage == 1:
            self._drafts.put(self._choices())
        self._stop.set()
        self._stop_btn.setEnabled(False)
        self._stop_btn.setText('Stopping…')
        self._run_btn.setEnabled(False)
        self._status_line.setText('Stopping and saving progress…')
        self._status_hint.setText('The current save is checked before closing. An outcome that cannot be verified stays “Needs checking.”')

    def _go_back(self):
        if self._task_active:
            self._back_pending = True
            self._back_btn.setEnabled(False)
            self._stop_run()
        elif self._stage:
            self._new_transfer()
        else:
            self.save_state()
            self.back_requested.emit()

    def _show_browser(self):
        callback, loop = self._show_callback, self._worker_loop
        service = self._browser_service.currentText()
        if callback and loop and loop.is_running():
            async def show():
                try:
                    await callback(service)
                except Exception:
                    pass  # Browser visibility is optional and never changes a transfer.
            loop.call_soon_threadsafe(lambda: asyncio.create_task(show()))

    def _update_progress(self, data):
        self._run_data, self._resume_id = data, data['id']
        self._course_title.setText(data['course_name'])
        source = parse_qs(urlsplit(data['moodle_url']).query).get('id', ['?'])[0]
        self._course_route.setText(f"Moodle {source} → Brightspace {data['course_id']}")
        from kaltura_progress import describe_run
        info = describe_run(KalturaRun('', data))
        self._course_stamp.setText(f"Transfer {data['id'][:6]} · Last saved {info['date']} · {info['time']}")
        self._course_stamp.setToolTip('Transfer ' + data['id'] + '\nCreated ' + data['created'])
        self._course_card.show()
        counts = KalturaRun('', data).counts()
        self._progress_bar.setRange(0, max(1, counts['total']))
        self._progress_bar.setValue(counts['created'] + counts['present'])
        if self._stage == 2:
            self._footer_hint.setText(f"{counts['created'] + counts['present']} of {counts['total']} videos in Brightspace")
            by_key = {r['key']: r for r in data['rows']}
            for group in self._results._groups:
                for check, entry in group.video_checkboxes:
                    row = by_key.get(entry['key'])
                    if row:
                        group.set_row_state(row)
                        check.setToolTip(row.get('problem') or row['name'])
                destination = next((by_key[e['key']].get('destination') for _, e in group.video_checkboxes if by_key.get(e['key'])), None)
                if destination and destination.get('id'):
                    id_ = str(destination['id'])
                    index = group.combo.findData(id_)
                    if index < 0:
                        title = next((m['title'] for m in data['modules'] if str(m['id']) == id_), id_)
                        group.combo.addItem(title, id_)
                        index = group.combo.findData(id_)
                    group.combo.setCurrentIndex(index)

    def _show_results(self):
        self._set_stage(3)
        if not self._run_data:
            self._status_line.setText('Transfer could not start')
            if self._last_error:
                self._status_hint.setText(self._last_error)
            self._run_btn.setText('Try connecting again')
            self._run_btn.setEnabled(True)
            return
        self._update_progress(self._run_data)
        self._render_rows(False)
        counts = KalturaRun('', self._run_data).counts()
        complete = self._run_data['status'] == 'complete'
        self._status_line.setText('Transfer complete' if complete else 'Progress saved' if self._stop.is_set() else 'Transfer needs attention')
        self._status_hint.setText('Complete scan: no Kaltura videos found.' if complete and not self._run_data['rows'] else
            'All approved videos are in Brightspace. Pages that were already there were kept.' if complete else
            progress_sentence(counts))
        # Zero counts say nothing, so only non-empty groups are listed.
        self._footer_hint.setText(' · '.join(text for n, text in (
            (counts['created'], f"{counts['created']} created and verified"), (counts['present'], f"{counts['present']} already there"),
            (counts['uncertain'], f"{counts['uncertain']} need checking"), (counts['failed'], f"{counts['failed']} failed"),
            (counts['excluded'], f"{counts['excluded']} skipped")) if n))
        self._run_btn.setText('Check && continue')
        self._run_btn.setEnabled(not complete and not self._task_active)
        self._render_issues()
        if self._last_error:
            self._status_hint.setText(self._last_error)

    def _poll_log(self):
        try:
            while True:
                kind, payload = self._log_queue.get_nowait()
                if kind == 'log':
                    self._log.append_log(*payload)
                elif kind == 'error':
                    self._last_error = payload
                    self._status_hint.setText(payload)
                elif kind == 'review':
                    self._show_review(payload)
                elif kind == 'state':
                    stage = payload['stage']
                    if payload.get('run'):
                        self._update_progress(payload['run'])
                    if stage == 'browser_control':
                        self._show_callback = payload['show']
                        self._browser_row.show()
                    elif stage == 'connection':
                        item = self._services[payload['service']]
                        item.setText(payload['state'])
                        item.setProperty('state', 'created')
                        item.style().unpolish(item)
                        item.style().polish(item)
                    elif stage == 'attention' and not self._stop.is_set():
                        self._status_line.setText(payload['message'])
                        item = self._services[payload['service']]
                        item.setText('Sign-in may be needed')
                        item.setProperty('state', 'review')
                        item.style().unpolish(item)
                        item.style().polish(item)
                        self._browser_service.setCurrentText(payload['service'])
                    elif stage == 'create':
                        self._set_stage(2)
                        self._run_btn.setText('Creating and checking…')
                        self._run_btn.setEnabled(False)
                        self._render_rows(False)
                    elif stage == 'results':
                        self._show_results()
                    elif stage in ('status', 'scan', 'connect') and not self._stop.is_set():
                        self._status_line.setText(payload['message'])
                        if payload.get('service'):
                            self._browser_service.setCurrentText(payload['service'])
                        if payload.get('waiting'):
                            self._status_hint.setText('Use the selected browser to finish sign-in. Automation continues when the service is ready.')
                        elif stage == 'scan':
                            self._status_hint.setText('Reading source content. Book chapter totals can grow during discovery; unread pages will be listed for review.')
                elif kind == 'done':
                    self._task_active = False
                    self._show_callback = None
                    self._show_results()
                    self._refresh_saved()
                    if self._back_pending:
                        self._back_pending = False
                        self._new_transfer()
                        self._status_hint.setText('Progress saved. Open Saved transfers to continue, or choose another course pair.')
        except queue.Empty:
            pass
