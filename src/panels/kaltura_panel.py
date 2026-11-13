from enum import Enum, auto

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QLineEdit, QScrollArea, QCheckBox,
    QFrame, QMessageBox, QToolButton, QDialog,
    QSizePolicy, QInputDialog, QComboBox,
)
from PySide6.QtCore import Qt, Signal, QSize
from PySide6.QtGui import QGuiApplication, QIcon

import gui_styles
from gui_tool_icons import tool_pixmap
from panels._shared import NoScrollComboBox


# Marker for the "create a new module" dropdown row. A selected pending module
# stores (NEW_MODULE_SENTINEL, name); existing modules store a plain id string,
# so the two are always distinguishable downstream.
NEW_MODULE_SENTINEL = "__NEW__"
NEW_MODULE_LABEL = "Create new unit…"

# Qt's "no maximum" sentinel. Not exported by PySide6, so spelled out here.
_QWIDGETSIZE_MAX = 16777215


STATUS_NAMES = dict(ready='Ready', preparing='Retrieving embed', pending='Save needs checking',
    needs_check='Needs checking', failed='Failed before page creation', created='Created and verified',
    already_present='Already present · kept')
# Pill tone per state; values match the QLabel[role="status"][state=…] styles.
STATUS_TONES = dict(created='created', already_present='created',
    pending='review', needs_check='review', failed='review')


class KalturaState(Enum):
    EMPTY = auto()
    SCANNING = auto()
    RESULTS = auto()


class _VideoLabel(QLabel):
    """Keep wrapped video titles clickable like a native checkbox label."""
    def __init__(self, text, checkbox):
        super().__init__(text)
        self.checkbox = checkbox
        self.setBuddy(checkbox)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.checkbox.isEnabled():
            self.checkbox.click()
            event.accept()
        else:
            super().mouseReleaseEvent(event)


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
        self.status_labels = {}
        self._no_match = False

        self.setFrameShape(QFrame.Shape.StyledPanel)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 10, 6)
        layout.setSpacing(6)

        header = QHBoxLayout()
        header.setSpacing(10)
        self.group_checkbox = QCheckBox()
        self.group_checkbox.setObjectName("group_checkbox")
        self.group_checkbox.setTristate(True)
        self.group_checkbox.setAccessibleName(f"Select all videos in {section_name or 'unnamed section'}")
        self.group_checkbox.clicked.connect(self._on_group_clicked)
        header.addWidget(self.group_checkbox, 0, Qt.AlignmentFlag.AlignTop)

        heading = QVBoxLayout()
        heading.setSpacing(4)
        self.title_label = QLabel(section_name if section_name else "(no section)")
        self.title_label.setTextFormat(Qt.TextFormat.PlainText)
        self.title_label.setWordWrap(True)
        self.title_label.setProperty("role", "field-label")
        # Badges share the title line so the destination row below gets the full width.
        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        title_row.addWidget(self.title_label, 1)
        heading.addLayout(title_row)
        header.addLayout(heading, 1)

        self.count_badge = QLabel()
        self.count_badge.setProperty("role", "status")
        title_row.addWidget(self.count_badge, 0, Qt.AlignmentFlag.AlignTop)

        self.needs_review_badge = QLabel("Needs review")
        self.needs_review_badge.setObjectName("needs_review_badge")
        self.needs_review_badge.setStyleSheet(
            "background: #7a5b00; color: #ffd75e; border-radius: 8px; padding: 1px 8px;"
        )
        self.needs_review_badge.setVisible(False)
        title_row.addWidget(self.needs_review_badge, 0, Qt.AlignmentFlag.AlignTop)

        self.expand_btn = QToolButton()
        self.expand_btn.setObjectName("group_expand_btn")
        self.expand_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.expand_btn.setIconSize(QSize(18, 18))
        self.expand_btn.setFixedSize(32, 32)
        self.expand_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.expand_btn.setCheckable(True)
        self.expand_btn.toggled.connect(self._on_expand_toggled)
        header.addWidget(self.expand_btn, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(header)

        # Destination sits under the section title so it reads as "section → unit".
        dest_row = QHBoxLayout()
        dest_row.setSpacing(8)
        self.destination_label = QLabel("Destination")
        self.destination_label.setObjectName("destination_label")
        self.destination_label.setProperty("role", "hint")
        self.destination_label.setWordWrap(True)
        self.destination_label.setMinimumWidth(0)
        self.destination_label.setMaximumWidth(200)
        dest_row.addWidget(self.destination_label)
        self.combo = NoScrollComboBox()
        self.combo.setMinimumWidth(0)
        self.combo.setMinimumContentsLength(8)
        self.combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.combo.setAccessibleName(f"Brightspace destination for {section_name or 'unnamed section'}")
        self.combo.addItem("— select module —", None)
        dest_row.addWidget(self.combo, 1)
        heading.addLayout(dest_row)

        self.video_list_container = QWidget()
        vlist = QVBoxLayout(self.video_list_container)
        vlist.setContentsMargins(0, 4, 0, 0)
        vlist.setSpacing(0)
        self.status_pills = {}
        for entry in entries:
            frame = QFrame()
            frame.setProperty('role', 'video-row')
            row = QHBoxLayout(frame)
            row.setContentsMargins(28, 8, 4, 8)
            row.setSpacing(10)
            cb = QCheckBox()
            cb.setAccessibleName(entry['name'])
            cb.setObjectName("video_row_checkbox")
            cb.stateChanged.connect(self._on_video_toggled)
            row.addWidget(cb, 0, Qt.AlignmentFlag.AlignTop)
            text = QVBoxLayout()
            text.setSpacing(2)
            title = _VideoLabel(entry['name'], cb)
            title.setTextFormat(Qt.TextFormat.PlainText)
            title.setWordWrap(True)
            title.setToolTip('\n'.join([entry.get('problem') or entry['name'],
                *[s.get('url', s.get('name', '')) for s in entry.get('sources', [])]]))
            text.addWidget(title)
            row.addLayout(text, 1)
            if entry.get('state'):
                detail = QLabel()
                detail.setTextFormat(Qt.TextFormat.PlainText)
                detail.setProperty('role', 'hint')
                detail.setWordWrap(True)
                text.addWidget(detail)
                pill = QLabel()
                pill.setProperty('role', 'status')
                row.addWidget(pill, 0, Qt.AlignmentFlag.AlignVCenter)
                self.status_labels[entry['key']] = detail
                self.status_pills[entry['key']] = pill
                self.set_row_state(entry)
            vlist.addWidget(frame)
            self.video_checkboxes.append((cb, entry))
        self.video_list_container.setVisible(False)
        layout.addWidget(self.video_list_container)
        self._sync_group_state()
        self.refresh_theme()

    def set_row_state(self, row: dict):
        """Show a placement's entry id, source count and state pill."""
        detail, pill = self.status_labels.get(row['key']), self.status_pills.get(row['key'])
        if detail is None:
            return
        sources = len(row.get('sources', []))
        detail.setText(f"{row['entry_id']} · {sources} source location{'s' if sources != 1 else ''}")
        pill.setText(STATUS_NAMES.get(row['state'], row['state']))
        pill.setProperty('state', STATUS_TONES.get(row['state'], ''))
        pill.style().unpolish(pill)
        pill.style().polish(pill)

    def refresh_theme(self):
        colors = gui_styles.modern_colors()
        self.needs_review_badge.setStyleSheet(
            f"background: {colors['warning_bg']}; color: {colors['warning']}; border-radius: 8px; padding: 1px 8px;")
        self.destination_label.setStyleSheet(f"color: {colors['warning']};" if self._no_match else '')
        self.expand_btn.setStyleSheet(f"""
            QToolButton#group_expand_btn {{
                background: transparent; border: 1px solid transparent;
                border-radius: 6px; padding: 0;
                min-width: 30px; max-width: 30px;
                min-height: 30px; max-height: 30px;
            }}
            QToolButton#group_expand_btn:hover {{ background: {colors['soft']}; }}
            QToolButton#group_expand_btn:pressed {{ background: {colors['selected']}; }}
            QToolButton#group_expand_btn:focus {{ border-color: {colors['focus']}; }}
        """)
        # Bundle the themed arrow: installed apps cannot write to their assets.
        theme = 'light' if gui_styles.is_light() else 'dark'
        arrow = gui_styles.modern_asset(f'kaltura-chevron-down-{theme}.png')
        self.combo.setStyleSheet(f"""
            QComboBox, NoScrollComboBox {{ padding-right: 36px; }}
            QComboBox::drop-down, NoScrollComboBox::drop-down {{
                subcontrol-origin: padding; subcontrol-position: top right;
                width: 32px; border: none; background: transparent;
            }}
            QComboBox::down-arrow, NoScrollComboBox::down-arrow {{
                image: url("{arrow}"); width: 18px; height: 18px;
            }}
        """)
        self._refresh_disclosure_icon()

    def _refresh_disclosure_icon(self):
        expanded = self.expand_btn.isChecked()
        icon = 'chevron-down' if expanded else 'chevron-right'
        self.expand_btn.setIcon(QIcon(tool_pixmap(icon, 18, gui_styles.modern_colors()['muted'])))
        name = f"{'Hide' if expanded else 'Show'} videos in {self.section_name or 'unnamed section'}"
        self.expand_btn.setAccessibleName(name)
        self.expand_btn.setToolTip(name)
        self.expand_btn.setAccessibleDescription('Expanded' if expanded else 'Collapsed')

    # ── selection logic ────────────────────────────────────────────────────

    def _on_expand_toggled(self, expanded: bool):
        self._refresh_disclosure_icon()
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
        total = len(self.video_checkboxes)
        self.count_badge.setText(f"{checked} of {total} video{'s' if total != 1 else ''}")
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
        self._no_match = no_match
        self.needs_review_badge.setVisible(needs_review)
        if no_match:
            self.destination_label.setText("Choose a destination")
            self.destination_label.setToolTip('No matching unit found. Choose an existing unit or create a new one.')
            self.destination_label.setStyleSheet(f"color: {gui_styles.modern_colors()['warning']};")
        else:
            self.destination_label.setText("Destination")
            self.destination_label.setToolTip('')
            self.destination_label.setStyleSheet("")


class KalturaResultsWidget(QWidget):
    selected_count_changed = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._groups: list[VideoGroupWidget] = []
        self._combos: dict[str, NoScrollComboBox] = {}
        self._bs_modules: list[dict] = []
        self._match_scores: dict[str, float] = {}
        self._last_combo_index: dict[NoScrollComboBox, int] = {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

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
        outer.addWidget(self._results_scroll, 1)

    def set_message(self, text: str, visible: bool = True):
        self._placeholder.setText(text)
        self._placeholder.setVisible(visible)

    def refresh_theme(self):
        for group in self._groups:
            group.refresh_theme()

    def clear(self):
        for group in self._groups:
            self._results_layout.removeWidget(group)
            group.hide()
            group.deleteLater()
        self._groups.clear()
        self._combos.clear()
        self._match_scores.clear()
        self._last_combo_index.clear()
        self.selected_count_changed.emit(0)

    def set_entries(self, entries: list[dict]):
        self.clear()
        buckets: dict[str, list[dict]] = {}
        for entry in entries:
            buckets.setdefault(entry.get('section_key') or entry.get("section_name", ""), []).append(entry)
        insert_at = 0
        for section, vids in buckets.items():
            group = VideoGroupWidget(vids[0].get('section_name') or section, vids, self._emit_selected_count_changed)
            group.section_key = section or '(unnamed section)'
            group.combo.currentIndexChanged.connect(self._emit_selected_count_changed)
            self._results_layout.insertWidget(insert_at, group)
            insert_at += 1
            self._groups.append(group)
            # A section with no usable name still needs a working destination —
            # skipping it here left its combo unregistered, so section_map()
            # dropped it and the videos were silently never created.
            key = section or "(unnamed section)"
            self._combos[key] = group.combo
            self._last_combo_index[group.combo] = 0
            group.combo.currentIndexChanged.connect(
                lambda idx, s=key, c=group.combo: self._on_combo_changed(s, c, idx)
            )
        if self._bs_modules:
            self.set_modules(self._bs_modules)
        self._emit_selected_count_changed()

    def set_modules(self, modules: list[dict]):
        self._bs_modules = modules
        names = [m.get('path', m['title']) for m in modules]
        for section, combo in self._combos.items():
            while combo.count() > 1:
                combo.removeItem(1)
            for mod in modules:
                title = mod.get('path', mod['title'])
                if names.count(title) > 1:
                    title += f" · unit {mod['id']}"
                combo.addItem(title, str(mod['id']))
            combo.addItem(QIcon(tool_pixmap('plus', 16, gui_styles.modern_colors()['muted'])), NEW_MODULE_LABEL, NEW_MODULE_SENTINEL)
        self._emit_selected_count_changed()

    def _on_combo_changed(self, section: str, combo: NoScrollComboBox, index: int):
        """Turn the '➕ Create new module…' row into a named pending module.

        The sentinel row is only ever a trigger — it is swapped for a real row
        carrying ("__NEW__", name) so section_map can tell pending modules from
        existing ids. Cancelling restores the prior selection so the user is not
        left sitting on the trigger row.
        """
        if combo.itemData(index) != NEW_MODULE_SENTINEL:
            self._last_combo_index[combo] = index
            return

        previous = self._last_combo_index.get(combo, 0)
        section_name = next((g.section_name for g in self._groups if g.combo is combo), section)
        default = section_name if section_name and not section_name.startswith("(unnamed section") else "Kaltura Videos"
        name, ok = QInputDialog.getText(
            self, "New Brightspace unit",
            "Name for the new unit:",
            QLineEdit.EchoMode.Normal, default,
        )
        name = (name or "").strip()
        if not ok or not name:
            combo.setCurrentIndex(previous)
            return

        # Reuse an existing module outright if the typed name already matches one —
        # creating a second module with the same title gives two identical choices.
        for i in range(combo.count()):
            data = combo.itemData(i)
            if isinstance(data, str) and combo.itemText(i).strip().lower() == name.lower():
                combo.setCurrentIndex(i)
                self._last_combo_index[combo] = i
                return

        pending_label = f"New: {name}"
        existing = combo.findText(pending_label)
        if existing >= 0:
            combo.setCurrentIndex(existing)
            self._last_combo_index[combo] = existing
            return

        insert_at = combo.count() - 1  # keep the trigger row last
        combo.insertItem(insert_at, pending_label, (NEW_MODULE_SENTINEL, name))
        combo.setCurrentIndex(insert_at)
        self._last_combo_index[combo] = insert_at

    def autosuggest(self):
        if not self._combos or not self._bs_modules:
            return
        from content_matcher import match_sections
        for group in self._groups:
            name = group.section_key
            module, score = match_sections([group.section_name], self._bs_modules)[group.section_name]
            self._match_scores[name] = score if module is not None else 0
            combo = self._combos.get(name)
            if combo is None or self._is_real_destination(combo.currentData()):
                continue
            if module is None or score < 75:
                continue
            idx = combo.findData(module["id"])
            if idx < 0:
                continue
            combo.setCurrentIndex(idx)
        self._emit_selected_count_changed()

    @staticmethod
    def _is_real_destination(data) -> bool:
        """True for an existing module id or a named pending module.

        The bare sentinel is the unclicked trigger row, not a destination.
        """
        return data is not None and data != NEW_MODULE_SENTINEL

    def apply_default_selection(self):
        for group in self._groups:
            score = self._match_scores.get(group.section_key, 0)
            has_dest = self._is_real_destination(group.combo.currentData())
            if has_dest and score >= 90:
                group.set_needs_review(False)
                group.set_all_checked(True)
            else:
                group.set_needs_review(True, no_match=not has_dest)
                group.set_all_checked(False)
        self._emit_selected_count_changed()

    def selected_entries(self) -> list[dict]:
        return [
            entry
            for group in self._groups
            for cb, entry in group.video_checkboxes
            if cb.isChecked()
        ]

    def section_map(self) -> dict:
        """{section: module_id} — pending modules appear as (NEW_MODULE_SENTINEL, name).

        The bare sentinel (the unclicked trigger row) is not a destination and is
        excluded, so a user who opens the dialog and cancels does not end up with
        a section that looks mapped.
        """
        result = {}
        for name, combo in self._combos.items():
            data = combo.currentData()
            if self._is_real_destination(data):
                result[name] = data
        return result

    def pending_module_names(self) -> list:
        """Names of modules the user asked to create, in group order."""
        names = []
        for value in self.section_map().values():
            if isinstance(value, tuple) and value[0] == NEW_MODULE_SENTINEL:
                if value[1] not in names:
                    names.append(value[1])
        return names

    def selected_count(self) -> int:
        return len(self.selected_entries())

    def has_groups(self) -> bool:
        return bool(self._groups)

    def has_modules(self) -> bool:
        return bool(self._bs_modules)

    def set_scroll_height(self, minimum: int, maximum: int = 0):
        """Bound the results list. `maximum` of 0 means uncapped.

        An uncapped list lets the dialog give the results area whatever height
        is going, which is what makes the window resizable down to laptop
        heights instead of being pinned open by a tall minimum.
        """
        self._results_scroll.setMinimumHeight(minimum)
        self._results_scroll.setMaximumHeight(maximum if maximum else _QWIDGETSIZE_MAX)

    def embed_inline(self):
        """Drop the inner scroll box so the list grows inside a page that already scrolls."""
        self._results_scroll.takeWidget()
        self._results_scroll.hide()
        self.layout().addWidget(self._results_container)
        self._results_layout.setSpacing(8)

    def set_all_expanded(self, expanded: bool):
        for group in self._groups:
            group.expand_btn.setChecked(expanded)

    def _emit_selected_count_changed(self, *_args):
        self.selected_count_changed.emit(self.selected_count())


class KalturaResultsDialog(QDialog):
    create_pages_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Kaltura Results")
        # Fit the screen actually in use. A hardcoded 760x620 overflowed laptop
        # displays, and because the window opened taller than the desktop the
        # Close/Create buttons at the bottom could sit off-screen entirely.
        available = None
        screen = self.screen() or QGuiApplication.primaryScreen()
        if screen is not None:
            available = screen.availableGeometry()
        if available is not None:
            self.resize(
                min(760, int(available.width() * 0.9)),
                min(620, int(available.height() * 0.85)),
            )
        else:
            self.resize(760, 620)
        # Floor low enough to stay usable on short screens; the results list and
        # log both scroll, so nothing is lost by shrinking.
        self.setMinimumSize(460, 320)
        self.setSizeGripEnabled(True)
        self._running = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        title = QLabel("Kaltura Videos")
        title.setObjectName("kaltura_results_title")
        title.setStyleSheet("font-size: 16px; font-weight: 700;")
        layout.addWidget(title)

        self._summary = QLabel("No videos found yet.")
        self._summary.setProperty("role", "dim")
        self._summary.setWordWrap(True)
        layout.addWidget(self._summary)

        self.results = KalturaResultsWidget()
        self.results.set_scroll_height(180)
        self.results.selected_count_changed.connect(self._update_create_button)
        layout.addWidget(self.results, 1)

        actions = QHBoxLayout()
        actions.addStretch()
        self._close_btn = QPushButton("Close")
        self._close_btn.setProperty("variant", "secondary")
        self._close_btn.clicked.connect(self.hide)
        actions.addWidget(self._close_btn)

        self._create_btn = QPushButton("Create Pages for 0 Selected Videos")
        self._create_btn.setObjectName("dialog_create_pages_btn")
        self._create_btn.clicked.connect(self.create_pages_requested.emit)
        actions.addWidget(self._create_btn)
        layout.addLayout(actions)

        self._update_create_button(0)

    def set_summary(self, text: str):
        self._summary.setText(text)

    def set_create_running(self, running: bool):
        self._running = running
        self._update_create_button(self.results.selected_count())

    def _update_create_button(self, count: int):
        if self._running:
            self._create_btn.setText("Running…")
            self._create_btn.setEnabled(False)
            return
        self._create_btn.setText(f"Create Pages for {count} Selected Videos")
        self._create_btn.setEnabled(count > 0)


# Public panel import remains stable for the main window and extensions.
from panels.kaltura_workflow import KalturaPanel
