"""Review navigation and truthful display states, separate from browser work."""
from PySide6.QtCore import QTimer, QPoint, Qt

import gui_styles
from gui_tool_icons import tool_pixmap
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
        margin = 16 if compact else 24
        self.layout().setContentsMargins(margin, 22, margin, 18)
        for key, row in self._fix_rows.items():
            grid = row.layout()
            count, show = self._fix_counts[key], self._finding_buttons[key]
            grid.removeWidget(count)
            grid.removeWidget(show)
            if compact:
                grid.addWidget(count, 2, 2)
                grid.addWidget(show, 3, 2, Qt.AlignmentFlag.AlignLeft)
            else:
                grid.addWidget(count, 0, 3, 2, 1)
                grid.addWidget(show, 0, 4, 2, 1)
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
        for icon, name in self._fix_icons:
            icon.setPixmap(tool_pixmap(name, 24, colors["muted"]))
        for icon, name, semantic in self._status_icons:
            icon.setPixmap(tool_pixmap(name, 22, colors[semantic]))
        for icon in self.findChildren(QLabel):
            name = icon.property("summary-icon")
            if name:
                icon.setPixmap(tool_pixmap(name, 24, colors[icon.property("semantic")]))
        self._paint_stages()
        self._log.set_log_colors({"info": colors["text"], "success": colors["success"],
                                  "warning": colors["warning"], "error": colors["error"],
                                  "dim": colors["muted"], "detail": colors["muted"], "step": colors["focus"]})

    def _summary_row(self, title, description, icon_name, semantic="muted", count=""):
        from PySide6.QtWidgets import QFrame, QHBoxLayout
        from panels.checker_workflow import column
        row = QFrame()
        row.setProperty("role", "checker-fix-row")
        line = QHBoxLayout(row)
        line.setContentsMargins(0, 6, 0, 6)
        line.setSpacing(14)
        icon = QLabel()
        icon.setFixedSize(24, 24)
        icon.setProperty("summary-icon", icon_name)
        icon.setProperty("semantic", semantic)
        icon.setPixmap(tool_pixmap(icon_name, 24, gui_styles.modern_colors()[semantic]))
        line.addWidget(icon, 0, Qt.AlignmentFlag.AlignTop)
        text, items = column(spacing=4)
        items.addWidget(label(title, "finding-title"))
        if description:
            items.addWidget(label(description, "dim"))
        line.addWidget(text, 1)
        if count:
            line.addWidget(label(count, "dim"))
        return row

    @staticmethod
    def _clear_rows(layout):
        while layout.count():
            widget = layout.takeAt(0).widget()
            widget.hide()
            widget.setParent(None)
            widget.deleteLater()

    def _render_running_choices(self, keys):
        from panels.checker_workflow import column
        self._clear_rows(self._apply_choices_layout)
        self._apply_choices_layout.addWidget(label("Your selected fixes", "section-heading"))
        for key, title, description in self.FIXES:
            if key not in keys:
                continue
            row, row_layout = column(spacing=4)
            row_layout.addWidget(label(title, "finding-title"))
            row_layout.addWidget(label(description, "dim"))
            self._apply_choices_layout.addWidget(row)
        self._apply_choices_layout.addWidget(label("Current operation is shown above. Detailed updates are available in activity details.", "hint"))

    def _set_stage(self, stage):
        self._stage = stage
        self._paint_stages()

    def _paint_stages(self):
        colors = gui_styles.modern_colors()
        for index, (step, (icon, name)) in enumerate(zip(self._steps, self._step_icons)):
            # A partial/failed Apply is never painted as successfully complete.
            complete = index in getattr(self, "_completed_stages", set())
            state = "active" if index == self._stage else "complete" if complete else ""
            step.setProperty("state", state)
            step.setAccessibleDescription("Current stage" if state == "active" else "Completed" if complete else "Not current")
            step.style().unpolish(step)
            step.style().polish(step)
            for child in step.findChildren(QLabel):
                child.style().unpolish(child)
                child.style().polish(child)
            color = colors["focus"] if state == "active" else colors["success"] if complete else colors["muted"]
            icon.setPixmap(tool_pixmap(name, 24, color))
            step.update()

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
        self._editing_courses = True
        self._set_view("overview")
        self._cancel_edit_btn.setVisible(self._scan_current())
        self._moodle_entry.setFocus()

    def _cancel_course_edit(self):
        if self._scan_current():
            self._editing_courses = False
            self._set_view("overview")

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
        current = self._scan_current()
        setup = view == "overview" and (not current or getattr(self, "_editing_courses", False))
        self._overview.setVisible(view == "overview")
        self._group_page.setVisible(view == "group")
        self._confirmation.setVisible(view == "confirm")
        self._result.setVisible(view == "result")
        self._review_surface.setVisible(not setup)
        self._empty_state.setVisible(setup)
        self._course_form.setVisible(setup and not self._task_active)
        self._course_summary.setVisible(not setup and view != "confirm")
        if view != "group":
            self._group_layout.removeWidget(self._fixes_widget)
            self._review_layout.insertWidget(3, self._fixes_widget)
            for row in self._fix_rows.values():
                row.show()
            self._optional_heading.show()
        self._workspace.setVisible(not self._task_active)
        if not self._task_active:
            self._set_stage(0 if setup else 3 if view == "result" else 1)
        self._update_apply_enabled()
        self._scroll.verticalScrollBar().setValue(0)

    def show_review(self):
        if not self._task_active:
            self._set_view("overview")

    def show_transfer(self):
        if self._scan_current() and not self._task_active:
            self._editing_courses = False
            self.show_review()

    def _open_group(self, group):
        if self._task_active:
            return
        if group != "other" and not self._scan_current():
            return
        self._active_group = group
        title, description, keys = GROUPS[group]
        self._group_title.setText(title)
        self._group_intro.setText(description + ". Choose a fix type below." if group != "other" else description)
        self._review_layout.removeWidget(self._fixes_widget)
        self._group_layout.insertWidget(3, self._fixes_widget)
        self._optional_heading.hide()
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
        if self._task_active:
            if self._run_mode == "scan" and not self._back_to_courses_pending:
                self._back_to_courses_pending = True
                self._stop_run()
                self._update_apply_enabled()
            return
        if self._view in ("confirm", "group"):
            self._editing_courses = False
            self.show_review()
        elif self._view == "result":
            self._edit_courses()
        elif getattr(self, "_editing_courses", False) and self._scan_current():
            self._cancel_course_edit()
        elif self._scan_current():
            self._edit_courses()

    def _toggle_details(self, shown):
        self._log.setVisible(shown)
        self._details_btn.setText("Hide activity details" if shown else "Show activity details")
        self._scroll.updateGeometry()
        # Qt propagates wrapped-label height changes through nested layouts
        # on the next event-loop turn. Refit after that propagation as well.
        QTimer.singleShot(0, self._scroll.updateGeometry)
        if shown:
            QTimer.singleShot(0, lambda: self._ensure_visible(self._log))

    def _invalidate_scan(self, *_args):
        self._scan_report = None
        self._scan_urls = None
        self._completed_stages = set()
        self._editing_courses = True
        for box in self._fix_boxes.values():
            box.setChecked(False)
            box.setEnabled(False)
        for error in self._course_errors.values():
            error.hide()
        self._cancel_edit_btn.hide()
        self._summary.setText("Course URLs changed. Scan again before choosing fixes.")
        self._summary.show()
        self._empty_title.setText("Ready to compare")
        self._empty_hint.setText("The browser may ask you to sign in. You’ll review any fixes before applying them.")
        self._empty_state.show()
        self._refresh_course_context()
        self._course_form.show()
        self._course_summary.hide()
        self._set_view("overview")

    def _selected_fixes(self):
        return {key: box.isChecked() and box.isEnabled() for key, box in self._fix_boxes.items()}

    def _update_apply_enabled(self, *_args):
        count = sum(self._selected_fixes().values())
        current, active = self._scan_current(), self._task_active
        self._apply_choices.setVisible(active and getattr(self, "_run_mode", "scan") == "apply")
        for widget in (self._run_btn, self._apply_btn, self._confirm_btn, self._stop_btn):
            widget.hide()
        self._back_btn.show()
        returning = self._back_to_courses_pending
        self._back_btn.setText("Going back…" if returning else "Back to review" if self._view == "confirm" else "Back to courses" if self._view == "result" else "Back")
        self._back_btn.setEnabled(not returning and (
            (active and self._run_mode == "scan" and not self._stop_flag[0]) or
            (not active and (current or self._view == "result"))))
        self._change_btn.setEnabled(not active)
        self._change_btn.setVisible(not active)
        self._bs_entry.setEnabled(not active)
        self._moodle_entry.setEnabled(not active)
        self._rescan_btn.setVisible(current and not active)
        self._apply_btn.setEnabled(current and count > 0 and not active)
        self._confirm_btn.setEnabled(current and count > 0 and not active)
        self._confirm_btn.setText(f"Apply {count} fix {'type' if count == 1 else 'types'}")
        self._apply_btn.setText("Review selected fixes")
        if active:
            self._stop_btn.show()
            self._run_btn.show()
            self._run_btn.setText("Scanning…" if self._run_mode == "scan" else "Applying…")
            self._run_btn.setEnabled(False)
            self._selection_summary.setText("Stopping keeps completed changes." if self._run_mode == "apply" else "Scanning makes no changes to Brightspace.")
        elif self._view == "overview" and (not current or getattr(self, "_editing_courses", False)):
            self._run_btn.show()
            self._run_btn.setEnabled(True)
            self._run_btn.setText("Scan courses")
            self._selection_summary.setText("No changes are made during scanning.")
        elif self._view in ("overview", "group"):
            self._apply_btn.show()
            self._selection_summary.setText(f"{count} fix {'type' if count == 1 else 'types'} selected · No changes have been applied.")
        elif self._view == "confirm":
            self._confirm_btn.show()
            self._selection_summary.setText("Only your selected fix types will run.")
        elif self._view == "result":
            self._run_btn.setText("Scan again")
            self._run_btn.setEnabled(True)
            self._run_btn.show()
            self._selection_summary.setText("Scan again to check the course’s current state.")
        self._scroll.updateGeometry()
        QTimer.singleShot(0, self._scroll.updateGeometry)

    def _show_scan_report(self, report, scanned_urls):
        if scanned_urls != self._current_urls():
            self._invalidate_scan()
            return
        self._scan_report, self._scan_urls = report, scanned_urls
        self._completed_stages = {0}
        self._editing_courses = False
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
            found = f"{count} {singular[key] if count == 1 else units[key]}" if count is not None else "Course-wide"
            if key == "repair_files" and failed:
                found = "Not checked"
            self._fix_counts[key].setText(found)
            box.setAccessibleName(f"{title}: {found}")
            box.setEnabled((count is None or count > 0) and not (key == "repair_files" and failed))
            if not box.isEnabled():
                box.setChecked(False)
        for key, show in self._finding_buttons.items():
            show.setEnabled(True)
        if not failed:
            self._summary.setText("No missing content or broken file links found. Optional improvements are still available.")
            self._summary.setVisible(not any(report.get(key, 0) for key in ("broken_links", "missing", "files", "activities")))
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
        self._confirm_courses.setText(
            f"From Moodle · {course_context(moodle, 'Moodle')}\n{moodle}\n\n"
            f"To Brightspace · {course_context(bs, 'Brightspace')}\n{bs}")
        self._clear_rows(self._confirm_choices_layout)
        self._confirm_choices.setAccessibleName("Selected fix types")
        for key, title, description in self.FIXES:
            if selected[key]:
                icon_name = dict(repair_files="link", content="file-plus", files="file", activities="clipboard-list", books="book", order="arrows-exchange")[key]
                self._confirm_choices_layout.addWidget(self._summary_row(title, description, icon_name, count=self._fix_counts[key].text()))
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
        failed_counts = any(summary_value for summary_key, summary_value in result.get("summary", {}).items()
                            if summary_key in ("files_failed", "existing_activity_links_failed"))
        summary = result.get("summary", {})
        failed_counts = failed_counts or summary.get("file_link_check") == "failed" or (
            "broken_links_repaired" in summary and summary.get("broken_links_found", 0) > summary["broken_links_repaired"])
        self._apply_succeeded = result.get("mode") == "apply" and status == "finished" and not notices and not failed_counts
        if result.get("mode") == "apply":
            self._completed_stages = {0, 1, 2} if self._apply_succeeded else {0, 1}
        if status == "finished" and failed_counts:
            title = "Run finished with issues to review"
        self._result_title.setText(title)
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
            lines.append("Scan again to verify the course, including content, Books and page order. Activity details show the work attempted during this run.")
            if status != "finished":
                lines.append("Some changes may already have been applied. Stopping does not undo them.")
        else:
            lines.append("Some findings were collected, but the run could not finish. Review activity details and scan again."
                         if self._scan_current() else "No complete scan report was produced. Review activity details, check your sign-in and try again.")
        if notices:
            lines.insert(0, "Issues to review:\n" + "\n".join(notices[:3]))
        self._clear_rows(self._outcome_layout)
        if result.get("mode") == "apply":
            last_section = None
            for key, text, failure in (
                ("files_uploaded", "Files uploaded", False),
                ("broken_links_repaired", "File links repaired", False),
                ("existing_activity_links_attached", "Activity links attached", False),
                ("existing_activity_links_already_linked", "Activity links already present", False),
                ("files_failed", "File uploads needing attention", True),
                ("existing_activity_links_failed", "Activity links needing attention", True),
            ):
                value = summary.get(key)
                number = len(value) if isinstance(value, list) else value
                if number:
                    section = "Needs attention" if failure else "Completed"
                    if section != last_section:
                        self._outcome_layout.addWidget(label(section, "finding-title"))
                        last_section = section
                    self._outcome_layout.addWidget(self._summary_row(f"{text}: {number}", "", "file" if failure else "circle-check", "warning" if failure else "success"))
                    if failure and isinstance(value, list):
                        for entry in value[:5]:
                            self._outcome_layout.addWidget(label(" · ".join(str(part) for part in entry) if isinstance(entry, (list, tuple)) else str(entry), "dim"))
            # Keep recorded counts in the outcome rows, not repeated in prose.
            if "broken_links_repaired" in summary:
                remaining = max(0, summary.get("broken_links_found", 0) - summary["broken_links_repaired"])
                if remaining:
                    if last_section != "Needs attention":
                        self._outcome_layout.addWidget(label("Needs attention", "finding-title"))
                    self._outcome_layout.addWidget(self._summary_row(f"File links needing attention: {remaining}", "", "link", "warning"))
            lines = [line for line in lines if not any(line.startswith(prefix + ":") for prefix in
                     ("Files uploaded", "File uploads failed", "File links repaired", "Activity links attached", "Activity links already present", "Activity links failed"))]
        self._result_body.setText("\n\n".join(lines))
        self._scan_report = None
        self._scan_urls = None
        for box in self._fix_boxes.values():
            box.setChecked(False)
            box.setEnabled(False)
        self._course_form.hide()
        self._course_summary.show()
        self._refresh_course_context()
        self._set_view("result")
