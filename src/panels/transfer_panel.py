"""Structured Transfer: copy any Brightspace course into any destination, reorganized.

Five steps: Connect → Starting point → Arrange → Styling → Create. The plan is
saved after every change and can be closed, reopened and resumed. One primary
button per step, always bottom right.
"""
from __future__ import annotations

import asyncio
import copy
import queue
import re
import threading
import webbrowser
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QApplication, QBoxLayout, QFileDialog, QFrame, QHBoxLayout, QMenu, QMessageBox, QPlainTextEdit,
                               QSizePolicy, QSplitter, QStackedWidget, QVBoxLayout, QWidget, QDialog, QDialogButtonBox)

import transfer_outline as o
from bulk_page_creator import course_id_from_url
from config import USERDATA_DIR
from gui_log import LogWidget
from panels.transfer_arrange import ArrangePage, button, label
from panels.transfer_pages import ConnectPage, CreatePage, StartPage, StylingPage, repolish
from panels.transfer_styles import transfer_stylesheet
from transfer_runner import load_plan, save_plan, with_transfer_browser

HOST = "https://learn.okanagancollege.ca"
STEPS = ("Connect", "Starting point", "Arrange", "Styling", "Create")
SUBTITLES = ("Copy any course into any destination, reorganized your way.",
             "Choose how the destination outline begins.",
             "Drag items from the source into the destination outline. Anything left over can be skipped.",
             "Choose what happens to the pages' existing styling. Content always stays.",
             "Check the plan, then create it. New Content starts hidden.")
LEGACY = 5
DATA_DIR = USERDATA_DIR / "structured-transfers"


def course_id(url):
    """Course ID from any course link people paste: home, content, lessons or ?ou=."""
    found = re.match(r"^https://learn\.okanagancollege\.ca/d2l/home/(\d+)(?:[/?#]|$)", url.strip())
    return found.group(1) if found else course_id_from_url(url)


class TransferPanel(QWidget):
    def __init__(self, main_window):
        super().__init__(main_window)
        self.setObjectName("transfer_panel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        self._mw = main_window
        self._plan, self._path, self._scan = None, None, None
        self._busy = self._creating = False
        self._step, self._narrow = 0, None
        self._notice = ""
        self._templates = (None, [])
        self._studio = None
        self._queue, self._stop = queue.Queue(), threading.Event()
        self._build()
        self._timer = QTimer(self); self._timer.timeout.connect(self._poll); self._timer.start(100)
        config = main_window.load_config() if hasattr(main_window, "load_config") else {}
        self._studio_saved_path = config.get("transfer_studio_plan_path")
        QApplication.instance().aboutToQuit.connect(self.close_studio)
        self.connect.source.setText(config.get("transfer_source_url", ""))
        self.connect.target.setText(config.get("transfer_target_url", ""))
        saved = config.get("transfer_plan_path")
        if saved and Path(saved).exists():
            try: self.set_plan(load_plan(saved), Path(saved), save=False)
            except Exception as exc: self._log.append_log(f"The last transfer couldn't be reopened: {exc}", "warning")
        self.refresh_theme()
        self.go(0)

    # ── Construction ───────────────────────────────────────────────────
    def _build(self):
        root = QVBoxLayout(self); root.setContentsMargins(0, 0, 0, 0); root.setSpacing(0)
        head = QWidget(); self._head = QBoxLayout(QBoxLayout.Direction.LeftToRight, head)
        self._head.setContentsMargins(30, 22, 30, 12); self._head.setSpacing(12)
        titles = QVBoxLayout(); titles.setSpacing(4)
        titles.addWidget(label("Structured Transfer", "heading", wrap=False))
        self._subtitle = label(""); titles.addWidget(self._subtitle)
        self._head.addLayout(titles, 1)
        steps = QHBoxLayout(); steps.setSpacing(6); self._step_buttons = []
        for index, name in enumerate(STEPS):
            if index: steps.addWidget(label("·", "dot", wrap=False))
            step = button(name, lambda i=index: self.go(i), "step"); self._step_buttons.append(step); steps.addWidget(step)
        self._head.addLayout(steps); self._head.setAlignment(steps, Qt.AlignmentFlag.AlignTop); self._steps = steps
        root.addWidget(head)
        self._pages = QStackedWidget()
        self.connect, self.start, self.arrange = ConnectPage(self), StartPage(self), ArrangePage(self)
        self.styling, self.create, self.legacy = StylingPage(self), CreatePage(self), self._build_legacy()
        for page in (self.connect, self.start, self.arrange, self.styling, self.create, self.legacy): self._pages.addWidget(page)
        root.addWidget(self._pages, 1)
        self._log_box = QWidget(); lbox = QVBoxLayout(self._log_box); lbox.setContentsMargins(30, 0, 30, 8)
        self._log = LogWidget(); self._log.setMaximumHeight(160); lbox.addWidget(self._log)
        self._log_box.hide(); root.addWidget(self._log_box)
        footer = QFrame(); footer.setObjectName("transfer_footer")
        self._footer = QBoxLayout(QBoxLayout.Direction.LeftToRight, footer); self._footer.setContentsMargins(30, 14, 30, 14); self._footer.setSpacing(10)
        self._status = label(""); self._footer.addWidget(self._status, 1)
        actions = QHBoxLayout(); actions.setSpacing(8)
        self._back = button("Back", lambda: self.go(self._step - 1))
        self._secondary = button("", self._secondary_clicked)
        self._more = button("More", None); menu = QMenu(self._more); menu.aboutToShow.connect(lambda: self._fill_more(menu)); self._more.setMenu(menu)
        self._stop_button = button("Stop after this step", self.stop_work, "secondary")
        self._primary = button("", self._primary_clicked, "primary")
        for widget in (self._back, self._secondary, self._more, self._stop_button, self._primary): actions.addWidget(widget)
        self._footer.addLayout(actions)
        root.addWidget(footer)

    def _build_legacy(self):
        page = QWidget(); box = QVBoxLayout(page); box.setContentsMargins(30, 10, 30, 20); box.setSpacing(12)
        box.addWidget(label("Earlier workshop transfer", "section"))
        self._legacy_text = label(""); box.addWidget(self._legacy_text)
        self._legacy_report = QPlainTextEdit(); self._legacy_report.setObjectName("transfer_text"); self._legacy_report.setReadOnly(True)
        box.addWidget(self._legacy_report, 1)
        return page

    # ── App integration ────────────────────────────────────────────────
    def refresh_theme(self):
        self.setStyleSheet(transfer_stylesheet())
        self._log.refresh_theme()
        self.render()

    def save_state(self):
        if self._plan and self._path and not self._busy:
            if self._path.exists() and self._path.stat().st_mtime_ns != self._plan_stamp:
                self._plan = load_plan(self._path)
                self._plan_stamp = self._path.stat().st_mtime_ns
                self.notify("This saved draft changed in another view. The in-app view has been refreshed.")
            else:
                save_plan(self._plan, self._path)
                self._plan_stamp = self._path.stat().st_mtime_ns
        if hasattr(self._mw, "save_config"):
            self._mw.save_config({"transfer_source_url": self.connect.source.text(), "transfer_target_url": self.connect.target.text(),
                                  "transfer_plan_path": str(self._path) if self._path else "",
                                  "transfer_studio_plan_path": str(self._studio.path) if self._studio and self._studio.path else self._studio_saved_path or ""})

    def open_studio(self):
        """Launch the local web planner; browser authentication stays in this app."""
        from transfer_studio import TransferStudio
        if self._studio is None:
            self._studio = TransferStudio(DATA_DIR,
                lambda request: self._queue.put(("studio_request", request)),
                self.connect.source.text(), self.connect.target.text(), self._studio_saved_path,
                request_create=lambda request: self._queue.put(("studio_create", request)),
                on_open=lambda path: self._queue.put(("studio_opened", path)))
        try:
            if not webbrowser.open(self._studio.start()):
                QMessageBox.information(self, "Open Transfer Studio",
                    "The default browser could not be opened. Check your system's default browser and try Plan transfer again.")
        except Exception as exc:
            self.notify(f"Transfer Studio could not open: {exc}")

    def open_studio_in_app(self):
        """Explicit fallback action: open the web draft in the existing editor."""
        path = self._studio.path if self._studio else self._studio_saved_path
        if path and not self._busy and not (self._studio and self._studio.scanning):
            try:
                self.set_plan(load_plan(path), path, save=False)
                self.go(self.resume_step())
            except Exception as exc:
                self.notify(f"The Studio draft could not be opened: {exc}")

    def close_studio(self):
        if self._studio:
            self._stop.set()
            self._studio.close()
            self._studio = None

    def _credentials(self):
        """Only the desktop browser worker receives these credentials."""
        return {k: getattr(self._mw, k, "") for k in ("bs_username", "bs_password", "sso_email", "sso_password")}

    def stop_work(self):
        self._stop.set()
        if self._studio: self._studio.stop()

    def host(self):
        return HOST

    def resizeEvent(self, event):
        super().resizeEvent(event)
        narrow = self.width() < 900
        if narrow == self._narrow: return
        self._narrow = narrow
        vertical, horizontal = QBoxLayout.Direction.TopToBottom, QBoxLayout.Direction.LeftToRight
        self._head.setDirection(vertical if narrow else horizontal)
        side = Qt.AlignmentFlag.AlignLeft if narrow else Qt.AlignmentFlag.AlignRight
        self._head.setAlignment(self._steps, Qt.AlignmentFlag.AlignTop | side)
        self.connect.pair.setDirection(vertical if narrow else horizontal)
        self._footer.setDirection(vertical if self.width() < 640 else horizontal)
        orientation = Qt.Orientation.Vertical if narrow else Qt.Orientation.Horizontal
        for split in (self.arrange.split, self.styling.findChild(QSplitter)): split.setOrientation(orientation)

    # ── State ──────────────────────────────────────────────────────────
    def _locked(self):
        return bool(self._plan and self._plan.get("run_signature"))

    def _editable(self):
        return bool(self._plan) and o.is_outline(self._plan) and not self._busy and not self._locked()

    def set_plan(self, plan, path=None, save=True):
        self._plan = plan
        self._path = Path(path) if path else DATA_DIR / (plan["id"] + ".json")
        self._plan_stamp = self._path.stat().st_mtime_ns if self._path.exists() else None
        self._scan = (plan["source"], plan["target"])
        self.connect.source.setText(f"{HOST}/d2l/le/lessons/{plan['source']['id']}")
        self.connect.target.setText(f"{HOST}/d2l/le/lessons/{plan['target']['id']}")
        self.create.confirm.setChecked(False)
        self.styling._cache.clear()
        if save: self.save_state()
        self.render()

    def changed(self):
        """The plan was edited: save it and redraw."""
        self._notice = ""
        self.create.confirm.setChecked(False)
        self.save_state(); self.render()

    def notify(self, message, level="warning"):
        """Show a message in the footer until the next edit or step change."""
        self._notice = message
        self._status.setText(message); self._log.append_log(message, level)

    def resume_step(self):
        if not self._plan: return 0
        if not o.is_outline(self._plan): return LEGACY
        return 4 if self._plan.get("run_signature") else 2

    def go(self, step):
        if step == LEGACY or self._plan and not o.is_outline(self._plan) and step > 0:
            step = LEGACY if self._plan and not o.is_outline(self._plan) else 0
        elif step >= 2 and not (self._plan and o.is_outline(self._plan)): step = 1 if self._scan else 0
        elif step == 1 and not self._scan: step = 0
        if max(0, step) != self._step: self._notice = ""
        self._step = max(0, step)
        self._pages.setCurrentIndex(self._step)
        self.render()

    # ── Rendering ──────────────────────────────────────────────────────
    def render(self):
        if not hasattr(self, "_primary"): return
        step = self._step
        self._subtitle.setText(SUBTITLES[step] if step < len(SUBTITLES) else "This transfer was made with the earlier workshop version.")
        outline = bool(self._plan and o.is_outline(self._plan))
        for index, widget in enumerate(self._step_buttons):
            available = index == 0 or index == 1 and bool(self._scan) or index >= 2 and outline
            widget.setEnabled(available and not self._busy)
            repolish(widget, state="current" if index == step else "done" if index < step and step != LEGACY else "")
        page = {0: self.connect, 1: self.start, 2: self.arrange, 3: self.styling, 4: self.create}.get(step)
        if page is not None: page.render()
        if step == LEGACY: self._render_legacy()
        self.update_actions()

    def _render_legacy(self):
        plan = self._plan or {}
        applied = bool(plan.get("run_signature"))
        self._legacy_text.setText(
            (f"{plan.get('source', {}).get('name', '')} → {plan.get('target', {}).get('name', '')}. " if plan else "")
            + ("It has already been created; you can resume or re-check it here. " if applied else
               "It was never created. Start a new transfer to plan it with the new screen. ")
            + "New transfers use Connect → Starting point → Arrange → Styling → Create.")
        self._legacy_report.setPlainText(plan.get("report") or "")

    def update_actions(self, *_):
        if not hasattr(self, "_primary"): return
        step, plan, busy = self._step, self._plan, self._busy
        self._back.setVisible(0 < step < LEGACY and not busy and not self._creating)
        self._stop_button.setVisible(busy)
        self._secondary.hide()
        primary, enabled = "", True
        if step == 0:
            primary = "Scanning…" if busy else "Scan both courses"
            try:
                same = course_id(self.connect.source.text()) == course_id(self.connect.target.text())
                ready = not same
            except Exception:
                ready = False
            enabled = ready and not busy
            self._status.setText("Both links are needed." if not ready else "Ready to scan. Nothing changes in either course.")
        elif step == 1:
            primary, enabled = "Build outline", bool(self._scan) and not busy
            self._status.setText("You can change the outline completely in the next step.")
        elif step == 2:
            primary = "Continue to styling"
            waiting = o.unplaced(plan) if plan else []
            suggested = [r for r in waiting if r.get("suggested")]
            if suggested and self._editable():
                self._secondary.setText(f"Accept {len(suggested)} suggestion{'s' if len(suggested) != 1 else ''}"); self._secondary.show()
            self._status.setText(f"{len(waiting)} item{'s' if len(waiting) != 1 else ''} still to place or skip." if waiting else
                                 "Every item has a place or is skipped.")
        elif step == 3:
            primary = "Continue to review"
            self._status.setText("Every cleaned page is checked against the original; if anything would be lost, the original is kept.")
        elif step == 4:
            errors = o.validate_outline(plan)[0] if plan else ["No plan"]
            done = bool(plan and plan.get("report") and plan.get("validation")) and all(plan["validation"].values())
            if self._creating:
                primary, enabled = "Creating…", False
                self._status.setText("Working in Brightspace. You can stop after the current step and resume later.")
            elif done:
                primary = "Open in Brightspace"
                self._status.setText("Created and checked.")
            else:
                primary = "Resume transfer" if self._locked() else "Create transfer"
                enabled = not errors and self.create.confirm.isChecked() and not busy
                self._status.setText(f"{len(errors)} thing{'s' if len(errors) != 1 else ''} to fix first." if errors else
                                     "Tick the box to confirm the destination." if not self.create.confirm.isChecked() else "Ready to create.")
        elif step == LEGACY:
            applied = bool(plan and plan.get("run_signature"))
            primary = "Resume / re-check" if applied else "Start a new transfer"
            if applied:
                self._secondary.setText("Start a new transfer"); self._secondary.show()
            self._status.setText("")
        self._primary.setText(primary); self._primary.setEnabled(enabled)
        if self._notice and not busy: self._status.setText(self._notice)

    def _fill_more(self, menu):
        menu.clear()
        menu.addAction(("Hide" if self._log_box.isVisible() else "Show") + " activity log", self._toggle_log)
        menu.addAction("Open Transfer Studio in browser", self.open_studio)
        menu.addAction("Open a saved transfer…", self.open_plan)
        if self._plan:
            menu.addAction("Save a copy…", self._save_copy)
            if o.is_outline(self._plan): menu.addAction("View the plan as text", self._show_text)
            if self._plan.get("report"): menu.addAction("Export report…", self._export_report)
            menu.addAction("Open the destination in Brightspace", self._open_target)
            if o.is_outline(self._plan) and not self._busy:
                menu.addSeparator(); menu.addAction("Start a new transfer", self._start_over)

    def _toggle_log(self):
        self._log_box.setVisible(self._log_box.isHidden())

    # ── Actions ────────────────────────────────────────────────────────
    def _primary_clicked(self):
        step = self._step
        if step == 0: self._scan_clicked()
        elif step == 1: self._build_outline()
        elif step in (2, 3): self.go(step + 1)
        elif step == 4:
            plan = self._plan
            if plan.get("report") and plan.get("validation") and all(plan["validation"].values()): self._open_target()
            else: self._apply_clicked()
        elif step == LEGACY:
            if self._plan and self._plan.get("run_signature"): self._legacy_resume()
            else: self._start_over()

    def _secondary_clicked(self):
        if self._step == 2:
            moved = self.arrange.apply(o.accept_suggestions)
            if moved is not None: self.notify(f"Placed {moved} item{'s' if moved != 1 else ''} where suggested.", "success")
        elif self._step == LEGACY:
            self._start_over()

    def _start_over(self):
        self._plan, self._path, self._scan = None, None, None
        self.save_state(); self.go(0)

    def _build_outline(self):
        if self._plan and o.is_outline(self._plan) and not self._plan.get("run_signature") and \
                self._plan["source"]["id"] == self._scan[0]["id"] and self._plan["target"]["id"] == self._scan[1]["id"]:
            answer = QMessageBox.question(self, "Build the outline again", "This replaces the current outline and its decisions. Continue?")
            if answer != QMessageBox.StandardButton.Yes: return
        source, target = copy.deepcopy(self._scan[0]), copy.deepcopy(self._scan[1])
        try:
            plan = o.build_outline_plan(source, target, self.start.choice(), self.start.selected_pattern())
        except ValueError as exc:
            return self.notify(str(exc))
        self.set_plan(plan)
        waiting = len(o.unplaced(plan))
        self.go(2)
        self.notify(f"Outline ready. {waiting} item{'s' if waiting != 1 else ''} to place or skip." if waiting else "Outline ready. Every item is placed.", "success")

    def use_existing(self, node_id):
        try:
            o.use_existing(self._plan, node_id)
        except (ValueError, KeyError) as exc:
            return self.notify(str(exc))
        self.changed()

    def show_issue(self, issue):
        self.go(2)
        if issue.get("node"): self.arrange.select_node(issue["node"])
        elif issue.get("row"): self.arrange.select_row(issue["row"])
        else: self.arrange.only_waiting.setChecked(True)

    def set_grade_merge(self, on):
        if not self._editable(): return
        self._plan["grade_categories"] = "merge" if on else "keep"
        self.changed()

    def edit_styling(self, mode=None, apply_template=None, template=None, override=None):
        if not self._editable(): return
        styling = self._plan["styling"]
        if mode: styling["mode"] = mode
        if apply_template is not None: styling["apply_template"] = apply_template
        if template is not None: styling["template"] = template
        if override:
            row_id, value = override
            if value: styling["overrides"][row_id] = value
            else: styling["overrides"].pop(row_id, None)
        self._plan["review_acknowledged"] = False
        self.changed()

    # ── Patterns and templates ─────────────────────────────────────────
    def _patterns_path(self):
        return DATA_DIR / "patterns.json"

    def patterns(self, scan=None):
        source, target = scan or (self._plan["source"], self._plan["target"])
        return o.pattern_choices(source, target, o.load_patterns(self._patterns_path()))

    def save_pattern(self, pattern):
        saved = [p for p in o.load_patterns(self._patterns_path()) if o.norm(p["name"]) != o.norm(pattern["name"])]
        o.save_patterns(self._patterns_path(), [pattern, *saved])
        self.notify(f"Saved the pattern “{pattern['name']}”. It's offered for every transfer.", "success")

    def template_choices(self):
        from transfer_styling import template_choices
        key = (self._plan["id"], id(self._plan["target"]))
        if self._templates[0] != key:
            self._templates = (key, template_choices(self._plan["target"], self._plan["source"]))
        return self._templates[1]

    # ── Workers ────────────────────────────────────────────────────────
    def _worker(self, action, kind):
        self._busy = True; self._stop.clear(); self.render()
        if self._studio: self._studio.app_busy = True
        def run():
            try: self._queue.put((kind, asyncio.run(action())))
            except Exception as exc:
                error_kind = "studio_error" if kind == "studio_scanned" else "studio_create_error" if kind == "studio_created" else "error"
                self._queue.put((error_kind, str(exc).split("Call log:")[0].strip()))
        threading.Thread(target=run, daemon=True).start()

    def _worker_log(self, message, level="info"): self._queue.put(("log", (message, level)))

    def _scan_clicked(self):
        if not getattr(self._mw, "chromium_ready", True): return self.notify("The browser engine is still installing.")
        try:
            source_id, target_id = course_id(self.connect.source.text()), course_id(self.connect.target.text())
        except ValueError as exc:
            return self.notify(str(exc))
        if source_id == target_id: return self.notify("Copy from and Copy into must be different courses.")
        from transfer_outline_runner import scan_courses
        credentials, url = self._credentials(), self.connect.source.text()
        self.save_state()
        self._worker(lambda: with_transfer_browser(url, credentials,
            lambda page: scan_courses(page, source_id, target_id, self._worker_log, self._stop), self._worker_log, self._stop), "scanned")

    def _apply_clicked(self):
        from transfer_outline_runner import apply_outline
        self.save_state()
        plan, path, credentials = copy.deepcopy(self._plan), self._path, self._credentials()
        confirmed, url = plan["target"]["id"], self.connect.target.text()
        self._creating = True
        self._worker(lambda: with_transfer_browser(url, credentials,
            lambda page: apply_outline(page, plan, path, confirmed, True, self._worker_log, self._stop),
            self._worker_log, self._stop), "applied")

    def _legacy_resume(self):
        from transfer_runner import apply_transfer
        plan, path, credentials = copy.deepcopy(self._plan), self._path, self._credentials()
        url = self.connect.target.text()
        self._creating = True
        self._worker(lambda: with_transfer_browser(url, credentials,
            lambda page: apply_transfer(page, plan, path, plan["target"]["id"], True, self._worker_log, self._stop),
            self._worker_log, self._stop), "applied")

    def _poll(self):
        try:
            while True:
                kind, value = self._queue.get_nowait()
                if kind == "studio_opened":
                    self._studio_saved_path = str(value)
                    self.save_state()
                    continue
                if kind == "studio_create":
                    if self._studio is None: continue
                    studio = self._studio
                    if self._busy or not getattr(self._mw, "chromium_ready", True):
                        studio.finish_create(error="The app is busy or the browser engine is still installing. Try again when it is ready.")
                        studio.app_busy = self._busy
                        continue
                    from transfer_outline_runner import apply_outline
                    request, credentials = value, self._credentials()
                    async def create_on_page(page, r=request, s=studio):
                        if s.stop_event.is_set(): raise InterruptedError("Stopped before creating. Resume when ready.")
                        return await apply_outline(page, r["plan"], r["path"], r["target_id"], True, s.run_log, s.stop_event)
                    self._creating = True
                    self._worker(lambda r=request, s=studio, c=credentials, a=create_on_page: with_transfer_browser(
                        f"{HOST}/d2l/home/{r['target_id']}", c, a, self._worker_log, s.stop_event), "studio_created")
                    continue
                if kind in ("studio_created", "studio_create_error"):
                    self._busy = self._creating = False
                    if self._studio:
                        self._studio.finish_create(value if kind == "studio_created" else None,
                                                   value if kind == "studio_create_error" else None)
                        if self._path == self._studio.path:
                            self.set_plan(load_plan(self._path), self._path, save=False)
                    self.save_state(); self.render()
                    continue
                if kind == "studio_request":
                    if self._studio is None: continue
                    if self._busy or not getattr(self._mw, "chromium_ready", True):
                        self._studio.finish_scan(error="The app is busy or the browser engine is still installing. Try again when it is ready.")
                        continue
                    from transfer_outline_runner import scan_courses
                    credentials = self._credentials()
                    studio = self._studio
                    # This closure is consumed asynchronously: freeze this request.
                    request = value
                    self._worker(lambda r=request, s=studio, c=credentials: with_transfer_browser(r["url"], c,
                        lambda page: scan_courses(page, r["source_id"], r["target_id"], s.log, self._stop),
                        self._worker_log, self._stop), "studio_scanned")
                    continue
                if kind in ("studio_scanned", "studio_error"):
                    self._busy = False
                    if self._studio:
                        self._studio.app_busy = False
                        self._studio.finish_scan(value if kind == "studio_scanned" else None,
                                                 value if kind == "studio_error" else None)
                        if self._studio.path: self._studio_saved_path = str(self._studio.path)
                    self.save_state(); self.render()
                    continue
                if kind == "log":
                    message, level = value
                    self._log.append_log(message, level)
                    if self._creating: self.create.progress_message(message)
                    continue
                self._busy = False; was_creating = self._creating; self._creating = False
                if self._studio: self._studio.app_busy = False
                if kind == "error":
                    self._log.append_log(value, "error"); self._log_box.show()
                    if was_creating and self._path and self._path.exists():
                        self.set_plan(load_plan(self._path), self._path, save=False)
                    self.render(); self._status.setText(value.splitlines()[0][:220])
                elif kind == "scanned":
                    self._scan = value
                    self._log.append_log("Both courses scanned.", "success")
                    self.go(1)
                elif kind == "applied":
                    self.set_plan(value, self._path, save=False)
                    passed = bool(value.get("validation")) and all(value["validation"].values())
                    self._log.append_log("Transfer created and checked." if passed else "Some checks need attention; see the report.",
                                         "success" if passed else "warning")
                    self.go(LEGACY if not o.is_outline(value) else 4)
        except queue.Empty:
            pass

    # ── Files ──────────────────────────────────────────────────────────
    def open_plan(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open saved transfer", str(DATA_DIR), "JSON (*.json)")
        if not path: return
        try:
            self.set_plan(load_plan(path), Path(path)); self.go(self.resume_step())
        except Exception as exc:
            self.notify(f"That file isn't a saved transfer: {exc}")

    def _save_copy(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save a copy of this transfer", str(self._path), "JSON (*.json)")
        if path: save_plan(self._plan, path); self.notify(f"Saved a copy: {path}", "info")

    def _export_report(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export report", "transfer-report.txt", "Text (*.txt)")
        if path: Path(path).write_text(self._plan["report"], encoding="utf-8")

    def _show_text(self):
        dialog = QDialog(self); dialog.setWindowTitle("Transfer plan"); dialog.resize(760, 640)
        box = QVBoxLayout(dialog); text = QPlainTextEdit(o.preview_text(self._plan)); text.setReadOnly(True)
        text.setObjectName("transfer_text"); box.addWidget(text)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close); buttons.rejected.connect(dialog.reject); box.addWidget(buttons)
        dialog.exec()

    def _open_target(self):
        if self._plan: webbrowser.open(f"{HOST}/d2l/le/lessons/{self._plan['target']['id']}")
