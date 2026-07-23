import asyncio
import queue
import re
import threading
from enum import Enum, auto
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
    QToolButton,
)
from PySide6.QtCore import Qt, Signal, QTimer

from gui_log import LogWidget
from panels._shared import (
    _divider, _form_label, _section_header, PAGE_THEMES, _build_theme_swatches, friendly_error,
)


class CollectState(Enum):
    READY = auto()
    RUNNING = auto()
    SUCCESS = auto()


_SPINNER_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]


class CollectorPanel(QWidget):
    step_success = Signal()
    continue_next = Signal()

    def __init__(self, main_window, parent=None):
        super().__init__(parent)
        self._mw = main_window
        self._log_queue: queue.Queue = queue.Queue()
        self._swatch_frames: dict = {}
        self._selected_theme: list = ["lake"]
        self._state = CollectState.READY
        self._succeeded = False          # set from the backend log stream, read on __DONE__
        self._last_page_count = None     # parsed from "✓ Text done: N pages"
        self._spin_idx = 0
        self._build()
        self._apply_state()
        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._poll_log)
        self._poll_timer.start(100)

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 28, 32, 20)
        layout.setSpacing(0)

        layout.addWidget(_section_header("Unit Collector"))
        sub = QLabel("Scrapes all topic pages from a unit and combines them into one collapsible Brightspace page.")
        sub.setProperty("role", "dim"); sub.setWordWrap(True)
        layout.addWidget(sub)
        layout.addSpacing(10)

        # ── Workflow card — fills the content width like Kaltura/Checker ──────
        main_panel = QFrame()
        main_panel.setProperty("role", "card")
        main_panel.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        panel = QVBoxLayout(main_panel)
        panel.setContentsMargins(16, 14, 16, 14)
        panel.setSpacing(8)

        # Top content is added straight into the card; the whole card sits in an
        # outer scroll (below) as the small-screen safety net so nothing clips
        # and the footer is never displaced — §2/§6.
        top = panel

        # ── Brightspace unit ─────────────────────────────────────────────────
        top.addWidget(_form_label("BRIGHTSPACE UNIT URL"))
        self._unit_entry = QLineEdit()
        self._unit_entry.setObjectName("unit_url_edit")
        self._unit_entry.setPlaceholderText("https://learn.okanagancollege.ca/d2l/le/content/…/lessons/…")
        self._unit_entry.setFixedHeight(36)
        self._unit_entry.setToolTip(
            "The URL of a Brightspace unit (a collection of topic pages).\n"
            "Find it by clicking a unit in the course Content table — copy the URL from your browser."
        )
        self._bs_course_hint = QLabel()
        self._bs_course_hint.setObjectName("carried_over_label")
        self._bs_course_hint.setProperty("role", "dim")
        self._bs_course_hint.setWordWrap(True)
        self._bs_course_hint.hide()
        top.addWidget(self._unit_entry)
        top.addWidget(self._bs_course_hint)

        # ── Combined page ────────────────────────────────────────────────────
        self._auto_create_chk = QCheckBox("Create the combined page for me (recommended)")
        self._auto_create_chk.setObjectName("combine_checkbox")
        self._auto_create_chk.setChecked(True)
        self._auto_create_chk.setToolTip(
            "When on, a blank page is created automatically at the end of the unit above,\n"
            "so you don't have to make one in Brightspace and paste its URL.\n"
            "Leave the Target Page URL below blank when this is on."
        )
        self._auto_create_chk.toggled.connect(self._on_auto_toggle)
        top.addWidget(self._auto_create_chk)

        self._target_entry = QLineEdit()
        self._target_entry.setPlaceholderText("Leave blank to auto-create, or paste an existing page URL")
        self._target_entry.setFixedHeight(36)
        self._target_entry.setToolTip(
            "Where the combined output is written.\n"
            "Leave blank (with auto-create on) to have one made for you, or paste the URL\n"
            "of an existing blank HTML topic to reuse it."
        )
        top.addWidget(self._target_entry)
        self._target_hint = QLabel("A blank page will be created for you. Paste a URL here only to reuse an existing page.")
        self._target_hint.setProperty("role", "dim")
        self._target_hint.setWordWrap(True)
        top.addWidget(self._target_hint)

        # ── Style ────────────────────────────────────────────────────────────
        top.addWidget(_form_label("STYLE / THEME"))
        self._swatch_frames, self._selected_theme = _build_theme_swatches(top)

        # ── Advanced (collapsed by default) ───────────────────────────────────
        self._adv_btn = QToolButton()
        self._adv_btn.setObjectName("advanced_toggle_btn")
        self._adv_btn.setText("▸  Advanced settings")
        self._adv_btn.setCheckable(True)
        self._adv_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._adv_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self._adv_btn.setStyleSheet(
            "QToolButton { background: transparent; border: none; color: palette(mid);"
            " padding: 4px 0px; font-size: 11px; font-weight: 700; text-align: left; }"
            "QToolButton:hover { color: palette(text); }"
        )
        self._adv_btn.setToolTip("Batch runs, Moodle name-fixing, and speed. Most runs don't need these.")
        self._adv_btn.toggled.connect(self._on_adv_toggle)
        top.addWidget(self._adv_btn, 0, Qt.AlignmentFlag.AlignLeft)

        self._adv_container = QFrame()
        self._adv_container.setObjectName("advanced_panel")
        adv = QVBoxLayout(self._adv_container)
        adv.setContentsMargins(12, 4, 12, 6)
        adv.setSpacing(6)

        self._multi_unit_chk = QCheckBox("Also do the following units in this course")
        self._multi_unit_chk.setObjectName("chk_also_do_following_units")
        self._multi_unit_chk.setToolTip(
            "After this unit finishes, find the next unit in the course\n"
            "(skipping empty units and units that already have a combined\n"
            "page) and run it too. You'll be asked to confirm before each\n"
            "additional unit unless “Don't ask me before each unit” is also checked."
        )
        self._multi_unit_chk.toggled.connect(self._on_multi_unit_toggle)
        adv.addWidget(self._multi_unit_chk)

        self._auto_continue_chk = QCheckBox("Don't ask me before each unit")
        self._auto_continue_chk.setObjectName("chk_dont_ask_before_each_unit")
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
        adv.addSpacing(2)

        adv.addWidget(_form_label("MOODLE COURSE URL  (optional — fixes odd file names)"))
        self._moodle_entry = QLineEdit()
        self._moodle_entry.setObjectName("moodle_url_edit")
        self._moodle_entry.setPlaceholderText("https://mymoodle.okanagan.bc.ca/course/view.php?id=…")
        self._moodle_entry.setFixedHeight(34)
        adv.addWidget(self._moodle_entry)
        adv.addSpacing(2)

        par_row = QHBoxLayout()
        par_row.setSpacing(10)
        par_row.addWidget(_form_label("SPEED  (pages at once)"), 0, Qt.AlignmentFlag.AlignVCenter)
        self._parallel_spin = QSpinBox()
        self._parallel_spin.setObjectName("speed_spin")
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
        top.addWidget(self._adv_container)
        panel.addSpacing(4)

        # ── Status row (hidden unless running or succeeded) ───────────────────
        self._status_row = QFrame()
        self._status_row.setObjectName("status_row")
        status_layout = QHBoxLayout(self._status_row)
        status_layout.setContentsMargins(10, 6, 10, 6)
        status_layout.setSpacing(8)
        self._status_spinner = QLabel("")
        self._status_spinner.setObjectName("status_spinner")
        self._status_label = QLabel("")
        self._status_label.setObjectName("status_label")
        self._status_label.setWordWrap(True)
        status_layout.addWidget(self._status_spinner, 0, Qt.AlignmentFlag.AlignVCenter)
        status_layout.addWidget(self._status_label, 1, Qt.AlignmentFlag.AlignVCenter)
        self._status_row.setVisible(False)
        panel.addWidget(self._status_row)

        # ── Footer: primary (+ secondary in SUCCESS) always visible ───────────
        self._btn_row = QHBoxLayout()
        self._btn_row.setSpacing(8)
        self._secondary_btn = QPushButton("Create Another")
        self._secondary_btn.setObjectName("secondary_action_btn")
        self._secondary_btn.setProperty("variant", "secondary")
        self._secondary_btn.setFixedHeight(38)
        self._secondary_btn.setMinimumWidth(150)
        self._secondary_btn.clicked.connect(self._on_create_another)
        self._secondary_btn.hide()

        self._run_btn = QPushButton("Create Combined Unit Page")
        self._run_btn.setObjectName("primary_action_btn")
        self._run_btn.setFixedHeight(38)
        self._run_btn.setMinimumWidth(240)
        self._run_btn.setStyleSheet(
            "QPushButton { background-color:#005F63; color:#ffffff; border:none; "
            "border-radius:6px; padding:8px 18px; font-size:13px; font-weight:600; }"
            "QPushButton:hover { background-color:#007a80; }"
            "QPushButton:disabled { background-color:#1a2a2c; color:#636780; }"
        )
        self._run_btn.setToolTip(
            "Scrapes all topic pages in the unit, combines them into one collapsible page,\n"
            "and writes the result to the target page."
        )
        self._run_btn.clicked.connect(self._on_primary_clicked)
        self._btn_row.addWidget(self._secondary_btn)
        self._btn_row.addWidget(self._run_btn, 1)
        panel.addLayout(self._btn_row)

        # ── Log handle + collapsible log panel ────────────────────────────────
        self._log_handle = QToolButton()
        self._log_handle.setObjectName("log_handle_btn")
        self._log_handle.setText("LOG  Collapse ▾")
        self._log_handle.setCheckable(True)
        self._log_handle.setCursor(Qt.CursorShape.PointingHandCursor)
        self._log_handle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self._log_handle.setStyleSheet(
            "QToolButton { background: transparent; border: none; color: palette(mid);"
            " padding: 2px 0px; font-size: 11px; font-weight: 700; text-align: left; }"
            "QToolButton:hover { color: palette(text); }"
        )
        self._log_handle.toggled.connect(self._toggle_log)
        panel.addWidget(self._log_handle, 0, Qt.AlignmentFlag.AlignLeft)

        self._log_panel = QFrame()
        self._log_panel.setObjectName("log_panel")
        log_layout = QVBoxLayout(self._log_panel)
        log_layout.setContentsMargins(0, 4, 0, 0)
        self._log = LogWidget()
        self._log.setMinimumHeight(110)
        self._log.setMaximumHeight(170)
        log_layout.addWidget(self._log)
        self._log_panel.setVisible(True)
        panel.addWidget(self._log_panel)
        self._log_handle.setChecked(True)

        # Outer scroll = small-screen safety net (§6): the card keeps its natural
        # compact height; a scrollbar only appears if the viewport is too short.
        outer_scroll = QScrollArea()
        outer_scroll.setWidgetResizable(True)
        outer_scroll.setFrameShape(QFrame.Shape.NoFrame)
        outer_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        outer_holder = QWidget()
        holder_layout = QVBoxLayout(outer_holder)
        holder_layout.setContentsMargins(0, 0, 0, 0)
        holder_layout.setSpacing(0)
        holder_layout.addWidget(main_panel, 0, Qt.AlignmentFlag.AlignTop)
        holder_layout.addStretch(1)
        outer_scroll.setWidget(outer_holder)
        layout.addWidget(outer_scroll, 1)

        # Restore this panel's URLs, falling back to Checker course URLs where useful.
        cfg = self._mw.load_config() if hasattr(self._mw, "load_config") else {}
        if cfg.get("col_unit_url"):
            self._unit_entry.setText(cfg["col_unit_url"])
        if cfg.get("col_target_url"):
            self._target_entry.setText(cfg["col_target_url"])
        saved_moodle_url = cfg.get("col_moodle_url") or cfg.get("chk_moodle_url")
        if saved_moodle_url:
            self._moodle_entry.setText(saved_moodle_url)
        if cfg.get("chk_bs_url"):
            self._bs_course_hint.setText(f"Course carried over from Checker: {cfg['chk_bs_url']}")
            self._bs_course_hint.show()
        if "col_auto_create" in cfg:
            self._auto_create_chk.setChecked(bool(cfg["col_auto_create"]))
        self._on_auto_toggle(self._auto_create_chk.isChecked())

    # ── Collapse toggles ──────────────────────────────────────────────────────

    def _toggle_log(self, open_: bool):
        self._log_handle.setText("LOG  Collapse ▾" if open_ else "LOG  Expand ▴")
        self._log_panel.setVisible(open_)

    def _on_adv_toggle(self, checked: bool):
        self._adv_container.setVisible(checked)
        self._adv_btn.setText("▾  Advanced settings" if checked else "▸  Advanced settings")

    # ── State machine ─────────────────────────────────────────────────────────

    def _apply_state(self):
        state = self._state
        if state == CollectState.READY:
            self._status_row.setVisible(False)
            self._secondary_btn.hide()
            self._run_btn.show()
            self._run_btn.setText("Create Combined Unit Page")
            self._run_btn.setEnabled(True)
            self._set_inputs_enabled(True)
        elif state == CollectState.RUNNING:
            self._show_status(
                "Collecting pages… this usually takes 1–3 min.", running=True
            )
            self._secondary_btn.hide()
            self._run_btn.show()
            self._run_btn.setText("Collecting… usually 1–3 min")
            self._run_btn.setEnabled(False)
            self._set_inputs_enabled(False)
        elif state == CollectState.SUCCESS:
            n = self._last_page_count
            count_txt = f"{n} topic pages" if n else "topic pages"
            self._show_status(
                f"Done — {count_txt} collected into one Brightspace page", running=False
            )
            self._set_inputs_enabled(True)
            self._secondary_btn.setText("Create Another")
            self._secondary_btn.show()
            self._run_btn.show()
            self._run_btn.setText("Continue →")
            self._run_btn.setEnabled(True)

    def _set_inputs_enabled(self, enabled: bool):
        self._unit_entry.setEnabled(enabled)
        self._target_entry.setEnabled(enabled)
        self._auto_create_chk.setEnabled(enabled)
        self._adv_btn.setEnabled(enabled)
        self._adv_container.setEnabled(enabled)
        self._parallel_spin.setEnabled(enabled)
        self._moodle_entry.setEnabled(enabled)
        self._multi_unit_chk.setEnabled(enabled)
        # dependent checkbox keeps its parent-gated rule when re-enabling
        self._auto_continue_chk.setEnabled(enabled and self._multi_unit_chk.isChecked())
        for swatch in self._swatch_frames.values():
            swatch.setEnabled(enabled)
        # log handle intentionally always clickable

    def _show_status(self, text: str, running: bool):
        self._status_label.setText(text)
        self._status_row.setVisible(True)
        if running:
            self._status_row.setStyleSheet(
                "QFrame#status_row { background:#1a2a2c; border:1px solid #2a4145;"
                " border-radius:6px; }"
            )
            self._status_label.setStyleSheet("color:#c8d4d6;")
        else:
            self._status_spinner.setText("✓")
            self._status_spinner.setStyleSheet("color:#3ddc84; font-weight:700; font-size:14px;")
            self._status_row.setStyleSheet(
                "QFrame#status_row { background:#12331f; border:1px solid #1f6b3a;"
                " border-radius:6px; }"
            )
            self._status_label.setStyleSheet("color:#b7f0cd; font-weight:600;")

    # ── Footer actions ────────────────────────────────────────────────────────

    def _on_primary_clicked(self):
        if self._state == CollectState.SUCCESS:
            self.continue_next.emit()   # existing post-success "Continue" action
        else:
            self._start_run()

    def _on_create_another(self):
        # Reset to READY, preserving all field values (spec §3 SUCCESS).
        self._state = CollectState.READY
        self._status_spinner.setText("")
        self._apply_state()

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

    def save_state(self):
        if not hasattr(self._mw, "save_config"):
            return
        self._mw.save_config({
            "col_unit_url": self._unit_entry.text().strip(),
            "col_target_url": self._target_entry.text().strip(),
            "col_moodle_url": self._moodle_entry.text().strip(),
            "col_auto_create": self._auto_create_chk.isChecked(),
        })

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

        self.save_state()

        theme_name   = self._selected_theme[0]
        theme_colors = PAGE_THEMES[theme_name]
        parallel     = self._parallel_spin.value()

        style_ref_path = Path(__file__).parent.parent.parent / "templates" / "style_reference.html"
        try:
            style_reference_html = style_ref_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            style_reference_html = ""

        self._succeeded = False
        self._last_page_count = None
        self._state = CollectState.RUNNING
        self._apply_state()
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
        # Animate the running spinner off the existing poll tick (no new timer).
        if self._state == CollectState.RUNNING:
            self._spin_idx = (self._spin_idx + 1) % len(_SPINNER_FRAMES)
            self._status_spinner.setText(_SPINNER_FRAMES[self._spin_idx])
            self._status_spinner.setStyleSheet("color:#2ECDDC; font-size:13px;")
        try:
            while True:
                msg, tag = self._log_queue.get_nowait()
                if msg == "__DONE__":
                    # Backend fires __DONE__ on both success and failure; decide
                    # final state from the success sentinel sniffed below.
                    self._state = CollectState.SUCCESS if self._succeeded else CollectState.READY
                    self._apply_state()
                elif msg == "__SUCCESS__":
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
                    # Sniff success + page count off the existing backend log
                    # stream (no backend change): "✓ Text done: N pages" and the
                    # final "✓ Done!" line are the only success signals emitted.
                    if tag == "success":
                        m = re.search(r"Text done:\s*(\d+)\s+pages", msg)
                        if m:
                            self._last_page_count = int(m.group(1))
                        if "Done!" in msg:
                            self._succeeded = True
                    self._log.append_log(msg, tag)
        except queue.Empty:
            pass
