import {createApi} from './api.js';
import {createDOM} from './dom.js';
import {createWorkspace} from './workspace.js';
import {createSettings} from './settings.js';
import {createMemory} from './memory.js';

export function init() {
  const dom = createDOM(document);
  const api = createApi();
  const memory = createMemory({dom, api, storage: localStorage, sessionStorage, crypto});
  const workspace = createWorkspace({dom, api, window, storage: localStorage, crypto,
    memoryOptions: memory.options, onCompleted: () => memory.load({preserveMessage: true})});
  const settings = createSettings({dom, api, onHealth(data) {
    const banner = dom.el('health');
    banner.textContent = data.ready ? (data.video_issues?.length ? data.video_issues.join('；') : '') : `请先完成右上角的模型设置。${data.issues.join('；')}。`;
    banner.style.display = data.ready && !data.video_issues?.length ? 'none' : 'block';
  }});
  for (const [id, event, handler] of [
    ['form', 'submit', workspace.submit], ['files', 'change', workspace.selectFiles],
    ['material', 'input', workspace.materialChanged], ['result', 'click', workspace.openSource],
    ['llm-form', 'submit', settings.save],
    ['memory-form', 'submit', memory.observe], ['memory-enabled', 'change', memory.enabledChanged],
  ]) dom.el(id).addEventListener(event, handler);
  for (const [id, handler] of Object.entries({
    example: workspace.example, animate: workspace.animate, copy: workspace.copy,
    edit: workspace.edit, cancel: workspace.cancel, save: workspace.saveRevision, reuse: workspace.reuse,
    'llm-add': settings.add, 'llm-reload': settings.load, 'configuration-link': settings.open,
    'memory-reload': memory.load, 'memory-cleanup': memory.cleanup, 'memory-new-session': memory.newSession,
  })) dom.el(id).addEventListener('click', handler);
  return Promise.all([workspace.restore(), settings.load(), memory.load()]);
}

init();
