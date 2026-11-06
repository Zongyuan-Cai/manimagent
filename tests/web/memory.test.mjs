import test from 'node:test';
import assert from 'node:assert/strict';
import {webcrypto} from 'node:crypto';
import {createMemory} from '../../src/manimagent/web/memory.js';
import {createWorkspace} from '../../src/manimagent/web/workspace.js';
import {fakeDOM, flush, jid, id} from './helpers.mjs';

const storage = (values = {}) => {
  const entries = new Map(Object.entries(values));
  return {getItem: key => entries.get(key), setItem: (key, value) => entries.set(key, value), removeItem: key => entries.delete(key)};
};
const entry = (changes = {}) => ({memory_id: 'memory_a', field: 'visual.preference', value: '先图后式', scope: 'global',
  layer: 'short_term', state: 'active', temporary: false, independent_sessions: 2,
  evidence: [{quote: '我喜欢先看图，再看公式'}], ...changes});
function setup({entries = [], respond, local = storage(), session = storage()} = {}) {
  const document = fakeDOM(), calls = [];
  const api = {
    request: async (url, options) => {
      calls.push({url, options});
      const result = await respond?.(url, options);
      return result ?? {entries, policy: {}};
    },
    post: (url, data) => api.request(url, {method: 'POST', body: JSON.stringify(data)}),
  };
  const memory = createMemory({dom: document.dom, api, storage: local, sessionStorage: session, crypto: webcrypto});
  return {...document, memory, calls, local, session};
}
const nodes = node => [node, ...node.children?.flatMap(nodes) || []];
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
  assert.equal(refreshed.memory.options().learn, true);
  refreshed.element('memory-enabled').checked = false; refreshed.memory.enabledChanged();
  assert.equal(setup({local, session}).memory.options(), null);
});

test('memory values and evidence render as plain text, with correct layer and controls', async () => {
  const hostile = '<img src=x onerror=alert(1)>';
  const b = setup({entries: [entry({field: hostile, value: hostile, evidence: [{quote: hostile}]}), entry({memory_id: 'memory_b', temporary: true}), entry({memory_id: 'memory_c', layer: 'deep'})]});
  await b.memory.load();
  const rendered = nodes(b.element('memory-list'));
  assert.equal(rendered.filter(node => node.textContent === hostile).length, 3);
  assert.ok(rendered.every(node => !node.innerHTML));
  assert.equal(rendered.filter(node => node.textContent === '确认沉淀').length, 1);
  assert.equal(b.element('memory-state').textContent, '短期 2 · 深层 1');
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
  nodes(editor).find(node => node.tagName === 'input').value = '先公式后画图';
  await editor.listeners.submit({preventDefault() {}});
  const corrected = b.calls.find(call => call.options?.method === 'PATCH');
  assert.deepEqual(JSON.parse(corrected.options.body), {value: '先公式后画图'});
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

test('expired and superseded entries cannot be confirmed or edited; corrections disclose long-term effect', async () => {
  const b = setup({entries: [entry({expires_at: '2000-01-01T00:00:00+00:00'}), entry({memory_id: 'memory_b', state: 'superseded'}), entry({memory_id: 'memory_c', temporary: true})]});
  await b.memory.load();
  const rendered = nodes(b.element('memory-list'));
  assert.equal(b.element('memory-state').textContent, '短期 1 · 深层 0');
  assert.equal(rendered.filter(node => node.textContent === '确认沉淀').length, 0);
  assert.equal(rendered.filter(node => node.textContent === '纠正').length, 0);
  assert.equal(rendered.filter(node => node.textContent === '改为长期偏好').length, 1);
  assert.equal(rendered.filter(node => node.textContent === '删除').length, 3);
  action(b, '改为长期偏好').listeners.click();
  assert.ok(rendered.some(node => /保存即确认这是一条长期偏好/.test(node.textContent)));
  assert.ok(rendered.some(node => node.textContent === '已到期'));
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

test('generation captures memory before uploads and isolates refresh errors from its result', async () => {
  const {dom, element} = fakeDOM();
  let release, submitted, completed = 0;
  const gate = new Promise(resolve => { release = resolve; });
  const selected = {session_id: 'session_original', scope: 'global', learn: true};
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
  workspace.state.fileRows = [{file: 'notes.pdf', start: {value: ''}, end: {value: ''}}];
  const pending = workspace.submit(); await flush();
  selected.session_id = 'session_changed';
  release(); await pending; await flush();
  assert.equal(submitted.memory.session_id, 'session_original');
  assert.equal(workspace.state.status, 'completed');
  assert.equal(completed, 1);
  assert.equal(element('error').textContent, '');
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

test('the actual main module initializes all index controls and loads memory without startup errors', async () => {
  const {document, element} = fakeDOM(), calls = [];
  const replacements = {
    document, window: {location: {href: 'http://localhost/', search: ''}, history: {replaceState() {}}, navigator: {}},
    localStorage: storage(), sessionStorage: storage(), crypto: webcrypto,
    fetch: async url => {
      calls.push(url);
      const data = url === '/api/v2/memory' ? {entries: []} : url === '/api/v2/llm-settings' ? {revision: 1, models: {}, roles: {}, answer_context_tokens: 24000, answer_check: true} : {ready: true, issues: [], video_issues: []};
      return {ok: true, json: async () => data};
    },
  };
  const originals = Object.fromEntries(Object.keys(replacements).map(key => [key, Object.getOwnPropertyDescriptor(globalThis, key)]));
  try {
    for (const [key, value] of Object.entries(replacements)) Object.defineProperty(globalThis, key, {value, configurable: true});
    await import('../../src/manimagent/web/main.js?memory-smoke'); await flush();
    assert.equal(element('memory-state').textContent, '还没有记忆');
    assert.equal(element('llm-state').textContent, '已就绪');
    assert.equal(element('memory-enabled').checked, false);
    for (const [control, event] of [['form', 'submit'], ['memory-form', 'submit'], ['memory-enabled', 'change'], ['memory-new-session', 'click'], ['memory-cleanup', 'click']]) assert.equal(typeof element(control).listeners[event], 'function');
    assert.ok(calls.includes('/api/v2/memory'));
  } finally {
    for (const [key, descriptor] of Object.entries(originals)) {
      if (descriptor) Object.defineProperty(globalThis, key, descriptor);
      else delete globalThis[key];
    }
  }
});
