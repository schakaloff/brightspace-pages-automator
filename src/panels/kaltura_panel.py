import asyncio
import os
import queue
import threading

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QLineEdit, QScrollArea, QCheckBox,
    QFrame, QGridLayout, QComboBox, QMessageBox,
)
from PySide6.QtCore import QTimer

from gui_log import LogWidget
from panels._shared import _form_label, _section_header, friendly_error


class KalturaPanel(QWidget):

    def __init__(self, main_window, parent=None):
        super().__init__(parent)
        self._mw = main_window
        self._log_queue: queue.Queue = queue.Queue()
        self._checkboxes: list[tuple[QCheckBox, dict]] = []
        self._combos: dict[str, QComboBox] = {}   # section_name → QComboBox
        self._section_labels: dict[str, QLabel] = {}  # section_name → QLabel (for Needs Review tag)
        self._bs_modules: list[dict] = []          # [{id, title}] cached after fetch
        self._build()
        self._load_saved_links()
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
        layout.addSpacing(20)

        layout.addWidget(_form_label("MOODLE COURSE URL"))
        layout.addSpacing(4)
        moodle_row = QHBoxLayout()
        self._moodle_url = QLineEdit()
        self._moodle_url.setPlaceholderText(
            "https://mymoodle.okanagan.bc.ca/course/view.php?id=183744"
        )
        self._moodle_url.setFixedHeight(40)
        moodle_row.addWidget(self._moodle_url)
        self._login_btn = QPushButton("Login to Moodle")
        self._login_btn.setFixedHeight(40)
        self._login_btn.clicked.connect(self._start_login)
        moodle_row.addWidget(self._login_btn)
        layout.addLayout(moodle_row)
        layout.addSpacing(12)

        bs_row = QHBoxLayout()
        bs_col = QVBoxLayout()
        bs_col.setSpacing(4)
        bs_col.addWidget(_form_label("BRIGHTSPACE COURSE URL"))
        self._bs_url = QLineEdit()
        self._bs_url.setPlaceholderText(
            "https://brightspace.okanagan.bc.ca/d2l/home/10263"
        )
        self._bs_url.setFixedHeight(40)
        bs_col.addWidget(self._bs_url)
        bs_row.addLayout(bs_col)
        bs_row.addSpacing(12)
        self._scan_btn = QPushButton("Scan Moodle")
        self._scan_btn.setFixedHeight(40)
        self._scan_btn.clicked.connect(self._start_scan)
        bs_row.addWidget(self._scan_btn, 0)
        layout.addLayout(bs_row)
        layout.addSpacing(16)

        self._find_suggest_btn = QPushButton("Find Videos && Suggest Destinations")
        self._find_suggest_btn.setFixedHeight(44)
        self._find_suggest_btn.clicked.connect(self._start_find_and_suggest)
        layout.addWidget(self._find_suggest_btn)
        layout.addSpacing(16)

        layout.addWidget(_form_label("FOUND VIDEOS"))
        layout.addSpacing(4)

        self._list_widget = QWidget()
        self._list_layout = QVBoxLayout(self._list_widget)
        self._list_layout.setContentsMargins(0, 0, 0, 0)
        self._list_layout.setSpacing(4)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self._list_widget)
        scroll.setFixedHeight(180)
        layout.addWidget(scroll)
        layout.addSpacing(8)

        sel_row = QHBoxLayout()
        self._select_all_btn = QPushButton("Select All")
        self._select_all_btn.setFixedHeight(30)
        self._select_all_btn.clicked.connect(lambda: self._set_all(True))
        self._deselect_btn = QPushButton("Deselect All")
        self._deselect_btn.setFixedHeight(30)
        self._deselect_btn.clicked.connect(lambda: self._set_all(False))
        sel_row.addWidget(self._select_all_btn)
        sel_row.addWidget(self._deselect_btn)
        sel_row.addStretch()
        layout.addLayout(sel_row)
        layout.addSpacing(16)

        # ── Map Sections area (hidden until scan) ─────────────────────────────
        self._mapping_frame = QFrame()
        self._mapping_frame.setVisible(False)
        mapping_layout = QVBoxLayout(self._mapping_frame)
        mapping_layout.setContentsMargins(0, 0, 0, 0)
        mapping_layout.setSpacing(8)

        map_header_row = QHBoxLayout()
        map_header_row.addWidget(_form_label("MAP SECTIONS → BRIGHTSPACE MODULES"))
        map_header_row.addStretch()
        self._fetch_modules_btn = QPushButton("Fetch BS Modules")
        self._fetch_modules_btn.setFixedHeight(32)
        self._fetch_modules_btn.clicked.connect(self._start_fetch_modules)
        map_header_row.addWidget(self._fetch_modules_btn)
        mapping_layout.addLayout(map_header_row)

        self._mapping_grid_widget = QWidget()
        self._mapping_grid = QGridLayout(self._mapping_grid_widget)
        self._mapping_grid.setContentsMargins(0, 0, 0, 0)
        self._mapping_grid.setSpacing(6)
        self._mapping_grid.setColumnStretch(1, 1)
        mapping_layout.addWidget(self._mapping_grid_widget)

        layout.addWidget(self._mapping_frame)
        layout.addSpacing(12)

        self._create_btn = QPushButton("Create Pages")
        self._create_btn.setFixedHeight(42)
        self._create_btn.setEnabled(False)
        self._create_btn.clicked.connect(self._start_create_pages)
        layout.addWidget(self._create_btn)
        layout.addSpacing(8)

        layout.addWidget(_form_label("LOG"))
        layout.addSpacing(4)
        self._log = LogWidget()
        layout.addWidget(self._log, 1)

    # ── List helpers ──────────────────────────────────────────────────────────

    def _populate_list(self, entries: list[dict]):
        while self._list_layout.count():
            item = self._list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._checkboxes.clear()
        for entry in entries:
            section = entry.get("section_name", "")
            label = f"[{section}] {entry['name']}" if section else entry["name"]
            cb = QCheckBox(label)
            cb.setChecked(True)
            self._list_layout.addWidget(cb)
            self._checkboxes.append((cb, entry))

    def _set_all(self, checked: bool):
        for cb, _ in self._checkboxes:
            cb.setChecked(checked)

    def _selected_entries(self) -> list[dict]:
        return [entry for cb, entry in self._checkboxes if cb.isChecked()]

    # ── Mapping helpers ───────────────────────────────────────────────────────

    def _build_mapping_rows(self, section_names: list[str]):
        """Create one label + QComboBox row per unique section name."""
        # Clear existing rows
        while self._mapping_grid.count():
            item = self._mapping_grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._combos.clear()
        self._section_labels.clear()

        for row, name in enumerate(section_names):
            lbl = QLabel(name)
            lbl.setWordWrap(True)
            combo = QComboBox()
            combo.addItem("— select module —", None)
            combo.currentIndexChanged.connect(self._check_mapping_complete)
            self._mapping_grid.addWidget(lbl, row, 0)
            self._mapping_grid.addWidget(combo, row, 1)
            self._combos[name] = combo
            self._section_labels[name] = lbl

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
        """Enable Create Pages only when every section combo has a non-placeholder selection."""
        if not self._combos:
            self._create_btn.setEnabled(False)
            return
        all_selected = all(
            combo.currentData() is not None
            for combo in self._combos.values()
        )
        self._create_btn.setEnabled(all_selected)

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
            combo = self._combos.get(name)
            if combo is None or combo.currentData() is not None:
                continue
            if module is None or score < 75:
                continue
            idx = combo.findData(module["id"])
            if idx < 0:
                continue
            combo.setCurrentIndex(idx)
            lbl = self._section_labels.get(name)
            if lbl is not None and score < 90:
                lbl.setText(f"{name}  ⚠ Needs review")
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
        buttons below — no scanning/mapping/write logic changes here.
        """
        moodle_url = self._moodle_url.text().strip()
        bs_url = self._bs_url.text().strip()
        if not moodle_url:
            self._log.append_log("Paste a Moodle course URL first.", "warning")
            return
        if not bs_url:
            self._log.append_log("Paste a Brightspace course URL first.", "warning")
            return

        self._find_suggest_btn.setText("Working…")
        self._find_suggest_btn.setEnabled(False)
        self._login_btn.setEnabled(False)
        self._scan_btn.setEnabled(False)
        self._fetch_modules_btn.setEnabled(False)
        self._create_btn.setEnabled(False)
        self._mapping_frame.setVisible(False)
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
        self._create_btn.setEnabled(False)
        self._mapping_frame.setVisible(False)
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

        self._create_btn.setText("Running…")
        self._create_btn.setEnabled(False)
        self._scan_btn.setEnabled(False)
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
                    self._scan_btn.setText("Scan Moodle")
                    self._scan_btn.setEnabled(True)
                    self._populate_list(entries)
                    section_names = list(dict.fromkeys(
                        e.get("section_name", "") for e in entries if e.get("section_name")
                    ))
                    self._build_mapping_rows(section_names)
                    self._mapping_frame.setVisible(bool(entries))
                    self._maybe_autosuggest()
                    self._log.append_log(f"Found {len(entries)} Kaltura video(s).", "success")
                elif msg == "__SCAN_FAIL__":
                    self._scan_btn.setText("Scan Moodle")
                    self._scan_btn.setEnabled(True)
                    self._create_btn.setEnabled(False)
                    self._mapping_frame.setVisible(False)
                    self._populate_list([])
                elif msg == "__MODULES_DONE__":
                    modules = payload
                    self._fetch_modules_btn.setText("Fetch BS Modules")
                    self._fetch_modules_btn.setEnabled(True)
                    self._populate_combos(modules)
                    self._maybe_autosuggest()
                    self._log.append_log(f"Loaded {len(modules)} module(s).", "success")
                elif msg == "__MODULES_FAIL__":
                    self._fetch_modules_btn.setText("Fetch BS Modules")
                    self._fetch_modules_btn.setEnabled(True)
                elif msg == "__CAT_DONE__":
                    self._create_btn.setText("Create Pages")
                    self._check_mapping_complete()
                    self._scan_btn.setEnabled(True)
                elif msg == "__FIND_SUGGEST_DONE__":
                    entries, modules = payload
                    self._find_suggest_btn.setText("Find Videos && Suggest Destinations")
                    self._find_suggest_btn.setEnabled(True)
                    self._login_btn.setEnabled(True)
                    self._scan_btn.setEnabled(True)
                    self._fetch_modules_btn.setEnabled(True)
                    self._populate_list(entries)
                    section_names = list(dict.fromkeys(
                        e.get("section_name", "") for e in entries if e.get("section_name")
                    ))
                    self._build_mapping_rows(section_names)
                    self._mapping_frame.setVisible(bool(entries))
                    self._populate_combos(modules)
                    self._maybe_autosuggest()
                    self._log.append_log(
                        f"Done — {len(entries)} video(s) found, {len(modules)} module(s) loaded. "
                        "Review the suggested mapping below before creating pages.",
                        "success",
                    )
                elif msg == "__FIND_SUGGEST_FAIL__":
                    self._find_suggest_btn.setText("Find Videos && Suggest Destinations")
                    self._find_suggest_btn.setEnabled(True)
                    self._login_btn.setEnabled(True)
                    self._scan_btn.setEnabled(True)
                    self._fetch_modules_btn.setEnabled(True)
                else:
                    self._log.append_log(msg, payload)
        except queue.Empty:
            pass

    def apply_theme(self, colors: dict):
        pass
