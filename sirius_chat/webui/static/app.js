const API = '/api';
let personas = [];
let currentPersona = null;
let personaState = {};
let providerDraft = [];
let currentPage = 'dashboard';

function $(id) { return document.getElementById(id); }
function toast(msg, type = 'success') {
  const t = $('toast');
  t.textContent = msg;
  t.className = 'toast ' + type + ' show';
  setTimeout(() => t.classList.remove('show'), 3000);
}

async function get(path) {
  const r = await fetch(API + path);
  return r.json();
}
async function post(path, body) {
  const r = await fetch(API + path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  return r.json();
}

function pApi(path) {
  return `/personas/${currentPersona}${path}`;
}

// ── Navigation ────────────────────────────────────────
const pageTitles = {
  dashboard: ['概览', 'Dashboard'],
  providers: ['Provider 配置', 'Configuration / Providers'],
  persona: ['人格配置', 'Configuration / Persona'],
  orchestration: ['模型编排', 'Configuration / Orchestration'],
  groups: ['群管理', 'Configuration / Groups'],
  napcat: ['NapCat 管理', 'Platform / NapCat'],
};

function navTo(page) {
  currentPage = page;
  document.querySelectorAll('.nav-item').forEach((el) => el.classList.remove('active'));
  document.querySelector(`.nav-item[data-page="${page}"]`)?.classList.add('active');
  document.querySelectorAll('.page').forEach((el) => el.classList.remove('active'));
  $(`page-${page}`).classList.add('active');
  const t = pageTitles[page];
  $('pageTitle').textContent = t[0];
  $('pageBreadcrumb').textContent = t[1];
  if (page === 'persona') loadPersonaPreview();
  if (page === 'orchestration') loadOrchestration();
  if (page === 'groups') loadGroups();
}

// ── Personas ──────────────────────────────────────────
async function loadPersonas() {
  try {
    const res = await get('/personas');
    personas = res.personas || [];
    renderPersonaSelect();
    renderPersonaCards();
    updateSidebar();
    if (!currentPersona && personas.length > 0) {
      selectPersona(personas[0].name);
    }
  } catch (e) {
    console.error('loadPersonas', e);
  }
}

function renderPersonaSelect() {
  const opts = personas.map((p) => `<option value="${p.name}">${p.persona_name || p.name}</option>`).join('');
  document.querySelectorAll('.persona-select').forEach((el) => {
    el.innerHTML = opts || '<option>无人格</option>';
    if (currentPersona) el.value = currentPersona;
  });
}

function selectPersona(name) {
  currentPersona = name;
  document.querySelectorAll('.persona-select').forEach((el) => {
    if (el.querySelector(`option[value="${name}"]`)) el.value = name;
  });
  loadPersonaStatus();
}

async function loadPersonaStatus() {
  if (!currentPersona) return;
  try {
    personaState = await get(pApi(''));
    updateSidebar();
    if (currentPage === 'dashboard') renderPersonaCards();
    if (currentPage === 'persona') loadPersonaPreview();
    if (currentPage === 'orchestration') loadOrchestration();
    if (currentPage === 'groups') loadGroups();
  } catch (e) {
    console.error('loadPersonaStatus', e);
  }
}

function updateSidebar() {
  const running = personas.filter((p) => p.running).length;
  $('sbCurrentPersona').textContent = currentPersona ? (personaState.persona_name || currentPersona) : '—';
  $('sbPersonaCount').textContent = String(personas.length);
  $('sbRunningCount').textContent = String(running);
}

function renderPersonaCards() {
  const el = $('personaCards');
  if (!personas.length) {
    el.innerHTML = '<div style="color:var(--text-2);padding:20px">暂无人格。使用 CLI <code>python main.py persona create &lt;name&gt;</code> 创建。</div>';
    return;
  }
  el.innerHTML = personas.map((p) => {
    const port = p.adapters?.[0]?.ws_url?.split(':').pop() || '—';
    return `
    <div class="persona-card ${p.running ? 'running' : ''}">
      <div class="p-port">端口 ${port}</div>
      <div class="p-name">${p.persona_name || p.name}</div>
      <div class="p-meta">${p.name}</div>
      <div class="p-status">${p.running ? '● 运行中' : '○ 已停止'}</div>
      <div class="p-actions">
        ${p.running
          ? `<button class="btn danger" onclick="stopPersona('${p.name}')">⏹ 停止</button>`
          : `<button class="btn success" onclick="startPersona('${p.name}')">▶ 启动</button>`}
        <button class="btn" onclick="selectPersona('${p.name}'); navTo('persona')">⚙️ 配置</button>
      </div>
    </div>
  `;
  }).join('');

  $('dashPersonaCount').textContent = String(personas.length);
  $('dashRunningCount').textContent = String(personas.filter((p) => p.running).length);
  $('dashNapcatCount').textContent = String(personas.filter((p) => p.adapters_count > 0).length);
}

async function startPersona(name) {
  const res = await post(`/personas/${name}/start`, {});
  toast(res.success ? `人格 ${name} 已启动` : res.error || '启动失败', res.success ? 'success' : 'error');
  loadPersonas();
}

async function stopPersona(name) {
  const res = await post(`/personas/${name}/stop`, {});
  toast(res.success ? `人格 ${name} 已停止` : res.error || '停止失败', res.success ? 'success' : 'error');
  loadPersonas();
}

// ── Providers ─────────────────────────────────────────
async function loadProviders() {
  try {
    const res = await get('/providers');
    providerDraft = JSON.parse(JSON.stringify(res.providers || []));
    _renderProviderDraft();
    $('dashProviderCount').textContent = String(providerDraft.length);
  } catch (e) {}
}

function _renderProviderDraft() {
  const el = $('providerList');
  el.innerHTML = providerDraft
    .map(
      (p, i) => `
    <div class="provider-row">
      <input placeholder="平台" value="${p.type || ''}" oninput="providerDraft[${i}].type=this.value">
      <input placeholder="Base URL" value="${p.base_url || ''}" oninput="providerDraft[${i}].base_url=this.value">
      <input type="password" placeholder="API Key" value="${p.api_key || ''}" oninput="providerDraft[${i}].api_key=this.value">
      <input placeholder="Model" value="${p.healthcheck_model || ''}" oninput="providerDraft[${i}].healthcheck_model=this.value">
      <button class="btn small" onclick="providerDraft[${i}].enabled=!providerDraft[${i}].enabled;_renderProviderDraft()">${providerDraft[i].enabled !== false ? '启用' : '禁用'}</button>
      <button class="btn small danger" onclick="providerDraft.splice(${i},1);_renderProviderDraft()">✕</button>
    </div>
  `
    )
    .join('');
  if (!providerDraft.length)
    el.innerHTML = '<div style="color:var(--text-2);padding:10px">暂无 Provider，请点击「添加 Provider」。</div>';
}
function addProvider() {
  providerDraft.push({ type: '', base_url: '', api_key: '', healthcheck_model: '', enabled: true, models: [] });
  _renderProviderDraft();
}
async function saveProviders() {
  const res = await post('/providers', { providers: providerDraft });
  toast(res.success ? 'Provider 已保存' : res.error || '保存失败', res.success ? 'success' : 'error');
  loadProviders();
}

// ── Persona ───────────────────────────────────────────
async function loadPersonaPreview() {
  if (!currentPersona) return;
  try {
    const res = await get(pApi('/persona'));
    const text = res.persona ? JSON.stringify(res.persona, null, 2) : '尚未配置人格。';
    $('personaPreview').textContent = text;
  } catch (e) {}
}

async function savePersona(jsonStr) {
  if (!currentPersona) return;
  const res = await post(pApi('/persona/save'), { persona: JSON.parse(jsonStr) });
  toast(res.success ? '人格已保存' : res.error || '保存失败', res.success ? 'success' : 'error');
  loadPersonaStatus();
}

async function generatePersonaKeywords() {
  if (!currentPersona) { toast('请先选择人格', 'error'); return; }
  const btn = document.querySelector('#page-persona .btn');
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span> 生成中...';
  const res = await post(pApi('/persona/keywords'), {
    name: $('kwName').value,
    keywords: $('kwKeywords').value,
    aliases: $('kwAliases').value.split(/\s+/).filter(Boolean),
  });
  btn.disabled = false;
  btn.innerHTML = '✨ 生成人格';
  if (res.success) {
    $('kwResult').innerHTML = `<div class="preview-box">${JSON.stringify(res.persona, null, 2)}</div><button class="btn success" onclick="savePersona(${JSON.stringify(JSON.stringify(res.persona))})">💾 保存人格</button>`;
  } else {
    toast(res.error || '生成失败', 'error');
  }
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
function renderInterviewQuestions() {
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
}

async function generatePersonaInterview() {
  if (!currentPersona) { toast('请先选择人格', 'error'); return; }
  const btn = document.querySelectorAll('#page-persona .btn')[1];
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
function _fillSelect(id, value, models) {
  const el = $(id);
  el.innerHTML = '';
  const opts = models.length ? models : [value || 'gpt-4o'];
  opts.forEach((m) => {
    const opt = document.createElement('option');
    opt.value = m;
    opt.textContent = m;
    if (m === value) opt.selected = true;
    el.appendChild(opt);
  });
}

async function loadOrchestration() {
  if (!currentPersona) return;
  try {
    const res = await get(pApi('/orchestration'));
    const orch = res || {};
    const models = orch.available_models || [];
    _fillSelect('orchAnalysis', orch.analysis_model || 'gpt-4o-mini', models);
    _fillSelect('orchChat', orch.chat_model || 'gpt-4o', models);
    _fillSelect('orchVision', orch.vision_model || 'gpt-4o', models);
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
}

// ── Groups ────────────────────────────────────────────
async function loadGroups() {
  if (!currentPersona) return;
  try {
    const adapters = personaState.adapters || [];
    const a = adapters[0] || {};
    renderGroups(a.allowed_group_ids || []);
    renderPrivates(a.allowed_private_user_ids || []);
    $('enableGroupChat').checked = a.enable_group_chat !== false;
    $('enablePrivateChat').checked = a.enable_private_chat !== false;
  } catch (e) {}
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

function addGroup() {
  const v = $('newGroupId').value.trim();
  if (v) {
    const adapters = personaState.adapters || [];
    const a = adapters[0] || {};
    a.allowed_group_ids = a.allowed_group_ids || [];
    a.allowed_group_ids.push(v);
    $('newGroupId').value = '';
    renderGroups(a.allowed_group_ids);
  }
}
function removeGroup(g) {
  const adapters = personaState.adapters || [];
  const a = adapters[0] || {};
  a.allowed_group_ids = (a.allowed_group_ids || []).filter((x) => x !== g);
  renderGroups(a.allowed_group_ids);
}
function addPrivate() {
  const v = $('newPrivateId').value.trim();
  if (v) {
    const adapters = personaState.adapters || [];
    const a = adapters[0] || {};
    a.allowed_private_user_ids = a.allowed_private_user_ids || [];
    a.allowed_private_user_ids.push(v);
    $('newPrivateId').value = '';
    renderPrivates(a.allowed_private_user_ids);
  }
}
function removePrivate(u) {
  const adapters = personaState.adapters || [];
  const a = adapters[0] || {};
  a.allowed_private_user_ids = (a.allowed_private_user_ids || []).filter((x) => x !== u);
  renderPrivates(a.allowed_private_user_ids);
}

async function saveConfig() {
  if (!currentPersona) { toast('请先选择人格', 'error'); return; }
  const adapters = personaState.adapters || [];
  const a = adapters[0] || {};
  a.allowed_group_ids = a.allowed_group_ids || [];
  a.allowed_private_user_ids = a.allowed_private_user_ids || [];
  a.enable_group_chat = $('enableGroupChat').checked;
  a.enable_private_chat = $('enablePrivateChat').checked;
  const res = await post(pApi('/adapters'), { adapters });
  toast(res.success ? '配置已保存' : res.error || '失败', res.success ? 'success' : 'error');
  loadPersonaStatus();
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
    if (!res.enabled) {
      $('ncInstalled').textContent = '管理未启用';
      $('ncRunning').textContent = '管理未启用';
      $('ncQQ').textContent = '管理未启用';
      return;
    }
    const installed = res.installed ? '✅ 已安装' : '❌ 未安装';
    const running = res.running ? '✅ 运行中' : '⏹ 已停止';
    const qq = res.qq_installed ? '✅ 已安装' : '❌ 未检测到';
    $('ncInstalled').textContent = installed;
    $('ncRunning').textContent = running;
    $('ncQQ').textContent = qq + (res.qq_path ? ` (${res.qq_path})` : '');
    $('ncInstallBtn').style.display = res.installed ? 'none' : 'inline-flex';
    $('ncStartBtn').style.display = res.installed ? 'inline-flex' : 'none';
    $('ncStopBtn').style.display = res.running ? 'inline-flex' : 'none';
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

// ── Init ──────────────────────────────────────────────
renderInterviewQuestions();
loadPersonas();
loadProviders();
ncLoadStatus();
setInterval(() => {
  loadPersonas();
  ncLoadLogs();
}, 5000);
