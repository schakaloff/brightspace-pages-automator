import asyncio
import os
import queue
import threading
from enum import Enum, auto

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QLineEdit, QScrollArea, QCheckBox,
    QFrame, QComboBox, QMessageBox, QToolButton,
)
from PySide6.QtCore import QTimer, Qt

from gui_log import LogWidget
from panels._shared import _form_label, _section_header, friendly_error


class KalturaState(Enum):
    EMPTY = auto()
    SCANNING = auto()
    RESULTS = auto()


class VideoGroupWidget(QFrame):
    """One collapsible group per Moodle section: tri-state checkbox header,
    destination combo row, and a hidden-until-expanded video checkbox list."""

    def __init__(self, section_name: str, entries: list[dict],
                 on_selection_changed, parent=None):
        super().__init__(parent)
        self.setObjectName("video_group_widget")
        self.section_name = section_name
        self._on_selection_changed = on_selection_changed
        self._syncing = False
        self.video_checkboxes: list[tuple[QCheckBox, dict]] = []

        self.setFrameShape(QFrame.Shape.StyledPanel)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(4)

        header = QHBoxLayout()
        self.group_checkbox = QCheckBox()
        self.group_checkbox.setObjectName("group_checkbox")
        self.group_checkbox.setTristate(True)
        self.group_checkbox.clicked.connect(self._on_group_clicked)
        header.addWidget(self.group_checkbox)

        title = section_name if section_name else "(no section)"
        self.title_label = QLabel(f"{title} ({len(entries)} video{'s' if len(entries) != 1 else ''})")
        self.title_label.setWordWrap(True)
        header.addWidget(self.title_label, 1)

        self.needs_review_badge = QLabel("Needs review")
        self.needs_review_badge.setObjectName("needs_review_badge")
        self.needs_review_badge.setStyleSheet(
            "background: #7a5b00; color: #ffd75e; border-radius: 8px; padding: 1px 8px;"
        )
        self.needs_review_badge.setVisible(False)
        header.addWidget(self.needs_review_badge)

        self.expand_btn = QToolButton()
        self.expand_btn.setObjectName("group_expand_btn")
        self.expand_btn.setText("▸")
        self.expand_btn.setCheckable(True)
        self.expand_btn.toggled.connect(self._on_expand_toggled)
        header.addWidget(self.expand_btn)
        layout.addLayout(header)

        dest_row = QHBoxLayout()
        self.destination_label = QLabel("→ Destination:")
        self.destination_label.setObjectName("destination_label")
        dest_row.addWidget(self.destination_label)
        self.combo = QComboBox()
        self.combo.addItem("— select module —", None)
        dest_row.addWidget(self.combo, 1)
        layout.addLayout(dest_row)

        self.video_list_container = QWidget()
        vlist = QVBoxLayout(self.video_list_container)
        vlist.setContentsMargins(24, 0, 0, 0)
        vlist.setSpacing(2)
        for entry in entries:
            cb = QCheckBox(entry["name"])
            cb.setObjectName("video_row_checkbox")
            cb.stateChanged.connect(self._on_video_toggled)
            vlist.addWidget(cb)
            self.video_checkboxes.append((cb, entry))
        self.video_list_container.setVisible(False)
        layout.addWidget(self.video_list_container)

    # ── selection logic ────────────────────────────────────────────────────

    def _on_expand_toggled(self, expanded: bool):
        self.expand_btn.setText("▾" if expanded else "▸")
        self.video_list_container.setVisible(expanded)

    def _on_group_clicked(self):
        # user click cycles tri-state; treat partial-click as "check all"
        state = self.group_checkbox.checkState()
        target = state != Qt.CheckState.Unchecked
        self.set_all_checked(target)

    def _on_video_toggled(self, _state):
        if self._syncing:
            return
        self._sync_group_state()
        self._on_selection_changed()

    def _sync_group_state(self):
        checked = sum(1 for cb, _ in self.video_checkboxes if cb.isChecked())
        self._syncing = True
        if checked == 0:
            self.group_checkbox.setCheckState(Qt.CheckState.Unchecked)
        elif checked == len(self.video_checkboxes):
            self.group_checkbox.setCheckState(Qt.CheckState.Checked)
        else:
            self.group_checkbox.setCheckState(Qt.CheckState.PartiallyChecked)
        self._syncing = False

    def set_all_checked(self, checked: bool):
        self._syncing = True
        for cb, _ in self.video_checkboxes:
            cb.setChecked(checked)
        self._syncing = False
        self._sync_group_state()
        self._on_selection_changed()

    def set_needs_review(self, needs_review: bool, no_match: bool = False):
        self.needs_review_badge.setVisible(needs_review)
        if no_match:
            self.destination_label.setText("→ No matching unit found — pick manually:")
            self.destination_label.setStyleSheet("color: #ffd75e;")
        else:
            self.destination_label.setText("→ Destination:")
            self.destination_label.setStyleSheet("")


class KalturaPanel(QWidget):

    def __init__(self, main_window, parent=None):
        super().__init__(parent)
        self._mw = main_window
        self._log_queue: queue.Queue = queue.Queue()
        self._groups: list[VideoGroupWidget] = []
        self._combos: dict[str, QComboBox] = {}   # section_name → QComboBox
        self._bs_modules: list[dict] = []          # [{id, title}] cached after fetch
        self._match_scores: dict[str, float] = {}  # section_name → autosuggest score
        self._state = KalturaState.EMPTY
        self._build()
        self._load_saved_links()
        self._apply_state()
        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._poll_log)
        self._poll_timer.start(100)

    # ── Build UI ──────────────────────────────────────────────────────────────

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 28, 32, 20)
        layout.setSpacing(0)

        layout.addWidget(_section_header("Kaltura → Brightspace Pages"))
        sub = QLabel(
            "Scans Moodle for Kaltura videos, then creates matching pages in Brightspace."
        )
        sub.setProperty("role", "dim")
        sub.setWordWrap(True)
        layout.addWidget(sub)
        layout.addSpacing(16)

        # ── URL fields ────────────────────────────────────────────────────────
        layout.addWidget(_form_label("MOODLE COURSE URL"))
        layout.addSpacing(4)
        self._moodle_url = QLineEdit()
        self._moodle_url.setObjectName("url_moodle_edit")
        self._moodle_url.setPlaceholderText(
            "https://mymoodle.okanagan.bc.ca/course/view.php?id=183744"
        )
        self._moodle_url.setFixedHeight(36)
        layout.addWidget(self._moodle_url)
        layout.addSpacing(8)

        layout.addWidget(_form_label("BRIGHTSPACE COURSE URL"))
        layout.addSpacing(4)
        self._bs_url = QLineEdit()
        self._bs_url.setObjectName("url_brightspace_edit")
        self._bs_url.setPlaceholderText(
            "https://brightspace.okanagan.bc.ca/d2l/home/10263"
        )
        self._bs_url.setFixedHeight(36)
        layout.addWidget(self._bs_url)
        layout.addSpacing(10)

        # ── Advanced settings (collapsed by default) ──────────────────────────
        self._advanced_toggle = QToolButton()
        self._advanced_toggle.setObjectName("advanced_toggle_btn")
        self._advanced_toggle.setText("▸ Advanced settings")
        self._advanced_toggle.setCheckable(True)
        self._advanced_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self._advanced_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self._advanced_toggle.toggled.connect(self._toggle_advanced)
        self._advanced_toggle.setStyleSheet(
            "QToolButton { background: transparent; border: none; color: palette(mid);"
            " padding: 4px 0px; font-size: 11px; font-weight: 700; text-align: left; }"
            "QToolButton:hover { color: palette(text); }"
        )
        layout.addWidget(self._advanced_toggle)

        self._advanced_panel = QFrame()
        self._advanced_panel.setObjectName("advanced_panel")
        self._advanced_panel.setVisible(False)
        adv = QVBoxLayout(self._advanced_panel)
        adv.setContentsMargins(12, 6, 12, 6)
        adv.setSpacing(6)
        adv.addWidget(_form_label("MANUAL STEPS"))

        manual_row = QHBoxLayout()
        manual_row.setSpacing(6)

        manual_btn_style = (
            "QPushButton { background: #e8eef4; border: 1px solid #9aa7b4;"
            " border-radius: 6px; color: #1a2530; padding: 6px 14px;"
            " font-size: 12px; font-weight: 600; }"
            "QPushButton:hover { background: #f4f8fb; border-color: #6b7885; }"
            "QPushButton:disabled { background: #cdd4db; color: #7a8590; }"
        )

        self._login_btn = QPushButton("Login to Moodle")
        self._login_btn.setObjectName("btn_login_moodle")
        self._login_btn.setProperty("variant", "secondary")
        self._login_btn.setMinimumHeight(34)
        self._login_btn.setStyleSheet(manual_btn_style)
        self._login_btn.clicked.connect(self._start_login)
        manual_row.addWidget(self._login_btn)

        self._scan_btn = QPushButton("Scan Moodle only")
        self._scan_btn.setObjectName("btn_scan_moodle_only")
        self._scan_btn.setProperty("variant", "secondary")
        self._scan_btn.setMinimumHeight(34)
        self._scan_btn.setStyleSheet(manual_btn_style)
        self._scan_btn.clicked.connect(self._start_scan)
        manual_row.addWidget(self._scan_btn)

        self._fetch_modules_btn = QPushButton("Load Brightspace modules only")
        self._fetch_modules_btn.setObjectName("btn_load_brightspace_modules")
        self._fetch_modules_btn.setProperty("variant", "secondary")
        self._fetch_modules_btn.setMinimumHeight(34)
        self._fetch_modules_btn.setStyleSheet(manual_btn_style)
        self._fetch_modules_btn.clicked.connect(self._start_fetch_modules)
        manual_row.addWidget(self._fetch_modules_btn)
        manual_row.addStretch()

        adv.addLayout(manual_row)

        layout.addWidget(self._advanced_panel)
        layout.addSpacing(10)

        # ── Results (internal scroll, hard height cap) ────────────────────────
        layout.addWidget(_form_label("RESULTS"))
        layout.addSpacing(4)

        self._results_container = QWidget()
        self._results_container.setObjectName("results_container")
        self._results_layout = QVBoxLayout(self._results_container)
        self._results_layout.setContentsMargins(0, 0, 0, 0)
        self._results_layout.setSpacing(6)
        self._results_layout.addStretch()

        self._placeholder = QLabel("No videos found yet — run a scan to see results here.")
        self._placeholder.setProperty("role", "dim")
        self._placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._placeholder.setWordWrap(True)
        self._results_layout.insertWidget(0, self._placeholder)

        self._results_scroll = QScrollArea()
        self._results_scroll.setObjectName("results_scroll_area")
        self._results_scroll.setWidgetResizable(True)
        self._results_scroll.setWidget(self._results_container)
        self._results_scroll.setMinimumHeight(140)
        self._results_scroll.setMaximumHeight(180)
        self._results_scroll.setFrameShape(QFrame.Shape.StyledPanel)
        self._results_scroll.setStyleSheet(
            "QScrollArea#results_scroll_area { border: 1px solid palette(mid);"
            " border-radius: 6px; }"
        )
        layout.addWidget(self._results_scroll, 1)
        layout.addSpacing(6)

        # ── Footer: primary button + log handle (never scrolls away) ──────────
        self._footer = QFrame()
        footer_layout = QVBoxLayout(self._footer)
        footer_layout.setContentsMargins(0, 0, 0, 0)
        footer_layout.setSpacing(4)

        self._primary_btn = QPushButton("Find Videos && Suggest Destinations")
        self._primary_btn.setObjectName("primary_action_btn")
        self._primary_btn.setFixedHeight(40)
        self._primary_btn.clicked.connect(self._on_primary_clicked)
        footer_layout.addWidget(self._primary_btn)

        self._log_handle = QToolButton()
        self._log_handle.setObjectName("log_handle_btn")
        self._log_handle.setText("LOG  Expand ▴")
        self._log_handle.setCheckable(True)
        self._log_handle.setCursor(Qt.CursorShape.PointingHandCursor)
        self._log_handle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self._log_handle.setStyleSheet(
            "QToolButton { background: transparent; border: none; color: palette(mid);"
            " padding: 2px 0px; font-size: 11px; font-weight: 700; text-align: left; }"
            "QToolButton:hover { color: palette(text); }"
        )
        self._log_handle.toggled.connect(self._toggle_log)
        footer_layout.addWidget(self._log_handle)
        layout.addWidget(self._footer, 0)

        self._log_panel = QFrame()
        self._log_panel.setObjectName("log_panel")
        self._log_panel.setVisible(False)
        log_layout = QVBoxLayout(self._log_panel)
        log_layout.setContentsMargins(0, 4, 0, 0)
        self._log = LogWidget()
        self._log.setMinimumHeight(100)
        self._log.setMaximumHeight(150)
        log_layout.addWidget(self._log)
        layout.addWidget(self._log_panel, 0)

    # ── Collapse toggles ──────────────────────────────────────────────────────

    def _toggle_advanced(self, open_: bool):
        self._advanced_toggle.setText(
            "▾ Advanced settings" if open_ else "▸ Advanced settings"
        )
        self._advanced_panel.setVisible(open_)

    def _toggle_log(self, open_: bool):
        self._log_handle.setText("LOG  Collapse ▾" if open_ else "LOG  Expand ▴")
        self._log_panel.setVisible(open_)

    # ── State machine ─────────────────────────────────────────────────────────

    def _apply_state(self):
        state = self._state
        if state == KalturaState.EMPTY:
            self._placeholder.setText(
                "No videos found yet — run a scan to see results here."
            )
            self._placeholder.setVisible(True)
            self._primary_btn.setText("Find Videos && Suggest Destinations")
            self._primary_btn.setEnabled(True)
            self._set_inputs_enabled(True)
        elif state == KalturaState.SCANNING:
            self._placeholder.setText("Scanning… usually 2–5 min")
            self._placeholder.setVisible(True)
            self._primary_btn.setText("Scanning… usually 2–5 min")
            self._primary_btn.setEnabled(False)
            self._set_inputs_enabled(False)
        elif state == KalturaState.RESULTS:
            self._placeholder.setVisible(False)
            self._set_inputs_enabled(True)
            self._update_primary_button_count()

    def _set_inputs_enabled(self, enabled: bool):
        self._moodle_url.setEnabled(enabled)
        self._bs_url.setEnabled(enabled)
        self._advanced_toggle.setEnabled(enabled)
        self._login_btn.setEnabled(enabled)
        self._scan_btn.setEnabled(enabled)
        self._fetch_modules_btn.setEnabled(enabled)
        # log handle stays clickable in every state

    def _update_primary_button_count(self):
        if self._state != KalturaState.RESULTS:
            return
        count = len(self._selected_entries())
        self._primary_btn.setText(f"Create Pages for {count} Selected Videos")
        self._primary_btn.setEnabled(count > 0)

    def _on_primary_clicked(self):
        if self._state == KalturaState.RESULTS:
            self._start_create_pages()
        else:
            self._start_find_and_suggest()

    # ── Results population ────────────────────────────────────────────────────

    def _clear_groups(self):
        for g in self._groups:
            self._results_layout.removeWidget(g)
            g.deleteLater()
        self._groups.clear()
        self._combos.clear()

    def _populate_groups(self, entries: list[dict]):
        self._clear_groups()
        buckets: dict[str, list[dict]] = {}
        for entry in entries:
            buckets.setdefault(entry.get("section_name", ""), []).append(entry)
        insert_at = 0
        for section, vids in buckets.items():
            group = VideoGroupWidget(section, vids, self._update_primary_button_count)
            group.combo.currentIndexChanged.connect(self._check_mapping_complete)
            self._results_layout.insertWidget(insert_at, group)
            insert_at += 1
            self._groups.append(group)
            if section:
                self._combos[section] = group.combo

    def _apply_default_selection(self):
        """Confident autosuggest (score ≥ 90) → checked; ambiguous or no
        match → unchecked + Needs review badge. Uses only match_sections
        output already computed in _maybe_autosuggest."""
        for group in self._groups:
            score = self._match_scores.get(group.section_name, 0)
            has_dest = group.combo.currentData() is not None
            if has_dest and score >= 90:
                group.set_needs_review(False)
                group.set_all_checked(True)
            else:
                group.set_needs_review(True, no_match=not has_dest)
                group.set_all_checked(False)
        self._update_primary_button_count()

    def _selected_entries(self) -> list[dict]:
        return [
            entry
            for group in self._groups
            for cb, entry in group.video_checkboxes
            if cb.isChecked()
        ]

    # ── Mapping helpers ───────────────────────────────────────────────────────

    def _populate_combos(self, modules: list[dict]):
        """Fill QComboBox options from fetched BS module list."""
        self._bs_modules = modules
        for combo in self._combos.values():
            # Keep placeholder at index 0, clear any previously loaded options
            while combo.count() > 1:
                combo.removeItem(1)
            for mod in modules:
                combo.addItem(mod["title"], mod["id"])
        self._check_mapping_complete()

    def _check_mapping_complete(self):
        self._update_primary_button_count()

    def _maybe_autosuggest(self):
        """Pre-select high-confidence Brightspace module matches per Moodle section.

        Safe to call after either the scan or the module fetch completes (whichever
        finishes first — this is a no-op until both are ready). Never overrides a
        combo the user (or a prior auto-suggest) already set, so it's safe to call
        more than once (e.g. if modules are re-fetched).
        """
        if not self._combos or not self._bs_modules:
            return
        from content_matcher import match_sections
        matches = match_sections(list(self._combos.keys()), self._bs_modules)
        for name, (module, score) in matches.items():
            self._match_scores[name] = score if module is not None else 0
            combo = self._combos.get(name)
            if combo is None or combo.currentData() is not None:
                continue
            if module is None or score < 75:
                continue
            idx = combo.findData(module["id"])
            if idx < 0:
                continue
            combo.setCurrentIndex(idx)
        self._check_mapping_complete()

    def _build_section_map(self) -> dict[str, str]:
        """Return {section_name: bs_module_id} from current combo selections."""
        return {
            name: combo.currentData()
            for name, combo in self._combos.items()
            if combo.currentData() is not None
        }

    def _load_saved_links(self):
        if not hasattr(self._mw, "load_config"):
            return
        cfg = self._mw.load_config()
        self._moodle_url.setText(cfg.get("kaltura_moodle_url", ""))
        self._bs_url.setText(cfg.get("kaltura_bs_url", ""))

    def save_state(self):
        if not hasattr(self._mw, "save_config"):
            return
        self._mw.save_config({
            "kaltura_moodle_url": self._moodle_url.text().strip(),
            "kaltura_bs_url": self._bs_url.text().strip(),
        })

    # ── Workers ───────────────────────────────────────────────────────────────

    def _start_login(self):
        url = self._moodle_url.text().strip() or "https://mymoodle.okanagan.bc.ca"
        self._login_btn.setText("Logging in…")
        self._login_btn.setEnabled(False)
        q = self._log_queue

        moodle_user = self._mw.moodle_username
        moodle_pass = self._mw.moodle_password

        def worker():
            try:
                from kaltura_categorizer import KalturaCategorizer
                q.put(("Logging in to Moodle…", "dim"))
                asyncio.run(KalturaCategorizer().login_to_moodle(
                    url,
                    moodle_username=moodle_user,
                    moodle_password=moodle_pass,
                    log_fn=lambda msg, tag="dim": q.put((msg, tag)),
                ))
                q.put(("__LOGIN_DONE__", None))
            except Exception as e:
                msg, detail = friendly_error(e)
                q.put((f"Login error: {msg}", "error"))
                if detail != msg:
                    q.put((detail, "detail"))
                q.put(("__LOGIN_DONE__", None))

        threading.Thread(target=worker, daemon=True).start()

    def _start_find_and_suggest(self):
        """Orchestrates the existing manual steps in one click: Moodle session
        check/login (only if no saved session), scan, Brightspace module fetch,
        then auto-suggest mapping. Reuses the same backend calls as the manual
        buttons — no scanning/mapping/write logic changes here.
        """
        moodle_url = self._moodle_url.text().strip()
        bs_url = self._bs_url.text().strip()
        if not moodle_url:
            self._log.append_log("Paste a Moodle course URL first.", "warning")
            return
        if not bs_url:
            self._log.append_log("Paste a Brightspace course URL first.", "warning")
            return

        self._state = KalturaState.SCANNING
        self._clear_groups()
        self._apply_state()
        self._log.clear_log()
        q = self._log_queue

        moodle_user = self._mw.moodle_username
        moodle_pass = self._mw.moodle_password
        bs_user = self._mw.bs_username
        bs_pass = self._mw.bs_password
        sso_email = self._mw.sso_email
        sso_pass = self._mw.sso_password

        def worker():
            try:
                from kaltura_categorizer import KalturaCategorizer, MOODLE_SESSION_FILE
                cat = KalturaCategorizer()

                q.put(("Step 1/4 — Checking Moodle session…", "dim"))
                if not os.path.exists(MOODLE_SESSION_FILE):
                    q.put(("No saved Moodle session — logging in first (this may open a browser window)…", "dim"))
                    asyncio.run(cat.login_to_moodle(
                        moodle_url,
                        moodle_username=moodle_user,
                        moodle_password=moodle_pass,
                        log_fn=lambda msg, tag="dim": q.put((msg, tag)),
                    ))
                else:
                    q.put(("Moodle session found — skipping login.", "dim"))

                q.put(("Step 2/4 — Scanning Moodle course for Kaltura videos… "
                       "this can take 5-10 minutes on large courses with many book chapters.", "dim"))
                entries = asyncio.run(cat.scan_moodle_course(
                    moodle_url, log_fn=lambda msg, tag="dim": q.put((msg, tag))
                ))
                q.put((f"Scan finished — {len(entries)} video(s) found.", "dim"))

                q.put(("Step 3/4 — Loading Brightspace modules…", "dim"))
                modules = asyncio.run(cat.get_bs_modules(
                    bs_url,
                    bs_username=bs_user,
                    bs_password=bs_pass,
                    sso_email=sso_email,
                    sso_password=sso_pass,
                    log_fn=lambda msg, tag="dim": q.put((msg, tag)),
                ))
                q.put((f"Loaded {len(modules)} Brightspace module(s).", "dim"))

                q.put(("Step 4/4 — Matching Moodle sections to Brightspace modules…", "dim"))
                q.put(("__FIND_SUGGEST_DONE__", (entries, modules)))
            except Exception as e:
                msg, detail = friendly_error(e)
                q.put((f"Find Videos & Suggest Destinations error: {msg}", "error"))
                if detail != msg:
                    q.put((detail, "detail"))
                q.put(("__FIND_SUGGEST_FAIL__", None))

        threading.Thread(target=worker, daemon=True).start()

    def _start_scan(self):
        url = self._moodle_url.text().strip()
        if not url:
            self._log.append_log("Paste a Moodle course URL first.", "warning")
            return
        self._scan_btn.setText("Scanning…")
        self._scan_btn.setEnabled(False)
        self._log.clear_log()
        q = self._log_queue

        def worker():
            try:
                from kaltura_categorizer import KalturaCategorizer
                q.put(("Scanning Moodle course…", "dim"))
                entries = asyncio.run(KalturaCategorizer().scan_moodle_course(
                    url, log_fn=lambda msg, tag="dim": q.put((msg, tag))
                ))
                q.put(("__SCAN_DONE__", entries))
            except Exception as e:
                msg, detail = friendly_error(e)
                q.put((f"Scan error: {msg}", "error"))
                if detail != msg:
                    q.put((detail, "detail"))
                q.put(("__SCAN_FAIL__", None))

        threading.Thread(target=worker, daemon=True).start()

    def _start_fetch_modules(self):
        bs_url = self._bs_url.text().strip()
        if not bs_url:
            self._log.append_log("Enter Brightspace course URL first.", "warning")
            return
        self._fetch_modules_btn.setText("Fetching…")
        self._fetch_modules_btn.setEnabled(False)
        q = self._log_queue
        bs_user = self._mw.bs_username
        bs_pass = self._mw.bs_password
        sso_email = self._mw.sso_email
        sso_pass = self._mw.sso_password

        def worker():
            try:
                from kaltura_categorizer import KalturaCategorizer
                q.put(("Fetching Brightspace modules…", "dim"))
                modules = asyncio.run(KalturaCategorizer().get_bs_modules(
                    bs_url,
                    bs_username=bs_user,
                    bs_password=bs_pass,
                    sso_email=sso_email,
                    sso_password=sso_pass,
                    log_fn=lambda msg, tag="dim": q.put((msg, tag)),
                ))
                q.put(("__MODULES_DONE__", modules))
            except Exception as e:
                msg, detail = friendly_error(e)
                q.put((f"Fetch modules error: {msg}", "error"))
                if detail != msg:
                    q.put((detail, "detail"))
                q.put(("__MODULES_FAIL__", None))

        threading.Thread(target=worker, daemon=True).start()

    def _start_create_pages(self):
        entries = self._selected_entries()
        bs_url = self._bs_url.text().strip()
        section_map = self._build_section_map()
        if not entries:
            self._log.append_log("No videos selected.", "warning")
            return
        if not bs_url:
            self._log.append_log("Enter Brightspace course URL first.", "warning")
            return
        if not section_map:
            self._log.append_log("Map sections to modules first.", "warning")
            return

        module_count = len(set(section_map.values()))
        confirm = QMessageBox.warning(
            self,
            "Create Brightspace Pages?",
            f"This will create {len(entries)} page(s) in Brightspace, "
            f"across {module_count} mapped module(s).\n\n"
            "This writes directly to the live Brightspace course — it cannot be undone automatically.\n\n"
            "Proceed?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            self._log.append_log("Create Pages cancelled — no pages created.", "dim")
            return

        self._primary_btn.setText("Running…")
        self._primary_btn.setEnabled(False)
        self._set_inputs_enabled(False)
        q = self._log_queue
        kmc_user = self._mw.kmc_username
        kmc_pass = self._mw.kmc_password

        def worker():
            try:
                from kaltura_categorizer import KalturaCategorizer
                asyncio.run(KalturaCategorizer().embed_entries(
                    entries,
                    section_map,
                    bs_url,
                    log_fn=lambda msg, tag="info": q.put((msg, tag)),
                    kmc_username=kmc_user,
                    kmc_password=kmc_pass,
                ))
                q.put(("__CAT_DONE__", None))
            except Exception as e:
                msg, detail = friendly_error(e)
                q.put((f"Error: {msg}", "error"))
                if detail != msg:
                    q.put((detail, "detail"))
                q.put(("__CAT_DONE__", None))

        threading.Thread(target=worker, daemon=True).start()

    # ── Poll ──────────────────────────────────────────────────────────────────

    def _poll_log(self):
        try:
            while True:
                msg, payload = self._log_queue.get_nowait()
                if msg == "__LOGIN_DONE__":
                    self._login_btn.setText("Login to Moodle")
                    self._login_btn.setEnabled(True)
                    self._log.append_log("Moodle session saved — ready to scan.", "success")
                elif msg == "__SCAN_DONE__":
                    entries = payload
                    self._scan_btn.setText("Scan Moodle only")
                    self._scan_btn.setEnabled(True)
                    if entries:
                        self._populate_groups(entries)
                        if self._bs_modules:
                            self._populate_combos(self._bs_modules)
                        self._maybe_autosuggest()
                        self._apply_default_selection()
                        self._state = KalturaState.RESULTS
                    else:
                        self._state = KalturaState.EMPTY
                    self._apply_state()
                    self._log.append_log(f"Found {len(entries)} Kaltura video(s).", "success")
                elif msg == "__SCAN_FAIL__":
                    self._scan_btn.setText("Scan Moodle only")
                    self._scan_btn.setEnabled(True)
                    self._clear_groups()
                    self._state = KalturaState.EMPTY
                    self._apply_state()
                elif msg == "__MODULES_DONE__":
                    modules = payload
                    self._fetch_modules_btn.setText("Load Brightspace modules only")
                    self._fetch_modules_btn.setEnabled(True)
                    self._populate_combos(modules)
                    self._maybe_autosuggest()
                    if self._groups:
                        self._apply_default_selection()
                    self._log.append_log(f"Loaded {len(modules)} module(s).", "success")
                elif msg == "__MODULES_FAIL__":
                    self._fetch_modules_btn.setText("Load Brightspace modules only")
                    self._fetch_modules_btn.setEnabled(True)
                elif msg == "__CAT_DONE__":
                    self._set_inputs_enabled(True)
                    self._apply_state()
                elif msg == "__FIND_SUGGEST_DONE__":
                    entries, modules = payload
                    if entries:
                        self._populate_groups(entries)
                        self._populate_combos(modules)
                        self._maybe_autosuggest()
                        self._apply_default_selection()
                        self._state = KalturaState.RESULTS
                        self._apply_state()
                        self._log.append_log(
                            f"Done — {len(entries)} video(s) found, {len(modules)} module(s) loaded. "
                            "Review the suggested groups above before creating pages.",
                            "success",
                        )
                    else:
                        self._state = KalturaState.EMPTY
                        self._apply_state()
                        self._placeholder.setText(
                            "Scan finished — no Kaltura videos found in this course."
                        )
                        self._log.append_log("No Kaltura videos found.", "warning")
                elif msg == "__FIND_SUGGEST_FAIL__":
                    self._state = KalturaState.EMPTY
                    self._apply_state()
                    self._placeholder.setText(
                        "Scan failed — expand the log below for details, then try again."
                    )
                else:
                    self._log.append_log(msg, payload)
        except queue.Empty:
            pass

    def apply_theme(self, colors: dict):
        pass
