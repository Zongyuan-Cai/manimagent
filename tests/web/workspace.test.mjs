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
  b.element('video-profile').value = 'standard_narrated';
  b.element('text').value = 'original question';
  b.element('files').files = [new File(['pdf'], 'first.pdf'), new File(['pdf'], 'second.pdf')];
  b.workspace.selectFiles();
  b.workspace.state.fileRows[0].start.value = '1'; b.workspace.state.fileRows[0].end.value = '2';
  b.workspace.state.fileRows[1].start.value = '3'; b.workspace.state.fileRows[1].end.value = '4';
  const submit = b.workspace.submit({preventDefault() {}});
  await flush();
  assert.equal(b.element('upload-reload').disabled, true);
  b.element('output-mode').value = 'both';
  b.element('video-profile').value = 'standard_silent';
  b.element('text').value = 'changed question';
  b.workspace.state.fileRows[1].start.value = '7'; b.workspace.state.fileRows[1].end.value = '8';
  // Repeated submission while uploading must not enqueue a second task.
  await b.workspace.submit({preventDefault() {}});
  unblock(); await submit;
  assert.equal(submitted.output_mode, 'text');
  assert.equal(submitted.video_profile_id, 'standard_narrated');
  assert.equal(submitted.source.instruction, 'original question');
  assert.deepEqual(submitted.source.inputs[2].selection, {kind: 'pages', start: 3, end: 4});
  assert.equal(b.requests.filter(url => url === '/api/v2/generations').length, 1);
  assert.equal(b.element('upload-reload').disabled, false);
});

test('attachment capabilities reload does not issue overlapping requests', async () => {
  let unblock;
  const gate = new Promise(resolve => { unblock = resolve; });
  const b = browser({intercept: url => url === '/api/v2/input-capabilities' ? gate : undefined});
  await flush();
  assert.equal(b.element('upload-reload').disabled, true);
  await b.workspace.loadInputs();
  assert.equal(b.requests.filter(url => url === '/api/v2/input-capabilities').length, 1);
  unblock(); await b.ready;
  assert.equal(b.element('upload-reload').disabled, false);
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

test('saving a revision blocks duplicate saves and new generation while retaining the original draft', async () => {
  let unblock, submitted;
  const gate = new Promise(resolve => { unblock = resolve; });
  const b = browser({search: '?answer_id=' + aid, respond: async (url, options) => {
    if (url === '/api/v2/answers/revisions') { submitted = JSON.parse(options.body); await gate; return response({answer_id: bid}); }
  }}); await b.ready;
  b.workspace.edit(); b.element('revision').value = 'my revised answer';
  const saving = b.workspace.saveRevision();
  await flush();
  for (const name of ['save', 'edit', 'revision', 'generate', 'animate', 'files', 'upload-reload']) assert.equal(b.element(name).disabled, true);
  await Promise.all([b.workspace.saveRevision(), b.workspace.submit(), b.workspace.animate()]);
  b.workspace.edit();
  assert.equal(b.element('revision').value, 'my revised answer');
  assert.equal(b.requests.filter(url => url === '/api/v2/answers/revisions').length, 1);
  assert.ok(!b.requests.some(url => ['/api/v2/generations', '/api/v2/videos'].includes(url)));
  unblock(); await saving;
  assert.deepEqual(submitted, {base_answer_id: aid, markdown: 'my revised answer'});
  assert.equal(b.workspace.state.answer.answer_id, bid);
  assert.equal(b.workspace.state.revisionSaving, false);
  for (const name of ['save', 'edit', 'revision', 'generate', 'animate']) assert.equal(b.element(name).disabled, false);
});

test('a rejected revision preserves its draft and the active animation poll', async () => {
  const b = browser({search: '?answer_id=' + aid, job: {status: 'running', stage: 'video', result: null}, respond: async url =>
    url === '/api/v2/answers/revisions' ? {ok: false, status: 422, json: async () => ({error: {message: '请核对引用'}})} : undefined,
  }); await b.ready;
  await b.workspace.watchJob(jid);
  b.workspace.edit(); b.element('revision').value = 'keep this draft';
  await b.workspace.saveRevision();
  assert.equal(b.workspace.state.jobId, jid);
  assert.equal(b.workspace.state.busy, true);
  assert.equal(b.timers.size, 1);
  assert.equal(b.element('generate').disabled, true);
  assert.equal(b.element('save').disabled, false);
  assert.equal(b.element('editor').hidden, false);
  assert.equal(b.element('revision').value, 'keep this draft');
  assert.match(b.element('message').textContent, /请核对引用/);
});

test('an animation completing during a revision save cannot unlock a second submission', async () => {
  let unblock;
  const gate = new Promise(resolve => { unblock = resolve; });
  const b = browser({search: '?answer_id=' + aid, intercept: url => url.endsWith('/answers/revisions') ? gate : undefined});
  await b.ready;
  const saving = b.workspace.saveRevision();
  await b.workspace.watchJob(jid);
  assert.equal(b.workspace.state.busy, false);
  assert.equal(b.workspace.state.revisionSaving, true);
  assert.equal(b.element('generate').disabled, true);
  await b.workspace.submit();
  assert.ok(!b.requests.includes('/api/v2/generations'));
  unblock(); await saving;
  assert.equal(b.element('generate').disabled, false);
});

test('a stale revision response cannot release a newer active job', async () => {
  let unblock;
  const gate = new Promise(resolve => { unblock = resolve; });
  const b = browser({search: '?answer_id=' + aid, job: {status: 'running', stage: 'video', result: null}, intercept: url => url.endsWith('/answers/revisions') ? gate : undefined});
  await b.ready;
  const saving = b.workspace.saveRevision();
  const epoch = b.workspace.beginView();
  await b.workspace.watchJob(jid, epoch);
  unblock(); await saving;
  assert.equal(b.workspace.state.jobId, jid);
  assert.equal(b.workspace.state.busy, true);
  assert.equal(b.timers.size, 1);
  assert.equal(b.element('generate').disabled, true);
  assert.equal(b.workspace.state.answer.answer_id, aid);
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
  assert.match(links[2].textContent, /页 2/);
  assert.doesNotMatch(links[2].textContent, /block_1|material_/);
  b.element('result-details').open = false; b.element('sources').open = false;
  b.workspace.openSource({target: {closest: () => ({getAttribute: () => '#block_1'})}});
  assert.equal(b.element('result-details').open, true);
  assert.equal(b.element('sources').open, true);
  assert.equal(b.element('answer-meta').textContent, '完整解答 · 可编辑与导出');
});


test('attachment selection appends, keeps page edits, deduplicates and allows removal', async () => {
  const b = browser(); await b.ready;
  const pdf = new File(['pdf'], 'chapter.pdf', {lastModified: 1});
  b.element('files').files = [pdf]; b.workspace.selectFiles();
  const first = b.workspace.state.fileRows[0]; first.start.value = '2'; first.end.value = '3';
  b.element('files').files = [pdf, new File(['notes'], 'notes.md', {lastModified: 2})]; b.workspace.selectFiles();
  assert.equal(b.workspace.state.fileRows.length, 2);
  assert.equal(b.workspace.state.fileRows[0].start.value, '2');
  first.remove.listeners.click();
  assert.deepEqual(b.workspace.state.fileRows.map(row => row.file.name), ['notes.md']);
});

test('unsupported, empty and oversized attachments fail before the existing answer is cleared', async () => {
  for (const file of [new File(['x'], 'program.exe'), new File([], 'empty.txt'), new File(['long'], 'large.txt')]) {
    const b = browser({search: '?answer_id=' + aid}); await b.ready;
    b.workspace.state.inputCapabilities = {...b.workspace.state.inputCapabilities, limits: {...b.workspace.state.inputCapabilities.limits, max_upload_bytes: 3}};
    b.element('files').files = [file]; b.workspace.selectFiles();
    assert.ok(b.workspace.state.fileRows[0].status.classList.contains('error-text'));
    const html = b.element('result').innerHTML;
    await b.workspace.submit();
    assert.equal(b.workspace.state.answer.answer_id, aid);
    assert.equal(b.element('result').innerHTML, html);
    assert.ok(!b.requests.some(url => ['/api/v2/files', '/api/v2/generations'].includes(url)));
    assert.match(b.element('error').textContent, new RegExp(file.name));
  }
});

test('file-only submission uses a complete default instruction and no empty text source', async () => {
  let submitted;
  const b = browser({job: {kind: 'generation', result: null, answer_id: null}, respond: async (url, options) => {
    if (url === '/api/v2/files') return response({file_id: id('file', 'e')});
    if (url === '/api/v2/generations') { submitted = JSON.parse(options.body); return response({job_id: jid}); }
  }}); await b.ready;
  b.element('output-mode').value = 'both'; b.element('video-profile').value = 'standard_silent';
  b.element('files').files = [new File(['question'], 'notes.txt')]; b.workspace.selectFiles();
  await b.workspace.submit();
  assert.equal(submitted.source.inputs.length, 1);
  assert.equal(submitted.source.inputs[0].type, 'file');
  assert.match(submitted.source.instruction, /根据上传或补充材料/);
  assert.equal(submitted.video_profile_id, 'standard_silent');
});

test('PDF range and input-count mistakes do not upload or detach the previous answer', async () => {
  const b = browser({search: '?answer_id=' + aid}); await b.ready;
  b.element('files').files = [new File(['pdf'], 'chapter.pdf')]; b.workspace.selectFiles();
  const row = b.workspace.state.fileRows[0]; row.start.value = '1'; row.end.value = '101';
  await b.workspace.submit();
  assert.match(b.element('error').textContent, /100 页/);
  row.start.value = ''; row.end.value = '';
  b.workspace.state.inputCapabilities = {...b.workspace.state.inputCapabilities, limits: {...b.workspace.state.inputCapabilities.limits, max_inputs: 1}};
  b.element('text').value = '问题'; await b.workspace.submit();
  assert.match(b.element('error').textContent, /最多 1 份输入/);
  assert.equal(b.workspace.state.answer.answer_id, aid);
  assert.ok(!b.requests.includes('/api/v2/files'));
});

test('server upload rejection preserves the previous answer and offers a file-specific retry', async () => {
  const b = browser({search: '?answer_id=' + aid, respond: async url => url === '/api/v2/files' ? {ok: false, status: 415, json: async () => ({error: {message: 'bad.txt：文本文件必须使用 UTF-8 编码'}})} : undefined});
  await b.ready;
  b.element('files').files = [new File(['bytes'], 'bad.txt')]; b.workspace.selectFiles();
  await b.workspace.submit();
  assert.equal(b.workspace.state.answer.answer_id, aid);
  assert.equal(b.ls.get('manimagent.answer'), aid);
  assert.match(b.workspace.state.fileRows[0].status.textContent, /UTF-8/);
  assert.equal(b.element('generate').disabled, false);
});

test('missing capabilities block attachments while direct text remains usable', async () => {
  let submitted;
  const b = browser({job: {kind: 'generation', result: null, answer_id: null}, respond: async (url, options) => {
    if (url === '/api/v2/input-capabilities') return {ok: false, status: 503, json: async () => ({error: {message: 'offline'}})};
    if (url === '/api/v2/generations') { submitted = JSON.parse(options.body); return response({job_id: jid}); }
  }}); await b.ready;
  b.element('files').files = [new File(['notes'], 'notes.txt')]; b.workspace.selectFiles();
  await b.workspace.submit(); assert.equal(submitted, undefined);
  b.workspace.state.fileRows[0].remove.listeners.click();
  b.element('text').value = '2+2'; b.element('output-mode').value = 'text';
  await b.workspace.submit(); assert.equal(submitted.source.inputs[0].text, '2+2');
});

test('configuration readiness is checked before replacing existing answer or video', async () => {
  const b = browser({search: '?answer_id=' + aid, generationReadiness: () => '请配置语音服务'}); await b.ready;
  b.element('text').value = '新的问题'; await b.workspace.submit();
  assert.equal(b.workspace.state.answer.answer_id, aid);
  assert.match(b.element('error').textContent, /请配置语音服务/);
  await b.workspace.animate();
  assert.match(b.element('message').textContent, /请配置语音服务/);
  assert.ok(!b.requests.includes('/api/v2/videos'));
});

test('rejected animation retries preserve the playable video and resumable job binding', async () => {
  const b = browser({search: '?video_id=' + vid, respond: async url => url === '/api/v2/videos' ? {ok: false, status: 503, json: async () => ({error: {message: '队列暂时不可用'}})} : undefined});
  await b.ready;
  b.workspace.state.resumeJobId = jid;
  const epoch = b.workspace.state.epoch, player = b.element('video-player').src;
  await b.workspace.animate();
  assert.equal(b.workspace.state.epoch, epoch);
  assert.equal(b.workspace.state.videoId, vid);
  assert.equal(b.workspace.state.resumeJobId, jid);
  assert.equal(b.element('video-player').src, player);
  assert.equal(b.element('video-player').hidden, false);
  assert.equal(b.ls.get('manimagent.video'), vid);
  assert.equal(b.window.location.search, '?video_id=' + vid);
  assert.match(b.element('message').textContent, /队列暂时不可用/);
  assert.equal(b.element('animate').disabled, false);
});


test('known pictures require vision configuration before upload without affecting text files', async () => {
  const b = browser({search: '?answer_id=' + aid, generationReadiness: ({requiresVision}) => requiresVision ? '请配置视觉模型' : null}); await b.ready;
  b.element('files').files = [new File(['png'], 'diagram.png')]; b.workspace.selectFiles();
  await b.workspace.submit();
  assert.match(b.element('error').textContent, /视觉模型/);
  assert.equal(b.workspace.state.answer.answer_id, aid);
  assert.ok(!b.requests.includes('/api/v2/files'));
});

test('PowerPoint selection uses slide bounds while preserving user-selected narration mode', async () => {
  let submitted;
  const b = browser({job: {kind: 'generation', result: null, answer_id: null}, respond: async (url, options) => {
    if (url === '/api/v2/files') return response({file_id: id('file', 'e')});
    if (url === '/api/v2/generations') { submitted = JSON.parse(options.body); return response({job_id: jid}); }
  }}); await b.ready;
  b.element('files').files = [new File(['slides'], 'lesson.pptx')]; b.workspace.selectFiles();
  b.workspace.state.fileRows[0].start.value = '2'; b.workspace.state.fileRows[0].end.value = '4';
  b.element('output-mode').value = 'both'; b.element('video-profile').value = 'standard_narrated';
  await b.workspace.submit();
  assert.deepEqual(submitted.source.inputs[0].selection, {kind: 'slides', start: 2, end: 4});
  assert.equal(submitted.video_profile_id, 'standard_narrated');
});
