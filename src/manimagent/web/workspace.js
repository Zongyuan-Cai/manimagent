import {appendLink} from './dom.js';
import {createResults} from './results.js';

const terminal = new Set(['completed', 'partial', 'needs_input', 'needs_review', 'failed', 'interrupted']);
const labels = {queued: '排队中', running: '处理中', completed: '已完成', partial: '文字已保存，动画未完成', needs_input: '需要补充材料', needs_review: '需要核对', failed: '失败', interrupted: '已中断'};

export function createWorkspace({dom, api, window, storage, crypto, scheduler = globalThis, memoryOptions = () => null, generationReadiness = () => null, onCompleted = () => {}}) {
  const {el} = dom;
  const state = {epoch: 0, jobId: null, answer: null, videoId: null, resumeJobId: null, timer: null, busy: false, revisionSaving: false, status: 'idle', reusedMaterialIds: null, fileRows: [], inputCapabilities: null, inputsLoading: false};
  const {clearVideo, clearMemorySnapshot, setStatus, showAnswer, showVideo, showUsage, showMemoryContext} = createResults({state, dom, api, storage, window, crypto});
  function beginView(clearShared = true) {
    state.epoch += 1; scheduler.clearTimeout(state.timer); state.timer = null; state.jobId = null; state.resumeJobId = null;
    storage.removeItem('manimagent.textJob');
    for (const id of ['job', 'message', 'error', 'artifacts']) el(id).replaceChildren();
    el('diagnostics').hidden = true;
    el('result-details').hidden = true; el('result-details').open = false;
    setStatus('');
    showUsage(null);
    showMemoryContext(null);
    if (clearShared && window.history) {
      const url = new URL(window.location.href);
      url.searchParams.delete('answer_id'); url.searchParams.delete('video_id');
      window.history.replaceState(null, '', url);
    }
    clearVideo();
    return state.epoch;
  }

  function setBusy(busy) {
    state.busy = busy;
    const locked = busy || state.revisionSaving;
    el('generate').disabled = locked; el('animate').disabled = locked; el('files').disabled = locked;
    el('save').disabled = state.revisionSaving; el('edit').disabled = state.revisionSaving; el('revision').disabled = state.revisionSaving;
    el('upload-reload').disabled = locked || state.inputsLoading;
    for (const row of state.fileRows) {
      row.remove.disabled = locked;
      row.start.disabled = locked; row.end.disabled = locked;
    }
  }
  function release() { setBusy(false); scheduler.clearTimeout(state.timer); state.timer = null; }
  function watchJob(id, epoch = state.epoch) {
    if (epoch !== state.epoch) return;
    state.jobId = id; setBusy(true); setStatus('排队中', 'queued');
    showMemoryContext(id);
    el('job').textContent = `任务 ${id}`;
    el('diagnostics').hidden = false;
    el('result-details').hidden = false;
    storage.setItem('manimagent.textJob', id);
    return poll(epoch, id);
  }

  function formatBytes(bytes) { return bytes >= 1048576 ? `${(bytes / 1048576).toFixed(1)} MB` : `${Math.ceil(bytes / 1024)} KB`; }
  function fileProblem(file) {
    const data = state.inputCapabilities;
    if (!data) return '附件支持信息尚未载入，请点击“重新读取上传支持”后重试。';
    const suffix = '.' + file.name.split('.').pop().toLowerCase();
    if (!data.formats.some(format => format.extensions.includes(suffix))) return '此格式暂不支持。请查看下方支持范围，转为可读文本后提交。';
    if (file.size === 0) return '文件为空，请换成包含正文的文件。';
    if (file.size > data.limits.max_upload_bytes) return `超过单个文件 ${formatBytes(data.limits.max_upload_bytes)} 上限，请缩小文件。`;
    return '';
  }
  function showFileRows() {
    el('file-list').replaceChildren();
    for (const row of state.fileRows) el('file-list').append(row.node);
    el('upload-summary').textContent = state.fileRows.length ? `已添加 ${state.fileRows.length} 个附件` : '';
  }
  async function loadInputs() {
    if (state.inputsLoading) return;
    state.inputsLoading = true; el('upload-reload').disabled = true;
    try {
      const data = await api.request('/api/v2/input-capabilities');
      state.inputCapabilities = data;
      el('files').accept = data.formats.flatMap(item => item.extensions).join(',');
      const limits = data.limits;
      el('file-help').textContent = `文档、图片、表格或代码 · 每个文件最多 ${formatBytes(limits.max_upload_bytes)}`;
      el('upload-limits').textContent = `每个 PDF / PPTX 最多读取 ${limits.max_parse_pages} 页，视觉读取每次最多 ${limits.max_visual_pages} 张图片或页面。一次最多 ${limits.max_inputs} 份输入（问题和补充材料各占一份），材料合计最多 ${limits.max_parse_chars.toLocaleString()} 字符。图片与扫描页需要视觉模型。`;
      el('upload-support').replaceChildren();
      for (const item of [...data.formats.map(format => ({label: format.label, text: format.description})), ...data.unsupported.map(item => ({label: item.label, text: item.action}))]) {
        const li = dom.create('li'); li.textContent = `${item.label}：${item.text}`; el('upload-support').append(li);
      }
      el('upload-error').textContent = '';
      for (const row of state.fileRows) if (!row.uploaded && !state.busy) {
        const problem = fileProblem(row.file); row.status.textContent = problem || '待上传';
        row.status.classList[problem ? 'add' : 'remove']('error-text');
      }
      showFileRows();
    } catch {
      state.inputCapabilities = null;
      el('upload-error').textContent = '无法读取附件支持范围，暂时不能上传。可继续直接输入问题，或点击“重新读取上传支持”。';
    } finally { state.inputsLoading = false; el('upload-reload').disabled = state.busy || state.revisionSaving; }
  }
  function selectFiles() {
    if (state.busy || state.revisionSaving) return;
    state.reusedMaterialIds = null;
    el('upload-error').textContent = '';
    for (const file of Array.from(el('files').files || [])) {
      if (state.fileRows.some(row => row.file.name === file.name && row.file.size === file.size && row.file.lastModified === file.lastModified)) continue;
      const row = dom.create('div'); row.className = 'file-row';
      const name = dom.create('span'); name.textContent = `${file.name} · ${formatBytes(file.size)}`; row.append(name);
      const start = dom.create('input'); const end = dom.create('input');
      if (/\.(pdf|pptx)$/i.test(file.name)) {
        for (const [input, label] of [[start, '起始页'], [end, '结束页']]) {
          input.type = 'number'; input.min = '1'; input.placeholder = label; input.setAttribute('aria-label', `${file.name} ${label}`); row.append(input);
        }
      }
      const status = dom.create('span'); status.className = 'file-status'; status.setAttribute('role', 'status');
      const problem = fileProblem(file); status.textContent = problem || '待上传';
      if (problem) status.classList.add('error-text');
      const remove = dom.create('button'); remove.type = 'button'; remove.className = 'secondary'; remove.textContent = '移除'; remove.setAttribute('aria-label', `移除 ${file.name}`);
      const entry = {file, start, end, uploaded: null, node: row, status, remove};
      remove.addEventListener('click', () => {
        if (state.busy || state.revisionSaving) return;
        state.fileRows = state.fileRows.filter(item => item !== entry); el('upload-error').textContent = ''; showFileRows();
      });
      row.append(status, remove); state.fileRows.push(entry);
    }
    el('files').value = '';
    showFileRows();
  }
  function materialChanged() { state.reusedMaterialIds = null; }

  async function poll(epoch = state.epoch, jobId = state.jobId) {
    try {
      const job = await api.request(`/api/v2/jobs/${jobId}`);
      if (epoch !== state.epoch || jobId !== state.jobId) return;
      showUsage(job.usage);
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
        for (const row of state.fileRows) {
          if (!row.uploaded) continue;
          if (job.error?.details?.file_id === row.uploaded.file_id) {
            row.status.textContent = job.error.message; row.status.classList.add('error-text');
          } else if (['completed', 'partial'].includes(job.status)) {
            row.status.textContent = '已读取'; row.status.classList.remove('error-text');
          }
        }
        state.resumeJobId = job.stage === 'video' && ['video_pacing', 'video_plan_check', 'render_layout', 'render_failed', 'render_timeout', 'output_truncated'].includes(job.error?.code) ? state.jobId : null;
        if (!state.answer) {
          el('result').textContent = '本次未发布完整回答，请根据提示补充或核对材料后重新提交。';
          el('answer-meta').textContent = '等待补充或核对材料';
        }
        release();
        // Memory refresh errors must not change the outcome of a completed generation.
        Promise.resolve().then(() => onCompleted(job)).catch(error => console.error('Post-generation refresh failed', error));
      } else { state.timer = scheduler.setTimeout(() => poll(epoch, jobId), 1000); }
    } catch (error) {
      if (epoch !== state.epoch || jobId !== state.jobId) return;
      el('message').textContent = error.message;
      if (error.status === 404 || error.retryable === false) { storage.removeItem('manimagent.textJob'); release(); }
      else { state.timer = scheduler.setTimeout(() => poll(epoch, jobId), 3000); }
    }
  }

  function submissionSnapshot() {
    const question = el('text').value.trim(), material = el('material').value.trim();
    const instruction = question || '请根据上传或补充材料，完整解释其中的问题、依据和结论。';
    const outputMode = el('output-mode').value;
    const videoProfile = el('video-profile').value;
    const requiresVision = !state.reusedMaterialIds && state.fileRows.some(row => state.inputCapabilities?.formats.some(format => format.requires_vision && format.extensions.includes('.' + row.file.name.split('.').pop().toLowerCase())));
    const readiness = generationReadiness({outputMode, videoProfile, existingAnswer: false, requiresVision});
    if (readiness) throw new Error(readiness);
    if (!question && !material && !state.fileRows.length && !state.reusedMaterialIds) throw new Error('请输入问题、补充材料，或添加至少一个支持的附件。');
    const selectedMemory = memoryOptions();
    const memory = selectedMemory ? {...selectedMemory} : null;
    if (state.reusedMaterialIds) return {instruction, outputMode, videoProfile, memory, materialIds: [...state.reusedMaterialIds]};
    const limits = state.inputCapabilities?.limits;
    const textInputs = [question, material].filter(Boolean);
    if (limits && (textInputs.some(text => text.length > limits.max_text_chars) || textInputs.reduce((total, text) => total + text.length, 0) > limits.max_parse_chars)) throw new Error('输入文字超过当前处理上限，请缩小材料范围。');
    if (limits && state.fileRows.length + textInputs.length > limits.max_inputs) throw new Error(`一次最多 ${limits.max_inputs} 份输入；问题和补充材料各占一份，请移除部分附件。`);
    const files = state.fileRows.map(row => {
      const problem = fileProblem(row.file);
      if (problem) throw new Error(`${row.file.name}：${problem}`);
      let selection;
      if (row.start.value || row.end.value) {
        const start = Number(row.start.value), end = Number(row.end.value);
        if (!Number.isInteger(start) || !Number.isInteger(end) || start < 1 || end < start) throw new Error(`${row.file.name}：请同时填写有效的起始页和结束页；读取全文时两项留空。`);
        if (end - start + 1 > limits.max_parse_pages) throw new Error(`${row.file.name}：选择范围超过 ${limits.max_parse_pages} 页，请缩小范围。`);
        selection = {kind: row.file.name.toLowerCase().endsWith('.pptx') ? 'slides' : 'pages', start, end};
      }
      return {row, selection};
    });
    return {instruction, outputMode, videoProfile, memory, inputs: textInputs.map(text => ({type: 'text', text})), files};
  }
  async function submit(event) {
    event?.preventDefault();
    if (state.busy || state.revisionSaving) return;
    let snapshot;
    try { snapshot = submissionSnapshot(); }
    catch (error) { el('error').textContent = error.message; return; }
    let epoch = state.epoch;
    setBusy(true); el('error').textContent = ''; setStatus('准备材料', 'running');
    try {
      const {instruction, outputMode, videoProfile, memory} = snapshot;
      let source;
      if (snapshot.materialIds) source = {type: 'materials', material_ids: snapshot.materialIds, instruction};
      else {
        const inputs = snapshot.inputs;
        for (const {row, selection} of snapshot.files) {
          if (!row.uploaded) {
            const data = new FormData(); data.append('file', row.file);
            el('message').textContent = `正在上传 ${row.file.name}`;
            row.status.textContent = '上传中…'; row.status.classList.remove('error-text');
            try { row.uploaded = await api.request('/api/v2/files', {method: 'POST', body: data}); }
            catch (error) {
              row.status.textContent = `上传失败：${error.message}`; row.status.classList.add('error-text');
              throw error;
            }
            if (epoch !== state.epoch) return;
            row.status.textContent = '已上传，等待读取';
          }
          inputs.push({type: 'file', file_id: row.uploaded.file_id, ...(selection ? {selection} : {})});
        }
        source = {type: 'inputs', inputs, instruction};
      }
      if (snapshot.files?.some(({row}) => row.uploaded?.media_type?.startsWith('image/'))) {
        const readiness = generationReadiness({outputMode, videoProfile, existingAnswer: false, requiresVision: true});
        if (readiness) throw new Error(readiness);
      }
      const job = await api.post('/api/v2/generations', {output_mode: outputMode, source, video_profile_id: videoProfile, ...(memory ? {memory} : {})});
      if (epoch !== state.epoch) return;
      // Keep the existing answer visible until the new request has actually been accepted.
      epoch = beginView();
      state.answer = null; storage.removeItem('manimagent.answer');
      clearMemorySnapshot();
      el('answer-meta').textContent = '正在生成，请保留此页面或稍后回来';
      el('result').textContent = '正在读取材料与生成完整回答…';
      el('actions').hidden = true; el('editor').hidden = true; el('sources').hidden = true; el('warnings').textContent = '';
      await watchJob(job.job_id, epoch);
    } catch (error) {
      if (epoch === state.epoch) {
        el('error').textContent = error.message; el('message').textContent = '';
        if (!state.answer) { el('result').textContent = '生成未完成，请检查输入后重试。'; el('answer-meta').textContent = '等待重新提交'; }
        setStatus('未完成', 'failed'); release();
      }
    }
  }
  function example() { el('text').value = '给定 a=(3,2)、b=(2,0)，求 a 在 b 方向上的投影。解释 b≠0 的前提、正交依据、投影系数与投影向量的区别，并验证残差与 b 正交。面向首次学习投影的学生完整推导。'; }
  async function animate() {
    if (!state.answer || state.busy || state.revisionSaving) return;
    const videoProfile = el('video-profile').value;
    const readiness = generationReadiness({outputMode: 'both', videoProfile, existingAnswer: true});
    if (readiness) { el('message').textContent = readiness; return; }
    const sourceAnswer = state.answer.answer_id, resumeJob = state.resumeJobId;
    let epoch = state.epoch;
    setBusy(true); setStatus('准备动画', 'running');
    try {
      const job = await api.post('/api/v2/videos', {answer_id: sourceAnswer, video_profile_id: videoProfile, ...(resumeJob ? {resume_job_id: resumeJob} : {})});
      if (epoch !== state.epoch) return;
      // Preserve the previous playable result until the new video request is accepted.
      epoch = beginView();
      el('video-panel').hidden = false;
      await watchJob(job.job_id, epoch);
    } catch (error) { if (epoch === state.epoch) { el('message').textContent = error.message; setStatus('动画未完成', 'failed'); release(); } }
  }
  async function copy() { try { await window.navigator.clipboard.writeText(state.answer.markdown); el('message').textContent = '正文已复制'; } catch { el('message').textContent = '复制失败，可下载 Markdown'; } }
  function openSource(event) {
    if (event.target.closest('a')?.getAttribute('href')?.startsWith('#block_')) {
      el('result-details').open = true;
      el('sources').open = true;
    }
  }
  function edit() { if (!state.answer || state.revisionSaving) return; el('revision').value = state.answer.markdown; el('editor').hidden = false; el('revision').focus(); }
  function cancel() { el('editor').hidden = true; }
  async function saveRevision() {
    if (!state.answer || state.revisionSaving) return;
    let epoch = state.epoch, accepted = false;
    state.revisionSaving = true; setBusy(state.busy);
    try {
      const revised = await api.post('/api/v2/answers/revisions', {base_answer_id: state.answer.answer_id, markdown: el('revision').value});
      if (epoch !== state.epoch) return;
      accepted = true;
      epoch = beginView();
      await showAnswer(revised.answer_id, epoch);
      if (epoch !== state.epoch) return;
      release();
      el('message').textContent = '已保存新版本，旧回答保持可读';
    } catch (error) { if (epoch === state.epoch) { el('message').textContent = error.message; if (accepted) release(); } }
    finally { state.revisionSaving = false; setBusy(state.busy); }
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
    selectFiles, loadInputs, materialChanged, example, copy, edit, cancel, reuse, openSource};
}
