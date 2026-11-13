"""Render the incumbent and a proposed Kaltura flow. No network or real records.

Run with .venv/Scripts/python.exe tools/preview_kaltura_workflow.py.
The proposal is an isolated design artifact, never imported by the application.
"""
import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QFontDatabase, QIcon
from PySide6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QFrame, QPushButton, QLineEdit, QCheckBox, QComboBox, QScrollArea,
    QProgressBar)
import gui_styles
from gui_tool_icons import tool_pixmap
from panels.h5p_saved_transfers import SavedH5PTransfersDialog
import panels.h5p_saved_transfers as saved_library

OUTPUT = ROOT / 'docs' / 'design' / 'kaltura-2026-11-02'
SAMPLES = [
    ('Welcome and course overview', 'Welcome', '1_sample01'),
    ('Lab safety briefing', 'Welcome', '1_sample02'),
    ('Preparing the workspace', 'Week 1 · Foundations', '1_sample03'),
    ('Hand hygiene demonstration', 'Week 1 · Foundations', '1_sample04'),
    ('Equipment walkthrough', 'Week 1 · Foundations', '1_sample05'),
    ('Instrument handling', 'Week 2 · Practice', '1_sample06'),
    ('Practice demonstration', 'Week 2 · Practice', '1_sample07'),
    ('Closing checklist', 'Week 3 · Review', '1_sample08'),
]


def label(text, role='dim'):
    item = QLabel(text)
    item.setTextFormat(Qt.TextFormat.PlainText)
    item.setProperty('role', role)
    item.setWordWrap(True)
    item.setMinimumWidth(0)
    return item


def button(text, secondary=True, icon=None):
    item = QPushButton(text.replace('&', '&&'))
    item.setMinimumHeight(38)
    item.setAutoDefault(False)
    if secondary:
        item.setProperty('variant', 'secondary')
    if icon:
        item.setIcon(QIcon(tool_pixmap(icon, 16, gui_styles.modern_colors()['muted'])))
    return item


def card(title=None, detail=None):
    frame = QFrame()
    frame.setProperty('role', 'surface')
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(20, 18, 20, 18)
    layout.setSpacing(10)
    if title:
        layout.addWidget(label(title, 'section-heading'))
    if detail:
        layout.addWidget(label(detail))
    return frame, layout


def badge(text, state=''):
    item = label(text, 'status')
    item.setProperty('state', state)
    return item


class Proposal(QWidget):
    def __init__(self, stage):
        super().__init__()
        self.stage = stage
        self.setProperty('design', 'modern')
        self.setObjectName('h5p_workspace')  # Reuse the actual shared phase styles.
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 20, 24, 18)
        outer.setSpacing(12)
        heading = QHBoxLayout()
        heading.addWidget(label('Kaltura transfer', 'page-heading'), 1)
        heading.addWidget(button('Saved transfers', icon='folder'))
        outer.addLayout(heading)
        outer.addWidget(label('Create Brightspace pages for Moodle videos. Review every destination before making changes.'))
        outer.addWidget(label('Design preview · sample data · proposed recovery behavior', 'hint'))
        steps = QHBoxLayout()
        active = {'setup': 0, 'connect': 0, 'review': 1, 'create': 2, 'results': 3}[stage]
        for i, (name, icon) in enumerate((('Connect', 'link'), ('Review', 'clipboard-list'),
                                          ('Create', 'movie'), ('Results', 'circle-check'))):
            step = QFrame()
            step.setProperty('role', 'h5p-step')
            step.setProperty('state', 'review' if i == active else 'created' if i < active and i < 2 else '')
            step.setFixedHeight(64)
            step.setMinimumWidth(0)
            box = QVBoxLayout(step)
            box.setContentsMargins(8, 6, 8, 6)
            box.setSpacing(4)
            image = QLabel()
            image.setPixmap(tool_pixmap(icon, 20, gui_styles.modern_colors()['text' if i == active else 'muted']))
            box.addWidget(image, 0, Qt.AlignmentFlag.AlignHCenter)
            box.addWidget(label(name, 'h5p-step-title'), 0, Qt.AlignmentFlag.AlignHCenter)
            steps.addWidget(step, 1)
        outer.addLayout(steps)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        self.content = QVBoxLayout(body)
        self.content.setContentsMargins(0, 0, 8, 0)
        self.content.setSpacing(14)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)
        getattr(self, stage)()
        self.content.addStretch()
        footer = QVBoxLayout()
        footer.setSpacing(8)
        caption = {
            'setup': 'Finding videos reads your courses. Review and approve before creating pages.',
            'connect': 'Signing in does not create course pages.',
            'review': 'Create 6 pages + 1 unit · keep 1 existing page · exclude 1 video',
            'create': 'Only verified pages count as created. Stopping keeps the saved transfer.',
            'results': '4 created and verified · 1 needs checking · 1 not started',
        }[stage]
        footer.addWidget(label(caption, 'hint'))
        actions = QHBoxLayout()
        left = button('Stop' if stage == 'create' else 'Back' if stage == 'review' else 'Close transfer')
        if stage == 'connect':
            left.setText('Cancel connection')
        actions.addWidget(left)
        actions.addStretch()
        primary = button({'setup': 'Connect & find videos', 'connect': 'Waiting for Kaltura sign-in…', 'review': 'Approve & create 6 pages',
                          'create': 'Creating pages…', 'results': 'Check remaining pages'}[stage], False)
        primary.setEnabled(stage not in ('connect', 'create'))
        actions.addWidget(primary)
        footer.addLayout(actions)
        outer.addLayout(footer)

    def course_summary(self):
        frame, box = card('Clinical Skills · Fall 2026')
        box.addWidget(label('Moodle 166972 → Brightspace 23840', 'hint'))
        box.addWidget(label('Transfer a13f82 · Last saved 02 Nov 2026, 14:35 UTC', 'hint'))
        self.content.addWidget(frame)

    def setup(self):
        frame, box = card('Your courses')
        for name, value in (('From Moodle', 'https://mymoodle.example/course/view.php?id=166972'),
                            ('To Brightspace', 'https://brightspace.example/d2l/home/23840')):
            entry = QLineEdit(value)
            entry.setReadOnly(True)
            caption = label(name, 'field-label')
            caption.setBuddy(entry)
            box.addWidget(caption)
            box.addWidget(entry)
        watch = QCheckBox('Show automation cursor and live messages')
        watch.setChecked(True)
        box.addWidget(watch)
        self.content.addWidget(frame)
        frame, box = card('Ready to connect', 'We will check Moodle, Brightspace and Kaltura, then scan the Moodle course and suggest destinations.')
        box.addWidget(label('If a service needs sign-in or MFA, a browser window opens and the next action appears here.', 'hint'))
        self.content.addWidget(frame)

    def connect(self):
        self.course_summary()
        frame, box = card('Sign in to Kaltura', 'Finish signing in in the Kaltura browser window. We will detect the entries list and continue automatically.')
        for name, status, state in (('Moodle', 'Connected', 'created'), ('Brightspace', 'Connected', 'created'),
                                    ('Kaltura Management Console', 'Your attention needed', 'review')):
            row = QHBoxLayout()
            row.addWidget(label(name, 'field-label'), 1)
            row.addWidget(badge(status, state))
            box.addLayout(row)
        box.addWidget(button('Show Kaltura browser', icon='arrow-right'))
        box.addWidget(label('Next: scan the whole Moodle course and suggest destinations. Pages, books and embedded videos are included; unreadable activities will be reported.', 'hint'))
        self.content.addWidget(frame)

    def review(self):
        self.course_summary()
        frame, box = card('Review videos and destinations', '8 unique videos found. Repeated uses of the same video are grouped; review additional source locations before approving.')
        query = QLineEdit()
        query.setPlaceholderText('Search videos, source sections or entry IDs…')
        box.addWidget(query)
        self.content.addWidget(frame)
        groups = [('Welcome', 'Welcome', SAMPLES[:2], ['Already present · kept', 'Ready']),
                  ('Week 1 · Foundations', 'Week 1 · Foundations', SAMPLES[2:5], ['Ready'] * 3),
                  ('Week 2 · Practice', 'New unit: Week 2 · Practice', SAMPLES[5:7], ['Ready'] * 2),
                  ('Week 3 · Review', 'Choose a destination…', SAMPLES[7:], ['Excluded · no destination'])]
        self.search_rows = []
        for source, destination, rows, statuses in groups:
            group = QFrame()
            box = QVBoxLayout(group)
            box.setContentsMargins(8, 4, 8, 8)
            box.setSpacing(8)
            box.addWidget(label(source, 'section-heading'))
            dest = QComboBox()
            dest.addItem(destination)
            dest.setAccessibleName('Brightspace destination for ' + source)
            box.addWidget(dest)
            for (name, section, entry_id), status in zip(rows, statuses):
                row = QHBoxLayout()
                check = QCheckBox()
                check.setChecked(status == 'Ready')
                check.setEnabled(status == 'Ready')
                check.setAccessibleName('Select ' + name)
                row.addWidget(check)
                copy = QVBoxLayout()
                copy.setSpacing(2)
                copy.addWidget(label(name, 'field-label'))
                copy.addWidget(label(entry_id + ' · ' + status, 'hint'))
                row.addLayout(copy, 1)
                box.addLayout(row)
            self.content.addWidget(group)
            self.search_rows.append((group, ' '.join((source, destination, *(r[0] for r in rows), *(r[2] for r in rows))).casefold()))
        query.textChanged.connect(lambda text: [widget.setVisible(text.casefold() in data) for widget, data in self.search_rows])
        frame, box = card('Changes to approve', 'Create 6 pages and 1 new unit in Brightspace course 23840. Keep the existing page. Leave the unmapped video excluded.')
        box.addWidget(label('No automatic undo. Back keeps your selections; changing courses requires a new plan.', 'hint'))
        self.content.addWidget(frame)

    def create(self):
        self.course_summary()
        frame, box = card('Verifying “Hand hygiene demonstration”', 'The Save action finished. Checking the destination for the saved page and its Kaltura entry ID before counting it as created.')
        progress = QProgressBar()
        progress.setTextVisible(False)
        progress.setRange(0, 6)
        progress.setValue(2)
        progress.setFormat('2 of 6 pages verified')
        box.addWidget(progress)
        box.addWidget(label('2 verified · 1 being checked · 3 not started', 'hint'))
        self.content.addWidget(frame)
        frame, box = card('What the automation is doing')
        box.addWidget(label('Brightspace · Week 1 · Foundations', 'field-label'))
        box.addWidget(label('Checking the saved video embed…'))
        box.addWidget(label('The existing automation cursor, click effects and status bubble appear in the browser. This preview does not simulate a live course.', 'hint'))
        box.addWidget(button('Show automation browser', icon='arrow-right'))
        self.content.addWidget(frame)
        frame, box = card('You can stop safely', 'Stop requests a pause. The current save is checked before the worker closes. If its outcome cannot be established, that item stays “Needs checking” and is checked before any retry.')
        self.content.addWidget(frame)

    def results(self):
        self.course_summary()
        frame, box = card('Transfer needs attention', '4 of 6 approved pages are created and verified. One save could not be verified; one page has not been started.')
        progress = QProgressBar()
        progress.setTextVisible(False)
        progress.setRange(0, 6)
        progress.setValue(4)
        progress.setFormat('4 of 6 pages verified')
        box.addWidget(progress)
        self.content.addWidget(frame)
        frame, box = card('Check before continuing')
        box.addWidget(label('Instrument handling', 'field-label'))
        box.addWidget(badge('Needs checking', 'review'), 0, Qt.AlignmentFlag.AlignLeft)
        box.addWidget(label('The browser closed after Save. The page may exist in Week 2 · Practice. Check the destination before creating another copy.'))
        box.addWidget(button('Open destination in Brightspace', icon='arrow-right'))
        box.addWidget(label('Practice demonstration', 'field-label'))
        box.addWidget(label('Not started · ready after the destination check', 'hint'))
        self.content.addWidget(frame)
        frame, box = card('Everything else is accounted for', '4 verified pages are kept. The page already present was not recreated. The excluded video was not changed.')
        box.addWidget(label('Continue checks existing pages first. A new page is allowed only after a reliable check confirms it is missing.', 'hint'))
        self.content.addWidget(frame)
        self.content.addWidget(button('Show verified pages and technical details'))


class Library(SavedH5PTransfersDialog):
    """Use the actual H5P library view, with an in-memory Kaltura adapter."""
    def __init__(self, removed=False):
        # This module runs only as a standalone preview; no disk run is loaded.
        samples = [
            ('Clinical Skills · Fall 2026', '166972 → 23840', 'Needs attention', '4 of 6 pages verified · 1 needs checking', '02 Nov 2026 · 14:35 UTC', 'a13f82'),
            ('Biology · Introduction', '185001 → 25401', 'Complete', '6 of 6 pages verified', '01 Nov 2026 · 10:12 UTC', 'bc715e'),
            ('Medical Terminology', '187689 → 25707', 'Ready to review', '12 videos found · no pages created', '30 Oct 2026 · 09:24 UTC', '5d910a'),
            ('Course orientation', '182002 → 25601', 'No videos', 'Complete scan · 0 Kaltura videos', '29 Oct 2026 · 16:18 UTC', '7f4d21'),
        ]
        runs = []
        for index, (title, route, status, progress, date, id_) in enumerate(samples):
            category = {'Needs attention': 'attention', 'Complete': 'complete',
                        'Ready to review': 'ready', 'No videos': 'empty'}[status]
            info = dict(title=title, route='Moodle ' + route.replace(' → ', ' → Brightspace '),
                status=status, progress=progress, category=category,
                date=date.split(' · ')[0], time=date.split(' · ')[1],
                search=' '.join((title, route, status, id_)).casefold(), removed=removed and index == 0)
            runs.append(SimpleNamespace(data=dict(id=id_ + '0' * 26, removed_at='sample' if info['removed'] else ''), info=info))
        saved_library.saved_runs = lambda *args, **kwargs: runs
        saved_library.describe_run = lambda run: run.info
        super().__init__(directory=OUTPUT / 'no-live-records')
        self.setWindowTitle('Saved Kaltura transfers · design preview')
        for item in self.findChildren(QLabel):
            if item.text() == 'Saved transfers':
                item.setText('Saved Kaltura transfers')
            elif item.text().startswith('Find a course, pick up'):
                item.setText('Design preview · sample data · searchable saved Kaltura work')
            elif item.text().startswith('Removing an entry'):
                item.setText('Remove from list hides the entry. Moodle videos, Kaltura media, Brightspace pages and recovery history are kept. Restore it from Removed.')
        self.search.setPlaceholderText('Search course, course ID or transfer ID…')
        self.table.setAccessibleName('Saved Kaltura transfers')
        self.filter.setItemText(self.filter.findData('empty'), 'No Kaltura videos')
        self.filter.insertItem(2, 'Ready to review', 'ready')
        if removed:
            self.filter.setCurrentIndex(self.filter.findData('removed'))
            self.notice.setText('1 transfer removed from the saved list. Course pages and recovery history are kept.')
            self.notice_row.show()
        self.table.selectRow(0)

    def _render(self, *args):
        super()._render(*args)
        # Kaltura's recovery descriptions wrap longer than H5P's counts.
        for row in range(self.table.rowCount()):
            self.table.setRowHeight(row, 112)

    def _selection_changed(self):
        super()._selection_changed()
        selected = self.selected_runs()
        if len(selected) == 1 and selected[0].info['category'] == 'attention':
            self.open_button.setText('Check && continue')

    def _change_removed(self):
        # Deliberately in-memory: preview interactions never touch real history.
        removed = self.filter.currentData() != 'removed'
        self._undo_ids = [run.data['id'] for run in self.selected_runs()]
        for run in self.selected_runs():
            run.info['removed'] = removed
            run.data['removed_at'] = 'sample' if removed else ''
        self.notice.setText('Saved list updated. Course content and recovery history are kept.')
        self.notice_row.show()
        self._render()

    def _undo(self):
        for run in self._runs:
            if run.data['id'] in self._undo_ids:
                run.info['removed'] = not run.info['removed']
                run.data['removed_at'] = 'sample' if run.info['removed'] else ''
        self._undo_ids = []
        self.notice_row.hide()
        self._render()

    def _open(self):
        pass  # A screenshot preview never starts a transfer.


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    app = QApplication([])
    app.setStyle('Fusion')
    for font in ('segoeui.ttf', 'segoeuib.ttf', 'seguisb.ttf', 'seguisym.ttf'):
        QFontDatabase.addApplicationFont(str(Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts' / font))
    app.setFont(QFont('Segoe UI', 10))
    def capture(widget, filename, size):
        widget.resize(*size)
        widget.show()
        for _ in range(5):
            app.processEvents()
        widget.grab().save(str(OUTPUT / filename))
    for theme in ('light', 'dark'):
        gui_styles.set_theme(theme)
        app.setStyleSheet(gui_styles.get_stylesheet())
        # Historical incumbent captures are preserved; render proposals only.
        for stage in ('setup', 'connect', 'review', 'create', 'results'):
            preview = Proposal(stage)
            capture(preview, f'proposed-{stage}-{theme}.png', (1040, 1000 if stage == 'review' else 900))
            capture(preview, f'proposed-{stage}-narrow-{theme}.png', (640, 720))
            preview.close()
        for removed in (False, True):
            preview = Library(removed)
            key = 'removed' if removed else 'library'
            capture(preview, f'proposed-{key}-{theme}.png', (1040, 960))
            capture(preview, f'proposed-{key}-narrow-{theme}.png', (640, 720))
            preview.close()
    # A readable index; the individual full-size PNGs remain the design source.
    from PIL import Image, ImageDraw, ImageFont
    font_path = str(Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts' / 'segoeui.ttf')
    font = ImageFont.truetype(font_path, 19)
    for board, suffix in (('preview-overview.png', ''), ('preview-small-windows.png', '-narrow')):
        sheet = Image.new('RGB', (1080, 1690), '#e8edf2')
        draw = ImageDraw.Draw(sheet)
        for index, (stage, theme, title) in enumerate((
            ('connect', 'light', 'Connect · sign-in attention'),
            ('review', 'light', 'Review · selections and destinations'),
            ('create', 'light', 'Create · verified progress'),
            ('results', 'dark', 'Results · uncertain save recovery'),
            ('library', 'light', 'Saved transfers · shared H5P library'),
            ('removed', 'dark', 'Removed · Undo and Restore'),
        )):
            x, y = 14 + (index % 2) * 534, 12 + (index // 2) * 556
            draw.text((x, y), title, font=font, fill='#1c2b3b')
            shot = Image.open(OUTPUT / f'proposed-{stage}{suffix}-{theme}.png').convert('RGB')
            shot.thumbnail((520, 514), Image.Resampling.LANCZOS)
            sheet.paste(shot, (x, y + 32))
        sheet.save(OUTPUT / board)
    print(OUTPUT)


if __name__ == '__main__':
    main()
