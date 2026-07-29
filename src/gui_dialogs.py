import json
import sys
import threading
import webbrowser
from pathlib import Path

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QScrollArea, QWidget, QCheckBox, QSpinBox,
    QFrame, QProgressBar,
)
from PySide6.QtCore import Qt, QTimer, Signal


# ── FileChecklistDialog ───────────────────────────────────────────────────────

class FileChecklistDialog(QDialog):
    """Two-section checklist: missing files (default checked) and fuzzy-matched
    review files (default UNCHECKED — user must opt in before anything uploads).

    Populates *result_list* in-place with selected file dicts from either
    section, then sets *event* so the background asyncio thread can resume.
    The event is guaranteed to be set on every exit path (OK, Skip All, window X).
    """

    def __init__(self, data_json: str, result_list: list, event: threading.Event, parent=None):
        super().__init__(parent)
        self._result_list = result_list
        self._event = event
        payload = json.loads(data_json)
        self._missing = payload.get("missing", [])
        self._review = payload.get("review", [])
        self._checkboxes: list[tuple[QCheckBox, dict]] = []

        self.setWindowTitle("Missing Files — Select to Download")
        self.setMinimumSize(560, 520)
        self.resize(580, 580)
        self.setModal(True)

        self._build()

        if not self._missing and not self._review:
            self._release(selected=[])

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(8)

        total = len(self._missing) + len(self._review)
        title = QLabel(f"📋  {total} file(s) need attention")
        title.setStyleSheet("font-size:15px; font-weight:bold;")
        layout.addWidget(title)

        sub1 = QLabel("Files will be downloaded from Moodle and uploaded to the matching section.")
        sub1.setProperty("role", "dim")
        sub1.setWordWrap(True)
        layout.addWidget(sub1)

        # Scrollable file list
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        inner = QWidget()
        inner_layout = QVBoxLayout(inner)
        inner_layout.setContentsMargins(4, 4, 4, 4)
        inner_layout.setSpacing(2)
        inner_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        scroll.setWidget(inner)
        layout.addWidget(scroll, 1)

        if self._missing:
            hdr = QLabel(f"Missing files ({len(self._missing)})")
            hdr.setStyleSheet("font-size:12px; font-weight:bold; padding-top:4px;")
            inner_layout.addWidget(hdr)
            sub2 = QLabel("Uncheck any you already have or don't need.")
            sub2.setProperty("role", "dim")
            inner_layout.addWidget(sub2)
            self._add_section_items(inner_layout, self._missing, default_checked=True)

        if self._review:
            hdr = QLabel(f"⚠ Needs review — fuzzy match ({len(self._review)})")
            hdr.setStyleSheet("font-size:12px; font-weight:bold; padding-top:14px; color:#d9822b;")
            inner_layout.addWidget(hdr)
            note = QLabel(
                "These only matched a similar Brightspace title, not confirmed. "
                "Unchecked by default — check the ones you've verified before uploading."
            )
            note.setProperty("role", "dim")
            note.setWordWrap(True)
            inner_layout.addWidget(note)
            self._add_section_items(inner_layout, self._review, default_checked=False)

        # Select / Deselect row
        tog_row = QHBoxLayout()
        tog_row.setSpacing(8)
        sel_all = QPushButton("Select All")
        sel_all.setProperty("variant", "secondary")
        sel_all.setFixedHeight(32)
        sel_all.clicked.connect(self._select_all)
        desel_all = QPushButton("Deselect All")
        desel_all.setProperty("variant", "secondary")
        desel_all.setFixedHeight(32)
        desel_all.clicked.connect(self._deselect_all)
        tog_row.addWidget(sel_all)
        tog_row.addWidget(desel_all)
        tog_row.addStretch()
        layout.addLayout(tog_row)

        self._count_lbl = QLabel("")
        self._count_lbl.setProperty("role", "dim")
        layout.addWidget(self._count_lbl)

        # Action buttons
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        self._dl_btn = QPushButton("⬇  Download Selected")
        self._dl_btn.setFixedHeight(40)
        self._dl_btn.clicked.connect(self._on_download)
        skip_btn = QPushButton("Skip All")
        skip_btn.setProperty("variant", "secondary")
        skip_btn.setFixedWidth(100)
        skip_btn.setFixedHeight(40)
        skip_btn.clicked.connect(self._on_skip)
        btn_row.addWidget(self._dl_btn, 1)
        btn_row.addWidget(skip_btn)
        layout.addLayout(btn_row)

        self._update_count()

    def _add_section_items(self, inner_layout, files: list, default_checked: bool):
        cur_section = None
        for f in files:
            sec = f.get("section") or "Other"
            if sec != cur_section:
                cur_section = sec
                sec_lbl = QLabel(f"── {sec} ──")
                sec_lbl.setProperty("role", "dim")
                sec_lbl.setStyleSheet("font-size:11px; padding-top:8px;")
                inner_layout.addWidget(sec_lbl)
            label = f["name"]
            if f.get("matched_title"):
                kind_tag = " [module/section title only]" if f.get("matched_kind") == "MODULE" else ""
                label += f"  →  matched \"{f['matched_title']}\" ({f.get('score', '?')}%){kind_tag}"
            if f.get("match_reason"):
                label += f"  [{f['match_reason']}]"
            cb = QCheckBox(label)
            cb.setChecked(default_checked)
            cb.toggled.connect(self._update_count)
            self._checkboxes.append((cb, f))
            inner_layout.addWidget(cb)

    def _update_count(self):
        n = sum(1 for cb, _ in self._checkboxes if cb.isChecked())
        self._count_lbl.setText(f"{n} of {len(self._checkboxes)} selected")
        self._dl_btn.setText(f"⬇  Download {n} Selected")

    def _select_all(self):
        for cb, _ in self._checkboxes:
            cb.setChecked(True)

    def _deselect_all(self):
        for cb, _ in self._checkboxes:
            cb.setChecked(False)

    def _on_download(self):
        selected = [f for cb, f in self._checkboxes if cb.isChecked()]
        self._release(selected)

    def _on_skip(self):
        self._release(selected=[])

    def _release(self, selected: list):
        self._result_list.clear()
        self._result_list.extend(selected)
        self._event.set()
        self.accept()

    def closeEvent(self, event):
        # Guarantee the background thread is never left hanging
        if not self._event.is_set():
            self._result_list.clear()
            self._event.set()
        super().closeEvent(event)


# ── PagesDialog ───────────────────────────────────────────────────────────────

class PagesDialog(QDialog):
    """Shows pages found in a section; user picks start index and count.

    After exec(), call result_value() → (start_0indexed, count).
    If the dialog is rejected (X button), result_value() returns (0, len(pages)).
    """

    def __init__(self, pages: list, parent=None):
        super().__init__(parent)
        self._pages = pages
        self._result = (0, len(pages))

        self.setWindowTitle("Pages Found")
        self.setFixedSize(480, 460)
        self.setModal(True)
        self._build()

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(10)

        title = QLabel(f"Found {len(self._pages)} pages in this section")
        title.setStyleSheet("font-size:16px; font-weight:bold;")
        layout.addWidget(title)

        # Page list
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setMaximumHeight(200)
        inner = QWidget()
        inner_layout = QVBoxLayout(inner)
        inner_layout.setContentsMargins(4, 4, 4, 4)
        inner_layout.setSpacing(2)
        inner_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        scroll.setWidget(inner)
        for i, p in enumerate(self._pages, 1):
            lbl = QLabel(f"{i}.  {p.get('label', p.get('title', ''))}")
            lbl.setProperty("role", "dim")
            inner_layout.addWidget(lbl)
        layout.addWidget(scroll)

        # Start / count fields
        fields_row = QHBoxLayout()
        fields_row.setSpacing(16)

        fields_row.addWidget(QLabel("Start from page:"))
        self._start_spin = QSpinBox()
        self._start_spin.setRange(1, max(1, len(self._pages)))
        self._start_spin.setValue(1)
        self._start_spin.setFixedWidth(70)
        self._start_spin.setFixedHeight(36)
        fields_row.addWidget(self._start_spin)

        fields_row.addWidget(QLabel("How many:"))
        self._count_spin = QSpinBox()
        self._count_spin.setRange(1, max(1, len(self._pages)))
        self._count_spin.setValue(len(self._pages))
        self._count_spin.setFixedWidth(70)
        self._count_spin.setFixedHeight(36)
        fields_row.addWidget(self._count_spin)
        fields_row.addStretch()
        layout.addLayout(fields_row)

        layout.addStretch()

        run_btn = QPushButton("▶  Run")
        run_btn.setFixedHeight(42)
        run_btn.clicked.connect(self._on_run)
        layout.addWidget(run_btn)

    def _on_run(self):
        start = self._start_spin.value() - 1  # 0-indexed
        count = self._count_spin.value()
        self._result = (start, count)
        self.accept()

    def result_value(self) -> tuple[int, int]:
        return self._result


# ── UpdateDialog ──────────────────────────────────────────────────────────────

class UpdateDialog(QDialog):
    """Offers the update and, on accept, installs it and closes the app.

    The download runs on a worker thread, so every hand-off back to the GUI goes
    through these signals. The actual installer is launched by a detached helper
    that waits for this app to exit first; starting Setup while AppMutex is still
    held can make silent updates exit without replacing anything.
    """

    _status_changed = Signal(str)
    _progress_changed = Signal(int)
    _install_started = Signal()
    _install_failed = Signal(str)

    def __init__(self, release: dict, parent=None):
        super().__init__(parent)
        self._release = release
        self._parent_window = parent

        self.setWindowTitle("Update available")
        self.setMinimumSize(420, 240)
        self.resize(460, 280)
        self.setModal(True)
        self._build()

        # Queued across the thread boundary by Qt, unlike QTimer.singleShot.
        self._status_changed.connect(self._on_status_changed)
        self._progress_changed.connect(self._on_progress_changed)
        self._install_started.connect(self._on_install_started)
        self._install_failed.connect(self._on_install_failed)

    def _build(self):
        from update_notes import note_for

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(10)

        if self._release.get("force_install"):
            title_text = f"Install latest version: {self._release.get('tag', '')}"
        else:
            title_text = f"New version available: {self._release.get('tag', '')}"
        title = QLabel(title_text)
        title.setStyleSheet("font-size:16px; font-weight:bold;")
        layout.addWidget(title)

        # One line instead of a changelog. Nobody running this tool needs a diff;
        # the honest notes stay on the GitHub release for whoever is debugging.
        joke = QLabel(note_for(self._release.get("tag", "")))
        joke.setWordWrap(True)
        joke.setStyleSheet("font-size:13px; padding:10px 0;")
        layout.addWidget(joke, 1)

        warning = QLabel(
            "⚠  Updating closes the app and stops anything it is currently running "
            "— scans, uploads and open browser windows will be cancelled."
        )
        warning.setWordWrap(True)
        warning.setStyleSheet("font-size:11px; color:#ffd75e;")
        layout.addWidget(warning)

        # Hidden until Restart & Update is pressed — then it is the primary
        # "something is happening" indicator. Percent mode while downloading,
        # indeterminate sweep while the installer takes over (no percent there).
        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setTextVisible(True)
        self._progress.setFixedHeight(16)
        self._progress.setStyleSheet(
            "QProgressBar { background:#1c2530; border:1px solid #2c3947;"
            "  border-radius:8px; font-size:10px; color:#e6edf3; text-align:center; }"
            "QProgressBar::chunk { background:#0ea5e9; border-radius:7px; }"
        )
        self._progress.hide()
        layout.addWidget(self._progress)

        self._status_lbl = QLabel("")
        self._status_lbl.setProperty("role", "dim")
        self._status_lbl.setStyleSheet("font-size:11px;")
        layout.addWidget(self._status_lbl)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        btn_row.addStretch()

        later_btn = QPushButton("Not now")
        later_btn.setProperty("variant", "secondary")
        later_btn.setFixedHeight(38)
        later_btn.clicked.connect(self.reject)

        button_text = (
            "Restart && Install"
            if self._release.get("force_install")
            else "Restart && Update"
        )
        self._update_btn = QPushButton(button_text)
        self._update_btn.setFixedHeight(38)
        self._update_btn.clicked.connect(self._on_update)

        if not self._release.get("asset_url") and sys.platform == "win32":
            self._update_btn.setEnabled(False)
            self._status_lbl.setText("No installer found in this release.")

        btn_row.addWidget(later_btn)
        btn_row.addWidget(self._update_btn)
        layout.addLayout(btn_row)

    def _on_update(self):
        release = self._release
        if sys.platform != "win32" or not release.get("asset_url"):
            webbrowser.open(release.get("html_url") or "")
            self.accept()
            return

        self._update_btn.setEnabled(False)
        self._update_btn.setText("Updating…")
        self._status_lbl.setText("Preparing update…")
        self._progress.setRange(0, 0)
        self._progress.show()
        threading.Thread(target=self._run_update, daemon=True).start()

    def _run_update(self):
        """Worker thread. Talks to the GUI only through signals."""
        release = self._release
        try:
            import tempfile
            from update_checker import download_asset
            tmp_dir = Path(tempfile.gettempdir())
            installer_path = tmp_dir / release["asset_name"]

            self._status_changed.emit("Downloading update…")
            def _on_pct(pct):
                self._progress_changed.emit(pct)
                self._status_changed.emit(f"Downloading update… {pct}%")
            download_asset(
                release["asset_url"], installer_path,
                progress_cb=_on_pct,
            )
            # Servers that omit Content-Length never call progress_cb — make
            # sure the bar still reflects a finished download.
            self._progress_changed.emit(100)

            self._status_changed.emit("Closing app to install…")
            from update_installer import launch_after_current_process_exits
            launch_after_current_process_exits(installer_path)
            self._install_started.emit()
        except Exception as e:
            self._install_failed.emit(str(e))

    # ── GUI-thread slots ─────────────────────────────────────────────────────

    def _on_status_changed(self, text: str):
        self._status_lbl.setText(text)
        if text.startswith("Closing"):
            self._progress.setRange(0, 0)

    def _on_progress_changed(self, pct: int):
        if self._progress.maximum() == 0:
            self._progress.setRange(0, 100)
        self._progress.setValue(pct)

    def _on_install_started(self):
        self._status_lbl.setText("Closing to finish the update…")
        self.accept()
        # Safe here: this runs on the GUI thread, which has an event loop. The
        # pause lets Setup get going before we release the .exe.
        QTimer.singleShot(1200, self._quit_app)

    def _on_install_failed(self, message: str):
        self._status_lbl.setText(f"⚠  Update failed: {message}")
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.hide()
        self._update_btn.setEnabled(True)
        button_text = (
            "Restart && Install"
            if self._release.get("force_install")
            else "Restart && Update"
        )
        self._update_btn.setText(button_text)

    def _quit_app(self):
        """Close via the main window so its closeEvent runs.

        QApplication.quit() only stops the event loop — closeEvent never fires,
        which would skip panel.save_state() and save_config() and quietly lose
        the user's entered URLs and API key on every update.
        """
        window = self._parent_window
        if window is not None and hasattr(window, "close"):
            window.close()
            return
        from PySide6.QtWidgets import QApplication
        QApplication.quit()
