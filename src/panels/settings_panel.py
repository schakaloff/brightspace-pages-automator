import json
import os
import sys
from pathlib import Path

from PySide6.QtWidgets import QWidget
from PySide6.QtCore import Signal, QTimer
import gui_styles


class SettingsPanel(QWidget):
    """Categorized app settings with explicit account management."""

    api_key_changed = Signal(str)
    model_changed = Signal(str)

    CREDENTIALS = {
        "bs": ("bs_username", "BrightspacePagesAutomator"),
        "sso": ("sso_email", "BrightspacePagesAutomator_SSO"),
        "moodle": ("moodle_username", "BrightspacePagesAutomator_Moodle"),
        "kmc": ("kmc_username", "BrightspacePagesAutomator_KMC"),
    }

    MODELS = [
        ("claude-opus-5", "Opus 5 — highest quality, slower/pricier"),
        ("claude-opus-4-5", "Opus 4.5 — previous Opus, same price"),
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
        from panels.settings_layout import build_settings
        build_settings(self)
        self._load_credentials()
        self.mark_active_theme("light" if gui_styles.is_light() else "dark")

    def _config(self):
        cfg = self._mw.load_config() if hasattr(self._mw, "load_config") else {}
        return cfg if isinstance(cfg, dict) else {}

    # ── Public API ────────────────────────────────────────────────────────────

    def mark_active_theme(self, name: str):
        """Refresh scoped colours and highlight the active theme button."""
        from panels.settings_styles import settings_stylesheet
        self.setStyleSheet(settings_stylesheet())
        for btn, theme in ((self._btn_dark, "dark"), (self._btn_light, "light")):
            active = theme == name
            btn.setProperty("variant", "theme-active" if active else "secondary")
            btn.setAccessibleName(f"{theme.title()} theme" + (", selected" if active else ""))
            btn.style().unpolish(btn)
            btn.style().polish(btn)

    def set_api_key(self, key: str):
        """Set the API key field without emitting api_key_changed."""
        self._save_timer.stop()
        self._key_field.blockSignals(True)
        self._key_field.setText(key)
        self._key_field.blockSignals(False)
        self._set_status(self._key_status, "API key saved on this computer" if key else "No API key saved", "saved" if key else "")

    def set_model(self, model_id: str):
        """Set the model dropdown without emitting model_changed."""
        idx = self._model_combo.findData(model_id)
        if idx < 0:
            idx = 0
        self._model_combo.blockSignals(True)
        self._model_combo.setCurrentIndex(idx)
        self._model_combo.blockSignals(False)

    def refresh_update_diagnostics(self, diagnostics: dict | None = None):
        if diagnostics is None:
            try:
                from update_checker import get_update_diagnostics
                diagnostics = get_update_diagnostics()
            except Exception:
                diagnostics = {}
        result = diagnostics.get("last_update_result", "Unknown")
        detail = diagnostics.get("last_update_detail", "")
        if detail:
            result = f"{result} - {detail}"
        # Names the failure (rate_limit / ssl / timeout / network / malformed)
        # so a support question can be answered from this panel alone.
        kind = diagnostics.get("fetch_error_kind", "")
        if kind:
            status = diagnostics.get("fetch_http_status", "")
            result = f"{result} [{kind}{f', HTTP {status}' if status != '' else ''}]"
        checked = diagnostics.get("last_update_at") or "not checked yet"
        latest = diagnostics.get("latest_build") or "(not known)"
        # A Setup that exited non-zero must never be readable as a success, so
        # the install outcome gets its own line with the exit code attached.
        install_result = diagnostics.get("last_install_result", "")
        if install_result:
            install_line = install_result
            exit_code = diagnostics.get("setup_exit_code", "")
            if exit_code != "":
                install_line = f"{install_line} (Setup exit code {exit_code})"
            install_detail = diagnostics.get("last_install_detail", "")
            if install_detail:
                install_line = f"{install_line} - {install_detail}"
            install_at = diagnostics.get("last_install_at", "")
            if install_at:
                install_line = f"{install_line} [{install_at}]"
        else:
            install_line = "no update has been installed from this app yet"
        lines = [
            f"Version: {diagnostics.get('current_version', 'unknown')}",
            f"Commit/build: {diagnostics.get('current_build', 'unknown')}",
            f"Install path: {diagnostics.get('install_path', 'unknown')}",
            f"Update channel: {diagnostics.get('update_channel', 'unknown')}",
            f"Update branch: {diagnostics.get('update_branch', 'unknown')}",
            f"Latest release seen: {latest}",
            f"Last update result: {result}",
            f"Last checked: {checked}",
            f"Last install: {install_line}",
            f"Updater log: {diagnostics.get('updater_log_path', 'unknown')}",
            f"Installer log: {diagnostics.get('setup_log_path', 'unknown')}",
        ]
        self._update_diag_lbl.setText("\n".join(lines))
        summary = f"Version {diagnostics.get('current_version', 'unknown')} · {result}\nLast checked: {checked}"
        if install_result:
            outcome = install_result
            if diagnostics.get("setup_exit_code", "") != "":
                outcome += f" (Setup exit code {diagnostics['setup_exit_code']})"
            summary += f"\nLast installation: {outcome}"
        self._update_summary_lbl.setText(summary)

    def _check_for_updates(self):
        self._update_summary_lbl.setText("Checking for updates…")
        if hasattr(self._mw, "_run_update_check"):
            self._mw._run_update_check(force_install=False)

    def _check_latest_installer(self):
        if hasattr(self._mw, "_run_update_check"):
            self._mw._run_update_check(force_install=True)
        self.refresh_update_diagnostics()

    def _open_updater_log(self):
        try:
            from update_checker import update_log_path
            path = update_log_path()
            if not path.exists():
                path.write_text("No updater events recorded yet.\n", encoding="utf-8")
            if sys.platform == "win32":
                os.startfile(str(path))
            elif sys.platform == "darwin":
                import subprocess
                subprocess.Popen(["open", str(path)])
            else:
                import subprocess
                subprocess.Popen(["xdg-open", str(path)])
        except Exception:
            self.refresh_update_diagnostics()

    @property
    def bs_username(self) -> str:
        return self._config().get("bs_username", "") if self._login_method == "direct" else ""

    @property
    def bs_password(self) -> str:
        import keyring
        u = self.bs_username
        return keyring.get_password("BrightspacePagesAutomator", u) or "" if u else ""

    @property
    def sso_email(self) -> str:
        return self._config().get("sso_email", "") if self._login_method == "sso" else ""

    @property
    def sso_password(self) -> str:
        import keyring
        e = self.sso_email
        return keyring.get_password("BrightspacePagesAutomator_SSO", e) or "" if e else ""

    @property
    def moodle_username(self) -> str:
        return self._config().get("moodle_username", "")

    @property
    def moodle_password(self) -> str:
        import keyring
        u = self.moodle_username
        return keyring.get_password("BrightspacePagesAutomator_Moodle", u) or "" if u else ""

    @property
    def kmc_username(self) -> str:
        return self._config().get("kmc_username", "")

    @property
    def kmc_password(self) -> str:
        import keyring
        u = self.kmc_username
        return keyring.get_password("BrightspacePagesAutomator_KMC", u) or "" if u else ""

    # ── Private slots ─────────────────────────────────────────────────────────

    def _on_key_changed(self, key: str):
        self.api_key_changed.emit(key)
        self._set_status(self._key_status, "Saving API key…")
        self._save_timer.start(500)

    def _save_api_key(self):
        import keyring
        key = self._key_field.text().strip()
        try:
            if key:
                keyring.set_password("BrightspacePagesAutomator_Claude", "api_key", key)
            else:
                try:
                    keyring.delete_password("BrightspacePagesAutomator_Claude", "api_key")
                except keyring.errors.PasswordDeleteError:
                    pass
        except Exception:
            self._set_status(self._key_status, "Could not save the API key. Check your system credential store and try again.", "error")
            return
        self._set_status(self._key_status, "API key saved on this computer" if key else "No API key saved", "saved" if key else "")

    def _on_model_changed(self, index: int):
        model_id = self._model_combo.itemData(index)
        self.model_changed.emit(model_id)
        if hasattr(self._mw, "save_config"):
            self._mw.save_config({"claude_model": model_id})

    @staticmethod
    def _set_status(widget, text, state=""):
        widget.setText(text)
        widget.setProperty("state", state)
        widget.style().unpolish(widget)
        widget.style().polish(widget)

    def _load_credentials(self):
        import keyring
        cfg = self._config()
        for key, (config_key, service) in self.CREDENTIALS.items():
            user, password, status = self._account_fields[key]
            username = cfg.get(config_key, "")
            user.setText(username)
            try:
                saved_password = keyring.get_password(service, username) or "" if username else ""
                password.setText(saved_password)
                self._refresh_account(key, bool(username and saved_password))
            except Exception:
                self._set_status(status, "Could not read the saved password. Check your system credential store.", "error")
                if key in self._account_summaries:
                    self._set_status(self._account_summaries[key], "Credential store unavailable", "error")
        method = cfg.get("bs_login_method")
        if method not in ("sso", "direct"):
            method = "sso" if cfg.get("sso_email") or not cfg.get("bs_username") else "direct"
        self._login_method = method
        for radio, value in ((self._sso_radio, "sso"), (self._direct_radio, "direct")):
            radio.blockSignals(True)
            radio.setChecked(method == value)
            radio.blockSignals(False)
        self._show_login_form()

    def _show_login_form(self):
        self._account_forms["sso"].setVisible(self._login_method == "sso")
        self._account_forms["bs"].setVisible(self._login_method == "direct")

    def _choose_login_method(self, method):
        self._login_method = method
        if hasattr(self._mw, "save_config"):
            self._mw.save_config({"bs_login_method": method})
        for key in ("moodle", "kmc"):
            self._account_forms[key].hide()
            self._account_edit_buttons[key].setText("Edit" if self._config().get(self.CREDENTIALS[key][0]) else "Set up")
        self._show_login_form()

    def _toggle_account_form(self, key):
        opening = self._account_forms[key].isHidden()
        for other in ("sso", "bs", "moodle", "kmc"):
            self._account_forms[other].setVisible(opening and other == key)
        for other, btn in self._account_edit_buttons.items():
            btn.setText("Close" if opening and other == key else ("Edit" if self._config().get(self.CREDENTIALS[other][0]) else "Set up"))
        if opening:
            self._account_fields[key][0].setFocus()
        else:
            self._show_login_form()

    def _refresh_account(self, key, configured):
        text = "Saved on this computer" if configured else "Not configured"
        state = "saved" if configured else ""
        self._set_status(self._account_fields[key][2], text, state)
        if key in self._account_summaries:
            self._set_status(self._account_summaries[key], text, state)
            if self._account_forms[key].isHidden():
                self._account_edit_buttons[key].setText("Edit" if configured else "Set up")

    def _mark_account_dirty(self, key):
        self._set_status(self._account_fields[key][2], "Unsaved changes — use Save changes.")

    def _save_credentials(self, key):
        import keyring
        config_key, service = self.CREDENTIALS[key]
        user, password_field, status = self._account_fields[key]
        username = user.text().strip()
        password = password_field.text()  # Spaces may be part of a valid password.
        if not username:
            self._set_status(status, "Enter an email first." if key in ("sso", "kmc") else "Enter a username first.", "error")
            user.setFocus()
            return False
        try:
            if not password and not keyring.get_password(service, username):
                self._set_status(status, "Enter a password first. To remove saved credentials, use Remove saved credentials.", "error")
                password_field.setFocus()
                return False
            if password:
                keyring.set_password(service, username, password)
            self._mw.save_config({config_key: username})
            if self._config().get(config_key) != username:
                self._set_status(status, "Could not save the account name. Check access to the app settings and try again.", "error")
                return False
        except Exception:
            self._set_status(status, "Could not save credentials. Check your system credential store and try again.", "error")
            return False
        self._refresh_account(key, True)
        return True

    def _remove_credentials(self, key):
        import keyring
        from PySide6.QtWidgets import QMessageBox
        config_key, service = self.CREDENTIALS[key]
        username = self._config().get(config_key, "")
        status = self._account_fields[key][2]
        if not username:
            self._set_status(status, "No saved credentials to remove.")
            return
        choice = QMessageBox.question(
            self, "Remove saved credentials?",
            "Remove the saved sign-in details from this computer? You can add them again later.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No,
        )
        if choice != QMessageBox.StandardButton.Yes:
            return
        try:
            try:
                keyring.delete_password(service, username)
            except keyring.errors.PasswordDeleteError:
                pass
            self._mw.save_config({config_key: ""})
            if self._config().get(config_key):
                self._set_status(status, "Password removed, but the saved account name could not be cleared. Try removing again.", "error")
                return
        except Exception:
            self._set_status(status, "Could not remove credentials. Check your system credential store and try again.", "error")
            return
        self._account_fields[key][0].clear()
        self._account_fields[key][1].clear()
        self._refresh_account(key, False)
        self._set_status(status, "Saved credentials removed.")

    def _save_bs_credentials(self):
        return self._save_credentials("bs")

    def _import_bs_from_quiz_automator(self):
        """Import Brightspace username/password from Quiz Automator's saved config.

        Reads %APPDATA%\\BrightspaceAutomator\\outline_config.json (Quiz Automator's
        plaintext config file), confirms with the user by field name only (never
        showing values), then saves through the same path _save_bs_credentials uses
        (config for username, keyring for password).
        """
        from PySide6.QtWidgets import QMessageBox

        appdata = os.environ.get("APPDATA", "")
        cfg_path = Path(appdata) / "BrightspaceAutomator" / "outline_config.json" if appdata else None

        if not cfg_path or not cfg_path.exists():
            self._bs_cred_status.setText("Quiz Automator config not found.")
            return

        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            self._bs_cred_status.setText("Could not read Quiz Automator config.")
            return

        username = (cfg.get("bs_username") or "").strip()
        password = cfg.get("bs_password") or ""
        fields = [name for name, val in (("bs_username", username), ("bs_password", password)) if val]

        if not fields:
            self._bs_cred_status.setText("No Brightspace credentials found in Quiz Automator.")
            return

        dlg = QMessageBox(self)
        dlg.setWindowTitle("Import from Quiz Automator")
        dlg.setText(
            "Import the following field(s) from Quiz Automator?\n\n"
            + "\n".join(f"• {name}" for name in fields)
            + "\n\nValues are not shown here for security."
        )
        dlg.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        dlg.setDefaultButton(QMessageBox.StandardButton.No)
        if dlg.exec() != QMessageBox.StandardButton.Yes:
            return

        if username:
            self._bs_user_field.setText(username)
        if password:
            self._bs_pass_field.setText(password)
        if not self._save_bs_credentials():
            return

        print(f"[settings] Imported from Quiz Automator: {', '.join(fields)}", flush=True)
        self._bs_cred_status.setText(f"Imported: {', '.join(fields)}")

    def _save_sso_credentials(self):
        self._save_credentials("sso")

    def _save_moodle_credentials(self):
        self._save_credentials("moodle")

    def _save_kmc_credentials(self):
        self._save_credentials("kmc")
