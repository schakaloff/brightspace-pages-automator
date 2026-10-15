"""Media's standalone SCORM package transfer tool."""
import asyncio
import queue
import threading

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QGridLayout, QLabel, QLineEdit,
    QPushButton, QCheckBox, QTableWidget, QTableWidgetItem, QComboBox, QHeaderView, QMessageBox)

from gui_log import LogWidget
from panels._shared import _section_header, _form_label, friendly_error


class SCORMPanel(QWidget):
    def __init__(self, main_window, parent=None):
        super().__init__(parent)
        self._mw = main_window
        self._busy = False
        self._loop = self._task = None
        self._ready_event = None
        self._cancel_requested = False
        self._queue = queue.Queue()
        self._run = None
        self._review_choices = {}
        self._transferring_keys = set()
        self._modules = []
        self._directory = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 28, 32, 20)
        layout.addWidget(_section_header('SCORM packages'))
        intro = QLabel('Download the original SCORM ZIPs from Moodle, then add them to selected Brightspace units. '
                       'Transfers use no grade item and keep the uploaded version fixed.')
        intro.setWordWrap(True)
        intro.setProperty('role', 'dim')
        layout.addWidget(intro)
        cfg = main_window.load_config() if hasattr(main_window, 'load_config') else {}
        self._bs_entry, self._moodle_entry = QLineEdit(), QLineEdit()
        for title, field, key, hint in (
            ('BRIGHTSPACE COURSE URL', self._bs_entry, 'scorm_bs_url', 'https://learn.okanagancollege.ca/d2l/le/lessons/…'),
            ('MOODLE COURSE URL', self._moodle_entry, 'scorm_moodle_url', 'https://mymoodle.okanagan.bc.ca/course/view.php?id=…')):
            layout.addWidget(_form_label(title))
            field.setPlaceholderText(hint)
            field.setText(cfg.get(key, ''))
            field.setMinimumHeight(36)
            field.textChanged.connect(self._invalidate)
            layout.addWidget(field)
        self._refresh = QCheckBox('Download fresh copies instead of reusing cached ZIPs')
        layout.addWidget(self._refresh)
        buttons = QGridLayout()
        self._prepare_btn = QPushButton('1. Download && review')
        self._prepare_btn.clicked.connect(self._prepare)
        buttons.addWidget(self._prepare_btn, 0, 0)
        self._transfer_one = QPushButton('Transfer first selected')
        self._transfer_one.clicked.connect(lambda: self._transfer(one=True))
        buttons.addWidget(self._transfer_one, 1, 0)
        self._transfer_all = QPushButton('2. Transfer selected')
        self._transfer_all.clicked.connect(lambda: self._transfer(one=False))
        buttons.addWidget(self._transfer_all, 1, 1)
        self._stop = QPushButton('Stop')
        self._stop.setProperty('variant', 'secondary')
        self._stop.clicked.connect(self._stop_work)
        self._stop.hide()
        buttons.addWidget(self._stop, 0, 1)
        layout.addLayout(buttons)
        self._ready = QPushButton('Ready — scan Moodle')
        self._ready.clicked.connect(lambda: (self._ready.hide(), self._ready_event.set()))
        self._ready.hide()
        layout.addWidget(self._ready)
        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(['Transfer', 'Moodle activity', 'Brightspace unit', 'Status'])
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self._table.setColumnWidth(0, 64)
        self._table.setColumnWidth(3, 155)
        self._table.setMinimumHeight(135)
        layout.addWidget(self._table, 1)
        self._recover = QPushButton('Allow retry after checking an uncertain result…')
        self._recover.setProperty('variant', 'secondary')
        self._recover.clicked.connect(self._recover_pending)
        self._recover.hide()
        layout.addWidget(self._recover)
        self._log = LogWidget()
        layout.addWidget(self._log, 1)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._poll)
        self._timer.start(100)
        self._set_busy(False)

    def save_state(self):
        if hasattr(self._mw, 'save_config'):
            self._mw.save_config({'scorm_bs_url': self._bs_entry.text().strip(),
                                  'scorm_moodle_url': self._moodle_entry.text().strip()})

    def _invalidate(self):
        self._run = None
        self._review_choices.clear()
        if hasattr(self, '_table'):
            self._table.setRowCount(0)
            self._set_busy(False)

    def _set_busy(self, busy):
        self._busy = busy
        for control in (self._bs_entry, self._moodle_entry, self._refresh, self._prepare_btn, self._table):
            control.setEnabled(not busy)
        for button in (self._transfer_one, self._transfer_all):
            button.setEnabled(not busy and self._run is not None)
        self._stop.setVisible(busy)
        self._stop.setEnabled(busy)
        if hasattr(self, '_recover'):
            self._recover.setVisible(not busy and self._run is not None and any(
                r.get('upload') == 'pending' or r.get('insertion') == 'pending' for r in self._run.data['activities']))

    def _credentials(self):
        return {key: getattr(self._mw, key, '') for key in
                ('bs_username', 'bs_password', 'sso_email', 'sso_password', 'moodle_username', 'moodle_password')}

    def _recover_pending(self):
        if self._busy or self._run is None: return
        rows = [r for r in self._run.data['activities'] if r.get('upload') == 'pending' or r.get('insertion') == 'pending']
        if not rows: return
        details = '\n'.join(f"• {r['name']} → {r.get('module_title', '')}" for r in rows)
        answer = QMessageBox.question(self, 'Check uncertain SCORM results first',
            'Check these packages in the Brightspace SCORM library and their destination units:\n\n' + details +
            '\n\nIf an upload is still processing, wait. If a topic already exists, run Download & review again. '
            'Only allow retry after confirming that the missing operation did not finish. Allow retry?',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes: return
        from scorm_packages import SCORMRun
        from transfer_runner import PlanLock
        try:
            run = SCORMRun.open(self._run.data['bs_url'], self._run.data['moodle_url'], self._directory)
            with PlanLock(run.path):
                for row in run.data['activities']:
                    for stage in ('upload', 'insertion'):
                        if row.get(stage) == 'pending': row[stage] = 'ready'
                run.save()
            self._run = run
            self._show_rows()
            self._set_busy(False)
        except Exception as exc:
            self._log.append_log(str(exc), 'error')

    def _prepare(self):
        from scorm_runner import prepare_scorm
        if self._busy: return
        self._review_choices.clear()
        self._ready_event = threading.Event()
        urls = self._bs_entry.text().strip(), self._moodle_entry.text().strip()
        credentials, directory, refresh = self._credentials(), self._directory, self._refresh.isChecked()
        self.save_state()
        self._log.clear_log()
        self._start(lambda: prepare_scorm(*urls, self._log_message,
            ready_event=self._ready_event, on_waiting=lambda: self._queue.put(('waiting', None)),
            credentials=credentials, directory=directory, refresh=refresh))

    def _transfer(self, one=False):
        from scorm_runner import transfer_scorm
        if self._busy or self._run is None: return
        selections = []
        for i, row in enumerate(self._run.data['activities']):
            self._review_choices[row['key']] = (self._table.cellWidget(i, 2).currentData(),
                self._table.item(i, 0).checkState() == Qt.CheckState.Checked)
            if self._table.item(i, 0).checkState() == Qt.CheckState.Checked:
                module_id = self._table.cellWidget(i, 2).currentData()
                if not module_id:
                    self._log.append_log(f"Choose a Brightspace unit for {row['name']}.", 'warning')
                    return
                selections.append((row['key'], str(module_id)))
        if not selections:
            self._log.append_log('Select at least one downloaded SCORM package.', 'warning')
            return
        if one: selections = selections[:1]
        self._transferring_keys = {key for key, _ in selections}
        urls = self._run.data['bs_url'], self._run.data['moodle_url']
        credentials = self._credentials()
        self._start(lambda: transfer_scorm(*urls, selections, self._log_message,
                                          credentials=credentials, directory=self._directory))

    def _log_message(self, message, tag='info'):
        self._queue.put(('log', (message, tag)))

    def _start(self, factory):
        if not getattr(self._mw, 'chromium_ready', True):
            self._log.append_log('Browser engine is still installing. Please wait.', 'warning')
            return
        self._set_busy(True)
        self._cancel_requested = False
        async def work():
            self._loop, self._task = asyncio.get_running_loop(), asyncio.current_task()
            if self._cancel_requested: raise asyncio.CancelledError()
            result = await factory()
            self._queue.put(('result', result))
        def worker():
            try:
                asyncio.run(work())
            except asyncio.CancelledError:
                self._log_message('SCORM stopped. Validated downloads and confirmed transfers are saved.', 'warning')
            except Exception as exc:
                message, detail = friendly_error(exc)
                self._log_message(message, 'error')
                if message != detail: self._log_message(detail, 'detail')
            finally:
                self._loop = self._task = None
                self._queue.put(('done', None))
        self._thread = threading.Thread(target=worker, daemon=True)
        self._thread.start()

    def _stop_work(self):
        if not self._busy: return
        self._cancel_requested = True
        self._stop.setEnabled(False)
        self._ready.hide()
        if self._loop and self._task:
            try: self._loop.call_soon_threadsafe(self._task.cancel)
            except RuntimeError: pass
        if self._ready_event: self._ready_event.set()

    def _show_rows(self):
        self._table.setRowCount(len(self._run.data['activities']))
        for i, row in enumerate(self._run.data['activities']):
            selected = QTableWidgetItem()
            # Hash/ZIP validation runs in the worker; GB-sized packages must
            # not be re-read on the Qt UI thread just to draw the review table.
            available = bool(row.get('download_valid') is not False and row.get('filename') and row.get('sha256') and
                             (self._run.folder(row) / row['filename']).is_file())
            selected.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable if available else Qt.ItemFlag.NoItemFlags)
            choice = self._review_choices.get(row['key'])
            checked = choice[1] if choice else row.get('state') not in ('Already present', 'Done + verified')
            selected.setCheckState(Qt.CheckState.Checked if available and checked else Qt.CheckState.Unchecked)
            self._table.setItem(i, 0, selected)
            name = QTableWidgetItem(row['name'])
            name.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            name.setToolTip(f"Moodle section: {row['section']}\nZIP: {row.get('filename', 'not downloaded')}")
            self._table.setItem(i, 1, name)
            unit = QComboBox()
            unit.addItem('Choose a unit…', None)
            for module in self._modules: unit.addItem(f"{module['title']} ({module['id']})", str(module['id']))
            unit.setCurrentIndex(max(0, unit.findData(choice[0] if choice else row.get('module_id'))))
            self._table.setCellWidget(i, 2, unit)
            status = QTableWidgetItem(row.get('state', 'Ready'))
            status.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            status.setToolTip(row.get('error', ''))
            self._table.setItem(i, 3, status)

    def _poll(self):
        while not self._queue.empty():
            kind, data = self._queue.get_nowait()
            if kind == 'log': self._log.append_log(*data)
            elif kind == 'waiting':
                if not self._cancel_requested: self._ready.show()
            elif kind == 'result':
                if isinstance(data, tuple): self._run, self._modules = data
                else:
                    self._run = data
                    for row in self._run.data['activities']:
                        if row['key'] in self._transferring_keys and row.get('state') in ('Already present', 'Done + verified'):
                            self._review_choices.pop(row['key'], None)
                self._show_rows()
            elif kind == 'done':
                self._ready.hide()
                self._set_busy(False)
