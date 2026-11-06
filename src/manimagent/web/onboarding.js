export function createReadiness({dom}) {
  const {el} = dom;
  let health = null, healthFailed = false;

  function check({outputMode = 'both', videoProfile = 'auto', requiresVision = false} = {}) {
    if (!health) return healthFailed ? '无法读取服务状态，请检查本地服务并重新读取配置。' : '尚未读取服务状态，请重新读取配置后再生成。';
    if (!health.ready) return '请先在设置中配置核心模型并保存。';
    if (requiresVision && !health.vision?.ready) return '本次含图片，请在设置中选择支持图片的材料读取模型。';
    if (outputMode === 'text') return null;
    if (health.video_issues?.length) return '动画暂不可用，可先生成完整解答。';
    const tts = health.tts || {enabled: false, key_configured: false};
    if (videoProfile === 'standard_narrated' || (videoProfile === 'auto' && tts.enabled)) {
      if (!tts.key_configured || tts.issues?.length) return '请先在设置中保存 MiMo 语音配置，或选择“本次不配音”。';
    }
    return null;
  }

  function render() {
    const outputMode = el('output-mode').value || 'both';
    const videoProfile = el('video-profile').value || 'auto';
    el('video-options').hidden = outputMode === 'text';
    const tts = health?.tts || {enabled: false, key_configured: false};
    const unavailable = healthFailed ? '配置状态读取失败' : '正在读取配置…';
    el('core-readiness').textContent = health ? (health.ready ? '已配置' : '待配置') : unavailable;
    el('vision-readiness').textContent = !health ? unavailable : health.vision?.ready ? '已配置' : '未配置';
    el('tts-readiness').textContent = !health ? unavailable : tts.enabled
      ? (tts.key_configured && !tts.issues?.length ? '默认配音' : '待配置')
      : (tts.key_configured && !tts.issues?.length ? '已配置，默认无声' : '未配置，默认无声');
    const narrationHelp = el('narration-help');
    narrationHelp.textContent = health && videoProfile === 'auto' ? (tts.enabled ? '默认：配音' : '默认：无声') : '';
    narrationHelp.hidden = !narrationHelp.textContent;
    const issue = check({outputMode, videoProfile});
    const banner = el('health');
    banner.replaceChildren();
    if ((health || healthFailed) && issue) {
      const message = dom.create('span'); message.textContent = issue; banner.append(message);
      if (health?.ready && health.video_issues?.length && outputMode !== 'text') {
        const button = dom.create('button'); button.type = 'button'; button.className = 'secondary';
        button.textContent = '先生成完整解答';
        button.onclick = () => { el('output-mode').value = 'text'; render(); };
        banner.append(button);
      }
    }
    banner.style.display = (health || healthFailed) && issue ? 'flex' : 'none';
  }

  function update(data) { health = data; healthFailed = false; render(); }
  function failed() { health = null; healthFailed = true; render(); }
  return {update, render, check, failed};
}
