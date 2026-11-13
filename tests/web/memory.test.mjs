import test from 'node:test';
import assert from 'node:assert/strict';
import {webcrypto} from 'node:crypto';
import {createMemory} from '../../src/manimagent/web/memory.js';
import {createWorkspace} from '../../src/manimagent/web/workspace.js';
import {fakeDOM, fakeScheduler, flush, jid, id, inputCapabilities} from './helpers.mjs';

const storage = (values = {}) => {
  const entries = new Map(Object.entries(values));
  return {getItem: key => entries.get(key), setItem: (key, value) => entries.set(key, value), removeItem: key => entries.delete(key)};
};
const fields = [{field: 'explanation.order', label: '讲解顺序', values: [{value: 'intuition_then_proof', label: '先直观后证明'}, {value: 'proof_then_examples', label: '先证明后例子'}]}];
const entry = (changes = {}) => ({memory_id: 'memory_a', field: 'explanation.order', value: 'intuition_then_proof', scope: 'global',
  layer: 'short_term', state: 'active', temporary: false, independent_sessions: 2,
  evidence: [{quote: '我喜欢先看图，再看公式'}], ...changes});
function setup({entries = [], policy = {}, learning = {tasks: [], counts: {}}, respond, local = storage(), session = storage()} = {}) {
  const document = fakeDOM(), calls = [], scheduler = fakeScheduler();
  const api = {
    request: async (url, options) => {
      calls.push({url, options});
      const result = await respond?.(url, options);
      return url === '/api/v2/memory' ? {fields, ...(result ?? {entries, policy, learning})} : result ?? {entries, policy, learning};
    },
    post: (url, data) => api.request(url, {method: 'POST', body: JSON.stringify(data)}),
  };
  const memory = createMemory({dom: document.dom, api, storage: local, sessionStorage: session, crypto: webcrypto, scheduler});
  return {...document, memory, calls, local, session, scheduler};
}
const nodes = node => [node, ...node.children?.flatMap(nodes) || []];
const visibleNodes = node => node.hidden ? [] : [node, ...(node.tagName === 'details' && !node.open ? node.children.filter(child => child.tagName === 'summary') : node.children || []).flatMap(visibleNodes)];
const action = (b, label) => nodes(b.element('memory-list')).find(node => node.tagName === 'button' && node.textContent === label);

test('memory defaults off, persists the choice, and keeps one session until explicitly replaced', () => {
  const local = storage(), session = storage();
  const first = setup({local, session});
  assert.equal(first.memory.options(), null);
  first.element('memory-enabled').checked = true; first.memory.enabledChanged();
  const original = first.memory.options();
  assert.deepEqual(first.memory.options(), original);
  const refreshed = setup({local, session});
  assert.deepEqual(refreshed.memory.options(), original);
  refreshed.memory.newSession();
  assert.notEqual(refreshed.memory.options().session_id, original.session_id);
  assert.equal(refreshed.memory.options().scope, 'global');
  assert.equal(refreshed.memory.options().learn, false);
  refreshed.element('memory-enabled').checked = false; refreshed.memory.enabledChanged();
  assert.equal(setup({local, session}).memory.options(), null);
});

test('read and learn are independent, persisted, and older read-only settings do not opt into learning', () => {
  const local = storage({'manimagent.memoryEnabled': 'true'});
  const b = setup({local});
  assert.equal(b.memory.options().read, true);
  assert.equal(b.memory.options().learn, false);
  b.element('memory-learn').checked = true; b.memory.learningChanged();
  assert.equal(b.memory.options().learn, true);
  b.element('memory-enabled').checked = false; b.memory.enabledChanged();
  assert.equal(b.element('memory-learn').disabled, false);
  assert.equal(b.element('memory-learn').checked, true);
  assert.equal(b.memory.options().read, false);
  assert.equal(b.memory.options().learn, true);
  assert.match(b.element('memory-mode').textContent, /不读取已有记忆.*供以后使用/);
  const reloaded = setup({local});
  assert.equal(reloaded.memory.options().read, false);
  assert.equal(reloaded.memory.options().learn, true);
  reloaded.element('memory-learn').checked = false; reloaded.memory.learningChanged();
  assert.equal(reloaded.memory.options(), null);
});

test('memory values and evidence render as plain text, with correct layer and controls', async () => {
  const hostile = '<img src=x onerror=alert(1)>';
  const b = setup({entries: [entry({field: hostile, value: hostile, evidence: [{quote: hostile}]}), entry({memory_id: 'memory_b', temporary: true}), entry({memory_id: 'memory_c', layer: 'deep'})]});
  await b.memory.load();
  const rendered = nodes(b.element('memory-list'));
  assert.equal(rendered.filter(node => node.textContent.includes(hostile)).length, 3);
  assert.ok(rendered.every(node => !node.innerHTML));
  assert.equal(rendered.filter(node => node.textContent === '确认沉淀').length, 0);
  assert.equal(b.element('memory-state').textContent, '近期 0 · 长期 1');
});

test('memory cards show the preference first and keep metadata and evidence in persistent details', async () => {
  const current = entry({scope: 'topic.数学分析.极限', updated_at: '2026-11-06T01:00:00Z', expires_at: '2099-11-06T01:00:00Z'});
  const b = setup({entries: [current]}); await b.memory.load();
  const card = b.element('memory-list').children[0], visible = visibleNodes(card);
  assert.ok(visible.some(node => node.className === 'memory-card-title' && node.textContent === '先直观后证明'));
  assert.ok(visible.some(node => node.textContent === '近期'));
  assert.ok(visible.some(node => node.textContent === '纠正'));
  assert.ok(visible.every(node => !/explanation.order|数学分析|memory_a|独立会话|我喜欢先看图|更新于|有效至/.test(node.textContent)));
  const details = nodes(card).find(node => node.tagName === 'details');
  assert.equal(details.open, false);
  assert.ok(nodes(details).some(node => node.textContent === '字段：讲解顺序'));
  assert.ok(nodes(details).some(node => node.textContent === '范围：课程 数学分析 · 主题 极限'));
  assert.ok(nodes(details).some(node => node.textContent === '记录：memory_a'));
  assert.ok(nodes(details).some(node => node.textContent === current.evidence[0].quote));
  details.open = true; details.listeners.toggle();
  await b.memory.load();
  assert.equal(nodes(b.element('memory-list')).find(node => node.tagName === 'details').open, true);
});

test('self-reported background shows dates and expiry without promising automatic or permanent promotion', async () => {
  const background = entry({field: 'profile.background', value: 'first_year', updated_at: '2026-11-13T00:00:00Z', expires_at: '2099-11-13T00:00:00Z'});
  const b = setup({respond: () => ({entries: [background], fields: [{field: 'profile.background', label: '自述学习阶段', values: [{value: 'first_year', label: '大一（自述）'}]}]})});
  await b.memory.load();
  const rendered = nodes(b.element('memory-list'));
  assert.ok(rendered.some(node => node.textContent === '大一（自述）'));
  assert.ok(rendered.some(node => node.textContent === '确认此自述'));
  assert.ok(rendered.some(node => /记录于/.test(node.textContent)));
  assert.ok(rendered.some(node => /有效至/.test(node.textContent)));
  assert.ok(rendered.every(node => !/晋升进度|独立会话.*\//.test(node.textContent)));
  assert.match(b.element('memory-state').textContent, /近期 0 · 长期 0 · 自述 1/);
  await action(b, '确认此自述').listeners.click();
  assert.match(b.element('memory-message').textContent, /仍按记录的有效期/);
});

test('observation retries retain source identity and surface extraction failure without success', async () => {
  let fail = true;
  const b = setup({respond: async (url) => {
    if (url.endsWith('/observations')) {
      if (fail) throw new Error('提取模型暂不可用');
      return {entries: [], warnings: ['没有足够证据']};
    }
  }});
  b.element('memory-text').value = '我喜欢看图';
  await b.memory.observe();
  assert.equal(b.element('memory-message').textContent, '提取模型暂不可用');
  assert.equal(b.element('memory-add').disabled, false);
  fail = false; await b.memory.observe();
  const submitted = b.calls.filter(call => call.url.endsWith('/observations')).map(call => JSON.parse(call.options.body));
  assert.equal(submitted[0].source_id, submitted[1].source_id);
  assert.equal(submitted[0].session_id, b.memory.state.sessionId);
  assert.match(b.element('memory-message').textContent, /没有生成.*没有足够证据/);
});

test('confirm, correction, deletion and cleanup use the intended methods and refresh the list', async () => {
  const b = setup({entries: [entry()]}); await b.memory.load();
  await action(b, '确认沉淀').listeners.click();
  assert.ok(b.calls.some(call => call.url === '/api/v2/memory/memory_a/confirm' && call.options.method === 'POST'));
  action(b, '纠正').listeners.click();
  const editor = nodes(b.element('memory-list')).find(node => node.tagName === 'form');
  assert.equal(editor.hidden, false);
  nodes(editor).find(node => node.tagName === 'select').value = 'proof_then_examples';
  await editor.listeners.submit({preventDefault() {}});
  const corrected = b.calls.find(call => call.options?.method === 'PATCH');
  assert.deepEqual(JSON.parse(corrected.options.body), {value: 'proof_then_examples', confirm_long_term: true});
  assert.match(b.element('memory-message').textContent, /已更正默认偏好/);
  await action(b, '删除').listeners.click();
  assert.ok(b.calls.some(call => call.url === '/api/v2/memory/memory_a' && call.options?.method === 'DELETE'));
  await b.memory.cleanup();
  assert.ok(b.calls.some(call => call.url === '/api/v2/memory/cleanup' && call.options.method === 'POST'));
});

test('pending observations cannot submit twice or change session identity', async () => {
  let finish;
  const gate = new Promise(resolve => { finish = resolve; });
  const b = setup({respond: url => url.endsWith('/observations') ? gate : undefined});
  b.element('memory-text').value = '我的偏好';
  const original = b.memory.state.sessionId;
  const pending = b.memory.observe();
  await b.memory.observe(); b.memory.newSession();
  assert.equal(b.memory.state.sessionId, original);
  assert.equal(b.calls.filter(call => call.url.endsWith('/observations')).length, 1);
  finish({entries: [entry()]}); await pending;
  assert.equal(b.memory.state.busy, false);
});

test('expired, superseded, temporary and unsupported legacy values cannot be confirmed or corrected', async () => {
  const b = setup({entries: [entry({expires_at: '2000-01-01T00:00:00+00:00'}), entry({memory_id: 'memory_b', state: 'superseded'}), entry({memory_id: 'memory_c', temporary: true}), entry({memory_id: 'legacy', field: 'visual.preference'})]});
  await b.memory.load();
  const rendered = nodes(b.element('memory-list'));
  assert.equal(rendered.filter(node => node.textContent === '确认沉淀').length, 0);
  assert.equal(rendered.filter(node => node.textContent === '纠正').length, 0);
  assert.equal(rendered.filter(node => node.textContent === '删除').length, 4);
  assert.ok(rendered.some(node => node.textContent === '已到期'));
  assert.equal(rendered.filter(node => node.textContent === '历史记录 · 不再应用').length, 2);
});

test('correction is limited to the field contract and rejects unsupported values before sending', async () => {
  const b = setup({entries: [entry()]}); await b.memory.load();
  action(b, '纠正').listeners.click();
  const editor = nodes(b.element('memory-list')).find(node => node.tagName === 'form');
  const input = nodes(editor).find(node => node.tagName === 'select');
  assert.deepEqual(input.children.map(option => option.value), ['intuition_then_proof', 'proof_then_examples']);
  input.value = 'arbitrary free text';
  await editor.listeners.submit({preventDefault() {}});
  assert.equal(b.calls.filter(call => call.options?.method === 'PATCH').length, 0);
  assert.match(b.element('memory-message').textContent, /受支持的偏好/);
});

test('an old memory reload cannot replace the refreshed state after a mutation', async () => {
  let finishOld, loads = 0;
  const old = new Promise(resolve => { finishOld = resolve; });
  const b = setup({respond: (url, options) => {
    if (!options) return ++loads === 1 ? old : {entries: [entry({value: '新内容'})]};
  }});
  const initial = b.memory.load();
  await b.memory.cleanup();
  finishOld({entries: [entry({value: '旧内容'})]}); await initial;
  assert.equal(b.memory.state.entries[0].value, '新内容');
});

test('removing one open memory editor preserves another draft until that editor closes', async () => {
  let entries = [entry(), entry({memory_id: 'memory_b', value: 'proof_then_examples'})];
  const b = setup({respond: (_url, options) => {
    if (options?.method === 'DELETE') entries = entries.slice(1);
    return {entries, policy: {}, learning: {tasks: [], counts: {}}};
  }});
  await b.memory.load();
  const cards = [...b.element('memory-list').children];
  for (const card of cards) nodes(card).find(node => node.textContent === '纠正').listeners.click();
  const firstEditor = nodes(cards[0]).find(node => node.tagName === 'form');
  const secondEditor = nodes(cards[1]).find(node => node.tagName === 'form');
  const input = nodes(secondEditor).find(node => node.tagName === 'select'); input.value = '第二条尚未保存';
  await nodes(cards[0]).find(node => node.textContent === '删除').listeners.click();
  assert.equal(firstEditor.hidden, true); assert.equal(secondEditor.hidden, false);
  assert.equal(input.value, '第二条尚未保存');
  nodes(secondEditor).find(node => node.textContent === '取消').listeners.click();
  assert.equal(b.element('memory-list').children.length, 1);
  assert.ok(nodes(b.element('memory-list')).some(node => node.textContent === '先证明后例子'));
});

test('generation captures memory before uploads and reports refresh errors without changing its result', async t => {
  const errors = [];
  t.mock.method(console, 'error', (...args) => errors.push(args));
  const {dom, element} = fakeDOM();
  let release, submitted, completed = 0;
  const gate = new Promise(resolve => { release = resolve; });
  const selected = {session_id: 'session_original', scope: 'global', read: true, learn: false};
  const api = {
    request: async url => {
      if (url === '/api/v2/files') { await gate; return {file_id: id('file', 'e')}; }
      if (url === `/api/v2/jobs/${jid}`) return {kind: 'generation', status: 'completed', stage: 'completed', message: '完成', diagnostic_asset_ids: []};
      throw new Error('Unexpected ' + url);
    },
    post: async (_url, data) => { submitted = data; return {job_id: jid}; },
  };
  const window = {location: {search: '', href: 'http://localhost/'}, navigator: {}, history: {replaceState() {}}};
  const workspace = createWorkspace({dom, api, window, storage: storage(), crypto: webcrypto,
    memoryOptions: () => selected, onCompleted: () => { completed++; throw new Error('记忆读取失败'); }});
  element('text').value = '求解这个问题'; element('output-mode').value = 'text';
  workspace.state.inputCapabilities = inputCapabilities;
  element('files').files = [new File(['pdf'], 'notes.pdf')]; workspace.selectFiles();
  const pending = workspace.submit(); await flush();
  selected.session_id = 'session_changed';
  selected.learn = true;
  release(); await pending; await flush();
  assert.equal(submitted.memory.session_id, 'session_original');
  assert.equal(submitted.memory.learn, false);
  assert.equal(workspace.state.status, 'completed');
  assert.equal(completed, 1);
  assert.equal(element('error').textContent, '');
  assert.equal(errors.length, 1); assert.match(errors[0][1].message, /记忆读取失败/);
});

test('generation omits memory entirely when the option is disabled', async () => {
  const {dom, element} = fakeDOM(); let submitted;
  const api = {post: async (_url, data) => { submitted = data; return {job_id: jid}; }, request: async () => ({kind: 'generation', status: 'completed', message: '完成', diagnostic_asset_ids: []})};
  const window = {location: {search: '', href: 'http://localhost/'}, navigator: {}, history: {replaceState() {}}};
  const workspace = createWorkspace({dom, api, window, storage: storage(), crypto: webcrypto});
  element('text').value = '求解'; element('output-mode').value = 'text';
  await workspace.submit();
  assert.equal(Object.hasOwn(submitted, 'memory'), false);
});

test('learn-only generation submits explicit controls and preserves successful saving when generation fails', async () => {
  const {dom, element} = fakeDOM(); let submitted;
  const local = storage({'manimagent.memoryLearning': 'true'}), scheduler = fakeScheduler();
  const api = {
    post: async (_url, body) => { submitted = body; return {job_id: jid}; },
    request: async url => {
      if (url === '/api/v2/memory') return {entries: [entry()], fields, learning: {tasks: [learningTask({state: 'completed', can_cancel: false, entry_ids: ['memory_a']})], counts: {completed: 1}}};
      return {kind: 'generation', status: 'failed', stage: 'answer', message: '生成失败', error: {message: '正文检查失败'}, diagnostic_asset_ids: []};
    },
  };
  const memory = createMemory({dom, api, storage: local, sessionStorage: storage(), crypto: webcrypto, scheduler});
  const window = {location: {search: '', href: 'http://localhost/'}, navigator: {}, history: {replaceState() {}}};
  const workspace = createWorkspace({dom, api, window, storage: local, crypto: webcrypto, scheduler,
    memoryOptions: memory.options, onCompleted: () => memory.load()});
  element('text').value = '请记住以后先直观后证明，然后解题'; element('output-mode').value = 'text';
  await workspace.submit(); await flush();
  assert.equal(submitted.memory.read, false); assert.equal(submitted.memory.learn, true);
  assert.equal(submitted.memory.scope, 'global');
  assert.equal(workspace.state.status, 'failed');
  assert.equal(element('memory-learning-summary').textContent, '偏好更新已完成。');
  assert.ok(nodes(element('memory-learning-list')).some(node => node.textContent === '已更新 1 条偏好。'));
});

const learningTask = (changes = {}) => ({job_id: jid, state: 'queued', attempts: 0, max_attempts: 3, can_retry: false, can_cancel: true,
  entry_ids: [], promoted_ids: [], created_at: '2026-11-06T00:00:00Z', updated_at: '2026-11-06T00:00:00Z', ...changes});
const learningAction = (b, label) => nodes(b.element('memory-learning-list')).find(node => node.tagName === 'button' && node.textContent === label);

test('failed learning shows an actionable status while keeping technical diagnostics in details', async () => {
  const task = learningTask({state: 'failed', attempts: 2, can_retry: true, can_cancel: false,
    error: {code: 'provider_auth_error', message: 'HTTP 401 from provider'},
    usage: {model_calls: 2, input_tokens: 123, output_tokens: 45, tokens_reported: true}});
  const b = setup({learning: {tasks: [task], counts: {failed: 1}}}); await b.memory.load();
  const list = b.element('memory-learning-list'), visible = visibleNodes(list);
  assert.ok(visible.some(node => node.textContent === '偏好尚未保存，可以重试。'));
  assert.ok(visible.some(node => node.textContent === '重试沉淀'));
  assert.ok(visible.every(node => !/job_|HTTP 401|token|已尝试|更新于/.test(node.textContent)));
  const details = nodes(list).find(node => node.tagName === 'details');
  assert.equal(details.open, false);
  assert.ok(nodes(details).some(node => node.textContent.includes(task.job_id)));
  assert.ok(nodes(details).every(node => !/token|后台提取用量/.test(node.textContent)));
  assert.ok(nodes(details).some(node => node.textContent === task.error.message));
});

test('deterministic saving states, authoritative actions and promotion progress render without another model usage', async () => {
  const hostile = '<img src=x onerror=alert(1)>';
  const tasks = ['waiting', 'queued', 'running', 'completed', 'failed', 'skipped', 'cancelled'].map((state, i) => learningTask({job_id: 'job_' + i, state,
    can_cancel: ['waiting', 'queued', 'running'].includes(state), can_retry: state === 'failed',
    entry_ids: state === 'completed' ? ['memory_a'] : [], promoted_ids: state === 'completed' ? ['memory_a'] : [],
    error: state === 'failed' ? {message: hostile} : null, usage_unknown: true}));
  tasks.push(learningTask({job_id: 'job_exhausted', state: 'failed', attempts: 3, can_retry: false, can_cancel: false}));
  const b = setup({entries: [entry(), entry({memory_id: 'deep', layer: 'deep', independent_sessions: 4}), entry({memory_id: 'manual', layer: 'deep', confirmed_at: '2026-11-06'})], policy: {promotion_sessions: 4}, learning: {tasks, counts: {waiting: 1, queued: 1, running: 1}}});
  await b.memory.load();
  const rendered = nodes(b.element('memory-learning-list'));
  for (const label of ['等待请求解释', '待保存', '保存中', '已完成', '失败', '已跳过', '已取消']) assert.ok(rendered.some(node => node.textContent === label));
  assert.equal(rendered.filter(node => node.textContent === '重试沉淀').length, 1);
  assert.equal(rendered.filter(node => node.textContent === '取消沉淀').length, 3);
  assert.ok(rendered.some(node => node.textContent === hostile));
  assert.ok(rendered.every(node => !node.innerHTML));
  assert.ok(rendered.some(node => /其中 1 条自动沉淀/.test(node.textContent)));
  assert.ok(rendered.every(node => !/后台提取用量/.test(node.textContent)));
  const entries = nodes(b.element('memory-list'));
  assert.ok(entries.some(node => /独立会话 2\/4/.test(node.textContent)));
  assert.ok(entries.some(node => /自动沉淀 · 4/.test(node.textContent)));
  assert.ok(entries.some(node => /已由你确认/.test(node.textContent)));
});

test('retry and cancellation cannot be duplicated and do not clear an open memory draft', async () => {
  let finish, active = learningTask({state: 'failed', can_retry: true, can_cancel: false});
  const gate = new Promise(resolve => { finish = resolve; });
  const b = setup({entries: [entry()], respond: async (url, options) => {
    if (url.endsWith('/retry')) { await gate; active = learningTask(); return active; }
    if (url.endsWith('/cancel')) { active = learningTask({state: 'cancelled', can_cancel: false}); return active; }
    if (!options) return {entries: [entry()], learning: {tasks: [active], counts: {}}};
  }});
  await b.memory.load(); action(b, '纠正').listeners.click();
  const editor = nodes(b.element('memory-list')).find(node => node.tagName === 'form');
  const input = nodes(editor).find(node => node.tagName === 'select'); input.value = '尚未保存的草稿';
  const retry = learningAction(b, '重试沉淀');
  const pending = retry.listeners.click(); await retry.listeners.click();
  assert.equal(retry.disabled, true); assert.equal(input.disabled, true);
  assert.equal(b.calls.filter(call => call.url.endsWith('/retry')).length, 1);
  finish(); await pending;
  assert.equal(input.value, '尚未保存的草稿'); assert.equal(editor.hidden, false); assert.equal(input.disabled, false);
  await learningAction(b, '取消沉淀').listeners.click();
  assert.ok(b.calls.some(call => call.url === `/api/v2/memory/learning/${jid}/cancel` && call.options.method === 'POST'));
  assert.equal(input.value, '尚未保存的草稿'); assert.equal(b.scheduler.timers.size, 0);
});

test('background refresh updates task progress while preserving editing, observation text and answer snapshot', async () => {
  let completed = false;
  const b = setup({respond: () => ({entries: [entry({value: completed ? 'proof_then_examples' : 'intuition_then_proof'})], learning: {tasks: [learningTask({state: completed ? 'completed' : 'running', can_cancel: !completed, entry_ids: completed ? ['memory_a'] : []})], counts: {running: completed ? 0 : 1}}})});
  await b.memory.load(); action(b, '纠正').listeners.click();
  const card = b.element('memory-list').children[0], editor = nodes(card).find(node => node.tagName === 'form');
  const input = nodes(editor).find(node => node.tagName === 'select'); input.value = '用户正在输入';
  b.element('memory-text').value = '待提交的观察'; b.element('answer-memory-summary').textContent = '历史快照';
  completed = true; await b.scheduler.tick();
  assert.equal(b.element('memory-list').children[0], card);
  assert.equal(input.value, '用户正在输入'); assert.equal(b.element('memory-text').value, '待提交的观察');
  assert.equal(b.element('answer-memory-summary').textContent, '历史快照');
  assert.match(b.element('memory-sync').textContent, /当前输入会保留/);
  assert.ok(nodes(b.element('memory-learning-list')).some(node => node.textContent === '已完成'));
  assert.equal(b.scheduler.timers.size, 0);
  nodes(editor).find(node => node.tagName === 'button' && node.textContent === '取消').listeners.click();
  assert.ok(nodes(b.element('memory-list')).some(node => node.textContent === '先证明后例子'));
});

test('automatic refresh is bounded and three consecutive errors pause it until manual refresh', async () => {
  let fail = false;
  const b = setup({respond: () => {
    if (fail) throw Error('服务暂时离线');
    return {entries: [entry()], learning: {tasks: [learningTask()], counts: {queued: 1}}};
  }});
  await b.memory.load(); fail = true;
  for (let i = 0; i < 3; i++) assert.equal(await b.scheduler.tick(), true);
  assert.equal(b.scheduler.timers.size, 0); assert.equal(b.memory.state.failures, 3);
  assert.match(b.element('memory-learning-sync').textContent, /自动刷新已暂停/);
  assert.ok(nodes(b.element('memory-list')).some(node => node.textContent === '先直观后证明'));
  fail = false; await b.memory.load(); assert.equal(b.scheduler.timers.size, 1);
  for (let i = 0; i < 120; i++) assert.equal(await b.scheduler.tick(), true);
  assert.equal(b.memory.state.polls, 120); assert.equal(b.scheduler.timers.size, 0);
  assert.match(b.element('memory-learning-sync').textContent, /点击.*刷新沉淀状态/);
});

test('an unavailable learning API does not imply that no work is pending', async () => {
  const b = setup({respond: () => ({entries: [], policy: {}})});
  await b.memory.load();
  assert.match(b.element('memory-learning-summary').textContent, /暂不可用/);
  assert.equal(b.scheduler.timers.size, 0);
});

test('reloading memory management discovers waiting tasks without waiting for foreground completion', async () => {
  const b = setup({learning: {tasks: [learningTask({state: 'waiting'})], counts: {waiting: 1}}});
  await b.memory.load({preserveMessage: true});
  assert.ok(nodes(b.element('memory-learning-list')).some(node => node.textContent === '等待请求解释'));
  assert.equal(b.scheduler.timers.size, 1);
});

test('cancellation that races with a completed commit does not claim memories were cancelled', async () => {
  const b = setup({learning: {tasks: [learningTask({state: 'running'})], counts: {running: 1}}, respond: url => url.endsWith('/cancel') ? {state: 'completed'} : undefined});
  await b.memory.load(); await learningAction(b, '取消沉淀').listeners.click();
  assert.match(b.element('memory-message').textContent, /任务已完成.*没有撤回/);
  assert.doesNotMatch(b.element('memory-message').textContent, /已取消/);
});

test('recovered completed tasks show unknown consolidation counts rather than claiming no preferences', async () => {
  const b = setup({learning: {tasks: [learningTask({state: 'completed', can_cancel: false, counts_known: false, entry_ids: []})], counts: {completed: 1}}});
  await b.memory.load();
  const rendered = nodes(b.element('memory-learning-list'));
  assert.ok(rendered.some(node => /统计未知/.test(node.textContent)));
  assert.ok(!rendered.some(node => /未识别到/.test(node.textContent)));
});

test('task details stay expanded across refresh and unchanged memory cards are not rebuilt', async () => {
  const b = setup({entries: [entry()], learning: {tasks: [learningTask()], counts: {queued: 1}}});
  await b.memory.load();
  const card = b.element('memory-list').children[0];
  const details = nodes(b.element('memory-learning-list')).find(node => node.tagName === 'details');
  details.open = true; details.listeners.toggle();
  b.element('memory-learning-list').scrollTop = 120;
  await b.scheduler.tick();
  assert.equal(b.element('memory-list').children[0], card);
  assert.equal(nodes(b.element('memory-learning-list')).find(node => node.tagName === 'details').open, true);
  assert.equal(b.element('memory-learning-list').scrollTop, 120);
});
