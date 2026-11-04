import {readFileSync} from 'node:fs';
import {webcrypto, createHash} from 'node:crypto';
import {createApi} from '../../src/manimagent/web/api.js';
import {createDOM} from '../../src/manimagent/web/dom.js';
import {createWorkspace} from '../../src/manimagent/web/workspace.js';

export const id = (prefix, hex) => prefix + '_' + hex.repeat(32);
export const aid = id('answer', 'a'), bid = id('answer', 'b'), vid = id('video', 'c'), jid = id('job', 'd');
export const hash = text => createHash('sha256').update(text).digest('hex');
export const flush = async () => { for (let i = 0; i < 30; i++) await new Promise(r => setImmediate(r)); };
export const response = data => ({ok: true, status: 200, json: async () => data, text: async () => '<p>' + data.markdown + '</p>'});
const html = readFileSync(new URL('../../src/manimagent/web/index.html', import.meta.url), 'utf8');

export function fakeDOM() {
  const elements = new Map(), created = [];
  function create(tag) {
    let value = '';
    const classes = new Set();
    const element = {
      tagName: tag, hidden: false, disabled: false, checked: false, textContent: '', innerHTML: '', style: {}, listeners: {}, children: [], attributes: {},
      get value() { return value; }, set value(next) { value = String(next ?? ''); },
      classList: {add: name => classes.add(name), remove: name => classes.delete(name), contains: name => classes.has(name)},
      addEventListener(event, handler) { this.listeners[event] = handler; },
      append(...nodes) { for (const node of nodes) { node.parent = this; this.children.push(node); if (this.tagName === 'select' && !this.value) this.value = node.value; } },
      replaceChildren(...nodes) { this.children = []; this.textContent = ''; this.innerHTML = ''; this.append(...nodes); },
      removeAttribute(key) { delete this[key]; delete this.attributes[key]; },
      setAttribute(key, value) { this.attributes[key] = String(value); },
      getAttribute(key) { return this.attributes[key] ?? this[key] ?? null; },
      focus() { this.focused = true; },
    };
    created.push(element);
    return element;
  }
  for (const match of html.matchAll(/<([\w-]+)\b[^>]*\bid="([^"]+)"[^>]*>/g)) {
    const node = create(match[1]); node.id = match[2]; node.hidden = /\bhidden\b/.test(match[0]); elements.set(node.id, node);
  }
  const element = name => {
    if (!elements.has(name)) throw new Error('Unknown DOM ID: ' + name);
    return elements.get(name);
  };
  const document = {
    getElementById: element, createElement: create, createTextNode: text => ({textContent: text}),
    querySelectorAll: selector => {
      if (selector !== '.llm-tests button') throw Error('Unexpected selector ' + selector);
      return created.filter(node => node.tagName === 'button' && node.parent?.className === 'llm-tests');
    },
  };
  return {dom: createDOM(document), document, element, created};
}

export function browser({storage = {}, search = '', job = {}, video = {}, intercept, respond, crypto = webcrypto} = {}) {
  const {dom, element} = fakeDOM(), requests = [], ls = new Map(Object.entries(storage));
  const window = {location: {search, href: 'http://localhost/' + search}, navigator: {}};
  window.history = {replaceState(_state, _title, url) { window.location.href = String(url); window.location.search = new URL(url).search; }};
  const storageApi = {getItem: key => ls.get(key), setItem: (key, value) => ls.set(key, value), removeItem: key => ls.delete(key)};
  const timers = new Map(); let nextTimer = 1;
  const scheduler = {setTimeout(callback) { const id = nextTimer++; timers.set(id, callback); return id; }, clearTimeout: id => timers.delete(id)};
  const api = createApi(async (url, options) => {
    requests.push(url);
    if (intercept) await intercept(url);
    if (respond) { const result = await respond(url, options); if (result) return result; }
    let result;
    if (url.endsWith('/health')) result = {ready: true, video_issues: []};
    else if (url.startsWith('/api/v2/jobs/')) result = {
      kind: 'video', status: 'completed', stage: 'completed', message: 'done',
      answer_id: aid, result: {type: 'video', id: vid}, diagnostic_asset_ids: [], ...job,
    };
    else if (url.startsWith('/api/v2/videos/')) result = {
      video_id: vid, answer_id: aid, answer_hash: hash('body ' + aid), duration_seconds: 180, frame_mapping: [], ...video,
    };
    else if (url.endsWith('/answers/revisions')) result = {answer_id: bid};
    else if (url.startsWith('/api/v2/answers/')) {
      const identifier = url.split('/')[4];
      result = {answer_id: identifier, markdown: 'body ' + identifier, warnings: [], material_ids: []};
    } else throw Error('Unexpected request ' + url);
    return response(result);
  });
  const workspace = createWorkspace({dom, api, window, storage: storageApi, crypto, scheduler});
  const ready = workspace.restore();
  return {element, requests, ls, workspace, api, window, ready, timers};
}
