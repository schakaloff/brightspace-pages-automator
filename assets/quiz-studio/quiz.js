'use strict';
const $ = id => document.getElementById(id);
let state, active = '', detail, editingVariant = 'current', dirty = false, setup = true, requestBusy = false;
function node(tag, text, cls) { const e = document.createElement(tag); if (text !== undefined) e.textContent = text; if (cls) e.className = cls; return e; }
function message(text, error = false) { $('message').hidden = !text; $('message').textContent = text; $('message').classList.toggle('error', error); }
function showReason(id, text) { $(id).textContent = text; $(id).hidden = !text; }
function buttons(busy) {
  requestBusy = busy; document.body.setAttribute('aria-busy', String(busy));
  document.querySelectorAll('button,input,select,textarea').forEach(b => { b.disabled = busy; });
  if (!busy && state) {
    $('load').disabled = !state.files.candidate;
    $('export').disabled = !state.workspace.rows.length || state.workspace.blocked > 0 || state.workspace.global_errors.length > 0;
    $('add-answer').disabled = detail?.record.kind === 'True/False';
    if (detail) {
      const version = detail.variants.find(v => v.id === $('version').value);
      $('use-version').disabled = !version?.can_use;
      $('keep').disabled = !detail.can_keep;
    }
  }
}
async function api(path, body) {
  if (body !== undefined && state) body = {...body, revision: state.revision};
  const response = await fetch(path, body === undefined ? {} : {method: 'POST', headers: {'Content-Type': 'application/json', 'X-Studio-Request': '1'}, body: JSON.stringify(body)});
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || 'The request could not be completed.');
  return data;
}
async function action(path, body, text) {
  buttons(true); message(text || 'Saving your review…');
  try { state = await api(path, body); render(); return true; }
  catch (e) { message(e.message, true); return false; }
  finally { buttons(false); }
}
function canLeave() { return !dirty || window.confirm('Leave this question without saving your latest edits?'); }
function setDirty() { dirty = true; $('question-state').textContent = 'Unsaved edits'; }
function render() {
  if (state.files_changed) setup = true;
  const placeholders = {candidate: 'Drop your Brightspace ZIP here.', reference: 'Drop the original course here, or leave this empty.', pictures: 'Drop a ZIP with pictures here, if needed.'};
  for (const field of ['candidate', 'reference', 'pictures']) {
    $(field + '-path').textContent = state.files[field]?.split(/[\\/]/).pop() || placeholders[field];
    const clear = document.querySelector(`[data-clear="${field}"]`);
    if (clear) clear.hidden = !state.files[field];
  }
  const w = state.workspace;
  $('connect').hidden = !setup && !!w.rows.length;
  $('workspace').hidden = setup || !w.rows.length;
  const quizzes = [...new Set(w.rows.map(r => r.quiz))];
  const selected = $('quiz-filter').value;
  $('quiz-filter').replaceChildren(new Option('All quizzes', ''), ...quizzes.map(q => new Option(q, q)));
  $('quiz-filter').value = quizzes.includes(selected) ? selected : '';
  $('counts').textContent = `${quizzes.length} quizzes · ${w.rows.length} questions · ${w.blocked} need attention · ${w.edited} saved changes`;
  $('draft-path').textContent = w.draft ? 'Your review is saved automatically on this computer. Use “Save review for later” to download a copy.' : '';
  $('download-last').hidden = !state.download_url;
  const problems = w.rows.filter(r => r.errors.length);
  $('remaining-title').textContent = w.global_errors.length ? 'The course files need attention' : problems.length ? `${problems.length} question${problems.length === 1 ? '' : 's'} to resolve before downloading` : 'Your fixed ZIP is ready to download';
  $('remaining-help').textContent = problems.length || w.global_errors.length ? 'You do not need to approve every question. Fix the problems below, and choose a version only when asked. Layout notes are optional.' : 'No blocking problems remain. Optional layout notes are still available; Brightspace appearance and grading need a separate check.';
  $('remaining-list').replaceChildren(...w.global_errors.map(t => node('li', t)));
  const groups = new Map();
  for (const row of problems) {
    if (row.needs_decision) groups.set('Choose the question version to keep', (groups.get('Choose the question version to keep') || 0) + 1);
    const reason = row.errors.find(t => !t.startsWith('Choose which question') && !t.includes('Review the question before exporting.'));
    if (reason) groups.set(reason, (groups.get(reason) || 0) + 1);
  }
  for (const [reason, count] of groups) $('remaining-list').append(node('li', `${count} question${count === 1 ? '' : 's'}: ${reason}`));
  if (w.image_note) $('remaining-list').append(node('li', w.image_note));
  $('show-problems').hidden = !problems.length;
  $('empty').querySelector('h2').textContent = problems.length || w.global_errors.length ? 'Choose a problem' : 'Ready to download';
  $('empty').querySelector('p').textContent = problems.length ? 'Start with a question in the list. Only unresolved problems block the download.' : 'No blocking problems remain. You can download the fixed ZIP or check optional layout notes.';
  renderList();
  if (state.error) message(state.error, true);
  else if (w.global_errors.length) message(w.global_errors.join(' '), true);
  else if (state.notice) message(state.notice);
}
function renderList() {
  if (!state) return;
  const q = $('quiz-filter').value, filter = $('issue-filter').value, search = $('search').value.trim().toLowerCase();
  const rows = state.workspace.rows.filter(r => (!q || r.quiz === q) && (!search || `${r.quiz} ${r.title} question ${r.number}`.toLowerCase().includes(search)) &&
    (filter === 'all' || filter === 'attention' && r.errors.length || filter === 'versions' && r.needs_decision || filter === 'notes' && r.warnings.length || filter === 'edited' && r.edited));
  $('list-count').textContent = `${rows.length} questions shown`;
  $('questions').replaceChildren();
  if (!rows.length) $('questions').append(node('p', 'No questions match this filter. Choose “All questions” to continue.', 'hint'));
  for (const row of rows) {
    const b = node('button', undefined, 'question-link');
    b.setAttribute('aria-label', `${row.quiz}, question ${row.number}, ${row.title}`);
    if (row.id === active) b.setAttribute('aria-current', 'true');
    b.append(node('span', `Q${row.number}`));
    const text = node('span', /^Q0*\d+$/i.test(row.title) || row.title === 'Untitled' ? '' : row.title);
    if (!q) text.append(node('small', row.quiz));
    text.append(node('small', row.errors.length ? row.needs_decision ? 'Choose a version' : 'Needs fixing' : row.reviewed ? 'Choice saved' : row.warnings.length ? 'Optional layout check' : 'No blocking problem found'));
    b.append(text); b.addEventListener('click', () => openQuestion(row.id)); $('questions').append(b);
  }
}
async function openQuestion(id, force = false) {
  if (!force && !canLeave()) return;
  buttons(true);
  try {
    detail = await api('/api/question?id=' + encodeURIComponent(id)); active = id; dirty = false;
    $('empty').hidden = true; $('question').hidden = false;
    $('quiz-title').textContent = detail.quiz; $('question-title').textContent = `Question ${detail.number}` + (/^Q0*\d+$/i.test(detail.title) || detail.title === 'Untitled' ? '' : ` · ${detail.title}`);
    $('version').replaceChildren(...detail.variants.map(v => new Option(v.label, v.id)));
    const choices = detail.variants.filter(v => v.id !== 'current');
    $('version').value = choices.find(v => v.can_use && v.id.startsWith('library:'))?.id || choices.find(v => v.can_use)?.id || choices[0]?.id || 'current';
    editingVariant = detail.selected; fillEditor(detail.record);
    const current = state.workspace.rows.find(r => r.id === id);
    $('question-state').textContent = current.errors.length ? current.needs_decision ? 'Choose a version' : 'Needs fixing' : current.reviewed ? 'Choice saved' : 'No blocking problem';
    $('question-state').classList.toggle('blocked', !!current.errors.length);
    $('findings').replaceChildren(...detail.checks[0].map(t => node('li', t)));
    $('layout-notes').hidden = !detail.checks[1].length;
    $('layout-list').replaceChildren(...detail.checks[1].map(t => node('li', t)));
    $('decision-help').textContent = current.needs_decision ? 'Pick the better copy: use the one on the left, or keep the one on the right. Either choice saves your decision.' : current.errors.length ? 'Fix the problem above before downloading. Confirming a choice cannot fix blank answers or missing pictures.' : 'No approval tick is needed for this question. Layout notes can be checked if you want.';
    $('keep').hidden = !current.needs_decision;
    $('keep').parentElement.hidden = !current.needs_decision;
    showReason('keep-reason', detail.can_keep ? '' : 'Cannot keep this question as it is: ' + (detail.checks[0][0] || 'it has a problem listed above.') + ' Edit it below, or use the other copy.');
    previewVersion(); previewWorking(); renderList();
    $('editor').open = false;
  } catch (e) { message(e.message, true); }
  finally { buttons(false); }
}
function fillEditor(record) {
  $('prompt').value = record.raw_prompt; $('context').value = record.raw_context || '';
  $('points').value = record.points; $('answers').replaceChildren();
  $('add-answer').hidden = record.kind === 'Matching' || record.kind === 'True/False';
  if (record.kind === 'Matching') {
    for (const [stem, answer] of record.pairs) $('answers').append(node('p', stem + ' → ' + answer.join(', ')));
    return;
  }
  record.choices.forEach((a, i) => addAnswer(a, i, record.kind));
}
function addAnswer(answer = {raw_html: '', correct: false}, index, kind = detail.record.kind) {
  const row = node('div', undefined, 'answer');
  const label = node('label', undefined, 'check'), check = node('input');
  check.type = kind === 'Multi-Select' ? 'checkbox' : 'radio'; check.name = 'correct-answer'; check.checked = answer.correct;
  check.dataset.correct = '1'; label.append(check, document.createTextNode(`Answer ${index + 1} is correct`));
  const text = node('textarea'); text.rows = 3; text.value = answer.raw_html ?? answer.html ?? ''; text.spellcheck = false; text.setAttribute('aria-label', `Answer ${index + 1} HTML`);
  row.append(label);
  if (kind !== 'True/False') {
    const remove = node('button', 'Remove'); remove.type = 'button'; remove.setAttribute('aria-label', `Remove answer ${index + 1}`);
    remove.addEventListener('click', () => { row.remove(); setDirty(); }); row.append(remove);
  }
  row.append(text); text.addEventListener('input', setDirty); check.addEventListener('change', setDirty); $('answers').append(row);
}
function editedRecord() {
  const record = structuredClone(detail.record);
  record.raw_prompt = $('prompt').value; record.raw_context = $('context').value;
  record.points = $('points').value === '' ? null : Number($('points').value);
  if (record.kind !== 'Matching') record.choices = [...$('answers').querySelectorAll('.answer')].map(row => ({raw_html: row.querySelector('textarea').value, correct: row.querySelector('input').checked}));
  return record;
}
function previewVersion() {
  if (!detail) return;
  const variant = detail.variants.find(v => v.id === $('version').value);
  $('source-label').textContent = variant.id === 'current' ? 'Brightspace question' : variant.id === 'reference' ? 'Original course question' : 'Saved copy in the Question Library';
  $('before').src = '/api/preview?id=' + encodeURIComponent(active) + '&variant=' + encodeURIComponent(variant.id);
  const count = variant.record.choices.length;
  $('version-note').textContent = `${count ? count + ' answers. ' : ''}${variant.problems.length ? variant.problems.join(' ') : 'Compare the wording, answers and picture placement, then choose the question to keep.'}`;
  $('use-version').disabled = requestBusy || !variant.can_use;
  showReason('use-reason', variant.can_use ? '' : 'Cannot use this copy yet: ' + (variant.problems.join(' ') || 'it has a problem.'));
  $('source-id').textContent = `Compared copy: ${variant.record.id || 'ID unavailable'}. Current exam copy: ${detail.variants.find(v => v.id === 'current')?.record.id || 'ID unavailable'}.`;
}
function previewWorking() { $('after').src = '/api/preview?id=' + encodeURIComponent(active) + '&v=' + Date.now(); }
async function addFile(field, file) {
  if (!file || requestBusy || !canLeave()) return;
  const allowed = field === 'reference' ? /\.(zip|mbz)$/i : field === 'draft' ? /\.json$/i : /\.zip$/i;
  if (!allowed.test(file.name)) { message(field === 'reference' ? 'Choose a Moodle backup (.mbz) or Brightspace ZIP.' : field === 'draft' ? 'Choose a saved review (.json).' : 'Choose a ZIP file for this box.', true); return; }
  const limit = field === 'draft' ? 32 * 1024 * 1024 : 4 * 1024 ** 3;
  if (!file.size || file.size > limit) { message('The file is empty or too large. A quiz-only export is usually much smaller.', true); return; }
  buttons(true); $('upload-status').hidden = false; $('upload-progress').value = 0;
  $('upload-text').textContent = 'Adding ' + file.name + ' on this computer…';
  message('Adding your file locally. Nothing is sent online.');
  try {
    state = await new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest(); xhr.open('POST', '/api/upload?field=' + encodeURIComponent(field));
      xhr.setRequestHeader('Content-Type', 'application/octet-stream');
      xhr.setRequestHeader('X-Studio-Request', '1');
      xhr.setRequestHeader('X-Quiz-Filename', encodeURIComponent(file.name));
      xhr.setRequestHeader('X-Quiz-Revision', String(state.revision));
      xhr.upload.onprogress = event => {
        if (event.lengthComputable) {
          const percent = Math.round(event.loaded / event.total * 100); $('upload-progress').value = percent;
          $('upload-text').textContent = `Adding ${file.name} on this computer: ${percent}%`;
        }
      };
      xhr.onload = () => { try { const data = JSON.parse(xhr.responseText); if (xhr.status >= 200 && xhr.status < 300) resolve(data); else reject(new Error(data.error || 'The file could not be added.')); } catch (e) { reject(new Error('The file could not be added. Open Quiz Repair again from MTB Writer.')); } };
      xhr.onerror = () => reject(new Error('The local file copy stopped. Keep MTB Writer open and try again.'));
      xhr.onabort = () => reject(new Error('The file copy was cancelled.'));
      xhr.send(file);
    });
    setup = field !== 'draft'; active = ''; detail = undefined; dirty = false;
    $('question').hidden = true; $('empty').hidden = false; render();
  } catch (e) { message(e.message, true); }
  finally { $('upload-status').hidden = true; buttons(false); }
}
function choose(field) { $('file-' + field).value = ''; $('file-' + field).click(); }
for (const field of ['candidate', 'reference', 'pictures', 'draft']) $('file-' + field).addEventListener('change', e => addFile(field, e.target.files[0]));
document.querySelectorAll('[data-file]').forEach(b => b.addEventListener('click', () => choose(b.dataset.file)));
document.querySelectorAll('[data-clear]').forEach(b => b.addEventListener('click', () => action('/api/clear', {field: b.dataset.clear})));
$('open-draft').addEventListener('click', () => choose('draft'));
for (const zone of document.querySelectorAll('[data-drop]')) {
  zone.addEventListener('dragover', e => { e.preventDefault(); e.stopPropagation(); if (!requestBusy) { zone.classList.add('dragging'); e.dataTransfer.dropEffect = 'copy'; } });
  zone.addEventListener('dragleave', e => { if (!zone.contains(e.relatedTarget)) zone.classList.remove('dragging'); });
  zone.addEventListener('drop', e => {
    e.preventDefault(); e.stopPropagation(); zone.classList.remove('dragging');
    if (e.dataTransfer.files.length !== 1) { message('Drop one file into each box.', true); return; }
    addFile(zone.dataset.drop, e.dataTransfer.files[0]);
  });
}
window.addEventListener('dragover', e => { if ([...e.dataTransfer.types].includes('Files')) e.preventDefault(); });
window.addEventListener('drop', e => { if ([...e.dataTransfer.types].includes('Files')) { e.preventDefault(); message('Drop the file into one of the file boxes above.', true); } });
function download(url) { const link = node('a'); link.href = url; link.download = ''; document.body.append(link); link.click(); link.remove(); }
function nextProblem() {
  const rows = state.workspace.rows.filter(r => r.errors.length);
  if (!rows.length) { message('No blocking problems remain. You can download the fixed ZIP.'); return; }
  const position = rows.findIndex(r => r.id === active);
  $('quiz-filter').value = ''; $('issue-filter').value = 'attention'; renderList();
  openQuestion(rows[(position + 1) % rows.length].id);
}
$('show-problems').addEventListener('click', nextProblem);
$('next').addEventListener('click', nextProblem);
$('save-review').addEventListener('click', () => { if (dirty) message('Save your edits before saving the review file.', true); else download('/api/review-file'); });
$('download-last').addEventListener('click', () => { if (state.download_url) download(state.download_url); });
$('load').addEventListener('click', async () => {
  if (!canLeave()) return;
  if (await action('/api/load', {}, 'Reading your local exports and checking the questions…')) {
    setup = false; active = ''; detail = undefined; dirty = false;
    $('question').hidden = true; $('empty').hidden = false; render(); buttons(false);
  }
});
$('change-files').addEventListener('click', () => { if (canLeave()) { setup = true; render(); } });
$('export').addEventListener('click', async () => {
  if (dirty) { message('Save your edits before downloading the fixed ZIP.', true); return; }
  if (await action('/api/export', {}, 'Preparing your fixed ZIP…')) download(state.download_url);
});
['quiz-filter', 'issue-filter'].forEach(id => $(id).addEventListener('change', renderList));
$('search').addEventListener('input', renderList);
$('version').addEventListener('change', previewVersion);
$('use-version').addEventListener('click', async () => {
  if (!canLeave()) return;
  const variant = $('version').value;
  if (await action('/api/edit', {id: active, variant, reviewed: true}, 'Keeping the question on the left…')) await openQuestion(active, true);
});
$('save').addEventListener('click', async () => {
  if (await action('/api/edit', {id: active, variant: editingVariant, record: editedRecord(), reviewed: true})) { dirty = false; await openQuestion(active, true); }
});
$('reset').addEventListener('click', async () => {
  if (window.confirm('Reset this question to the copy currently in the exam?')) {
    if (await action('/api/reset', {id: active})) { dirty = false; await openQuestion(active, true); }
  }
});
$('add-answer').addEventListener('click', () => { addAnswer(undefined, $('answers').children.length); setDirty(); });
['prompt', 'context', 'points'].forEach(id => $(id).addEventListener('input', setDirty));
$('keep').addEventListener('click', async () => {
  if (await action('/api/edit', {id: active, variant: editingVariant, record: editedRecord(), reviewed: true}, 'Keeping the question on the right…')) { dirty = false; await openQuestion(active, true); }
});
window.addEventListener('beforeunload', e => { if (dirty) { e.preventDefault(); e.returnValue = ''; } });
(async () => {
  try {
    state = await api('/api/state'); setup = !state.workspace.rows.length; render(); buttons(false);
    if (location.search) history.replaceState(null, '', '/');
  } catch (e) { message('Open Quiz Repair from MTB Writer to start a local review.', true); }
})();
