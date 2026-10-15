"""Native views for the Check & fix overview, focused review and confirmation."""
import re
from urllib.parse import parse_qs, urlparse

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QToolButton,
    QLineEdit, QCheckBox, QFrame, QScrollArea, QGridLayout, QSizePolicy,
)

from gui_log import LogWidget
from course_context import brightspace_course_key


GROUPS = {
    "links": ("Broken file links", "Links in existing Brightspace pages", ("repair_files",)),
    "content": ("Missing course content", "Items found in Moodle but missing from Brightspace", ("content", "files", "activities")),
    "books": ("Moodle Books", "Review whether to create a page for each chapter", ("books",)),
    "other": ("Other options", "Review page order or open a dedicated media tool.", ("order",)),
}


class WorkflowScrollArea(QScrollArea):
    """Use only the height the current view needs; scroll when space is short."""
    def sizeHint(self):
        hint = super().sizeHint()
        body = self.widget()
        if body is not None:
            layout = body.layout()
            height = layout.totalHeightForWidth(max(1, self.viewport().width()))
            hint.setHeight((height if height >= 0 else layout.sizeHint().height()) + 6)
        return hint


def course_context(value, platform):
    """Return an ID/scope label, never a guessed or verified course name."""
    try:
        parsed = urlparse(value.strip())
        if (parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password
                or any(char.isspace() for char in value.strip())):
            return None
        parsed.port  # Reject malformed port values before browser navigation.
        if platform == "Brightspace":
            key = brightspace_course_key(value)
            legacy = re.search(r"/le/(\d+)/", parsed.path)
            identifier = key[1] if key else legacy.group(1) if legacy else None
            return f"Course {identifier}" if identifier else None
        identifier = parse_qs(parsed.query).get("id", [""])[0]
        if not identifier.isdigit():
            return None
        if parsed.path.endswith("/course/section.php"):
            return f"Section {identifier}"
        if not parsed.path.endswith(("/course/view.php", "/enrol/index.php")):
            return None
        section = parse_qs(parsed.query).get("section", [""])[0]
        return f"Course {identifier}" + (f" · section {section}" if section else "")
    except ValueError:
        return None


def label(text="", role=None):
    widget = QLabel(text)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    widget.setWordWrap(True)
    widget.setMinimumWidth(0)
    if role:
        widget.setProperty("role", role)
    return widget


def button(text, callback, variant=None):
    widget = QPushButton(text)
    widget.setMinimumWidth(0)
    if variant:
        widget.setProperty("variant", variant)
    widget.clicked.connect(callback)
    return widget


def column(widget=None, spacing=12):
    widget = widget or QWidget()
    layout = QVBoxLayout(widget)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(spacing)
    return widget, layout


def build(panel):
    panel.setObjectName("checker_panel")
    panel.setProperty("design", "modern")
    panel.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    outer = QVBoxLayout(panel)
    outer.setContentsMargins(0, 0, 0, 0)
    outer.setSpacing(0)
    panel._scroll = WorkflowScrollArea()
    panel._scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
    panel._scroll.setWidgetResizable(True)
    panel._scroll.setFrameShape(QFrame.Shape.NoFrame)
    panel._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    outer.addWidget(panel._scroll)
    body, layout = column(spacing=0)
    body.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
    layout.setContentsMargins(44, 36, 44, 24)
    panel._scroll.setWidget(body)
    layout.addWidget(label("Check & fix", "page-heading"))
    layout.addSpacing(6)
    panel._subtitle = label("Compare Moodle with Brightspace, then choose what to fix.", "dim")
    layout.addWidget(panel._subtitle)
    layout.addSpacing(30)

    panel._course_summary, summary_layout = column(spacing=8)
    context_row = QHBoxLayout()
    panel._course_context_label = label()
    panel._course_context_label.setProperty("role", "course-context")
    context_row.addWidget(panel._course_context_label, 1)
    panel._change_btn = button("Change", panel._edit_courses, "link")
    context_row.addWidget(panel._change_btn)
    summary_layout.addLayout(context_row)
    layout.addWidget(panel._course_summary)
    panel._course_summary.hide()

    panel._course_form, form_layout = column(spacing=12)
    form_layout.addWidget(label("Your courses", "section-heading"))
    panel._course_grid = QGridLayout()
    panel._course_grid.setSpacing(16)
    panel._course_fields = []
    panel._course_errors = {}
    panel._moodle_entry = QLineEdit()
    panel._bs_entry = QLineEdit()
    for name, title, entry, example in (
        ("moodle", "Moodle source — course or section URL", panel._moodle_entry, "https://moodle.example/course/view.php?id=456"),
        ("bs", "Brightspace destination — course URL", panel._bs_entry, "https://learn.example/d2l/le/lessons/123"),
    ):
        field, field_layout = column(spacing=5)
        entry.setMinimumWidth(0)
        entry.setPlaceholderText(example)
        entry.setAccessibleName(title)
        entry.setAccessibleDescription("Required. Paste the full course URL." if name == "bs" else "Required. A Moodle section URL checks only that section.")
        title_label = label(title, "field-label")
        title_label.setBuddy(entry)
        field_layout.addWidget(title_label)
        field_layout.addWidget(entry)
        error = label("", "error")
        error.hide()
        field_layout.addWidget(error)
        panel._course_errors[name] = error
        panel._course_fields.append(field)
    panel._compact_courses = None
    form_layout.addLayout(panel._course_grid)
    form_layout.addWidget(label("A Moodle section URL checks only that section. Scanning leaves Brightspace unchanged.", "dim"))
    panel._cancel_edit_btn = button("Keep current scan", panel._cancel_course_edit, "link")
    form_layout.addWidget(panel._cancel_edit_btn, 0, Qt.AlignmentFlag.AlignLeft)
    panel._cancel_edit_btn.hide()
    layout.addWidget(panel._course_form)
    layout.addSpacing(38)

    panel._workspace, workspace_layout = column(spacing=0)
    layout.addWidget(panel._workspace)
    panel._overview, overview_layout = column(spacing=0)
    workspace_layout.addWidget(panel._overview)
    findings_header = QHBoxLayout()
    findings_header.addWidget(label("Findings", "section-heading"), 1)
    panel._rescan_btn = button("Scan again", panel._start_run, "link")
    findings_header.addWidget(panel._rescan_btn)
    overview_layout.addLayout(findings_header)
    overview_layout.addSpacing(12)
    panel._summary = label("No scan yet. Enter both course URLs to begin.", "dim")
    overview_layout.addWidget(panel._summary)
    panel._empty_state, empty_layout = column(spacing=5)
    panel._empty_title = label("Ready to compare", "section-heading")
    panel._empty_hint = label("Your findings will appear here after a scan.", "dim")
    empty_layout.addWidget(panel._empty_title)
    empty_layout.addWidget(panel._empty_hint)
    empty_layout.setContentsMargins(0, 24, 0, 24)
    overview_layout.addWidget(panel._empty_state)
    panel._group_rows = {}
    panel._group_counts = {}
    panel._group_icons = {}
    for key in ("links", "content", "books"):
        title, description, _ = GROUPS[key]
        row = button("", lambda _checked=False, group=key: panel._open_group(group))
        row.setProperty("role", "finding-row")
        row.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        row.setAccessibleName(f"Review {title.lower()}")
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(12, 18, 4, 18)
        row_layout.setSpacing(18)
        icon = QLabel()
        icon.setFixedSize(28, 28)
        icon.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        panel._group_icons[key] = icon
        row_layout.addWidget(icon)
        text, text_layout = column(spacing=5)
        text.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        title_label = label(title, "finding-title")
        text_layout.addWidget(title_label)
        text_layout.addWidget(label(description, "dim"))
        row_layout.addWidget(text, 1)
        count = label("", "dim")
        count.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        row_layout.addWidget(count)
        chevron = QLabel()
        chevron.setObjectName(f"finding_arrow_{key}")
        chevron.setFixedSize(16, 16)
        chevron.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        row_layout.addWidget(chevron)
        panel._group_counts[key] = count
        panel._group_rows[key] = row
        overview_layout.addWidget(row)
        row.hide()
    overview_layout.addSpacing(20)
    panel._other_btn = QToolButton()
    panel._other_btn.setText("Other options")
    panel._other_btn.setProperty("variant", "ghost")
    panel._other_btn.setArrowType(Qt.ArrowType.RightArrow)
    panel._other_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
    panel._other_btn.clicked.connect(lambda: panel._open_group("other"))
    overview_layout.addWidget(panel._other_btn, 0, Qt.AlignmentFlag.AlignLeft)

    panel._group_page, group_layout = column(spacing=16)
    workspace_layout.addWidget(panel._group_page)
    panel._group_title = label("", "section-heading")
    panel._group_intro = label("", "dim")
    group_layout.addWidget(panel._group_title)
    group_layout.addWidget(panel._group_intro)
    panel._findings, panel._evidence_layout = column(spacing=10)
    group_layout.addWidget(panel._findings)
    panel._fixes_widget, fixes_layout = column(spacing=0)
    group_layout.addWidget(panel._fixes_widget)
    panel._fix_boxes = {}
    panel._fix_labels = {}
    panel._fix_rows = {}
    for key, title, description in panel.FIXES:
        row = QFrame()
        row.setProperty("role", "checker-fix-row")
        row_layout = QVBoxLayout(row)
        row_layout.setContentsMargins(0, 14, 0, 14)
        row_layout.setSpacing(6)
        box = QCheckBox(title)
        box.setObjectName(f"fix_{key}")
        box.setEnabled(False)
        box.setAccessibleDescription(description)
        box.toggled.connect(panel._update_apply_enabled)
        row_layout.addWidget(box)
        detail = label(description, "dim")
        detail.setContentsMargins(28, 0, 0, 0)
        row_layout.addWidget(detail)
        fixes_layout.addWidget(row)
        panel._fix_boxes[key], panel._fix_labels[key], panel._fix_rows[key] = box, detail, row
    panel._media_tools, media_layout = column(spacing=10)
    media_layout.addWidget(label("H5P and Kaltura are handled in Media.", "dim"))
    for title, tool in (("Open H5P tool", "h5p"), ("Open Kaltura tool", "kaltura"), ("Try sample link review", "link_review_preview")):
        tool_button = button(title, lambda _checked=False, name=tool: panel.open_tool.emit(name), "link")
        media_layout.addWidget(tool_button, 0, Qt.AlignmentFlag.AlignLeft)
    group_layout.addWidget(panel._media_tools)
    panel._group_page.hide()

    panel._confirmation, confirm_layout = column(spacing=16)
    workspace_layout.addWidget(panel._confirmation)
    confirm_layout.addWidget(label("Confirm selected fixes", "section-heading"))
    panel._confirm_courses = label()
    panel._confirm_courses.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    confirm_layout.addWidget(panel._confirm_courses)
    panel._confirm_choices = label()
    confirm_layout.addWidget(panel._confirm_choices)
    confirm_layout.addWidget(label("The app checks the courses again, then attempts these fix types. Individual files and uncertain matches may need your review during the run.", "dim"))
    confirm_layout.addWidget(label("Applying can upload files, create content or update existing pages. Stopping a run does not undo changes already made.", "dim"))
    panel._confirmation.hide()

    panel._result, result_layout = column(spacing=12)
    workspace_layout.addWidget(panel._result)
    panel._result_title = label("", "section-heading")
    panel._result_body = label()
    result_layout.addWidget(panel._result_title)
    result_layout.addWidget(panel._result_body)
    panel._result.hide()

    panel._progress, progress_layout = column(spacing=12)
    layout.addWidget(panel._progress)
    panel._status_line = label("", "section-heading")
    progress_layout.addWidget(panel._status_line)
    panel._moodle_hint = label("A browser has opened Moodle. Sign in and check that the correct course or section is shown, then continue.", "dim")
    progress_layout.addWidget(panel._moodle_hint)
    panel._ready_btn = button("I'm signed in — continue", panel._moodle_ready)
    progress_layout.addWidget(panel._ready_btn, 0, Qt.AlignmentFlag.AlignLeft)
    panel._h5p_hint = label("Switch your Moodle role to Teacher to download H5P, or skip it for this run.", "dim")
    progress_layout.addWidget(panel._h5p_hint)
    h5p_row = QHBoxLayout()
    panel._h5p_ready_btn = button("Download H5P", panel._h5p_ready)
    panel._h5p_skip_btn = button("Skip H5P", panel._h5p_skip, "secondary")
    h5p_row.addWidget(panel._h5p_ready_btn)
    h5p_row.addWidget(panel._h5p_skip_btn)
    h5p_row.addStretch()
    progress_layout.addLayout(h5p_row)
    for widget in (panel._progress, panel._moodle_hint, panel._ready_btn, panel._h5p_hint, panel._h5p_ready_btn, panel._h5p_skip_btn):
        widget.hide()

    layout.addSpacing(22)
    panel._details_btn = QToolButton()
    panel._details_btn.setObjectName("checker_details_toggle")
    panel._details_btn.setProperty("variant", "ghost")
    panel._details_btn.setText("Activity details")
    panel._details_btn.setCheckable(True)
    panel._details_btn.toggled.connect(panel._toggle_details)
    layout.addWidget(panel._details_btn, 0, Qt.AlignmentFlag.AlignLeft)
    panel._log = LogWidget()
    panel._log.setFixedHeight(210)
    layout.addWidget(panel._log)
    panel._log.hide()
    panel._dl_label = label("", "dim")
    layout.addWidget(panel._dl_label)
    panel._dl_label.hide()
    panel._continue_btn = button("Continue to Unit Collector", lambda: panel.continue_next.emit(), "secondary")
    layout.addWidget(panel._continue_btn, 0, Qt.AlignmentFlag.AlignLeft)
    panel._continue_btn.hide()
    layout.addStretch(1)

    # Outside the content scroll: the current task's action stays reachable.
    panel._footer = QWidget()
    panel._footer_layout = QGridLayout(panel._footer)
    panel._footer_layout.setContentsMargins(44, 12, 44, 18)
    panel._footer_layout.setHorizontalSpacing(12)
    panel._selection_summary = label("Scanning leaves Brightspace unchanged.", "dim")
    panel._footer_layout.addWidget(panel._selection_summary, 0, 0)
    actions = QWidget()
    panel._action_layout = QHBoxLayout(actions)
    panel._action_layout.setContentsMargins(0, 0, 0, 0)
    panel._back_btn = button("Back", panel._go_back, "secondary")
    panel._run_btn = button("Scan courses", panel._start_run)
    panel._review_btn = button("Choose fixes", panel.show_transfer)
    panel._apply_btn = button("Review selected fixes", panel._preview_selected)
    panel._confirm_btn = button("Apply selected fixes", panel._apply_selected)
    panel._stop_btn = button("Stop current task", panel._stop_run, "secondary")
    for widget in (panel._back_btn, panel._run_btn, panel._review_btn, panel._apply_btn, panel._confirm_btn, panel._stop_btn):
        widget.setAutoDefault(False)
        panel._action_layout.addWidget(widget)
        widget.hide()
    panel._footer_actions = actions
    panel._footer_layout.addWidget(actions, 0, 1)
    panel._footer_layout.setColumnStretch(0, 1)
    outer.addWidget(panel._footer)
    outer.addStretch(1)

    cfg = panel._mw.load_config() if hasattr(panel._mw, "load_config") else {}
    panel._bs_entry.setText(cfg.get("chk_bs_url", ""))
    panel._moodle_entry.setText(cfg.get("chk_moodle_url", ""))
    panel._bs_entry.textChanged.connect(panel._invalidate_scan)
    panel._moodle_entry.textChanged.connect(panel._invalidate_scan)
    panel._view = "overview"
    panel._active_group = None
    panel._reflow_courses()
    panel._set_view("overview")
