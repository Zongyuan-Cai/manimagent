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

function browser({storage = {}, search = '', job = {}, video = {}, intercept} = {}) {
  const elements = new Map(), requests = [], ls = new Map(Object.entries(storage));
  const element = name => {
    if (!elements.has(name)) elements.set(name, {
      hidden: true, value: '', textContent: '', innerHTML: '', style: {}, listeners: {},
      addEventListener(event, fn) { this.listeners[event] = fn; }, append() {}, replaceChildren() {},
      removeAttribute(key) { delete this[key]; }, setAttribute() {}, focus() {},
    });
    return elements.get(name);
  };
  const context = vm.createContext({
    document: {getElementById: element, createElement: element},
    window: {location: {search}}, URLSearchParams, TextEncoder, crypto: webcrypto, navigator: {},
    localStorage: {getItem: k => ls.get(k), setItem: (k, v) => ls.set(k, v), removeItem: k => ls.delete(k)},
    setTimeout() {}, clearTimeout() {},
    fetch: async (url, options) => {
      requests.push(url);
      if (intercept) await intercept(url);
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
