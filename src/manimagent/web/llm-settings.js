let llmRevision = 0;
let llmCards = [];

function llmInput(card, parent, key, label, value, type = 'text') {
  const container = document.createElement('div');
  const input = document.createElement('input');
  const id = `llm-${card.identifier}-${key}`;
  const caption = document.createElement('label'); caption.htmlFor = id; caption.textContent = label;
  input.id = id; input.type = type; input.value = value ?? '';
  if (type === 'password') input.autocomplete = 'new-password';
  container.append(caption, input); parent.append(container); card.fields[key] = input;
  return input;
}

function llmSelect(card, parent, key, label, value, choices) {
  const container = document.createElement('div');
  const input = document.createElement('select'); input.id = `llm-${card.identifier}-${key}`;
  const caption = document.createElement('label'); caption.htmlFor = input.id; caption.textContent = label;
  for (const [id, name] of choices) { const option = document.createElement('option'); option.value = id; option.textContent = name; input.append(option); }
  input.value = value; container.append(caption, input); parent.append(container); card.fields[key] = input;
  return input;
}

function updateRoleOptions() {
  for (const id of ['llm-writer', 'llm-checker', 'llm-planner', 'llm-scene', 'llm-plan-checker']) {
    const selection = el(id).value;
    el(id).replaceChildren();
    for (const card of llmCards) {
      const option = document.createElement('option'); option.value = card.identifier;
      option.textContent = `${card.identifier}${card.fields.model.value ? ' · ' + card.fields.model.value : ''}`;
      el(id).append(option);
    }
    if (llmCards.some(c => c.identifier === selection)) el(id).value = selection;
  }
}

function renderModelCard(identifier, model) {
  const card = {identifier, original: model.config, fields: {}};
  const fieldset = document.createElement('fieldset'); fieldset.className = 'llm-card';
  const legend = document.createElement('legend'); legend.textContent = identifier; fieldset.append(legend);
  const grid = document.createElement('div'); grid.className = 'llm-grid'; fieldset.append(grid);
  const cfg = model.config;
  llmInput(card, grid, 'base_url', 'API 根地址（包含版本前缀）', cfg.base_url, 'url').placeholder = 'https://gateway.example/v1';
  llmInput(card, grid, 'model', '模型名称', cfg.model).addEventListener('input', updateRoleOptions);
  const auth = llmSelect(card, grid, 'auth_mode', '鉴权方式', cfg.auth_mode, [['bearer', 'Bearer API Key'], ['api_key_header', '自定义 API Key 请求头'], ['none', '无鉴权（本地服务等）']]);
  const key = llmInput(card, grid, 'api_key', model.key_configured ? 'API Key（已设置；留空保留）' : 'API Key', '', 'password');
  key.placeholder = model.key_configured ? '已设置，输入新值可替换' : '填写服务商提供的密钥';
  const clearLabel = document.createElement('label'); clearLabel.className = 'check-label';
  const clear = document.createElement('input'); clear.type = 'checkbox';
  clearLabel.append(clear, document.createTextNode('停用此模型当前的密钥关联')); fieldset.append(clearLabel); card.clear = clear;
  const advanced = document.createElement('details');
  const summary = document.createElement('summary'); summary.textContent = '高级选项'; advanced.append(summary);
  const advancedGrid = document.createElement('div'); advancedGrid.className = 'llm-grid'; advanced.append(advancedGrid); fieldset.append(advanced);
  llmInput(card, advancedGrid, 'generate_url', '完整生成地址（可选，填写后优先使用）', cfg.generate_url, 'url');
  const header = llmInput(card, advancedGrid, 'api_key_header', '自定义密钥头名称', cfg.api_key_header);
  const output = llmInput(card, advancedGrid, 'max_output_tokens', '最大输出 token', cfg.max_output_tokens, 'number'); output.min = '256'; output.max = '32000'; output.required = true;
  const context = llmInput(card, advancedGrid, 'context_window', '模型上下文上限（token）', cfg.context_window, 'number'); context.min = '1024'; context.required = true;
  llmSelect(card, advancedGrid, 'token_parameter', '输出长度参数', cfg.token_parameter, [['max_tokens', 'max_tokens'], ['max_completion_tokens', 'max_completion_tokens']]);
  llmSelect(card, advancedGrid, 'json_mode', '检查报告 JSON 模式', String(cfg.json_mode), [['true', '使用 response_format'], ['false', '提示词约束并校验']]);
  llmSelect(card, advancedGrid, 'thinking', '思考模式（仅用于已确认支持的接口）', cfg.thinking || 'default', [['default', '接口默认，不发送参数'], ['disabled', '关闭 thinking'], ['enabled', '启用 thinking']]);
  const controls = document.createElement('div'); controls.className = 'llm-tests';
  const status = document.createElement('p'); status.className = 'llm-test-status'; status.setAttribute('role', 'status'); card.status = status;
  for (const [scope, title] of [['text', '测试文本调用'], ['structured', '测试结构化输出']]) {
    const button = document.createElement('button'); button.type = 'button'; button.className = 'secondary'; button.textContent = title;
    button.onclick = () => testModel(card, scope); controls.append(button);
  }
  fieldset.append(controls, status);
  function showAuth() { key.disabled = auth.value === 'none'; header.disabled = auth.value !== 'api_key_header'; }
  auth.addEventListener('change', showAuth); showAuth();
  llmCards.push(card); el('llm-models').append(fieldset);
}

function readModelDraft(card) {
  const config = {...card.original};
  for (const [name, input] of Object.entries(card.fields)) {
    if (name === 'api_key') continue;
    config[name] = input.type === 'number' ? Number(input.value) : input.value.trim();
  }
  config.json_mode = config.json_mode === 'true';
  const apiKey = card.fields.api_key.value.trim();
  if (apiKey && card.clear.checked) throw new Error(`${card.identifier}：不能同时输入新密钥和停用密钥关联。`);
  return {config, ...(apiKey ? {api_key: apiKey} : {}), clear_api_key: card.clear.checked};
}

async function testModel(card, scope) {
  const buttons = Array.from(document.querySelectorAll('.llm-tests button'));
  buttons.forEach(b => { b.disabled = true; });
  card.status.textContent = '正在向指定接口发送测试请求…'; card.status.classList.remove('error-text');
  try {
    const result = await post('/api/v2/llm-settings/test', {model_id: card.identifier, draft: readModelDraft(card), scope});
    card.status.textContent = `${result.message} · ${result.elapsed_seconds} 秒 · ${result.usage.model_calls} 次调用（未保存配置）`;
  } catch (error) { card.status.textContent = error.message; card.status.classList.add('error-text'); }
  finally { buttons.forEach(b => { b.disabled = false; }); }
}

function displayLLMSettings(data) {
  llmRevision = data.revision; llmCards = []; el('llm-models').replaceChildren();
  for (const [identifier, model] of Object.entries(data.models)) renderModelCard(identifier, model);
  updateRoleOptions();
  el('llm-writer').value = data.roles.answer_writer; el('llm-checker').value = data.roles.answer_checker;
  el('llm-planner').value = data.roles.video_planner; el('llm-scene').value = data.roles.scene_generator; el('llm-plan-checker').value = data.roles.plan_checker;
  el('llm-context').value = data.answer_context_tokens; el('llm-check-enabled').checked = data.answer_check;
}

async function loadLLMSettings() {
  el('llm-message').classList.remove('error-text');
  try {
    displayLLMSettings(await request('/api/v2/llm-settings'));
    const health = await refreshHealth();
    el('llm-state').textContent = health.ready ? '已就绪' : '待配置';
    if (!health.ready) el('llm-settings').open = true;
    el('llm-message').textContent = '';
  } catch (error) {
    el('llm-message').textContent = `配置读取失败：${error.message}`; el('llm-message').classList.add('error-text');
    el('llm-state').textContent = '读取失败'; el('llm-settings').open = true;
  }
}

el('llm-add').onclick = () => {
  if (llmCards.length >= 16) { el('llm-message').textContent = '当前最多支持 16 个模型配置。'; return; }
  let index = 1; while (llmCards.some(c => c.identifier === `model_${index}`)) index += 1;
  renderModelCard(`model_${index}`, {config: {base_url: '', model: '', auth_mode: 'bearer', api_key_header: 'X-API-Key', generate_url: '', max_output_tokens: 6000, context_window: 32000, token_parameter: 'max_tokens', json_mode: true}, key_configured: false});
  updateRoleOptions();
};
el('llm-reload').onclick = loadLLMSettings;
el('llm-form').addEventListener('submit', async event => {
  event.preventDefault(); el('llm-save').disabled = true; el('llm-message').classList.remove('error-text');
  try {
    const models = Object.fromEntries(llmCards.map(card => [card.identifier, readModelDraft(card)]));
    const response = await request('/api/v2/llm-settings', {method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({
      expected_revision: llmRevision, models,
      roles: {answer_writer: el('llm-writer').value, answer_checker: el('llm-checker').value, video_planner: el('llm-planner').value, scene_generator: el('llm-scene').value, plan_checker: el('llm-plan-checker').value},
      answer_check: el('llm-check-enabled').checked, answer_context_tokens: Number(el('llm-context').value),
    })});
    displayLLMSettings(response);
    const health = await refreshHealth();
    el('llm-state').textContent = health.ready ? '已就绪' : '待配置';
    el('llm-message').textContent = health.ready ? '已保存并应用。新任务立即使用此配置。' : '配置已保存，但仍缺少接口、模型或密钥，请补全后保存。';
  } catch (error) { el('llm-message').textContent = error.message; el('llm-message').classList.add('error-text'); }
  finally { el('llm-save').disabled = false; }
});
loadLLMSettings();
