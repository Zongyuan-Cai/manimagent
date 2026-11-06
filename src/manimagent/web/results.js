import {appendLink} from './dom.js';

export function createResults({state, dom, api, storage, window, crypto}) {
  const {el} = dom;
  function showMemoryContext(jobId) {
    el('memory-context-links').hidden = !jobId;
    for (const id of ['memory-context-view', 'memory-context-download']) el(id).removeAttribute('href');
    if (jobId) {
      const path = `/api/v2/jobs/${encodeURIComponent(jobId)}/memory-context`;
      el('memory-context-view').href = path;
      el('memory-context-download').href = path + '?download=true';
    }
  }
  function clearMemorySnapshot() {
    el('answer-memory').hidden = true; el('answer-memory-list').replaceChildren(); el('answer-memory-summary').textContent = '';
  }
  function showMemorySnapshot(snapshot) {
    clearMemorySnapshot(); el('answer-memory').hidden = false; el('answer-memory').open = false;
    if (snapshot === undefined) { el('answer-memory-summary').textContent = '此回答没有记忆快照记录，无法确认当时是否使用记忆。'; return; }
    if (snapshot === null) { el('answer-memory-summary').textContent = '本次未使用用户记忆。'; return; }
    if (!Array.isArray(snapshot.entries)) { el('answer-memory-summary').textContent = '此回答的记忆快照格式无法识别。'; return; }
    el('answer-memory-summary').textContent = snapshot.entries.length ? `本次快照记录 ${snapshot.entries.length} 条适用记忆；各阶段按任务筛选，不代表每条都进入提示词或影响输出。` : '本次已启用记忆，但没有适用条目。';
    for (const entry of snapshot.entries) {
      const card = dom.create('div'); card.className = 'memory-card';
      const value = dom.create('p'); value.className = 'memory-value'; value.textContent = entry.value;
      const scope = dom.create('p'); scope.className = 'hint';
      scope.textContent = `${entry.temporary ? '本次会话 · 临时' : entry.layer === 'deep' ? '深层记忆' : '短期记忆'}${entry.scope && entry.scope !== 'global' ? ` · 适用于 ${entry.scope}` : ''}`;
      card.append(value, scope); el('answer-memory-list').append(card);
    }
  }
  function showUsage(usage) {
    const valid = value => Number.isInteger(value) && value >= 0;
    const cached = usage?.cached_input_tokens, reported = usage?.cache_reported_calls, accounted = usage?.cache_accounted_input_tokens, calls = usage?.model_calls;
    if (!valid(cached) || !valid(reported) || !reported || !valid(accounted) || cached > accounted || (valid(calls) && reported > calls)) {
      el('cache-usage').textContent = '提示词缓存：未知，服务商未提供可用的缓存统计。'; return;
    }
    const coverage = valid(calls) ? `${reported}/${calls}` : `${reported}`;
    const rate = accounted ? `${Math.round(cached / accounted * 1000) / 10}%` : '未知（已报告输入为 0）';
    el('cache-usage').textContent = `提示词缓存：已报告 ${coverage} 次调用 · 命中 ${cached.toLocaleString()} / ${accounted.toLocaleString()} 个已报告输入 token · 已报告输入中的命中比例 ${rate}${valid(calls) && reported < calls ? '。其余调用未知，不代表整个任务的命中比例。' : '。'}`;
  }
  function clearVideo() {
    state.videoId = null;
    el('video-panel').hidden = true; el('video-player').hidden = true; el('video-actions').hidden = true;
    el('video-jump').hidden = true;
    el('video-player').removeAttribute('src'); storage.removeItem('manimagent.video');
  }
  function setStatus(text, status = 'idle') {
    state.status = status;
    el('status').textContent = text;
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
    el('video-message').textContent = `${Math.round(video.duration_seconds)} 秒 · ${video.audio_asset_id ? '有声' : '无声'}`;
    el('video-technical').textContent = `720p / 30 fps · ${video.frame_mapping.length} 个关键节拍`;
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
    el('result-details').hidden = false;
    el('answer-meta').textContent = '完整解答 · 可编辑与导出';
    showMemorySnapshot(nextAnswer.memory_snapshot);
    if (!state.jobId) setStatus('回答已载入', 'completed');
    el('source-list').replaceChildren();
    for (const {materialId, material, files} of sources) {
      const title = dom.create('h3'); title.textContent = files.length ? '原始附件' : '输入材料';
      el('source-list').append(title);
      for (const file of files) {
        appendLink(dom, el('source-list'), `原件 ${file.filename}`, `/api/v2/files/${file.file_id}/content`);
      }
      for (const block of material.blocks) {
        const item = dom.create('pre');
        item.id = block.block_id;
        const labels = {page: '页', slide: '页幻灯片', row: '行', paragraph: '段', line: '行', line_start: '起始行', line_end: '结束行', sheet: '工作表', sheet_index: '工作表', cell: '单元格', column: '列', cell_index: '单元格', cell_start: '起始单元格', cell_end: '结束单元格', cell_row: '行', cell_column: '列'};
        const location = Object.entries(block.source.locator || {}).map(([key, value]) => labels[key] ? `${labels[key]} ${value}` : '').filter(Boolean).join(' · ');
        item.textContent = `${location ? location + '\n' : ''}${block.content}`;
        el('source-list').append(item);
      }
      const raw = dom.create('details'), summary = dom.create('summary'); summary.textContent = '查看原始解析记录';
      raw.append(summary); appendLink(dom, raw, '打开材料记录', `/api/v2/materials/${materialId}`); el('source-list').append(raw);
    }
    el('sources').hidden = !sources.length;
    storage.setItem('manimagent.answer', id);
    if (window.MathJax?.startup?.promise) {
      try {
        await window.MathJax.startup.promise;
        if (epoch !== state.epoch) return;
        await window.MathJax.typesetPromise([el('result')]);
      } catch { if (epoch === state.epoch) el('message').textContent = '部分公式未能排版，原始 LaTeX 可在 Markdown 中查看。'; }
    }
  }

  return {clearVideo, clearMemorySnapshot, setStatus, showAnswer, showVideo, showUsage, showMemoryContext};
}
