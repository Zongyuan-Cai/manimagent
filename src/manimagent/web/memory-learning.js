const pendingStates = new Set(['waiting', 'queued', 'running']);
const labels = {waiting: '等待本次生成结束', queued: '待处理', running: '沉淀中', completed: '已完成', failed: '失败', skipped: '已跳过', cancelled: '已取消'};

export function hasPendingLearning(learning) {
  return !!learning && ([...pendingStates].some(name => learning.counts?.[name] > 0) || learning.tasks.some(task => pendingStates.has(task.state)));
}

// This view owns only its controls and disclosure state. Requests and polling stay
// in the memory controller so task actions share the same mutation lock as edits.
export function createLearningView({dom, onAction}) {
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
      result.textContent = task.state === 'failed' ? (['provider_auth_error', 'model_unavailable'].includes(task.error?.code) ? task.can_retry ? '请检查核心 API 配置后重试。' : '请检查核心 API 配置。' : task.can_retry ? '偏好尚未保存，可以重试。' : '偏好尚未保存，请查看详情。') : task.state === 'completed' ? (task.counts_known === false ? '请在记忆列表核对结果。' : task.entry_ids?.length ? `已更新 ${task.entry_ids.length} 条偏好。` : '没有需要保存的新偏好。') : '';
      const outcome = dom.create('p');
      outcome.textContent = task.state === 'completed' ? (task.entry_ids?.length ? `已处理 ${task.entry_ids.length} 条记忆，其中 ${task.promoted_ids?.length || 0} 条自动沉淀为深层。` : '未识别到可保存的新偏好。') : task.state === 'waiting' ? '等待本次生成结束，不会延迟本次结果。' : task.state === 'queued' ? '等待后台处理。' : task.state === 'running' ? '正在提取有原句依据的偏好。' : task.state === 'cancelled' ? '已取消此沉淀任务。' : task.state === 'skipped' ? '本次未进行沉淀。' : '本次沉淀未完成，已有记忆和生成结果仍可使用。';
      if (task.state === 'completed' && task.counts_known === false) outcome.textContent = '沉淀已完成；本次新增或强化的统计未知，请核对记忆列表。';
      else if (task.state === 'completed' && task.counts_known && task.counts) outcome.textContent = `新增 ${task.counts.created} 条，强化 ${task.counts.reinforced} 条，自动沉淀为深层 ${task.counts.promoted} 条。`;
      const details = dom.create('details'); details.className = 'memory-evidence'; details.open = openDetails.has(task.job_id);
      details.addEventListener('toggle', () => { if (details.open) openDetails.add(task.job_id); else openDetails.delete(task.job_id); });
      const summary = dom.create('summary'); summary.textContent = '查看任务详情'; details.append(summary);
      const identity = dom.create('p'); identity.textContent = `任务 ${task.job_id} · 已尝试 ${task.attempts ?? 0}/${task.max_attempts ?? 3} 次`;
      const timestamp = dom.create('p'), date = new Date(task.updated_at || task.created_at || '');
      timestamp.textContent = Number.isNaN(date.getTime()) ? '更新时间未知' : `更新于 ${date.toLocaleString()}`;
      const usage = dom.create('p'), used = task.usage;
      usage.textContent = !used || task.usage_unknown ? '后台提取用量：未知或不完整，不计入本次正文的用量展示。' : `后台提取用量：${used.model_calls ?? 0} 次调用${used.tokens_reported ? `，输入 ${used.input_tokens ?? 0} / 输出 ${used.output_tokens ?? 0} token` : '，token 用量未报告'}。与本次正文分开记账。`;
      details.append(outcome, identity, timestamp, usage);
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
