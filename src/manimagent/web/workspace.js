import {appendLink} from './dom.js';
import {createResults} from './results.js';

const terminal = new Set(['completed', 'partial', 'needs_input', 'needs_review', 'failed', 'interrupted']);
const labels = {queued: '排队中', running: '处理中', completed: '已完成', partial: '文字已保存，动画未完成', needs_input: '需要补充材料', needs_review: '需要核对', failed: '失败', interrupted: '已中断'};

export function createWorkspace({dom, api, window, storage, crypto, scheduler = globalThis}) {
  const {el} = dom;
  const state = {epoch: 0, jobId: null, answer: null, videoId: null, resumeJobId: null, timer: null, busy: false, status: 'idle', reusedMaterialIds: null, fileRows: []};
  const {clearVideo, setStatus, showAnswer, showVideo} = createResults({state, dom, api, storage, window, crypto});
  function beginView(clearShared = true) {
    state.epoch += 1; scheduler.clearTimeout(state.timer); state.timer = null; state.jobId = null; state.resumeJobId = null;
    storage.removeItem('manimagent.textJob');
    for (const id of ['job', 'message', 'error', 'artifacts']) el(id).replaceChildren();
    el('diagnostics').hidden = true;
    setStatus('');
    if (clearShared && window.history) {
      const url = new URL(window.location.href);
      url.searchParams.delete('answer_id'); url.searchParams.delete('video_id');
      window.history.replaceState(null, '', url);
    }
    clearVideo();
    return state.epoch;
  }

  function setBusy(busy) { state.busy = busy; el('generate').disabled = busy; el('animate').disabled = busy; }
  function release() { setBusy(false); scheduler.clearTimeout(state.timer); state.timer = null; }
  function watchJob(id, epoch = state.epoch) {
    if (epoch !== state.epoch) return;
    state.jobId = id; setBusy(true); setStatus('排队中', 'queued');
    el('job').textContent = `任务 ${id}`;
    el('diagnostics').hidden = false;
    storage.setItem('manimagent.textJob', id);
    return poll(epoch, id);
  }

  function selectFiles() {
    state.reusedMaterialIds = null;
    el('file-list').replaceChildren();
    state.fileRows = Array.from(el('files').files).map((file) => {
      const row = dom.create('div'); row.className = 'file-row';
      const name = dom.create('span'); name.textContent = file.name; row.append(name);
      const start = dom.create('input'); const end = dom.create('input');
      if (file.name.toLowerCase().endsWith('.pdf')) {
        for (const [input, label] of [[start, '起始页'], [end, '结束页']]) {
          input.type = 'number'; input.min = '1'; input.placeholder = label; input.setAttribute('aria-label', `${file.name} ${label}`); row.append(input);
        }
      }
      el('file-list').append(row);
      return {file, start, end, uploaded: null};
    });
  }
  function materialChanged() { state.reusedMaterialIds = null; }

  async function poll(epoch = state.epoch, jobId = state.jobId) {
    try {
      const job = await api.request(`/api/v2/jobs/${jobId}`);
      if (epoch !== state.epoch || jobId !== state.jobId) return;
      setStatus(labels[job.status] || job.status, job.status);
      el('message').textContent = job.message;
      if (job.resource_ref?.type === 'generation') {
        const generation = await api.request(`/api/v2/generations/${job.resource_ref.id}`);
        if (epoch !== state.epoch) return;
        if (generation.answer_id) await showAnswer(generation.answer_id, epoch);
        if (generation.video_id) await showVideo(generation.video_id, epoch);
      }
      if (job.kind === 'video' && job.answer_id) await showAnswer(job.answer_id, epoch);
      if (job.result?.type === 'video') await showVideo(job.result.id, epoch);
      if (epoch !== state.epoch || jobId !== state.jobId) return;
      if (job.stage === 'video' && !state.videoId) { el('video-panel').hidden = false; el('video-message').textContent = job.message; }
      el('artifacts').replaceChildren();
      for (const id of job.diagnostic_asset_ids) appendLink(dom, el('artifacts'), `诊断 ${id}`, `/api/v2/files/${id}/content`);
      el('diagnostics').hidden = !job.diagnostic_asset_ids.length && !el('job').textContent;
      if (terminal.has(job.status)) {
        state.resumeJobId = job.stage === 'video' && ['video_pacing', 'video_plan_check', 'render_layout', 'render_failed', 'render_timeout', 'output_truncated'].includes(job.error?.code) ? state.jobId : null;
        if (!state.answer) {
          el('result').textContent = '本次未发布完整回答，请根据提示补充或核对材料后重新提交。';
          el('answer-meta').textContent = '等待补充或核对材料';
        }
        release();
      } else { state.timer = scheduler.setTimeout(() => poll(epoch, jobId), 1000); }
    } catch (error) {
      if (epoch !== state.epoch || jobId !== state.jobId) return;
      el('message').textContent = error.message;
      if (error.status === 404 || error.retryable === false) { storage.removeItem('manimagent.textJob'); release(); }
      else { state.timer = scheduler.setTimeout(() => poll(epoch, jobId), 3000); }
    }
  }

  async function submit(event) {
    event?.preventDefault();
    if (state.busy) return;
    const epoch = beginView();
    setBusy(true); setStatus('准备材料', 'running');
    state.answer = null; storage.removeItem('manimagent.answer');
    el('answer-meta').textContent = '正在生成，请保留此页面或稍后回来';
    el('result').textContent = '正在准备材料与文字回答…';
    el('actions').hidden = true; el('editor').hidden = true; el('sources').hidden = true; el('warnings').textContent = '';
    try {
      const instruction = el('text').value;
      const outputMode = el('output-mode').value;
      let source;
      if (state.reusedMaterialIds) source = {type: 'materials', material_ids: state.reusedMaterialIds, instruction};
      else {
        const inputs = [{type: 'text', text: instruction}];
        if (el('material').value.trim()) inputs.push({type: 'text', text: el('material').value});
        // Capture the whole submission before uploads yield to edits in the form.
        const files = state.fileRows.map(row => {
          let selection;
          if (row.start.value || row.end.value) {
            const start = Number(row.start.value), end = Number(row.end.value);
            if (!Number.isInteger(start) || !Number.isInteger(end) || start < 1 || end < start) throw new Error('请填写有效的起始页和结束页');
            selection = {kind: 'pages', start, end};
          }
          return {row, selection};
        });
        for (const {row, selection} of files) {
          if (!row.uploaded) {
            const data = new FormData(); data.append('file', row.file);
            el('message').textContent = `正在上传 ${row.file.name}`;
            row.uploaded = await api.request('/api/v2/files', {method: 'POST', body: data});
            if (epoch !== state.epoch) return;
          }
          inputs.push({type: 'file', file_id: row.uploaded.file_id, ...(selection ? {selection} : {})});
        }
        source = {type: 'inputs', inputs, instruction};
      }
      const job = await api.post('/api/v2/generations', {output_mode: outputMode, source});
      await watchJob(job.job_id, epoch);
    } catch (error) { if (epoch === state.epoch) { el('error').textContent = error.message; el('message').textContent = ''; el('result').textContent = '生成未完成，请检查输入后重试。'; el('answer-meta').textContent = '等待重新提交'; setStatus('未完成', 'failed'); release(); } }
  }
  function example() { el('text').value = '给定 a=(3,2)、b=(2,0)，求 a 在 b 方向上的投影。解释 b≠0 的前提、正交依据、投影系数与投影向量的区别，并验证残差与 b 正交。面向首次学习投影的学生完整推导。'; }
  async function animate() {
    if (!state.answer || state.busy) return;
    const sourceAnswer = state.answer.answer_id, resumeJob = state.resumeJobId;
    const epoch = beginView();
    setBusy(true); setStatus('准备动画', 'running');
    try {
      const job = await api.post('/api/v2/videos', {answer_id: sourceAnswer, ...(resumeJob ? {resume_job_id: resumeJob} : {})});
      if (epoch !== state.epoch) return;
      el('video-panel').hidden = false;
      await watchJob(job.job_id, epoch);
    } catch (error) { if (epoch === state.epoch) { el('message').textContent = error.message; setStatus('动画未完成', 'failed'); release(); } }
  }
  async function copy() { try { await window.navigator.clipboard.writeText(state.answer.markdown); el('message').textContent = '正文已复制'; } catch { el('message').textContent = '复制失败，可下载 Markdown'; } }
  function openSource(event) {
    if (event.target.closest('a')?.getAttribute('href')?.startsWith('#block_')) el('sources').open = true;
  }
  function edit() { el('revision').value = state.answer.markdown; el('editor').hidden = false; el('revision').focus(); }
  function cancel() { el('editor').hidden = true; }
  async function saveRevision() {
    let epoch = state.epoch;
    el('save').disabled = true;
    try {
      const revised = await api.post('/api/v2/answers/revisions', {base_answer_id: state.answer.answer_id, markdown: el('revision').value});
      if (epoch !== state.epoch) return;
      epoch = beginView();
      await showAnswer(revised.answer_id, epoch);
      if (epoch !== state.epoch) return;
      release();
      el('message').textContent = '已保存新版本，旧回答保持可读';
    } catch (error) { if (epoch === state.epoch) { el('message').textContent = error.message; release(); } }
    finally { el('save').disabled = false; }
  }
  function reuse() { state.reusedMaterialIds = state.answer.material_ids; el('text').focus(); el('message').textContent = '已复用此回答的材料。修改问题后点击“开始生成”。'; }
  async function restoreView() {
    const shared = new URLSearchParams(window.location.search);
    const valid = (kind, value) => new RegExp(`^${kind}_[a-f0-9]{32}$`).test(value || '');
    const savedJob = storage.getItem('manimagent.textJob');
    const savedAnswer = storage.getItem('manimagent.answer');
    const savedVideo = storage.getItem('manimagent.video');
    const epoch = beginView(false);
    try {
      if (shared.has('answer_id') || shared.has('video_id')) {
        const aid = shared.get('answer_id'), vid = shared.get('video_id');
        if ((aid !== null && !valid('answer', aid)) || (vid !== null && !valid('video', vid))) throw new Error('分享链接中的结果 ID 无效');
        if (aid) await showAnswer(aid, epoch);
        if (vid) await showVideo(vid, epoch);
      } else if (valid('job', savedJob)) {
        await watchJob(savedJob, epoch);
      } else {
        if (valid('answer', savedAnswer)) await showAnswer(savedAnswer, epoch);
        if (valid('video', savedVideo)) await showVideo(savedVideo, epoch);
      }
    } catch (error) { if (epoch === state.epoch) { el('message').textContent = error.message; release(); } }
  }

  return {state, submit, animate, saveRevision, restore: restoreView, beginView, watchJob,
    selectFiles, materialChanged, example, copy, edit, cancel, reuse, openSource};
}
