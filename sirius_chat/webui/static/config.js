
// ── Providers ─────────────────────────────────────────
const BUILTIN_PROVIDER_TYPES = ['deepseek','aliyun-bailian','bigmodel','siliconflow','volcengine-ark','ytea'];
const PROVIDER_TYPE_OPTIONS = [
  {value:'openai-compatible',label:'OpenAI Compatible'},
  {value:'deepseek',label:'DeepSeek'},
  {value:'aliyun-bailian',label:'阿里云百炼'},
  {value:'bigmodel',label:'智谱 BigModel'},
  {value:'siliconflow',label:'SiliconFlow'},
  {value:'volcengine-ark',label:'火山方舟'},
  {value:'ytea',label:'YTea'},
];
const PROVIDER_DEFAULT_URLS = {
  'openai-compatible': 'https://api.openai.com',
  'deepseek': 'https://api.deepseek.com',
  'aliyun-bailian': 'https://dashscope.aliyuncs.com/compatible-mode',
  'bigmodel': 'https://open.bigmodel.cn/api/paas/v4',
  'siliconflow': 'https://api.siliconflow.cn',
  'volcengine-ark': 'https://ark.cn-beijing.volces.com/api/v3',
  'ytea': 'https://api.ytea.top',
};

let providerEditIndex = -1;
let providerBackup = null;

async function loadProviders() {
  try {
    const res = await get('/providers');
    providerDraft = JSON.parse(JSON.stringify(res.providers || []));
    providerEditIndex = -1;
    providerBackup = null;
    _renderProviderDraft();
    $('dashProviderCount').textContent = String(providerDraft.length);
  } catch (e) {}
}

function _providerTypeSelect(i, selected) {
  return `<select onchange="_onProviderTypeChange(${i},this.value)">
    ${PROVIDER_TYPE_OPTIONS.map(o => `<option value="${o.value}"${o.value===selected?' selected':''}>${o.label}</option>`).join('')}
  </select>`;
}

function _onProviderTypeChange(i, val) {
  providerDraft[i].type = val;
  if (BUILTIN_PROVIDER_TYPES.includes(val)) {
    providerDraft[i].base_url = PROVIDER_DEFAULT_URLS[val] || '';
  } else {
    providerDraft[i].base_url = providerDraft[i].base_url || 'https://';
  }
  _renderProviderDraft();
}

function _renderProviderModelsEdit(i) {
  const p = providerDraft[i];
  const models = p.models || [];
  const tags = models.map((m, mi) => `<span class="tag">${m} <span class="remove" onclick="providerDraft[${i}].models.splice(${mi},1);_renderProviderDraft()">✕</span></span>`).join('');
  return `
    <div class="tag-list">${tags}</div>
    <div class="pv-models-add">
      <input type="text" placeholder="添加模型名" id="pmodel-${i}" onkeydown="if(event.key==='Enter'){_addProviderModel(${i})}">
      <button class="btn small" onclick="_addProviderModel(${i})">添加</button>
    </div>
  `;
}

function _addProviderModel(i) {
  const input = $(`pmodel-${i}`);
  const v = input?.value?.trim();
  if (!v) return;
  providerDraft[i].models = providerDraft[i].models || [];
  if (!providerDraft[i].models.includes(v)) {
    providerDraft[i].models.push(v);
  }
  input.value = '';
  _renderProviderDraft();
}

function _maskKey(key) {
  if (!key) return '未设置';
  if (key.length <= 10) return '••••';
  return key.slice(0,6) + '••••' + key.slice(-4);
}

function _shortUrl(url, type) {
  if (!url) return '—';
  if (_isBuiltin(type)) {
    try { return new URL(url).hostname; } catch { return url; }
  }
  return url;
}

function _isBuiltin(type) {
  return BUILTIN_PROVIDER_TYPES.includes(type);
}

function providerToggleEnabled(i) {
  providerDraft[i].enabled = providerDraft[i].enabled === false ? true : false;
  _renderProviderDraft();
  // 自动保存，避免用户忘记点保存
  saveProviders();
}

function providerStartEdit(i) {
  providerBackup = JSON.parse(JSON.stringify(providerDraft[i]));
  providerEditIndex = i;
  _renderProviderDraft();
}

function providerCancelEdit() {
  if (providerEditIndex >= 0 && providerBackup) {
    providerDraft[providerEditIndex] = providerBackup;
  }
  providerEditIndex = -1;
  providerBackup = null;
  _renderProviderDraft();
}

function _renderProviderDraft() {
  const el = $('providerList');
  if (!providerDraft.length) {
    el.innerHTML = '<div style="color:var(--text-2);padding:10px">暂无 Provider，请点击「添加 Provider」。</div>';
    return;
  }
  el.innerHTML = providerDraft.map((p, i) => {
    const isEditing = i === providerEditIndex;
    const builtin = _isBuiltin(p.type);
    if (isEditing) {
      return `
      <div class="provider-row editing">
        <div class="pv-edit-grid">
          <div class="form-group"><label>平台</label>${_providerTypeSelect(i, p.type || '')}</div>
          <div class="form-group"><label>Base URL</label>
            ${builtin
              ? `<input type="text" value="${PROVIDER_DEFAULT_URLS[p.type]||''}" disabled style="opacity:.6">`
              : `<input type="text" value="${p.base_url||''}" oninput="providerDraft[${i}].base_url=this.value">`}
          </div>
          <div class="form-group"><label>API Key</label><input type="password" value="${p.api_key||''}" oninput="providerDraft[${i}].api_key=this.value" placeholder="sk-..."></div>
          <div class="form-group"><label>健康检查模型</label><input type="text" value="${p.healthcheck_model||''}" oninput="providerDraft[${i}].healthcheck_model=this.value"></div>
          <div class="form-group full">
            <label>模型列表</label>
            ${_renderProviderModelsEdit(i)}
          </div>
        </div>
        <div class="pv-edit-footer">
          <label style="display:flex;align-items:center;gap:6px;cursor:pointer;font-size:13px;color:var(--text)">
            <input type="checkbox"${p.enabled!==false?' checked':''} onchange="providerDraft[${i}].enabled=this.checked"> 启用
          </label>
          <div class="pv-actions">
            <button class="btn success small" onclick="saveProviders()">💾 保存</button>
            <button class="btn small" onclick="providerCancelEdit()">取消</button>
            <button class="btn small danger" onclick="providerDraft.splice(${i},1);providerEditIndex=-1;_renderProviderDraft()">删除</button>
          </div>
        </div>
      </div>`;
    }
    // 只读模式
    const modelsHtml = (p.models || []).map(m => `<span class="tag">${m}</span>`).join('');
    const urlDisplay = _shortUrl(p.base_url, p.type);
    const enabled = p.enabled !== false;
    return `
    <div class="provider-row readonly">
      <div class="pv-header">
        <div class="pv-header-left">
          <span class="pv-status ${enabled?'on':'off'}" onclick="providerToggleEnabled(${i})">${enabled?'启用':'禁用'}</span>
          <span class="pv-platform">${p.type || '未命名'}</span>
          ${builtin?'<span class="pv-badge builtin">内置</span>':''}
        </div>
        <div class="pv-actions">
          <button class="btn small" onclick="providerStartEdit(${i})">编辑</button>
          <button class="btn small danger" onclick="providerDraft.splice(${i},1);_renderProviderDraft()">删除</button>
        </div>
      </div>
      <div class="pv-models">${modelsHtml||'<span style="color:var(--text-2);font-size:12px">暂无模型</span>'}</div>
      <div class="pv-meta">
        <div class="pv-meta-item">🔑 <span class="mono">${_maskKey(p.api_key)}</span></div>
        <div class="pv-meta-item">🩺 <span class="mono">${p.healthcheck_model||'—'}</span></div>
        <div class="pv-meta-item">🔗 <span class="mono" title="${p.base_url||''}">${urlDisplay}</span></div>
      </div>
    </div>`;
  }).join('');
  mountCustomSelects(el);
}

function addProvider() {
  if (providerEditIndex >= 0) providerCancelEdit();
  const idx = providerDraft.length;
  providerDraft.push({ type: 'openai-compatible', base_url: 'https://api.openai.com', api_key: '', healthcheck_model: '', enabled: true, models: [] });
  providerStartEdit(idx);
}

async function saveProviders() {
  const res = await post('/providers', { providers: providerDraft });
  toast(res.success ? 'Provider 已保存' : res.error || '保存失败', res.success ? 'success' : 'error');
  if (res.success) {
    providerEditIndex = -1;
    providerBackup = null;
    flashSuccess(document.activeElement);
  }
  loadProviders();
}

// ── Persona ───────────────────────────────────────────
async function loadPersonaPreview() {
  if (!currentPersona) return;
  try {
    const res = await get(pApi('/persona'));
    const p = res.persona || {};
    $('pfName').value = p.name || '';
    $('pfAliases').value = (p.aliases || []).join(' ');
    $('pfSocialRole').value = p.social_role || 'caregiver';
    $('pfSummary').value = p.persona_summary || '';
    $('pfTraits').value = (p.personality_traits || []).join('，');
    $('pfStyle').value = p.communication_style || '';
    $('pfCatchphrases').value = (p.catchphrases || []).join('，');
    $('pfEmoji').value = p.emoji_preference || 'moderate';
    $('pfHumor').value = p.humor_style || 'wholesome';
    $('pfEmpathy').value = p.empathy_style || 'warm';
    $('pfBoundaries').value = (p.boundaries || []).join('，');
    $('pfTaboos').value = (p.taboo_topics || []).join('，');
    $('pfBackstory').value = p.backstory || '';
  } catch (e) {}
}

async function savePersonaForm() {
  if (!currentPersona) { toast('请先选择人格', 'error'); return; }
  const res = await post(pApi('/persona/save'), {
    persona: {
      name: $('pfName').value.trim(),
      aliases: $('pfAliases').value.split(/\s+/).filter(Boolean),
      social_role: $('pfSocialRole').value,
      persona_summary: $('pfSummary').value.trim(),
      personality_traits: $('pfTraits').value.split(/[,，]/).map(s => s.trim()).filter(Boolean),
      communication_style: $('pfStyle').value.trim(),
      catchphrases: $('pfCatchphrases').value.split(/[,，]/).map(s => s.trim()).filter(Boolean),
      emoji_preference: $('pfEmoji').value,
      humor_style: $('pfHumor').value,
      empathy_style: $('pfEmpathy').value,
      boundaries: $('pfBoundaries').value.split(/[,，]/).map(s => s.trim()).filter(Boolean),
      taboo_topics: $('pfTaboos').value.split(/[,，]/).map(s => s.trim()).filter(Boolean),
      backstory: $('pfBackstory').value.trim(),
    }
  });
  toast(res.success ? '人格已保存' : res.error || '保存失败', res.success ? 'success' : 'error');
  if (res.success) flashSuccess(document.activeElement);
  loadPersonaStatus();
}

async function savePersona(jsonStr) {
  if (!currentPersona) return;
  const res = await post(pApi('/persona/save'), { persona: JSON.parse(jsonStr) });
  toast(res.success ? '人格已保存' : res.error || '保存失败', res.success ? 'success' : 'error');
  loadPersonaStatus();
}

const interviewQuestions = [
  '如果把 TA 放进群聊，TA 更像哪类群体角色？是活跃气氛的人、冷幽默观察者、可靠收束者，还是偶尔出手的梗王？',
  'TA 在多人对话里的发言节奏如何？什么时候会抢话、接梗、补刀、收尾，什么时候会选择潜水？',
  'TA 如何区分群内不同关系层级？公开场合和私下场合，对熟人和生人会有什么明显区别？',
  '群里气氛好、被冷落、有人争执、有人单独 cue TA 时，TA 的情绪和反应路径分别是什么？',
  'TA 的群聊语言风格是什么？会不会用梗、方言、昵称、复读、反问、表情包式句法？最该避免哪些 AI 味回复？',
  'TA 在群聊中的边界与禁忌是什么？面对多人起哄、越界玩笑、道德绑架或拉踩时会怎么处理？',
  'TA 在群里最真实的小习惯或记忆点是什么？什么细节会让人一看就觉得「这人很具体」？',
  '这个群聊角色的社交气质从什么经历里长出来？哪些过去的圈子、职业或成长环境塑造了 TA 的群体互动方式？',
];
async function renderInterviewQuestions() {
  // 先渲染空表单
  $('interviewQuestions').innerHTML = interviewQuestions
    .map(
      (q, i) => `
    <div class="question-block">
      <div class="q">Q${i + 1}. ${q}</div>
      <textarea id="ivAns${i}" placeholder="请回答..."></textarea>
    </div>
  `
    )
    .join('');
  // 若有已选人格，尝试加载已有 interview 答案
  if (!currentPersona) return;
  try {
    const data = await get(pApi('/persona/interview'));
    if (data.answers) {
      interviewQuestions.forEach((_, i) => {
        const v = data.answers[String(i + 1)];
        if (v) {
          const el = $(`ivAns${i}`);
          if (el) el.value = v;
        }
      });
    }
    if (data.name) $('ivName').value = data.name;
    if (data.aliases && data.aliases.length) $('ivAliases').value = data.aliases.join(' ');
  } catch (e) {
    // 静默失败：无记录时保持空表单
  }
}

async function generatePersonaInterview() {
  if (!currentPersona) { toast('请先选择人格', 'error'); return; }
  const btn = $('ivBtn');
  if (!btn) return;
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span> 生成中...';
  const answers = {};
  interviewQuestions.forEach((_, i) => {
    const v = $(`ivAns${i}`).value.trim();
    if (v) answers[String(i + 1)] = v;
  });
  const res = await post(pApi('/persona/interview'), {
    name: $('ivName').value,
    aliases: $('ivAliases').value.split(/\s+/).filter(Boolean),
    answers,
    model: $('ivModel').value,
  });
  btn.disabled = false;
  btn.innerHTML = '✨ 生成人格';
  if (res.success) {
    $('ivResult').innerHTML = `<div class="preview-box">${JSON.stringify(res.persona, null, 2)}</div><button class="btn success" onclick="savePersona(${JSON.stringify(JSON.stringify(res.persona))})">💾 保存人格</button>`;
  } else {
    toast(res.error || '生成失败', 'error');
  }
}

// ── Orchestration ─────────────────────────────────────
function _fillSelect(id, value, choices) {
  const el = $(id);
  if (!el) return;
  el.innerHTML = '';
  const opts = choices.length ? choices : [{ label: value || 'gpt-4o', value: value || 'gpt-4o' }];
  opts.forEach((c) => {
    const opt = document.createElement('option');
    const label = typeof c === 'string' ? c : c.label;
    const val = typeof c === 'string' ? c : c.value;
    opt.value = val;
    opt.textContent = label;
    if (val === value) opt.selected = true;
    el.appendChild(opt);
  });
  syncCustomSelect(id);
}

async function loadAvailableModels() {
  try {
    const res = await get('/models');
    const choices = res.model_choices || [];
    const defaultModel = 'gpt-4o-mini';
    _fillSelect('kwModel', defaultModel, choices);
    _fillSelect('ivModel', defaultModel, choices);
  } catch (e) {}
}

async function loadOrchestration() {
  if (!currentPersona) return;
  try {
    const res = await get(pApi('/orchestration'));
    const orch = res || {};
    const choices = orch.model_choices || [];
    _fillSelect('orchAnalysis', orch.analysis_model || 'gpt-4o-mini', choices);
    _fillSelect('orchChat', orch.chat_model || 'gpt-4o', choices);
    _fillSelect('orchVision', orch.vision_model || 'gpt-4o', choices);
  } catch (e) {}
}

async function saveOrchestration() {
  if (!currentPersona) { toast('请先选择人格', 'error'); return; }
  const res = await post(pApi('/orchestration'), {
    analysis_model: $('orchAnalysis').value,
    chat_model: $('orchChat').value,
    vision_model: $('orchVision').value,
  });
  toast(res.success ? '模型编排已保存' : res.error || '失败', res.success ? 'success' : 'error');
  if (res.success) flashSuccess(document.activeElement);
}

// ── Groups ────────────────────────────────────────────
async function loadAdapters() {
  if (!currentPersona) return;
  try {
    const res = await get(pApi('/adapters'));
    const adapters = res.adapters || [];
    personaState.adapters = adapters;
    const a = adapters[0] || {};
    $('adEnabled').value = String(a.enabled !== false);
    $('adQQ').value = a.qq_number || '';
    $('adWsUrl').value = a.ws_url || 'ws://localhost:3001';
    $('adToken').value = a.token || 'napcat_ws';
    $('adRoot').value = a.root || '';
    $('adEnableGroup').value = String(a.enable_group_chat !== false);
    $('adEnablePrivate').value = String(a.enable_private_chat !== false);
    adapterGroupIds = a.allowed_group_ids || [];
    adapterPrivateIds = a.allowed_private_user_ids || [];
    renderGroups(adapterGroupIds);
    renderPrivates(adapterPrivateIds);
    clearAdaptersDirty();
  } catch (e) {
    // 保底初始化，避免 addGroup / saveAdapters 操作空对象
    adapterGroupIds = [];
    adapterPrivateIds = [];
    personaState.adapters = personaState.adapters || [{}];
  }
}

async function createBlankPersona() {
  const name = $('cpName').value.trim();
  if (!name) { toast('请输入人格标识', 'error'); return; }
  const res = await post('/personas', {
    name: name,
    persona_name: $('cpPersonaName').value.trim() || name,
  });
  if (res.success) {
    toast('人格创建成功');
    await loadPersonas();
    selectPersona(name);
    $('cpName').value = '';
    $('cpPersonaName').value = '';
  } else {
    toast(res.error || '创建失败', 'error');
  }
}

async function saveAdapters() {
  if (!currentPersona) { toast('请先选择人格', 'error'); return; }
  const res = await post(pApi('/adapters'), {
    adapters: [{
      type: 'napcat',
      enabled: $('adEnabled').value === 'true',
      qq_number: $('adQQ').value.trim(),
      ws_url: $('adWsUrl').value.trim(),
      token: $('adToken').value.trim(),
      root: $('adRoot').value.trim(),
      enable_group_chat: $('adEnableGroup').value === 'true',
      enable_private_chat: $('adEnablePrivate').value === 'true',
      allowed_group_ids: adapterGroupIds,
      allowed_private_user_ids: adapterPrivateIds,
    }]
  });
  toast(res.success ? 'Adapter 配置已保存' : res.error || '保存失败', res.success ? 'success' : 'error');
  if (res.success) {
    flashSuccess(document.activeElement);
    clearAdaptersDirty();
    const refreshed = await get(pApi('/adapters'));
    personaState.adapters = refreshed.adapters || [];
    const a = personaState.adapters[0] || {};
    adapterGroupIds = a.allowed_group_ids || [];
    adapterPrivateIds = a.allowed_private_user_ids || [];
  }
}

function renderGroups(list) {
  $('groupTags').innerHTML = list
    .map((g) => `<span class="tag">${g} <span class="remove" onclick="removeGroup('${g}')">✕</span></span>`)
    .join('');
}
function renderPrivates(list) {
  $('privateTags').innerHTML = list
    .map((u) => `<span class="tag">${u} <span class="remove" onclick="removePrivate('${u}')">✕</span></span>`)
    .join('');
}

function markAdaptersDirty() {
  const hint = $('adaptersDirtyHint');
  const btn = $('adaptersSaveBtn');
  if (hint) hint.style.display = '';
  if (btn) {
    btn.style.borderColor = 'var(--warn)';
    btn.style.color = 'var(--warn)';
  }
}
function clearAdaptersDirty() {
  const hint = $('adaptersDirtyHint');
  const btn = $('adaptersSaveBtn');
  if (hint) hint.style.display = 'none';
  if (btn) {
    btn.style.borderColor = '';
    btn.style.color = '';
  }
}

function addGroup() {
  const v = $('newGroupId').value.trim();
  if (v) {
    adapterGroupIds = adapterGroupIds || [];
    if (!adapterGroupIds.includes(v)) adapterGroupIds.push(v);
    $('newGroupId').value = '';
    renderGroups(adapterGroupIds);
    markAdaptersDirty();
  }
}
function removeGroup(g) {
  adapterGroupIds = (adapterGroupIds || []).filter((x) => x !== g);
  renderGroups(adapterGroupIds);
  markAdaptersDirty();
}
function addPrivate() {
  const v = $('newPrivateId').value.trim();
  if (v) {
    adapterPrivateIds = adapterPrivateIds || [];
    if (!adapterPrivateIds.includes(v)) adapterPrivateIds.push(v);
    $('newPrivateId').value = '';
    renderPrivates(adapterPrivateIds);
    markAdaptersDirty();
  }
}
function removePrivate(u) {
  adapterPrivateIds = (adapterPrivateIds || []).filter((x) => x !== u);
  renderPrivates(adapterPrivateIds);
  markAdaptersDirty();
}

// ── Engine ────────────────────────────────────────────
async function toggleEngine() {
  if (!currentPersona) { toast('请先选择人格', 'error'); return; }
  const res = await post(pApi('/engine/toggle'), { enabled: !personaState.enabled });
  toast(res.success ? (res.enabled ? 'AI 已开启' : 'AI 已关闭') : res.error || '失败', res.success ? 'success' : 'error');
  loadPersonaStatus();
}

async function reloadEngine() {
  if (!currentPersona) { toast('请先选择人格', 'error'); return; }
  const res = await post(pApi('/engine/reload'), {});
  toast(res.success ? '引擎已重建' : res.error || '失败', res.success ? 'success' : 'error');
  loadPersonaStatus();
}

// ── NapCat ────────────────────────────────────────────
async function ncLoadStatus() {
  try {
    const res = await get('/napcat/status');
    const elInstalled = $('ncInstalled');
    const elRunning = $('ncRunning');
    const elQQ = $('ncQQ');
    if (!res.enabled) {
      if (elInstalled) elInstalled.textContent = '管理未启用';
      if (elRunning) elRunning.textContent = '管理未启用';
      if (elQQ) elQQ.textContent = '管理未启用';
      return;
    }
    const installed = res.installed ? '✅ 已安装' : '❌ 未安装';
    const running = res.running ? '✅ 运行中' : '⏹ 已停止';
    const qq = res.qq_installed ? '✅ 已安装' : '❌ 未检测到';
    if (elInstalled) elInstalled.textContent = installed;
    if (elRunning) elRunning.textContent = running;
    if (elQQ) elQQ.textContent = qq + (res.qq_path ? ` (${res.qq_path})` : '');
    const installBtn = $('ncInstallBtn');
    const startBtn = $('ncStartBtn');
    const stopBtn = $('ncStopBtn');
    if (installBtn) installBtn.style.display = res.installed ? 'none' : 'inline-flex';
    if (startBtn) startBtn.style.display = res.installed ? 'inline-flex' : 'none';
    if (stopBtn) stopBtn.style.display = res.running ? 'inline-flex' : 'none';
  } catch (e) {
    console.error('ncLoadStatus', e);
  }
}

async function ncInstall() {
  const btn = $('ncInstallBtn');
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span> 安装中...';
  const res = await post('/napcat/install', {});
  btn.disabled = false;
  btn.innerHTML = '⬇️ 安装 NapCat';
  toast(res.success ? res.message : res.message || '安装失败', res.success ? 'success' : 'error');
  ncLoadStatus();
}
async function ncStart() {
  const btn = $('ncStartBtn');
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span> 启动中...';
  const res = await post('/napcat/start', {});
  btn.disabled = false;
  btn.innerHTML = '▶ 启动 NapCat';
  toast(res.success ? res.message : res.message || '启动失败', res.success ? 'success' : 'error');
  ncLoadStatus();
}
async function ncStop() {
  const res = await post('/napcat/stop', {});
  toast(res.success ? res.message : res.message || '停止失败', res.success ? 'success' : 'error');
  ncLoadStatus();
}
async function ncLoadLogs() {
  try {
    const res = await get('/napcat/logs?lines=50');
    if (res.enabled) {
      $('ncLogs').textContent = res.logs.length ? res.logs.join('\n') : '暂无日志';
    }
  } catch (e) {}
}

// ── Global Settings ───────────────────────────────────
async function loadGlobalSettings() {
  try {
    const res = await get('/global-config');
    $('gsHost').value = res.webui_host || '0.0.0.0';
    $('gsPort').value = res.webui_port || 8080;
    $('gsLogLevel').value = res.log_level || 'INFO';
    $('gsNapcatDir').value = res.napcat_install_dir || '';
    $('gsNapcatPort').value = res.napcat_base_port || 3001;
    // NapCat 自动管理默认启用，无需配置
  } catch (e) {}
}

async function saveGlobalSettings() {
  const res = await post('/global-config', {
    webui_host: $('gsHost').value,
    webui_port: parseInt($('gsPort').value, 10),
    log_level: $('gsLogLevel').value,
    napcat_install_dir: $('gsNapcatDir').value,
    napcat_base_port: parseInt($('gsNapcatPort').value, 10),
  });
  toast(res.success ? '全局设置已保存' : res.error || '保存失败', res.success ? 'success' : 'error');
  if (res.success) flashSuccess(document.activeElement);
}

// ── Experience ────────────────────────────────────────
async function loadExperience() {
  if (!currentPersona) return;
  try {
    const res = await get(pApi('/experience'));
    const e = res.experience || {};
    $('expReplyMode').value = e.reply_mode || 'auto';
    $('expSensitivity').value = e.engagement_sensitivity ?? 0.5;
    $('expHeatWindow').value = e.heat_window_seconds ?? 60;
    $('expProactive').value = String(e.proactive_enabled !== false);
    $('expProactiveInterval').value = e.proactive_interval_seconds ?? 300;
    $('expActiveStart').value = e.proactive_active_start_hour ?? 8;
    $('expActiveEnd').value = e.proactive_active_end_hour ?? 23;
    $('expDelayReply').value = String(e.delay_reply_enabled !== false);
    $('expPendingThreshold').value = e.pending_message_threshold ?? 4;
    $('expMinReplyInterval').value = e.min_reply_interval_seconds ?? 0;
    $('expReplyFreqWindow').value = e.reply_frequency_window_seconds ?? 60;
    $('expReplyFreqMax').value = e.reply_frequency_max_replies ?? 8;
    $('expExemptMention').value = String(e.reply_frequency_exempt_on_mention !== false);
    $('expMaxConcurrent').value = e.max_concurrent_llm_calls ?? 1;
    $('expEnableSkills').value = String(e.enable_skills !== false);
    $('expMaxSkillRounds').value = e.max_skill_rounds ?? 3;
    $('expSkillTimeout').value = e.skill_execution_timeout ?? 30;
    $('expAutoInstallDeps').value = String(e.auto_install_skill_deps !== false);
    $('expMemoryDepth').value = e.memory_depth || 'deep';
    $('expOtherAINames').value = (e.other_ai_names || []).join(', ');
  } catch (e) {}
}

async function saveExperience() {
  if (!currentPersona) { toast('请先选择人格', 'error'); return; }
  const res = await post(pApi('/experience'), {
    experience: {
      reply_mode: $('expReplyMode').value,
      engagement_sensitivity: parseFloat($('expSensitivity').value),
      heat_window_seconds: parseFloat($('expHeatWindow').value),
      proactive_enabled: $('expProactive').value === 'true',
      proactive_interval_seconds: parseFloat($('expProactiveInterval').value),
      proactive_active_start_hour: parseInt($('expActiveStart').value, 10),
      proactive_active_end_hour: parseInt($('expActiveEnd').value, 10),
      delay_reply_enabled: $('expDelayReply').value === 'true',
      pending_message_threshold: parseFloat($('expPendingThreshold').value),
      min_reply_interval_seconds: parseFloat($('expMinReplyInterval').value),
      reply_frequency_window_seconds: parseFloat($('expReplyFreqWindow').value),
      reply_frequency_max_replies: parseInt($('expReplyFreqMax').value, 10),
      reply_frequency_exempt_on_mention: $('expExemptMention').value === 'true',
      max_concurrent_llm_calls: parseInt($('expMaxConcurrent').value, 10),
      enable_skills: $('expEnableSkills').value === 'true',
      max_skill_rounds: parseInt($('expMaxSkillRounds').value, 10),
      skill_execution_timeout: parseFloat($('expSkillTimeout').value),
      auto_install_skill_deps: $('expAutoInstallDeps').value === 'true',
      memory_depth: $('expMemoryDepth').value,
      other_ai_names: $('expOtherAINames').value.split(',').map(s => s.trim()).filter(Boolean),
    }
  });
  toast(res.success ? '体验参数已保存' : res.error || '保存失败', res.success ? 'success' : 'error');
  if (res.success) flashSuccess(document.activeElement);
}

