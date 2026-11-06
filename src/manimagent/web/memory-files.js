const labels = {current: '已同步', edited: '本地已修改 · 待导入', stale: '版本冲突 · 保留已检测到的本地修改', deleted: '记忆已移除 · 保留已检测到的本地修改'};
const base = '/api/v2/memory/documents';

export function createMemoryFiles({dom, api, importDocument}) {
  const {el} = dom;
  const state = {loaded: false, busy: false, parentBusy: false, selected: null, original: '', dirty: false, buttons: [], openDetails: new Set()};
  const busy = () => state.busy || state.parentBusy;
  function message(text) { el('memory-files-message').textContent = text; }
  function controls() {
    el('memory-files-reload').disabled = busy();
    el('memory-file-content').disabled = busy();
    el('memory-file-save').disabled = busy() || !state.selected;
    el('memory-file-close').disabled = busy();
    for (const button of state.buttons) button.disabled = busy() || button.blocked;
  }
  function setBusy(value) { state.parentBusy = value; controls(); }
  function button(text, callback, blocked = false) {
    const node = dom.create('button'); node.type = 'button'; node.className = 'secondary'; node.textContent = text;
    node.blocked = blocked; node.disabled = busy() || blocked;
    node.addEventListener('click', callback); state.buttons.push(node); return node;
  }
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
      if (row.status === 'stale' || row.status === 'deleted') status.textContent += '。请核对上方当前记忆，通过“纠正”处理需要保留的内容。';
      const path = dom.create('p'); path.className = 'memory-file-path'; path.textContent = row.path;
      const details = dom.create('details'); details.className = 'memory-file-details'; details.open = state.openDetails.has(row.memory_id);
      details.addEventListener('toggle', () => { if (details.open) state.openDetails.add(row.memory_id); else state.openDetails.delete(row.memory_id); });
      const summary = dom.create('summary'); summary.textContent = '文件详情';
      const identity = dom.create('p'); identity.className = 'memory-file-path'; identity.textContent = row.memory_id;
      details.append(summary, identity, path);
      const actions = dom.create('div'); actions.className = 'button-row memory-actions';
      actions.append(button('查看 / 编辑 Markdown', () => select(row.memory_id)));
      const download = dom.create('a'); download.textContent = '下载文件'; download.href = `${base}/${encodeURIComponent(row.memory_id)}?download=true`; actions.append(download);
      actions.append(button('导入本地修改', () => importDisk(row.memory_id), row.status !== 'edited'));
      card.append(title, status, actions, details); list.append(card);
    }
    if (!rows.length) { const empty = dom.create('p'); empty.className = 'hint'; empty.textContent = '还没有记忆文件。生成记忆后会自动导出。'; list.append(empty); }
    if (data.error) message(`文件同步未完成：${data.error.message}。已提交的记忆仍保存在本机，请修复后刷新。`);
    else message(state.dirty ? '文件状态已刷新，页面中的未导入修改保持不变。' : `已读取 ${rows.length} 个记忆文件。修改文件后需要显式导入才能用于后续任务。`);
  }
  async function load(refresh = false) {
    if (busy()) return;
    state.busy = true; controls();
    try {
      render(refresh ? await api.post(`${base}/sync`, {}) : await api.request(base)); state.loaded = true;
    } catch (error) { message(`无法读取记忆文件：${error.message}`); }
    finally { state.busy = false; controls(); }
  }
  async function select(id) {
    if (busy()) return;
    if (state.dirty) { message('请先导入页面修改，或关闭编辑后再打开另一个文件。'); return; }
    state.busy = true; controls();
    try {
      const result = await api.request(`${base}/${encodeURIComponent(id)}`);
      state.selected = id; state.original = result.content; state.dirty = false;
      el('memory-file-content').value = result.content;
      el('memory-file-title').textContent = `${id}.md`;
      el('memory-file-editor').hidden = false;
      message('只修改“## 记忆内容”中的正文；元信息与证据保持原样。保存导入会检查版本，保留记忆层级和有效期。');
    } catch (error) { message(error.message); }
    finally { state.busy = false; controls(); }
  }
  function close() {
    if (busy()) return;
    state.selected = null; state.original = ''; state.dirty = false;
    el('memory-file-content').value = ''; el('memory-file-editor').hidden = true;
    message('已关闭页面编辑。本地文件的修改仍保留，尚未导入的内容不会生效。'); controls();
  }
  async function apply(id, content) {
    if (busy()) return;
    state.busy = true; controls();
    let committed = false;
    try {
      await importDocument(id, content);
      committed = true;
      state.selected = null; state.original = ''; state.dirty = false;
      el('memory-file-content').value = ''; el('memory-file-editor').hidden = true;
      // No refresh can replace the draft while the import is pending or fails.
      const updated = await api.request(base); render(updated); state.loaded = true;
      message(`已导入修改，后续任务将使用新内容。${updated.error ? `文件同步未完成：${updated.error.message}，请修复后刷新。` : ''}`);
    } catch (error) { message(committed ? `修改已导入，但文件状态刷新失败：${error.message}。请刷新文件状态，不要重复导入。` : `未完成导入：${error.message}。页面修改会保留，请核对当前记忆后处理。`); }
    finally { state.busy = false; controls(); }
  }
  function save(event) {
    event?.preventDefault();
    if (!state.selected) return;
    return apply(state.selected, el('memory-file-content').value);
  }
  function importDisk(id) {
    if (state.dirty) { message('页面还有未导入修改，请先保存导入或关闭编辑，再导入本地文件。'); return; }
    return apply(id, undefined);
  }
  function edited() { state.dirty = el('memory-file-content').value !== state.original; }
  el('memory-files').addEventListener('toggle', () => { if (el('memory-files').open && !state.loaded) return load(); });
  el('memory-files-reload').addEventListener('click', () => load(true));
  el('memory-file-editor').addEventListener('submit', save);
  el('memory-file-close').addEventListener('click', close);
  el('memory-file-content').addEventListener('input', edited);
  return {state, load, select, save, close, importDisk, setBusy};
}
