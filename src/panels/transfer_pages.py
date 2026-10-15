"""Structured Transfer steps other than Arrange: Connect, Starting point, Styling and Create."""
from __future__ import annotations

import os

from PySide6.QtCore import Qt, QUrl
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QFrame, QGridLayout, QHBoxLayout, QLineEdit,
                               QListWidget, QListWidgetItem, QPlainTextEdit, QProgressBar, QRadioButton, QScrollArea,
                               QSplitter, QStackedWidget, QTextBrowser, QVBoxLayout, QWidget, QSizePolicy)

import transfer_outline as o
from panels.transfer_arrange import button, label
from transfer_styling import MODES


def card(selected=False):
    frame = QFrame(); frame.setProperty("role", "card"); frame.setProperty("selected", "true" if selected else "false")
    return frame


def repolish(widget, **properties):
    for key, value in properties.items(): widget.setProperty(key, value)
    widget.style().unpolish(widget); widget.style().polish(widget)


def scrolled(widget):
    area = QScrollArea(); area.setWidgetResizable(True); area.setFrameShape(QFrame.Shape.NoFrame)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff); area.setWidget(widget)
    return area


def clear(layout):
    while layout.count():
        item = layout.takeAt(0)
        if item.widget(): item.widget().deleteLater()
        elif item.layout(): clear(item.layout())


# ── 1. Connect ──────────────────────────────────────────────────────────

class ConnectPage(QWidget):
    def __init__(self, panel):
        super().__init__()
        self.panel = panel
        box = QVBoxLayout(self); box.setContentsMargins(30, 10, 30, 20); box.setSpacing(18)
        box.addWidget(label("Paste the two course links. Scanning only reads both courses; nothing changes until you create the transfer."))
        self.pair = QHBoxLayout(); self.pair.setSpacing(16)
        self.source, self.target = QLineEdit(), QLineEdit()
        for field, title, hint in ((self.source, "Copy from", "The course whose content you want to reuse."),
                                   (self.target, "Copy into", "The destination shell. Only this course is changed.")):
            frame = card(); inner = QVBoxLayout(frame); inner.setContentsMargins(18, 16, 18, 18); inner.setSpacing(8)
            inner.addWidget(label(title, "field")); inner.addWidget(label(hint))
            field.setPlaceholderText("https://learn.okanagancollege.ca/d2l/home/12345"); field.setAccessibleName(title)
            field.textChanged.connect(panel.update_actions); inner.addWidget(field)
            self.pair.addWidget(frame, 1)
        box.addLayout(self.pair)
        self.saved = card(); saved = QHBoxLayout(self.saved); saved.setContentsMargins(18, 14, 18, 14)
        text = QVBoxLayout(); text.setSpacing(2)
        self.saved_title = label("", "field"); self.saved_detail = label("")
        text.addWidget(self.saved_title); text.addWidget(self.saved_detail); saved.addLayout(text, 1)
        self.continue_saved = button("Continue", lambda: panel.go(panel.resume_step()), "secondary")
        saved.addWidget(self.continue_saved)
        box.addWidget(self.saved)
        self.open_saved = button("Open a saved transfer…", panel.open_plan); box.addWidget(self.open_saved, 0, Qt.AlignmentFlag.AlignLeft)
        box.addStretch()

    def render(self):
        plan = self.panel._plan
        self.saved.setVisible(bool(plan))
        if plan:
            if o.is_outline(plan):
                waiting = len(o.unplaced(plan))
                state = "Created" if plan.get("run_signature") and plan.get("report") else f"{waiting} to place" if waiting else "Ready to review"
                self.saved_title.setText(f"{plan['source']['name']} → {plan['target']['name']}")
                self.saved_detail.setText(f"Saved transfer · {len(plan['outline'])} modules · {state}")
            else:
                self.saved_title.setText(f"{plan['source']['name']} → {plan['target']['name']}")
                self.saved_detail.setText("Made with the earlier workshop version of this screen.")


# ── 2. Starting point ───────────────────────────────────────────────────

STARTS = (("mirror", "Copy the source as it is",
           "The destination gets the same modules and folders as the source, with everything already in them. "
           "Best when the source is already organized the way you want."),
          ("pattern", "Give every module the same sections",
           "One module per source module, each split into the same sections (choose a layout below). Items the app is "
           "sure about are sorted in for you. Best when the destination should follow a standard layout."),
          ("destination", "Fill the destination's existing modules",
           "Keep the destination's modules as they are and add content into the ones with matching names. "
           "Best when the shell is already built and just needs content."),
          ("empty", "Build it myself",
           "Start with an empty outline, add your own modules and drag in what you need."))


class StartPage(QWidget):
    def __init__(self, panel):
        super().__init__()
        self.panel = panel
        outer = QVBoxLayout(self); outer.setContentsMargins(0, 0, 0, 0)
        content = QWidget(); box = QVBoxLayout(content); box.setContentsMargins(30, 10, 30, 20); box.setSpacing(12)
        self.scanned = label(""); box.addWidget(self.scanned)
        box.addWidget(label("How should the destination be organized to begin with?", "field"))
        box.addWidget(label("This only sets up a first draft. In the next step you can move, rename, add or remove anything."))
        self.group = QButtonGroup(self); self.cards = {}
        for key, title, text in STARTS:
            frame = card(); inner = QVBoxLayout(frame); inner.setContentsMargins(16, 12, 16, 14); inner.setSpacing(4)
            radio = QRadioButton(title); radio.setProperty("start", key); self.group.addButton(radio)
            radio.toggled.connect(self.changed); inner.addWidget(radio)
            detail = label(text); detail.setContentsMargins(28, 0, 0, 0); inner.addWidget(detail)
            if key == "pattern":
                self.pattern = QComboBox(); self.pattern.setAccessibleName("Pattern"); self.pattern.currentIndexChanged.connect(self.changed)
                self.pattern_detail = label(""); row = QVBoxLayout(); row.setContentsMargins(28, 6, 0, 0); row.setSpacing(6)
                row.addWidget(self.pattern); row.addWidget(self.pattern_detail); inner.addLayout(row)
            frame.mousePressEvent = lambda _e, r=radio: r.setChecked(True)
            self.cards[key] = (frame, radio); box.addWidget(frame)
        self.replace_note = label("Building again replaces the current outline and its decisions.", "error"); box.addWidget(self.replace_note)
        box.addStretch()
        outer.addWidget(scrolled(content))
        self.cards["pattern"][1].setChecked(True)

    def choice(self):
        checked = self.group.checkedButton()
        return checked.property("start") if checked else "mirror"

    def selected_pattern(self):
        index = self.pattern.currentIndex()
        return self.pattern.itemData(index) if index >= 0 else None

    def render(self):
        scan = self.panel._scan
        if scan:
            source, target = scan
            topics = sum(1 for x in o.all_objects(source["modules"]) if x.get("Type") == 1)
            self.scanned.setText(f"{source['name']}: {len([m for m in source['modules'] if m.get('Type') == 0])} modules, {topics} items  ·  "
                                 f"{target['name']}: {len([m for m in target['modules'] if m.get('Type') == 0])} modules")
            current = self.pattern.currentText()
            self.pattern.blockSignals(True); self.pattern.clear()
            for pattern in self.panel.patterns(scan):
                self.pattern.addItem(pattern["name"], pattern)
            index = self.pattern.findText(current)
            self.pattern.setCurrentIndex(max(0, index)); self.pattern.blockSignals(False)
            has_modules = any(m.get("Type") == 0 for m in target["modules"])
            self.cards["destination"][0].setVisible(has_modules)
            if not has_modules and self.choice() == "destination": self.cards["pattern"][1].setChecked(True)
        plan = self.panel._plan
        self.replace_note.setVisible(bool(plan and o.is_outline(plan) and scan and plan["source"]["id"] == scan[0]["id"]
                                          and plan["target"]["id"] == scan[1]["id"] and not plan.get("run_signature")))
        self.changed()

    def changed(self, *_):
        for key, (frame, radio) in self.cards.items(): repolish(frame, selected="true" if radio.isChecked() else "false")
        pattern = self.selected_pattern()
        self.pattern.setEnabled(self.choice() == "pattern")
        if pattern:
            parts = [s["title"] for s in pattern["sections"] if s.get("kind") != "inline"]
            self.pattern_detail.setText("Each module gets: " + " · ".join(parts) if parts else "Items go straight into each module.")


# ── 4. Styling ──────────────────────────────────────────────────────────

class Preview(QWidget):
    """Rendered HTML when the browser engine is available, otherwise the editor's own renderer."""
    def __init__(self):
        super().__init__()
        box = QVBoxLayout(self); box.setContentsMargins(0, 0, 0, 0)
        self.view = None
        if not os.environ.get("BPA_SIMPLE_PREVIEW"):
            try:
                from PySide6.QtWebEngineWidgets import QWebEngineView
                self.view = QWebEngineView()
            except Exception:
                self.view = None
        self.fallback = QTextBrowser() if self.view is None else None
        box.addWidget(self.view or self.fallback)

    def show_html(self, html, base=""):
        if self.view is not None: self.view.setHtml(html, QUrl(base))
        else: self.fallback.setHtml(html)


class StylingPage(QWidget):
    def __init__(self, panel):
        super().__init__()
        self.panel = panel
        self._cache = {}
        box = QVBoxLayout(self); box.setContentsMargins(30, 6, 30, 12); box.setSpacing(10)
        options = QHBoxLayout(); options.setSpacing(12)
        self.group = QButtonGroup(self); self.radios = {}
        explain = {"preserve": "Copy pages exactly as they are.",
                   "strip_bpa": "Remove what Page Automator added. Keep the author's own formatting.",
                   "strip_all": "Remove all custom styling. Keep content, structure and accessibility."}
        for key, title in MODES.items():
            frame = card(); inner = QVBoxLayout(frame); inner.setContentsMargins(14, 10, 14, 12); inner.setSpacing(2)
            radio = QRadioButton(title); radio.setProperty("mode", key); self.group.addButton(radio)
            radio.toggled.connect(lambda on, k=key: on and self.set_mode(k)); inner.addWidget(radio)
            text = label(explain[key]); text.setContentsMargins(28, 0, 0, 0); inner.addWidget(text)
            frame.mousePressEvent = lambda _e, r=radio: r.setChecked(True)
            self.radios[key] = (frame, radio); options.addWidget(frame, 1)
        box.addLayout(options)
        theme = QHBoxLayout(); theme.setSpacing(10)
        self.apply_template = QCheckBox("Then apply this look:"); self.apply_template.toggled.connect(self.template_changed)
        self.template = QComboBox(); self.template.setAccessibleName("Destination look"); self.template.currentIndexChanged.connect(self.template_changed)
        self.template.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon); self.template.setMinimumContentsLength(24)
        theme.addWidget(self.apply_template); theme.addWidget(self.template, 1); theme.addStretch()
        box.addLayout(theme)
        split = QSplitter(Qt.Orientation.Horizontal); split.setChildrenCollapsible(False); split.setHandleWidth(14)
        left = QFrame(); left.setProperty("role", "pane"); lbox = QVBoxLayout(left); lbox.setContentsMargins(12, 10, 12, 10)
        self.pages_title = label("Pages", "pane-title"); lbox.addWidget(self.pages_title)
        self.pages = QListWidget(); self.pages.setAccessibleName("Pages to copy"); self.pages.currentRowChanged.connect(self.page_selected)
        self.pages.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.pages.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        lbox.addWidget(self.pages, 1)
        right = QFrame(); right.setProperty("role", "pane"); rbox = QVBoxLayout(right); rbox.setContentsMargins(12, 10, 12, 10); rbox.setSpacing(8)
        bar = QHBoxLayout(); bar.setSpacing(4)
        self.before = button("Before", lambda: self.show_side("before"), "step"); self.after = button("After", lambda: self.show_side("after"), "step")
        bar.addWidget(self.before); bar.addWidget(label("·", "dot", wrap=False)); bar.addWidget(self.after); bar.addStretch()
        self.override = QComboBox(); self.override.setAccessibleName("Styling for this page")
        self.override.addItem("Use the setting above", None)
        for key, title in MODES.items(): self.override.addItem(title, key)
        self.override.currentIndexChanged.connect(self.override_changed)
        bar.addWidget(label("This page:", "muted", wrap=False)); bar.addWidget(self.override)
        rbox.addLayout(bar)
        self.summary = label(""); rbox.addWidget(self.summary)
        self.preview = Preview(); rbox.addWidget(self.preview, 1)
        split.addWidget(left); split.addWidget(right); split.setSizes([330, 820])
        box.addWidget(split, 1)
        self.side = "after"

    def rows(self):
        plan = self.panel._plan
        return [plan["items"][n["item"]] for n, _, _ in o.walk(plan["outline"])
                if n["kind"] == "item" and plan["items"].get(n["item"], {}).get("kind") == "page"]

    def result(self, row):
        from transfer_outline_runner import styled_markup
        plan = self.panel._plan
        styling = plan["styling"]
        key = (row["id"], styling["overrides"].get(row["id"], styling["mode"]), styling.get("apply_template"),
               (styling.get("template") or {}).get("id"))
        if key not in self._cache: self._cache[key] = styled_markup(plan, row)
        return self._cache[key]

    def render(self):
        plan = self.panel._plan
        if not plan: return
        styling = plan["styling"]
        locked = not self.panel._editable()
        for key, (frame, radio) in self.radios.items():
            radio.blockSignals(True); radio.setChecked(styling["mode"] == key); radio.blockSignals(False)
            radio.setEnabled(not locked); repolish(frame, selected="true" if styling["mode"] == key else "false")
        self.template.blockSignals(True); self.template.clear()
        for choice in self.panel.template_choices(): self.template.addItem(choice["label"], choice)
        chosen = (styling.get("template") or {}).get("id")
        index = next((i for i in range(self.template.count()) if self.template.itemData(i)["id"] == chosen), 0)
        self.template.setCurrentIndex(index); self.template.blockSignals(False)
        self.apply_template.blockSignals(True); self.apply_template.setChecked(bool(styling.get("apply_template")))
        self.apply_template.blockSignals(False)
        preserve = styling["mode"] == "preserve"
        self.apply_template.setEnabled(not locked and not preserve); self.template.setEnabled(not locked and not preserve and self.apply_template.isChecked())
        self.override.setEnabled(not locked)
        current = self.pages.currentRow()
        self.pages.blockSignals(True); self.pages.clear()
        rows = self.rows()
        changed = 0
        for row in rows:
            result = self.result(row)
            status = "Kept original" if not result.ok else "No change" if not result.removed and not result.templated else "Cleaned"
            changed += status == "Cleaned"
            item = QListWidgetItem(f"{row['title']}\n{status}"); item.setData(Qt.ItemDataRole.UserRole, row["id"])
            item.setToolTip(result.summary); self.pages.addItem(item)
        self.pages.blockSignals(False)
        self.pages_title.setText(f"Pages ({len(rows)})" + (f" · {changed} cleaned" if not preserve else ""))
        if rows: self.pages.setCurrentRow(min(max(current, 0), len(rows) - 1))
        else:
            self.summary.setText("No pages are placed in the outline yet."); self.preview.show_html("")
        self.page_selected(self.pages.currentRow())

    def current_row(self):
        item = self.pages.currentItem()
        return self.panel._plan["items"].get(item.data(Qt.ItemDataRole.UserRole)) if item else None

    def page_selected(self, *_):
        row = self.current_row()
        if not row: return
        plan = self.panel._plan
        self.override.blockSignals(True)
        self.override.setCurrentIndex(max(0, self.override.findData(plan["styling"]["overrides"].get(row["id"]))))
        self.override.blockSignals(False)
        self.show_side(self.side)

    def show_side(self, side):
        self.side = side
        repolish(self.before, state="current" if side == "before" else "")
        repolish(self.after, state="current" if side == "after" else "")
        row = self.current_row()
        if not row: return
        plan = self.panel._plan
        result = self.result(row)
        obj = o.source_of(plan, row)
        if side == "before":
            from transfer_styling import piece_document
            html = piece_document(obj.get("html", ""), row["piece"]["html"], row["title"]) if row.get("piece") else obj.get("html", "")
        else:
            html = result.html
        self.summary.setText(result.summary)
        self.preview.show_html(html, self.panel.host() + (obj.get("Url") or "/"))

    def set_mode(self, mode):
        self.panel.edit_styling(mode=mode)

    def template_changed(self, *_):
        choice = self.template.currentData()
        self.panel.edit_styling(apply_template=self.apply_template.isChecked(),
                                template={k: choice[k] for k in ("id", "label", "html", "url") if k in choice} if choice else None)

    def override_changed(self, *_):
        row = self.current_row()
        if row: self.panel.edit_styling(override=(row["id"], self.override.currentData()))


# ── 5. Create ───────────────────────────────────────────────────────────

class CreatePage(QWidget):
    def __init__(self, panel):
        super().__init__()
        self.panel = panel
        outer = QVBoxLayout(self); outer.setContentsMargins(0, 0, 0, 0)
        content = QWidget(); self.box = box = QVBoxLayout(content); box.setContentsMargins(30, 10, 30, 20); box.setSpacing(14)
        self.issues_title = label("Before you create", "section"); box.addWidget(self.issues_title)
        self.issues = QVBoxLayout(); self.issues.setSpacing(0); box.addLayout(self.issues)
        self.notes_toggle = button("", self.toggle_notes); box.addWidget(self.notes_toggle, 0, Qt.AlignmentFlag.AlignLeft)
        self.notes = label(""); self.notes.hide(); box.addWidget(self.notes)
        box.addWidget(label("What will be created", "section"))
        self.stats = QGridLayout(); self.stats.setSpacing(10); box.addLayout(self.stats)
        self.about = label(""); box.addWidget(self.about)
        # Copy Components always adds the source's grade category, even when one with that name exists.
        self.merge_grades = QCheckBox(""); self.merge_grades.toggled.connect(panel.set_grade_merge); box.addWidget(self.merge_grades)
        self.confirm_card = card(); confirm = QVBoxLayout(self.confirm_card); confirm.setContentsMargins(18, 14, 18, 16); confirm.setSpacing(6)
        self.confirm_target = label("", "field"); confirm.addWidget(self.confirm_target)
        self.confirm = QCheckBox(""); self.confirm.toggled.connect(panel.update_actions); confirm.addWidget(self.confirm)
        box.addWidget(self.confirm_card)
        self.running = QWidget(); run = QVBoxLayout(self.running); run.setContentsMargins(0, 0, 0, 0); run.setSpacing(8)
        self.running_title = label("Creating the transfer…", "section"); run.addWidget(self.running_title)
        self.progress = QProgressBar(); self.progress.setRange(0, 0); self.progress.setTextVisible(False); run.addWidget(self.progress)
        self.running_detail = label(""); run.addWidget(self.running_detail)
        box.addWidget(self.running)
        self.report = QWidget(); rep = QVBoxLayout(self.report); rep.setContentsMargins(0, 0, 0, 0); rep.setSpacing(8)
        self.report_title = label("", "section"); rep.addWidget(self.report_title)
        self.report_detail = label(""); rep.addWidget(self.report_detail)
        self.report_text = QPlainTextEdit(); self.report_text.setObjectName("transfer_text"); self.report_text.setReadOnly(True)
        self.report_text.setMinimumHeight(260); rep.addWidget(self.report_text)
        box.addWidget(self.report)
        box.addStretch()
        outer.addWidget(scrolled(content))

    def toggle_notes(self):
        self.notes.setVisible(self.notes.isHidden()); self.render()

    def render(self):
        plan = self.panel._plan
        if not plan or not o.is_outline(plan): return
        running, done = self.panel._creating, bool(plan.get("report")) and not self.panel._creating
        issues = o.check(plan)
        errors = [i for i in issues if i["level"] == "error"]
        notes = [i["text"] for i in issues if i["level"] != "error"]
        clear(self.issues)
        editing = not running and not plan.get("run_signature")
        self.issues_title.setVisible(editing)
        if editing:
            if not errors:
                self.issues.addWidget(label("Nothing to fix. Review the summary below, then create."))
            for issue in errors:
                row = QFrame(); row.setProperty("role", "issue"); line = QHBoxLayout(row); line.setContentsMargins(0, 8, 0, 8)
                line.addWidget(label(issue["text"]), 1)
                if issue.get("fix") == "use_existing":
                    line.addWidget(button("Use the existing one", lambda i=issue: self.panel.use_existing(i["node"]), "small"))
                if issue.get("node") or issue.get("row") or "still need" in issue["text"]:
                    line.addWidget(button("Show", lambda i=issue: self.panel.show_issue(i), "small"))
                self.issues.addWidget(row)
        self.notes_toggle.setVisible(editing and bool(notes))
        self.notes_toggle.setText(("Hide" if self.notes.isVisible() else "Show") + f" notes ({len(notes)})")
        self.notes.setText("• " + "\n• ".join(notes)); self.notes.setVisible(editing and self.notes.isVisible() and bool(notes))
        clear(self.stats)
        for index, (name, value) in enumerate(o.counts(plan).items()):
            tile = QFrame(); tile.setProperty("role", "stat"); inner = QVBoxLayout(tile); inner.setContentsMargins(14, 10, 14, 12); inner.setSpacing(0)
            inner.addWidget(label(name.capitalize(), "muted", wrap=False)); inner.addWidget(label(str(value), "stat-value", wrap=False))
            self.stats.addWidget(tile, index // 5, index % 5)
        styling = plan["styling"]
        self.about.setText(f"Files are copied into {plan['target']['name']}'s own course files and links point to them. "
                           f"Pages: {MODES[styling['mode']]}" + (", then the chosen look." if styling.get("apply_template") and styling["mode"] != "preserve" else ".")
                           + " Activities bring their grade items with them.")
        self.merge_grades.blockSignals(True)
        self.merge_grades.setChecked(plan.get("grade_categories", "merge") == "merge")
        self.merge_grades.setText(f"Put copied grade items into {plan['target']['name']}'s categories with the same name")
        self.merge_grades.setEnabled(editing); self.merge_grades.setVisible(o.counts(plan)["activities"] > 0)
        self.merge_grades.blockSignals(False)
        self.confirm_card.setVisible(editing or bool(plan.get("run_signature")) and not done and not running)
        self.confirm_target.setText(f"{plan['target']['name']} · {plan['target']['id']}")
        self.confirm.setText(f"Add this as hidden content to {plan['target']['name']}")
        self.running.setVisible(running)
        self.report.setVisible(done)
        if done:
            checks = plan.get("validation") or {}
            failed = [k for k, ok in checks.items() if not ok]
            complete = bool(checks) and not failed
            self.report_title.setText("Transfer created" if complete else "Transfer needs attention")
            self.report_detail.setText((f"All {len(checks)} checks passed. New Content is hidden until you publish it." if complete else
                                        f"{len(failed)} of {len(checks)} checks failed. Resume to try again, or review the report.")
                                       if checks else "The transfer stopped before finishing. Resume to continue where it left off.")
            self.report_text.setPlainText(plan["report"])

    def progress_message(self, message):
        self.running_detail.setText(message)
