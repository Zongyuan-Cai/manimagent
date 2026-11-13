import test from 'node:test';
import assert from 'node:assert/strict';
import {createMemoryFiles} from '../../src/manimagent/web/memory-files.js';
import {fakeDOM} from './helpers.mjs';

const nodes = node => [node, ...node.children?.flatMap(nodes) || []];
const metadata = {root: 'C:/private/memory', index_content: '# MEMORY\n<script>private</script>', entries: [
  {memory_id: 'memory_a', field: 'visual.palette', path: 'entries/memory_a.md', status: 'edited'},
  {memory_id: 'memory_b', field: 'explanation.detail', path: 'entries/memory_b.md', status: 'stale'},
  {memory_id: 'memory_c', path: 'entries/memory_c.md', status: 'deleted'},
]};
function setup({respond} = {}) {
  const document = fakeDOM(), calls = [];
  const api = {request: async (url, options) => {
    calls.push({url, options});
    return await respond?.(url, options) ?? (url === '/api/v2/memory/documents' || url.endsWith('/sync') ? metadata : {content: '## 记忆内容\n\n蓝金主题'});
  }};
  api.post = (url, body) => api.request(url, {method: 'POST', body: JSON.stringify(body)});
  const files = createMemoryFiles({dom: document.dom, api});
  return {...document, files, calls};
}

test('memory exports load lazily, show safe text and distinguish historical local copies', async () => {
  const b = setup(); assert.equal(b.calls.length, 0);
  b.element('memory-files').open = true; await b.element('memory-files').listeners.toggle();
  assert.equal(b.element('memory-files-root').textContent, metadata.root);
  assert.equal(b.element('memory-files-index').textContent, metadata.index_content);
  assert.equal(b.element('memory-files-index').innerHTML, '');
  const rendered = nodes(b.element('memory-files-list'));
  assert.ok(rendered.every(node => !/导入|编辑/.test(node.textContent)));
  assert.ok(rendered.some(node => /历史副本/.test(node.textContent)));
  assert.ok(rendered.some(node => /已移除/.test(node.textContent)));
  assert.ok(rendered.some(node => node.href === '/api/v2/memory/documents/memory_a?download=true'));
  await b.element('memory-files').listeners.toggle(); assert.equal(b.calls.length, 1);
});

test('Markdown viewer is readonly and exposes no import or edit operations', async () => {
  const b = setup(); await b.files.select('memory_a');
  assert.equal(b.element('memory-file-content').readOnly, true);
  assert.equal(b.element('memory-file-content').value, '## 记忆内容\n\n蓝金主题');
  assert.equal(b.element('memory-file-editor').hidden, false);
  assert.equal(b.element('memory-file-editor').listeners.submit, undefined);
  assert.equal(b.element('memory-file-content').listeners.input, undefined);
  assert.equal(b.files.save, undefined); assert.equal(b.files.importDisk, undefined);
  await b.files.select('memory_b');
  assert.equal(b.files.state.selected, 'memory_b');
  b.files.close();
  assert.equal(b.files.state.selected, null);
  assert.equal(b.element('memory-file-editor').hidden, true);
  assert.ok(b.calls.every(call => !call.url.endsWith('/import')));
});

test('file cards keep identity and path in persistent details while exports remain available', async () => {
  const b = setup(); await b.files.load();
  const card = b.element('memory-files-list').children[0];
  const details = nodes(card).find(node => node.tagName === 'details');
  assert.equal(details.className, 'memory-file-details'); assert.equal(details.open, false);
  assert.ok(nodes(details).some(node => node.textContent === metadata.entries[0].path));
  assert.ok(nodes(details).some(node => node.textContent === metadata.entries[0].memory_id));
  const visible = card.children.filter(node => node !== details).flatMap(nodes);
  assert.ok(visible.every(node => !/memory_a|entries\//.test(node.textContent)));
  assert.ok(visible.some(node => node.textContent === '查看 Markdown'));
  assert.ok(visible.some(node => /不影响有效记忆/.test(node.textContent)));
  details.open = true; details.listeners.toggle();
  await b.files.load(true);
  assert.equal(nodes(b.element('memory-files-list').children[0]).find(node => node.tagName === 'details').open, true);
});

test('refresh regenerates exports and closes the obsolete preview without importing anything', async () => {
  const b = setup(); await b.files.select('memory_a');
  await b.files.load(true);
  assert.equal(b.element('memory-file-editor').hidden, true);
  assert.equal(b.files.state.selected, null);
  assert.deepEqual(b.calls.filter(call => call.options?.method === 'POST').map(call => call.url), ['/api/v2/memory/documents/sync']);
  assert.match(b.element('memory-files-message').textContent, /仅供查看和导出/);
});

test('concurrent reads and refreshes are bounded and mutations can lock export controls', async () => {
  let finish;
  const gate = new Promise(resolve => { finish = resolve; });
  const b = setup({respond: url => url.endsWith('/memory_a') ? gate : undefined});
  const pending = b.files.select('memory_a');
  assert.equal(b.element('memory-files-reload').disabled, true);
  await b.files.select('memory_b'); await b.files.load(true);
  assert.equal(b.calls.length, 1);
  finish({content: '导出原文'}); await pending;
  b.files.setBusy(true); await b.files.load(true);
  assert.equal(b.calls.length, 1);
  b.files.setBusy(false); await b.files.load(true);
  assert.equal(b.calls.length, 2);
});

test('a failed preview read preserves the existing view and reports its error', async () => {
  const b = setup({respond: url => { if (url.endsWith('/memory_b')) throw Error('文件不可用'); }});
  await b.files.select('memory_a'); await b.files.select('memory_b');
  assert.equal(b.files.state.selected, 'memory_a');
  assert.equal(b.element('memory-file-title').textContent, 'memory_a.md');
  assert.match(b.element('memory-files-message').textContent, /文件不可用/);
});

test('an export failure remains visible without implying the database mutation failed', async () => {
  const b = setup({respond: async () => ({...metadata, error: {code: 'memory_document_unavailable', message: '目录不可写'}})});
  await b.files.load(); assert.match(b.element('memory-files-message').textContent, /导出未完成.*目录不可写.*已提交的记忆仍保存在本机/);
});
