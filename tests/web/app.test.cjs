const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const {webcrypto, createHash} = require('node:crypto');
const source = fs.readFileSync('src/manimagent/web/app.js', 'utf8');
const id = (prefix, hex) => prefix + '_' + hex.repeat(32);
const aid = id('answer', 'a'), bid = id('answer', 'b'), vid = id('video', 'c'), jid = id('job', 'd');
const hash = text => createHash('sha256').update(text).digest('hex');
const flush = async () => { for (let i = 0; i < 30; i++) await new Promise(r => setImmediate(r)); };

function browser({storage = {}, search = '', job = {}, video = {}, intercept, respond} = {}) {
  const elements = new Map(), requests = [], ls = new Map(Object.entries(storage));
  const element = name => {
    if (!elements.has(name)) elements.set(name, {
      hidden: true, value: '', textContent: '', innerHTML: '', style: {}, listeners: {},
      addEventListener(event, fn) { this.listeners[event] = fn; }, append() {}, replaceChildren() { this.textContent = ''; },
      removeAttribute(key) { delete this[key]; }, setAttribute() {}, focus() {},
    });
    return elements.get(name);
  };
  const context = vm.createContext({
    document: {getElementById: element, createElement: element},
    window: {location: {search}}, URLSearchParams, TextEncoder, FormData, crypto: webcrypto, navigator: {},
    localStorage: {getItem: k => ls.get(k), setItem: (k, v) => ls.set(k, v), removeItem: k => ls.delete(k)},
    setTimeout() {}, clearTimeout() {},
    fetch: async (url, options) => {
      requests.push(url);
      if (intercept) await intercept(url);
      if (respond) { const response = await respond(url, options); if (response) return response; }
      let result;
      if (url.endsWith('/health')) result = {ready: true, video_issues: []};
      else if (url.startsWith('/api/v2/jobs/')) result = {
        kind: 'video', status: 'completed', stage: 'completed', message: 'done',
        answer_id: aid, result: {type: 'video', id: vid}, diagnostic_asset_ids: [], ...job,
      };
      else if (url.startsWith('/api/v2/videos/')) result = {
        video_id: vid, answer_id: aid, answer_hash: hash('body ' + aid), duration_seconds: 180, frame_mapping: [], ...video,
      };
      else if (url.endsWith('/answers/revisions')) {
        assert.equal(JSON.parse(options.body).base_answer_id, aid);
        result = {answer_id: bid};
      } else if (url.startsWith('/api/v2/answers/')) {
        const identifier = url.split('/')[4];
        result = {answer_id: identifier, markdown: 'body ' + identifier, warnings: [], material_ids: []};
      } else throw Error('Unexpected request ' + url);
      return {ok: true, status: 200, json: async () => result, text: async () => '<p>' + result.markdown + '</p>'};
    },
  });
  vm.runInContext(source, context);
  return {element, requests, ls, run: code => vm.runInContext(code, context)};
}

test('refresh a completed video-only job restores its exact text and actions', async () => {
  const b = browser({storage: {'manimagent.textJob': jid, 'manimagent.answer': bid}});
  await flush();
  assert.equal(b.run('answer.answer_id'), aid);
  assert.equal(b.run('currentVideo'), vid);
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
  await flush();
  b.element('output-mode').value = 'text';
  b.element('text').value = 'original question';
  b.run("fileRows = [{file: 'first.pdf', start: {value: '1'}, end: {value: '2'}}, {file: 'second.pdf', start: {value: '3'}, end: {value: '4'}}]");
  const submit = b.element('form').listeners.submit({preventDefault() {}});
  await flush();
  b.element('output-mode').value = 'both';
  b.element('text').value = 'changed question';
  b.run("fileRows[1].start.value = '7'; fileRows[1].end.value = '8'");
  // Repeated submission while uploading must not enqueue a second task.
  await b.element('form').listeners.submit({preventDefault() {}});
  unblock(); await submit;
  assert.equal(submitted.output_mode, 'text');
  assert.equal(submitted.source.instruction, 'original question');
  assert.deepEqual(submitted.source.inputs[2].selection, {kind: 'pages', start: 3, end: 4});
  assert.equal(b.requests.filter(url => url === '/api/v2/generations').length, 1);
});

test('starting a view clears old status and diagnostic artifacts', async () => {
  const b = browser(); await flush();
  for (const name of ['job', 'message', 'error', 'artifacts', 'status']) b.element(name).textContent = 'old';
  b.element('diagnostics').hidden = false;
  b.run('beginView()');
  for (const name of ['job', 'message', 'error', 'artifacts', 'status']) assert.equal(b.element(name).textContent, '');
  assert.equal(b.element('diagnostics').hidden, true);
});

test('non-JSON gateway failure preserves HTTP status without leaking its body', async () => {
  const b = browser({respond: async url => url === '/broken' ? {ok: false, status: 502, json: async () => { throw new SyntaxError('private gateway body'); }} : undefined});
  await flush();
  await assert.rejects(b.run("request('/broken')"), error => error.status === 502 && error.message === '请求失败（HTTP 502）');
});
test('running video job restores its source before result exists', async () => {
  const b = browser({storage: {'manimagent.textJob': jid}, job: {status: 'running', stage: 'video', result: null}});
  await flush();
  assert.equal(b.run('answer.answer_id'), aid);
  assert.equal(b.run('currentVideo'), null);
});
test('shared answer overrides saved job, answer and video', async () => {
  const b = browser({storage: {'manimagent.textJob': jid, 'manimagent.answer': aid, 'manimagent.video': vid}, search: '?answer_id=' + bid});
  await flush();
  assert.equal(b.run('answer.answer_id'), bid);
  assert.equal(b.run('currentVideo'), null);
  assert.ok(!b.requests.some(url => url.includes('/videos/') || url.includes('/jobs/')));
});
test('shared video loads source and rejects mismatched ID or content hash', async () => {
  for (const options of [
    {search: '?video_id=' + vid},
    {search: '?answer_id=' + bid + '&video_id=' + vid},
    {search: '?video_id=' + vid, video: {answer_hash: 'wrong'}},
  ]) {
    const b = browser(options);
    await flush();
    const rejected = !!options.video || options.search.includes('answer_id');
    assert.equal(b.run('currentVideo'), rejected ? null : vid);
    if (rejected) assert.match(b.element('message').textContent, /不匹配/);
  }
});
test('a delayed answer response cannot overwrite a newer view', async () => {
  let unblock;
  const gate = new Promise(r => { unblock = r; });
  const b = browser({search: '?answer_id=' + aid, intercept: url => url.endsWith(aid) ? gate : undefined});
  await flush();
  await b.run(`showAnswer('${bid}', beginView())`);
  unblock(); await flush();
  assert.equal(b.run('answer.answer_id'), bid);
  assert.equal(b.ls.get('manimagent.answer'), bid);
  assert.match(b.element('result').innerHTML, new RegExp(bid));
});
test('saving an edit detaches an animation poll already in flight', async () => {
  let unblock;
  const gate = new Promise(r => { unblock = r; });
  const b = browser({search: '?answer_id=' + aid, intercept: url => url.includes('/jobs/') ? gate : undefined});
  await flush();
  const pendingPoll = b.run(`currentJob = '${jid}'; poll()`);
  await b.element('save').onclick();
  unblock(); await pendingPoll;
  await flush();
  assert.equal(b.run('answer.answer_id'), bid);
  assert.equal(b.run('currentJob'), null);
  assert.equal(b.run('currentVideo'), null);
  assert.equal(b.element('video-panel').hidden, true);
  assert.equal(b.ls.has('manimagent.textJob'), false);
});

test('a saved revision that fails to load releases detached animation controls', async () => {
  const b = browser({
    search: '?answer_id=' + aid,
    respond: async url => url.endsWith(bid) ? {ok: false, status: 503, json: async () => ({error: {message: '正文暂不可用'}})} : undefined,
  });
  await flush();
  b.run(`currentJob = '${jid}'; setBusy(true)`);
  await b.element('save').onclick();
  assert.equal(b.run('currentJob'), null);
  assert.equal(b.element('generate').disabled, false);
  assert.equal(b.element('animate').disabled, false);
  assert.match(b.element('message').textContent, /正文暂不可用/);
});
