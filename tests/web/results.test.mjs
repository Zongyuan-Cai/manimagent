import test from 'node:test';
import assert from 'node:assert/strict';
import {browser, response, aid, bid, jid} from './helpers.mjs';

const nodes = node => [node, ...node.children?.flatMap(nodes) || []];
const answer = (id, snapshot) => ({answer_id: id, markdown: 'body ' + id, warnings: [], material_ids: [], memory_snapshot: snapshot});

test('answer memory displays its immutable snapshot as text, never reading the current memory library', async () => {
  const hostile = '<img src=x onerror=alert(1)>';
  const snapshot = {entries: [{field: 'visual.preference', value: hostile, layer: 'deep', scope: 'global'}]};
  const b = browser({search: '?answer_id=' + aid, respond: async url => url.startsWith(`/api/v2/answers/${aid}`) ? response(answer(aid, snapshot)) : undefined});
  await b.ready;
  assert.equal(b.element('answer-memory').hidden, false);
  assert.match(b.element('answer-memory-summary').textContent, /1 条适用记忆/);
  const rendered = nodes(b.element('answer-memory-list'));
  assert.ok(rendered.some(node => node.textContent === hostile));
  assert.ok(rendered.every(node => !node.innerHTML));
  assert.ok(!b.requests.includes('/api/v2/memory'));
  b.window.location.search = '?answer_id=' + bid;
  await b.workspace.restore();
  assert.equal(b.element('answer-memory-list').children.length, 0);
  assert.match(b.element('answer-memory-summary').textContent, /没有记忆快照记录/);
});

test('memory display distinguishes disabled, empty and legacy snapshots', async () => {
  for (const [snapshot, wording] of [[null, /未使用用户记忆/], [{entries: []}, /没有适用条目/], [undefined, /无法确认/]]) {
    const b = browser({search: '?answer_id=' + aid, respond: async url => url.startsWith(`/api/v2/answers/${aid}`) ? response(answer(aid, snapshot)) : undefined});
    await b.ready;
    assert.match(b.element('answer-memory-summary').textContent, wording);
  }
});

test('a newly accepted generation clears the preceding answer memory snapshot', async () => {
  const b = browser({search: '?answer_id=' + aid, job: {kind: 'generation', result: null, answer_id: null}, respond: async url => {
    if (url.startsWith(`/api/v2/answers/${aid}`)) return response(answer(aid, {entries: [{field: 'detail', value: '旧偏好'}]}));
    if (url === '/api/v2/generations') return response({job_id: jid});
  }});
  await b.ready; b.element('text').value = '新的问题'; b.element('output-mode').value = 'text';
  await b.workspace.submit();
  assert.equal(b.element('answer-memory').hidden, true);
  assert.equal(b.element('answer-memory-list').children.length, 0);
});

test('cache diagnostics show partial coverage using only reported input tokens and reset on a new view', async () => {
  const b = browser({storage: {'manimagent.textJob': jid}, job: {usage: {model_calls: 4, input_tokens: 20000, cached_input_tokens: 600, cache_reported_calls: 2, cache_accounted_input_tokens: 1000}}});
  await b.ready;
  assert.match(b.element('cache-usage').textContent, /2\/4 次调用/);
  assert.match(b.element('cache-usage').textContent, /60%/);
  assert.match(b.element('cache-usage').textContent, /其余调用未知/);
  b.workspace.beginView();
  assert.match(b.element('cache-usage').textContent, /未知/);
  assert.doesNotMatch(b.element('cache-usage').textContent, /60%/);
});

test('cache diagnostics distinguish known zero from unreported or invalid statistics', async () => {
  for (const [usage, wording] of [
    [{model_calls: 1, cached_input_tokens: 0, cache_reported_calls: 1, cache_accounted_input_tokens: 100}, /命中比例 0%/],
    [{model_calls: 1, cached_input_tokens: null, cache_reported_calls: 0, cache_accounted_input_tokens: 0}, /未知/],
    [{model_calls: 1, input_tokens: 100}, /未知/],
    [{model_calls: 1, cached_input_tokens: 0, cache_reported_calls: 1, cache_accounted_input_tokens: 0}, /未知（已报告输入为 0）/],
    [{model_calls: 1, cached_input_tokens: 200, cache_reported_calls: 1, cache_accounted_input_tokens: 100}, /未知/],
  ]) {
    const b = browser({storage: {'manimagent.textJob': jid}, job: {usage}}); await b.ready;
    assert.match(b.element('cache-usage').textContent, wording);
  }
});

test('memory injection diagnostics bind the current job and disappear for a standalone answer view', async () => {
  const b = browser({storage: {'manimagent.textJob': jid}}); await b.ready;
  assert.equal(b.element('memory-context-links').hidden, false);
  assert.equal(b.element('memory-context-view').href, `/api/v2/jobs/${jid}/memory-context`);
  assert.equal(b.element('memory-context-download').href, `/api/v2/jobs/${jid}/memory-context?download=true`);
  b.window.location.search = '?answer_id=' + aid; await b.workspace.restore();
  assert.equal(b.element('memory-context-links').hidden, true);
  assert.equal(b.element('memory-context-view').getAttribute('href'), null);
  assert.equal(b.element('memory-context-download').getAttribute('href'), null);
});
