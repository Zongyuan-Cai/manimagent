import test from 'node:test';
import assert from 'node:assert/strict';
import {createSettings} from '../../src/manimagent/web/settings.js';
import {fakeDOM} from './helpers.mjs';

const data = revision => ({revision, models: {}, roles: {}, answer_context_tokens: 24000, answer_check: true});

test('configuration reload and save cannot overwrite each other in flight', async () => {
  const {dom, element} = fakeDOM(), pending = [], calls = [];
  const settings = createSettings({dom, api: {
    health: async () => ({ready: true}),
    request: (url, options) => {
      calls.push(options?.method || 'GET');
      return new Promise(resolve => pending.push(resolve));
    },
  }});
  const initialLoad = settings.load();
  const submit = () => settings.save({preventDefault() {}});
  await submit(); assert.deepEqual(calls, ['GET']);
  pending.shift()(data(1)); await initialLoad;
  const reload = settings.load();
  await submit(); assert.deepEqual(calls, ['GET', 'GET']);
  pending.shift()(data(1)); await reload;
  const save = submit();
  assert.equal(element('llm-reload').disabled, true);
  await settings.load();
  assert.deepEqual(calls, ['GET', 'GET', 'PUT']);
  pending.shift()(data(2)); await save;
  assert.equal(settings.state.revision, 2);
  assert.equal(element('llm-reload').disabled, false);
  assert.equal(element('llm-save').disabled, false);
});

test('settings instances keep independent busy state, model drafts and revisions', async () => {
  const firstDOM = fakeDOM(), secondDOM = fakeDOM();
  let finishFirst;
  const first = createSettings({dom: firstDOM.dom, api: {request: () => new Promise(resolve => { finishFirst = resolve; }), health: async () => ({ready: true})}});
  const second = createSettings({dom: secondDOM.dom, api: {request: async () => data(8), health: async () => ({ready: true})}});
  const pending = first.load();
  await second.load();
  second.add();
  assert.equal(first.state.busy, true);
  assert.equal(second.state.busy, false);
  assert.equal(first.state.cards.length, 0);
  assert.equal(second.state.cards.length, 1);
  assert.equal(second.state.revision, 8);
  finishFirst(data(3)); await pending;
  assert.equal(first.state.revision, 3);
  assert.equal(second.state.cards.length, 1);
});

test('saving an added model preserves typed configuration and binds all five roles', async () => {
  const {dom, element} = fakeDOM(); let submitted;
  const settings = createSettings({dom, api: {
    health: async () => ({ready: true}),
    request: async (_url, options) => { if (options) submitted = JSON.parse(options.body); return data(options ? 2 : 1); },
  }});
  await settings.load(); settings.add();
  const card = settings.state.cards[0];
  card.fields.base_url.value = 'https://gateway.example/v1'; card.fields.model.value = 'example-model';
  card.fields.api_key.value = 'test-key';
  for (const name of ['llm-writer', 'llm-checker', 'llm-planner', 'llm-scene', 'llm-plan-checker']) element(name).value = card.identifier;
  await settings.save({preventDefault() {}});
  assert.equal(submitted.expected_revision, 1);
  assert.equal(submitted.models.model_1.config.max_output_tokens, 6000);
  assert.equal(submitted.models.model_1.config.json_mode, true);
  assert.equal(submitted.models.model_1.api_key, 'test-key');
  assert.equal(Object.values(submitted.roles).filter(role => role === 'model_1').length, 5);
  assert.equal(settings.state.busy, false);
});

test('presets configure the expected authentication and let one model serve every role', async () => {
  const {dom, element} = fakeDOM(); let submitted;
  const settings = createSettings({dom, api: {
    health: async () => ({ready: true}),
    request: async (_url, options) => { if (options) submitted = JSON.parse(options.body); return data(options ? 2 : 1); },
  }});
  await settings.load(); settings.add();
  const card = settings.state.cards[0]; card.provider.value = 'mimo'; card.provider.listeners.change();
  assert.equal(card.fields.base_url.value, 'https://api.xiaomimimo.com/v1');
  assert.equal(card.fields.auth_mode.value, 'api_key_header');
  assert.equal(card.fields.api_key_header.value, 'api-key');
  assert.equal(card.fields.token_parameter.value, 'max_completion_tokens');
  card.fields.model.value = 'available-text-model'; card.fields.api_key.value = 'new-key'; card.use.onclick();
  await settings.save();
  assert.equal(Object.values(submitted.roles).filter(role => role === card.identifier).length, 5);
  assert.equal(submitted.roles.material_reader, null);
  assert.equal(submitted.models.model_1.config.auth_mode, 'api_key_header');
  assert.equal(element('llm-reload').disabled, false);
});

test('a pasted full generation URL is normalized and changing the basic URL clears its old override', async () => {
  const {dom} = fakeDOM(); let submitted;
  const settings = createSettings({dom, api: {
    health: async () => ({ready: true}),
    request: async (_url, options) => { if (options) submitted = JSON.parse(options.body); return data(); },
  }});
  await settings.load(); settings.add(); const card = settings.state.cards[0];
  card.fields.generate_url.value = 'https://old.example/generation';
  card.fields.base_url.value = 'https://new.example/v1/chat/completions'; card.fields.base_url.listeners.input();
  assert.equal(card.fields.generate_url.value, '');
  card.fields.model.value = 'model'; card.use.onclick(); await settings.save();
  assert.equal(submitted.models.model_1.config.base_url, 'https://new.example/v1');
  assert.equal(submitted.models.model_1.config.generate_url, 'https://new.example/v1/chat/completions');
});

test('changing endpoint does not silently reuse a stored key', async () => {
  const {dom, element} = fakeDOM(); let writes = 0;
  const initial = {...data(1), models: {text: {config: {base_url: 'https://old.example/v1', model: 'm', auth_mode: 'bearer', max_output_tokens: 6000, context_window: 32000, json_mode: true}, key_configured: true}}};
  const settings = createSettings({dom, api: {
    health: async () => ({ready: true}),
    request: async (_url, options) => { if (options) writes++; return initial; },
  }});
  await settings.load(); const card = settings.state.cards[0];
  assert.equal(card.fields.api_key.value, '');
  card.fields.base_url.value = 'https://different.example/v1';
  await settings.save(); assert.equal(writes, 0); assert.match(element('llm-message').textContent, /地址已改变/);
  card.fields.api_key.value = 'replacement-key';
  await settings.save(); assert.equal(writes, 1);
});

test('probing locks the entire connection draft and blocks overlapping saves/reloads', async () => {
  const {dom, created} = fakeDOM(); let finish; let requests = 0;
  const settings = createSettings({dom, api: {
    health: async () => ({ready: true}),
    request: async () => { requests++; return data(); },
    post: () => new Promise(resolve => { finish = resolve; }),
  }});
  await settings.load(); settings.add(); const card = settings.state.cards[0];
  const testButton = created.find(node => node.textContent === '测试连接');
  const pending = testButton.onclick();
  assert.equal(card.fieldset.disabled, true);
  await settings.save(); await settings.load(); assert.equal(requests, 1);
  finish({message: '成功', elapsed_seconds: 1, usage: {model_calls: 1}}); await pending;
  assert.equal(card.fieldset.disabled, false);
});

test('successful save is not described as failed when refreshing health fails', async () => {
  const {dom, element} = fakeDOM(); let refreshes = 0;
  const settings = createSettings({dom, api: {
    request: async () => data(2),
    health: async () => { if (refreshes++) throw Error('网络失败'); return {ready: true}; },
  }});
  await settings.load(); await settings.save();
  assert.match(element('llm-message').textContent, /核心配置已保存，但状态刷新失败/);
  assert.equal(settings.state.revision, 2);
});

test('loaded configuration stays editable when health refresh fails and reader can follow the writer', async () => {
  const {dom, element} = fakeDOM();
  const settings = createSettings({dom, api: {
    request: async () => ({...data(3), roles: {material_reader: null}}),
    health: async () => { throw Error('网络失败'); },
  }});
  await settings.load();
  assert.equal(settings.state.revision, 3);
  assert.equal(element('llm-material-reader').value, '');
  assert.equal(element('llm-save').disabled, false);
  assert.equal(element('llm-state').textContent, '状态待刷新');
  assert.match(element('llm-message').textContent, /核心配置已读取，但状态刷新失败/);
});

test('visual reader is optional, uses only declared vision models, and follows the single-model action', async () => {
  const {dom, element} = fakeDOM(); let submitted;
  const settings = createSettings({dom, api: {
    health: async () => ({ready: true}),
    request: async (_url, options) => { if (options) submitted = JSON.parse(options.body); return data(); },
  }});
  await settings.load(); settings.add(); const text = settings.state.cards[0];
  text.fields.model.value = 'text'; settings.add(); const vision = settings.state.cards[1];
  vision.fields.model.value = 'vision'; vision.fields.supports_vision.checked = true; vision.fields.supports_vision.listeners.change();
  assert.deepEqual(element('llm-material-reader').children.map(option => option.value), ['', vision.identifier]);
  vision.use.onclick(); assert.equal(element('llm-material-reader').value, vision.identifier);
  await settings.save();
  assert.equal(submitted.models[vision.identifier].config.supports_vision, true);
  assert.equal(submitted.models[text.identifier].config.supports_vision, false);
  assert.equal(submitted.roles.material_reader, vision.identifier);
});

test('model cards keep basic connection fields visible and put protocol and vision controls in closed advanced settings', async () => {
  const {dom, created} = fakeDOM();
  const settings = createSettings({dom, api: {request: async () => data(), health: async () => ({ready: true})}});
  await settings.load(); settings.add();
  const card = settings.state.cards[0];
  const advanced = card.fieldset.children.find(node => node.tagName === 'details');
  const contains = (parent, target) => parent === target || parent.children?.some(child => contains(child, target));
  assert.equal(!!advanced.open, false);
  for (const field of ['auth_mode', 'generate_url', 'supports_vision', 'max_output_tokens', 'thinking']) assert.ok(contains(advanced, card.fields[field]));
  for (const field of ['base_url', 'model', 'api_key']) assert.equal(!!contains(advanced, card.fields[field]), false);
  assert.equal(!!contains(advanced, card.provider), false);
  assert.ok(contains(advanced, created.find(node => node.textContent === '测试结构化输出')));
  assert.equal(!!contains(advanced, created.find(node => node.textContent === '测试连接')), false);
});
