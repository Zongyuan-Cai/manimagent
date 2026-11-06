import test from 'node:test';
import assert from 'node:assert/strict';
import {fakeDOM} from './helpers.mjs';
import {createPanels} from '../../src/manimagent/web/panels.js';

test('settings switch categories without reloading or losing connection drafts', () => {
  const {dom, document, element} = fakeDOM();
  const panels = createPanels({dom, document});
  element('tts-api-key').value = 'unsaved-test-key';
  panels.openSettings('speech');
  assert.equal(element('settings-dialog').open, true);
  assert.equal(element('tts-settings').hidden, false);
  assert.equal(element('llm-settings').hidden, true);
  assert.equal(element('tts-configuration-link').getAttribute('aria-pressed'), 'true');
  panels.openSettings();
  assert.equal(element('llm-settings').hidden, false);
  assert.equal(element('tts-settings').hidden, true);
  assert.equal(element('tts-api-key').value, 'unsaved-test-key');
  element('settings-close').listeners.click();
  panels.openSettings('speech');
  assert.equal(element('tts-api-key').value, 'unsaved-test-key');
});

test('closing a panel returns focus and keeps memory edits for reopening', () => {
  const {dom, document, element} = fakeDOM();
  document.activeElement = element('memory-nav-link');
  const panels = createPanels({dom, document});
  element('memory-file-content').value = 'unfinished memory draft';
  panels.openMemory();
  assert.equal(element('memory-dialog').open, true);
  element('memory-close').listeners.click();
  assert.equal(element('memory-dialog').open, false);
  assert.equal(document.activeElement.focused, true);
  panels.openMemory();
  assert.equal(element('memory-file-content').value, 'unfinished memory draft');
});

test('only one secondary panel is open at a time', () => {
  const {dom, document, element} = fakeDOM();
  const panels = createPanels({dom, document});
  panels.openMemory();
  panels.openSettings();
  assert.equal(element('memory-dialog').open, false);
  assert.equal(element('settings-dialog').open, true);
});
