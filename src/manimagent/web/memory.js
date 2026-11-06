const enabledKey = 'manimagent.memoryEnabled';
const sessionKey = 'manimagent.memorySession';
const get = (storage, key) => { try { return storage?.getItem(key); } catch { return null; } };
const put = (storage, key, value) => { try { storage?.setItem(key, value); } catch { /* Keep this page usable if browser storage is unavailable. */ } };

export function createMemory({dom, api, storage, sessionStorage, crypto}) {
  const {el} = dom;
  const savedSession = get(sessionStorage, sessionKey);
  const state = {entries: [], buttons: [], busy: false, epoch: 0, pending: null,
    sessionId: /^[A-Za-z0-9_-]{1,80}$/.test(savedSession || '') ? savedSession : crypto.randomUUID()};
  put(sessionStorage, sessionKey, state.sessionId);
  el('memory-enabled').checked = get(storage, enabledKey) === 'true';

  function message(text) { el('memory-message').textContent = text; }
  function enabledChanged() { put(storage, enabledKey, String(el('memory-enabled').checked)); }
  function options() { return el('memory-enabled').checked ? {session_id: state.sessionId, scope: 'global', learn: true} : null; }
  function newSession() {
    if (state.busy) return;
    state.sessionId = crypto.randomUUID(); state.pending = null;
    put(sessionStorage, sessionKey, state.sessionId);
    render();
    message('已开始新会话。本次任务的临时记忆不再沿用，深层记忆继续保留。');
  }
  function setBusy(busy) {
    state.busy = busy;
    for (const id of ['memory-add', 'memory-cleanup', 'memory-reload', 'memory-new-session']) el(id).disabled = busy;
    for (const button of state.buttons) button.disabled = busy;
  }
  function button(label, action, className = 'secondary') {
    const node = dom.create('button'); node.type = 'button'; node.className = className; node.textContent = label;
    node.disabled = state.busy; node.addEventListener('click', action); state.buttons.push(node); return node;
  }
  function render() {
    state.buttons = [];
    const list = el('memory-list'); list.replaceChildren();
    const expired = entry => entry.expires_at && Date.parse(entry.expires_at) <= Date.now();
    const active = state.entries.filter(entry => entry.state === 'active' && !expired(entry));
    const deep = active.filter(entry => entry.layer === 'deep').length;
    el('memory-state').textContent = active.length ? `短期 ${active.length - deep} · 深层 ${deep}` : '还没有记忆';
    if (!state.entries.length) {
      const empty = dom.create('p'); empty.className = 'hint'; empty.textContent = '写下你的偏好或背景，或在生成时启用用户记忆。'; list.append(empty); return;
    }
    for (const entry of state.entries) {
      const card = dom.create('article'); card.className = 'memory-card';
      const heading = dom.create('div'); heading.className = 'memory-heading';
      const title = dom.create('strong'); title.textContent = entry.field;
      const badge = dom.create('span'); badge.className = 'memory-badge';
      badge.textContent = entry.state === 'superseded' ? '已被替代' : expired(entry) ? '已到期' : entry.temporary ? (entry.session_id === state.sessionId ? '本次会话' : '其他会话 · 临时') : entry.layer === 'deep' ? '深层记忆' : '短期记忆';
      heading.append(title, badge);
      const value = dom.create('p'); value.className = 'memory-value'; value.textContent = entry.value;
      const meta = dom.create('p'); meta.className = 'hint';
      meta.textContent = `${entry.confirmed_at ? '已由你确认' : `来自 ${entry.independent_sessions ?? 0} 个独立会话`}${entry.scope !== 'global' ? ` · 适用于 ${entry.scope}` : ''}`;
      const evidence = dom.create('details'); evidence.className = 'memory-evidence';
      const summary = dom.create('summary'); summary.textContent = '查看来源'; evidence.append(summary);
      for (const item of entry.evidence || []) {
        const quote = dom.create('blockquote'); quote.textContent = item.quote; evidence.append(quote);
      }
      const actions = dom.create('div'); actions.className = 'button-row memory-actions';
      const path = `/api/v2/memory/${encodeURIComponent(entry.memory_id)}`;
      const editable = entry.state === 'active' && !expired(entry);
      if (editable && entry.layer !== 'deep' && !entry.temporary) {
        actions.append(button('确认沉淀', () => mutate(() => api.post(path + '/confirm', {}), '已确认，后续任务可使用这条深层记忆。')));
      }
      const editor = dom.create('form'); editor.className = 'memory-editor'; editor.hidden = true;
      const label = dom.create('label'); label.textContent = '更正后的内容';
      const input = dom.create('input'); input.value = entry.value; input.maxLength = 300; input.required = true;
      input.setAttribute('aria-label', `更正 ${entry.field}`); label.append(input); editor.append(label);
      const explanation = dom.create('p'); explanation.className = 'hint'; explanation.textContent = '保存即确认这是一条长期偏好，将替代相同范围内的旧值，用于后续任务。'; editor.append(explanation);
      const save = button('确认并保存为深层记忆', () => {}); save.type = 'submit';
      const cancel = button('取消', () => { editor.hidden = true; });
      const editActions = dom.create('div'); editActions.className = 'button-row'; editActions.append(save, cancel); editor.append(editActions);
      editor.addEventListener('submit', event => {
        event.preventDefault();
        const corrected = input.value.trim();
        if (!corrected) { message('请填写更正后的内容。'); input.focus(); return; }
        return mutate(() => api.request(path, {method: 'PATCH', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({value: corrected})}), '已更正并确认为深层记忆，后续任务将使用新值。');
      });
      if (editable) actions.append(button(entry.temporary ? '改为长期偏好' : '纠正', () => { editor.hidden = !editor.hidden; if (!editor.hidden) input.focus(); }));
      actions.append(button('删除', () => mutate(() => api.request(path, {method: 'DELETE'}), '已删除，后续任务不再使用这条记忆。')));
      card.append(heading, value, meta, evidence, actions); if (editable) card.append(editor); list.append(card);
    }
  }
  async function load({preserveMessage = false} = {}) {
    const epoch = ++state.epoch;
    try {
      const data = await api.request('/api/v2/memory');
      if (epoch !== state.epoch) return false;
      state.entries = data.entries; render();
      if (!preserveMessage) message('');
      return true;
    } catch (error) {
      if (epoch === state.epoch) { el('memory-state').textContent = '读取失败'; message(`记忆读取失败：${error.message}`); }
      return false;
    }
  }
  async function mutate(action, success) {
    if (state.busy) return;
    setBusy(true); ++state.epoch; message('正在保存…');
    try {
      const result = await action();
      message(typeof success === 'function' ? success(result) : success);
      await load({preserveMessage: true});
    } catch (error) { message(error.message); }
    finally { setBusy(false); }
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
  return {state, load, observe, cleanup, newSession, enabledChanged, options};
}
