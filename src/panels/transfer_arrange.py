"""Arrange: the source course on the left, the destination outline on the right.

Items move by dragging, or by selecting them and choosing Move to. Actions only
appear for what is selected, so the screen stays quiet until it's needed.
"""
from __future__ import annotations

import json

from PySide6.QtCore import QMimeData, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QDrag, QIcon
from PySide6.QtWidgets import (QAbstractItemView, QDialog, QDialogButtonBox, QFrame, QHBoxLayout, QInputDialog, QLabel,
                               QLineEdit, QMenu, QPlainTextEdit, QPushButton, QSplitter, QTreeWidget, QTreeWidgetItem,
                               QVBoxLayout, QWidget, QCheckBox, QMessageBox)

import transfer_outline as o
from gui_tool_icons import tool_pixmap
from panels.transfer_styles import palette

MIME = "application/x-bpa-transfer"
ROLE = Qt.ItemDataRole.UserRole
ICONS = {"module": "folder", "page": "file", "file": "file", "link": "link", "placeholder": "file-plus",
         "template": "file-pencil", "assignment": "book", "quiz": "book", "discussion": "book", "survey": "book"}


def label(text, role="muted", wrap=True):
    result = QLabel(text); result.setProperty("role", role); result.setWordWrap(wrap)
    result.setTextFormat(Qt.TextFormat.PlainText); result.setMinimumWidth(0)
    return result


def button(text, handler=None, variant=""):
    result = QPushButton(text); result.setProperty("variant", variant)
    result.setCursor(Qt.CursorShape.PointingHandCursor)
    if handler: result.clicked.connect(lambda _=False: handler())
    return result


def icon(kind):
    return QIcon(tool_pixmap(ICONS.get(kind, "file"), 18, palette()["text"]))


class SourceTree(QTreeWidget):
    """Read-only source course. Drags carry rows and folders."""
    def __init__(self):
        super().__init__()
        self.setHeaderHidden(True); self.setColumnCount(2); self.setIndentation(18); self.setIconSize(QSize(18, 18))
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setDragEnabled(True); self.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setAccessibleName("Source course")

    def payload(self):
        return [item.data(0, ROLE) for item in self.selectedItems() if item.data(0, ROLE)]

    def startDrag(self, actions):
        payload = [p for p in self.payload() if p[0] in ("row", "folder")]
        if not payload: return
        data = QMimeData(); data.setData(MIME, json.dumps(payload).encode())
        drag = QDrag(self); drag.setMimeData(data); drag.exec(Qt.DropAction.CopyAction)


class OutlineTree(QTreeWidget):
    """The destination outline. Accepts rows/folders from the source and moves its own nodes."""
    dropped = Signal(list, object, object)   # payload, parent node id (None = top level), index (None = end)

    def __init__(self):
        super().__init__()
        self.setHeaderHidden(True); self.setColumnCount(2); self.setIndentation(18); self.setIconSize(QSize(18, 18))
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setDragEnabled(True); self.setAcceptDrops(True); self.setDropIndicatorShown(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked | QAbstractItemView.EditTrigger.EditKeyPressed)
        self.setAccessibleName("Destination outline")

    def startDrag(self, actions):
        item = self.currentItem()
        if not item or not item.data(0, ROLE): return
        data = QMimeData(); data.setData(MIME, json.dumps([item.data(0, ROLE)]).encode())
        drag = QDrag(self); drag.setMimeData(data); drag.exec(Qt.DropAction.MoveAction)

    def dragEnterEvent(self, event):
        if event.mimeData().hasFormat(MIME): event.acceptProposedAction()
        else: event.ignore()

    def dragMoveEvent(self, event):
        if event.mimeData().hasFormat(MIME):
            super().dragMoveEvent(event); event.acceptProposedAction()
        else: event.ignore()

    def drop_target(self, item, position):
        """(parent node id, index) for a drop on `item` at the indicator position."""
        if item is None: return None, None
        kind, node_id = item.data(0, ROLE)[0], item.data(0, ROLE)[1]
        parent = item.parent()
        parent_id = parent.data(0, ROLE)[1] if parent else None
        index = (parent.indexOfChild(item) if parent else self.indexOfTopLevelItem(item))
        if position == QAbstractItemView.DropIndicatorPosition.OnItem and item.data(0, ROLE)[2] == "module":
            return node_id, None
        if position == QAbstractItemView.DropIndicatorPosition.BelowItem or position == QAbstractItemView.DropIndicatorPosition.OnItem:
            index += 1
        return parent_id, index

    def dropEvent(self, event):
        if not event.mimeData().hasFormat(MIME): return event.ignore()
        payload = json.loads(bytes(event.mimeData().data(MIME)).decode())
        parent_id, index = self.drop_target(self.itemAt(event.position().toPoint()), self.dropIndicatorPosition())
        event.setDropAction(Qt.DropAction.CopyAction); event.accept()
        self.dropped.emit(payload, parent_id, index)


class TextDialog(QDialog):
    """Introduction / Session Overview text for a template page."""
    def __init__(self, node, parent=None):
        super().__init__(parent)
        self.setWindowTitle(node["title"]); self.setMinimumWidth(520)
        box = QVBoxLayout(self); box.setSpacing(8)
        text = node.get("text", {})
        if node["template"] == "introduction":
            box.addWidget(QLabel("Introduction")); self.intro = QPlainTextEdit(text.get("introduction", "")); box.addWidget(self.intro)
            box.addWidget(QLabel("Learning outcomes (one per line)"))
            self.outcomes = QPlainTextEdit("\n".join(text.get("outcomes", []))); box.addWidget(self.outcomes)
            self.session = None
        else:
            box.addWidget(QLabel("Session plan")); self.session = QPlainTextEdit(text.get("session", "")); box.addWidget(self.session)
            self.intro = self.outcomes = None
        box.addWidget(QLabel("Leave blank to create a visible “to be supplied” note."))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject); box.addWidget(buttons)

    def values(self):
        if self.session is not None: return {"session": self.session.toPlainText()}
        return {"introduction": self.intro.toPlainText(), "outcomes": self.outcomes.toPlainText().splitlines()}


class ArrangePage(QWidget):
    def __init__(self, panel):
        super().__init__()
        self.panel = panel
        self._expanded_source, self._expanded_outline = set(), None
        root = QVBoxLayout(self); root.setContentsMargins(30, 6, 30, 12); root.setSpacing(10)
        self.split = QSplitter(Qt.Orientation.Horizontal); self.split.setChildrenCollapsible(False); self.split.setHandleWidth(14)
        # Source pane
        left = QFrame(); left.setProperty("role", "pane"); lbox = QVBoxLayout(left); lbox.setContentsMargins(14, 12, 14, 10); lbox.setSpacing(8)
        head = QHBoxLayout(); self.source_title = label("From the source", "pane-title", wrap=False); head.addWidget(self.source_title, 1)
        self.only_waiting = QCheckBox("Only items to place"); self.only_waiting.toggled.connect(self.render_source); head.addWidget(self.only_waiting)
        lbox.addLayout(head)
        self.source = SourceTree(); self.source.itemSelectionChanged.connect(self.source_selected); lbox.addWidget(self.source, 1)
        self.source_empty = label("Nothing left to place. Untick “Only items to place” to see everything.", "muted")
        lbox.addWidget(self.source_empty); lbox.addStretch()
        self.source.customContextMenuRequested.connect(lambda pos: self.source_menu().exec(self.source.viewport().mapToGlobal(pos)))
        self.source.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.source_bar = QWidget(); sbar = QHBoxLayout(self.source_bar); sbar.setContentsMargins(0, 4, 0, 0); sbar.setSpacing(6)
        self.source_hint = label("", "muted"); sbar.addWidget(self.source_hint, 1)
        self.move_button = button("Move to…", None, "small"); self.move_button.setMenu(QMenu(self.move_button))
        self.move_button.menu().aboutToShow.connect(lambda: self.fill_move_menu(self.move_button.menu()))
        self.skip_button = button("Skip", self.skip_selected, "small")
        self.split_button = button("Keep page whole", self.join_selected, "small")
        for widget in (self.move_button, self.skip_button, self.split_button): sbar.addWidget(widget)
        lbox.addWidget(self.source_bar)
        # Destination pane
        right = QFrame(); right.setProperty("role", "pane"); rbox = QVBoxLayout(right); rbox.setContentsMargins(14, 12, 14, 10); rbox.setSpacing(8)
        head = QHBoxLayout(); self.target_title = label("Into the destination", "pane-title", wrap=False); head.addWidget(self.target_title, 1)
        self.add_module_button = button("+ Module", self.add_top_module, "small"); head.addWidget(self.add_module_button)
        rbox.addLayout(head)
        self.outline = OutlineTree(); self.outline.itemSelectionChanged.connect(self.outline_selected)
        self.outline.dropped.connect(self.dropped); self.outline.itemChanged.connect(self.renamed)
        self.outline.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.outline.customContextMenuRequested.connect(lambda pos: self.outline_menu().exec(self.outline.viewport().mapToGlobal(pos)))
        rbox.addWidget(self.outline, 1)
        self.empty_hint = label("Add a module, then drag items here from the left.", "muted"); rbox.addWidget(self.empty_hint)
        self.outline_bar = QWidget(); obar = QHBoxLayout(self.outline_bar); obar.setContentsMargins(0, 4, 0, 0); obar.setSpacing(6)
        self.outline_hint = label("", "muted"); obar.addWidget(self.outline_hint, 1)
        self.text_button = button("Edit text", self.edit_text, "small")
        self.add_button = button("Add", None, "small"); self.add_button.setMenu(QMenu(self.add_button))
        self.add_button.menu().aboutToShow.connect(lambda: self.fill_add_menu(self.add_button.menu()))
        self.more_button = button("More", None, "small"); self.more_button.setMenu(QMenu(self.more_button))
        self.more_button.menu().aboutToShow.connect(lambda: self.fill_more_menu(self.more_button.menu()))
        for widget in (self.text_button, self.add_button, self.more_button): obar.addWidget(widget)
        rbox.addWidget(self.outline_bar)
        self.split.addWidget(left); self.split.addWidget(right); self.split.setSizes([520, 620])
        root.addWidget(self.split, 1)

    # ── Rendering ──────────────────────────────────────────────────────
    @property
    def plan(self):
        return self.panel._plan

    def editable(self):
        return self.panel._editable()

    def render(self):
        if not self.plan: return
        self.source_title.setText("From " + self.plan["source"]["name"])
        self.target_title.setText("Into " + self.plan["target"]["name"])
        self.render_source(); self.render_outline()
        locked = not self.editable()
        self.add_module_button.setVisible(not locked)
        self.outline.setDragDropMode(QAbstractItemView.DragDropMode.NoDragDrop if locked else QAbstractItemView.DragDropMode.DragDrop)

    def _caption(self, item, text, tone=None):
        c = palette()
        item.setText(1, text)
        item.setForeground(1, QBrush(QColor(c["warning"] if tone == "warn" else c["muted"])))
        item.setTextAlignment(1, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

    def render_source(self, *_):
        if not self.plan: return
        selected = {tuple(p) for p in self.source.payload()}
        expanded = {item.data(0, ROLE)[1] for item in self._all(self.source) if item.isExpanded()} or None
        self.source.blockSignals(True); self.source.clear()
        placed, skipped = o.placements(self.plan), set(self.plan["skipped"])
        waiting = {r["id"] for r in o.unplaced(self.plan)}
        titles = {}
        for node, _, _ in o.walk(self.plan["outline"]): titles[node["id"]] = node.get("title", "")
        rows_by_source = {}
        for row in self.plan["items"].values(): rows_by_source.setdefault(row["source_id"], []).append(row)
        only = self.only_waiting.isChecked()

        def add(entries, parent, top=None):
            count = 0
            for obj in entries:
                if obj.get("Type") == 0:
                    item = QTreeWidgetItem([obj["Title"], ""]); item.setIcon(0, icon("module"))
                    item.setData(0, ROLE, ["folder", str(obj["Id"]), "module"])
                    (parent.addChild(item) if parent else self.source.addTopLevelItem(item))
                    inner = add(obj.get("children", []), item, top or str(obj["Id"]))
                    count += inner
                    if inner: self._caption(item, f"{inner} to place", "warn")
                    if only and not inner: (parent.removeChild(item) if parent else self.source.takeTopLevelItem(self.source.indexOfTopLevelItem(item)))
                    elif expanded is None and not parent or expanded and str(obj["Id"]) in expanded: item.setExpanded(True)
                    continue
                for row in rows_by_source.get(str(obj["Id"]), []):
                    if only and row["id"] not in waiting: continue
                    item = QTreeWidgetItem([row["title"], ""]); item.setIcon(0, icon(row["kind"]))
                    item.setData(0, ROLE, ["row", row["id"], row["kind"]])
                    item.setToolTip(0, f"{o.KIND_NAMES.get(row['kind'], row['kind'])} · {' / '.join(row['path'])}")
                    if row["id"] in placed:
                        holder = placed[row["id"]][0]
                        self._caption(item, titles.get(holder, ""))
                        item.setToolTip(1, "Goes to " + " / ".join(o.module_path(self.plan, holder)))
                    elif row["id"] in skipped:
                        self._caption(item, "Manual" if row["method"] == "manual" else "Skipped")
                        item.setForeground(0, QBrush(QColor(palette()["muted"])))
                    else:
                        count += 1
                        self._caption(item, ("→ " + titles[row["suggested"]]) if row.get("suggested") in titles else "To place", "warn")
                    (parent.addChild(item) if parent else self.source.addTopLevelItem(item))
                    if tuple(item.data(0, ROLE)) in selected: item.setSelected(True)
            return count
        add(self.plan["source"]["modules"], None)
        self.source_empty.setVisible(not self.source.topLevelItemCount())
        self.source.setVisible(bool(self.source.topLevelItemCount()))
        self.source.resizeColumnToContents(1)
        self.source.header().setStretchLastSection(False)
        self.source.header().setSectionResizeMode(0, self.source.header().ResizeMode.Stretch)
        self.source.blockSignals(False)
        self.source_selected()

    def render_outline(self):
        if not self.plan: return
        current = self.selected_node()
        expanded = {item.data(0, ROLE)[1] for item in self._all(self.outline) if item.isExpanded()}
        first = not self.outline.topLevelItemCount()
        self.outline.blockSignals(True); self.outline.clear()
        editable = self.editable()

        def add(nodes, parent):
            for node in nodes:
                if node["kind"] == "item":
                    row = self.plan["items"].get(node["item"], {"title": "(missing)", "kind": "other", "method": "content"})
                    item = QTreeWidgetItem([row["title"], ""]); item.setIcon(0, icon(row["kind"]))
                    self._caption(item, o.KIND_NAMES.get(row["kind"], ""))
                    if row["method"] == "components": item.setToolTip(0, "Copied with Brightspace's Copy Components, then linked here")
                    elif row.get("repaired_from"): item.setToolTip(0, "Title taken from the page's heading. In the source: " + row["repaired_from"])
                    if editable and row["method"] == "content": item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
                else:
                    item = QTreeWidgetItem([node["title"], ""]); item.setIcon(0, icon(node["kind"]))
                    if node["kind"] == "module" and node.get("existing_id"): self._caption(item, "Already there")
                    elif node["kind"] == "placeholder": self._caption(item, "Placeholder page")
                    elif node["kind"] == "template":
                        text = node.get("text", {})
                        filled = text.get("session") if node["template"] == "session" else text.get("introduction")
                        self._caption(item, "Template page" if filled else "Needs text", None if filled else "warn")
                    if editable and not node.get("existing_id"):
                        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
                item.setData(0, ROLE, ["node", node["id"], node["kind"]])
                (parent.addChild(item) if parent else self.outline.addTopLevelItem(item))
                if node["kind"] == "module":
                    add(node["children"], item)
                    item.setExpanded(first or node["id"] in expanded)
                if node["id"] == current: self.outline.setCurrentItem(item)
        add(self.plan["outline"], None)
        self.outline.header().setStretchLastSection(False)
        self.outline.header().setSectionResizeMode(0, self.outline.header().ResizeMode.Stretch)
        self.outline.resizeColumnToContents(1)
        self.outline.blockSignals(False)
        self.empty_hint.setVisible(not self.plan["outline"])
        self.outline_selected()

    def _all(self, tree):
        stack = [tree.topLevelItem(i) for i in range(tree.topLevelItemCount())]
        while stack:
            item = stack.pop(); yield item
            stack.extend(item.child(i) for i in range(item.childCount()))

    # ── Selection and actions ──────────────────────────────────────────
    def selected_rows(self):
        return [p[1] for p in self.source.payload() if p[0] == "row"]

    def selected_folder(self):
        folders = [p[1] for p in self.source.payload() if p[0] == "folder"]
        return folders[0] if len(folders) == 1 and not self.selected_rows() else None

    def selected_node(self):
        item = self.outline.currentItem()
        return item.data(0, ROLE)[1] if item and item.isSelected() and item.data(0, ROLE) else None

    def node(self, node_id=None):
        node_id = node_id or self.selected_node()
        try: return o.find(self.plan, node_id)[0] if node_id else None
        except KeyError: return None

    def source_selected(self):
        rows, folder = self.selected_rows(), self.selected_folder()
        editable = self.editable()
        self.source_bar.setVisible(bool(rows or folder) and editable)
        if not (rows or folder): return
        items = [self.plan["items"][r] for r in rows if r in self.plan["items"]]
        all_skipped = items and all(r["id"] in self.plan["skipped"] for r in items)
        manual = [r for r in items if r["method"] == "manual"]
        self.skip_button.setText("Include" if all_skipped and not manual else "Skip")
        self.skip_button.setVisible(bool(items) and not (all_skipped and manual))
        self.split_button.setVisible(len(items) == 1 and bool(items[0].get("piece")))
        self.move_button.setText("Copy folder to…" if folder else "Move to…")
        self.move_button.setVisible(not manual)
        if manual:
            self.source_hint.setText("The app can't copy this kind of activity. Add it in Brightspace after the transfer.")
        elif len(items) == 1 and items[0].get("reason") and items[0]["id"] not in o.placements(self.plan):
            self.source_hint.setText(items[0]["reason"])
        elif len(items) == 1 and items[0]["method"] == "components":
            self.source_hint.setText("Copied with its settings, rubric and grade item.")
        else:
            self.source_hint.setText(f"{len(items)} selected" if len(items) > 1 else "")

    def outline_selected(self):
        node = self.node()
        self.outline_bar.setVisible(bool(node) and self.editable())
        if not node: return
        self.text_button.setVisible(node["kind"] == "template")
        self.add_button.setVisible(node["kind"] == "module")
        hints = {"placeholder": node.get("message", ""), "template": "Made from the destination's page template.",
                 "item": "Drag to reorder, or drag to another module."}
        hint = "Double-click to rename." if node["kind"] == "module" and not node.get("existing_id") else \
            "Already in the destination; new items are added after what's there." if node.get("existing_id") else             "Double-click to rename. Drag to reorder or move." if node["kind"] == "item" and self.plan["items"].get(node["item"], {}).get("method") == "content" else             hints.get(node["kind"], "")
        self.outline_hint.setText(hint)

    def destinations(self):
        """[(label, node id)] for every module, indented by depth."""
        result = []
        def walk(nodes, depth):
            for node in nodes:
                if node["kind"] == "module":
                    result.append(("    " * depth + node["title"], node["id"])); walk(node["children"], depth + 1)
        walk(self.plan["outline"], 0)
        return result

    def fill_move_menu(self, menu):
        menu.clear()
        folder = self.selected_folder()
        if folder:
            menu.addAction("Top level (new module)", lambda: self.apply(o.place_source_module, folder, None))
            menu.addSeparator()
            for text, node_id in self.destinations():
                menu.addAction(text, lambda n=node_id: self.apply(o.place_source_module, folder, n))
            return
        rows = self.selected_rows()
        suggested = {self.plan["items"][r].get("suggested") for r in rows if r in self.plan["items"]}
        if len(suggested) == 1 and None not in suggested:
            target = suggested.pop()
            menu.addAction("Suggested: " + self.node(target)["title"], lambda: self.apply(o.place, rows, target))
            menu.addSeparator()
        for text, node_id in self.destinations():
            menu.addAction(text, lambda n=node_id: self.apply(o.place, rows, n))
        if not self.plan["outline"]:
            menu.addAction("Add a module first").setEnabled(False)

    def source_menu(self):
        menu = QMenu(self)
        if not self.editable() or not self.source.payload(): return menu
        move = menu.addMenu(self.move_button.text()); self.fill_move_menu(move)
        if self.selected_rows() and self.skip_button.isVisible():
            menu.addAction(self.skip_button.text(), self.skip_selected)
        if self.split_button.isVisible(): menu.addAction("Keep page whole", self.join_selected)
        return menu

    def skip_selected(self):
        rows = self.selected_rows()
        if self.skip_button.text() == "Include": self.apply(o.unplace, rows)
        else: self.apply(o.skip, rows)

    def join_selected(self):
        rows = self.selected_rows()
        if rows: self.apply(o.join_page, self.plan["items"][rows[0]]["source_id"])

    def fill_add_menu(self, menu):
        menu.clear()
        node = self.node()
        if not node or node["kind"] != "module": return
        menu.addAction("Submodule", lambda: self.add_child_module(node["id"]))
        menu.addAction("Placeholder page", lambda: self.add_page(node["id"], "placeholder"))
        menu.addAction("Introduction page (from template)", lambda: self.add_page(node["id"], "introduction"))
        menu.addAction("Session Overview page (from template)", lambda: self.add_page(node["id"], "session"))

    def fill_more_menu(self, menu):
        menu.clear()
        node = self.node()
        if not node: return
        if node["kind"] == "module":
            patterns = menu.addMenu("Apply a pattern")
            for pattern in self.panel.patterns():
                patterns.addAction(pattern["name"], lambda p=pattern: self.apply(o.apply_pattern, node["id"], p))
            menu.addAction("Save this layout as a pattern…", lambda: self.save_pattern(node["id"]))
            sources = menu.addMenu("Suggest items from")
            chosen = set(node.get("sources", []))
            for module in [m for m in self.plan["source"]["modules"] if m.get("Type") == 0]:
                action = sources.addAction(module["Title"]); action.setCheckable(True); action.setChecked(str(module["Id"]) in chosen)
                action.toggled.connect(lambda on, m=str(module["Id"]): self.apply(
                    o.set_sources, node["id"], sorted((chosen | {m}) if on else (chosen - {m}))))
            if sum(1 for _ in self.destinations()) and node.get("sources"):
                menu.addAction("Accept suggestions for this module", lambda: self.apply(
                    o.accept_suggestions, [r["id"] for r in o.unplaced(self.plan) if r["top"] in node["sources"]]))
            menu.addSeparator()
        if not node.get("existing_id") and node["kind"] != "item":
            menu.addAction("Rename", lambda: self.outline.editItem(self.outline.currentItem(), 0))
        menu.addAction("Remove from the outline" if node["kind"] != "item" else "Take out (back to the left)", lambda: self.remove(node))

    def outline_menu(self):
        menu = QMenu(self)
        node = self.node()
        if not node or not self.editable(): return menu
        if node["kind"] == "module":
            self.fill_add_menu(menu.addMenu("Add"))
        if node["kind"] == "template": menu.addAction("Edit text…", self.edit_text)
        self.fill_more_menu(menu)
        return menu

    # ── Edits ──────────────────────────────────────────────────────────
    def apply(self, action, *args):
        try:
            result = action(self.plan, *args)
        except (ValueError, KeyError) as exc:
            self.panel.notify(str(exc)); return None
        self.panel.changed()
        return result

    def dropped(self, payload, parent_id, index):
        if not self.editable(): return
        rows = [p[1] for p in payload if p[0] == "row"]
        folders = [p[1] for p in payload if p[0] == "folder"]
        nodes = [p[1] for p in payload if p[0] == "node"]
        if rows:
            if parent_id is None: return self.panel.notify("Drop items into a module.")
            self.apply(o.place, rows, parent_id, index)
        for folder in folders:
            self.apply(o.place_source_module, folder, parent_id)
        for node_id in nodes:
            if node_id == parent_id: continue
            self.apply(o.move, node_id, parent_id, index)

    def renamed(self, item, column):
        if column or not item.data(0, ROLE): return
        node, text = self.node(item.data(0, ROLE)[1]), item.text(0)
        current = self.plan["items"][node["item"]]["title"] if node and node["kind"] == "item" else (node or {}).get("title")
        if not node or text == current: return
        try:
            o.rename(self.plan, node["id"], text)
        except ValueError as exc:
            self.panel.notify(str(exc))
            QTimer.singleShot(0, self.render_outline)   # put the old name back
            return
        QTimer.singleShot(0, self.panel.changed)        # redraw after Qt finishes with the edited item

    def add_top_module(self):
        node = self.apply(o.add_module, None, "New module")
        if node: self.select_node(node["id"], edit=True)

    def add_child_module(self, parent_id):
        node = self.apply(o.add_module, parent_id, "New submodule")
        if node: self.select_node(node["id"], edit=True)

    def add_page(self, parent_id, kind):
        title = {"placeholder": "New page", "introduction": "Module Introduction", "session": "Session Overview"}[kind]
        node = self.apply(o.add_page, parent_id, kind, title)
        if node: self.select_node(node["id"], edit=kind == "placeholder")

    def edit_text(self):
        node = self.node()
        if not node or node["kind"] != "template": return
        dialog = TextDialog(node, self)
        if dialog.exec(): self.apply(o.set_text, node["id"], **dialog.values())

    def remove(self, node):
        if node["kind"] == "module" and any(n["kind"] == "item" for n, _, _ in o.walk(node["children"])):
            answer = QMessageBox.question(self, "Remove module", f"Remove {node['title']}? Its items go back to the left to be placed again.")
            if answer != QMessageBox.StandardButton.Yes: return
        if node["kind"] == "item": self.apply(o.unplace, [node["item"]])
        else: self.apply(o.remove, node["id"])

    def save_pattern(self, node_id):
        name, ok = QInputDialog.getText(self, "Save as pattern", "Pattern name:", QLineEdit.EchoMode.Normal, self.node(node_id)["title"] + " layout")
        if not ok: return
        try:
            self.panel.save_pattern(o.pattern_from_module(self.plan, node_id, name))
        except ValueError as exc:
            self.panel.notify(str(exc))

    def select_node(self, node_id, edit=False):
        for item in self._all(self.outline):
            if item.data(0, ROLE) and item.data(0, ROLE)[1] == node_id:
                self.outline.setCurrentItem(item); self.outline.scrollToItem(item)
                if edit and item.flags() & Qt.ItemFlag.ItemIsEditable: self.outline.editItem(item, 0)
                return True
        return False

    def select_row(self, row_id):
        self.only_waiting.setChecked(False)
        for item in self._all(self.source):
            if item.data(0, ROLE) and item.data(0, ROLE)[:2] == ["row", row_id]:
                self.source.clearSelection(); item.setSelected(True); self.source.setCurrentItem(item)
                self.source.scrollToItem(item); return True
        return False
