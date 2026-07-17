import asyncio
import queue
import re
import threading
from pathlib import Path


def _normalize_url(u: str) -> str:
    """Ensure a pasted URL has a scheme. Browsers often show/copy URLs without
    'https://', which makes Playwright's page.goto() fail silently and breaks
    URL parsing downstream. Prepend https:// when missing."""
    u = u.strip()
    if u and not re.match(r"^https?://", u, re.IGNORECASE):
        u = "https://" + u
    return u

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QLineEdit, QSpinBox, QCheckBox, QScrollArea, QFrame, QSizePolicy,
)
from PySide6.QtCore import Qt, Signal, QTimer

from gui_log import LogWidget
from panels._shared import (
    _divider, _form_label, _section_header, PAGE_THEMES, _build_theme_swatches, friendly_error,
)


class CollectorPanel(QWidget):
    step_success = Signal()
    continue_next = Signal()

    def __init__(self, main_window, parent=None):
        super().__init__(parent)
        self._mw = main_window
        self._log_queue: queue.Queue = queue.Queue()
        self._swatch_frames: dict = {}
        self._selected_theme: list = ["lake"]
        self._log_expanded = False
        self._build()
        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._poll_log)
        self._poll_timer.start(100)

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 16)
        layout.setSpacing(0)

        # ── Main content column (scrollable on small screens) ─────────────────
        content_widget = QWidget()
        content_layout = QVBoxLayout(content_widget)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(8)
        content_widget.setMaximumWidth(1080)
        content_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)

        content_layout.addWidget(_section_header("Unit Collector"))
        sub = QLabel("Scrapes all topic pages from a unit and combines them into one collapsible HTML file.")
        sub.setProperty("role", "dim"); sub.setWordWrap(True)
        content_layout.addWidget(sub)
        content_layout.addSpacing(4)

        main_panel = QFrame()
        main_panel.setProperty("role", "card")
        main_panel.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        panel = QVBoxLayout(main_panel)
        panel.setContentsMargins(16, 14, 16, 14)
        panel.setSpacing(8)

        # ── Brightspace unit ─────────────────────────────────────────────────
        panel.addWidget(_form_label("BRIGHTSPACE UNIT URL"))
        self._unit_entry = QLineEdit()
        self._unit_entry.setPlaceholderText("https://learn.okanagancollege.ca/d2l/le/content/…/lessons/…")
        self._unit_entry.setFixedHeight(36)
        self._unit_entry.setToolTip(
            "The URL of a Brightspace unit (a collection of topic pages).\n"
            "Find it by clicking a unit in the course Content table — copy the URL from your browser."
        )
        self._bs_course_hint = QLabel()
        self._bs_course_hint.setProperty("role", "dim")
        self._bs_course_hint.setWordWrap(True)
        self._bs_course_hint.hide()
        panel.addWidget(self._unit_entry)
        panel.addWidget(self._bs_course_hint)
        panel.addSpacing(2)
        panel.addWidget(_divider())

        # ── Combined page ────────────────────────────────────────────────────
        panel.addWidget(_form_label("COMBINED PAGE"))
        self._auto_create_chk = QCheckBox("Create the combined page for me (recommended)")
        self._auto_create_chk.setChecked(True)
        self._auto_create_chk.setToolTip(
            "When on, a blank page is created automatically at the end of the unit above,\n"
            "so you don't have to make one in Brightspace and paste its URL.\n"
            "Leave the Target Page URL below blank when this is on."
        )
        self._auto_create_chk.toggled.connect(self._on_auto_toggle)
        panel.addWidget(self._auto_create_chk)

        self._target_entry = QLineEdit()
        self._target_entry.setPlaceholderText("Leave blank to auto-create, or paste an existing page URL")
        self._target_entry.setFixedHeight(36)
        self._target_entry.setToolTip(
            "Where the combined output is written.\n"
            "Leave blank (with auto-create on) to have one made for you, or paste the URL\n"
            "of an existing blank HTML topic to reuse it."
        )
        panel.addWidget(self._target_entry)
        self._target_hint = QLabel("A blank page will be created for you. Paste a URL here only to reuse an existing page.")
        self._target_hint.setProperty("role", "dim")
        self._target_hint.setWordWrap(True)
        panel.addWidget(self._target_hint)
        panel.addSpacing(2)
        panel.addWidget(_divider())

        # ── Style ────────────────────────────────────────────────────────────
        panel.addWidget(_form_label("STYLE / THEME"))
        self._swatch_frames, self._selected_theme = _build_theme_swatches(panel)
        panel.addSpacing(2)
        panel.addWidget(_divider())

        # ── Advanced (collapsed by default) ───────────────────────────────────
        self._adv_btn = QPushButton("▸  Advanced options")
        self._adv_btn.setProperty("variant", "secondary")
        self._adv_btn.setCheckable(True)
        self._adv_btn.setFixedHeight(30)
        self._adv_btn.setMaximumWidth(210)
        self._adv_btn.setToolTip("Batch runs, Moodle name-fixing, and speed. Most runs don't need these.")
        self._adv_btn.toggled.connect(self._on_adv_toggle)
        panel.addWidget(self._adv_btn, 0, Qt.AlignmentFlag.AlignLeft)

        self._adv_container = QWidget()
        adv = QVBoxLayout(self._adv_container)
        adv.setContentsMargins(0, 2, 0, 0)
        adv.setSpacing(8)

        self._multi_unit_chk = QCheckBox("Also do the following units in this course")
        self._multi_unit_chk.setToolTip(
            "After this unit finishes, find the next unit in the course\n"
            "(skipping empty units and units that already have a combined\n"
            "page) and run it too. You'll be asked to confirm before each\n"
            "additional unit unless “Don't ask me before each unit” is also checked."
        )
        self._multi_unit_chk.toggled.connect(self._on_multi_unit_toggle)
        adv.addWidget(self._multi_unit_chk)

        self._auto_continue_chk = QCheckBox("Don't ask me before each unit")
        self._auto_continue_chk.setEnabled(False)
        self._auto_continue_chk.setToolTip(
            "Runs straight through additional units without pausing to\n"
            "confirm. Only used when “Also do the following units” is on."
        )
        sub_row = QHBoxLayout()
        sub_row.setContentsMargins(0, 2, 0, 0)
        sub_row.addSpacing(24)
        sub_row.addWidget(self._auto_continue_chk)
        sub_row.addStretch()
        adv.addLayout(sub_row)
        adv.addSpacing(4)

        adv.addWidget(_form_label("MOODLE COURSE URL  (optional — fixes odd file names)"))
        self._moodle_entry = QLineEdit()
        self._moodle_entry.setPlaceholderText("https://mymoodle.okanagan.bc.ca/course/view.php?id=…")
        self._moodle_entry.setFixedHeight(36)
        adv.addWidget(self._moodle_entry)
        adv.addSpacing(4)

        par_row = QHBoxLayout()
        par_row.setSpacing(10)
        par_row.addWidget(_form_label("SPEED  (pages at once)"), 0, Qt.AlignmentFlag.AlignVCenter)
        self._parallel_spin = QSpinBox()
        self._parallel_spin.setRange(1, 10)
        self._parallel_spin.setValue(3)
        self._parallel_spin.setFixedWidth(84)
        self._parallel_spin.setFixedHeight(32)
        self._parallel_spin.setToolTip(
            "Number of topic pages fetched simultaneously.\n"
            "Higher = faster, but may trigger Brightspace rate limits. Default 3 is safe."
        )
        par_row.addWidget(self._parallel_spin)
        par_row.addStretch()
        adv.addLayout(par_row)

        self._adv_container.setVisible(False)
        panel.addWidget(self._adv_container)

        self._run_btn = QPushButton("Create Combined Page")
        self._run_btn.setFixedHeight(38)
        self._run_btn.setMinimumWidth(240)
        self._run_btn.setStyleSheet(
            "QPushButton { background-color:#005F63; color:#ffffff; border:none; "
            "border-radius:6px; padding:8px 18px; font-size:13px; font-weight:600; }"
            "QPushButton:hover { background-color:#007a80; }"
            "QPushButton:disabled { background-color:#1a2a2c; color:#636780; }"
        )
        self._run_btn.setToolTip(
            "Scrapes all topic pages in the unit, combines them into one collapsible HTML file,\n"
            "and writes the result to the target page."
        )
        self._run_btn.clicked.connect(self._start_run)
        panel.addSpacing(4)
        panel.addWidget(self._run_btn, 0, Qt.AlignmentFlag.AlignLeft)

        content_layout.addWidget(main_panel)

        log_header = QWidget()
        log_header_layout = QHBoxLayout(log_header)
        log_header_layout.setContentsMargins(0, 2, 0, 0)
        log_header_layout.setSpacing(8)
        log_header_layout.addWidget(_form_label("LOG"))
        log_header_layout.addStretch()
        self._log_expand_btn = QPushButton("Expand Log")
        self._log_expand_btn.setProperty("variant", "secondary")
        self._log_expand_btn.setFixedHeight(28)
        self._log_expand_btn.setMaximumWidth(120)
        self._log_expand_btn.clicked.connect(self._toggle_log_size)
        log_header_layout.addWidget(self._log_expand_btn)
        content_layout.addWidget(log_header)

        self._log = LogWidget()
        self._log.setFixedHeight(240)
        content_layout.addWidget(self._log)

        self._continue_btn = QPushButton("Continue to Page Changer")
        self._continue_btn.setProperty("variant", "next-step")
        self._continue_btn.setFixedHeight(38)
        self._continue_btn.setToolTip("Proceed to Step 3: use Claude AI to restyle pages with an OC brand theme.")
        self._continue_btn.hide()
        self._continue_btn.clicked.connect(self.continue_next)
        content_layout.addWidget(self._continue_btn, 0, Qt.AlignmentFlag.AlignLeft)

        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setWidget(content_widget)
        scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        scroll_area.setAlignment(
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop
        )
        layout.addWidget(scroll_area, 1)

        # Carry over URLs entered in the Checker tab, and restore the checkbox.
        cfg = self._mw.load_config() if hasattr(self._mw, "load_config") else {}
        if cfg.get("chk_moodle_url") and not self._moodle_entry.text().strip():
            self._moodle_entry.setText(cfg["chk_moodle_url"])
        if cfg.get("chk_bs_url"):
            self._bs_course_hint.setText(f"Course carried over from Checker: {cfg['chk_bs_url']}")
            self._bs_course_hint.show()
        if "col_auto_create" in cfg:
            self._auto_create_chk.setChecked(bool(cfg["col_auto_create"]))
        self._on_auto_toggle(self._auto_create_chk.isChecked())

    def _toggle_log_size(self):
        self._log_expanded = not self._log_expanded
        self._log.setFixedHeight(420 if self._log_expanded else 240)
        self._log_expand_btn.setText("Collapse Log" if self._log_expanded else "Expand Log")

    def _on_adv_toggle(self, checked: bool):
        self._adv_container.setVisible(checked)
        self._adv_btn.setText("▾  Advanced options" if checked else "▸  Advanced options")

    def _on_auto_toggle(self, checked: bool):
        """Reflect the auto-create choice: hide the target field when a page will
        be made automatically, show it (with guidance) when the user must supply one."""
        self._target_entry.setVisible(not checked)
        self._target_hint.setVisible(not checked)
        if checked:
            self._target_entry.setPlaceholderText("Leave blank to auto-create, or paste an existing page URL")
            self._target_hint.setText("A blank page will be created for you. Paste a URL here only to reuse an existing page.")
        else:
            self._target_entry.setPlaceholderText("https://learn.okanagancollege.ca/d2l/le/lessons/…/topics/…")
            self._target_hint.setText("Auto-create is off — paste the URL of a blank Brightspace page to write into.")

    def _on_multi_unit_toggle(self, checked: bool):
        self._auto_continue_chk.setEnabled(checked)
        if not checked:
            self._auto_continue_chk.setChecked(False)

    def refresh_carryover(self):
        """Re-read Checker's saved URLs (call when this tab becomes visible)."""
        cfg = self._mw.load_config() if hasattr(self._mw, "load_config") else {}
        if cfg.get("chk_moodle_url") and not self._moodle_entry.text().strip():
            self._moodle_entry.setText(cfg["chk_moodle_url"])
            self._adv_btn.setChecked(True)
        if cfg.get("chk_bs_url"):
            self._bs_course_hint.setText(f"Course carried over from Checker: {cfg['chk_bs_url']}")
            self._bs_course_hint.show()

    def _start_run(self):
        if not self._mw.chromium_ready:
            self._log.append_log("Browser engine still installing — please wait.", "warning")
            return
        unit_url   = _normalize_url(self._unit_entry.text())
        target_url = _normalize_url(self._target_entry.text())
        moodle_url = _normalize_url(self._moodle_entry.text())
        auto_create = self._auto_create_chk.isChecked()
        multi_unit  = self._multi_unit_chk.isChecked()
        auto_continue = self._auto_continue_chk.isChecked()
        if not unit_url:
            self._log.append_log("Paste a Brightspace unit URL first.", "warning"); return
        if not target_url and not auto_create:
            self._log.append_log(
                "Paste a target page URL, or turn on “Auto-create the target page”.", "warning"
            ); return

        course_id = None
        if multi_unit:
            from target_page_creator import _parse_ids
            course_id, _ = _parse_ids(unit_url)
            if not course_id:
                self._log.append_log(
                    "Couldn't read a course id from that unit URL — multi-unit mode needs one.",
                    "warning",
                ); return

        self._mw.save_config({"col_auto_create": auto_create})

        theme_name   = self._selected_theme[0]
        theme_colors = PAGE_THEMES[theme_name]
        parallel     = self._parallel_spin.value()

        style_ref_path = Path(__file__).parent.parent.parent / "templates" / "style_reference.html"
        try:
            style_reference_html = style_ref_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            style_reference_html = ""

        self._run_btn.setText("Running…"); self._run_btn.setEnabled(False)
        self._continue_btn.hide()
        self._log.clear_log()

        q = self._log_queue
        shared_kwargs = dict(
            theme_name=theme_name,
            theme_colors=theme_colors,
            claude_api_key=self._mw.claude_api_key,
            claude_model=self._mw.claude_model,
            style_reference_html=style_reference_html,
            parallel_pages=parallel,
            bs_username=self._mw.bs_username,
            bs_password=self._mw.bs_password,
            sso_email=self._mw.sso_email,
            sso_password=self._mw.sso_password,
            moodle_url=moodle_url,
            moodle_username=self._mw.moodle_username,
            moodle_password=self._mw.moodle_password,
        )

        def worker():
            done_sent = [False]
            def on_done():
                if not done_sent[0]:
                    done_sent[0] = True
                    q.put(("__DONE__", ""))
            try:
                from unit_collector import run as collector_run
                if multi_unit:
                    asyncio.run(self._run_multi_unit(
                        unit_url, course_id, auto_continue, shared_kwargs, collector_run, q
                    ))
                else:
                    asyncio.run(collector_run(
                        unit_url=unit_url,
                        target_url=target_url,
                        auto_create_target=auto_create,
                        log=lambda msg, tag="info": q.put((msg, tag)),
                        on_complete=on_done,
                        **shared_kwargs,
                    ))
            except Exception as e:
                msg, detail = friendly_error(e)
                q.put((f"Error: {msg}", "error"))
                if detail != msg:
                    q.put((detail, "detail"))
            finally:
                on_done()

        threading.Thread(target=worker, daemon=True).start()

    async def _run_multi_unit(self, first_unit_url, course_id, auto_continue, shared_kwargs, collector_run, q):
        from browser import launch_browser, wait_for_login
        from multi_unit_selector import run_multi

        log = lambda msg, tag="info": q.put((msg, tag))
        base = "/".join(first_unit_url.split("/")[:3])

        p, browser_, context, page = await launch_browser()
        try:
            await wait_for_login(
                page, context,
                self._mw.bs_username or None, self._mw.bs_password or None,
                self._mw.sso_email or None, self._mw.sso_password or None,
            )

            async def run_unit(unit_url: str) -> bool:
                try:
                    return await collector_run(
                        unit_url=unit_url,
                        target_url="",
                        auto_create_target=True,
                        log=log,
                        on_complete=lambda: None,
                        context=context,
                        page=page,
                        **shared_kwargs,
                    )
                except Exception as e:
                    msg, detail = friendly_error(e)
                    log(f"✗ Unit failed: {msg}", "error")
                    if detail != msg:
                        log(detail, "detail")
                    return False

            def confirm_fn(message: str) -> bool:
                if auto_continue:
                    return True
                result_ref = [False]
                event = threading.Event()
                q.put(("__COL_CONFIRM__", (message, result_ref, event)))
                event.wait()
                return result_ref[0]

            summary = await run_multi(
                page=page,
                course_id=course_id,
                base_url=base,
                run_unit=run_unit,
                confirm_fn=confirm_fn,
                log=log,
            )
            log(
                f"─── Multi-unit run finished: {len(summary['processed'])} unit(s) done, "
                f"stopped because: {summary['stopped_reason']} ───",
                "info",
            )
        finally:
            if browser_.is_connected():
                await browser_.close()
            await p.stop()

    def _poll_log(self):
        try:
            while True:
                msg, tag = self._log_queue.get_nowait()
                if msg == "__DONE__":
                    self._run_btn.setText("Create Combined Page")
                    self._run_btn.setEnabled(True)
                elif msg == "__SUCCESS__":
                    self._continue_btn.show()
                    self.step_success.emit()
                elif msg == "__COL_CONFIRM__":
                    conf_msg, result_ref, event = tag
                    from PySide6.QtWidgets import QMessageBox
                    dlg = QMessageBox(self)
                    dlg.setWindowTitle("Continue to next unit?")
                    dlg.setText(conf_msg)
                    dlg.setStandardButtons(
                        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                    )
                    dlg.setDefaultButton(QMessageBox.StandardButton.No)
                    dlg.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
                    dlg.raise_()
                    dlg.activateWindow()
                    result_ref[0] = dlg.exec() == QMessageBox.StandardButton.Yes
                    event.set()
                else:
                    self._log.append_log(msg, tag)
        except queue.Empty:
            pass
