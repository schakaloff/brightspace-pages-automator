# src/gui_panels.py
import os
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
