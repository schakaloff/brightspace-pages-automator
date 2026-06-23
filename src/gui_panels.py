# src/gui_panels.py
import asyncio
import os
import queue
import threading
import webbrowser
from pathlib import Path

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QLineEdit, QFrame, QScrollArea,
)
from PySide6.QtCore import Qt, Signal, QTimer

from gui_styles import (
    BG, PANEL, BORDER, TEXT_PRI, TEXT_SEC, TEXT_FAINT,
    OC_TEAL, OC_ORANGE,
)
from gui_log import LogWidget
from gui_icons import make_icon


# ── Shared helpers (used by Tasks 8 and 9 as well) ───────────────────────────

def _divider() -> QFrame:
    """Return a thin horizontal rule styled to BORDER color."""
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    line.setStyleSheet(f"color:{BORDER};background:{BORDER};max-height:1px;")
    return line


def _form_label(text: str) -> QLabel:
    """Return an upper-case form-field label with the 'form-label' role."""
    lbl = QLabel(text)
    lbl.setProperty("role", "form-label")
    return lbl


def _section_header(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setProperty("role", "header")
    return lbl


# ── SettingsPanel ─────────────────────────────────────────────────────────────

class SettingsPanel(QWidget):
    """Scrollable settings panel with API key, downloads folder, and guide."""

    api_key_changed = Signal(str)

    def __init__(self, main_window, parent=None):
        super().__init__(parent)
        self._mw = main_window
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.timeout.connect(self._save_api_key)
        self._build()

    # ── Build UI ──────────────────────────────────────────────────────────────

    def _build(self):
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        container = QWidget()
        scroll.setWidget(container)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

        layout = QVBoxLayout(container)
        layout.setContentsMargins(32, 28, 32, 28)
        layout.setSpacing(0)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        # ── Header ────────────────────────────────────────────────────────────
        layout.addWidget(_section_header("Settings"))
        sub = QLabel("Shared configuration for all tabs.")
        sub.setProperty("role", "dim")
        layout.addWidget(sub)
        layout.addSpacing(24)
        layout.addWidget(_divider())
        layout.addSpacing(20)

        # ── Section 1: Gemini API Key ─────────────────────────────────────────
        layout.addWidget(_form_label("GEMINI API KEY"))
        layout.addSpacing(6)

        key_row = QHBoxLayout()
        key_row.setSpacing(8)

        self._key_field = QLineEdit()
        self._key_field.setPlaceholderText("AIza…")
        self._key_field.setEchoMode(QLineEdit.EchoMode.Password)
        self._key_field.setFixedHeight(40)
        self._key_field.textChanged.connect(self._on_key_changed)
        key_row.addWidget(self._key_field)

        show_btn = QPushButton("Show")
        show_btn.setProperty("variant", "secondary")
        show_btn.setFixedSize(60, 40)
        show_btn.setCheckable(True)
        show_btn.toggled.connect(
            lambda on: self._key_field.setEchoMode(
                QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password
            )
        )
        show_btn.toggled.connect(lambda on: show_btn.setText("Hide" if on else "Show"))
        key_row.addWidget(show_btn)
        layout.addLayout(key_row)

        hint = QLabel("Used by Collect and Restyle tabs. Saved automatically.")
        hint.setProperty("role", "dim")
        hint.setWordWrap(True)
        layout.addSpacing(6)
        layout.addWidget(hint)
        layout.addSpacing(24)
        layout.addWidget(_divider())
        layout.addSpacing(20)

        # ── Section 2: Downloads Folder ───────────────────────────────────────
        layout.addWidget(_form_label("DOWNLOADS FOLDER"))
        layout.addSpacing(6)

        dl_row = QHBoxLayout()
        downloads_path = Path(__file__).parent.parent / "downloads"
        path_lbl = QLabel(str(downloads_path))
        path_lbl.setProperty("role", "dim")
        path_lbl.setStyleSheet(
            "font-family: 'Consolas', monospace; font-size: 11px;"
        )
        dl_row.addWidget(path_lbl, 1)

        open_btn = QPushButton("Open Folder")
        open_btn.setProperty("variant", "secondary")
        open_btn.setFixedHeight(36)
        open_btn.clicked.connect(
            lambda: os.startfile(
                str(downloads_path) if downloads_path.exists()
                else str(downloads_path.parent)
            )
        )
        dl_row.addWidget(open_btn)
        layout.addLayout(dl_row)
        layout.addSpacing(24)
        layout.addWidget(_divider())
        layout.addSpacing(20)

        # ── Section 3: Workflow Guide ─────────────────────────────────────────
        layout.addWidget(_form_label("WORKFLOW GUIDE"))
        layout.addSpacing(6)

        guide_btn = QPushButton("Open Full Visual Guide in Browser")
        guide_btn.setFixedHeight(42)
        guide_path = Path(__file__).parent.parent / "WORKFLOW_GUIDE.html"
        guide_btn.clicked.connect(
            lambda: webbrowser.open(
                f"file:///{str(guide_path).replace(os.sep, '/')}"
            )
        )
        layout.addWidget(guide_btn)
        layout.addSpacing(8)

        guide_hint = QLabel("Detailed step-by-step flowchart — shareable and printable.")
        guide_hint.setProperty("role", "dim")
        layout.addWidget(guide_hint)
        layout.addSpacing(24)
        layout.addWidget(_divider())
        layout.addSpacing(20)

        # ── Version footer ────────────────────────────────────────────────────
        ver = QLabel("Brightspace Pages Automator  v0.8.0")
        ver.setStyleSheet(f"color:{TEXT_FAINT}; font-size:11px;")
        layout.addWidget(ver)
        layout.addStretch()

    # ── Public API ────────────────────────────────────────────────────────────

    def set_api_key(self, key: str):
        """Set the API key field without emitting api_key_changed."""
        self._key_field.blockSignals(True)
        self._key_field.setText(key)
        self._key_field.blockSignals(False)

    # ── Private slots ─────────────────────────────────────────────────────────

    def _on_key_changed(self, key: str):
        self.api_key_changed.emit(key)
        self._save_timer.start(500)

    def _save_api_key(self):
        if hasattr(self._mw, "save_config"):
            self._mw.save_config({"gemini_api_key": self._key_field.text().strip()})


# ── CheckerPanel ──────────────────────────────────────────────────────────────

class CheckerPanel(QWidget):
    step_success = Signal()
    continue_next = Signal()

    def __init__(self, main_window, parent=None):
        super().__init__(parent)
        self._mw = main_window
        self._log_queue: queue.Queue = queue.Queue()
        self._moodle_ready_event = None
        self._h5p_ready_event    = None
        self._file_checklist_event = None
        self._h5p_skip_flag      = [False]
        self._build()
        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._poll_log)
        self._poll_timer.start(100)

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 28, 32, 20)
        layout.setSpacing(0)

        layout.addWidget(_section_header("Content Checker"))
        sub = QLabel("Verify that Moodle content exists in Brightspace — leave either URL blank to test just that side.")
        sub.setProperty("role", "dim"); sub.setWordWrap(True)
        layout.addWidget(sub)
        layout.addSpacing(20)

        layout.addWidget(_form_label("BRIGHTSPACE COURSE URL"))
        layout.addSpacing(4)
        self._bs_entry = QLineEdit()
        self._bs_entry.setPlaceholderText("https://learn.okanagancollege.ca/d2l/le/content/<id>/home")
        self._bs_entry.setFixedHeight(40)
        layout.addWidget(self._bs_entry)
        layout.addSpacing(12)

        layout.addWidget(_form_label("MOODLE COURSE URL"))
        layout.addSpacing(4)
        self._moodle_entry = QLineEdit()
        self._moodle_entry.setPlaceholderText("https://mymoodle.okanagan.bc.ca/course/view.php?id=…")
        self._moodle_entry.setFixedHeight(40)
        layout.addWidget(self._moodle_entry)
        layout.addSpacing(14)

        from PySide6.QtWidgets import QCheckBox
        self._relink_cb   = QCheckBox("Re-link Moodle files in Brightspace after check")
        self._pdf_cb      = QCheckBox("Upload missing PDFs / files to Brightspace")
        self._h5p_cb      = QCheckBox("Upload H5P to Brightspace")
        self._relink_cb.setChecked(True)
        self._pdf_cb.setChecked(True)
        for cb in (self._relink_cb, self._pdf_cb, self._h5p_cb):
            layout.addWidget(cb)
            layout.addSpacing(4)
        layout.addSpacing(10)

        # Run buttons row
        btn_row = QHBoxLayout(); btn_row.setSpacing(8)
        self._run_btn = QPushButton("Run Check")
        self._run_btn.setFixedHeight(42)
        self._run_btn.setIcon(make_icon("run", "#ffffff", 14))
        self._run_btn.clicked.connect(self._start_run)
        btn_row.addWidget(self._run_btn, 3)

        self._phase_b_btn = QPushButton("Phase B — H5P Upload")
        self._phase_b_btn.setProperty("variant", "phase-b")
        self._phase_b_btn.setFixedHeight(42)
        self._phase_b_btn.clicked.connect(self._start_phase_b)
        btn_row.addWidget(self._phase_b_btn, 1)
        layout.addLayout(btn_row)
        layout.addSpacing(8)

        # Pause-point buttons (hidden until needed)
        self._ready_btn = QPushButton("Ready — Scrape Now")
        self._ready_btn.setProperty("variant", "success")
        self._ready_btn.setFixedHeight(38)
        self._ready_btn.hide()
        layout.addWidget(self._ready_btn)

        h5p_row = QHBoxLayout(); h5p_row.setSpacing(8)
        self._h5p_ready_btn = QPushButton("Ready — Download H5P")
        self._h5p_ready_btn.setProperty("variant", "success")
        self._h5p_ready_btn.setFixedHeight(38)
        self._h5p_ready_btn.hide()
        self._h5p_skip_btn = QPushButton("Skip H5P")
        self._h5p_skip_btn.setProperty("variant", "secondary")
        self._h5p_skip_btn.setFixedWidth(120)
        self._h5p_skip_btn.setFixedHeight(38)
        self._h5p_skip_btn.hide()
        h5p_row.addWidget(self._h5p_ready_btn, 1)
        h5p_row.addWidget(self._h5p_skip_btn)
        layout.addLayout(h5p_row)
        layout.addSpacing(8)

        layout.addWidget(_form_label("LOG"))
        layout.addSpacing(4)

        self._log = LogWidget()
        layout.addWidget(self._log, 1)
        layout.addSpacing(8)

        # Downloads path (hidden until run completes)
        self._dl_label = QLabel()
        self._dl_label.setStyleSheet("font-family:Consolas,monospace;font-size:11px;")
        self._dl_label.setProperty("role", "dim")
        self._dl_label.hide()
        layout.addWidget(self._dl_label)

        # Continue button (hidden until success)
        self._continue_btn = QPushButton("Continue to Unit Collector")
        self._continue_btn.setProperty("variant", "next-step")
        self._continue_btn.setFixedHeight(38)
        self._continue_btn.setIcon(make_icon("next", "#dde0ee", 14))
        self._continue_btn.hide()
        self._continue_btn.clicked.connect(self.continue_next)
        layout.addWidget(self._continue_btn)

        # Load saved URLs
        cfg = self._mw.load_config() if hasattr(self._mw, "load_config") else {}
        if cfg.get("chk_bs_url"):
            self._bs_entry.setText(cfg["chk_bs_url"])
        if cfg.get("chk_moodle_url"):
            self._moodle_entry.setText(cfg["chk_moodle_url"])

    def _run_worker(self, phase_b: bool = False):
        bs_url     = self._bs_entry.text().strip()
        moodle_url = self._moodle_entry.text().strip()
        if not bs_url and not moodle_url:
            self._log.append_log("Paste at least one URL.", "warning")
            return
        if phase_b and not bs_url:
            self._log.append_log("Paste a Brightspace URL first.", "warning")
            return

        self._mw.save_config({"chk_bs_url": bs_url, "chk_moodle_url": moodle_url})

        import threading as _t
        moodle_ev = _t.Event(); h5p_ev = _t.Event(); file_ev = _t.Event()
        file_result = []
        skip_flag   = [False]
        self._moodle_ready_event   = moodle_ev
        self._h5p_ready_event      = h5p_ev
        self._file_checklist_event = file_ev
        self._h5p_skip_flag        = skip_flag

        self._ready_btn.hide()
        self._h5p_ready_btn.hide()
        self._h5p_skip_btn.hide()
        self._continue_btn.hide()
        self._dl_label.hide()

        self._run_btn.setText("Running…"); self._run_btn.setEnabled(False)
        self._phase_b_btn.setEnabled(False)
        self._log.clear_log()

        q = self._log_queue

        def confirm(msg: str) -> bool:
            from PySide6.QtWidgets import QMessageBox
            result = [False]; ev = _t.Event()
            def ask():
                result[0] = QMessageBox.question(
                    self, "Continue?", msg,
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                ) == QMessageBox.StandardButton.Yes
                ev.set()
            QTimer.singleShot(0, ask)
            ev.wait()
            return result[0]

        def worker():
            done_sent = [False]
            def on_done():
                if not done_sent[0]:
                    done_sent[0] = True
                    q.put(("__DONE__", ""))
            try:
                from content_checker import ContentChecker
                checker = ContentChecker(
                    bs_url=bs_url,
                    moodle_url=moodle_url,
                    log=lambda msg, tag="info": q.put((msg, tag)),
                    on_complete=on_done,
                    moodle_ready_event=moodle_ev,
                    on_moodle_waiting=lambda: q.put(("__CHK_MOODLE_WAITING__", "")),
                    h5p_ready_event=h5p_ev,
                    on_h5p_waiting=lambda: q.put(("__CHK_H5P_WAITING__", skip_flag)),
                    file_checklist_event=file_ev,
                    on_file_checklist=lambda d: q.put(("__CHK_FILE_CHECKLIST__", (d, file_result, file_ev))),
                    confirm_fn=confirm,
                )
                checker.do_relink     = self._relink_cb.isChecked()
                checker.do_pdf_upload = self._pdf_cb.isChecked()
                checker.do_h5p_embed  = self._h5p_cb.isChecked()
                checker.file_checklist_result = file_result
                checker.h5p_skip_flag = skip_flag
                if phase_b:
                    checker.do_relink = False
                    checker.do_h5p_embed = True
                    checker.h5p_phase_b_only = True
                asyncio.run(checker.run())
            except Exception as e:
                q.put((f"Error: {e}", "error"))
            finally:
                on_done()

        threading.Thread(target=worker, daemon=True).start()

    def _start_run(self):
        if not self._mw.chromium_ready:
            self._log.append_log("Browser engine still installing — please wait.", "warning")
            return
        self._run_worker(phase_b=False)

    def _start_phase_b(self):
        if not self._mw.chromium_ready:
            self._log.append_log("Browser engine still installing — please wait.", "warning")
            return
        self._run_worker(phase_b=True)

    def _poll_log(self):
        try:
            while True:
                msg, tag = self._log_queue.get_nowait()
                if msg == "__DONE__":
                    self._run_btn.setText("Run Check"); self._run_btn.setEnabled(True)
                    self._phase_b_btn.setEnabled(True)
                    self._ready_btn.hide()
                    self._h5p_ready_btn.hide(); self._h5p_skip_btn.hide()
                    dl = Path(__file__).parent.parent / "downloads"
                    self._dl_label.setText(f"Downloads: {dl}")
                    self._dl_label.show()
                elif msg == "__SUCCESS__":
                    self._continue_btn.show()
                    self.step_success.emit()
                elif msg == "__CHK_MOODLE_WAITING__":
                    self._ready_btn.setText("Ready — Scrape Now")
                    self._ready_btn.clicked.disconnect() if self._ready_btn.receivers(self._ready_btn.clicked) > 0 else None
                    self._ready_btn.clicked.connect(self._moodle_ready)
                    self._ready_btn.show()
                elif msg == "__CHK_H5P_WAITING__":
                    self._h5p_ready_btn.show(); self._h5p_skip_btn.show()
                    try:
                        self._h5p_ready_btn.clicked.disconnect()
                    except RuntimeError:
                        pass
                    try:
                        self._h5p_skip_btn.clicked.disconnect()
                    except RuntimeError:
                        pass
                    self._h5p_ready_btn.clicked.connect(self._h5p_ready)
                    self._h5p_skip_btn.clicked.connect(self._h5p_skip)
                elif msg == "__CHK_FILE_CHECKLIST__":
                    data_json, result_list, event = tag
                    from gui_dialogs import FileChecklistDialog
                    dlg = FileChecklistDialog(data_json, result_list, event, self)
                    dlg.exec()
                else:
                    self._log.append_log(msg, tag)
        except queue.Empty:
            pass

    def _moodle_ready(self):
        self._ready_btn.hide()
        if self._moodle_ready_event:
            self._moodle_ready_event.set()

    def _h5p_ready(self):
        self._h5p_ready_btn.hide(); self._h5p_skip_btn.hide()
        if self._h5p_ready_event:
            self._h5p_ready_event.set()

    def _h5p_skip(self):
        self._h5p_ready_btn.hide(); self._h5p_skip_btn.hide()
        self._h5p_skip_flag[0] = True
        if self._h5p_ready_event:
            self._h5p_ready_event.set()


# ── CollectorPanel (stub — implemented in Task 9) ─────────────────────────────

class CollectorPanel(QWidget):
    step_success = Signal()
    continue_next = Signal()

    def __init__(self, main_window, parent=None):
        super().__init__(parent)
        self._mw = main_window
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 28, 32, 20)
        layout.addWidget(_section_header("Unit Collector"))
        placeholder = QLabel("Unit Collector panel — coming in Task 9.")
        placeholder.setProperty("role", "dim")
        layout.addWidget(placeholder)
        layout.addStretch()


# ── RestylePanel (stub — implemented in Task 10) ──────────────────────────────

class RestylePanel(QWidget):
    step_success = Signal()
    continue_next = Signal()

    def __init__(self, main_window, parent=None):
        super().__init__(parent)
        self._mw = main_window
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 28, 32, 20)
        layout.addWidget(_section_header("Restyle"))
        placeholder = QLabel("Restyle panel — coming in Task 10.")
        placeholder.setProperty("role", "dim")
        layout.addWidget(placeholder)
        layout.addStretch()
