const API = '/api';
let personas = [];
let currentPersona = null;
let personaState = {};
let providerDraft = [];
let currentPage = 'dashboard';

// Adapter 白名单的独立状态（避免被 personaState 覆盖丢失）
let adapterGroupIds = [];
let adapterPrivateIds = [];

function $(id) { return document.getElementById(id); }

/* ── Animation helpers ──────────────────────────────── */

function animateNumber(el, target, duration = 600) {
  if (!el) return;
  const start = parseInt(el.textContent.replace(/,/g, '') || '0', 10) || 0;
  if (start === target) return;
  const startTime = performance.now();
  function tick(now) {
    const elapsed = now - startTime;
    const progress = Math.min(elapsed / duration, 1);
    const eased = 1 - Math.pow(1 - progress, 3);
    const current = Math.round(start + (target - start) * eased);
    el.textContent = String(current);
    if (progress < 1) requestAnimationFrame(tick);
  }
  requestAnimationFrame(tick);
}

function flashSuccess(btn) {
  if (!btn) return;
  const prev = btn.textContent;
  btn.classList.add('btn-success-flash');
  btn.textContent = '✓ ' + prev;
  btn.disabled = true;
  setTimeout(() => {
    btn.classList.remove('btn-success-flash');
    btn.textContent = prev;
    btn.disabled = false;
  }, 1200);
}

function applyStagger(containerSelector, childSelector) {
  const container = typeof containerSelector === 'string'
    ? document.querySelector(containerSelector)
    : containerSelector;
  if (!container) return;
  container.classList.add('animate-stagger');
  const children = childSelector
    ? container.querySelectorAll(childSelector)
    : container.children;
  Array.from(children).forEach((child, i) => {
    child.style.setProperty('--i', String(i));
  });
}

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
  'global-settings': ['全局设置', 'Configuration / Global'],
  providers: ['Provider 配置', 'Configuration / Providers'],
  persona: ['人格配置', 'Configuration / Persona'],
  'create-persona': ['新建人格', 'Configuration / Create Persona'],
  orchestration: ['模型编排', 'Configuration / Orchestration'],
  experience: ['体验参数', 'Configuration / Experience'],
  adapters: ['Adapter 配置', 'Configuration / Adapters'],
  napcat: ['NapCat 管理', 'Platform / NapCat'],
};

async function navTo(page) {
  currentPage = page;
  document.querySelectorAll('.nav-item').forEach((el) => el.classList.remove('active'));
  document.querySelector(`.nav-item[data-page="${page}"]`)?.classList.add('active');
  const t = pageTitles[page];
  $('pageTitle').textContent = t?.[0] ?? '';
  $('pageBreadcrumb').textContent = t?.[1] ?? '';

  const container = $('mainContainer');
  try {
    const res = await fetch(`/static/pages/${page}.html`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    container.innerHTML = await res.text();
  } catch (e) {
    container.innerHTML = `<div class="card"><h2>加载失败</h2><p>无法加载页面：${page}</p><pre style="color:var(--text-2);font-size:12px">${e.message}</pre></div>`;
    console.error('navTo error:', e);
  }

  // 页面切换入场动画
  container.classList.remove('animate-fade-in');
  void container.offsetWidth; // force reflow
  container.classList.add('animate-fade-in');

  // 页面加载后重新填充人格下拉框
  renderPersonaSelect();

  // 如果尚未选中有数据的人格，默认选中第一个
  if (!currentPersona && personas.length > 0) {
    selectPersona(personas[0].name);
  }

  if (page === 'dashboard') renderPersonaCards();
  if (page === 'global-settings') loadGlobalSettings();
  if (page === 'persona') loadPersonaPreview();
  if (page === 'create-persona') { renderInterviewQuestions(); loadAvailableModels(); }
  if (page === 'orchestration') loadOrchestration();
  if (page === 'experience') loadExperience();
  if (page === 'adapters') loadAdapters();
  if (page === 'providers') _renderProviderDraft();
  if (page === 'napcat') { ncLoadStatus(); ncLoadLogs(); }
}

// ── Personas ──────────────────────────────────────────
async function loadPersonas() {
  try {
    const res = await get('/personas');
    personas = res.personas || [];
    renderPersonaSelect();
    renderPersonaCards(false);
    updateSidebar();
    if (!currentPersona && personas.length > 0) {
      selectPersona(personas[0].name);
    }
    // Load telemetry in parallel (don't block persona list)
    loadTelemetry();
  } catch (e) {
    console.error('loadPersonas', e);
    personas = [];
    renderPersonaSelect();
    updateSidebar();
  }
}

function renderPersonaSelect() {
  document.querySelectorAll('.persona-select-bar').forEach((el) => {
    if (!personas.length) {
      el.innerHTML = '<span style="color:var(--text-2);font-size:13px">暂无人格</span>';
      return;
    }
    el.innerHTML = personas.map((p) => {
      const selected = p.name === currentPersona;
      return `<div class="persona-chip ${p.running ? 'running' : ''} ${selected ? 'selected' : ''}" onclick="selectPersona('${p.name}')">`
        + `<div class="chip-status">${p.running ? '●' : '○'}</div>`
        + `<div class="chip-name">${p.persona_name || p.name}</div>`
        + `</div>`;
    }).join('');
  });
}

function selectPersona(name) {
  currentPersona = name;
  renderPersonaSelect();
  loadPersonaStatus();
}

async function loadPersonaStatus() {
  if (!currentPersona) return;
  try {
    personaState = await get(pApi(''));
    updateSidebar();
    if (currentPage === 'dashboard') renderPersonaCards();
    if (currentPage === 'global-settings') loadGlobalSettings();
    if (currentPage === 'persona') loadPersonaPreview();
    if (currentPage === 'orchestration') loadOrchestration();
    if (currentPage === 'experience') loadExperience();
    if (currentPage === 'adapters') loadAdapters();
  } catch (e) {
    console.error('loadPersonaStatus', e);
  }
}

function updateSidebar() {
  const running = personas.filter((p) => p.running).length;
  $('sbCurrentPersona').textContent = currentPersona ? (personaState.persona_name || currentPersona) : '—';
  animateNumber($('sbPersonaCount'), personas.length, 400);
  animateNumber($('sbRunningCount'), running, 400);
  const dot = $('sbRunningDot');
  if (dot) {
    dot.classList.toggle('ok', running > 0);
    dot.classList.toggle('pulse', running > 0);
  }
}

function formatHeartbeat(ts) {
  if (!ts) return '—';
  const d = new Date(ts);
  const now = new Date();
  const diff = (now - d) / 1000;
  if (diff < 5) return '刚刚';
  if (diff < 60) return `${Math.floor(diff)}秒前`;
  if (diff < 3600) return `${Math.floor(diff / 60)}分钟前`;
  return d.toLocaleString();
}

function renderPersonaCards(animate = true) {
  const el = $('personaCards');
  if (!el) return;
  if (!personas.length) {
    el.innerHTML = '<div style="color:var(--text-2);padding:20px">暂无人格。使用 CLI <code>python main.py persona create &lt;name&gt;</code> 创建。</div>';
    return;
  }

  // 非动画刷新尝试增量更新，避免 DOM 重建导致的跳动
  if (!animate) {
    const cards = el.querySelectorAll('.persona-card');
    let needRebuild = cards.length !== personas.length;
    if (!needRebuild) {
      for (let i = 0; i < personas.length; i++) {
        if (cards[i].dataset.name !== personas[i].name) { needRebuild = true; break; }
      }
    }
    if (!needRebuild) {
      cards.forEach((card, i) => {
        const p = personas[i];
        const isSelected = p.name === currentPersona;
        card.className = `persona-card ${p.running ? 'running' : ''} ${isSelected ? 'selected' : ''}`;
        card.querySelector('.p-status').textContent = p.running ? '● 运行中' : (p.status === 'stale' ? '○ 心跳超时' : '○ 已停止');
        const hbEl = card.querySelector('.p-status').nextElementSibling;
        if (hbEl) hbEl.textContent = '心跳: ' + formatHeartbeat(p.heartbeat_at);
        const actions = card.querySelector('.p-actions');
        if (actions) {
          actions.innerHTML = (p.running
            ? `<button class="btn danger" onclick="event.stopPropagation(); stopPersona('${p.name}')">⏹ 停止</button>`
            : `<button class="btn success" onclick="event.stopPropagation(); startPersona('${p.name}')">▶ 启动</button>`)
            + `<button class="btn" onclick="event.stopPropagation(); selectPersona('${p.name}'); navTo('persona')">⚙️ 配置</button>`;
        }
      });
      $('dashPersonaCount').textContent = String(personas.length);
      $('dashRunningCount').textContent = String(personas.filter((p) => p.running).length);
      $('dashStoppedCount').textContent = String(personas.filter((p) => !p.running).length);
      return;
    }
    // 结构变化需要重建，先移除动画类避免新子元素触发 stagger
    el.classList.remove('animate-stagger');
  }

  el.innerHTML = personas.map((p) => {
    const port = p.adapters?.[0]?.ws_url?.split(':').pop() || '—';
    const hb = formatHeartbeat(p.heartbeat_at);
    const isSelected = p.name === currentPersona;
    return `
    <div class="persona-card ${p.running ? 'running' : ''} ${isSelected ? 'selected' : ''}" data-name="${p.name}" onclick="selectPersona('${p.name}')">
      <div class="p-port">端口 ${port}</div>
      <div class="p-name">${p.persona_name || p.name}</div>
      <div class="p-meta">${p.persona_summary || p.name}</div>
      <div class="p-status">${p.running ? '● 运行中' : (p.status === 'stale' ? '○ 心跳超时' : '○ 已停止')}</div>
      <div style="font-size:11px;color:var(--text-2);margin-bottom:8px">心跳: ${hb}</div>
      <div class="p-actions">
        ${p.running
          ? `<button class="btn danger" onclick="event.stopPropagation(); stopPersona('${p.name}')">⏹ 停止</button>`
          : `<button class="btn success" onclick="event.stopPropagation(); startPersona('${p.name}')">▶ 启动</button>`}
        <button class="btn" onclick="event.stopPropagation(); selectPersona('${p.name}'); navTo('persona')">⚙️ 配置</button>
      </div>
    </div>
  `;
  }).join('');

  if (animate) applyStagger(el, '.persona-card');

  if (animate) {
    animateNumber($('dashPersonaCount'), personas.length, 500);
    animateNumber($('dashRunningCount'), personas.filter((p) => p.running).length, 500);
    animateNumber($('dashStoppedCount'), personas.filter((p) => !p.running).length, 500);
  } else {
    $('dashPersonaCount').textContent = String(personas.length);
    $('dashRunningCount').textContent = String(personas.filter((p) => p.running).length);
    $('dashStoppedCount').textContent = String(personas.filter((p) => !p.running).length);
  }

  // 更新选中人格详细信息
  const sp = personas.find((p) => p.name === currentPersona);
  const ds = $('dashSelectedInfo');
  if (sp) {
    ds.style.display = '';
    $('dsName').textContent = sp.persona_name || sp.name;
    $('dsSummary').textContent = sp.persona_summary || '暂无描述';
    $('dsTags').innerHTML = (sp.persona_summary || '').split(/[,，]/).filter(Boolean).slice(0,5).map((t) => `<span class="tag">${t.trim()}</span>`).join('');
    $('dsStatus').textContent = sp.running ? '运行中' : (sp.status === 'stale' ? '心跳超时' : '已停止');
    $('dsHeartbeat').textContent = formatHeartbeat(sp.heartbeat_at);
    $('dsPid').textContent = sp.pid || '—';
    $('dsAdapters').textContent = String(sp.adapters_count || 0);
    $('dsPort').textContent = sp.adapters?.[0]?.ws_url?.split(':').pop() || '—';
  } else {
    ds.style.display = 'none';
  }
}

async function loadTelemetry() {
  const container = $('dashSkillStats');
  const totalEl = $('dashSkillTotalCalls');
  if (!container) return;
  try {
    const res = await get('/telemetry');
    const skills = res.skills || {};
    const total = res.total_calls || 0;
    if (totalEl) totalEl.textContent = String(total);
    const names = Object.keys(skills).sort((a, b) => skills[b].calls - skills[a].calls);
    if (!names.length) {
      container.innerHTML = '<div style="color:var(--text-2);padding:12px">暂无 Skill 调用记录</div>';
      return;
    }
    container.innerHTML = names.map((name) => {
      const s = skills[name];
      const successRate = s.success_rate || 0;
      const color = successRate >= 95 ? 'var(--success)' : successRate >= 80 ? 'var(--warning)' : 'var(--danger)';
      return `
        <div class="stat-card">
          <div class="label">${name}</div>
          <div class="value">${s.calls}</div>
          <div style="font-size:11px;color:var(--text-2)">
            成功率 <span style="color:${color}">${successRate}%</span> &nbsp;|&nbsp; 平均 ${s.avg_ms || 0}ms
          </div>
        </div>
      `;
    }).join('');
    applyStagger(container, '.stat-card');
  } catch (e) {
    container.innerHTML = '<div style="color:var(--text-2);padding:12px">统计加载失败</div>';
  }
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

async function generatePersonaKeywords() {
  if (!currentPersona) { toast('请先选择人格', 'error'); return; }
  const btn = $('kwBtn');
  if (!btn) return;
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span> 生成中...';
  const res = await post(pApi('/persona/keywords'), {
    name: $('kwName').value,
    keywords: $('kwKeywords').value,
    aliases: $('kwAliases').value.split(/\s+/).filter(Boolean),
    model: $('kwModel').value,
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
}

async function loadAvailableModels() {
  try {
    const res = await get('/api/models');
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
    keywords: $('cpKeywords').value.trim() || undefined,
  });
  if (res.success) {
    toast('人格创建成功');
    await loadPersonas();
    selectPersona(name);
    $('cpName').value = '';
    $('cpPersonaName').value = '';
    $('cpKeywords').value = '';
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

function addGroup() {
  const v = $('newGroupId').value.trim();
  if (v) {
    adapterGroupIds = adapterGroupIds || [];
    if (!adapterGroupIds.includes(v)) adapterGroupIds.push(v);
    $('newGroupId').value = '';
    renderGroups(adapterGroupIds);
  }
}
function removeGroup(g) {
  adapterGroupIds = (adapterGroupIds || []).filter((x) => x !== g);
  renderGroups(adapterGroupIds);
}
function addPrivate() {
  const v = $('newPrivateId').value.trim();
  if (v) {
    adapterPrivateIds = adapterPrivateIds || [];
    if (!adapterPrivateIds.includes(v)) adapterPrivateIds.push(v);
    $('newPrivateId').value = '';
    renderPrivates(adapterPrivateIds);
  }
}
function removePrivate(u) {
  adapterPrivateIds = (adapterPrivateIds || []).filter((x) => x !== u);
  renderPrivates(adapterPrivateIds);
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
    }
  });
  toast(res.success ? '体验参数已保存' : res.error || '保存失败', res.success ? 'success' : 'error');
  if (res.success) flashSuccess(document.activeElement);
}

// ── Init ──────────────────────────────────────────────
(async function init() {
  await loadPersonas();
  await loadProviders();
  await loadGlobalSettings();
  await ncLoadStatus();
  await navTo('dashboard');
  setInterval(() => {
    loadPersonas();
    ncLoadLogs();
  }, 5000);
})();
