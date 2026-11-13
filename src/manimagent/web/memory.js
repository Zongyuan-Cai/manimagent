import {createMemoryFiles} from './memory-files.js';

const enabledKey = 'manimagent.memoryEnabled';
const learningKey = 'manimagent.memoryLearning';
const sessionKey = 'manimagent.memorySession';
const get = (storage, key) => { try { return storage?.getItem(key); } catch { return null; } };
const put = (storage, key, value) => { try { storage?.setItem(key, value); } catch { /* Keep this page usable if browser storage is unavailable. */ } };

export function createMemory({dom, api, storage, sessionStorage, crypto, scheduler = globalThis}) {
  const {el} = dom;
  const savedSession = get(sessionStorage, sessionKey);
  const savedLearning = get(storage, learningKey);
  const state = {entries: [], fields: [], buttons: [], inputs: [], policy: {}, learningTasks: null, busy: false, epoch: 0, pending: null,
    editing: new Map(), openDetails: new Set(), entriesDeferred: false, entrySignature: '', loading: 0, timer: null, polls: 0, failures: 0,
    learning: savedLearning === 'true',
    sessionId: /^[A-Za-z0-9_-]{1,80}$/.test(savedSession || '') ? savedSession : crypto.randomUUID()};
  const learningView = createLearningView({dom, onAction: learningAction});
  const files = createMemoryFiles({dom, api});
  put(sessionStorage, sessionKey, state.sessionId);
  put(storage, learningKey, String(state.learning));
  el('memory-enabled').checked = get(storage, enabledKey) === 'true';
  renderOptions();

  function message(text) { el('memory-message').textContent = text; }
  function renderOptions() {
    const enabled = el('memory-enabled').checked;
    el('memory-learn').disabled = false;
    el('memory-learn').checked = state.learning;
    el('memory-mode').textContent = enabled ? state.learning ? '使用已有记忆，并保存新的可复用偏好。' : '只使用已有记忆。' : state.learning ? '本次不读取已有记忆；新的偏好可保存供以后使用。' : '本次不读取或保存记忆，仍遵循当前要求。';
  }
  function enabledChanged() { put(storage, enabledKey, String(el('memory-enabled').checked)); renderOptions(); }
  function learningChanged() {
    state.learning = el('memory-learn').checked; put(storage, learningKey, String(state.learning)); renderOptions();
  }
  function options() {
    const read = el('memory-enabled').checked;
    return read || state.learning ? {session_id: state.sessionId, scope: 'global', read, learn: state.learning} : null;
  }
  function newSession() {
    if (state.busy) return;
    if (state.editing.size) { message('请先保存或取消正在编辑的记忆，再开始新会话。'); return; }
    state.sessionId = crypto.randomUUID(); state.pending = null;
    put(sessionStorage, sessionKey, state.sessionId);
    render();
    message('已开始新会话。已有的有效偏好继续保留；仅本次要求不会成为跨任务记忆。');
  }
  function setBusy(busy) {
    state.busy = busy;
    for (const id of ['memory-add', 'memory-cleanup', 'memory-reload', 'memory-learning-reload', 'memory-new-session']) el(id).disabled = busy;
    for (const button of state.buttons) button.disabled = busy;
    learningView.setBusy(busy);
    files.setBusy(busy);
    for (const input of state.inputs) input.disabled = busy;
  }
  function button(label, action) {
    const node = dom.create('button'); node.type = 'button'; node.className = 'secondary'; node.textContent = label;
    node.disabled = state.busy; node.addEventListener('click', action); state.buttons.push(node); return node;
  }
  function closeEditor(identifier, editor) {
    editor.hidden = true; state.editing.delete(identifier);
    if (!state.editing.size && state.entriesDeferred) render();
  }
  function render() {
    if (state.editing.size) {
      state.entriesDeferred = true; el('memory-sync').textContent = '后台状态已更新；保存或取消编辑后再更新记忆列表，当前输入会保留。'; return;
    }
    state.entriesDeferred = false; el('memory-sync').textContent = '';
    state.buttons = []; state.inputs = [];
    const list = el('memory-list'); list.replaceChildren();
    const present = new Set(state.entries.map(entry => entry.memory_id));
    for (const id of state.openDetails) if (!present.has(id)) state.openDetails.delete(id);
    const expired = entry => entry.expires_at && Date.parse(entry.expires_at) <= Date.now();
    const active = state.entries.filter(entry => entry.state === 'active' && !expired(entry) && !entry.temporary && state.fields.some(field => field.field === entry.field && field.values.some(value => value.value === entry.value)));
    const backgrounds = active.filter(entry => entry.field === 'profile.background').length;
    const deep = active.filter(entry => entry.layer === 'deep' && entry.field !== 'profile.background').length;
    el('memory-state').textContent = active.length ? `近期 ${active.length - deep - backgrounds} · 长期 ${deep}${backgrounds ? ` · 自述 ${backgrounds}` : ''}` : '还没有记忆';
    if (!state.entries.length) {
      const empty = dom.create('p'); empty.className = 'hint'; empty.textContent = '写下你的偏好或背景，或在生成时启用用户记忆。'; list.append(empty); return;
    }
    for (const entry of state.entries) {
      const card = dom.create('article'); card.className = 'memory-card';
      const heading = dom.create('div'); heading.className = 'memory-heading';
      const definition = state.fields.find(field => field.field === entry.field);
      const supported = definition?.values.some(option => option.value === entry.value);
      const background = entry.field === 'profile.background';
      const title = dom.create('strong'); title.className = 'memory-card-title';
      title.textContent = definition?.values.find(option => option.value === entry.value)?.label || entry.value;
      const badge = dom.create('span'); badge.className = 'memory-badge';
      badge.textContent = entry.state === 'superseded' ? '已被替代' : expired(entry) ? '已到期' : entry.temporary || !supported ? '历史记录 · 不再应用' : background ? '自述背景' : entry.layer === 'deep' ? '长期' : '近期';
      heading.append(title, badge);
      const meta = dom.create('p'); meta.className = 'hint';
      const threshold = state.policy.promotion_sessions || 3;
      meta.textContent = `${background ? '自述记录，不代表能力或掌握度；重复表达不会自动晋升，确认后仍按有效期使用。' : entry.confirmed_at ? '已由你确认' : entry.temporary ? '历史临时要求，不再跨任务应用' : entry.layer === 'deep' ? `自动沉淀 · ${entry.independent_sessions ?? 0} 个独立会话支持` : `晋升进度：独立会话 ${entry.independent_sessions ?? 0}/${threshold}`}${entry.scope !== 'global' ? ` · 适用于 ${scopeLabel(entry.scope)}` : ''}`;
      const evidence = dom.create('details'); evidence.className = 'memory-evidence';
      evidence.open = state.openDetails.has(entry.memory_id);
      evidence.addEventListener('toggle', () => { if (evidence.open) state.openDetails.add(entry.memory_id); else state.openDetails.delete(entry.memory_id); });
      const summary = dom.create('summary'); summary.textContent = '详情与来源'; evidence.append(summary);
      const field = dom.create('p'); field.textContent = `字段：${definition?.label || entry.field}`;
      const scope = dom.create('p'); scope.textContent = `范围：${scopeLabel(entry.scope)}`;
      const identity = dom.create('p'); identity.textContent = `记录：${entry.memory_id}`;
      evidence.append(meta, field, scope, identity);
      for (const [label, raw] of [[background ? '记录于' : '更新于', entry.updated_at], ['有效至', entry.expires_at]]) {
        if (!raw) continue;
        const date = new Date(raw), text = dom.create('p');
        text.textContent = `${label} ${Number.isNaN(date.getTime()) ? raw : date.toLocaleString()}`; evidence.append(text);
      }
      for (const item of entry.evidence || []) {
        const quote = dom.create('blockquote'); quote.textContent = item.quote; evidence.append(quote);
      }
      const actions = dom.create('div'); actions.className = 'button-row memory-actions';
      const path = `/api/v2/memory/${encodeURIComponent(entry.memory_id)}`;
      const editable = entry.state === 'active' && !expired(entry) && supported && !entry.temporary;
      if (editable && entry.layer !== 'deep' && !entry.temporary) {
        actions.append(button(background ? '确认此自述' : '确认沉淀', () => mutate(() => api.post(path + '/confirm', {}), background ? '已确认此自述，仍按记录的有效期使用。' : '已确认为长期默认，后续任务可使用这条偏好。', entry.memory_id)));
      }
      const editor = dom.create('form'); editor.className = 'memory-editor'; editor.hidden = true;
      const label = dom.create('label'); label.textContent = definition?.label || entry.field;
      const input = dom.create('select'); input.required = true;
      for (const value of definition?.values || []) {
        const option = dom.create('option'); option.value = value.value; option.textContent = value.label; input.append(option);
      }
      input.value = entry.value;
      input.disabled = state.busy; state.inputs.push(input);
      input.setAttribute('aria-label', `更正 ${entry.field}`); label.append(input); editor.append(label);
      const explanation = dom.create('p'); explanation.className = 'hint'; editor.append(explanation);
      const save = button('保存更正', () => {}); save.type = 'submit';
      function openEditor() {
        state.editing.set(entry.memory_id, editor);
        explanation.textContent = background ? '保存纠正后的自述，替代同范围旧记录；记录观察日期，按有效期使用。' : '保存即确认新的默认偏好，替代相同范围内的旧值，用于后续任务。';
        editor.hidden = false; input.focus();
      }
      const cancel = button('取消', () => closeEditor(entry.memory_id, editor));
      const editActions = dom.create('div'); editActions.className = 'button-row'; editActions.append(save, cancel); editor.append(editActions);
      editor.addEventListener('submit', event => {
        event.preventDefault();
        const corrected = input.value.trim();
        if (!definition?.values.some(value => value.value === corrected)) { message('请选择受支持的偏好。'); input.focus(); return; }
        return mutate(() => api.request(path, {method: 'PATCH', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({value: corrected, confirm_long_term: true})}), background ? '已更正自述，仍按记录的有效期使用。' : '已更正默认偏好，后续任务将使用新值。', entry.memory_id);
      });
      if (editable) {
        actions.append(button('纠正', () => openEditor()));
      }
      actions.append(button('删除', () => mutate(() => api.request(path, {method: 'DELETE'}), '已删除，后续任务不再使用这条记忆。', entry.memory_id)));
      card.append(heading, actions, evidence); if (editable) card.append(editor); list.append(card);
    }
  }
  function learningAction(jobId, action) {
    const path = `/api/v2/memory/learning/${encodeURIComponent(jobId)}/${action}`;
    return mutate(() => api.post(path, {}), action === 'retry' ? '已请求重试，等待后台沉淀。' : result => result.state === 'completed' ? '任务已完成，已写入的记忆没有撤回；可在下方纠正或删除。' : '已取消此沉淀任务。');
  }
  function schedulePoll() {
    scheduler.clearTimeout(state.timer); state.timer = null;
    if (!hasPendingLearning(state.learningTasks)) return;
    if (state.failures >= 3 || state.polls >= 120) {
      el('memory-learning-sync').textContent = '自动刷新已暂停，点击“刷新沉淀状态”继续；后台任务仍按服务端状态运行。'; return;
    }
    state.timer = scheduler.setTimeout(async () => {
      state.timer = null; state.polls++;
      if (state.busy || state.loading) { schedulePoll(); return; }
      await load({preserveMessage: true, background: true});
    }, 5000);
  }
  async function load({preserveMessage = false, background = false} = {}) {
    if (!background) { state.polls = 0; state.failures = 0; }
    scheduler.clearTimeout(state.timer); state.timer = null;
    const epoch = ++state.epoch;
    state.loading++;
    try {
      const data = await api.request('/api/v2/memory');
      if (epoch !== state.epoch) return false;
      const signature = JSON.stringify([data.entries, data.fields || [], data.policy || {}]);
      const changed = signature !== state.entrySignature;
      state.entrySignature = signature; state.failures = 0; state.entries = data.entries; state.fields = data.fields || []; state.policy = data.policy || {};
      state.learningTasks = Array.isArray(data.learning?.tasks) ? data.learning : null;
      if (changed || !background) render();
      learningView.render(state.learningTasks); el('memory-learning-sync').textContent = hasPendingLearning(state.learningTasks) ? '正在自动刷新沉淀状态。' : '';
      if (!preserveMessage && !state.editing.size) message('');
      return true;
    } catch (error) {
      if (epoch === state.epoch) {
        state.failures++; el('memory-learning-sync').textContent = '沉淀状态刷新失败，稍后重试；后台状态暂时未知。';
        if (!background) { el('memory-state').textContent = '读取失败'; message(`记忆读取失败：${error.message}`); }
      }
      return false;
    } finally { state.loading--; if (epoch === state.epoch) schedulePoll(); }
  }
  async function mutate(action, success, closedEditorId = null) {
    if (state.busy) return;
    setBusy(true); ++state.epoch; message('正在保存…');
    try {
      const result = await action();
      if (state.editing.has(closedEditorId)) { state.editing.get(closedEditorId).hidden = true; state.editing.delete(closedEditorId); }
      message(typeof success === 'function' ? success(result) : success);
      await load({preserveMessage: true});
      return result;
    } catch (error) { message(error.message); }
    finally { setBusy(false); schedulePoll(); }
  }
  async function observe(event) {
    event?.preventDefault();
    if (state.busy) return;
    const text = el('memory-text').value.trim();
    if (!text) { message('请先写下希望记住的内容。'); return; }
    // A retry of the same observation keeps its source ID and cannot create new evidence.
    if (state.pending?.text !== text || state.pending.session_id !== state.sessionId) {
      state.pending = {text, session_id: state.sessionId, source_id: crypto.randomUUID(), scope: 'global'};
    }
    const pending = {...state.pending};
    return mutate(() => api.post('/api/v2/memory/observations', pending), result => {
      const warnings = (result.warnings || []).map(item => typeof item === 'string' ? item : item.message).filter(Boolean);
      return [result.entries.length ? `已处理 ${result.entries.length} 条记忆，请核对内容与来源。` : '这段表达没有生成可保存的用户记忆。', ...warnings].join(' ');
    });
  }
  function cleanup() { return mutate(() => api.post('/api/v2/memory/cleanup', {}), counts => `已清理 ${(counts.expired || 0) + (counts.superseded || 0) + (counts.evicted || 0)} 条记忆，保留 ${counts.remaining ?? 0} 条。`); }
  return {state, files, load, observe, cleanup, newSession, enabledChanged, learningChanged, options};
}

const pendingStates = new Set(['waiting', 'queued', 'running']);
const labels = {waiting: '等待请求解释', queued: '待保存', running: '保存中', completed: '已完成', failed: '失败', skipped: '已跳过', cancelled: '已取消'};

function scopeLabel(scope) {
  if (scope === 'global') return '所有课程';
  const parts = (scope || '').split('.');
  if (parts[0] === 'course' && parts.length === 2) return `课程 ${parts[1]}`;
  if (parts[0] === 'topic' && parts.length === 3) return `课程 ${parts[1]} · 主题 ${parts[2]}`;
  return scope || '范围未知';
}

function hasPendingLearning(learning) {
  return !!learning && ([...pendingStates].some(name => learning.counts?.[name] > 0) || learning.tasks.some(task => pendingStates.has(task.state)));
}

// This view owns only its controls and disclosure state. Requests and polling stay
// in the memory controller so task actions share the same mutation lock as edits.
function createLearningView({dom, onAction}) {
  const {el} = dom, openDetails = new Set();
  let buttons = [], busy = false;
  function setBusy(value) { busy = value; for (const button of buttons) button.disabled = value; }
  function actionButton(label, task, action) {
    const button = dom.create('button'); button.type = 'button'; button.className = 'secondary'; button.textContent = label;
    button.disabled = busy; button.addEventListener('click', () => onAction(task.job_id, action)); buttons.push(button); return button;
  }
  function render(learning) {
    const list = el('memory-learning-list'), scroll = list.scrollTop;
    buttons = []; list.replaceChildren();
    if (!learning) { el('memory-learning-summary').textContent = '后台沉淀状态暂不可用。'; return; }
    const present = new Set(learning.tasks.map(task => task.job_id));
    for (const id of openDetails) if (!present.has(id)) openDetails.delete(id);
    const counts = learning.counts || {};
    const pending = [...pendingStates].reduce((total, name) => total + (counts[name] || 0), 0) || learning.tasks.filter(task => pendingStates.has(task.state)).length;
    const failed = counts.failed || learning.tasks.filter(task => task.state === 'failed').length;
    el('memory-learning-summary').textContent = pending ? `${pending} 项偏好正在处理。` : failed ? `${failed} 项偏好未保存，可查看原因。` : learning.tasks.some(task => task.state === 'completed') ? '偏好更新已完成。' : learning.tasks.length ? '暂无进行中的偏好更新。' : '暂无偏好更新。';
    for (const task of learning.tasks.slice(0, 20)) {
      const card = dom.create('article'); card.className = 'memory-learning-card';
      const heading = dom.create('div'); heading.className = 'memory-heading';
      const title = dom.create('strong'); title.textContent = '偏好更新';
      const badge = dom.create('span'); badge.className = 'memory-badge'; badge.setAttribute('data-state', task.state);
      badge.textContent = labels[task.state] || '状态未知'; heading.append(title, badge);
      const result = dom.create('p'); result.className = 'hint';
      result.textContent = task.state === 'failed' ? (task.can_retry ? '偏好尚未保存，可以重试。' : '偏好尚未保存，请查看详情。') : task.state === 'completed' ? (task.counts_known === false ? '请在记忆列表核对结果。' : task.entry_ids?.length ? `已更新 ${task.entry_ids.length} 条偏好。` : '没有需要保存的新偏好。') : '';
      const outcome = dom.create('p');
      outcome.textContent = task.state === 'completed' ? (task.entry_ids?.length ? `已处理 ${task.entry_ids.length} 条记忆，其中 ${task.promoted_ids?.length || 0} 条自动沉淀为深层。` : '没有需要保存的新偏好。') : task.state === 'waiting' ? '请求解释通过后保存合法偏好，不等待文字或视频生成。' : task.state === 'queued' ? '已验证候选，等待保存。' : task.state === 'running' ? '正在按原句证据更新偏好，后台不再次调用模型。' : task.state === 'cancelled' ? '已取消此保存任务。' : task.state === 'skipped' ? '本次未保存偏好。' : '偏好保存未完成；生成结果与保存状态分别报告。';
      if (task.state === 'completed' && task.counts_known === false) outcome.textContent = '沉淀已完成；本次新增或强化的统计未知，请核对记忆列表。';
      else if (task.state === 'completed' && task.counts_known && task.counts) outcome.textContent = `新增 ${task.counts.created} 条，强化 ${task.counts.reinforced} 条，自动沉淀为深层 ${task.counts.promoted} 条。`;
      const details = dom.create('details'); details.className = 'memory-evidence'; details.open = openDetails.has(task.job_id);
      details.addEventListener('toggle', () => { if (details.open) openDetails.add(task.job_id); else openDetails.delete(task.job_id); });
      const summary = dom.create('summary'); summary.textContent = '查看任务详情'; details.append(summary);
      const identity = dom.create('p'); identity.textContent = `任务 ${task.job_id} · 已尝试 ${task.attempts ?? 0}/${task.max_attempts ?? 3} 次`;
      const timestamp = dom.create('p'), date = new Date(task.updated_at || task.created_at || '');
      timestamp.textContent = Number.isNaN(date.getTime()) ? '更新时间未知' : `更新于 ${date.toLocaleString()}`;
      details.append(outcome, identity, timestamp);
      if (task.error?.message) { const error = dom.create('p'); error.className = task.state === 'failed' ? 'error-text' : 'hint'; error.textContent = task.error.message; details.append(error); }
      const actions = dom.create('div'); actions.className = 'button-row memory-actions';
      if (task.can_retry === true) actions.append(actionButton('重试沉淀', task, 'retry'));
      if (task.can_cancel === true) actions.append(actionButton('取消沉淀', task, 'cancel'));
      card.append(heading); if (result.textContent) card.append(result); if (actions.children.length) card.append(actions); card.append(details); list.append(card);
    }
    list.scrollTop = scroll;
  }
  return {render, setBusy};
}
