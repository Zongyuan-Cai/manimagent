import test from 'node:test';
import assert from 'node:assert/strict';
import {fakeDOM} from './helpers.mjs';
import {createReadiness} from '../../src/manimagent/web/onboarding.js';

test('optional visual and voice services do not block text-only work', () => {
  const {dom, element} = fakeDOM();
  const readiness = createReadiness({dom});
  element('output-mode').value = 'text';
  readiness.update({ready: true, video_issues: ['no renderer'], vision: {ready: false}, tts: {enabled: false, key_configured: false, issues: ['missing key']}});
  assert.equal(readiness.check({outputMode: 'text'}), null);
  assert.match(readiness.check({outputMode: 'text', requiresVision: true}), /图片/);
  assert.equal(element('health').style.display, 'none');
});

test('explicit narration can use a configured key while default narration is off', () => {
  const {dom} = fakeDOM();
  const readiness = createReadiness({dom});
  readiness.update({ready: true, video_issues: [], tts: {enabled: false, key_configured: true, issues: []}});
  assert.equal(readiness.check({videoProfile: 'standard_narrated'}), null);
  readiness.update({ready: true, video_issues: [], tts: {enabled: false, key_configured: false, issues: ['missing key']}});
  assert.match(readiness.check({videoProfile: 'standard_narrated'}), /MiMo/);
  assert.equal(readiness.check({videoProfile: 'standard_silent'}), null);
});

test('unavailable renderer offers an explicit switch to text', () => {
  const {dom, element} = fakeDOM();
  const readiness = createReadiness({dom});
  element('output-mode').value = 'both';
  readiness.update({ready: true, video_issues: ['no renderer']});
  element('health').children.find(child => child.tagName === 'button').onclick();
  assert.equal(element('output-mode').value, 'text');
  assert.equal(element('video-options').hidden, true);
});

test('failed readiness remains visible when options change until a successful refresh', () => {
  const {dom, element} = fakeDOM();
  const readiness = createReadiness({dom});
  readiness.failed();
  for (const mode of ['text', 'both']) {
    element('output-mode').value = mode; readiness.render();
    for (const id of ['core-readiness', 'vision-readiness', 'tts-readiness']) assert.equal(element(id).textContent, '配置状态读取失败');
    assert.notEqual(element('health').style.display, 'none');
    assert.match(element('health').children[0].textContent, /无法读取服务状态/);
    assert.match(readiness.check({outputMode: mode}), /无法读取服务状态/);
  }
  readiness.update({ready: true, video_issues: [], tts: {enabled: false, key_configured: false}});
  assert.equal(element('health').style.display, 'none');
  assert.equal(element('core-readiness').textContent, '已配置');
});

test('healthy generation stays quiet and only the automatic narration option needs a default explanation', () => {
  const {dom, element} = fakeDOM();
  const readiness = createReadiness({dom});
  element('output-mode').value = 'both'; element('video-profile').value = 'auto';
  readiness.update({ready: true, video_issues: [], vision: {ready: true}, tts: {enabled: true, key_configured: true, issues: []}});
  assert.equal(element('health').style.display, 'none');
  assert.equal(element('narration-help').textContent, '默认：配音');
  for (const profile of ['standard_silent', 'standard_narrated']) {
    element('video-profile').value = profile; readiness.render();
    assert.equal(element('health').style.display, 'none');
    assert.equal(element('narration-help').hidden, true);
    assert.equal(element('narration-help').textContent, '');
  }
});
