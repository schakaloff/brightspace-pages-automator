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


def surface(spacing=12):
    frame = QFrame()
    frame.setProperty("role", "checker-surface")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(20, 18, 20, 18)
    layout.setSpacing(spacing)
    return frame, layout


def build(panel):
    panel.setObjectName("checker_panel")
    panel.setProperty("design", "modern")
    panel.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    outer = QVBoxLayout(panel)
    outer.setContentsMargins(24, 22, 24, 18)
    outer.setSpacing(10)
    outer.addWidget(label("Check & fix", "page-heading"))
    panel._subtitle = label("Compare your courses, review the fixes, then apply your choices.", "dim")
    outer.addWidget(panel._subtitle)
    steps = QHBoxLayout()
    steps.setSpacing(8)
    panel._steps, panel._step_icons = [], []
    for title, icon_name in (("Connect", "link"), ("Review", "clipboard-list"),
                             ("Apply", "arrows-exchange"), ("Results", "circle-check")):
        step, item = column(QFrame(), 6)
        step.setProperty("role", "checker-step")
        step.setAccessibleName(title)
        step.setMinimumWidth(0)
        step.setFixedHeight(64)
        item.setContentsMargins(6, 8, 6, 8)
        icon = QLabel()
        icon.setFixedSize(24, 24)
        item.addWidget(icon, 0, Qt.AlignmentFlag.AlignHCenter)
        text = label(title, "step-title")
        text.setWordWrap(False)
        text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        item.addWidget(text)
        panel._steps.append(step)
        panel._step_icons.append((icon, icon_name))
        steps.addWidget(step, 1)
    outer.addLayout(steps)
    panel._stage = 0

    panel._scroll = QScrollArea()
    panel._scroll.setWidgetResizable(True)
    panel._scroll.setFrameShape(QFrame.Shape.NoFrame)
    panel._scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    panel._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    outer.addWidget(panel._scroll, 1)
    body, layout = column(spacing=14)
    layout.setContentsMargins(0, 0, 8, 0)
    panel._scroll.setWidget(body)

    panel._course_summary, summary_layout = surface()
    summary_layout.setContentsMargins(20, 8, 20, 8)
    context_row = QHBoxLayout()
    panel._course_context_label = label("", "dim")
    context_row.addWidget(panel._course_context_label, 1)
    panel._change_btn = button("Change courses", panel._edit_courses, "link")
    context_row.addWidget(panel._change_btn)
    summary_layout.addLayout(context_row)
    layout.addWidget(panel._course_summary)
    panel._course_summary.hide()

    panel._course_form, form_layout = surface()
    form_layout.addWidget(label("Your courses", "section-heading"))
    panel._course_grid = QGridLayout()
    panel._course_grid.setContentsMargins(0, 0, 0, 0)
    panel._course_grid.setSpacing(8)
    panel._course_fields, panel._course_errors = [], {}
    panel._moodle_entry, panel._bs_entry = QLineEdit(), QLineEdit()
    for name, title, entry, example in (
        ("moodle", "From Moodle — course or section URL", panel._moodle_entry, "https://mymoodle.okanagan.bc.ca/course/view.php?id=…"),
        ("bs", "To Brightspace — course URL", panel._bs_entry, "https://learn.okanagancollege.ca/d2l/le/lessons/…"),
    ):
        field, field_layout = column(spacing=6)
        entry.setMinimumWidth(0)
        entry.setMinimumHeight(42)
        entry.setPlaceholderText(example)
        entry.setAccessibleName(title)
        entry.setAccessibleDescription("Required. Paste the full course URL.")
        title_label = label(title, "field-label")
        title_label.setBuddy(entry)
        field_layout.addWidget(title_label)
        field_layout.addWidget(entry)
        error = label("", "error")
        error.hide()
        field_layout.addWidget(error)
        if name == "moodle":
            field_layout.addWidget(label("A section URL checks only that section.", "hint"))
        panel._course_errors[name] = error
        panel._course_fields.append(field)
        panel._course_grid.addWidget(field, len(panel._course_fields) - 1, 0)
    panel._compact_courses = None
    form_layout.addLayout(panel._course_grid)
    form_layout.addWidget(label("Scanning checks for missing content and broken file links. It makes no course changes.", "dim"))
    panel._cancel_edit_btn = button("Keep current scan", panel._cancel_course_edit, "link")
    form_layout.addWidget(panel._cancel_edit_btn, 0, Qt.AlignmentFlag.AlignLeft)
    panel._cancel_edit_btn.hide()
    layout.addWidget(panel._course_form)

    panel._workspace, workspace_layout = column(spacing=14)
    layout.addWidget(panel._workspace)
    panel._overview, overview_layout = column(spacing=14)
    workspace_layout.addWidget(panel._overview)
    panel._empty_state, empty_layout = surface(6)
    panel._empty_title = label("Ready to compare", "section-heading")
    panel._empty_hint = label("The browser may ask you to sign in. You'll review any fixes before applying them.", "dim")
    empty_layout.addWidget(panel._empty_title)
    empty_layout.addWidget(panel._empty_hint)
    overview_layout.addWidget(panel._empty_state)

    panel._review_surface, review_layout = surface(6)
    review_layout.setContentsMargins(20, 12, 20, 12)
    overview_layout.addWidget(panel._review_surface)
    header = QHBoxLayout()
    header.addWidget(label("Review your findings", "section-heading"), 1)
    panel._rescan_btn = button("Scan again", panel._start_run, "link")
    header.addWidget(panel._rescan_btn)
    review_layout.addLayout(header)
    review_layout.addWidget(label("Choose the fix types to apply. Open findings to see the affected content.", "dim"))
    panel._summary = label("", "dim")
    review_layout.addWidget(panel._summary)
    panel._fixes_widget, panel._fixes_layout = column(spacing=0)
    review_layout.addWidget(panel._fixes_widget)
    panel._fix_boxes, panel._fix_labels, panel._fix_rows = {}, {}, {}
    panel._fix_counts, panel._finding_buttons, panel._fix_icons = {}, {}, []
    panel._group_rows, panel._group_counts = {}, {}
    icon_names = dict(repair_files="link", content="file-plus", files="file", activities="clipboard-list", books="book", order="arrows-exchange")
    groups = dict(repair_files="links", content="content", files="content", activities="content", books="books", order="other")
    panel._optional_heading = label("Optional improvements", "dim")
    for key, title, description in panel.FIXES:
        if key == "books":
            panel._fixes_layout.addWidget(panel._optional_heading)
        row = QFrame()
        row.setProperty("role", "checker-fix-row")
        row_layout = QGridLayout(row)
        row_layout.setContentsMargins(0, 5, 0, 5)
        row_layout.setVerticalSpacing(2)
        row_layout.setHorizontalSpacing(12)
        box = QCheckBox()
        box.setObjectName(f"fix_{key}")
        box.setAccessibleName(title)
        box.setAccessibleDescription(description)
        box.setEnabled(False)
        box.toggled.connect(panel._update_apply_enabled)
        row_layout.addWidget(box, 0, 0, 2, 1, Qt.AlignmentFlag.AlignTop)
        tile = QFrame()
        tile.setProperty("role", "checker-icon-tile")
        tile.setFixedSize(36, 36)
        tile_layout = QVBoxLayout(tile)
        tile_layout.setContentsMargins(6, 6, 6, 6)
        icon = QLabel()
        icon.setFixedSize(24, 24)
        tile_layout.addWidget(icon)
        panel._fix_icons.append((icon, icon_names[key]))
        row_layout.addWidget(tile, 0, 1, 2, 1, Qt.AlignmentFlag.AlignTop)
        name = label(title, "finding-title")
        name.setBuddy(box)
        detail = label(description, "hint")
        row_layout.addWidget(name, 0, 2)
        row_layout.addWidget(detail, 1, 2)
        count = label("", "dim")
        row_layout.addWidget(count, 0, 3)
        show = button("View details" if key == "order" else "View findings", lambda _checked=False, group=groups[key]: panel._open_group(group), "link")
        show.setAccessibleName(f"View findings: {title}")
        row_layout.addWidget(show, 1, 3)
        row_layout.setColumnStretch(2, 1)
        panel._fixes_layout.addWidget(row)
        panel._fix_boxes[key], panel._fix_labels[key], panel._fix_rows[key] = box, detail, row
        panel._fix_counts[key], panel._finding_buttons[key] = count, show
        if groups[key] not in panel._group_rows:
            panel._group_rows[groups[key]] = show
            panel._group_counts[groups[key]] = count
    review_layout.addWidget(label("Counts may overlap between fix types.", "hint"))

    panel._group_page, group_layout = surface(10)
    workspace_layout.addWidget(panel._group_page)
    panel._group_title = label("", "section-heading")
    panel._group_intro = label("", "dim")
    group_layout.addWidget(panel._group_title)
    group_layout.addWidget(panel._group_intro)
    panel._findings, panel._evidence_layout = column(spacing=10)
    group_layout.addWidget(panel._findings)
    # Fix choices move here while inspecting a finding; the same checkboxes
    # return to the overview, preserving choices and keyboard state.
    panel._group_layout = group_layout
    panel._media_tools, media_layout = column(spacing=8)
    media_layout.addWidget(label("H5P and videos are handled in Media.", "dim"))
    for title, tool in (("Open H5P tool", "h5p"), ("Open Kaltura tool", "kaltura"), ("Try sample link review", "link_review_preview")):
        media_layout.addWidget(button(title, lambda _checked=False, name=tool: panel.open_tool.emit(name), "link"), 0, Qt.AlignmentFlag.AlignLeft)
    group_layout.addWidget(panel._media_tools)
    panel._group_page.hide()
    panel._review_layout = review_layout
    panel._other_btn = panel._group_rows["other"]

    panel._confirmation, confirm_layout = column(spacing=14)
    workspace_layout.addWidget(panel._confirmation)
    confirm_layout.addWidget(label("Confirm your changes", "section-heading"))
    confirm_layout.addWidget(label("Check the destination and selected fix types before starting.", "dim"))
    courses, course_layout = surface(8)
    course_layout.addWidget(label("Your courses", "section-heading"))
    panel._confirm_courses = label("", "dim")
    panel._confirm_courses.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    course_layout.addWidget(panel._confirm_courses)
    confirm_layout.addWidget(courses)
    choices, choice_layout = surface(8)
    choice_layout.addWidget(label("Selected fix types", "section-heading"))
    panel._confirm_choices, panel._confirm_choices_layout = column(spacing=0)
    choice_layout.addWidget(panel._confirm_choices)
    choice_layout.addWidget(label("Affected content is checked again while these fixes run. Individual files and uncertain matches may need your review.", "dim"))
    choice_layout.addWidget(label("Stopping keeps changes that have already completed.", "dim"))
    confirm_layout.addWidget(choices)
    panel._confirmation.hide()

    panel._result, result_layout = surface()
    workspace_layout.addWidget(panel._result)
    panel._result_title = label("", "section-heading")
    panel._result_body = label("", "dim")
    result_layout.addWidget(panel._result_title)
    panel._outcome_list, panel._outcome_layout = column(spacing=10)
    result_layout.addWidget(panel._outcome_list)
    result_layout.addWidget(panel._result_body)
    panel._result.hide()

    panel._progress, progress_layout = surface()
    layout.addWidget(panel._progress)
    panel._status_line = label("", "section-heading")
    progress_layout.addWidget(panel._status_line)
    panel._progress_hint = label("", "dim")
    progress_layout.addWidget(panel._progress_hint)
    from PySide6.QtWidgets import QProgressBar
    panel._progress_bar = QProgressBar()
    panel._progress_bar.setRange(0, 0)
    panel._progress_bar.setTextVisible(False)
    panel._progress_bar.setFixedHeight(5)
    panel._progress_bar.setAccessibleName("Current operation is running")
    progress_layout.addWidget(panel._progress_bar)
    panel._connection_steps, connection_layout = column(spacing=12)
    panel._status_icons = []
    for title, icon_name, semantic in (("Browser opened", "circle-check", "success"),
                                       ("Waiting for Moodle confirmation", "link", "warning"),
                                       ("Compare course content", "clipboard-list", "muted")):
        row = QHBoxLayout()
        icon = QLabel()
        icon.setFixedSize(22, 22)
        row.addWidget(icon)
        row.addWidget(label(title, "dim"), 1)
        connection_layout.addLayout(row)
        panel._status_icons.append((icon, icon_name, semantic))
    progress_layout.addWidget(panel._connection_steps)
    panel._attention_title = label("Check the Moodle course in the browser", "finding-title")
    progress_layout.addWidget(panel._attention_title)
    panel._moodle_hint = label("Sign in if needed, then open the correct Moodle course or section. Return here when you're ready to continue.", "dim")
    progress_layout.addWidget(panel._moodle_hint)
    panel._ready_btn = button("I'm on the correct course — continue", panel._moodle_ready)
    progress_layout.addWidget(panel._ready_btn)
    panel._h5p_hint = label("Switch your Moodle role to Teacher to download H5P, or skip it for this run.", "dim")
    progress_layout.addWidget(panel._h5p_hint)
    panel._h5p_ready_btn = button("Download H5P", panel._h5p_ready)
    panel._h5p_skip_btn = button("Skip H5P", panel._h5p_skip, "secondary")
    progress_layout.addWidget(panel._h5p_ready_btn)
    progress_layout.addWidget(panel._h5p_skip_btn)
    for widget in (panel._progress, panel._connection_steps, panel._attention_title, panel._moodle_hint, panel._ready_btn, panel._h5p_hint, panel._h5p_ready_btn, panel._h5p_skip_btn):
        widget.hide()

    panel._apply_choices, panel._apply_choices_layout = surface(10)
    panel._apply_choices.hide()
    layout.addWidget(panel._apply_choices)

    panel._details_btn = QToolButton()
    panel._details_btn.setObjectName("checker_details_toggle")
    panel._details_btn.setProperty("variant", "ghost")
    panel._details_btn.setText("Show activity details")
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

    panel._footer = QWidget()
    panel._footer_layout = QGridLayout(panel._footer)
    panel._footer_layout.setContentsMargins(0, 0, 0, 0)
    panel._footer_layout.setHorizontalSpacing(12)
    panel._selection_summary = label("No changes are made during scanning.", "hint")
    panel._footer_layout.addWidget(panel._selection_summary, 0, 0, 1, 2)
    panel._back_btn = button("Back", panel._go_back, "secondary")
    panel._footer_layout.addWidget(panel._back_btn, 1, 0, Qt.AlignmentFlag.AlignLeft)
    actions = QWidget()
    panel._action_layout = QHBoxLayout(actions)
    panel._action_layout.setContentsMargins(0, 0, 0, 0)
    panel._run_btn = button("Scan courses", panel._start_run)
    panel._apply_btn = button("Review selected fixes", panel._preview_selected)
    panel._confirm_btn = button("Apply selected fixes", panel._apply_selected)
    panel._stop_btn = button("Stop", panel._stop_run, "secondary")
    for widget in (panel._back_btn, panel._stop_btn, panel._run_btn, panel._apply_btn, panel._confirm_btn):
        widget.setAutoDefault(False)
        widget.setMinimumHeight(38)
        if widget is not panel._back_btn:
            panel._action_layout.addWidget(widget)
        widget.hide()
    panel._footer_actions = actions
    panel._footer_layout.addWidget(actions, 1, 1, Qt.AlignmentFlag.AlignRight)
    panel._footer_layout.setColumnStretch(1, 1)
    outer.addWidget(panel._footer)

    cfg = panel._mw.load_config() if hasattr(panel._mw, "load_config") else {}
    panel._bs_entry.setText(cfg.get("chk_bs_url", ""))
    panel._moodle_entry.setText(cfg.get("chk_moodle_url", ""))
    panel._bs_entry.textChanged.connect(panel._invalidate_scan)
    panel._moodle_entry.textChanged.connect(panel._invalidate_scan)
    panel._view, panel._active_group = "overview", None
    panel._reflow_courses()
    panel._set_view("overview")
