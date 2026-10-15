"use strict";
// The launch secret has already been exchanged for an HttpOnly session cookie.
history.replaceState(null, "", "/");
const el = (id) => document.getElementById(id);
let state, view = "connect", onlyWaiting = false, scanPending = false, submitting = false;
let pollTimer;
let editing = false, selectionSide = null, selection = new Set(), anchorId = null, areaMode = false;
let dragPayload = null, dialogAction = null, menuAnchor = null, toastTimer, renderedRevision = "";
let errorScope = "connect", lastServerError = null;
let skipConfirmation = null;
const kindNames = {module: "Folder", page: "Page", file: "File", link: "Link", assignment: "Assignment", quiz: "Quiz", discussion: "Discussion", survey: "Survey", template: "Page from template", placeholder: "Placeholder page", tool: "External tool", other: "Other activity", checklist: "Checklist", "self-assessment": "Self assessment"};

function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}
function showError(message, scope = view) {
  errorScope = scope;
  el("error").textContent = message || "";
  el("error").hidden = !message || scope !== view;
}
async function api(path, body) {
  const options = {credentials: "same-origin", cache: "no-store"};
  if (body) Object.assign(options, {method: "POST", headers: {"Content-Type": "application/json", "X-Studio-Request": "1"}, body: JSON.stringify(body)});
  const response = await fetch(path, options);
  const data = await response.json();
  if (!response.ok) {const error = new Error(data.error || "The request could not be completed."); error.field = data.field; error.scope = data.scope; error.confirmation = data.confirmation; throw error;}
  return data;
}
function setView(next, focus = true) {
  closeMenu();
  view = next;
  for (const step of ["connect", "arrange", "styling", "review", "create"]) el(step).hidden = next !== step;
  el("back").hidden = next !== "arrange";
  for (const step of ["connect", "arrange", "styling", "review", "create"]) {
    if (next === step) el(step + "-step").setAttribute("aria-current", "step");
    else el(step + "-step").removeAttribute("aria-current");
  }
  el("error").hidden = !el("error").textContent || errorScope !== view;
  renderWorkflow(true);
  if (focus) {
    const heading = el(next).querySelector("h1");
    heading.tabIndex = -1;
    heading.focus();
  }
}
function patternDetail() {
  const pattern = state?.patterns.find((p) => p.name === el("pattern").value);
  el("pattern-detail").textContent = pattern ? pattern.sections.join(" · ") : "";
  el("pattern-field").hidden = document.querySelector("input[name=start]:checked").value !== "pattern";
}
function hasWaiting(node) {
  return node.status === "Unplaced" || node.children.some(hasWaiting);
}
function renderTree(target, nodes, source) {
  const side = source ? "source" : "destination";
  const collapsed = new Set([...target.querySelectorAll("details:not([open])")].map((folder) => folder.dataset.id));
  const scroll = target.scrollTop;
  target.replaceChildren();
  function render(entries, container, parent = null) {
    for (const [index, node] of entries.entries()) {
      if (source && onlyWaiting && !hasWaiting(node)) continue;
      const position = {parent, before: node.id, after: entries[index + 1]?.id || null};
      if (!source) container.append(dropSlot(parent, node.id));
      if (node.kind === "module") {
        const folder = element("details");
        folder.dataset.id = node.id;
        folder.open = !collapsed.has(node.id);
        const summary = element("summary");
        summary.append(element("span", "", "disclosure"), element("span", node.title, "node-label"));
        wireRow(summary, node, side, position);
        if (source && node.status) {
          summary.classList.toggle("skipped-item", node.status === "Skipped");
          const label = node.status === "Skipped" ? node.unit_skipped ? "Skipped" : "All items skipped" : `${node.skipped_count} of ${node.item_count} skipped`;
          summary.append(element("span", label, "status skipped"));
        }
        if (!source) summary.append(element("span", node.existing ? "Existing" : "New", "node-badge"));
        const children = element("div", undefined, "children");
        render(node.children, children, node.id);
        if (!children.childElementCount) children.append(element("p", source ? "No items to show." : "No new items in this module.", "empty"));
        folder.append(summary, children);
        container.append(folder);
      } else {
        const row = element("div", undefined, "row");
        const checkbox = element("input", undefined, "select-check");
        checkbox.type = "checkbox";
        checkbox.setAttribute("aria-label", "Select " + node.title);
        checkbox.addEventListener("click", (event) => event.stopPropagation());
        checkbox.addEventListener("change", (event) => selectRow(row, event, true));
        row.append(checkbox);
        wireRow(row, node, side, position);
        row.classList.toggle("skipped-item", source && node.status === "Skipped");
        const text = element("div", node.title, "title");
        text.append(element("span", kindNames[node.kind] || node.kind, "kind"));
        row.append(text);
        if (node.status) row.append(element("span", node.status, "status " + node.status.toLowerCase()));
        if (node.suggestion) row.title = node.suggestion;
        container.append(row);
      }
    }
    if (!source && entries.length) container.append(dropSlot(parent, null));
  }
  render(nodes, target);
  if (!target.childElementCount) target.append(element("p", source ? "Every item has a place or is skipped." : "The outline is empty. Add a module, or drag a source folder here.", "empty"));
  target.scrollTop = scroll;
}
function renderPlan() {
  const plan = state.plan;
  el("course-pair").replaceChildren();
  if (!plan) return;
  if (renderedRevision && !renderedRevision.startsWith(plan.id + ":")) {selection.clear(); anchorId = null; setAreaMode(false);}
  el("course-pair").append(element("span", plan.source.name, "badge"), element("span", "→"), element("span", plan.target.name, "badge"));
  el("source-title").textContent = plan.source.name;
  el("target-title").textContent = plan.target.name;
  el("existing-note").hidden = plan.start !== "destination";
  el("waiting-filter").textContent = "Only unplaced · " + plan.counts.unplaced;
  el("waiting-filter").setAttribute("aria-pressed", String(onlyWaiting));
  renderTree(el("source-tree"), plan.source_nodes, true);
  renderTree(el("destination-tree"), plan.outline, false);
  const available = new Set([...document.querySelectorAll(`.selectable[data-side="${selectionSide}"]`)].map((row) => row.dataset.id));
  selection = new Set([...selection].filter((id) => available.has(id)));
  updateSelection();
  renderedRevision = plan.id + ":" + state.revision;
  el("counts").replaceChildren();
  const labels = {modules: "New modules and folders", pages: "Pages", "files and links": "Files and links", activities: "Activities", skipped: "Skipped", unplaced: "Still to place"};
  for (const [key, label] of Object.entries(labels)) {
    const row = element("div");
    row.append(element("dt", label), element("dd", String(plan.counts[key])));
    el("counts").append(row);
  }
  el("issues").replaceChildren();
  for (const issue of plan.issues) {
    const card = element("div", undefined, "issue " + issue.level);
    card.append(element("p", issue.level === "error" ? "Needs attention" : "For your review", "issue-label"), element("p", issue.text));
    if (issue.fix === "use_existing" && canEdit()) {
      const fix = element("button", "Add into the existing module");
      fix.addEventListener("click", () => mutate("use_existing", {}, [{side: "destination", id: issue.node}]));
      card.append(fix);
    }
    el("issues").append(card);
  }
  if (!plan.issues.length) el("issues").append(element("div", "Every item has a place or is skipped. No outline problems found.", "issue"));
}
function renderState(initial = false) {
  if (lastServerError !== state.error) {lastServerError = state.error; showError(state.error, state.error_scope || "connect");}
  const scanning = state.scanning || submitting;
  el("scan").disabled = scanning;
  el("scan").textContent = scanning ? "Scanning…" : "Scan both courses";
  for (const input of el("connect-form").querySelectorAll("input,select")) input.disabled = scanning;
  el("progress").hidden = !scanning;
  el("progress-text").textContent = state.messages.at(-1) || "Reading both courses…";
  el("connect-step").disabled = scanning;
  el("arrange-step").disabled = scanning || !state.plan;
  const selected = el("pattern").value;
  el("pattern").replaceChildren(...state.patterns.map((p) => {
    const option = element("option", p.name); option.value = p.name; return option;
  }));
  if (state.patterns.some((p) => p.name === selected)) el("pattern").value = selected;
  patternDetail();
  if (initial) {
    el("source-url").value = state.source_url;
    el("target-url").value = state.target_url;
    if (state.plan) {
      document.querySelector(`input[name=start][value="${state.plan.start}"]`).checked = true;
      if (state.plan.pattern) el("pattern").value = state.plan.pattern;
      patternDetail();
      renderPlan();
      setView(state.plan.locked || state.run.active ? "create" : "arrange", false);
    }
  }
  if (scanPending && !state.scanning && !submitting) {
    scanPending = false;
    if (!state.error && state.plan) {renderPlan(); setView("arrange");}
  }
  if (state.plan && renderedRevision !== state.plan.id + ":" + state.revision) renderPlan();
  updateSelection();
  renderWorkflow();
  el("footer-status").textContent = scanning ? "Scanning only reads. Keep the desktop app open." : state.run.active ? "Working in Brightspace · Keep the desktop app open" : state.plan?.locked ? state.run.status === "complete" ? "Created and checked · New content is hidden in the destination" : "Transfer journal saved · Resume the same plan when ready" : state.plan ? "Draft saved automatically · Nothing changed in Brightspace" : "Local preview · Keep the desktop app open";
}
async function refresh(initial = false) {
  clearTimeout(pollTimer);
  try {
    state = await api("/api/state");
    renderState(initial);
    pollTimer = setTimeout(() => refresh(), state.scanning || state.run.active ? 700 : 3000);
  } catch (error) {
    showError(error.message === "Failed to fetch" ? "The desktop app is no longer connected. Reopen Plan transfer in the app." : error.message);
    el("scan").disabled = true;
    el("footer-status").textContent = "Desktop app disconnected";
  }
}
el("connect-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (submitting || state?.scanning) return;
  clearTimeout(pollTimer);
  submitting = true;
  showError("");
  for (const field of ["source-url", "target-url"]) {el(field).removeAttribute("aria-invalid"); el(field + "-error").hidden = true;}
  const body = {source_url: el("source-url").value, target_url: el("target-url").value, start: document.querySelector("input[name=start]:checked").value, pattern: el("pattern").value};
  renderState();
  try {
    await api("/api/scan", body);
    submitting = false;
    scanPending = true;
    await refresh();
  } catch (error) {
    submitting = false;
    renderState();
    showError(error.message, "connect");
    if (error.field) {const id = error.field.replace("_", "-"); el(id).setAttribute("aria-invalid", "true"); el(id).setAttribute("aria-describedby", id + "-error"); el(id + "-error").textContent = error.message; el(id + "-error").hidden = false; el(id).focus();}
    // Keep the validation error visible until the next user action.
  }
});
el("starts").addEventListener("change", patternDetail);
el("pattern").addEventListener("change", patternDetail);
el("connect-step").addEventListener("click", () => setView("connect"));
el("arrange-step").addEventListener("click", () => {renderPlan(); setView("arrange");});
el("waiting-filter").addEventListener("click", () => {onlyWaiting = !onlyWaiting; renderPlan();});

function canEdit() {return !!state?.plan && !state.plan.locked && !state.scanning && !state.busy && !state.run.active && !editing;}
function nodeFor(side, id) {
  function find(nodes) {
    for (const node of nodes) {
      if (node.id === id) return node;
      const child = find(node.children);
      if (child) return child;
    }
  }
  return find(side === "source" ? state.plan.source_nodes : state.plan.outline);
}
function selectedEntries() {return [...selection].map((id) => ({side: selectionSide, id}));}
function updateSelection() {
  for (const row of document.querySelectorAll(".selectable")) {
    const selected = row.dataset.side === selectionSide && selection.has(row.dataset.id);
    row.classList.toggle("selected", selected);
    if (row.tagName === "SUMMARY") row.setAttribute("aria-label", (selected ? "Selected folder: " : "Folder: ") + row.dataset.title);
    const checkbox = row.querySelector(".select-check");
    if (checkbox) {checkbox.checked = selected; checkbox.disabled = !canEdit();}
    row.draggable = canEdit();
  }
  const count = selection.size;
  el("selection-count").textContent = count ? `${count} selected · ${selectionSide === "source" ? "source" : "outline"}` : "No items selected";
  el("selection-actions").disabled = !count || !canEdit();
  el("clear-selection").disabled = !count;
  el("add-module").disabled = !canEdit();
  el("area-select").disabled = !canEdit();
  el("undo").disabled = !canEdit() || !state?.history.undo;
  el("redo").disabled = !canEdit() || !state?.history.redo;
  el("toast-undo").disabled = !canEdit() || !state?.history.undo;
  el("undo").title = state?.history.undo ? "Undo: " + state.history.undo + " (Ctrl+Z)" : "Nothing to undo";
  el("redo").title = state?.history.redo ? "Redo: " + state.history.redo + " (Ctrl+Y)" : "Nothing to redo";
  el("edit-badge").textContent = state?.plan?.locked ? state.run.status === "complete" ? "Created · locked" : "Started · locked" : state?.busy ? "App working…" : "Editing draft";
  el("accept-suggestions").hidden = !state?.plan?.suggestions;
  el("accept-suggestions").textContent = "Accept " + (state?.plan?.suggestions || 0) + " suggestions";
  el("accept-suggestions").disabled = !canEdit();
}
function selectRow(row, event = {}, toggle = false) {
  if (!canEdit()) return;
  const side = row.dataset.side, id = row.dataset.id;
  if (side !== selectionSide) {selection.clear(); anchorId = null; selectionSide = side;}
  const rows = [...document.querySelectorAll(`.selectable[data-side="${side}"]`)].filter((item) => item.getClientRects().length);
  if (event.shiftKey && anchorId) {
    const a = rows.findIndex((item) => item.dataset.id === anchorId), b = rows.indexOf(row);
    if (a >= 0 && b >= 0) {
      if (!event.ctrlKey && !event.metaKey) selection.clear();
      for (const item of rows.slice(Math.min(a,b), Math.max(a,b) + 1)) selection.add(item.dataset.id);
    } else {selection = new Set([id]); anchorId = id;}
  } else if (toggle || event.ctrlKey || event.metaKey) {
    if (selection.has(id)) selection.delete(id); else selection.add(id);
    anchorId = id;
  } else {
    selection = new Set([id]); anchorId = id;
  }
  updateSelection();
}
function wireRow(row, node, side, position) {
  row.classList.add("selectable");
  row.dataset.side = side; row.dataset.id = node.id;
  row.dataset.title = node.title;
  if (row.tagName === "SUMMARY") row.title = "Click to select this folder; click the arrow to expand or collapse. Right-click for options." + (side === "source" ? " Skipping this unit also removes its linked draft module." : "");
  row.addEventListener("click", (event) => {
    if (event.target.closest(".disclosure")) return;
    if (row.tagName === "SUMMARY") event.preventDefault();
    if (!areaMode) selectRow(row, event);
  });
  row.addEventListener("dblclick", () => {if (row.tagName === "SUMMARY") row.parentElement.open = !row.parentElement.open;});
  row.addEventListener("keydown", (event) => {
    if (event.key === " " && event.target.tagName === "SUMMARY") {event.preventDefault(); selectRow(row, event, true);}
    if (event.key === "F10" && event.shiftKey) {event.preventDefault(); openRowMenu(row, row.getBoundingClientRect().left, row.getBoundingClientRect().bottom);}
  });
  row.addEventListener("contextmenu", (event) => {
    if (!canEdit()) return;
    event.preventDefault(); openRowMenu(row, event.clientX, event.clientY);
  });
  row.addEventListener("dragstart", (event) => {
    if (!canEdit() || areaMode || event.target.closest("input")) {event.preventDefault(); return;}
    if (selectionSide !== side || !selection.has(node.id)) selectRow(row);
    closeMenu(false);
    dragPayload = {entries: selectedEntries(), plan_id: state.plan.id, revision: state.revision};
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("text/plain", node.title);
    el("destination-tree").classList.add("dragging-outline");
  });
  row.addEventListener("dragend", () => {dragPayload = null; clearDropTargets(); el("destination-tree").classList.remove("dragging-outline");});
  if (side === "destination") {
    function target(event) {
      const rect = row.getBoundingClientRect(), offset = event.clientY - rect.top;
      if (offset < rect.height * .25 || node.kind !== "module" && offset < rect.height / 2) return {parent: position.parent, before: position.before, edge: "drop-before"};
      if (offset > rect.height * .75 || node.kind !== "module") return {parent: position.parent, before: position.after, edge: "drop-after"};
      return {parent: node.id, edge: "drop-target"};
    }
    row.addEventListener("dragover", (event) => {
      if (!dragPayload) return;
      event.preventDefault(); event.stopPropagation(); clearDropTargets(); row.classList.add(target(event).edge);
    });
    row.addEventListener("dragleave", () => row.classList.remove("drop-target", "drop-before", "drop-after"));
    row.addEventListener("drop", (event) => {
      event.preventDefault(); event.stopPropagation(); const next = target(event); dropInto(next.parent, next.before);
    });
  }
}
function dropSlot(parent, before) {
  const slot = element("div", undefined, "drop-slot");
  slot.dataset.parent = parent || ""; slot.dataset.before = before || "";
  slot.setAttribute("aria-hidden", "true");
  slot.addEventListener("dragover", (event) => {
    if (!dragPayload) return;
    event.preventDefault(); event.stopPropagation(); clearDropTargets(); slot.classList.add("drop-target");
  });
  slot.addEventListener("dragleave", () => slot.classList.remove("drop-target"));
  slot.addEventListener("drop", (event) => {event.preventDefault(); event.stopPropagation(); dropInto(parent, before);});
  return slot;
}
function clearDropTargets() {document.querySelectorAll(".drop-target,.drop-before,.drop-after").forEach((row) => row.classList.remove("drop-target", "drop-before", "drop-after"));}
function dropInto(parent, before) {
  const payload = dragPayload;
  dragPayload = null; clearDropTargets(); el("destination-tree").classList.remove("dragging-outline");
  if (payload) mutate("place", {parent, before: before ?? null}, payload.entries, payload);
}
function selectContents(node, side) {
  selectionSide = side; selection.clear(); anchorId = null;
  function add(nodes) {for (const child of nodes) {if (child.kind === "module") add(child.children); else selection.add(child.id);}}
  add(node.children); updateSelection();
}
function closeMenu(restore = true) {
  el("context-menu").hidden = true;
  if (restore && menuAnchor?.isConnected) menuAnchor.focus();
  menuAnchor = null;
}
function openRowMenu(row, x, y) {
  if (selectionSide !== row.dataset.side || !selection.has(row.dataset.id)) selectRow(row);
  openMenu(x, y, row.tagName === "SUMMARY" ? row : row.querySelector("input"));
}
function openMenu(x, y, anchor) {
  if (!canEdit()) return;
  closeMenu(false); menuAnchor = anchor;
  const menu = el("context-menu"); menu.replaceChildren();
  const entries = selectedEntries(), single = entries.length === 1 ? nodeFor(selectionSide, entries[0].id) : null;
  function option(label, action, danger = false) {
    const button = element("button", label, danger ? "danger" : "");
    button.type = "button"; button.setAttribute("role", "menuitem");
    button.addEventListener("click", () => {closeMenu(); action();}); menu.append(button);
  }
  if (entries.length) {
    option(selectionSide === "source" ? "Place in outline…" : "Move to folder…", () => openPlaceDialog(entries));
    if (single?.kind === "module") {
      option("Select all items in this folder", () => selectContents(single, selectionSide));
      if (selectionSide === "destination") {
        option("Edit sections…", () => openSections(single));
        option("Add a section folder…", () => openNameDialog("add_module", "Add a section folder", "New section", {parent: single.id}));
        option("Apply saved layout…", () => openLayoutDialog(single, entries));
        option("Save sections as a layout…", () => openNameDialog("save_pattern", "Save this layout", single.title + " layout", {}, entries));
        option("Choose source suggestions…", () => openSourcesDialog(single, entries));
        option("Add a placeholder page…", () => openNameDialog("add_page", "Add a placeholder page", "New page", {kind: "placeholder"}, entries));
        option("Add an introduction page", () => mutate("add_page", {kind: "introduction", title: "Module Introduction"}, entries));
        option("Add a session overview page", () => mutate("add_page", {kind: "session", title: "Session Overview"}, entries));
      }
      else option("Make a new module", () => mutate("place", {parent: null}, entries));
    }
    if (selectionSide === "destination") {
      if (single?.renamable) option("Rename…", () => openNameDialog("rename", "Rename item", single.title, {}, entries));
      if (single?.kind === "template") option("Write page text…", () => openContentDialog(single, entries));
      if (single && !single.existing) {
        option("Move up", () => mutate("reorder", {direction: "up"}, entries));
        option("Move down", () => mutate("reorder", {direction: "down"}, entries));
      }
      if (single && state.plan.issues.some((issue) => issue.node === single.id && issue.fix === "use_existing")) option("Add into the existing module", () => mutate("use_existing", {}, entries));
      option("Remove from outline", () => mutate("remove", {}, entries), true);
    } else {
      option("Leave out of transfer", () => mutate("skip", {}, entries), true);
      option("Include again (unplaced)", () => mutate("include", {}, entries));
      if (single?.split) option("Keep the page whole", () => mutate("join_page", {}, entries));
    }
    if (single) option("Copy name", () => navigator.clipboard.writeText(single.title).then(() => showToast("Copied the name", false)).catch(() => showError("The browser could not copy the name.")));
  }
  option("Select all visible items", () => {
    const side = selectionSide || "destination"; selectionSide = side;
    selection = new Set([...document.querySelectorAll(`.selectable[data-side="${side}"]`)].filter((row) => row.getClientRects().length).map((row) => row.dataset.id)); updateSelection();
  });
  menu.hidden = false;
  const rect = menu.getBoundingClientRect();
  menu.style.left = Math.max(8, Math.min(x, innerWidth - rect.width - 8)) + "px";
  menu.style.top = Math.max(8, Math.min(y, innerHeight - rect.height - 8)) + "px";
  menu.querySelector("button")?.focus();
}
function openNameDialog(action, title, value, args = {}, entries = []) {
  dialogAction = {action, args, entries};
  el("dialog-title").textContent = title;
  el("dialog-note").textContent = "Saved to your draft. You can undo this change.";
  el("dialog-text-label").hidden = false; el("dialog-place-label").hidden = true;
  el("dialog-text").required = true; el("dialog-text").value = value;
  el("edit-dialog").showModal(); el("dialog-text").select();
}
function openPlaceDialog(entries) {
  dialogAction = {action: "place", args: {}, entries};
  el("dialog-title").textContent = "Choose a destination folder";
  el("dialog-note").textContent = "The selection is placed at the end of this folder. You can undo the move.";
  el("dialog-text-label").hidden = true; el("dialog-place-label").hidden = false; el("dialog-text").required = false;
  el("dialog-place-caption").textContent = "Destination folder"; el("dialog-place").multiple = false; el("dialog-place").size = 0;
  const excluded = new Set();
  function exclude(node) {excluded.add(node.id); node.children.forEach(exclude);}
  for (const entry of entries) if (entry.side === "destination") exclude(nodeFor(entry.side, entry.id));
  const options = [];
  function add(nodes, path = []) {for (const node of nodes) if (node.kind === "module") {if (!excluded.has(node.id)) {const option = element("option", [...path, node.title].join(" / ")); option.value = node.id; options.push(option);} add(node.children, [...path, node.title]);}}
  if (entries.every((entry) => nodeFor(entry.side, entry.id)?.kind === "module")) {const top = element("option", "Top level (modules)"); top.value = ""; options.push(top);}
  add(state.plan.outline);
  if (!options.length) {showError("Add a destination module first, then place your selection."); return;}
  el("dialog-place").replaceChildren(...options); el("edit-dialog").showModal(); el("dialog-place").focus();
}
function showToast(message, undoAllowed = true) {
  clearTimeout(toastTimer);
  el("toast-message").textContent = message;
  el("toast").hidden = false; el("toast-undo").disabled = !state.history.undo;
  el("toast-undo").hidden = !undoAllowed;
  toastTimer = setTimeout(() => {el("toast").hidden = true;}, 8000);
}
async function mutate(action, args = {}, entries = selectedEntries(), expected = {}) {
  if (!canEdit()) return;
  clearTimeout(pollTimer); closeMenu(false);
  const body = {plan_id: expected.plan_id || state.plan.id, revision: expected.revision ?? state.revision, action, args, entries};
  editing = true; updateSelection();
  try {
    const result = await api("/api/edit", body);
    state = result.state; editing = false;
    if (["remove", "skip", "include"].includes(action)) selection.clear();
    renderState(); renderPlan(); showToast(result.message, state.revision !== body.revision);
  } catch (error) {
    editing = false;
    if (action === "skip" && error.confirmation) {
      skipConfirmation = body;
      el("skip-unit-title").textContent = error.confirmation.modules.length === 1 ? "Skip this unit and its draft module?" : "Skip selected units and their draft modules?";
      el("skip-unit-details").replaceChildren(...error.confirmation.protected.map((item) => element("li", `${item.unit} / ${item.title} — ${item.reason}`)));
      el("skip-unit-dialog").showModal(); el("skip-unit-cancel").focus();
    } else {await refresh(); showError(error.message);}
  } finally {
    updateSelection();
    clearTimeout(pollTimer); pollTimer = setTimeout(() => refresh(), 3000);
  }
}
function setAreaMode(on) {
  areaMode = on; el("area-select").setAttribute("aria-pressed", String(on));
  el("arrange").classList.toggle("area-mode", on);
  el("selection-help").textContent = on ? "Drag a box over items in either column. Hold Ctrl/Cmd to add to the selection. Esc cancels." : "Click to select · Ctrl/Cmd-click to add · Shift-click for a range · Delete to remove from the outline";
}
for (const tree of [el("source-tree"), el("destination-tree")]) {
  tree.addEventListener("pointerdown", (event) => {
    if (!areaMode || !canEdit() || event.button !== 0) return;
    event.preventDefault(); closeMenu(false);
    const side = tree.id === "source-tree" ? "source" : "destination";
    const initial = event.ctrlKey || event.metaKey ? new Set(selectionSide === side ? selection : []) : new Set();
    const start = {x: event.clientX, y: event.clientY};
    const beforeSide = selectionSide, beforeSelection = new Set(selection);
    tree.setPointerCapture(event.pointerId);
    const box = el("selection-box"); box.hidden = false;
    function draw(move) {
      const bounds = tree.getBoundingClientRect();
      const x = Math.max(bounds.left, Math.min(move.clientX, bounds.right)), y = Math.max(bounds.top, Math.min(move.clientY, bounds.bottom));
      const rect = {left: Math.min(start.x,x), top: Math.min(start.y,y), right: Math.max(start.x,x), bottom: Math.max(start.y,y)};
      box.style.left = rect.left + "px"; box.style.top = rect.top + "px"; box.style.width = rect.right - rect.left + "px"; box.style.height = rect.bottom - rect.top + "px";
      selectionSide = side; selection = new Set(initial);
      for (const row of tree.querySelectorAll(".selectable")) {
        if (!row.getClientRects().length) continue;
        const r = row.getBoundingClientRect();
        if (r.bottom > bounds.top && r.top < bounds.bottom && r.right > rect.left && r.left < rect.right && r.bottom > rect.top && r.top < rect.bottom) selection.add(row.dataset.id);
      }
      updateSelection();
    }
    function finish(cancel = false) {
      tree.removeEventListener("pointermove", draw); tree.removeEventListener("pointerup", up); tree.removeEventListener("pointercancel", cancelPointer); document.removeEventListener("keydown", escape);
      if (tree.hasPointerCapture(event.pointerId)) tree.releasePointerCapture(event.pointerId);
      box.hidden = true; setAreaMode(false);
      if (cancel) {selectionSide = beforeSide; selection = beforeSelection; updateSelection();}
    }
    function up() {finish();}
    function cancelPointer() {finish(true);}
    function escape(key) {if (key.key === "Escape") finish(true);}
    draw(event);
    tree.addEventListener("pointermove", draw); tree.addEventListener("pointerup", up); tree.addEventListener("pointercancel", cancelPointer); document.addEventListener("keydown", escape);
  });
}
el("context-menu").addEventListener("keydown", (event) => {
  const buttons = [...el("context-menu").querySelectorAll("button")], index = buttons.indexOf(document.activeElement);
  if (["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) {event.preventDefault(); buttons[event.key === "Home" ? 0 : event.key === "End" ? buttons.length - 1 : (index + (event.key === "ArrowDown" ? 1 : -1) + buttons.length) % buttons.length].focus();}
  if (event.key === "Escape" || event.key === "Tab") {if (event.key === "Escape") event.preventDefault(); closeMenu();}
});
document.addEventListener("pointerdown", (event) => {if (!event.target.closest("#context-menu")) closeMenu(false);});
document.addEventListener("scroll", () => closeMenu(false), true);
document.addEventListener("keydown", (event) => {
  if (!["arrange", "styling"].includes(view) || event.target.closest("input:not([type=checkbox]):not([type=radio]),textarea,select,[contenteditable=true]") || document.querySelector("dialog[open]")) return;
  const key = event.key.toLowerCase();
  if (event.ctrlKey || event.metaKey) {
    if (key === "z" || key === "y") {event.preventDefault(); const action = key === "y" || event.shiftKey ? "redo" : "undo"; if (state?.history[action]) mutate(action);}
    if (key === "a" && event.target.closest(".tree")) {event.preventDefault(); selectionSide = event.target.closest(".selectable")?.dataset.side || (event.target.closest("#source-tree") ? "source" : "destination"); selection = new Set([...event.target.closest(".tree").querySelectorAll(".selectable")].filter((row) => row.getClientRects().length).map((row) => row.dataset.id)); updateSelection();}
  } else if ((event.key === "Delete" || event.key === "Backspace") && selection.size && !event.target.closest("#context-menu")) {event.preventDefault(); mutate(selectionSide === "source" ? "skip" : "remove");}
  if (event.key === "Escape") {closeMenu(); setAreaMode(false); selection.clear(); updateSelection();}
});
el("edit-form").addEventListener("submit", (event) => {
  event.preventDefault(); const task = dialogAction;
  if (task.action === "place") task.args.parent = el("dialog-place").value || null;
  else if (task.action === "open_plan") {const key = el("dialog-place").value; el("edit-dialog").close(); openSavedTransfer(key); return;}
  else if (task.action === "apply_pattern") task.args.pattern = el("dialog-place").value;
  else if (task.action === "set_sources") task.args.sources = [...el("dialog-place").selectedOptions].map((option) => option.value);
  else task.args.title = el("dialog-text").value;
  el("edit-dialog").close(); mutate(task.action, task.args, task.entries);
});
el("dialog-cancel").addEventListener("click", () => el("edit-dialog").close());
el("skip-unit-cancel").addEventListener("click", () => el("skip-unit-dialog").close());
el("skip-unit-dialog").addEventListener("close", () => {skipConfirmation = null;});
el("skip-unit-confirm").addEventListener("click", () => {
  const pending = skipConfirmation;
  el("skip-unit-dialog").close();
  if (pending) mutate("skip", {...pending.args, confirmed_custom: true}, pending.entries, pending);
});
el("selection-actions").addEventListener("click", () => {const rect = el("selection-actions").getBoundingClientRect(); openMenu(rect.left, rect.bottom + 5, el("selection-actions"));});
el("clear-selection").addEventListener("click", () => {selection.clear(); updateSelection();});
el("area-select").addEventListener("click", () => setAreaMode(!areaMode));
el("add-module").addEventListener("click", () => openNameDialog("add_module", "Add a module", "New module"));
el("undo").addEventListener("click", () => mutate("undo"));
el("redo").addEventListener("click", () => mutate("redo"));
el("toast-undo").addEventListener("click", () => mutate("undo"));
el("toast-dismiss").addEventListener("click", () => {el("toast").hidden = true;});
el("destination-tree").addEventListener("dragover", (event) => {if (dragPayload) event.preventDefault();});
el("destination-tree").addEventListener("drop", (event) => {event.preventDefault(); dropInto(null);});
document.addEventListener("DOMContentLoaded", () => refresh(true), {once: true});
