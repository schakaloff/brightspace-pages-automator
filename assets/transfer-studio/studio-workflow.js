"use strict";
// Uses the shared state and authenticated API in studio.js.
let workflowKey = "", selectedPage = null, previewKey = "", previewRequest = 0;
let reviewRevision = null, sectionsId = null, contentEntries = [], logCount = 0;
const workflowSteps = ["connect", "arrange", "styling", "review", "create"];
const modeLabels = {preserve: "Preserve existing styling", strip_bpa: "Strip Page Automator styling", strip_all: "Strip all custom styling"};

function renderWorkflow(force = false) {
  if (!state) return;
  const plan = state.plan, active = state.run.active;
  for (const step of ["styling", "review", "create"]) el(step + "-step").disabled = !plan || state.scanning || (active && step !== "create");
  el("create-step").disabled ||= !plan?.locked && state.run.status === "idle";
  if (active) {el("connect-step").disabled = true; el("arrange-step").disabled = true;}
  el("open-saved").disabled = active || state.scanning || state.busy;
  const index = workflowSteps.indexOf(view);
  el("back").hidden = index < 1 || active;
  el("back").textContent = "Back to " + (workflowSteps[index - 1] || "Connect").replace(/^./, (c) => c.toUpperCase());
  el("workflow-next").hidden = !plan || !["arrange", "styling", "review"].includes(view) || active;
  el("create-blockers").hidden = view !== "review";
  document.querySelector("footer").classList.toggle("review-footer", view === "review");
  el("workflow-next").textContent = view === "arrange" ? "Continue to styling" : view === "styling" ? "Continue to review" : plan?.locked ? "Resume transfer" : "Create transfer";
  el("workflow-next").disabled = view === "review" && (!el("confirm-create").checked || !!plan?.issues.some((i) => i.level === "error") || state.busy || state.run.status === "complete");
  const key = [view, plan?.id, state.revision].join(":");
  if (plan && (force || key !== workflowKey)) {
    workflowKey = key;
    if (view === "styling") renderStyling();
    if (view === "review") renderReview();
  }
  if (view === "styling") {
    for (const input of el("styling").querySelectorAll("input,select")) input.disabled = !canEdit();
    const preserve = plan?.styling.mode === "preserve";
    el("apply-look").disabled = !canEdit() || preserve;
    el("look").disabled = !canEdit() || preserve || !plan?.styling.apply_template;
    el("page-override").disabled = !canEdit() || !selectedPage || !!plan.pages.find((page) => page.id === selectedPage)?.generated;
  }
  if (view === "review") {el("grade-categories").disabled = !canEdit(); el("confirm-create").disabled = active || state.busy;}
  if (view === "create" && plan) renderRun();
  if (el("sections-dialog").open && plan) renderSections();
  if (view === "review") {
    const blockers = plan.issues.filter((i) => i.level === "error").length;
    el("workflow-next").disabled = !el("confirm-create").checked || !!blockers || state.busy || state.run.status === "complete";
    el("create-blockers").textContent = state.run.status === "complete" ? "This transfer is already complete." : state.busy ? "Wait for the desktop app to finish." : blockers ? `Fix ${blockers} blocking issue${blockers === 1 ? "" : "s"} before creating.` : !el("confirm-create").checked ? "Confirm the destination above to enable Create." : "Ready to create.";
  }
}

function renderStyling() {
  const plan = state.plan;
  for (const radio of document.querySelectorAll("input[name=style-mode]")) radio.checked = radio.value === plan.styling.mode;
  el("apply-look").checked = !!plan.styling.apply_template;
  el("look").replaceChildren(...plan.looks.map((look) => {const option = element("option", look.label); option.value = look.key; return option;}));
  if (plan.look) el("look").value = plan.look;
  if (!plan.pages.some((page) => page.id === selectedPage)) selectedPage = plan.pages[0]?.id || null;
  el("page-count").textContent = "Pages · " + plan.pages.length;
  el("style-pages").replaceChildren(...plan.pages.map((page) => {
    const button = element("button");
    button.type = "button"; button.dataset.page = page.id;
    button.setAttribute("aria-pressed", String(page.id === selectedPage));
    button.append(element("span", page.title), element("small", page.generated ? "Uses its generated template" : page.override ? "This page: " + modeLabels[page.override] : "Uses the transfer setting"));
    button.addEventListener("click", () => {selectedPage = page.id; renderStyling();});
    return button;
  }));
  if (!plan.pages.length) el("style-pages").append(element("p", "No source pages are placed. Generated pages use their chosen template.", "empty"));
  el("page-override").value = plan.pages.find((page) => page.id === selectedPage)?.override || "";
  el("page-override").disabled = !selectedPage || !canEdit();
  loadPagePreview();
}
async function loadPagePreview() {
  if (!selectedPage) {
    el("preview-title").textContent = "No source page selected"; el("preview-summary").textContent = "Add source pages in Arrange to preview styling.";
    el("before-preview").removeAttribute("src"); el("after-preview").removeAttribute("src"); return;
  }
  const key = state.plan.id + ":" + state.revision + ":" + selectedPage;
  if (previewKey === key) return;
  const request = ++previewRequest, row = selectedPage;
  el("preview-summary").textContent = "Preparing the preview…";
  try {
    const data = await api("/api/page?row=" + encodeURIComponent(row));
    if (request !== previewRequest || key !== state.plan.id + ":" + state.revision + ":" + selectedPage) return;
    previewKey = key;
    el("preview-title").textContent = data.title;
    el("preview-summary").textContent = data.summary;
    el("preview-summary").classList.toggle("field-error", !data.ok);
    for (const side of ["before", "after"]) el(side + "-preview").src = `/api/preview?row=${encodeURIComponent(row)}&side=${side}&revision=${state.revision}`;
    el("removed-list").replaceChildren(...(data.removed.length ? data.removed : ["Nothing removed."]).map((item) => element("li", item)));
    if (data.templated) el("removed-list").append(element("li", "Then placed inside the selected look."));
    el("kept-list").replaceChildren(...data.kept.map((item) => element("li", item)));
  } catch (error) {if (request === previewRequest) {el("preview-summary").textContent = error.message; showError(error.message, "styling");}}
}

function showIssue(issue) {
  onlyWaiting = issue.fix === "place_unplaced"; selection.clear();
  selectionSide = issue.node ? "destination" : "source";
  let id = issue.node || issue.row;
  if (onlyWaiting) {
    function firstWaiting(nodes) {for (const node of nodes) {if (node.status === "Unplaced") return node.id; const found = firstWaiting(node.children); if (found) return found;}}
    id = firstWaiting(state.plan.source_nodes);
  }
  if (id) {selection.add(id); anchorId = id;}
  renderPlan(); setView("arrange");
  const row = [...document.querySelectorAll(".selectable")].find((node) => node.dataset.side === selectionSide && node.dataset.id === id);
  if (row) {
    let parent = row.parentElement;
    while (parent) {if (parent.tagName === "DETAILS") parent.open = true; parent = parent.parentElement;}
    row.scrollIntoView({block: "nearest"}); (row.querySelector("input") || row).focus();
  }
}
function renderReview() {
  const plan = state.plan, counts = plan.counts;
  if (reviewRevision !== state.revision) {el("confirm-create").checked = false; reviewRevision = state.revision;}
  const steps = [
    ["Check both courses", "The source must still match its scan. The destination identity and conflicts are checked before the transfer proceeds."],
    ["Prepare pages and files", "Check styling, file collisions and every course-file/activity link. Different files at the same path are handled separately; large collisions stop the transfer."],
    ["Copy the selected activities and course files", `${counts.activities} activities use one Copy Components job with their rubrics, grade items and attachments. Content is not selected and dates are not shifted.`],
    ["Build the hidden outline", `${counts.modules} new modules and folders. Existing destination modules keep their names, order and existing content.`],
    ["Add the pages, files and links", `${counts.pages} pages and ${counts["files and links"]} files/links, placed in your outline. Source pages: ${modeLabels[plan.styling.mode].toLowerCase()}${plan.styling.apply_template ? ", then apply the chosen look" : ""}.`],
    ["Read everything back", "Check order, activities, rubric/grade details, files and page content. Save a report and the journal so an interrupted transfer can resume."]
  ];
  el("review-steps").replaceChildren(...steps.map(([title, text]) => {const item = element("li"); item.append(element("h3", title), element("p", text, "hint")); return item;}));
  el("review-activities").replaceChildren(...(plan.activities.length ? plan.activities.map((item) => kindNames[item.kind] + ": " + item.title) : ["No activities selected."]).map((text) => element("li", text)));
  el("review-skipped").replaceChildren(...(plan.skipped.length ? plan.skipped.map((item) => item.title + (item.manual ? " · Add manually in Brightspace" : " · Left out")) : ["No items skipped."]).map((text) => element("li", text)));
  el("grade-categories").value = plan.grade_categories;
  el("review-issues").replaceChildren();
  el("review-note-list").replaceChildren();
  const blockers = plan.issues.filter((issue) => issue.level === "error").length;
  const notes = plan.issues.length - blockers;
  el("review-notes-summary").textContent = `${notes} informational note${notes === 1 ? "" : "s"} · Do not block Create`;
  el("review-notes").hidden = !notes;
  for (const issue of plan.issues) {
    const card = element("div", undefined, "issue " + issue.level);
    card.append(element("p", issue.level === "error" ? "Blocks Create" : "Informational · Does not block Create", "issue-label"), element("p", issue.text));
    if (issue.node) {
      function location(nodes, path = []) {for (const node of nodes) {const trail = [...path, node.title]; if (node.id === issue.node) return trail.join(" / "); const found = location(node.children, trail); if (found) return found;}}
      const path = location(plan.outline);
      if (path) card.append(element("p", path, "issue-location"));
    }
    if (issue.node || issue.row) {const show = element("button", "Show in Arrange"); show.addEventListener("click", () => showIssue(issue)); card.append(show);}
    if (issue.fix === "place_unplaced") {const show = element("button", "Find unplaced items"); show.addEventListener("click", () => showIssue(issue)); card.append(show);}
    if (issue.fix === "use_existing" && canEdit()) {const fix = element("button", "Add into the existing module"); fix.addEventListener("click", () => mutate("use_existing", {}, [{side: "destination", id: issue.node}])); card.append(fix);}
    if (issue.fix === "use_existing_link" && canEdit()) {const fix = element("button", "Use the existing destination assignment"); fix.addEventListener("click", () => mutate("use_existing_link", {target_id: issue.target_id}, [{side: "source", id: issue.link_row}])); card.append(fix);}
    if (issue.row && canEdit()) {const skip = element("button", "Leave this item out"); skip.addEventListener("click", () => mutate("skip", {}, [{side: "source", id: issue.row}])); card.append(skip);}
    el(issue.level === "error" ? "review-issues" : "review-note-list").append(card);
  }
  if (!blockers) el("review-issues").append(element("p", "No blocking issues. Informational notes can be addressed later.", "hint"));
  el("review-status").textContent = blockers ? `${blockers} blocking issue${blockers === 1 ? "" : "s"} to fix. Informational notes do not block Create.` : "No blocking issues. Confirm the destination to enable Create.";
  el("confirm-label").textContent = `I have reviewed the plan and notes. ${plan.locked ? "Resume" : "Create"} this transfer in ${plan.target.name} (course ${plan.target.id}). New Content starts hidden.`;
}

function renderRun() {
  const run = state.run, plan = state.plan;
  const titles = {idle: "Ready to create", queued: "Waiting for the desktop app…", running: "Creating the transfer…", stopping: "Stopping after the current request…", stopped: "Transfer stopped", paused: "This transfer can be resumed", failed: "The transfer needs attention", review: "Some checks need attention", complete: "Transfer created and checked"};
  el("run-title").textContent = titles[run.status] || "Transfer progress";
  el("run-description").textContent = run.status === "complete" ? `New Content is hidden in ${plan.target.name} until you publish it.` : run.active ? "Keep the app open. You can stop after the current request and resume the same saved plan." : "Review the log and report below. The saved journal records what has already been created.";
  const phases = ["Check courses", "Prepare pages and files", "Copy activities and course files", "Create modules and link activities", "Add pages and files", "Read everything back"];
  el("run-phases").replaceChildren(...phases.map((title, index) => {
    const done = run.status === "complete" || index < run.phase;
    const item = element("li", undefined, done ? "done" : run.active && index === run.phase ? "current" : "");
    item.append(element("span", done ? "✓" : String(index + 1), "phase-marker"), element("span", title)); return item;
  }));
  el("stop-create").hidden = !run.active; el("stop-create").disabled = run.stop_requested;
  const log = el("run-log"), bottom = log.scrollHeight - log.scrollTop - log.clientHeight < 60;
  const lastSequence = run.messages.at(-1)?.sequence || 0;
  if (lastSequence < logCount) {log.replaceChildren(); logCount = 0;}
  for (const message of run.messages.filter((message) => message.sequence > logCount)) log.append(element("p", message.text, "log-" + message.level));
  logCount = lastSequence;
  if (bottom) log.scrollTop = log.scrollHeight;
  el("report-section").hidden = !plan.report;
  el("transfer-report").textContent = plan.report;
  el("validation-results").replaceChildren(...Object.entries(plan.validation).map(([label, passed]) => element("div", (passed ? "✓ " : "Needs attention: ") + label, passed ? "check-pass" : "check-fail")));
  el("open-destination").href = `https://learn.okanagancollege.ca/d2l/home/${encodeURIComponent(plan.target.id)}`;
  const resumable = !run.active && run.status !== "complete";
  el("resume-section").hidden = !resumable;
  el("resume-label").textContent = `I confirm ${plan.target.name} (course ${plan.target.id}) as the destination.`;
  el("resume-create").disabled = !el("confirm-resume").checked || state.busy || !!plan.issues.some((issue) => issue.level === "error");
  el("resume-create").textContent = plan.locked ? "Resume transfer" : "Create transfer";
}
async function createTransfer(resume = false) {
  if (editing || state.run.active || !(resume ? el("confirm-resume").checked : el("confirm-create").checked)) return;
  clearTimeout(pollTimer); editing = true; updateSelection();
  try {
    const result = await api("/api/create", {plan_id: state.plan.id, revision: state.revision, target_id: state.plan.target.id, confirmed: true});
    state = result.state; editing = false; el("confirm-resume").checked = false;
    logCount = 0; el("run-log").replaceChildren(); setView("create"); renderState(); await refresh();
  } catch (error) {editing = false; showError(error.message, resume ? "create" : "review"); updateSelection(); pollTimer = setTimeout(() => refresh(), 3000);}
}

function openLayoutDialog(module, entries) {
  dialogAction = {action: "apply_pattern", args: {}, entries};
  el("dialog-title").textContent = "Apply a section layout";
  el("dialog-note").textContent = "Existing draft content is kept. The layout adds or matches sections and suggests places for source items. You can undo it.";
  el("dialog-text-label").hidden = true; el("dialog-text").required = false; el("dialog-place-label").hidden = false;
  el("dialog-place-caption").textContent = "Saved layout"; el("dialog-place").multiple = false; el("dialog-place").size = 0;
  el("dialog-place").replaceChildren(...state.patterns.map((pattern) => {const option = element("option", pattern.name + " · " + pattern.sections.join(" / ")); option.value = pattern.name; return option;}));
  el("edit-dialog").showModal(); el("dialog-place").focus();
}
function openSourcesDialog(module, entries) {
  dialogAction = {action: "set_sources", args: {}, entries};
  el("dialog-title").textContent = "Choose source suggestions";
  el("dialog-note").textContent = "Suggestions for this module come from the selected source folders. Ctrl/Cmd-click to select more than one.";
  el("dialog-text-label").hidden = true; el("dialog-text").required = false; el("dialog-place-label").hidden = false;
  el("dialog-place-caption").textContent = "Source folders"; el("dialog-place").multiple = true; el("dialog-place").size = 6;
  const options = [];
  function add(nodes, path = []) {for (const node of nodes) if (node.kind === "module") {const option = element("option", [...path, node.title].join(" / ")); option.value = node.id; option.selected = module.sources.includes(node.id); options.push(option); add(node.children, [...path, node.title]);}}
  add(state.plan.source_nodes); el("dialog-place").replaceChildren(...options); el("edit-dialog").showModal();
}
function openSections(module) {sectionsId = module.id; renderSections(); el("sections-dialog").showModal();}
function renderSections() {
  const module = nodeFor("destination", sectionsId);
  if (!module) {el("sections-dialog").close(); return;}
  el("sections-title").textContent = "Sections in “" + module.title + "”";
  const sections = module.children.filter((child) => child.kind === "module");
  el("sections-list").replaceChildren(...sections.map((section) => {
    const row = element("div", undefined, "section-editor-row"); row.append(element("strong", section.title));
    const rename = element("button", "Rename"), remove = element("button", "Remove");
    rename.setAttribute("aria-label", "Rename " + section.title); remove.setAttribute("aria-label", "Remove " + section.title);
    rename.disabled = !section.renamable;
    rename.addEventListener("click", () => {el("sections-dialog").close(); openNameDialog("rename", "Rename section", section.title, {}, [{side: "destination", id: section.id}]);});
    remove.addEventListener("click", () => mutate("remove", {}, [{side: "destination", id: section.id}]));
    row.append(rename, remove); return row;
  }));
  if (!sections.length) el("sections-list").append(element("p", "This module has no section folders yet.", "hint"));
}
function openContentDialog(node, entries) {
  contentEntries = entries;
  el("content-title").textContent = "Text for “" + node.title + "”";
  el("intro-text-label").hidden = node.template !== "introduction";
  el("outcomes-text-label").hidden = node.template !== "introduction";
  el("session-text-label").hidden = node.template !== "session";
  el("intro-text").value = node.text?.introduction || ""; el("outcomes-text").value = (node.text?.outcomes || []).join("\n"); el("session-text").value = node.text?.session || "";
  el("content-dialog").showModal();
}

el("back").addEventListener("click", () => setView(workflowSteps[Math.max(0, workflowSteps.indexOf(view) - 1)]));
el("workflow-next").addEventListener("click", () => view === "review" ? createTransfer() : setView(view === "arrange" ? "styling" : "review"));
for (const step of ["styling", "review", "create"]) el(step + "-step").addEventListener("click", () => setView(step));
el("styling-modes").addEventListener("change", (event) => mutate("styling", {mode: event.target.value}));
el("apply-look").addEventListener("change", () => mutate("styling", {apply_template: el("apply-look").checked, look: el("look").value}));
el("look").addEventListener("change", () => mutate("styling", {look: el("look").value, apply_template: true}));
el("page-override").addEventListener("change", () => mutate("styling", {row: selectedPage, override: el("page-override").value || null}));
el("grade-categories").addEventListener("change", () => mutate("grade_categories", {mode: el("grade-categories").value}));
el("confirm-create").addEventListener("change", () => renderWorkflow());
el("confirm-resume").addEventListener("change", renderRun);
el("resume-create").addEventListener("click", () => createTransfer(true));
el("stop-create").addEventListener("click", async () => {try {const result = await api("/api/stop", {}); state = result.state; renderState();} catch (error) {showError(error.message, "create");}});
el("accept-suggestions").addEventListener("click", () => mutate("accept_suggestions", {}, []));
el("sections-close").addEventListener("click", () => el("sections-dialog").close());
el("sections-add").addEventListener("click", () => {el("sections-dialog").close(); openNameDialog("add_module", "Add a section", "New section", {parent: sectionsId});});
el("sections-apply").addEventListener("click", () => {el("sections-dialog").close(); openLayoutDialog(nodeFor("destination", sectionsId), [{side: "destination", id: sectionsId}]);});
el("sections-save").addEventListener("click", () => {el("sections-dialog").close(); openNameDialog("save_pattern", "Save this layout", "My section layout", {}, [{side: "destination", id: sectionsId}]);});
el("content-cancel").addEventListener("click", () => el("content-dialog").close());
el("content-form").addEventListener("submit", (event) => {event.preventDefault(); el("content-dialog").close(); mutate("set_text", {introduction: el("intro-text").value, outcomes: el("outcomes-text").value.split("\n").filter((line) => line.trim()), session: el("session-text").value}, contentEntries);});
el("open-saved").addEventListener("click", async () => {
  try {
    const result = await api("/api/plans");
    if (!result.plans.length) {showError("No saved browser transfers yet. Scan both courses to start one.", "connect"); return;}
    dialogAction = {action: "open_plan", args: {}, entries: []};
    el("dialog-title").textContent = "Open a saved transfer";
    el("dialog-note").textContent = "Started transfers reopen with their report and can resume from the saved journal.";
    el("dialog-text-label").hidden = true; el("dialog-text").required = false; el("dialog-place-label").hidden = false;
    el("dialog-place-caption").textContent = "Saved transfer"; el("dialog-place").multiple = false; el("dialog-place").size = 0;
    el("dialog-place").replaceChildren(...result.plans.map((plan) => {const option = element("option", plan.label + " · " + plan.status + " · " + new Date(plan.updated * 1000).toLocaleString()); option.value = plan.key; return option;}));
    el("edit-dialog").showModal();
  } catch (error) {showError(error.message, "connect");}
});
async function openSavedTransfer(key) {
  try {
    const result = await api("/api/open", {key}); state = result.state;
    el("source-url").value = state.source_url; el("target-url").value = state.target_url;
    showError("");
    for (const field of ["source-url", "target-url"]) {el(field).removeAttribute("aria-invalid"); el(field + "-error").hidden = true;}
    renderState(); setView(state.plan.locked ? "create" : "arrange");
  } catch (error) {showError(error.message, "connect");}
}
for (const field of ["source-url", "target-url"]) el(field).addEventListener("input", () => {el(field).removeAttribute("aria-invalid"); el(field + "-error").hidden = true; if (errorScope === "connect") showError("", "connect");});
