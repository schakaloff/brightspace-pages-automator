import os
import sys
import webbrowser
from pathlib import Path

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QLineEdit, QFrame, QScrollArea, QComboBox,
)
from PySide6.QtCore import Qt, Signal, QTimer

from panels._shared import _divider, _form_label, _section_header


class SettingsPanel(QWidget):
    """Scrollable settings panel with API key, downloads folder, and guide."""

    api_key_changed = Signal(str)
    model_changed = Signal(str)

    MODELS = [
        ("claude-opus-4-5", "Opus 4.5 — highest quality, slower/pricier"),
        ("claude-sonnet-5", "Sonnet 5 — best quality/cost balance (recommended)"),
        ("claude-haiku-4-5", "Haiku 4.5 — cheapest/fastest"),
    ]

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

        # ── Section 0: Appearance ─────────────────────────────────────────────
        layout.addWidget(_form_label("APPEARANCE"))
        layout.addSpacing(6)

        theme_row = QHBoxLayout()
        theme_row.setSpacing(8)
        theme_row.setAlignment(Qt.AlignmentFlag.AlignLeft)

        self._btn_dark = QPushButton("Dark")
        self._btn_dark.setProperty("variant", "secondary")
        self._btn_dark.setFixedSize(80, 36)
        self._btn_dark.setToolTip("Switch to dark theme.")
        self._btn_dark.clicked.connect(lambda: self._mw.set_theme("dark"))

        self._btn_light = QPushButton("Light")
        self._btn_light.setProperty("variant", "secondary")
        self._btn_light.setFixedSize(80, 36)
        self._btn_light.setToolTip("Switch to light theme.")
        self._btn_light.clicked.connect(lambda: self._mw.set_theme("light"))

        theme_row.addWidget(self._btn_dark)
        theme_row.addWidget(self._btn_light)
        layout.addLayout(theme_row)
        layout.addSpacing(24)
        layout.addWidget(_divider())
        layout.addSpacing(20)

        # ── Section 1: Claude API Key ─────────────────────────────────────────
        layout.addWidget(_form_label("CLAUDE API KEY"))
        layout.addSpacing(6)

        key_row = QHBoxLayout()
        key_row.setSpacing(8)

        self._key_field = QLineEdit()
        self._key_field.setPlaceholderText("sk-ant-…")
        self._key_field.setEchoMode(QLineEdit.EchoMode.Password)
        self._key_field.setFixedHeight(40)
        self._key_field.setToolTip(
            "Your Anthropic Claude API key. Required for the Collect and Restyle tabs.\n"
            "Get one at console.anthropic.com. Saved automatically."
        )
        self._key_field.textChanged.connect(self._on_key_changed)
        key_row.addWidget(self._key_field)

        show_btn = QPushButton("Show")
        show_btn.setProperty("variant", "secondary")
        show_btn.setFixedSize(72, 40)
        show_btn.setToolTip("Toggle API key visibility.")
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
        layout.addSpacing(16)

        layout.addWidget(_form_label("MODEL"))
        layout.addSpacing(6)

        self._model_combo = QComboBox()
        self._model_combo.setFixedHeight(40)
        for model_id, label in self.MODELS:
            self._model_combo.addItem(label, model_id)
        self._model_combo.currentIndexChanged.connect(self._on_model_changed)
        layout.addWidget(self._model_combo)
        layout.addSpacing(24)
        layout.addWidget(_divider())
        layout.addSpacing(20)

        # ── Section 2: Downloads Folder ───────────────────────────────────────
        layout.addWidget(_form_label("DOWNLOADS FOLDER"))
        layout.addSpacing(6)

        dl_row = QHBoxLayout()
        downloads_path = Path(__file__).parent.parent.parent / "downloads"
        path_lbl = QLabel(str(downloads_path))
        path_lbl.setProperty("role", "dim")
        path_lbl.setStyleSheet(
            "font-family: 'Consolas', monospace; font-size: 11px;"
        )
        dl_row.addWidget(path_lbl, 1)

        open_btn = QPushButton("Open Folder")
        open_btn.setProperty("variant", "secondary")
        open_btn.setFixedHeight(40)
        open_btn.setToolTip("Open the downloads folder in your file manager.")
        def _open_folder():
            p = str(downloads_path) if downloads_path.exists() else str(downloads_path.parent)
            if sys.platform == "win32":
                os.startfile(p)
            elif sys.platform == "darwin":
                import subprocess; subprocess.Popen(["open", p])
            else:
                import subprocess; subprocess.Popen(["xdg-open", p])
        open_btn.clicked.connect(_open_folder)
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
        guide_btn.setToolTip("Open the step-by-step workflow guide in your browser. Shareable and printable.")
        guide_path = Path(__file__).parent.parent.parent / "WORKFLOW_GUIDE.html"
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

        # ── Section 3: Brightspace Credentials ───────────────────────────────
        layout.addWidget(_form_label("BRIGHTSPACE CREDENTIALS"))
        layout.addSpacing(4)
        bs_hint = QLabel("Used for direct login. If SSO is configured below, SSO takes priority.")
        bs_hint.setProperty("role", "dim")
        bs_hint.setWordWrap(True)
        layout.addWidget(bs_hint)
        layout.addSpacing(6)

        self._bs_user_field = QLineEdit()
        self._bs_user_field.setPlaceholderText("n.firstname")
        self._bs_user_field.setFixedHeight(40)
        self._bs_user_field.setToolTip("Your Brightspace username (e.g. n.firstname).")
        layout.addWidget(self._bs_user_field)
        layout.addSpacing(6)

        self._bs_pass_field = QLineEdit()
        self._bs_pass_field.setPlaceholderText("Password")
        self._bs_pass_field.setEchoMode(QLineEdit.EchoMode.Password)
        self._bs_pass_field.setFixedHeight(40)
        self._bs_pass_field.setToolTip("Your Brightspace password. Stored securely in your system keyring.")
        layout.addWidget(self._bs_pass_field)
        layout.addSpacing(6)

        self._bs_cred_status = QLabel("")
        self._bs_cred_status.setProperty("role", "dim")
        layout.addWidget(self._bs_cred_status)

        bs_save_btn = QPushButton("Save Brightspace Credentials")
        bs_save_btn.setProperty("variant", "secondary")
        bs_save_btn.setFixedHeight(36)
        bs_save_btn.setToolTip("Save username to config and password to your system keyring.")
        bs_save_btn.clicked.connect(self._save_bs_credentials)
        layout.addWidget(bs_save_btn)
        layout.addSpacing(24)
        layout.addWidget(_divider())
        layout.addSpacing(20)

        # ── Section 4: Microsoft SSO Credentials ─────────────────────────────
        layout.addWidget(_form_label("MICROSOFT SSO CREDENTIALS"))
        layout.addSpacing(4)
        sso_hint = QLabel("Used for Microsoft single sign-on login. Takes priority over direct Brightspace login.")
        sso_hint.setProperty("role", "dim")
        sso_hint.setWordWrap(True)
        layout.addWidget(sso_hint)
        layout.addSpacing(6)

        self._sso_email_field = QLineEdit()
        self._sso_email_field.setPlaceholderText("NFirstname.Lastname@okanagan.bc.ca")
        self._sso_email_field.setFixedHeight(40)
        self._sso_email_field.setToolTip("Your Microsoft SSO email address (e.g. NFirstname.Lastname@okanagan.bc.ca).")
        layout.addWidget(self._sso_email_field)
        layout.addSpacing(6)

        self._sso_pass_field = QLineEdit()
        self._sso_pass_field.setPlaceholderText("Password")
        self._sso_pass_field.setEchoMode(QLineEdit.EchoMode.Password)
        self._sso_pass_field.setFixedHeight(40)
        self._sso_pass_field.setToolTip("Your Microsoft SSO password. Stored securely in your system keyring.")
        layout.addWidget(self._sso_pass_field)
        layout.addSpacing(6)

        self._sso_status = QLabel("")
        self._sso_status.setProperty("role", "dim")
        layout.addWidget(self._sso_status)

        sso_save_btn = QPushButton("Save SSO Credentials")
        sso_save_btn.setProperty("variant", "secondary")
        sso_save_btn.setFixedHeight(36)
        sso_save_btn.setToolTip("Save SSO email and password to your system keyring.")
        sso_save_btn.clicked.connect(self._save_sso_credentials)
        layout.addWidget(sso_save_btn)
        layout.addSpacing(24)
        layout.addWidget(_divider())
        layout.addSpacing(20)

        # ── Section 5: Moodle Credentials ────────────────────────────────────
        layout.addWidget(_form_label("MOODLE CREDENTIALS"))
        layout.addSpacing(4)
        moodle_hint = QLabel("Used by the Checker and Kaltura tabs to access your Moodle course content.")
        moodle_hint.setProperty("role", "dim")
        moodle_hint.setWordWrap(True)
        layout.addWidget(moodle_hint)
        layout.addSpacing(6)

        self._moodle_user_field = QLineEdit()
        self._moodle_user_field.setPlaceholderText("n.firstname")
        self._moodle_user_field.setFixedHeight(40)
        self._moodle_user_field.setToolTip("Your Moodle username.")
        layout.addWidget(self._moodle_user_field)
        layout.addSpacing(6)

        self._moodle_pass_field = QLineEdit()
        self._moodle_pass_field.setPlaceholderText("Password")
        self._moodle_pass_field.setEchoMode(QLineEdit.EchoMode.Password)
        self._moodle_pass_field.setFixedHeight(40)
        self._moodle_pass_field.setToolTip("Your Moodle password. Stored securely in your system keyring.")
        layout.addWidget(self._moodle_pass_field)
        layout.addSpacing(6)

        self._moodle_status = QLabel("")
        self._moodle_status.setProperty("role", "dim")
        layout.addWidget(self._moodle_status)

        moodle_save_btn = QPushButton("Save Moodle Credentials")
        moodle_save_btn.setProperty("variant", "secondary")
        moodle_save_btn.setFixedHeight(36)
        moodle_save_btn.setToolTip("Save Moodle username and password to your system keyring.")
        moodle_save_btn.clicked.connect(self._save_moodle_credentials)
        layout.addWidget(moodle_save_btn)
        layout.addSpacing(24)
        layout.addWidget(_divider())
        layout.addSpacing(20)

        # ── Section 6: KMC (Kaltura) Credentials ─────────────────────────────
        layout.addWidget(_form_label("KMC CREDENTIALS"))
        layout.addSpacing(6)

        self._kmc_user_field = QLineEdit()
        self._kmc_user_field.setPlaceholderText("NFirstname.Lastname@okanagan.bc.ca")
        self._kmc_user_field.setFixedHeight(40)
        layout.addWidget(self._kmc_user_field)
        layout.addSpacing(6)

        self._kmc_pass_field = QLineEdit()
        self._kmc_pass_field.setPlaceholderText("Password")
        self._kmc_pass_field.setEchoMode(QLineEdit.EchoMode.Password)
        self._kmc_pass_field.setFixedHeight(40)
        layout.addWidget(self._kmc_pass_field)
        layout.addSpacing(6)

        self._kmc_status = QLabel("")
        self._kmc_status.setProperty("role", "dim")
        layout.addWidget(self._kmc_status)

        kmc_save_btn = QPushButton("Save KMC Credentials")
        kmc_save_btn.setProperty("variant", "secondary")
        kmc_save_btn.setFixedHeight(36)
        kmc_save_btn.clicked.connect(self._save_kmc_credentials)
        layout.addWidget(kmc_save_btn)
        layout.addSpacing(24)
        layout.addWidget(_divider())
        layout.addSpacing(20)

        # ── Version footer ────────────────────────────────────────────────────
        ver = QLabel("Brightspace Pages Automator  v0.8.0")
        ver.setProperty("role", "dim")
        ver.setStyleSheet("font-size:11px;")
        layout.addWidget(ver)
        layout.addStretch()

        self._load_credentials()

    # ── Public API ────────────────────────────────────────────────────────────

    def mark_active_theme(self, name: str):
        """Highlight the active theme button."""
        for btn, theme in ((self._btn_dark, "dark"), (self._btn_light, "light")):
            active = theme == name
            btn.setProperty("variant", "theme-active" if active else "secondary")
            btn.style().unpolish(btn)
            btn.style().polish(btn)

    def set_api_key(self, key: str):
        """Set the API key field without emitting api_key_changed."""
        self._key_field.blockSignals(True)
        self._key_field.setText(key)
        self._key_field.blockSignals(False)

    def set_model(self, model_id: str):
        """Set the model dropdown without emitting model_changed."""
        idx = self._model_combo.findData(model_id)
        if idx < 0:
            idx = 0
        self._model_combo.blockSignals(True)
        self._model_combo.setCurrentIndex(idx)
        self._model_combo.blockSignals(False)

    @property
    def bs_username(self) -> str:
        return self._mw.load_config().get("bs_username", "")

    @property
    def bs_password(self) -> str:
        import keyring
        u = self.bs_username
        return keyring.get_password("BrightspacePagesAutomator", u) or "" if u else ""

    @property
    def sso_email(self) -> str:
        return self._mw.load_config().get("sso_email", "")

    @property
    def sso_password(self) -> str:
        import keyring
        e = self.sso_email
        return keyring.get_password("BrightspacePagesAutomator_SSO", e) or "" if e else ""

    @property
    def moodle_username(self) -> str:
        return self._mw.load_config().get("moodle_username", "")

    @property
    def moodle_password(self) -> str:
        import keyring
        u = self.moodle_username
        return keyring.get_password("BrightspacePagesAutomator_Moodle", u) or "" if u else ""

    @property
    def kmc_username(self) -> str:
        return self._mw.load_config().get("kmc_username", "")

    @property
    def kmc_password(self) -> str:
        import keyring
        u = self.kmc_username
        return keyring.get_password("BrightspacePagesAutomator_KMC", u) or "" if u else ""

    # ── Private slots ─────────────────────────────────────────────────────────

    def _on_key_changed(self, key: str):
        self.api_key_changed.emit(key)
        self._save_timer.start(500)

    def _save_api_key(self):
        if hasattr(self._mw, "save_config"):
            self._mw.save_config({"claude_api_key": self._key_field.text().strip()})

    def _on_model_changed(self, index: int):
        model_id = self._model_combo.itemData(index)
        self.model_changed.emit(model_id)
        if hasattr(self._mw, "save_config"):
            self._mw.save_config({"claude_model": model_id})

    def _load_credentials(self):
        import keyring
        cfg = self._mw.load_config() if hasattr(self._mw, "load_config") else {}

        bs_user = cfg.get("bs_username", "")
        if bs_user:
            self._bs_user_field.setText(bs_user)
            pw = keyring.get_password("BrightspacePagesAutomator", bs_user)
            if pw:
                self._bs_pass_field.setText(pw)

        sso_email = cfg.get("sso_email", "")
        if sso_email:
            self._sso_email_field.setText(sso_email)
            pw = keyring.get_password("BrightspacePagesAutomator_SSO", sso_email)
            if pw:
                self._sso_pass_field.setText(pw)

        moodle_user = cfg.get("moodle_username", "")
        if moodle_user:
            self._moodle_user_field.setText(moodle_user)
            pw = keyring.get_password("BrightspacePagesAutomator_Moodle", moodle_user)
            if pw:
                self._moodle_pass_field.setText(pw)

        kmc_user = cfg.get("kmc_username", "")
        if kmc_user:
            self._kmc_user_field.setText(kmc_user)
            pw = keyring.get_password("BrightspacePagesAutomator_KMC", kmc_user)
            if pw:
                self._kmc_pass_field.setText(pw)

    def _save_bs_credentials(self):
        import keyring
        username = self._bs_user_field.text().strip()
        password = self._bs_pass_field.text().strip()
        if not username:
            self._bs_cred_status.setText("Enter a username first.")
            return
        self._mw.save_config({"bs_username": username})
        if password:
            keyring.set_password("BrightspacePagesAutomator", username, password)
        self._bs_cred_status.setText("Saved.")
        QTimer.singleShot(3000, lambda: self._bs_cred_status.setText(""))

    def _save_sso_credentials(self):
        import keyring
        email = self._sso_email_field.text().strip()
        password = self._sso_pass_field.text().strip()
        if not email:
            self._sso_status.setText("Enter an email first.")
            return
        self._mw.save_config({"sso_email": email})
        if password:
            keyring.set_password("BrightspacePagesAutomator_SSO", email, password)
        self._sso_status.setText("Saved.")
        QTimer.singleShot(3000, lambda: self._sso_status.setText(""))

    def _save_moodle_credentials(self):
        import keyring
        username = self._moodle_user_field.text().strip()
        password = self._moodle_pass_field.text().strip()
        if not username:
            self._moodle_status.setText("Enter a username first.")
            return
        self._mw.save_config({"moodle_username": username})
        if password:
            keyring.set_password("BrightspacePagesAutomator_Moodle", username, password)
        self._moodle_status.setText("Saved.")
        QTimer.singleShot(3000, lambda: self._moodle_status.setText(""))

    def _save_kmc_credentials(self):
        import keyring
        username = self._kmc_user_field.text().strip()
        password = self._kmc_pass_field.text().strip()
        if not username:
            self._kmc_status.setText("Enter a username first.")
            return
        self._mw.save_config({"kmc_username": username})
        if password:
            keyring.set_password("BrightspacePagesAutomator_KMC", username, password)
        self._kmc_status.setText("Saved.")
        QTimer.singleShot(3000, lambda: self._kmc_status.setText(""))
