import test from 'node:test';
import assert from 'node:assert/strict';
import {webcrypto} from 'node:crypto';
import {createMemoryFiles} from '../../src/manimagent/web/memory-files.js';
import {createMemory} from '../../src/manimagent/web/memory.js';
import {fakeDOM, fakeScheduler} from './helpers.mjs';

const nodes = node => [node, ...node.children?.flatMap(nodes) || []];
const metadata = {root: 'C:/private/memory', index_content: '# MEMORY\n<script>private</script>', entries: [
  {memory_id: 'memory_a', field: 'visual.palette', path: 'entries/memory_a.md', status: 'edited'},
  {memory_id: 'memory_b', field: 'detail', path: 'entries/memory_b.md', status: 'stale'},
  {memory_id: 'memory_c', path: 'entries/memory_c.md', status: 'deleted'},
]};
function setup({respond, importDocument = async () => ({memory_id: 'memory_new'})} = {}) {
  const document = fakeDOM(), calls = [];
  const api = {request: async (url, options) => {
    calls.push({url, options});
    return await respond?.(url, options) ?? (url === '/api/v2/memory/documents' || url.endsWith('/sync') ? metadata : {content: '## 记忆内容\n\n旧值'});
  }};
  api.post = (url, body) => api.request(url, {method: 'POST', body: JSON.stringify(body)});
  const files = createMemoryFiles({dom: document.dom, api, importDocument});
  return {...document, files, calls};
}

test('memory files load lazily, show safe text and distinguish edited, stale and deleted copies', async () => {
  const b = setup(); assert.equal(b.calls.length, 0);
  b.element('memory-files').open = true; await b.element('memory-files').listeners.toggle();
  assert.equal(b.element('memory-files-root').textContent, metadata.root);
  assert.equal(b.element('memory-files-index').textContent, metadata.index_content);
  assert.equal(b.element('memory-files-index').innerHTML, '');
  const rendered = nodes(b.element('memory-files-list'));
  assert.equal(rendered.filter(node => node.textContent === '导入本地修改' && !node.disabled).length, 1);
  assert.ok(rendered.some(node => /版本冲突/.test(node.textContent)));
  assert.ok(rendered.some(node => /已移除/.test(node.textContent)));
  assert.ok(rendered.some(node => node.href === '/api/v2/memory/documents/memory_a?download=true'));
  await b.element('memory-files').listeners.toggle(); assert.equal(b.calls.length, 1);
});

test('file refresh preserves a dirty Markdown draft and prevents accidental file switching or disk import', async () => {
  const b = setup(); await b.files.select('memory_a');
  b.element('memory-file-content').value = '我的未保存修改'; b.element('memory-file-content').listeners.input();
  await b.files.load(true);
  assert.equal(b.element('memory-file-content').value, '我的未保存修改');
  assert.match(b.element('memory-files-message').textContent, /未导入修改保持不变/);
  const count = b.calls.length;
  await b.files.select('memory_b'); await b.files.importDisk('memory_a');
  assert.equal(b.calls.length, count); assert.equal(b.files.state.selected, 'memory_a');
  b.files.close(); await b.files.select('memory_b');
  assert.equal(b.files.state.selected, 'memory_b');
});

test('file cards keep identity and path in persistent details without hiding conflict actions', async () => {
  const b = setup(); await b.files.load();
  const card = b.element('memory-files-list').children[0];
  const details = nodes(card).find(node => node.tagName === 'details');
  assert.equal(details.className, 'memory-file-details'); assert.equal(details.open, false);
  assert.ok(nodes(details).some(node => node.textContent === metadata.entries[0].path));
  assert.ok(nodes(details).some(node => node.textContent === metadata.entries[0].memory_id));
  const visible = card.children.filter(node => node !== details).flatMap(nodes);
  assert.ok(visible.every(node => !/memory_a|entries\//.test(node.textContent)));
  assert.ok(visible.some(node => node.textContent === '导入本地修改' && !node.disabled));
  assert.ok(visible.some(node => /待导入/.test(node.textContent)));
  details.open = true; details.listeners.toggle();
  await b.files.load(true);
  assert.equal(nodes(b.element('memory-files-list').children[0]).find(node => node.tagName === 'details').open, true);
});

test('failed imports retain the draft, report conflicts and do not silently update the version', async () => {
  const b = setup({importDocument: async () => { throw Error('记忆版本已变化'); }});
  await b.files.select('memory_a');
  b.element('memory-file-content').value = 'dirty'; b.element('memory-file-content').listeners.input();
  await b.files.save({preventDefault() {}});
  assert.equal(b.element('memory-file-content').value, 'dirty');
  assert.equal(b.files.state.dirty, true); assert.equal(b.element('memory-file-editor').hidden, false);
  assert.match(b.element('memory-files-message').textContent, /未完成导入.*版本已变化/);
});

test('imports are bounded by the shared memory mutation lock and cannot be submitted twice', async () => {
  const document = fakeDOM(), calls = []; let finish;
  const gate = new Promise(resolve => { finish = resolve; });
  const api = {request: async (url, options) => {
    calls.push({url, options});
    if (url.endsWith('/import')) return gate;
    if (url === '/api/v2/memory/documents') return metadata;
    if (url.startsWith('/api/v2/memory/documents/')) return {content: '## 记忆内容\n\n旧值'};
    return {entries: [], policy: {}, learning: {tasks: [], counts: {}}};
  }};
  api.post = (url, data) => api.request(url, {method: 'POST', body: JSON.stringify(data)});
  const memory = createMemory({dom: document.dom, api, crypto: webcrypto, scheduler: fakeScheduler()});
  await memory.files.select('memory_a');
  document.element('memory-file-content').value = 'revised';
  const pending = memory.files.save({preventDefault() {}});
  assert.equal(document.element('memory-cleanup').disabled, true);
  assert.equal(document.element('memory-file-content').disabled, true);
  await memory.files.save(); await memory.cleanup();
  assert.equal(calls.filter(call => call.url.endsWith('/import')).length, 1);
  assert.equal(calls.filter(call => call.url.endsWith('/cleanup')).length, 0);
  finish({memory_id: 'memory_new'}); await pending;
  assert.deepEqual(JSON.parse(calls.find(call => call.url.endsWith('/import')).options.body), {content: 'revised'});
  assert.equal(document.element('memory-file-editor').hidden, true);
  assert.equal(document.element('memory-cleanup').disabled, false);
  assert.match(document.element('memory-files-message').textContent, /已导入修改/);
});

test('disk imports send no editor text and successful commits are not reported as failed after refresh errors', async () => {
  const requests = [];
  const b = setup({respond: async url => { if (url === '/api/v2/memory/documents') throw Error('离线'); }, importDocument: async (...args) => { requests.push(args); return {memory_id: 'memory_new'}; }});
  await b.files.importDisk('memory_a');
  assert.deepEqual(requests, [['memory_a', undefined]]);
  assert.match(b.element('memory-files-message').textContent, /修改已导入.*刷新失败/);
  assert.doesNotMatch(b.element('memory-files-message').textContent, /未完成导入/);
});

test('an export failure remains visible without implying the database mutation failed', async () => {
  const b = setup({respond: async () => ({...metadata, error: {code: 'memory_document_unavailable', message: '目录不可写'}})});
  await b.files.load(); assert.match(b.element('memory-files-message').textContent, /同步未完成.*目录不可写.*已提交的记忆仍保存在本机/);
});
