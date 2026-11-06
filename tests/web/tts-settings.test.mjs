import test from 'node:test';
import assert from 'node:assert/strict';
import {createTTSSettings} from '../../src/manimagent/web/tts-settings.js';
import {fakeDOM} from './helpers.mjs';

const config = {enabled: false, base_url: 'https://api.xiaomimimo.com/v1', model: 'mimo-v2.5-tts', voice: 'mimo_default', style: '自然讲解', api_key_env: 'local_secret_example', timeout: 60};
const saved = (revision = 3, enabled = false) => ({revision, config: {...config, enabled}, key_configured: true, state: enabled ? 'ready' : 'disabled', issues: []});

test('TTS loads independently, never prefills keys, and saves a new key with its revision', async () => {
  const {dom, element} = fakeDOM(); let submitted;
  const settings = createTTSSettings({dom, api: {
    request: async (_url, options) => { if (options) submitted = JSON.parse(options.body); return saved(options ? 4 : 3, !!options); },
    health: async () => ({ready: true}),
  }});
  await settings.load();
  assert.equal(element('tts-api-key').value, '');
  assert.equal(element('tts-state').textContent, '默认无声');
  element('tts-api-key').value = 'sample-new-key'; element('tts-enabled').checked = true;
  await settings.save();
  assert.equal(submitted.expected_revision, 3);
  assert.equal(submitted.draft.api_key, 'sample-new-key');
  assert.equal(submitted.draft.config.timeout, 60);
  assert.equal(submitted.draft.config.enabled, true);
  assert.equal(element('tts-api-key').value, '');
  assert.equal(settings.state.revision, 4);
});

test('TTS preview uses draft without saving or changing enabled default; failure clears old audio', async () => {
  const {dom, element} = fakeDOM(); const calls = []; let fail = false;
  const settings = createTTSSettings({dom, api: {
    request: async url => { calls.push(url); return saved(); },
    health: async () => ({ready: true}),
    post: async (url, body) => { calls.push(url); assert.equal(body.draft.config.enabled, false); if (fail) throw Error('鉴权失败'); return {ok: true, media_type: 'audio/wav', audio_base64: 'UklGRg==', duration_seconds: 2, calls: 1}; },
  }});
  await settings.load(); element('tts-test-text').value = '测试中文语音';
  await settings.test();
  assert.equal(element('tts-audio').hidden, false);
  assert.equal(element('tts-audio').src, 'data:audio/wav;base64,UklGRg==');
  assert.equal(settings.state.revision, 3);
  assert.match(element('tts-message').textContent, /尚未保存/);
  fail = true; await settings.test();
  assert.equal(element('tts-audio').hidden, true);
  assert.equal(element('tts-audio').src, undefined);
  assert.match(element('tts-message').textContent, /试听失败/);
  assert.deepEqual(calls, ['/api/v2/tts-settings', '/api/v2/tts-settings/test', '/api/v2/tts-settings/test']);
});

test('preview locks settings against edits, duplicate calls, saving and reloading until complete', async () => {
  const {dom, element} = fakeDOM(); let finish; let posts = 0; let reads = 0;
  const settings = createTTSSettings({dom, api: {
    request: async () => { reads++; return saved(); },
    health: async () => ({ready: true}),
    post: () => { posts++; return new Promise(resolve => { finish = resolve; }); },
  }});
  await settings.load(); const pending = settings.test();
  assert.equal(element('tts-base-url').disabled, true);
  assert.equal(element('tts-api-key').disabled, true);
  await Promise.all([settings.save(), settings.load(), settings.test()]);
  assert.equal(posts, 1); assert.equal(reads, 1);
  finish({ok: true, media_type: 'audio/wav', audio_base64: 'UklGRg==', duration_seconds: 2, calls: 1});
  await pending; assert.equal(element('tts-base-url').disabled, false);
});

test('TTS refuses to forward an old key to another endpoint and normalizes a pasted full URL', async () => {
  const {dom, element} = fakeDOM(); const posts = [];
  const settings = createTTSSettings({dom, api: {
    request: async () => saved(),
    health: async () => ({ready: true}),
    post: async (_url, body) => { posts.push(body); return {ok: true, media_type: 'audio/wav', audio_base64: 'UklGRg==', duration_seconds: 2, calls: 1}; },
  }});
  await settings.load();
  element('tts-base-url').value = 'https://other.example/v1';
  await settings.test(); assert.equal(posts.length, 0); assert.match(element('tts-message').textContent, /地址已改变/);
  element('tts-base-url').value = 'https://api.xiaomimimo.com/v1/chat/completions';
  await settings.test(); assert.equal(posts[0].draft.config.base_url, config.base_url);
  assert.equal('api_key' in posts[0].draft, false);
});

test('save conflict preserves draft and revision, including key; clear-key conflict never sends', async () => {
  const {dom, element} = fakeDOM(); let writes = 0;
  const settings = createTTSSettings({dom, api: {health: async () => ({ready: true}), request: async (_url, options) => {
    if (options) { writes++; throw Error('配置已被修改，请重新载入后保存'); } return saved();
  }}});
  await settings.load(); element('tts-api-key').value = 'new-key'; element('tts-clear-key').checked = true;
  await settings.save(); assert.equal(writes, 0);
  element('tts-clear-key').checked = false;
  await settings.save(); assert.equal(writes, 1);
  assert.equal(settings.state.revision, 3); assert.equal(element('tts-api-key').value, 'new-key');
  assert.match(element('tts-message').textContent, /配置已被修改/);
});

test('reloading saved speech configuration requests fresh generation readiness', async () => {
  const {dom} = fakeDOM(); const updates = [];
  let enabled = false;
  const settings = createTTSSettings({dom, api: {
    request: async () => saved(enabled ? 4 : 3, enabled),
    health: async () => { updates.push(enabled); return {ready: true, tts: {enabled, key_configured: true}}; },
  }});
  await settings.load(); enabled = true; await settings.load();
  assert.deepEqual(updates, [false, true]);
  assert.equal(settings.state.revision, 4);
});

test('loading optional or healthy speech settings is quiet while incomplete enabled speech remains actionable', async () => {
  const {dom, element} = fakeDOM(); let current = {...saved(), key_configured: false, issues: ['请填写语音密钥']};
  const settings = createTTSSettings({dom, api: {request: async () => current, health: async () => ({ready: true})}});
  await settings.load(); assert.equal(element('tts-message').textContent, '');
  current = {...current, config: {...config, enabled: true}, state: 'needs_configuration'};
  await settings.load(); assert.match(element('tts-message').textContent, /请填写语音密钥/);
  current = saved(5, true); await settings.load();
  assert.equal(element('tts-message').textContent, '');
  assert.match(element('tts-key-state').textContent, /留空保留/);
});
