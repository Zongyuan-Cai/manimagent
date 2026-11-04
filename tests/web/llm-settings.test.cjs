const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

test('configuration reload and save cannot overwrite each other in flight', async () => {
  const elements = new Map(), pending = [], calls = [];
  const el = id => {
    if (!elements.has(id)) elements.set(id, {
      value: '', classList: {remove() {}, add() {}}, replaceChildren() {}, listeners: {},
      addEventListener(event, fn) { this.listeners[event] = fn; },
    });
    return elements.get(id);
  };
  const data = revision => ({revision, models: {}, roles: {}, answer_context_tokens: 24000, answer_check: true});
  const context = vm.createContext({
    el, document: {}, refreshHealth: async () => ({ready: true}),
    request: (url, options) => {
      calls.push(options?.method || 'GET');
      return new Promise(resolve => pending.push(resolve));
    },
  });
  vm.runInContext(fs.readFileSync('src/manimagent/web/llm-settings.js', 'utf8'), context);
  const flush = async () => { for (let i = 0; i < 5; i++) await new Promise(r => setImmediate(r)); };
  const submit = () => el('llm-form').listeners.submit({preventDefault() {}});
  // Initial load and an explicit reload both exclude a concurrent save.
  await submit(); assert.deepEqual(calls, ['GET']);
  pending.shift()(data(1)); await flush();
  const reload = el('llm-reload').onclick();
  await submit(); assert.deepEqual(calls, ['GET', 'GET']);
  pending.shift()(data(1)); await reload;
  const save = submit();
  assert.equal(el('llm-reload').disabled, true);
  await el('llm-reload').onclick();
  assert.deepEqual(calls, ['GET', 'GET', 'PUT']);
  pending.shift()(data(2)); await save;
  assert.equal(vm.runInContext('llmRevision', context), 2);
  assert.equal(el('llm-reload').disabled, false);
  assert.equal(el('llm-save').disabled, false);
});
