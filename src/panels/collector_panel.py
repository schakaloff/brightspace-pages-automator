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
    QPushButton, QLineEdit, QSpinBox, QCheckBox, QScrollArea, QFrame,
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
        self._build()
        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._poll_log)
        self._poll_timer.start(100)

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 28, 32, 20)
        layout.setSpacing(0)

        layout.addWidget(_section_header("Unit Collector"))
        sub = QLabel("Scrapes all topic pages from a unit and combines them into one collapsible HTML file.")
        sub.setProperty("role", "dim"); sub.setWordWrap(True)
        layout.addWidget(sub)
        layout.addSpacing(20)

        # ── Controls (scrollable on small screens) ────────────────────────────
        controls_widget = QWidget()
        controls_layout = QVBoxLayout(controls_widget)
        controls_layout.setContentsMargins(0, 0, 0, 0)
        controls_layout.setSpacing(0)

        controls_layout.addWidget(_form_label("PAGE THEME"))
        controls_layout.addSpacing(6)
        self._swatch_frames, self._selected_theme = _build_theme_swatches(controls_layout)
        controls_layout.addSpacing(14)

        controls_layout.addWidget(_form_label("BRIGHTSPACE UNIT URL"))
        controls_layout.addSpacing(4)
        self._unit_entry = QLineEdit()
        self._unit_entry.setPlaceholderText("https://learn.okanagancollege.ca/d2l/le/content/…/lessons/…")
        self._unit_entry.setFixedHeight(40)
        self._unit_entry.setToolTip(
            "The URL of a Brightspace unit (a collection of topic pages).\n"
            "Find it by clicking a unit in the course Content table — copy the URL from your browser."
        )
        controls_layout.addWidget(self._unit_entry)
        self._bs_course_hint = QLabel()
        self._bs_course_hint.setProperty("role", "dim")
        self._bs_course_hint.setWordWrap(True)
        self._bs_course_hint.hide()
        controls_layout.addSpacing(4)
        controls_layout.addWidget(self._bs_course_hint)
        controls_layout.addSpacing(12)

        self._auto_create_chk = QCheckBox("Auto-create the target page in this unit (recommended)")
        self._auto_create_chk.setChecked(True)
        self._auto_create_chk.setToolTip(
            "When on, a blank page is created automatically at the end of the unit above,\n"
            "so you don't have to make one in Brightspace and paste its URL.\n"
            "Leave the Target Page URL below blank when this is on."
        )
        self._auto_create_chk.toggled.connect(self._on_auto_toggle)
        controls_layout.addWidget(self._auto_create_chk)
        controls_layout.addSpacing(8)

        self._multi_unit_chk = QCheckBox("Continue to next unit automatically")
        self._multi_unit_chk.setToolTip(
            "After this unit finishes, find the next unit in the course\n"
            "(skipping empty units and units that already have a combined\n"
            "page) and run it too. You'll be asked to confirm before each\n"
            "additional unit unless “Don't ask before each unit” is also checked."
        )
        self._multi_unit_chk.toggled.connect(self._on_multi_unit_toggle)
        controls_layout.addWidget(self._multi_unit_chk)

        self._auto_continue_chk = QCheckBox("Don't ask before each unit")
        self._auto_continue_chk.setEnabled(False)
        self._auto_continue_chk.setToolTip(
            "Runs straight through additional units without pausing to\n"
            "confirm. Only used when “Continue to next unit automatically” is on."
        )
        controls_layout.addWidget(self._auto_continue_chk)
        controls_layout.addSpacing(8)

        controls_layout.addWidget(_form_label("TARGET PAGE URL  (optional — leave blank to auto-create)"))
        controls_layout.addSpacing(4)
        self._target_entry = QLineEdit()
        self._target_entry.setPlaceholderText("Leave blank to auto-create, or paste an existing page URL")
        self._target_entry.setFixedHeight(40)
        self._target_entry.setToolTip(
            "Where the combined output is written.\n"
            "Leave blank (with auto-create on) to have one made for you, or paste the URL\n"
            "of an existing blank HTML topic to reuse it."
        )
        controls_layout.addWidget(self._target_entry)
        self._target_hint = QLabel("A blank page will be created for you. Paste a URL here only to reuse an existing page.")
        self._target_hint.setProperty("role", "dim")
        self._target_hint.setWordWrap(True)
        controls_layout.addSpacing(4)
        controls_layout.addWidget(self._target_hint)
        controls_layout.addSpacing(12)

        controls_layout.addWidget(_form_label("MOODLE COURSE URL  (optional — fixes weird file/link names)"))
        controls_layout.addSpacing(4)
        self._moodle_entry = QLineEdit()
        self._moodle_entry.setPlaceholderText("https://mymoodle.okanagan.bc.ca/course/view.php?id=…")
        self._moodle_entry.setFixedHeight(40)
        controls_layout.addWidget(self._moodle_entry)
        controls_layout.addSpacing(12)

        par_row = QHBoxLayout()
        par_row.addWidget(_form_label("PARALLEL PAGES"))
        self._parallel_spin = QSpinBox()
        self._parallel_spin.setRange(1, 10)
        self._parallel_spin.setValue(3)
        self._parallel_spin.setFixedWidth(60)
        self._parallel_spin.setToolTip(
            "Number of topic pages fetched simultaneously.\n"
            "Higher = faster, but may trigger Brightspace rate limits. Default 3 is safe."
        )
        par_row.addWidget(self._parallel_spin)
        par_row.addStretch()
        controls_layout.addLayout(par_row)

        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setWidget(controls_widget)
        scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        layout.addWidget(scroll_area, 3)

        # ── Pinned below the scroll area: run action, log, continue ───────────
        layout.addSpacing(14)

        self._run_btn = QPushButton("Collect & Assemble")
        self._run_btn.setFixedHeight(42)
        self._run_btn.setToolTip(
            "Scrapes all topic pages in the unit, combines them into one collapsible HTML file,\n"
            "and writes the result to the target page."
        )
        self._run_btn.clicked.connect(self._start_run)
        layout.addWidget(self._run_btn)
        layout.addSpacing(8)

        layout.addWidget(_form_label("LOG"))
        layout.addSpacing(4)
        self._log = LogWidget()
        self._log.setMinimumHeight(120)
        layout.addWidget(self._log, 1)
        layout.addSpacing(8)

        self._continue_btn = QPushButton("Continue to Page Changer")
        self._continue_btn.setProperty("variant", "next-step")
        self._continue_btn.setFixedHeight(38)
        self._continue_btn.setToolTip("Proceed to Step 3: use Claude AI to restyle pages with an OC brand theme.")
        self._continue_btn.hide()
        self._continue_btn.clicked.connect(self.continue_next)
        layout.addWidget(self._continue_btn)

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

    def _on_auto_toggle(self, checked: bool):
        """Reflect the auto-create choice in the Target URL field's hint/placeholder."""
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
                    self._run_btn.setText("Collect & Assemble")
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
