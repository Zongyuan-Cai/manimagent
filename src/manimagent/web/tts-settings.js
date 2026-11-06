const fields = {base_url: 'tts-base-url', model: 'tts-model', voice: 'tts-voice', style: 'tts-style'};
const controls = ['tts-enabled', 'tts-api-key', 'tts-clear-key', 'tts-test-text', 'tts-test', 'tts-save', 'tts-reload', ...Object.values(fields)];
const origin = value => { try { return new URL(value).origin; } catch { return ''; } };

export function createTTSSettings({dom, api}) {
  const {el} = dom;
  const state = {revision: 0, config: null, busy: false, keyConfigured: false};
  function busy(value) { state.busy = value; for (const id of controls) el(id).disabled = value; }
  function message(text, error = false) {
    el('tts-message').textContent = text;
    el('tts-message').classList[error ? 'add' : 'remove']('error-text');
  }
  function clearAudio() {
    el('tts-audio').pause(); el('tts-audio').removeAttribute('src'); el('tts-audio').hidden = true;
  }
  function display(data) {
    state.revision = data.revision; state.config = {...data.config}; state.keyConfigured = data.key_configured;
    el('tts-enabled').checked = data.config.enabled;
    for (const [name, id] of Object.entries(fields)) el(id).value = data.config[name];
    el('tts-api-key').value = ''; el('tts-clear-key').checked = false;
    el('tts-key-state').textContent = data.key_configured ? '· 已设置，留空保留' : '· 未设置';
    el('tts-state').textContent = ({disabled: '默认无声', ready: '默认配音', needs_configuration: '待配置'})[data.state] || '待配置';
  }
  async function health() {
    try { await api.health(); }
    catch { message('配置已读取或保存，但状态刷新失败。请稍后重新载入。', true); }
  }
  function draft() {
    if (!state.config) throw new Error('请先载入语音配置。');
    const config = {...state.config, enabled: el('tts-enabled').checked};
    for (const [name, id] of Object.entries(fields)) config[name] = el(id).value.trim();
    if (/\/chat\/completions\/?$/.test(config.base_url)) config.base_url = config.base_url.replace(/\/chat\/completions\/?$/, '');
    const apiKey = el('tts-api-key').value.trim(), clear = el('tts-clear-key').checked;
    if (apiKey && clear) throw new Error('不能同时输入新语音密钥和清除密钥关联，请选择其中一项。');
    if (!apiKey && !clear && state.keyConfigured && origin(config.base_url) !== origin(state.config.base_url)) throw new Error('语音服务地址已改变，请输入新服务的 API Key，避免发送原密钥。');
    return {config, ...(apiKey ? {api_key: apiKey} : {}), clear_api_key: clear};
  }
  async function load() {
    if (state.busy) return;
    busy(true); clearAudio();
    try {
      const data = await api.request('/api/v2/tts-settings'); display(data);
      message(data.state === 'needs_configuration' && data.issues?.length ? data.issues.join('；') : '');
      await health();
    } catch (error) { message(`语音配置读取失败：${error.message}`, true); el('tts-state').textContent = '读取失败'; }
    finally { busy(false); }
  }
  async function save(event) {
    event?.preventDefault(); if (state.busy) return;
    busy(true); clearAudio();
    try {
      const data = await api.request('/api/v2/tts-settings', {method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({expected_revision: state.revision, draft: draft()})});
      display(data);
      message(data.state === 'needs_configuration' ? `已保存，请补全配置：${data.issues.join('；')}` : data.config.enabled ? '已保存，新动画默认配音。' : '已保存，新动画默认无声。');
      await health();
    } catch (error) { message(error.message, true); }
    finally { busy(false); }
  }
  async function test() {
    if (state.busy) return;
    busy(true); clearAudio(); message('正在生成试听…');
    try {
      const text = el('tts-test-text').value.trim() || '你好，我们一起把每一步讲清楚。';
      const result = await api.post('/api/v2/tts-settings/test', {draft: draft(), text});
      if (!result.ok || result.media_type !== 'audio/wav' || !result.audio_base64 || result.audio_base64.length > 6 * 1024 * 1024 || !/^[A-Za-z0-9+/]+={0,2}$/.test(result.audio_base64)) throw new Error('服务未返回可播放的试听音频，请核对接口协议。');
      el('tts-audio').src = `data:audio/wav;base64,${result.audio_base64}`; el('tts-audio').hidden = false;
      message(`试听已生成 · ${result.duration_seconds} 秒，尚未保存配置。`);
    } catch (error) { message(`试听失败：${error.message}`, true); }
    finally { busy(false); }
  }
  el('tts-api-key').addEventListener('input', () => { if (el('tts-api-key').value.trim()) el('tts-clear-key').checked = false; });
  return {state, load, save, test};
}
