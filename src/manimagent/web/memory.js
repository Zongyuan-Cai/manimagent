import {createLearningView, hasPendingLearning} from './memory-learning.js';
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
  const state = {entries: [], buttons: [], inputs: [], policy: {}, learningTasks: null, busy: false, epoch: 0, pending: null,
    editing: new Map(), openDetails: new Set(), entriesDeferred: false, entrySignature: '', loading: 0, timer: null, polls: 0, failures: 0,
    learning: savedLearning === null || savedLearning === undefined ? get(storage, enabledKey) === 'true' : savedLearning === 'true',
    sessionId: /^[A-Za-z0-9_-]{1,80}$/.test(savedSession || '') ? savedSession : crypto.randomUUID()};
  const learningView = createLearningView({dom, onAction: learningAction});
  const files = createMemoryFiles({dom, api, importDocument: (id, content) => mutate(
    () => api.post(`/api/v2/memory/documents/${encodeURIComponent(id)}/import`, content === undefined ? {} : {content}),
    '已导入记忆文件，后续任务将使用更正后的内容。', null, true)});
  put(sessionStorage, sessionKey, state.sessionId);
  put(storage, learningKey, String(state.learning));
  el('memory-enabled').checked = get(storage, enabledKey) === 'true';
  renderOptions();

  function message(text) { el('memory-message').textContent = text; }
  function renderOptions() {
    const enabled = el('memory-enabled').checked;
    el('memory-learn').disabled = !enabled;
    el('memory-learn').checked = enabled && state.learning;
    el('memory-mode').textContent = !enabled ? '本次不使用记忆。' : state.learning ? '使用已有记忆，完成后记住新偏好。' : '只使用已有记忆。';
  }
  function enabledChanged() { put(storage, enabledKey, String(el('memory-enabled').checked)); renderOptions(); }
  function learningChanged() {
    if (!el('memory-enabled').checked) return;
    state.learning = el('memory-learn').checked; put(storage, learningKey, String(state.learning)); renderOptions();
  }
  function options() { return el('memory-enabled').checked ? {session_id: state.sessionId, scope: 'global', learn: state.learning} : null; }
  function newSession() {
    if (state.busy) return;
    if (state.editing.size) { message('请先保存或取消正在编辑的记忆，再开始新会话。'); return; }
    state.sessionId = crypto.randomUUID(); state.pending = null;
    put(sessionStorage, sessionKey, state.sessionId);
    render();
    message('已开始新会话。原会话的临时记忆不再沿用，有效期内的近期偏好与深层记忆继续保留。');
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
    const active = state.entries.filter(entry => entry.state === 'active' && !expired(entry));
    const deep = active.filter(entry => entry.layer === 'deep').length;
    el('memory-state').textContent = active.length ? `近期 ${active.length - deep} · 长期 ${deep}` : '还没有记忆';
    if (!state.entries.length) {
      const empty = dom.create('p'); empty.className = 'hint'; empty.textContent = '写下你的偏好或背景，或在生成时启用用户记忆。'; list.append(empty); return;
    }
    for (const entry of state.entries) {
      const card = dom.create('article'); card.className = 'memory-card';
      const heading = dom.create('div'); heading.className = 'memory-heading';
      const title = dom.create('strong'); title.className = 'memory-card-title'; title.textContent = entry.value;
      const badge = dom.create('span'); badge.className = 'memory-badge';
      badge.textContent = entry.state === 'superseded' ? '已被替代' : expired(entry) ? '已到期' : entry.temporary ? (entry.session_id === state.sessionId ? '本次会话' : '其他会话 · 临时') : entry.layer === 'deep' ? '长期' : '近期';
      heading.append(title, badge);
      const meta = dom.create('p'); meta.className = 'hint';
      const threshold = state.policy.promotion_sessions || 3;
      meta.textContent = `${entry.confirmed_at ? '已由你确认' : entry.temporary ? '临时要求，只用于原会话，不自动晋升' : entry.layer === 'deep' ? `自动沉淀 · ${entry.independent_sessions ?? 0} 个独立会话支持` : `晋升进度：独立会话 ${entry.independent_sessions ?? 0}/${threshold}`}${entry.scope !== 'global' ? ` · 适用于 ${entry.scope}` : ''}`;
      const evidence = dom.create('details'); evidence.className = 'memory-evidence';
      evidence.open = state.openDetails.has(entry.memory_id);
      evidence.addEventListener('toggle', () => { if (evidence.open) state.openDetails.add(entry.memory_id); else state.openDetails.delete(entry.memory_id); });
      const summary = dom.create('summary'); summary.textContent = '详情与来源'; evidence.append(summary);
      const field = dom.create('p'); field.textContent = `字段：${entry.field}`;
      const scope = dom.create('p'); scope.textContent = `范围：${entry.scope || 'global'}`;
      const identity = dom.create('p'); identity.textContent = `记录：${entry.memory_id}`;
      evidence.append(meta, field, scope, identity);
      for (const [label, raw] of [['更新于', entry.updated_at], ['有效至', entry.expires_at]]) {
        if (!raw) continue;
        const date = new Date(raw), text = dom.create('p');
        text.textContent = `${label} ${Number.isNaN(date.getTime()) ? raw : date.toLocaleString()}`; evidence.append(text);
      }
      for (const item of entry.evidence || []) {
        const quote = dom.create('blockquote'); quote.textContent = item.quote; evidence.append(quote);
      }
      const actions = dom.create('div'); actions.className = 'button-row memory-actions';
      const path = `/api/v2/memory/${encodeURIComponent(entry.memory_id)}`;
      const editable = entry.state === 'active' && !expired(entry);
      if (editable && entry.layer !== 'deep' && !entry.temporary) {
        actions.append(button('确认沉淀', () => mutate(() => api.post(path + '/confirm', {}), '已确认，后续任务可使用这条深层记忆。', entry.memory_id)));
      }
      const editor = dom.create('form'); editor.className = 'memory-editor'; editor.hidden = true;
      const label = dom.create('label'); label.textContent = '更正后的内容';
      const input = dom.create('input'); input.value = entry.value; input.maxLength = 300; input.required = true;
      input.disabled = state.busy; state.inputs.push(input);
      input.setAttribute('aria-label', `更正 ${entry.field}`); label.append(input); editor.append(label);
      const explanation = dom.create('p'); explanation.className = 'hint'; editor.append(explanation);
      const save = button('保存更正', () => {}); save.type = 'submit';
      let confirmLongTerm = false;
      function openEditor(confirm = false) {
        state.editing.set(entry.memory_id, editor);
        confirmLongTerm = confirm;
        explanation.textContent = confirm ? '保存即确认这是一条长期偏好，将替代相同范围内的旧值，用于后续任务。' : entry.temporary ? '只更正这条临时记忆，仍限原会话使用，保留原到期时间。' : entry.layer === 'deep' ? '更正后仍为深层记忆，后续任务使用新值。' : '只更正这条短期记忆，保留原到期时间，不会自动沉淀为深层记忆。';
        save.textContent = confirm ? '确认并保存为深层记忆' : '保存更正';
        editor.hidden = false; input.focus();
      }
      const cancel = button('取消', () => closeEditor(entry.memory_id, editor));
      const editActions = dom.create('div'); editActions.className = 'button-row'; editActions.append(save, cancel); editor.append(editActions);
      editor.addEventListener('submit', event => {
        event.preventDefault();
        const corrected = input.value.trim();
        if (!corrected) { message('请填写更正后的内容。'); input.focus(); return; }
        return mutate(() => api.request(path, {method: 'PATCH', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({value: corrected, confirm_long_term: confirmLongTerm})}), confirmLongTerm ? '已更正并确认为深层记忆，后续任务将使用新值。' : '已更正内容，记忆层级和有效期保持不变。', entry.memory_id);
      });
      if (editable) {
        actions.append(button('纠正', () => openEditor()));
        if (entry.temporary) actions.append(button('改为长期偏好', () => openEditor(true)));
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
      const signature = JSON.stringify([data.entries, data.policy || {}]);
      const changed = signature !== state.entrySignature;
      state.entrySignature = signature; state.failures = 0; state.entries = data.entries; state.policy = data.policy || {};
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
  async function mutate(action, success, closedEditorId = null, propagate = false) {
    if (state.busy) return;
    setBusy(true); ++state.epoch; message('正在保存…');
    try {
      const result = await action();
      if (state.editing.has(closedEditorId)) { state.editing.get(closedEditorId).hidden = true; state.editing.delete(closedEditorId); }
      message(typeof success === 'function' ? success(result) : success);
      await load({preserveMessage: true});
      return result;
    } catch (error) { message(error.message); if (propagate) throw error; }
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
