import {createApi} from './api.js';
import {createDOM} from './dom.js';
import {createWorkspace} from './workspace.js';
import {createSettings} from './settings.js';
import {createMemory} from './memory.js';
import {createTTSSettings} from './tts-settings.js';
import {createReadiness} from './onboarding.js';
import {createPanels} from './panels.js';

export function init() {
  const dom = createDOM(document);
  const panels = createPanels({dom, document});
  const api = createApi();
  const readiness = createReadiness({dom});
  const getHealth = api.health;
  let healthRequest = 0;
  api.health = async () => {
    const request = ++healthRequest;
    try {
      const data = await getHealth();
      if (request === healthRequest) readiness.update(data);
      return data;
    } catch (error) {
      if (request === healthRequest) readiness.failed();
      throw error;
    }
  };
  const memory = createMemory({dom, api, storage: localStorage, sessionStorage, crypto});
  const workspace = createWorkspace({dom, api, window, storage: localStorage, crypto,
    memoryOptions: memory.options, generationReadiness: readiness.check,
    onCompleted: () => memory.load({preserveMessage: true})});
  const settings = createSettings({dom, api});
  const tts = createTTSSettings({dom, api});
  const openSettings = event => { event?.preventDefault(); panels.openSettings(); };
  const openSpeech = event => { event?.preventDefault(); panels.openSettings('speech'); };
  const openMemory = event => { event?.preventDefault(); panels.openMemory(); return memory.load({preserveMessage: true}); };
  for (const [id, event, handler] of [
    ['form', 'submit', workspace.submit], ['files', 'change', workspace.selectFiles],
    ['material', 'input', workspace.materialChanged], ['result', 'click', workspace.openSource],
    ['llm-form', 'submit', settings.save],
    ['tts-form', 'submit', tts.save],
    ['output-mode', 'change', readiness.render], ['video-profile', 'change', readiness.render],
    ['memory-form', 'submit', memory.observe], ['memory-enabled', 'change', memory.enabledChanged], ['memory-learn', 'change', memory.learningChanged],
  ]) dom.el(id).addEventListener(event, handler);
  for (const [id, handler] of Object.entries({
    example: workspace.example, animate: workspace.animate, copy: workspace.copy,
    edit: workspace.edit, cancel: workspace.cancel, save: workspace.saveRevision, reuse: workspace.reuse,
    'llm-add': settings.add, 'llm-reload': settings.load, 'configuration-link': openSettings,
    'core-configuration-link': openSettings, 'settings-model-tab': openSettings,
    'vision-configuration-link': event => { openSettings(event); dom.el('model-roles').open = true; },
    'tts-configuration-link': openSpeech, 'tts-setup-link': openSpeech,
    'tts-reload': tts.load, 'tts-test': tts.test, 'upload-reload': workspace.loadInputs,
    'memory-reload': memory.load, 'memory-learning-reload': memory.load, 'memory-cleanup': memory.cleanup, 'memory-new-session': memory.newSession,
    'memory-manage-link': openMemory, 'memory-nav-link': openMemory,
  })) dom.el(id).addEventListener('click', handler);
  readiness.render();
  return Promise.all([workspace.restore(), workspace.loadInputs(), settings.load(), tts.load(), memory.load(), api.health().catch(() => {})]);
}

init();
