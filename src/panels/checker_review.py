"""Review navigation and truthful display states, separate from browser work."""
from PySide6.QtCore import Qt, QTimer, QPoint

import gui_styles
from gui_icons import make_pixmap
from PySide6.QtWidgets import QLabel
from panels.checker_workflow import GROUPS, build, course_context, label


class CheckerReview:
    def _build(self):
        build(self)

    def _reflow_courses(self):
        compact = self.width() < 760
        if compact == self._compact_courses:
            return
        self._compact_courses = compact
        for field in self._course_fields:
            self._course_grid.removeWidget(field)
        for index, field in enumerate(self._course_fields):
            self._course_grid.addWidget(field, index if compact else 0, 0 if compact else index)
        self._course_grid.setColumnStretch(0, 1)
        self._course_grid.setColumnStretch(1, 0 if compact else 1)
        self._footer_layout.removeWidget(self._footer_actions)
        self._footer_layout.addWidget(self._footer_actions, 1 if compact else 0, 0 if compact else 1)
        margin = 22 if compact else 44
        self._scroll.widget().layout().setContentsMargins(margin, 28 if compact else 36, margin, 20)
        self._footer_layout.setContentsMargins(margin, 10, margin, 16)
        self._refresh_course_context()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "_course_fields"):
            self._reflow_courses()
            self._scroll.updateGeometry()

    def _ensure_visible(self, widget):
        position = widget.mapTo(self._scroll.widget(), QPoint(0, 0))
        self._scroll.ensureVisible(0, position.y() + widget.height() // 2, 0, widget.height() // 2 + 16)

    def refresh_theme(self):
        from panels.checker_styles import checker_stylesheet
        self.setStyleSheet(checker_stylesheet())
        colors = gui_styles.modern_colors()
        for key, icon in self._group_icons.items():
            icon.setPixmap(make_pixmap("collect" if key == "books" else "checker", colors["muted"], 28))
            self.findChild(QLabel, f"finding_arrow_{key}").setPixmap(make_pixmap("next", colors["muted"], 16))
        self._log.set_log_colors({"info": colors["text"], "success": colors["success"],
                                  "warning": colors["warning"], "error": colors["error"],
                                  "dim": colors["muted"], "detail": colors["muted"], "step": colors["focus"]})

    def _current_urls(self):
        return self._bs_entry.text().strip(), self._moodle_entry.text().strip()

    def _scan_current(self):
        return self._scan_report is not None and self._scan_urls == self._current_urls()

    def _refresh_course_context(self):
        bs, moodle = self._current_urls()
        source = "Moodle · " + (course_context(moodle, "Moodle") or "Source")
        target = "Brightspace · " + (course_context(bs, "Brightspace") or "Destination")
        self._course_context_label.setText(source + ("\n→ " if self._compact_courses else "  →  ") + target)
        self._course_context_label.setToolTip(f"Moodle source: {moodle}\nBrightspace destination: {bs}")

    def _edit_courses(self):
        if self._task_active:
            return
        self._set_view("overview")
        self._course_summary.hide()
        self._course_form.show()
        self._cancel_edit_btn.setVisible(self._scan_current())
        self._moodle_entry.setFocus()
        self._scroll.verticalScrollBar().setValue(0)

    def _cancel_course_edit(self):
        if self._scan_current():
            self._course_form.hide()
            self._course_summary.show()

    def _validate_courses(self):
        valid = True
        for name, platform, entry in (("moodle", "Moodle", self._moodle_entry), ("bs", "Brightspace", self._bs_entry)):
            value = entry.text().strip()
            okay = course_context(value, platform) is not None
            message = ("Paste a Moodle course or section URL with a numeric id." if name == "moodle"
                       else "Paste a Brightspace course URL containing its course ID.")
            self._course_errors[name].setText("" if okay else message)
            self._course_errors[name].setVisible(not okay)
            if not okay and valid:
                entry.setFocus()
            valid = valid and okay
        if not valid:
            self._course_form.show()
            self._course_summary.hide()
            self._scroll.verticalScrollBar().setValue(0)
        return valid

    def _set_view(self, view):
        self._view = view
        self._overview.setVisible(view == "overview")
        self._group_page.setVisible(view == "group")
        self._confirmation.setVisible(view == "confirm")
        self._result.setVisible(view == "result")
        self._workspace.setVisible(not self._task_active)
        self._update_apply_enabled()
        self._scroll.verticalScrollBar().setValue(0)

    def show_review(self):
        if not self._task_active:
            self._set_view("overview")

    def show_transfer(self):
        if not self._scan_current() or self._task_active:
            return
        for group in ("links", "content", "books", "other"):
            if any(self._fix_boxes[key].isEnabled() for key in GROUPS[group][2]):
                self._open_group(group)
                return

    def _open_group(self, group):
        if self._task_active:
            return
        if group != "other" and not self._scan_current():
            return
        self._active_group = group
        title, description, keys = GROUPS[group]
        self._group_title.setText(title)
        self._group_intro.setText(description + ". Choose a fix type below." if group != "other" else description)
        for key, row in self._fix_rows.items():
            row.setVisible(key in keys)
        self._media_tools.setVisible(group == "other")
        self._render_evidence(group)
        self._set_view("group")

    def _render_evidence(self, group):
        while self._evidence_layout.count():
            item = self._evidence_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()
        report = self._scan_report or {}
        lines = []
        if group == "links":
            if report.get("file_link_check") == "failed":
                lines.append("File links could not be checked. Scan again before selecting repairs.")
            lines.extend(f"{title or 'Untitled page'} — file link needs repair" for title in report.get("broken_pages", []))
            if report.get("broken_links") and not report.get("broken_pages"):
                lines.append("Broken file links were found. Affected pages will be checked again during the run.")
            lines.append("Repairing existing links can upload recovered files into Manage Files.")
        elif group == "content":
            for item in report.get("findings", []):
                status = "Missing" if item.get("status") == "missing" else "Needs review"
                section = f" · {item['section']}" if item.get("section") else ""
                lines.append(f"{status}: {item.get('name') or 'Untitled item'} ({item.get('type') or 'Item'}){section}")
            lines.append("Missing items, file uploads and activity links can overlap. Selecting a fix type does not select individual pages.")
            if not any(self._fix_boxes[key].isEnabled() for key in GROUPS[group][2]):
                lines.append("No automatic fix is available for these matches. Review them in your courses before making changes.")
        elif group == "books":
            lines.extend(f"{item.get('name') or 'Untitled Book'} · {item.get('section') or 'Course'}" for item in report.get("book_findings", []))
            lines.append("Books are an optional conversion, not a broken-link finding. Chapter details are checked during the run.")
        else:
            if not self._scan_current():
                lines.append("Scan your course pair before choosing a page-order fix.")
            if report.get("moodle_links"):
                lines.append(f"{report['moodle_links']} link(s) still point to Moodle. They may need manual rehosting; this is separate from broken-file repair.")
            if report.get("h5p"):
                lines.append(f"{report['h5p']} H5P item(s) found. Review these in the H5P tool.")
        for text in lines:
            self._evidence_layout.addWidget(label(text, "dim"))

    def _go_back(self):
        if self._view == "confirm":
            self._open_group(self._active_group or "links")
        else:
            self.show_review()

    def _toggle_details(self, shown):
        self._log.setVisible(shown)
        self._details_btn.setText("Hide activity details" if shown else "Activity details")
        self._scroll.updateGeometry()
        # Qt propagates wrapped-label height changes through nested layouts
        # on the next event-loop turn. Refit after that propagation as well.
        QTimer.singleShot(0, self._scroll.updateGeometry)
        if shown:
            QTimer.singleShot(0, lambda: self._ensure_visible(self._log))

    def _invalidate_scan(self, *_args):
        self._scan_report = None
        self._scan_urls = None
        self._subtitle.setText("Compare Moodle with Brightspace, then choose what to fix.")
        for box in self._fix_boxes.values():
            box.setChecked(False)
            box.setEnabled(False)
        for row in self._group_rows.values():
            row.hide()
        for error in self._course_errors.values():
            error.hide()
        self._cancel_edit_btn.hide()
        self._summary.setText("Course URLs changed. Scan again before choosing fixes.")
        self._summary.show()
        self._empty_title.setText("Ready to compare")
        self._empty_hint.setText("Your findings will appear here after a scan.")
        self._empty_state.show()
        self._refresh_course_context()
        self._course_form.show()
        self._course_summary.hide()
        self._set_view("overview")

    def _selected_fixes(self):
        return {key: box.isChecked() and box.isEnabled() for key, box in self._fix_boxes.items()}

    def _update_apply_enabled(self, *_args):
        count = sum(self._selected_fixes().values())
        current = self._scan_current()
        active = self._task_active
        for widget in (self._back_btn, self._run_btn, self._review_btn, self._apply_btn, self._confirm_btn, self._stop_btn):
            widget.hide()
        self._change_btn.setEnabled(not active)
        self._bs_entry.setEnabled(not active)
        self._moodle_entry.setEnabled(not active)
        self._rescan_btn.setVisible(current)
        self._review_btn.setEnabled(current and not active)
        self._apply_btn.setEnabled(current and count > 0 and not active)
        self._confirm_btn.setEnabled(current and count > 0 and not active)
        if active:
            self._stop_btn.show()
            self._selection_summary.setText("Stopping does not undo completed changes." if getattr(self, "_run_mode", "scan") == "apply" else "Scanning leaves Brightspace unchanged.")
        elif self._view == "overview":
            (self._review_btn if current else self._run_btn).show()
            self._run_btn.setText("Scan courses")
            self._selection_summary.setText(f"{count} {'fix type' if count == 1 else 'fix types'} selected" if count else "This scan made no changes to Brightspace." if current else "Scanning leaves Brightspace unchanged.")
        elif self._view == "group":
            self._back_btn.show()
            self._apply_btn.show()
            self._selection_summary.setText(f"{count} {'fix type' if count == 1 else 'fix types'} selected" if count else "Choose a fix type to continue.")
        elif self._view == "confirm":
            self._back_btn.show()
            self._confirm_btn.show()
            self._selection_summary.setText("Confirm the course pair before applying.")
        elif self._view == "result":
            self._run_btn.setText("Scan again")
            self._run_btn.show()
            self._selection_summary.setText("Scan again to verify the course's current state.")
        self._scroll.updateGeometry()
        QTimer.singleShot(0, self._scroll.updateGeometry)

    def _show_scan_report(self, report, scanned_urls):
        if scanned_urls != self._current_urls():
            self._invalidate_scan()
            return
        self._scan_report, self._scan_urls = report, scanned_urls
        self._subtitle.setText("Scan complete. Review what needs attention.")
        failed = report.get("file_link_check") == "failed"
        self._summary.setText("File links could not be checked. Other findings are available; scan again to retry.")
        self._summary.setVisible(failed)
        counts = {"repair_files": report.get("broken_links", 0), "files": report.get("files", 0),
                  "activities": report.get("activities", 0), "books": report.get("books", 0), "content": report.get("missing", 0)}
        units = {"repair_files": "links", "files": "files", "activities": "activities", "books": "books", "content": "missing items"}
        singular = {"repair_files": "link", "files": "file", "activities": "activity", "books": "book", "content": "missing item"}
        for key, title, _ in self.FIXES:
            count = counts.get(key)
            box = self._fix_boxes[key]
            box.setText(title + (f" ({count} {singular[key] if count == 1 else units[key]})" if count is not None else ""))
            box.setEnabled((count is None or count > 0) and not (key == "repair_files" and failed))
            if not box.isEnabled():
                box.setChecked(False)
        findings = report.get("findings", [])
        content_available = bool(findings or any(report.get(key, 0) for key in ("missing", "files", "activities")))
        values = {"links": "Not checked" if failed else f"{report.get('broken_links', 0)} links",
                  "content": f"{report.get('missing', 0)} items" if report.get("missing") else "Needs review",
                  "books": f"{report.get('books', 0)} {'book' if report.get('books') == 1 else 'books'}"}
        visible = {"links": failed or bool(report.get("broken_links")), "content": content_available, "books": bool(report.get("books"))}
        for key in values:
            self._group_rows[key].setVisible(visible[key])
            self._group_counts[key].setText(values[key])
            self._group_rows[key].setToolTip("Open findings and fix choices")
            self._group_rows[key].setAccessibleName(f"Review {GROUPS[key][0].lower()}: {values[key]}")
        self._empty_state.setVisible(not any(visible.values()))
        self._empty_title.setText("No missing content or broken file links found")
        self._empty_hint.setText("Page order and media can still be reviewed under Other options.")
        self._course_form.hide()
        self._course_summary.show()
        self._refresh_course_context()
        self._set_view("overview")

    def _preview_selected(self):
        if self._task_active:
            return
        if not self._scan_current():
            self._invalidate_scan()
            return
        selected = self._selected_fixes()
        if not any(selected.values()):
            return
        bs, moodle = self._current_urls()
        self._confirm_courses.setText(f"Moodle source: {moodle}\n\nBrightspace destination: {bs}")
        self._confirm_choices.setText("Selected fix types:\n\n" + "\n".join(title for key, title, _ in self.FIXES if selected[key]))
        self._set_view("confirm")
        # Applying requires an intentional click, not Enter left over from navigation.
        self._back_btn.setFocus()

    def _apply_selected(self):
        if self._view != "confirm" or self._task_active:
            return
        if not self._scan_current():
            self._invalidate_scan()
            return
        if not self._mw.chromium_ready:
            self._selection_summary.setText("Browser engine is still installing. Try again when it is ready.")
            return
        selected = self._selected_fixes()
        if not any(selected.values()):
            return
        if selected == {key: key == "books" for key in selected}:
            self._run_worker(books_only=True)
        elif selected == {key: key == "order" for key in selected}:
            self._run_worker(order_only=True)
        else:
            self._run_worker(full_run=True, operations=selected)

    def _show_run_result(self, result):
        if result.get("mode") == "scan" and result.get("status") == "finished" and self._scan_current():
            self._set_view("overview")
            return
        status = result.get("status", "incomplete")
        title = {"stopped": "Run stopped", "failed": "Run could not finish", "finished": "Run finished", "incomplete": "Run ended before completion"}[status]
        notices = result.get("errors", [])
        if status == "finished" and notices:
            title = "Run finished with issues to review"
        self._result_title.setText(title)
        summary = result.get("summary", {})
        lines = []
        if result.get("mode") == "apply":
            for key, text in (("files_uploaded", "Files uploaded"), ("files_failed", "File uploads failed"),
                              ("broken_links_repaired", "File links repaired"), ("existing_activity_links_attached", "Activity links attached"),
                              ("existing_activity_links_already_linked", "Activity links already present"), ("existing_activity_links_failed", "Activity links failed")):
                if key in summary:
                    value = summary[key]
                    number = len(value) if isinstance(value, list) else value
                    lines.append(f"{text}: {number}")
            if summary.get("file_link_check") == "failed":
                lines.append("File links could not be checked during this run. Scan again before interpreting the link counts.")
            lines.append("These are recorded outcomes, not a complete verification of every selected fix. Review activity details and scan again to verify content, Books and ordering.")
            if status != "finished":
                lines.append("Some changes may already have been applied. Stopping does not undo them.")
        else:
            lines.append("Some findings were collected, but the run could not finish. Review activity details and scan again."
                         if self._scan_current() else "No complete scan report was produced. Review activity details, check your sign-in and try again.")
        if notices:
            lines.append("Issues to review:\n" + "\n".join(notices[:3]))
        self._result_body.setText("\n\n".join(lines))
        self._scan_report = None
        self._scan_urls = None
        for box in self._fix_boxes.values():
            box.setChecked(False)
            box.setEnabled(False)
        self._subtitle.setText("Review the outcome, then scan again to verify.")
        self._course_form.hide()
        self._course_summary.show()
        self._refresh_course_context()
        self._set_view("result")
