import {appendLink} from './dom.js';

export function createResults({state, dom, api, storage, window, crypto}) {
  const {el} = dom;
  function clearVideo() {
    state.videoId = null;
    el('video-panel').hidden = true; el('video-player').hidden = true; el('video-actions').hidden = true;
    el('video-jump').hidden = true;
    el('video-player').removeAttribute('src'); storage.removeItem('manimagent.video');
  }
  function setStatus(text, status = 'idle') {
    state.status = status;
    el('status').textContent = text; el('status').style.display = text ? 'block' : 'none';
    el('workspace-state').textContent = {partial: '部分完成', needs_input: '待补充', needs_review: '待核对'}[status] || text || '等待输入';
    el('workspace-state').setAttribute('data-state', status);
  }
  async function showVideo(id, epoch = state.epoch) {
    if (epoch !== state.epoch || state.videoId === id) return;
    const video = await api.request(`/api/v2/videos/${id}`);
    if (epoch !== state.epoch) return;
    if (!state.answer) await showAnswer(video.answer_id, epoch);
    if (epoch !== state.epoch) return;
    const hash = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(state.answer.markdown));
    if (epoch !== state.epoch) return;
    const hex = Array.from(new Uint8Array(hash), b => b.toString(16).padStart(2, '0')).join('');
    if (state.answer.answer_id !== video.answer_id || hex !== video.answer_hash) {
      clearVideo();
      throw Object.assign(new Error('视频与当前回答版本不匹配，请基于此回答重新生成动画'), {retryable: false});
    }
    el('video-panel').hidden = false; el('video-player').hidden = false; el('video-actions').hidden = false;
    el('video-jump').hidden = false;
    el('video-player').src = `/api/v2/videos/${id}/content`;
    el('video-download').href = `/api/v2/videos/${id}/content?download=true`;
    el('video-plan').href = `/api/v2/videos/${id}/plan`;
    el('video-message').textContent = `${Math.round(video.duration_seconds)} 秒 · 720p / 30 fps · 无声 · ${video.frame_mapping.length} 个关键节拍`;
    state.videoId = id;
    storage.setItem('manimagent.video', id);
  }

  async function showAnswer(id, epoch = state.epoch) {
    if (epoch !== state.epoch || state.answer?.answer_id === id) return;
    const nextAnswer = await api.request(`/api/v2/answers/${id}`);
    const html = await api.html(`/api/v2/answers/${id}/html`);
    const sources = await Promise.all(nextAnswer.material_ids.map(async materialId => {
      const material = await api.request(`/api/v2/materials/${materialId}`);
      const files = await Promise.all(material.source_file_ids.map(fileId => api.request(`/api/v2/files/${fileId}`)));
      return {materialId, material, files};
    }));
    if (epoch !== state.epoch) return;
    if (state.answer?.answer_id !== id) { clearVideo(); state.resumeJobId = null; }
    state.answer = nextAnswer;
    if (window.MathJax?.typesetClear) window.MathJax.typesetClear([el('result')]);
    // Raw HTML is disabled and unsafe link protocols are rejected by the server renderer.
    el('result').innerHTML = html;
    el('actions').hidden = false; el('editor').hidden = true;
    el('download').href = `/api/v2/answers/${id}/markdown?download=true`;
    el('warnings').textContent = nextAnswer.warnings.map(w => w.message).join('；');
    el('job').textContent = `回答版本 ${id}`;
    el('diagnostics').hidden = false;
    el('answer-meta').textContent = '完整解答 · 可编辑与导出';
    if (!state.jobId) setStatus('回答已载入', 'completed');
    el('source-list').replaceChildren();
    for (const {materialId, material, files} of sources) {
      appendLink(dom, el('source-list'), `材料 ${materialId}`, `/api/v2/materials/${materialId}`);
      for (const file of files) {
        appendLink(dom, el('source-list'), `原件 ${file.filename}`, `/api/v2/files/${file.file_id}/content`);
      }
      for (const block of material.blocks) {
        const item = dom.create('pre');
        item.id = block.block_id;
        item.textContent = `${materialId}:${block.block_id}\n来源 ${JSON.stringify(block.source.locator)}\n${block.content}`;
        el('source-list').append(item);
      }
    }
    el('sources').hidden = false;
    storage.setItem('manimagent.answer', id);
    if (window.MathJax?.startup?.promise) {
      try {
        await window.MathJax.startup.promise;
        if (epoch !== state.epoch) return;
        await window.MathJax.typesetPromise([el('result')]);
      } catch { if (epoch === state.epoch) el('message').textContent = '部分公式未能排版，原始 LaTeX 可在 Markdown 中查看。'; }
    }
  }

  return {clearVideo, setStatus, showAnswer, showVideo};
}
