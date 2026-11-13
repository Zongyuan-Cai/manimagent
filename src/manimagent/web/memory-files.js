const labels = {current: '当前导出', edited: '本地副本已修改 · 不影响有效记忆', stale: '历史副本 · 不影响有效记忆', deleted: '已移除记忆的历史副本'};
const base = '/api/v2/memory/documents';

export function createMemoryFiles({dom, api}) {
  const {el} = dom;
  const state = {loaded: false, busy: false, parentBusy: false, selected: null, buttons: [], openDetails: new Set()};
  const busy = () => state.busy || state.parentBusy;
  el('memory-file-content').readOnly = true;
  function message(text) { el('memory-files-message').textContent = text; }
  function controls() {
    el('memory-files-reload').disabled = busy();
    el('memory-file-close').disabled = busy();
    for (const button of state.buttons) button.disabled = busy();
  }
  function setBusy(value) { state.parentBusy = value; controls(); }
  function render(data) {
    const rows = data.entries; state.buttons = [];
    const present = new Set(rows.map(row => row.memory_id));
    for (const id of state.openDetails) if (!present.has(id)) state.openDetails.delete(id);
    el('memory-files-root').textContent = data.root || '';
    el('memory-files-index').textContent = data.index_content || '';
    const list = el('memory-files-list'); list.replaceChildren();
    for (const row of rows) {
      const card = dom.create('article'); card.className = 'memory-card';
      const title = dom.create('strong'); title.textContent = row.field || '已移除的记忆';
      const status = dom.create('p'); status.className = 'hint'; status.textContent = labels[row.status] || '状态未知';
      const path = dom.create('p'); path.className = 'memory-file-path'; path.textContent = row.path;
      const details = dom.create('details'); details.className = 'memory-file-details'; details.open = state.openDetails.has(row.memory_id);
      details.addEventListener('toggle', () => { if (details.open) state.openDetails.add(row.memory_id); else state.openDetails.delete(row.memory_id); });
      const summary = dom.create('summary'); summary.textContent = '文件详情';
      const identity = dom.create('p'); identity.className = 'memory-file-path'; identity.textContent = row.memory_id;
      details.append(summary, identity, path);
      const actions = dom.create('div'); actions.className = 'button-row memory-actions';
      const view = dom.create('button'); view.type = 'button'; view.className = 'secondary'; view.textContent = '查看 Markdown';
      view.addEventListener('click', () => select(row.memory_id)); state.buttons.push(view); actions.append(view);
      const download = dom.create('a'); download.textContent = '下载文件'; download.href = `${base}/${encodeURIComponent(row.memory_id)}?download=true`; actions.append(download);
      card.append(title, status, actions, details); list.append(card);
    }
    if (!rows.length) { const empty = dom.create('p'); empty.className = 'hint'; empty.textContent = '还没有可导出的记忆。'; list.append(empty); }
    if (data.error) message(`文件导出未完成：${data.error.message}。已提交的记忆仍保存在本机，请修复后刷新。`);
    else message(`已读取 ${rows.length} 个记忆文件。文件仅供查看和导出；请通过上方管理操作更改偏好。`);
  }
  async function load(refresh = false) {
    if (busy()) return;
    state.busy = true; controls();
    try {
      render(refresh ? await api.post(`${base}/sync`, {}) : await api.request(base)); state.loaded = true;
      if (refresh) resetViewer();
    } catch (error) { message(`无法读取记忆文件：${error.message}`); }
    finally { state.busy = false; controls(); }
  }
  async function select(id) {
    if (busy()) return;
    state.busy = true; controls();
    try {
      const result = await api.request(`${base}/${encodeURIComponent(id)}`);
      state.selected = id;
      el('memory-file-content').value = result.content;
      el('memory-file-title').textContent = `${id}.md`;
      el('memory-file-editor').hidden = false;
      message('只读导出副本。更改偏好请使用上方的确认、纠正或删除操作。');
    } catch (error) { message(error.message); }
    finally { state.busy = false; controls(); }
  }
  function resetViewer() {
    state.selected = null;
    el('memory-file-content').value = ''; el('memory-file-editor').hidden = true;
  }
  function close() {
    if (busy()) return;
    resetViewer(); message('已关闭文件预览。'); controls();
  }
  el('memory-files').addEventListener('toggle', () => { if (el('memory-files').open && !state.loaded) return load(); });
  el('memory-files-reload').addEventListener('click', () => load(true));
  el('memory-file-close').addEventListener('click', close);
  return {state, load, select, close, setBusy};
}
