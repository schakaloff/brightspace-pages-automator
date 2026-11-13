"""Native file-based quiz repair; no browser, account, or AI connection."""
import json
import queue
import threading
import webbrowser
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal, QUrl, QTimer
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication, QMessageBox, QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, QFileDialog,
    QPlainTextEdit, QProgressBar, QScrollArea, QFrame,
)

from panels.checker_workflow import label, button
from quiz_repair import repair_package
from quiz_review import inspect_exports, write_review_report
from config import USERDATA_DIR


class RepairWorker(QThread):
    progress = Signal(str)
    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(self, moodle, package, output, parent=None, *, review_only=False):
        super().__init__(parent)
        self.paths = moodle, package, output
        self.review_only = review_only

    def run(self):
        try:
            self.progress.emit('Checking source versions, answer choices, and HTML…')
            review = inspect_exports(self.paths[0], self.paths[1])
            if self.review_only:
                path = write_review_report(review, self.paths[2])
                self.succeeded.emit({'review': review, 'report_path': path, 'output': path})
                return
            if review['blocking_count']:
                findings = [f for q in review['quizzes'] for row in q['questions'] for f in row['findings'] if f['severity'] == 'block']
                detail = findings[0]['message'] if findings else 'Question-library issues were found.'
                raise ValueError(f'Preflight found {review["blocking_count"]} blocking finding(s). {detail} '
                                 'Use Review exports to inspect them. No ZIP was saved.')
            result = repair_package(*self.paths, progress=self.progress.emit)
            result['preflight_review_count'] = review['review_count']
            self.succeeded.emit(result)
        except Exception as exc:
            self.failed.emit(str(exc) or type(exc).__name__)


class QuizRepairPanel(QWidget):
    def __init__(self, main_window=None, parent=None):
        super().__init__(parent)
        self._busy = False
        self._worker = None
        self._mw = main_window
        self._studio = None
        self._studio_queue = queue.Queue()
        self._last_draft = (main_window.load_config().get('quiz_review_draft', '') if main_window and hasattr(main_window, 'load_config') else '')
        self._studio_timer = QTimer(self)
        self._studio_timer.timeout.connect(self._poll_studio)
        self._studio_timer.start(250)
        QApplication.instance().aboutToQuit.connect(self.close_studio)
        self._result = None
        self.setObjectName('quiz_repair_panel')
        self.setProperty('design', 'modern')
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        outer.addWidget(scroll)
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(32, 28, 32, 24)
        layout.setSpacing(12)
        layout.addWidget(label('Quiz repair', 'page-heading'))
        layout.addWidget(button('Open Quiz Repair workspace', self.open_studio))
        layout.addWidget(label('Review the export versions before repairing. Comparisons and repairs run on this computer.', 'dim'))
        layout.addSpacing(12)
        self._moodle, self._package = QLineEdit(), QLineEdit()
        self._pickers = []
        for title, field, extension, placeholder in (
            ('Reference: Moodle backup or original Brightspace export', self._moodle, '*.mbz *.zip', 'Choose the reference .mbz or .zip'),
            ('Brightspace export to review or repair', self._package, '*.zip', 'Choose the candidate .zip'),
        ):
            heading = label(title, 'field-label')
            heading.setBuddy(field)
            layout.addWidget(heading)
            row = QHBoxLayout()
            field.setPlaceholderText(placeholder)
            field.setAccessibleName(title)
            field.setMinimumWidth(0)
            field.setMinimumHeight(42)
            field.textChanged.connect(self._inputs_changed)
            row.addWidget(field, 1)
            picker = button('Browse…', lambda _=False, f=field, e=extension: self._choose(f, e), 'secondary')
            picker.setAccessibleName('Browse for ' + title.lower())
            picker.setMinimumHeight(42)
            row.addWidget(picker)
            self._pickers.append(picker)
            layout.addLayout(row)
        layout.addSpacing(8)
        layout.addWidget(label('Repairs included', 'section-heading'))
        layout.addWidget(label('Restore missing question and section images.\nUse Correct Answers, Limited Selections for multi-select questions.\nGive matching questions equal credit for each correct pair.'))
        layout.addWidget(label('The original files stay intact. Dates, quiz instructions, visibility, pagination, and shuffling stay as they are.', 'hint'))
        self._run = button('Repair and save a candidate…', self._start)
        self._run.setMinimumHeight(42)
        self._review = button('Review exports…', self._start_review, 'secondary')
        self._review.setMinimumHeight(42)
        action_row = QHBoxLayout()
        action_row.addWidget(self._review)
        action_row.addWidget(self._run)
        action_row.addStretch()
        layout.addLayout(action_row)
        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setAccessibleName('Quiz repair progress')
        self._progress.hide()
        layout.addWidget(self._progress)
        self._status = label('Choose both exports to begin.', 'dim')
        self._status.setAccessibleName('Repair status')
        self._status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self._status)
        self._summary = label()
        self._summary.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self._summary)
        self._details = QPlainTextEdit()
        self._details.setReadOnly(True)
        self._details.setAccessibleName('Repair changes and review notes')
        self._details.setMinimumHeight(160)
        self._details.hide()
        self._show_details = button('Show changes and review notes', self._toggle_details, 'link')
        self._show_details.setCheckable(True)
        self._show_details.hide()
        layout.addWidget(self._show_details, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(self._details)
        actions = QHBoxLayout()
        self._folder = button('Show saved ZIP', self._open_folder, 'secondary')
        self._report = button('Save change report…', self._save_report, 'secondary')
        for widget in (self._folder, self._report):
            widget.setMinimumHeight(38)
            widget.hide()
            actions.addWidget(widget)
        actions.addStretch()
        layout.addLayout(actions)
        layout.addStretch()
        scroll.setWidget(body)
        self._inputs_changed()

    @property
    def studio_busy(self):
        return bool(self._studio and (self._studio.busy or self._studio.pending_file))

    def open_studio(self):
        from quiz_studio import QuizStudio
        if self._studio is None:
            self._studio = QuizStudio(USERDATA_DIR / 'quiz-reviews', self._studio_queue.put)
            for key, field in [('reference', self._moodle), ('candidate', self._package)]:
                if Path(field.text().strip()).is_file():
                    self._studio.files[key] = field.text().strip()
        try:
            if not webbrowser.open(self._studio.start()):
                QMessageBox.information(self, 'Open Quiz Repair', 'Check the default browser in Windows settings and try again.')
        except Exception as exc:
            QMessageBox.warning(self, 'Open Quiz Repair', str(exc))

    def close_studio(self):
        if self._studio:
            self._studio.close()
            self._studio = None

    def _poll_studio(self):
        if not self._studio:
            return
        draft = str(self._studio.workspace.draft_path or '')
        if draft and draft != self._last_draft:
            self._last_draft = draft
            if self._mw and hasattr(self._mw, 'save_config'):
                self._mw.save_config({'quiz_review_draft': draft})
        try:
            field = self._studio_queue.get_nowait()
        except queue.Empty:
            return
        if field == 'output':
            source = Path(self._studio.files['candidate'])
            path, _ = QFileDialog.getSaveFileName(self, 'Save reviewed quiz package as a new file', str(source.with_name(source.stem + '_REVIEWED.zip')), 'Brightspace package (*.zip)')
        elif field == 'draft':
            path, _ = QFileDialog.getOpenFileName(self, 'Open saved quiz review', self._last_draft, 'Quiz review (*.quiz-review.json)')
        else:
            extension = '*.mbz *.zip' if field == 'reference' else '*.zip'
            path, _ = QFileDialog.getOpenFileName(self, 'Choose ' + field + ' export', self._studio.files.get(field, ''), f'Export ({extension})')
        studio = self._studio
        if studio:
            studio.busy = True
            threading.Thread(target=studio.picked, args=(field, path), daemon=True).start()

    def _inputs_changed(self):
        if self._busy:
            return
        valid = all(Path(field.text().strip()).is_file() for field in (self._moodle, self._package))
        self._review.setEnabled(valid)
        self._run.setEnabled(valid and Path(self._moodle.text().strip()).suffix.lower() == '.mbz')
        self._result = None
        self._summary.clear()
        self._details.hide()
        self._show_details.hide()
        self._folder.hide()
        self._report.hide()
        self._status.setText('Ready for a local review. Repairs require a Moodle backup as reference.' if valid else 'Choose a reference export and a candidate Brightspace ZIP to begin.')

    def _choose(self, field, extension):
        path, _ = QFileDialog.getOpenFileName(self, field.accessibleName(), field.text(), f'Export ({extension})')
        if path:
            field.setText(path)

    def _start(self):
        if self._busy:
            return
        package = Path(self._package.text().strip())
        default = package.with_name(package.stem + '_QUIZ_REPAIRED.zip')
        output, _ = QFileDialog.getSaveFileName(self, 'Save corrected quiz package as a new file', str(default), 'Brightspace package (*.zip)')
        if output:
            self._begin(self._moodle.text().strip(), str(package), output)

    def _start_review(self):
        if self._busy:
            return
        package = Path(self._package.text().strip())
        output, _ = QFileDialog.getSaveFileName(self, 'Save local quiz review report', str(package.with_name(package.stem + '_REVIEW.html')), 'Local HTML report (*.html)')
        if output:
            self._begin(self._moodle.text().strip(), str(package), output, review_only=True)

    def _begin(self, moodle, package, output, *, review_only=False):
        self._result = None
        self._summary.clear()
        self._details.hide()
        self._show_details.hide()
        self._folder.hide()
        self._report.hide()
        self._busy = True
        self._progress.show()
        self._run.setEnabled(False)
        self._review.setEnabled(False)
        for field in (self._moodle, self._package, *self._pickers):
            field.setEnabled(False)
        self._status.setText('Reading the exports…')
        worker = RepairWorker(moodle, package, output, self, review_only=review_only)
        self._worker = worker
        worker.progress.connect(self._status.setText)
        worker.succeeded.connect(self._success)
        worker.failed.connect(self._failure)
        worker.finished.connect(self._finished)
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def _success(self, result):
        self._result = result
        if 'review' in result:
            review = result['review']
            self._status.setText(f'Review saved: {review["blocking_count"]} blocking finding(s), {review["review_count"]} item(s) need review.')
            self._summary.setText(f'{len(review["quizzes"])} quizzes · {review["question_count"]} question placements\n'
                                  'This checks the selected files; live-course appearance and grading are not verified.')
            self._folder.setText('Open review report')
            self._folder.show()
            return
        self._status.setText('Repair candidate saved. Structural checks passed; visual review is still required.')
        counts = result['counts']
        multi = counts.get('Multi-select: Correct Answers, Limited Selections', 0)
        matching = counts.get('Matching: equal weighting and Moodle answer pairs', 0)
        self._summary.setText(f"{result['quizzes']} quizzes retained · {result['images_restored']} images restored\n"
                              f'{multi} multi-select records updated · {matching} matching records updated\n'
                              f"Saved to: {result['output']}")
        details = ['Review notes', *result['warnings'], 'Brightspace import and grading have not been tested.', '', 'Changes']
        details.extend(f"{c['repair']} — {c.get('asset', c.get('question', ''))} ({c['file']})" for c in result['changes'])
        if not result['changes']:
            details.append('The package already uses these scoring rules and includes its referenced local images.')
        self._details.setPlainText('\n'.join(details))
        self._details.hide()
        self._show_details.setChecked(False)
        self._show_details.setText('Show changes and review notes')
        self._show_details.show()
        self._folder.show()
        self._folder.setText('Show saved ZIP')
        self._report.show()

    def _failure(self, message):
        self._result = None
        self._status.setText('Repair stopped. ' + message)

    def _toggle_details(self, shown):
        self._details.setVisible(shown)
        self._show_details.setText('Hide changes and review notes' if shown else 'Show changes and review notes')

    def _finished(self):
        self._busy = False
        self._worker = None
        self._progress.hide()
        for field in (self._moodle, self._package, *self._pickers):
            field.setEnabled(True)
        valid = all(Path(f.text().strip()).is_file() for f in (self._moodle, self._package))
        self._review.setEnabled(valid)
        self._run.setEnabled(valid and Path(self._moodle.text().strip()).suffix.lower() == '.mbz')

    def _open_folder(self):
        if self._result:
            path = self._result.get('report_path') or str(Path(self._result['output']).parent)
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def _save_report(self):
        if not self._result:
            return
        default = str(Path(self._result['output']).with_suffix('.report.json'))
        path, _ = QFileDialog.getSaveFileName(self, 'Save quiz repair report', default, 'JSON report (*.json)')
        if path:
            try:
                destination = Path(path).resolve()
                protected = {Path(f.text()).resolve() for f in (self._moodle, self._package)} | {Path(self._result['output']).resolve()}
                if destination in protected or destination.suffix.lower() != '.json':
                    raise OSError('Choose a .json filename separate from the quiz exports.')
                destination.write_text(json.dumps(self._result, indent=2, ensure_ascii=False), encoding='utf-8')
            except OSError as exc:
                self._status.setText('The ZIP is saved, but the report could not be saved: ' + str(exc))
