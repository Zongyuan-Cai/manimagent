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
