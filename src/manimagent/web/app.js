const el = (id) => document.getElementById(id);
const terminal = new Set(['completed', 'partial', 'needs_input', 'needs_review', 'failed', 'interrupted']);
const labels = {queued: '排队中', running: '处理中', completed: '已完成', partial: '文字已保存，动画未完成', needs_input: '需要补充材料', needs_review: '需要核对', failed: '失败', interrupted: '已中断'};
let currentJob = null;
let answer = null;
let timer;
let reusedMaterialIds = null;
let fileRows = [];
let currentVideo = null;
let animationResumeJob = null;
let viewEpoch = 0;

function clearVideo() {
  currentVideo = null;
  el('video-panel').hidden = true; el('video-player').hidden = true; el('video-actions').hidden = true;
  el('video-player').removeAttribute('src'); localStorage.removeItem('manimagent.video');
}
function beginView(clearShared = true) {
  viewEpoch += 1; clearTimeout(timer); currentJob = null; animationResumeJob = null;
  localStorage.removeItem('manimagent.textJob');
  if (clearShared && window.history) {
    const url = new URL(window.location.href);
    url.searchParams.delete('answer_id'); url.searchParams.delete('video_id');
    window.history.replaceState(null, '', url);
  }
  clearVideo();
  return viewEpoch;
}

async function request(url, options) {
  const response = await fetch(url, options);
  const data = await response.json();
  if (!response.ok) {
    const error = new Error(data.error?.message || data.detail || '请求失败');
    error.status = response.status;
    throw error;
  }
  return data;
}
function post(url, data) {
  return request(url, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data)});
}
function link(parent, title, url) {
  const a = document.createElement('a');
  a.textContent = title; a.href = url; a.className = 'artifact'; parent.append(a);
}
function release() { el('generate').disabled = false; el('animate').disabled = false; clearTimeout(timer); }

async function showVideo(id, epoch = viewEpoch) {
  if (epoch !== viewEpoch || currentVideo === id) return;
  const video = await request(`/api/v2/videos/${id}`);
  if (epoch !== viewEpoch) return;
  if (!answer) await showAnswer(video.answer_id, epoch);
  if (epoch !== viewEpoch) return;
  const hash = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(answer.markdown));
  if (epoch !== viewEpoch) return;
  const hex = Array.from(new Uint8Array(hash), b => b.toString(16).padStart(2, '0')).join('');
  if (answer.answer_id !== video.answer_id || hex !== video.answer_hash) {
    clearVideo();
    throw Object.assign(new Error('视频与当前回答版本不匹配，请基于此回答重新生成动画'), {retryable: false});
  }
  el('video-panel').hidden = false; el('video-player').hidden = false; el('video-actions').hidden = false;
  el('video-player').src = `/api/v2/videos/${id}/content`;
  el('video-download').href = `/api/v2/videos/${id}/content?download=true`;
  el('video-plan').href = `/api/v2/videos/${id}/plan`;
  el('video-message').textContent = `${Math.round(video.duration_seconds)} 秒 · 720p / 30 fps · 无声 · ${video.frame_mapping.length} 个关键节拍`;
  currentVideo = id;
  localStorage.setItem('manimagent.video', id);
}

el('files').addEventListener('change', () => {
  reusedMaterialIds = null;
  el('file-list').replaceChildren();
  fileRows = Array.from(el('files').files).map((file) => {
    const row = document.createElement('div'); row.className = 'file-row';
    const name = document.createElement('span'); name.textContent = file.name; row.append(name);
    const start = document.createElement('input'); const end = document.createElement('input');
    if (file.name.toLowerCase().endsWith('.pdf')) {
      for (const [input, label] of [[start, '起始页'], [end, '结束页']]) {
        input.type = 'number'; input.min = '1'; input.placeholder = label; input.setAttribute('aria-label', `${file.name} ${label}`); row.append(input);
      }
    }
    el('file-list').append(row);
    return {file, start, end, uploaded: null};
  });
});
el('material').addEventListener('input', () => { reusedMaterialIds = null; });

async function showAnswer(id, epoch = viewEpoch) {
  if (epoch !== viewEpoch || answer?.answer_id === id) return;
  const nextAnswer = await request(`/api/v2/answers/${id}`);
  const htmlResponse = await fetch(`/api/v2/answers/${id}/html`);
  if (!htmlResponse.ok) throw new Error('正文读取失败');
  const html = await htmlResponse.text();
  const sources = await Promise.all(nextAnswer.material_ids.map(async materialId => {
    const material = await request(`/api/v2/materials/${materialId}`);
    const files = await Promise.all(material.source_file_ids.map(fileId => request(`/api/v2/files/${fileId}`)));
    return {materialId, material, files};
  }));
  if (epoch !== viewEpoch) return;
  if (answer?.answer_id !== id) { clearVideo(); animationResumeJob = null; }
  answer = nextAnswer;
  if (window.MathJax?.typesetClear) window.MathJax.typesetClear([el('result')]);
  // Raw HTML is disabled and unsafe link protocols are rejected by the server renderer.
  el('result').innerHTML = html;
  el('actions').hidden = false; el('editor').hidden = true;
  el('download').href = `/api/v2/answers/${id}/markdown?download=true`;
  el('warnings').textContent = nextAnswer.warnings.map(w => w.message).join('；');
  el('job').textContent = `回答版本 ${id}`;
  el('source-list').replaceChildren();
  for (const {materialId, material, files} of sources) {
    link(el('source-list'), `材料 ${materialId}`, `/api/v2/materials/${materialId}`);
    for (const file of files) {
      link(el('source-list'), `原件 ${file.filename}`, `/api/v2/files/${file.file_id}/content`);
    }
    for (const block of material.blocks) {
      const item = document.createElement('pre');
      item.id = block.block_id;
      item.textContent = `${materialId}:${block.block_id}\n来源 ${JSON.stringify(block.source.locator)}\n${block.content}`;
      el('source-list').append(item);
    }
  }
  el('sources').hidden = false;
  localStorage.setItem('manimagent.answer', id);
  if (window.MathJax?.startup?.promise) {
    try {
      await window.MathJax.startup.promise;
      if (epoch !== viewEpoch) return;
      await window.MathJax.typesetPromise([el('result')]);
    } catch { if (epoch === viewEpoch) el('message').textContent = '部分公式未能排版，原始 LaTeX 可在 Markdown 中查看。'; }
  }
}

async function poll(epoch = viewEpoch, jobId = currentJob) {
  try {
    const job = await request(`/api/v2/jobs/${jobId}`);
    if (epoch !== viewEpoch || jobId !== currentJob) return;
    el('status').style.display = 'block';
    el('status').textContent = labels[job.status] || job.status;
    el('message').textContent = job.message;
    if (job.resource_ref?.type === 'generation') {
      const generation = await request(`/api/v2/generations/${job.resource_ref.id}`);
      if (epoch !== viewEpoch) return;
      if (generation.answer_id) await showAnswer(generation.answer_id, epoch);
      if (generation.video_id) await showVideo(generation.video_id, epoch);
    }
    if (job.kind === 'video' && job.answer_id) await showAnswer(job.answer_id, epoch);
    if (job.result?.type === 'video') await showVideo(job.result.id, epoch);
    if (epoch !== viewEpoch || jobId !== currentJob) return;
    if (job.stage === 'video' && !currentVideo) { el('video-panel').hidden = false; el('video-message').textContent = job.message; }
    el('artifacts').replaceChildren();
    for (const id of job.diagnostic_asset_ids) link(el('artifacts'), `诊断 ${id}`, `/api/v2/files/${id}/content`);
    el('diagnostics').hidden = !job.diagnostic_asset_ids.length;
    if (terminal.has(job.status)) {
      animationResumeJob = job.stage === 'video' && ['video_pacing', 'video_plan_check', 'render_layout', 'render_failed', 'render_timeout', 'output_truncated'].includes(job.error?.code) ? currentJob : null;
      if (!answer) el('result').textContent = '本次未发布完整回答，请根据提示补充或核对材料后重新提交。';
      release();
    } else { timer = setTimeout(() => poll(epoch, jobId), 1000); }
  } catch (error) {
    if (epoch !== viewEpoch || jobId !== currentJob) return;
    el('message').textContent = error.message;
    if (error.status === 404 || error.retryable === false) { localStorage.removeItem('manimagent.textJob'); release(); }
    else { timer = setTimeout(() => poll(epoch, jobId), 3000); }
  }
}

el('form').addEventListener('submit', async (event) => {
  event.preventDefault(); clearTimeout(timer); el('generate').disabled = true; el('animate').disabled = true; el('error').textContent = '';
  const epoch = beginView();
  answer = null; localStorage.removeItem('manimagent.answer');
  el('result').textContent = '正在准备材料与文字回答…';
  el('actions').hidden = true; el('editor').hidden = true; el('sources').hidden = true; el('warnings').textContent = '';
  try {
    const instruction = el('text').value;
    let source;
    if (reusedMaterialIds) source = {type: 'materials', material_ids: reusedMaterialIds, instruction};
    else {
      const inputs = [{type: 'text', text: instruction}];
      if (el('material').value.trim()) inputs.push({type: 'text', text: el('material').value});
      for (const row of fileRows) {
        let selection;
        if (row.start.value || row.end.value) {
          const start = Number(row.start.value), end = Number(row.end.value);
          if (!Number.isInteger(start) || !Number.isInteger(end) || start < 1 || end < start) throw new Error('请填写有效的起始页和结束页');
          selection = {kind: 'pages', start, end};
        }
        if (!row.uploaded) {
          const data = new FormData(); data.append('file', row.file);
          el('message').textContent = `正在上传 ${row.file.name}`;
          row.uploaded = await request('/api/v2/files', {method: 'POST', body: data});
        }
        inputs.push({type: 'file', file_id: row.uploaded.file_id, ...(selection ? {selection} : {})});
      }
      source = {type: 'inputs', inputs, instruction};
    }
    const job = await post('/api/v2/generations', {output_mode: el('output-mode').value, source});
    if (epoch !== viewEpoch) return;
    currentJob = job.job_id;
    el('job').textContent = `任务 ${currentJob}`;
    localStorage.setItem('manimagent.textJob', currentJob); poll();
  } catch (error) { if (epoch === viewEpoch) { el('error').textContent = error.message; el('message').textContent = ''; release(); } }
});
el('example').onclick = () => { el('text').value = '给定 a=(3,2)、b=(2,0)，求 a 在 b 方向上的投影。解释 b≠0 的前提、正交依据、投影系数与投影向量的区别，并验证残差与 b 正交。面向首次学习投影的学生完整推导。'; };
el('animate').onclick = async () => {
  if (!answer) return;
  const sourceAnswer = answer.answer_id, resumeJob = animationResumeJob;
  const epoch = beginView();
  el('animate').disabled = true; el('generate').disabled = true;
  try {
    const job = await post('/api/v2/videos', {answer_id: sourceAnswer, ...(resumeJob ? {resume_job_id: resumeJob} : {})});
    if (epoch !== viewEpoch) return;
    currentJob = job.job_id; currentVideo = null;
    el('video-panel').hidden = false; el('video-player').hidden = true; el('video-actions').hidden = true;
    localStorage.setItem('manimagent.textJob', currentJob); poll();
  } catch (error) { if (epoch === viewEpoch) { el('message').textContent = error.message; release(); } }
};
el('copy').onclick = async () => { try { await navigator.clipboard.writeText(answer.markdown); el('message').textContent = '正文已复制'; } catch { el('message').textContent = '复制失败，可下载 Markdown'; } };
el('result').addEventListener('click', event => {
  if (event.target.closest('a')?.getAttribute('href')?.startsWith('#block_')) el('sources').open = true;
});
el('edit').onclick = () => { el('revision').value = answer.markdown; el('editor').hidden = false; };
el('cancel').onclick = () => { el('editor').hidden = true; };
el('save').onclick = async () => {
  let epoch = viewEpoch;
  el('save').disabled = true;
  try {
    const revised = await post('/api/v2/answers/revisions', {base_answer_id: answer.answer_id, markdown: el('revision').value});
    if (epoch !== viewEpoch) return;
    epoch = beginView();
    await showAnswer(revised.answer_id, epoch);
    if (epoch !== viewEpoch) return;
    release();
    el('message').textContent = '已保存新版本，旧回答保持可读';
  } catch (error) { if (epoch === viewEpoch) el('message').textContent = error.message; }
  finally { el('save').disabled = false; }
};
el('reuse').onclick = () => { reusedMaterialIds = answer.material_ids; el('text').focus(); el('message').textContent = '已复用此回答的材料。修改问题后点击生成文字回答。'; };
async function refreshHealth() {
  const data = await request('/api/v2/health');
  el('health').textContent = data.ready ? (data.video_issues?.length ? data.video_issues.join('；') : '') : `回答模型尚未就绪：${data.issues.join('；')}。可展开 LLM 配置完成设置；上传与独立解析仍可使用。`;
  el('health').style.display = data.ready && !data.video_issues?.length ? 'none' : 'block';
  return data;
}
refreshHealth().catch(() => {});
async function restoreView() {
  const shared = new URLSearchParams(window.location.search);
  const valid = (kind, value) => new RegExp(`^${kind}_[a-f0-9]{32}$`).test(value || '');
  const savedJob = localStorage.getItem('manimagent.textJob');
  const savedAnswer = localStorage.getItem('manimagent.answer');
  const savedVideo = localStorage.getItem('manimagent.video');
  const epoch = beginView(false);
  try {
    if (shared.has('answer_id') || shared.has('video_id')) {
      const aid = shared.get('answer_id'), vid = shared.get('video_id');
      if ((aid !== null && !valid('answer', aid)) || (vid !== null && !valid('video', vid))) throw new Error('分享链接中的结果 ID 无效');
      if (aid) await showAnswer(aid, epoch);
      if (vid) await showVideo(vid, epoch);
    } else if (valid('job', savedJob)) {
      currentJob = savedJob;
      localStorage.setItem('manimagent.textJob', savedJob);
      el('generate').disabled = true; el('animate').disabled = true;
      await poll(epoch, savedJob);
    } else {
      if (valid('answer', savedAnswer)) await showAnswer(savedAnswer, epoch);
      if (valid('video', savedVideo)) await showVideo(savedVideo, epoch);
    }
  } catch (error) { if (epoch === viewEpoch) { el('message').textContent = error.message; release(); } }
}
restoreView();
