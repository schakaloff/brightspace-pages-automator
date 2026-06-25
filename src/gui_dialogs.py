import json
import sys
import threading
import webbrowser
from pathlib import Path

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QScrollArea, QWidget, QCheckBox, QSpinBox,
    QFrame,
)
from PySide6.QtCore import Qt, QTimer


# ── FileChecklistDialog ───────────────────────────────────────────────────────

class FileChecklistDialog(QDialog):
    """Checkbox list of missing files; user picks which to download.

    Populates *result_list* in-place with selected file dicts, then sets
    *event* so the background asyncio thread can resume.  The event is
    guaranteed to be set on every exit path (OK, Skip All, window X).
    """

    def __init__(self, data_json: str, result_list: list, event: threading.Event, parent=None):
        super().__init__(parent)
        self._result_list = result_list
        self._event = event
        self._files = json.loads(data_json)
        self._checkboxes: list[tuple[QCheckBox, dict]] = []

        self.setWindowTitle("Missing Files — Select to Download")
        self.setMinimumSize(560, 480)
        self.resize(580, 540)
        self.setModal(True)

        self._build()

        if not self._files:
            self._release(selected=[])

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(8)

        title = QLabel(f"📋  {len(self._files)} file(s) missing from Brightspace")
        title.setStyleSheet("font-size:15px; font-weight:bold;")
        layout.addWidget(title)

        sub1 = QLabel("Files will be downloaded from Moodle and uploaded to the matching section.")
        sub1.setProperty("role", "dim")
        sub1.setWordWrap(True)
        layout.addWidget(sub1)

        sub2 = QLabel("Uncheck any you already have or don't need.")
        sub2.setProperty("role", "dim")
        layout.addWidget(sub2)

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

        cur_section = None
        for f in self._files:
            sec = f.get("section") or "Other"
            if sec != cur_section:
                cur_section = sec
                sec_lbl = QLabel(f"── {sec} ──")
                sec_lbl.setProperty("role", "dim")
                sec_lbl.setStyleSheet("font-size:11px; padding-top:8px;")
                inner_layout.addWidget(sec_lbl)
            cb = QCheckBox(f["name"])
            cb.setChecked(True)
            cb.toggled.connect(self._update_count)
            self._checkboxes.append((cb, f))
            inner_layout.addWidget(cb)

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

    def _update_count(self):
        n = sum(1 for cb, _ in self._checkboxes if cb.isChecked())
        self._count_lbl.setText(f"{n} of {len(self._files)} selected")
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
    """Shows release notes and offers Skip / Later / Update Now actions."""

    def __init__(self, release: dict, parent=None):
        super().__init__(parent)
        self._release = release
        self._parent_window = parent

        self.setWindowTitle("Update available")
        self.setMinimumSize(520, 360)
        self.resize(520, 420)
        self.setModal(True)
        self._build()

    def _build(self):
        from gui_log import LogWidget

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(10)

        title = QLabel(f"New version available: {self._release.get('tag', '')}")
        title.setStyleSheet("font-size:16px; font-weight:bold;")
        layout.addWidget(title)

        notes = LogWidget()
        notes.append_log(self._release.get("body", ""), "info")
        layout.addWidget(notes, 1)

        self._status_lbl = QLabel("")
        self._status_lbl.setProperty("role", "dim")
        self._status_lbl.setStyleSheet("font-size:11px;")
        layout.addWidget(self._status_lbl)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        skip_btn = QPushButton("Skip this version")
        skip_btn.setProperty("variant", "secondary")
        skip_btn.setFixedHeight(38)
        skip_btn.clicked.connect(self._on_skip)

        later_btn = QPushButton("Remind me later")
        later_btn.setProperty("variant", "secondary")
        later_btn.setFixedHeight(38)
        later_btn.clicked.connect(self.reject)

        self._update_btn = QPushButton("Update Now")
        self._update_btn.setFixedHeight(38)
        self._update_btn.clicked.connect(self._on_update)

        if not self._release.get("asset_url") and sys.platform == "win32":
            self._update_btn.setEnabled(False)
            self._status_lbl.setText("No installer found in this release.")

        btn_row.addWidget(skip_btn)
        btn_row.addWidget(later_btn)
        btn_row.addWidget(self._update_btn)
        layout.addLayout(btn_row)

    def _on_skip(self):
        if self._parent_window and hasattr(self._parent_window, "save_config"):
            self._parent_window.save_config({"skipped_update_tag": self._release.get("tag", "")})
        self.reject()

    def _on_update(self):
        release = self._release
        if sys.platform != "win32" or not release.get("asset_url"):
            webbrowser.open(release.get("html_url") or "")
            self.accept()
            return

        self._update_btn.setEnabled(False)
        self._update_btn.setText("Updating…")
        threading.Thread(target=self._run_update, daemon=True).start()

    def _run_update(self):
        release = self._release
        try:
            import tempfile
            from update_checker import download_asset
            tmp_dir = Path(tempfile.gettempdir())
            installer_path = tmp_dir / release["asset_name"]

            def set_status(text):
                QTimer.singleShot(0, lambda: self._status_lbl.setText(text))

            set_status("Downloading update…")
            download_asset(
                release["asset_url"], installer_path,
                progress_cb=lambda pct: set_status(f"Downloading update… {pct}%"),
            )
            set_status("Installing…")
            import subprocess
            subprocess.Popen(
                [str(installer_path), "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART"],
                close_fds=True,
            )
            QTimer.singleShot(0, self.accept)
        except Exception as e:
            QTimer.singleShot(0, lambda: self._status_lbl.setText(f"⚠  Update failed: {e}"))
            QTimer.singleShot(0, lambda: self._update_btn.setEnabled(True))
            QTimer.singleShot(0, lambda: self._update_btn.setText("Update Now"))
