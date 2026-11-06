// Presets choose an endpoint, not a new provider protocol.
const presets = {
  custom: {label: '自定义 / 本地兼容接口'},
  deepseek: {label: 'DeepSeek 官方', base_url: 'https://api.deepseek.com', model: 'deepseek-flash', docs: 'https://api-docs.deepseek.com/'},
  mimo: {label: 'MiMo 官方 · 核心模型', base_url: 'https://api.xiaomimimo.com/v1', docs: 'https://mimo.mi.com/docs/api/text-generation/openai-api'},
};
const origin = value => { try { return new URL(value).origin; } catch { return ''; } };

export function createSettings({dom, api}) {
  const {el} = dom;
  const state = {revision: 0, cards: [], busy: false};
  async function refreshHealth() {
    const health = await api.health();
    el('llm-state').textContent = health.ready ? '已就绪' : '待配置';
    return health;
  }
  const llmRoles = {answer_writer: 'llm-writer', answer_checker: 'llm-checker', video_planner: 'llm-planner', scene_generator: 'llm-scene', plan_checker: 'llm-plan-checker', material_reader: 'llm-material-reader'};

  function setLLMBusy(busy) {
    state.busy = busy;
    for (const id of ['llm-save', 'llm-reload', 'llm-add']) el(id).disabled = busy;
    for (const id of [...Object.values(llmRoles), 'llm-context', 'llm-check-enabled']) el(id).disabled = busy;
    for (const card of state.cards) card.fieldset.disabled = busy;
  }

  function llmField(card, parent, key, label, value, type = 'text') {
    const container = dom.create('div');
    const input = dom.create(Array.isArray(type) ? 'select' : 'input');
    input.id = `llm-${card.identifier}-${key}`;
    const caption = dom.create('label'); caption.htmlFor = input.id; caption.textContent = label;
    if (Array.isArray(type)) {
      for (const [id, name] of type) { const option = dom.create('option'); option.value = id; option.textContent = name; input.append(option); }
    } else { input.type = type; }
    input.value = value ?? '';
    if (type === 'password') input.autocomplete = 'new-password';
    container.append(caption, input); parent.append(container); card.fields[key] = input;
    return input;
  }

  function updateRoleOptions() {
    for (const id of Object.values(llmRoles)) {
      const selection = el(id).value;
      el(id).replaceChildren();
      if (id === 'llm-material-reader') { const option = dom.create('option'); option.value = ''; option.textContent = '跟随回答模型'; el(id).append(option); }
      for (const card of state.cards) {
        if (id === 'llm-material-reader' && !card.fields.supports_vision.checked) continue;
        const option = dom.create('option'); option.value = card.identifier;
        option.textContent = `${card.identifier}${card.fields.model.value ? ' · ' + card.fields.model.value : ''}`;
        el(id).append(option);
      }
      if (state.cards.some(c => c.identifier === selection && (id !== 'llm-material-reader' || c.fields.supports_vision.checked))) el(id).value = selection;
      else if (id === 'llm-material-reader') el(id).value = '';
    }
  }

  function renderModelCard(identifier, model) {
    const card = {identifier, original: model.config, fields: {}, keyConfigured: model.key_configured};
    const fieldset = dom.create('fieldset'); fieldset.className = 'llm-card';
    card.fieldset = fieldset; fieldset.disabled = state.busy;
    const legend = dom.create('legend'); legend.textContent = identifier; fieldset.append(legend);
    const providerBox = dom.create('div'); providerBox.className = 'provider-choice'; fieldset.append(providerBox);
    const providerLabel = dom.create('label'); providerLabel.htmlFor = `llm-${identifier}-provider`; providerLabel.textContent = 'API 服务';
    const provider = dom.create('select'); provider.id = providerLabel.htmlFor; card.provider = provider;
    for (const [value, preset] of Object.entries(presets)) { const option = dom.create('option'); option.value = value; option.textContent = preset.label; provider.append(option); }
    provider.value = Object.keys(presets).find(name => presets[name].base_url && origin(presets[name].base_url) === origin(model.config.base_url)) || 'custom';
    const help = dom.create('p'); help.className = 'hint provider-help';
    providerBox.append(providerLabel, provider, help);
    const grid = dom.create('div'); grid.className = 'llm-grid'; fieldset.append(grid);
    const cfg = model.config;
    const base = llmField(card, grid, 'base_url', 'API 地址', cfg.base_url, 'url');
    base.placeholder = 'https://gateway.example/v1';
    const modelName = llmField(card, grid, 'model', '模型名称', cfg.model);
    modelName.placeholder = '例如 deepseek-flash'; modelName.addEventListener('input', updateRoleOptions);
    const visionLabel = dom.create('label'); visionLabel.className = 'check-label';
    const vision = dom.create('input'); vision.type = 'checkbox'; vision.checked = !!cfg.supports_vision; card.fields.supports_vision = vision;
    visionLabel.append(vision, dom.text('支持图片输入'));
    const visionHelp = dom.create('p'); visionHelp.className = 'hint'; visionHelp.textContent = '仅对支持 image_url 的模型开启；连接测试不验证读图能力。';
    vision.addEventListener('change', updateRoleOptions);
    const key = llmField(card, grid, 'api_key', model.key_configured ? 'API Key（已设置；留空保留）' : 'API Key', '', 'password');
    key.placeholder = model.key_configured ? '已设置，输入新值可替换' : '填写服务商提供的密钥';
    const clearLabel = dom.create('label'); clearLabel.className = 'check-label';
    const clear = dom.create('input'); clear.type = 'checkbox';
    clearLabel.append(clear, dom.text('清除此模型保存的密钥关联')); card.clear = clear;
    key.addEventListener('input', () => { if (key.value.trim()) clear.checked = false; });
    const advanced = dom.create('details');
    const summary = dom.create('summary'); summary.textContent = '高级设置'; advanced.append(summary);
    const advancedGrid = dom.create('div'); advancedGrid.className = 'llm-grid'; advanced.append(advancedGrid); fieldset.append(advanced);
    advanced.append(visionLabel, visionHelp);
    const auth = llmField(card, advancedGrid, 'auth_mode', '鉴权方式', cfg.auth_mode || 'bearer', [['bearer', 'Bearer API Key'], ['api_key_header', '自定义 API Key 请求头'], ['none', '无鉴权（本地服务等）']]);
    advanced.append(clearLabel);
    llmField(card, advancedGrid, 'generate_url', '完整生成地址（可选，填写后优先使用）', cfg.generate_url, 'url');
    base.addEventListener('input', () => { card.fields.generate_url.value = ''; });
    const header = llmField(card, advancedGrid, 'api_key_header', '自定义密钥头名称', cfg.api_key_header);
    const output = llmField(card, advancedGrid, 'max_output_tokens', '最大输出 token', cfg.max_output_tokens, 'number'); output.min = '256'; output.max = '32000'; output.required = true;
    const context = llmField(card, advancedGrid, 'context_window', '模型上下文上限（token）', cfg.context_window, 'number'); context.min = '1024'; context.required = true;
    llmField(card, advancedGrid, 'token_parameter', '输出长度参数', cfg.token_parameter, [['max_tokens', 'max_tokens'], ['max_completion_tokens', 'max_completion_tokens']]);
    llmField(card, advancedGrid, 'json_mode', '检查报告 JSON 模式', String(cfg.json_mode), [['true', '使用 response_format'], ['false', '提示词约束并校验']]);
    llmField(card, advancedGrid, 'thinking', '思考模式（仅用于已确认支持的接口）', cfg.thinking || 'default', [['default', '接口默认，不发送参数'], ['disabled', '关闭 thinking'], ['enabled', '启用 thinking']]);
    const controls = dom.create('div'); controls.className = 'llm-tests';
    const use = dom.create('button'); use.type = 'button'; use.className = 'secondary'; use.textContent = '使用此模型';
    use.onclick = () => {
      if (state.busy) return;
      for (const [role, id] of Object.entries(llmRoles)) el(id).value = role === 'material_reader' && !vision.checked ? '' : identifier;
      el('llm-message').textContent = `已选择 ${identifier}，保存后生效。`;
    };
    controls.append(use); card.use = use;
    const status = dom.create('p'); status.className = 'llm-test-status'; status.setAttribute('role', 'status'); card.status = status;
    const advancedTests = dom.create('div'); advancedTests.className = 'llm-tests'; advanced.append(advancedTests);
    for (const [scope, title] of [['text', '测试连接'], ['structured', '测试结构化输出']]) {
      const button = dom.create('button'); button.type = 'button'; button.className = 'secondary'; button.textContent = title;
      button.onclick = () => testModel(card, scope); (scope === 'text' ? controls : advancedTests).append(button);
    }
    fieldset.append(controls, status);
    function showAuth() { key.disabled = auth.value === 'none'; header.disabled = auth.value !== 'api_key_header'; }
    function providerHelp() {
      help.replaceChildren();
      const preset = presets[provider.value] || presets.custom;
      modelName.placeholder = provider.value === 'mimo' ? '例如 mimo-v2.6-flash（以账户可用型号为准）' : provider.value === 'deepseek' ? 'deepseek-flash' : '服务商提供的模型 ID';
      if (provider.value === 'custom') help.append(dom.text('使用服务商提供的 Chat Completions 兼容地址。'));
      if (preset.docs) { const link = dom.create('a'); link.href = preset.docs; link.target = '_blank'; link.rel = 'noopener noreferrer'; link.textContent = '服务商文档 ↗'; help.append(link); }
    }
    provider.addEventListener('change', () => {
      if (state.busy) return;
      const preset = presets[provider.value];
      if (preset.base_url) {
        card.fields.base_url.value = preset.base_url;
        card.fields.generate_url.value = '';
        card.fields.model.value = preset.model || '';
        vision.checked = false;
        card.fields.thinking.value = 'default'; auth.value = provider.value === 'mimo' ? 'api_key_header' : 'bearer';
        header.value = provider.value === 'mimo' ? 'api-key' : 'X-API-Key';
        card.fields.token_parameter.value = provider.value === 'mimo' ? 'max_completion_tokens' : 'max_tokens';
        if (card.keyConfigured && origin(cfg.generate_url || cfg.base_url) !== origin(preset.base_url)) clear.checked = true;
        key.value = ''; showAuth(); updateRoleOptions();
      }
      providerHelp();
    });
    auth.addEventListener('change', showAuth); showAuth();
    providerHelp();
    state.cards.push(card); el('llm-models').append(fieldset);
  }

  function readModelDraft(card) {
    const config = {...card.original};
    for (const [name, input] of Object.entries(card.fields)) {
      if (name === 'api_key') continue;
      config[name] = input.type === 'checkbox' ? input.checked : input.type === 'number' ? Number(input.value) : input.value.trim();
    }
    config.json_mode = config.json_mode === 'true';
    // Accept the complete Chat Completions URL users copy from provider examples.
    if (!config.generate_url && /\/chat\/completions\/?$/.test(config.base_url)) {
      config.generate_url = config.base_url.replace(/\/$/, '');
      config.base_url = config.generate_url.slice(0, -'/chat/completions'.length);
    }
    const apiKey = card.fields.api_key.value.trim();
    if (apiKey && card.clear.checked) throw new Error(`${card.identifier}：不能同时输入新密钥和停用密钥关联。`);
    if (!apiKey && !card.clear.checked && card.keyConfigured && config.auth_mode !== 'none' && origin(config.generate_url || config.base_url) !== origin(card.original.generate_url || card.original.base_url)) {
      throw new Error(`${card.identifier}：接口服务地址已改变，请输入新服务的 API Key，避免把原密钥发往其他服务。`);
    }
    return {config, ...(apiKey ? {api_key: apiKey} : {}), clear_api_key: card.clear.checked};
  }

  async function testModel(card, scope) {
    if (state.busy) return;
    setLLMBusy(true);
    card.status.textContent = '正在测试…'; card.status.classList.remove('error-text');
    try {
      const result = await api.post('/api/v2/llm-settings/test', {model_id: card.identifier, draft: readModelDraft(card), scope});
      card.status.textContent = `${result.message} · ${result.elapsed_seconds} 秒（未保存配置）`;
    } catch (error) { card.status.textContent = error.message; card.status.classList.add('error-text'); }
    finally { setLLMBusy(false); }
  }

  function displayLLMSettings(data) {
    state.revision = data.revision; state.cards = []; el('llm-models').replaceChildren();
    for (const [identifier, model] of Object.entries(data.models)) renderModelCard(identifier, model);
    updateRoleOptions();
    for (const [role, id] of Object.entries(llmRoles)) el(id).value = data.roles[role] ?? '';
    el('llm-context').value = data.answer_context_tokens; el('llm-check-enabled').checked = data.answer_check;
  }

  async function loadLLMSettings() {
    if (state.busy) return;
    setLLMBusy(true);
    el('llm-message').classList.remove('error-text');
    try {
      displayLLMSettings(await api.request('/api/v2/llm-settings'));
      try {
        const health = await refreshHealth();
        el('llm-message').textContent = health.ready ? '' : '填写地址、模型和密钥后保存。';
      } catch {
        el('llm-message').textContent = '核心配置已读取，但状态刷新失败。请稍后重新载入。';
        el('llm-message').classList.add('error-text'); el('llm-state').textContent = '状态待刷新';
      }
    } catch (error) {
      el('llm-message').textContent = `配置读取失败：${error.message}`; el('llm-message').classList.add('error-text');
      el('llm-state').textContent = '读取失败';
    } finally { setLLMBusy(false); }
  }

  function add() {
    if (state.busy) return;
    if (state.cards.length >= 16) { el('llm-message').textContent = '当前最多支持 16 个模型配置。'; return; }
    let index = 1; while (state.cards.some(c => c.identifier === `model_${index}`)) index += 1;
    renderModelCard(`model_${index}`, {config: {base_url: '', model: '', auth_mode: 'bearer', api_key_header: 'X-API-Key', generate_url: '', max_output_tokens: 6000, context_window: 32000, token_parameter: 'max_tokens', json_mode: true}, key_configured: false});
    updateRoleOptions();
  }
  async function save(event) {
    event?.preventDefault();
    if (state.busy) return;
    setLLMBusy(true); el('llm-message').classList.remove('error-text');
    try {
      const models = Object.fromEntries(state.cards.map(card => [card.identifier, readModelDraft(card)]));
      const response = await api.request('/api/v2/llm-settings', {method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({
        expected_revision: state.revision, models,
        roles: Object.fromEntries(Object.entries(llmRoles).map(([role, id]) => [role, role === 'material_reader' ? el(id).value || null : el(id).value])),
        answer_check: el('llm-check-enabled').checked, answer_context_tokens: Number(el('llm-context').value),
      })});
      displayLLMSettings(response);
      try {
        const health = await refreshHealth();
        el('llm-message').textContent = health.ready ? '已保存，用于新任务。' : '已保存，请补全地址、模型和密钥。';
      } catch { el('llm-message').textContent = '核心配置已保存，但状态刷新失败。请稍后重新载入；无需重复提交密钥。'; }
    } catch (error) { el('llm-message').textContent = error.message; el('llm-message').classList.add('error-text'); }
    finally { setLLMBusy(false); }
  }
  return {state, load: loadLLMSettings, save, add};
}
