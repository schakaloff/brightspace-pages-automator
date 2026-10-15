"""Settings categories and account forms; persistence lives in SettingsPanel."""
import os
import sys
import webbrowser
from pathlib import Path
from urllib.parse import urlparse

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QLineEdit,
    QFrame, QScrollArea, QTabWidget, QRadioButton, QButtonGroup,
)
from panels._shared import NoScrollComboBox


def label(text, role="hint"):
    widget = QLabel(text)
    widget.setProperty("role", role)
    widget.setWordWrap(True)
    return widget


def button(text, callback, variant="secondary"):
    widget = QPushButton(text)
    widget.setProperty("variant", variant)
    widget.setMinimumHeight(40)
    widget.clicked.connect(callback)
    return widget


def divider(layout):
    widget = QFrame()
    widget.setProperty("role", "settings-divider")
    widget.setFixedHeight(1)
    layout.addSpacing(12)
    layout.addWidget(widget)
    layout.addSpacing(12)


def category(tabs, title):
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    holder = QWidget()
    scroll.setWidget(holder)
    outer = QVBoxLayout(holder)
    outer.setContentsMargins(0, 26, 0, 0)
    content = QWidget()
    content.setMaximumWidth(1120)
    outer.addWidget(content)
    outer.addStretch()
    layout = QVBoxLayout(content)
    layout.setContentsMargins(0, 0, 12, 0)
    layout.setSpacing(12)
    tabs.addTab(scroll, title.replace("&", "&&"))
    return layout


def password_row(field):
    row = QHBoxLayout()
    row.setSpacing(8)
    row.addWidget(field, 1)
    show = button("Show", lambda: None)
    show.setCheckable(True)
    show.setAccessibleName("Show password")
    def toggle(on):
        field.setEchoMode(QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password)
        show.setText("Hide" if on else "Show")
        show.setAccessibleName("Hide password" if on else "Show password")
    show.toggled.connect(toggle)
    row.addWidget(show)
    return row


def field(layout, text, placeholder="", password=False):
    name = label(text, "field-label")
    edit = QLineEdit()
    edit.setPlaceholderText(placeholder)
    edit.setMinimumHeight(44)
    edit.setAccessibleName(text)
    name.setBuddy(edit)
    layout.addWidget(name)
    if password:
        edit.setEchoMode(QLineEdit.EchoMode.Password)
        layout.addLayout(password_row(edit))
    else:
        layout.addWidget(edit)
    return edit


def account_form(panel, key, user_label, placeholder, save_callback):
    widget = QWidget()
    layout = QVBoxLayout(widget)
    layout.setContentsMargins(0, 8, 0, 6)
    layout.setSpacing(10)
    user = field(layout, user_label, placeholder)
    password = field(layout, "Password", password=True)
    status = label("", "account-status")
    layout.addWidget(status)
    actions = QHBoxLayout()
    actions.addWidget(button("Save changes", save_callback, "primary"))
    actions.addWidget(button("Remove saved credentials", lambda: panel._remove_credentials(key), "link"))
    actions.addStretch()
    layout.addLayout(actions)
    layout.addWidget(label("Password stored in your system credential store. To remove it, use Remove saved credentials."))
    panel._account_fields[key] = (user, password, status)
    panel._account_forms[key] = widget
    user.textEdited.connect(lambda: panel._mark_account_dirty(key))
    password.textEdited.connect(lambda: panel._mark_account_dirty(key))
    return widget


def build_settings(panel):
    panel.setObjectName("settings_panel")
    panel.setProperty("design", "modern")
    panel.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    panel._account_fields = {}
    panel._account_forms = {}
    panel._account_summaries = {}
    panel._account_edit_buttons = {}
    root = QVBoxLayout(panel)
    root.setContentsMargins(32, 28, 32, 24)
    root.setSpacing(12)
    root.addWidget(label("Settings", "page-heading"))
    root.addWidget(label("Manage accounts and app preferences.", "dim"))
    panel._tabs = QTabWidget()
    panel._tabs.setObjectName("settings_categories")
    panel._tabs.setUsesScrollButtons(True)
    root.addWidget(panel._tabs, 1)

    accounts = category(panel._tabs, "Accounts")
    accounts.addWidget(label("Brightspace sign-in", "section-heading"))
    accounts.addWidget(label("Choose how you sign in to Brightspace. Credentials save with the Save button.", "dim"))
    method_row = QHBoxLayout()
    panel._sso_radio = QRadioButton("Microsoft SSO")
    panel._direct_radio = QRadioButton("Direct login")
    panel._login_group = QButtonGroup(panel)
    for radio in (panel._sso_radio, panel._direct_radio):
        panel._login_group.addButton(radio)
        method_row.addWidget(radio)
    method_row.addStretch()
    accounts.addLayout(method_row)
    sso = account_form(panel, "sso", "Email", "name@okanagan.bc.ca", panel._save_sso_credentials)
    direct = account_form(panel, "bs", "Username", "n.firstname", panel._save_bs_credentials)
    direct.layout().addWidget(button("Import from Quiz Automator", panel._import_bs_from_quiz_automator, "link"), 0, Qt.AlignmentFlag.AlignLeft)
    accounts.addWidget(sso)
    accounts.addWidget(direct)
    panel._sso_email_field, panel._sso_pass_field, panel._sso_status = panel._account_fields["sso"]
    panel._bs_user_field, panel._bs_pass_field, panel._bs_cred_status = panel._account_fields["bs"]
    panel._sso_radio.toggled.connect(lambda on: panel._choose_login_method("sso") if on else None)
    panel._direct_radio.toggled.connect(lambda on: panel._choose_login_method("direct") if on else None)

    for key, title, hint, placeholder, callback in (
        ("moodle", "Moodle", "For Checker, Collect and Kaltura", "n.firstname", panel._save_moodle_credentials),
        ("kmc", "Kaltura Management Console", "For Kaltura workflows", "name@okanagan.bc.ca", panel._save_kmc_credentials),
    ):
        divider(accounts)
        row = QHBoxLayout()
        text = QVBoxLayout()
        text.addWidget(label(title, "section-heading"))
        text.addWidget(label(hint))
        row.addLayout(text, 1)
        status = label("Not configured", "account-status")
        row.addWidget(status)
        edit = button("Set up", lambda checked=False, k=key: panel._toggle_account_form(k))
        row.addWidget(edit)
        accounts.addLayout(row)
        form = account_form(panel, key, "Email" if key == "kmc" else "Username", placeholder, callback)
        form.hide()
        accounts.addWidget(form)
        panel._account_summaries[key] = status
        panel._account_edit_buttons[key] = edit
    panel._moodle_user_field, panel._moodle_pass_field, panel._moodle_status = panel._account_fields["moodle"]
    panel._kmc_user_field, panel._kmc_pass_field, panel._kmc_status = panel._account_fields["kmc"]
    divider(accounts)
    accounts.addWidget(label("Course links are managed in each workflow."))

    ai = category(panel._tabs, "AI")
    ai.addWidget(label("Claude", "section-heading"))
    ai.addWidget(label("Used to style pages in Collect and Restyle. API key and model changes save automatically.", "dim"))
    panel._key_field = field(ai, "API key", "sk-ant-…", password=True)
    panel._key_status = label("No API key saved", "account-status")
    ai.addWidget(panel._key_status)
    panel._key_field.textChanged.connect(panel._on_key_changed)
    model_label = label("Model", "field-label")
    ai.addWidget(model_label)
    panel._model_combo = NoScrollComboBox()
    panel._model_combo.setMinimumHeight(44)
    panel._model_combo.setAccessibleName("Claude model")
    model_label.setBuddy(panel._model_combo)
    for model_id, name in panel.MODELS:
        panel._model_combo.addItem(name, model_id)
    panel._model_combo.currentIndexChanged.connect(panel._on_model_changed)
    ai.addWidget(panel._model_combo)
    ai.addWidget(label("Choose a balance of quality, speed and API cost."))

    appearance = category(panel._tabs, "Appearance & files")
    appearance.addWidget(label("Appearance", "section-heading"))
    themes = QHBoxLayout()
    panel._btn_dark = button("Dark", lambda: panel._mw.set_theme("dark"))
    panel._btn_light = button("Light", lambda: panel._mw.set_theme("light"))
    themes.addWidget(panel._btn_dark)
    themes.addWidget(panel._btn_light)
    themes.addStretch()
    appearance.addLayout(themes)
    divider(appearance)
    appearance.addWidget(label("Downloads folder", "section-heading"))
    downloads = Path(__file__).resolve().parents[2] / "downloads"
    path_label = label(str(downloads), "dim")
    path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    appearance.addWidget(path_label)
    def open_folder():
        destination = downloads if downloads.exists() else downloads.parent
        if sys.platform == "win32":
            os.startfile(str(destination))
        else:
            import subprocess
            subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(destination)])
    appearance.addWidget(button("Open folder", open_folder), 0, Qt.AlignmentFlag.AlignLeft)

    help_page = category(panel._tabs, "Help & updates")
    help_page.addWidget(label("Interactive user guide", "section-heading"))
    cfg = panel._config()
    guide_url = str(cfg.get("interactive_guide_url", "")).strip()
    try:
        parsed = urlparse(guide_url)
        valid_guide = parsed.scheme in ("http", "https") and bool(parsed.hostname)
    except ValueError:
        valid_guide = False
    if valid_guide:
        help_page.addWidget(button("Open interactive guide", lambda: webbrowser.open(guide_url)), 0, Qt.AlignmentFlag.AlignLeft)
    else:
        help_page.addWidget(label("An interactive guide is being prepared. Its link will appear here when available.", "dim"))
    divider(help_page)
    help_page.addWidget(label("App updates", "section-heading"))
    panel._update_summary_lbl = label("", "dim")
    help_page.addWidget(panel._update_summary_lbl)
    help_page.addWidget(button("Check for updates", panel._check_for_updates), 0, Qt.AlignmentFlag.AlignLeft)
    divider(help_page)
    panel._troubleshooting_btn = button("Show troubleshooting details", lambda: None)
    panel._troubleshooting_btn.setCheckable(True)
    help_page.addWidget(panel._troubleshooting_btn, 0, Qt.AlignmentFlag.AlignLeft)
    details = QWidget()
    detail_layout = QVBoxLayout(details)
    detail_layout.setContentsMargins(0, 0, 0, 0)
    panel._update_diag_lbl = label("", "hint")
    panel._update_diag_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    detail_layout.addWidget(panel._update_diag_lbl)
    actions = QHBoxLayout()
    actions.addWidget(button("Open updater log", panel._open_updater_log))
    actions.addWidget(button("Find latest installer / reinstall", panel._check_latest_installer))
    actions.addStretch()
    detail_layout.addLayout(actions)
    detail_layout.addWidget(label("Finding the latest installer does not start installation. Use the update badge to review it."))
    details.hide()
    panel._troubleshooting_btn.toggled.connect(details.setVisible)
    panel._troubleshooting_btn.toggled.connect(lambda on: panel._troubleshooting_btn.setText("Hide troubleshooting details" if on else "Show troubleshooting details"))
    help_page.addWidget(details)
    panel.refresh_update_diagnostics()
