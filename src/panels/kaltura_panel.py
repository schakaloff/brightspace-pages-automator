import asyncio
import queue
import threading

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QLineEdit, QScrollArea, QCheckBox,
    QFrame, QGridLayout, QComboBox,
)
from PySide6.QtCore import QTimer

from gui_log import LogWidget
from panels._shared import _form_label, _section_header


class KalturaPanel(QWidget):

    def __init__(self, main_window, parent=None):
        super().__init__(parent)
        self._mw = main_window
        self._log_queue: queue.Queue = queue.Queue()
        self._checkboxes: list[tuple[QCheckBox, dict]] = []
        self._combos: dict[str, QComboBox] = {}   # section_name → QComboBox
        self._bs_modules: list[dict] = []          # [{id, title}] cached after fetch
        self._build()
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
        self._moodle_url = QLineEdit()
        self._moodle_url.setPlaceholderText(
            "https://mymoodle.okanagan.bc.ca/course/view.php?id=183744"
        )
        self._moodle_url.setFixedHeight(40)
        layout.addWidget(self._moodle_url)
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

        for row, name in enumerate(section_names):
            lbl = QLabel(name)
            lbl.setWordWrap(True)
            combo = QComboBox()
            combo.addItem("— select module —", None)
            combo.currentIndexChanged.connect(self._check_mapping_complete)
            self._mapping_grid.addWidget(lbl, row, 0)
            self._mapping_grid.addWidget(combo, row, 1)
            self._combos[name] = combo

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

    def _build_section_map(self) -> dict[str, str]:
        """Return {section_name: bs_module_id} from current combo selections."""
        return {
            name: combo.currentData()
            for name, combo in self._combos.items()
            if combo.currentData() is not None
        }

    # ── Workers ───────────────────────────────────────────────────────────────

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
                entries = asyncio.run(KalturaCategorizer().scan_moodle_course(url))
                q.put(("__SCAN_DONE__", entries))
            except Exception as e:
                q.put((f"Scan error: {e}", "error"))
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

        def worker():
            try:
                from kaltura_categorizer import KalturaCategorizer
                q.put(("Fetching Brightspace modules…", "dim"))
                modules = asyncio.run(KalturaCategorizer().get_bs_modules(bs_url))
                q.put(("__MODULES_DONE__", modules))
            except Exception as e:
                q.put((f"Fetch modules error: {e}", "error"))
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
        self._create_btn.setText("Running…")
        self._create_btn.setEnabled(False)
        self._scan_btn.setEnabled(False)
        q = self._log_queue

        def worker():
            try:
                from kaltura_categorizer import KalturaCategorizer
                asyncio.run(KalturaCategorizer().embed_entries(
                    entries,
                    section_map,
                    bs_url,
                    log_fn=lambda msg, tag="info": q.put((msg, tag)),
                ))
                q.put(("__CAT_DONE__", None))
            except Exception as e:
                q.put((f"Error: {e}", "error"))
                q.put(("__CAT_DONE__", None))

        threading.Thread(target=worker, daemon=True).start()

    # ── Poll ──────────────────────────────────────────────────────────────────

    def _poll_log(self):
        try:
            while True:
                msg, payload = self._log_queue.get_nowait()
                if msg == "__SCAN_DONE__":
                    entries = payload
                    self._scan_btn.setText("Scan Moodle")
                    self._scan_btn.setEnabled(True)
                    self._populate_list(entries)
                    section_names = list(dict.fromkeys(
                        e.get("section_name", "") for e in entries if e.get("section_name")
                    ))
                    self._build_mapping_rows(section_names)
                    self._mapping_frame.setVisible(bool(entries))
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
                    self._log.append_log(f"Loaded {len(modules)} module(s).", "success")
                elif msg == "__MODULES_FAIL__":
                    self._fetch_modules_btn.setText("Fetch BS Modules")
                    self._fetch_modules_btn.setEnabled(True)
                elif msg == "__CAT_DONE__":
                    self._create_btn.setText("Create Pages")
                    self._check_mapping_complete()
                    self._scan_btn.setEnabled(True)
                else:
                    self._log.append_log(msg, payload)
        except queue.Empty:
            pass

    def apply_theme(self, colors: dict):
        pass
