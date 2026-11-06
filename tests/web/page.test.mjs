import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync, readdirSync, existsSync} from 'node:fs';
import {webcrypto} from 'node:crypto';
import {fakeDOM, flush, inputCapabilities, response} from './helpers.mjs';

const web = new URL('../../src/manimagent/web/', import.meta.url);
const html = readFileSync(new URL('index.html', web), 'utf8');
const modules = readdirSync(web).filter(name => name.endsWith('.js'));

test('page IDs, labels, disclosure anchors and static DOM lookups resolve uniquely', () => {
  const ids = [...html.matchAll(/\bid="([^"]+)"/g)].map(match => match[1]);
  const known = new Set(ids);
  assert.equal(known.size, ids.length, 'Duplicate page IDs break event and accessibility bindings');
  const references = [...html.matchAll(/\b(?:for|aria-labelledby|aria-describedby)="([^"]+)"/g)].flatMap(match => match[1].split(/\s+/));
  references.push(...[...html.matchAll(/\bhref="#([^"\s]+)"/g)].map(match => match[1]));
  for (const id of references) assert.ok(known.has(id), `HTML refers to absent #${id}`);
  for (const name of modules) {
    const source = readFileSync(new URL(name, web), 'utf8');
    for (const match of source.matchAll(/\bel\('([^']+)'\)/g)) assert.ok(known.has(match[1]), `${name} refers to absent #${match[1]}`);
  }
});

test('page assets and the complete application module graph exist with no orphan modules', () => {
  for (const match of html.matchAll(/\b(?:src|href)="\/static\/([^"?]+)(?:\?[^"]*)?"/g)) assert.ok(existsSync(new URL(match[1], web)), `Missing page asset ${match[1]}`);
  const seen = new Set();
  function visit(name) {
    if (seen.has(name)) return;
    assert.ok(modules.includes(name), `Missing application module ${name}`); seen.add(name);
    const source = readFileSync(new URL(name, web), 'utf8');
    for (const match of source.matchAll(/\bfrom\s+['"]\.\/([^'"]+)['"]/g)) visit(match[1]);
  }
  visit('main.js');
  assert.deepEqual([...seen].sort(), modules.sort());
});

test('the actual main module starts every page service and binds its forms, buttons and configuration links', async () => {
  const {document, element} = fakeDOM(), calls = [], unexpected = [];
  const storage = () => {
    const entries = new Map();
    return {getItem: key => entries.get(key), setItem: (key, value) => entries.set(key, value), removeItem: key => entries.delete(key)};
  };
  const fixtures = {
    '/api/v2/memory': {entries: [], policy: {}, learning: {tasks: [], counts: {}}},
    '/api/v2/input-capabilities': inputCapabilities,
    '/api/v2/llm-settings': {revision: 1, models: {}, roles: {}, answer_context_tokens: 24000, answer_check: true},
    '/api/v2/tts-settings': {revision: 1, config: {enabled: false, base_url: 'https://tts.example/v1', model: 'tts-model', voice: 'default', style: '自然'}, key_configured: false, state: 'disabled', issues: []},
    '/api/v2/health': {ready: true, issues: [], video_issues: [], vision: {ready: false}, tts: {enabled: false, key_configured: false, issues: []}},
  };
  const replacements = {
    document, window: {location: {href: 'http://localhost/', search: ''}, history: {replaceState() {}}, navigator: {}},
    localStorage: storage(), sessionStorage: storage(), crypto: webcrypto,
    fetch: async url => { calls.push(url); if (!(url in fixtures)) { unexpected.push(url); throw Error(`Unexpected request ${url}`); } return response(fixtures[url]); },
  };
  const originals = Object.fromEntries(Object.keys(replacements).map(key => [key, Object.getOwnPropertyDescriptor(globalThis, key)]));
  try {
    for (const [key, value] of Object.entries(replacements)) Object.defineProperty(globalThis, key, {value, configurable: true});
    await import('../../src/manimagent/web/main.js?page-smoke'); await flush();
    for (const url of Object.keys(fixtures)) assert.ok(calls.includes(url), `Startup skipped ${url}`);
    assert.deepEqual(unexpected, []);
    assert.equal(element('memory-state').textContent, '还没有记忆');
    assert.equal(element('memory-learning-summary').textContent, '暂无偏好更新。');
    assert.equal(element('llm-state').textContent, '已就绪');
    assert.equal(element('tts-state').textContent, '默认无声');
    assert.equal(element('upload-error').textContent, '');
    assert.match(element('files').accept, /\.pdf/);
    assert.equal(element('memory-enabled').checked, false);
    for (const match of html.matchAll(/<button\b[^>]*>/g)) {
      const id = match[0].match(/\bid="([^"]+)"/)?.[1];
      if (id && /\btype="button"/.test(match[0])) assert.equal(typeof element(id).listeners.click, 'function', `Button #${id} is unbound`);
    }
    for (const match of html.matchAll(/<form\b[^>]*\bid="([^"]+)"/g)) assert.equal(typeof element(match[1]).listeners.submit, 'function');
    for (const id of ['configuration-link', 'core-configuration-link', 'vision-configuration-link']) { element(id).listeners.click(); assert.equal(element('settings-dialog').open, true); assert.equal(element('llm-settings').hidden, false); }
    for (const id of ['tts-configuration-link', 'tts-setup-link']) { element(id).listeners.click(); assert.equal(element('settings-dialog').open, true); assert.equal(element('tts-settings').hidden, false); }
    await element('memory-manage-link').listeners.click();
    assert.equal(element('memory-dialog').open, true);
    for (const id of ['files', 'output-mode', 'video-profile', 'memory-enabled', 'memory-learn']) assert.equal(typeof element(id).listeners.change, 'function');
  } finally {
    for (const [key, descriptor] of Object.entries(originals)) {
      if (descriptor) Object.defineProperty(globalThis, key, descriptor);
      else delete globalThis[key];
    }
  }
});
