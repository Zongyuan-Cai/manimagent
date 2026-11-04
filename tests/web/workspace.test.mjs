import test from 'node:test';
import assert from 'node:assert/strict';
import {webcrypto} from 'node:crypto';
import {browser, id, aid, bid, vid, jid, flush, response} from './helpers.mjs';

test('refresh a completed video-only job restores its exact text and actions', async () => {
  const b = browser({storage: {'manimagent.textJob': jid, 'manimagent.answer': bid}});
  await b.ready;
  assert.equal(b.workspace.state.answer.answer_id, aid);
  assert.equal(b.workspace.state.videoId, vid);
  assert.equal(b.element('actions').hidden, false);
});

test('submission snapshots output mode and PDF ranges before asynchronous uploads', async () => {
  let unblock, submitted;
  const gate = new Promise(r => { unblock = r; });
  const b = browser({
    job: {kind: 'generation', result: null, answer_id: null},
    respond: async (url, options) => {
      if (url === '/api/v2/files') { await gate; return {ok: true, json: async () => ({file_id: id('file', 'e')})}; }
      if (url === '/api/v2/generations') { submitted = JSON.parse(options.body); return {ok: true, json: async () => ({job_id: jid})}; }
    },
  });
  await b.ready;
  b.element('output-mode').value = 'text';
  b.element('text').value = 'original question';
  b.workspace.state.fileRows = [{file: 'first.pdf', start: {value: '1'}, end: {value: '2'}}, {file: 'second.pdf', start: {value: '3'}, end: {value: '4'}}];
  const submit = b.workspace.submit({preventDefault() {}});
  await flush();
  b.element('output-mode').value = 'both';
  b.element('text').value = 'changed question';
  b.workspace.state.fileRows[1].start.value = '7'; b.workspace.state.fileRows[1].end.value = '8';
  // Repeated submission while uploading must not enqueue a second task.
  await b.workspace.submit({preventDefault() {}});
  unblock(); await submit;
  assert.equal(submitted.output_mode, 'text');
  assert.equal(submitted.source.instruction, 'original question');
  assert.deepEqual(submitted.source.inputs[2].selection, {kind: 'pages', start: 3, end: 4});
  assert.equal(b.requests.filter(url => url === '/api/v2/generations').length, 1);
});

test('starting a view clears old status and diagnostic artifacts', async () => {
  const b = browser(); await b.ready;
  for (const name of ['job', 'message', 'error', 'artifacts', 'status']) b.element(name).textContent = 'old';
  b.element('diagnostics').hidden = false;
  b.workspace.beginView();
  for (const name of ['job', 'message', 'error', 'artifacts', 'status']) assert.equal(b.element(name).textContent, '');
  assert.equal(b.element('diagnostics').hidden, true);
});

test('non-JSON gateway failure preserves HTTP status without leaking its body', async () => {
  const b = browser({respond: async url => url === '/broken' ? {ok: false, status: 502, json: async () => { throw new SyntaxError('private gateway body'); }} : undefined});
  await b.ready;
  await assert.rejects(b.api.request('/broken'), error => error.status === 502 && error.message === '请求失败（HTTP 502）');
});
test('running video job restores its source before result exists', async () => {
  const b = browser({storage: {'manimagent.textJob': jid}, job: {status: 'running', stage: 'video', result: null}});
  await b.ready;
  assert.equal(b.workspace.state.answer.answer_id, aid);
  assert.equal(b.workspace.state.videoId, null);
});
test('shared answer overrides saved job, answer and video', async () => {
  const b = browser({storage: {'manimagent.textJob': jid, 'manimagent.answer': aid, 'manimagent.video': vid}, search: '?answer_id=' + bid});
  await b.ready;
  assert.equal(b.workspace.state.answer.answer_id, bid);
  assert.equal(b.workspace.state.videoId, null);
  assert.ok(!b.requests.some(url => url.includes('/videos/') || url.includes('/jobs/')));
});
test('shared video loads source and rejects mismatched ID or content hash', async () => {
  for (const options of [
    {search: '?video_id=' + vid},
    {search: '?answer_id=' + bid + '&video_id=' + vid},
    {search: '?video_id=' + vid, video: {answer_hash: 'wrong'}},
  ]) {
    const b = browser(options);
    await b.ready;
    const rejected = !!options.video || options.search.includes('answer_id');
    assert.equal(b.workspace.state.videoId, rejected ? null : vid);
    if (rejected) assert.match(b.element('message').textContent, /不匹配/);
  }
});
test('a delayed answer response cannot overwrite a newer view', async () => {
  let unblock;
  const gate = new Promise(r => { unblock = r; });
  const b = browser({search: '?answer_id=' + aid, intercept: url => url.endsWith(aid) ? gate : undefined});
  await flush();
  b.window.location.search = '?answer_id=' + bid;
  await b.workspace.restore();
  unblock(); await b.ready;
  assert.equal(b.workspace.state.answer.answer_id, bid);
  assert.equal(b.ls.get('manimagent.answer'), bid);
  assert.match(b.element('result').innerHTML, new RegExp(bid));
});
test('saving an edit detaches an animation poll already in flight', async () => {
  let unblock;
  const gate = new Promise(r => { unblock = r; });
  const b = browser({search: '?answer_id=' + aid, intercept: url => url.includes('/jobs/') ? gate : undefined});
  await b.ready;
  const pendingPoll = b.workspace.watchJob(jid, b.workspace.state.epoch);
  await b.workspace.saveRevision();
  unblock(); await pendingPoll;
  await b.ready;
  assert.equal(b.workspace.state.answer.answer_id, bid);
  assert.equal(b.workspace.state.jobId, null);
  assert.equal(b.workspace.state.videoId, null);
  assert.equal(b.element('video-panel').hidden, true);
  assert.equal(b.ls.has('manimagent.textJob'), false);
});

test('a saved revision that fails to load releases detached animation controls', async () => {
  const b = browser({
    search: '?answer_id=' + aid, job: {status: 'running', stage: 'video', result: null},
    respond: async url => url.endsWith(bid) ? {ok: false, status: 503, json: async () => ({error: {message: '正文暂不可用'}})} : undefined,
  });
  await b.ready;
  await b.workspace.watchJob(jid, b.workspace.state.epoch);
  await b.workspace.saveRevision();
  assert.equal(b.workspace.state.jobId, null);
  assert.equal(b.element('generate').disabled, false);
  assert.equal(b.element('animate').disabled, false);
  assert.match(b.element('message').textContent, /正文暂不可用/);
});

test('workspace instances never share answer, job or polling state', async () => {
  const first = browser({storage: {'manimagent.textJob': jid}, job: {status: 'running', stage: 'video', result: null}});
  const second = browser({search: '?answer_id=' + bid});
  await Promise.all([first.ready, second.ready]);
  assert.equal(first.workspace.state.answer.answer_id, aid);
  assert.equal(second.workspace.state.answer.answer_id, bid);
  assert.equal(first.workspace.state.busy, true);
  assert.equal(second.workspace.state.busy, false);
  assert.equal(first.timers.size, 1);
  assert.equal(second.timers.size, 0);
  second.workspace.beginView();
  assert.equal(first.workspace.state.jobId, jid);
  assert.equal(first.timers.size, 1);
  assert.equal(first.element('generate').disabled, true);
});

test('a delayed video response cannot attach to a newer answer view', async () => {
  let unblock;
  const gate = new Promise(resolve => { unblock = resolve; });
  const b = browser({search: '?video_id=' + vid, intercept: url => url.endsWith(vid) ? gate : undefined});
  await flush();
  b.window.location.search = '?answer_id=' + bid;
  await b.workspace.restore();
  unblock(); await b.ready;
  assert.equal(b.workspace.state.answer.answer_id, bid);
  assert.equal(b.workspace.state.videoId, null);
  assert.equal(b.element('video-player').hidden, true);
  assert.equal(b.element('video-jump').hidden, true);
  assert.equal(b.ls.has('manimagent.video'), false);
});

test('a delayed video hash check cannot attach after a revision switches views', async () => {
  let unblock, started;
  const gate = new Promise(resolve => { unblock = resolve; });
  const hashing = new Promise(resolve => { started = resolve; });
  const crypto = {subtle: {async digest(...args) { started(); await gate; return webcrypto.subtle.digest(...args); }}};
  const b = browser({search: '?video_id=' + vid, crypto});
  await hashing;
  await b.workspace.saveRevision();
  unblock(); await b.ready;
  assert.equal(b.workspace.state.answer.answer_id, bid);
  assert.equal(b.workspace.state.videoId, null);
  assert.equal(b.element('video-panel').hidden, true);
  assert.equal(b.ls.get('manimagent.answer'), bid);
});

test('answer sources fetch original files through the explicit API dependency', async () => {
  const materialId = id('material', 'e'), fileId = id('file', 'f');
  const b = browser({search: '?answer_id=' + aid, respond: async url => {
    if (url === `/api/v2/answers/${aid}`) return response({answer_id: aid, markdown: 'source text', warnings: [], material_ids: [materialId]});
    if (url === `/api/v2/materials/${materialId}`) return response({source_file_ids: [fileId], blocks: [{block_id: 'block_1', source: {locator: {page: 2}}, content: 'original text'}]});
    if (url === `/api/v2/files/${fileId}`) return response({file_id: fileId, filename: 'notes.pdf'});
  }});
  await b.ready;
  const links = b.element('source-list').children;
  assert.equal(links[1].href, `/api/v2/files/${fileId}/content`);
  assert.equal(links[2].id, 'block_1');
  assert.match(links[2].textContent, /original text/);
  assert.equal(b.element('answer-meta').textContent, '完整解答 · 可编辑与导出');
});
