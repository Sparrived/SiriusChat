const API = '/api';
let state = {};
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
  if (page === 'dashboard') loadPersonaPreview();
}

// ── Status ────────────────────────────────────────────
async function loadStatus() {
  try {
    state = await get('/status');
    const ready = state.ready;
    const enabled = state.enabled;

    // Sidebar status
    $('sbEngineDot').className = 'status-dot ' + (ready ? 'ok' : 'err');
    $('sbEngineText').textContent = ready ? '就绪' : '未就绪';
    $('sbAiState').textContent = enabled ? '开启' : '关闭';
    $('sbProviderCount').textContent = (state.providers?.length || 0) + ' 个';
    $('sbPersonaName').textContent = state.persona_name || '—';

    // Header toggle
    $('toggleBtn').textContent = enabled ? '⏸ 关闭 AI' : '▶ 开启 AI';
    $('toggleBtn').className = 'btn' + (enabled ? ' danger' : '');

    // Dashboard stats
    $('dashEngine').textContent = ready ? '✅ 就绪' : '⏳ 未就绪';
    $('dashAi').textContent = enabled ? '✅ 开启' : '⏹ 关闭';
    $('dashProviderCount').textContent = state.providers?.length || 0;
    $('dashPersona').textContent = state.persona_name || '—';
    $('dashGroupCount').textContent = state.allowed_group_ids?.length || 0;
    $('dashAnalysisModel').textContent = state.orchestration?.analysis_model || '—';
    $('dashChatModel').textContent = state.orchestration?.chat_model || '—';

    // Form values
    renderProviders(state.providers || []);
    renderGroups(state.allowed_group_ids || []);
    renderPrivates(state.allowed_private_user_ids || []);
    $('enableGroupChat').checked = state.enable_group_chat;
    $('enablePrivateChat').checked = state.enable_private_chat;

    const orch = state.orchestration || {};
    $('orchAnalysis').value = orch.analysis_model || 'gpt-4o-mini';
    $('orchChat').value = orch.chat_model || 'gpt-4o';
    $('orchVision').value = orch.vision_model || 'gpt-4o';

    loadPersonaPreview();
  } catch (e) {
    console.error('loadStatus', e);
  }
}

// ── Providers ─────────────────────────────────────────
let providerDraft = [];
function renderProviders(list) {
  providerDraft = JSON.parse(JSON.stringify(list));
  _renderProviderDraft();
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
    el.innerHTML =
      '<div style="color:var(--text-2);padding:10px">暂无 Provider，请点击「添加 Provider」。</div>';
}
function addProvider() {
  providerDraft.push({ type: '', base_url: '', api_key: '', healthcheck_model: '', enabled: true, models: [] });
  _renderProviderDraft();
}

async function saveProviders() {
  const res = await post('/providers', { providers: providerDraft });
  toast(res.success ? 'Provider 已保存' : res.error || '保存失败', res.success ? 'success' : 'error');
  loadStatus();
}

// ── Groups ────────────────────────────────────────────
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
    state.allowed_group_ids.push(v);
    $('newGroupId').value = '';
    renderGroups(state.allowed_group_ids);
  }
}
function removeGroup(g) {
  state.allowed_group_ids = state.allowed_group_ids.filter((x) => x !== g);
  renderGroups(state.allowed_group_ids);
}
function addPrivate() {
  const v = $('newPrivateId').value.trim();
  if (v) {
    state.allowed_private_user_ids.push(v);
    $('newPrivateId').value = '';
    renderPrivates(state.allowed_private_user_ids);
  }
}
function removePrivate(u) {
  state.allowed_private_user_ids = state.allowed_private_user_ids.filter((x) => x !== u);
  renderPrivates(state.allowed_private_user_ids);
}

async function saveConfig() {
  const res = await post('/config', {
    allowed_group_ids: state.allowed_group_ids,
    allowed_private_user_ids: state.allowed_private_user_ids,
    enable_group_chat: $('enableGroupChat').checked,
    enable_private_chat: $('enablePrivateChat').checked,
  });
  toast(res.success ? '配置已保存' : res.error || '失败', res.success ? 'success' : 'error');
}

// ── Orchestration ─────────────────────────────────────
async function saveOrchestration() {
  const res = await post('/orchestration', {
    analysis_model: $('orchAnalysis').value,
    chat_model: $('orchChat').value,
    vision_model: $('orchVision').value,
  });
  toast(res.success ? '模型编排已保存' : res.error || '失败', res.success ? 'success' : 'error');
}

// ── Engine ────────────────────────────────────────────
async function toggleEngine() {
  const res = await post('/engine/toggle', { enabled: !state.enabled });
  toast(res.success ? (res.enabled ? 'AI 已开启' : 'AI 已关闭') : res.error || '失败', res.success ? 'success' : 'error');
  loadStatus();
}
async function reloadEngine() {
  const res = await post('/engine/reload', {});
  toast(res.success ? '引擎已重建' : res.error || '失败', res.success ? 'success' : 'error');
  loadStatus();
}

// ── Persona ───────────────────────────────────────────
async function generatePersonaKeywords() {
  const btn = document.querySelector('#page-persona .btn');
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span> 生成中...';
  const res = await post('/persona/keywords', {
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
  const btn = document.querySelectorAll('#page-persona .btn')[1];
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span> 生成中...';
  const answers = {};
  interviewQuestions.forEach((_, i) => {
    const v = $(`ivAns${i}`).value.trim();
    if (v) answers[String(i + 1)] = v;
  });
  const res = await post('/persona/interview', {
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

async function savePersona(jsonStr) {
  const res = await post('/persona/save', { persona: JSON.parse(jsonStr) });
  toast(res.success ? '人格已保存' : res.error || '保存失败', res.success ? 'success' : 'error');
  loadStatus();
}

async function loadPersonaPreview() {
  try {
    const res = await get('/persona');
    const text = res.persona ? JSON.stringify(res.persona, null, 2) : '尚未配置人格。';
    $('personaPreview').textContent = text;
    $('dashPersonaPreview').textContent = text;
  } catch (e) {}
}

// ── NapCat ────────────────────────────────────────────
async function ncLoadStatus() {
  try {
    const res = await get('/napcat/status');
    if (!res.enabled) {
      $('ncInstalled').textContent = '管理未启用';
      $('ncRunning').textContent = '管理未启用';
      $('ncQQ').textContent = '管理未启用';
      $('dashNapcat').textContent = '管理未启用';
      return;
    }
    const installed = res.installed ? '✅ 已安装' : '❌ 未安装';
    const running = res.running ? '✅ 运行中' : '⏹ 已停止';
    const qq = res.qq_installed ? '✅ 已安装' : '❌ 未检测到';
    $('ncInstalled').textContent = installed;
    $('ncRunning').textContent = running;
    $('ncQQ').textContent = qq + (res.qq_path ? ` (${res.qq_path})` : '');
    $('dashNapcat').textContent = `${installed} / ${running}`;
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
async function ncConfigure() {
  const qq = $('ncQQNumber').value.trim();
  if (!qq) {
    toast('请填写 QQ 号', 'error');
    return;
  }
  const res = await post('/napcat/configure', {
    qq,
    ws_port: parseInt($('ncWSPort').value) || 3001,
    ws_token: $('ncWSToken').value || 'napcat_ws',
  });
  toast(res.success ? res.message : res.message || '配置失败', res.success ? 'success' : 'error');
}
async function ncStart() {
  const qq = $('ncQQNumber').value.trim() || undefined;
  const btn = $('ncStartBtn');
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span> 启动中...';
  const res = await post('/napcat/start', qq ? { qq } : {});
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
loadStatus();
ncLoadStatus();
setInterval(() => {
  ncLoadLogs();
}, 5000);
