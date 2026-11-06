// Native dialogs keep secondary tools out of the workspace and retain unsaved forms.
export function createPanels({dom, document}) {
  const {el} = dom;
  let active = null, returnFocus = null;

  function open(id) {
    const panel = el(id);
    if (panel.open) return;
    const origin = active ? returnFocus : document.activeElement;
    if (active) active.close();
    returnFocus = origin;
    active = panel;
    panel.showModal();
    document.body.classList.add('dialog-open');
  }

  function openSettings(section = 'core') {
    const speech = section === 'speech';
    el('llm-settings').hidden = speech;
    el('tts-settings').hidden = !speech;
    el('settings-model-tab').setAttribute('aria-pressed', String(!speech));
    el('tts-configuration-link').setAttribute('aria-pressed', String(speech));
    open('settings-dialog');
  }

  for (const [id, closeId] of [['settings-dialog', 'settings-close'], ['memory-dialog', 'memory-close']]) {
    const panel = el(id);
    el(closeId).addEventListener('click', () => panel.close());
    panel.addEventListener('close', () => {
      if (active !== panel) return;
      active = null;
      document.body.classList.remove('dialog-open');
      returnFocus?.focus();
      returnFocus = null;
    });
  }

  return {openSettings, openMemory: () => open('memory-dialog')};
}
