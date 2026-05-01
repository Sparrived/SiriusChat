const API = '/api';
let personas = [];
let currentPersona = null;
let personaState = {};
let providerDraft = [];
let currentPage = 'dashboard';

// Adapter 白名单的独立状态（避免被 personaState 覆盖丢失）
let adapterGroupIds = [];
let adapterPrivateIds = [];

// 缓存上次统计数据，避免无变化时重复重建 DOM 触发跳动
let _lastTelemetryData = null;
let _lastTokenData = null;

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
  'token-tracker': ['Token 追踪', 'Analytics / Token Tracker'],
  'cognition': ['认知分析', 'Analytics / Cognition'],
  'diary': ['日记', 'Analytics / Diary'],
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

  if (page === 'dashboard') { renderPersonaCards(); loadProviders(); _lastTelemetryData = null; _lastTokenData = null; loadTelemetry(); loadTokenStats(); }
  if (page === 'global-settings') loadGlobalSettings();
  if (page === 'persona') loadPersonaPreview();
  if (page === 'create-persona') { renderInterviewQuestions(); loadAvailableModels(); }
  if (page === 'orchestration') loadOrchestration();
  if (page === 'experience') loadExperience();
  if (page === 'adapters') loadAdapters();
  if (page === 'providers') _renderProviderDraft();
  if (page === 'napcat') { ncLoadStatus(); ncLoadLogs(); }
  if (page === 'token-tracker') loadTokenTracker();
  if (page === 'cognition') loadCognition();
  if (page === 'diary') diaryLoadData();
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
    // Load telemetry and token stats in parallel (don't block persona list)
    loadTelemetry();
    loadTokenStats();
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
    if (currentPage === 'token-tracker') ttLoadData();
    if (currentPage === 'cognition') loadCognition();
    if (currentPage === 'diary') diaryLoadData();
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
        <button class="btn" onclick="event.stopPropagation(); selectPersona('${p.name}'); navTo('token-tracker')">📈 Token</button>
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

function renderSectionBars(container, breakdown, breakdownByTask) {
  if (!container) return;
  const rawEntries = Object.entries(breakdown)
    .filter(([k]) => k !== 'total');
  if (!rawEntries.length || typeof echarts === 'undefined') {
    container.innerHTML = '<div style="color:var(--text-2);padding:12px">暂无模块分布数据</div>';
    return;
  }

  const labels = {
    persona: '人格设定', identity: '身份识别', output_constraint: '输出约束',
    emotion: '情感上下文', empathy: '共情策略', relationship: '关系上下文',
    memory: '记忆引用', interests: '用户兴趣', group_style: '群体风格',
    participants: '近期参与者', cross_group: '跨群认知', skills: '可用技能',
    glossary: '名词解释', output_format: '输出格式', diary: '日记记忆',
    history_xml: '对话历史', cross_group_xml: '跨群历史',
    system_prompt_total: '系统指令', user_message: '用户消息',
  };

  const groups = [
    { name: '人格与身份', keys: ['persona', 'identity'], color: '#58a6ff' },
    { name: '情感与关系', keys: ['emotion', 'empathy', 'relationship'], color: '#3fb950' },
    { name: '记忆与历史', keys: ['memory', 'diary', 'history_xml', 'cross_group_xml'], color: '#d29922' },
    { name: '环境与风格', keys: ['group_style', 'participants', 'cross_group', 'interests'], color: '#f85149' },
    { name: '功能与格式', keys: ['skills', 'glossary', 'output_format', 'output_constraint'], color: '#a371f7' },
    { name: '输入组成', keys: ['system_prompt_total', 'user_message'], color: '#e3b341' },
  ];

  const taskLabels = {
    response_generate: '主模型调用',
    cognition_analyze: '认知分析',
    diary_generate: '日记生成',
    diary_consolidate: '日记合并',
    proactive_generate: '主动生成',
    persona_generate: '人格生成',
  };
  const taskColors = ['#58a6ff', '#3fb950', '#d29922', '#f85149', '#a371f7', '#e3b341'];

  const nodes = [{ name: '总输入', itemStyle: { color: '#ffffff' } }];
  const links = [];

  const hasTaskBreakdown = breakdownByTask && Object.keys(breakdownByTask).length > 1;

  if (hasTaskBreakdown) {
    // 4-level sankey: 总输入 → 任务 → 大类 → 子模块
    // 大类和子模块节点跨任务合并（不带后缀），任务层保留区分
    const taskNames = Object.keys(breakdownByTask);
    taskNames.forEach((taskName, ti) => {
      const taskLabel = taskLabels[taskName] || taskName;
      const taskColor = taskColors[ti % taskColors.length];
      const taskBreakdown = breakdownByTask[taskName];
      let taskSum = 0;

      groups.forEach((g) => {
        let groupSum = 0;
        g.keys.forEach((key) => {
          const val = taskBreakdown[key] || 0;
          if (val) {
            const label = labels[key] || key;
            // 子模块节点：跨任务合并（不带后缀）
            nodes.push({ name: label, itemStyle: { color: g.color } });
            links.push({ source: g.name, target: label, value: val });
            groupSum += val;
          }
        });
        if (groupSum) {
          // 大类节点：跨任务合并（不带后缀）
          nodes.push({ name: g.name, itemStyle: { color: g.color } });
          links.push({ source: taskLabel, target: g.name, value: groupSum });
          taskSum += groupSum;
        }
      });

      if (taskSum) {
        nodes.push({ name: taskLabel, itemStyle: { color: taskColor } });
        links.push({ source: '总输入', target: taskLabel, value: taskSum });
      }
    });
  } else {
    // 3-level sankey fallback (aggregate view)
    groups.forEach((g) => {
      let groupSum = 0;
      g.keys.forEach((key) => {
        const val = breakdown[key] || 0;
        if (val) {
          const label = labels[key] || key;
          nodes.push({ name: label, itemStyle: { color: g.color } });
          links.push({ source: g.name, target: label, value: val });
          groupSum += val;
        }
      });
      if (groupSum) {
        nodes.push({ name: g.name, itemStyle: { color: g.color } });
        links.push({ source: '总输入', target: g.name, value: groupSum });
      }
    });
  }

  if (!links.length) {
    container.innerHTML = '<div style="color:var(--text-2);padding:12px">暂无模块分布数据</div>';
    return;
  }

  // 去重 nodes：同名节点只保留一个（ECharts 按 name 聚合）
  const nodeMap = new Map();
  nodes.forEach((n) => { if (!nodeMap.has(n.name)) nodeMap.set(n.name, n); });
  const uniqueNodes = Array.from(nodeMap.values());

  // Sankey 数据结构变化大，每次重建实例避免增量更新内部状态错乱
  let chart = echarts.getInstanceByDom(container);
  if (chart) {
    chart.dispose();
    window.removeEventListener('resize', container._sankeyResize);
  }
  chart = echarts.init(container, 'dark');
  const onResize = () => chart.resize();
  window.addEventListener('resize', onResize);
  container._sankeyResize = onResize;

  chart.setOption({
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'item',
      formatter: (params) => {
        if (params.dataType === 'edge') {
          return `${params.data.source} → ${params.data.target}<br/><b>${params.data.value.toLocaleString()} tokens</b>`;
        }
        return `<b>${params.name}</b>`;
      },
    },
    series: [{
      type: 'sankey',
      layout: 'none',
      emphasis: { focus: 'adjacency' },
      data: uniqueNodes,
      links: links,
      top: 10, bottom: 10, left: 10, right: hasTaskBreakdown ? 140 : 110,
      nodeWidth: hasTaskBreakdown ? 22 : 28,
      nodeGap: 10,
      layoutIterations: 32,
      lineStyle: { color: 'gradient', curveness: 0.5, opacity: 0.55 },
      label: {
        color: '#e8eaf0',
        fontSize: 11,
        formatter: (p) => p.name,
      },
      itemStyle: { borderWidth: 1, borderColor: '#0d1117' },
    }],
  });
}

function renderTimeSeries(container, hourly) {
  if (!container) return;
  if (!hourly.length || typeof echarts === 'undefined') {
    container.innerHTML = '<div style="color:var(--text-2);padding:12px">暂无趋势数据</div>';
    return;
  }
  let chart = echarts.getInstanceByDom(container);
  if (!chart) {
    chart = echarts.init(container, 'dark');
    const onResize = () => chart.resize();
    window.removeEventListener('resize', container._tsResize);
    window.addEventListener('resize', onResize);
    container._tsResize = onResize;
  }

  const dates = hourly.map((h) => new Date(h.hour_ts * 1000).toLocaleString('zh-CN', { month: 'short', day: 'numeric', hour: '2-digit' }));
  const promptData = hourly.map((h) => h.prompt_tokens || 0);
  const completionData = hourly.map((h) => h.completion_tokens || 0);

  chart.setOption({
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'cross', label: { backgroundColor: '#6a7985' } },
    },
    legend: { data: ['Prompt', 'Completion'], textStyle: { color: '#c9d1d9', fontSize: 11 }, top: 0 },
    grid: { left: 10, right: 10, bottom: 10, top: 32, containLabel: true },
    xAxis: {
      type: 'category',
      boundaryGap: false,
      data: dates,
      axisLabel: { fontSize: 10, color: '#8b949e', rotate: 30 },
      axisLine: { lineStyle: { color: '#30363d' } },
    },
    yAxis: {
      type: 'value',
      axisLabel: { fontSize: 10, color: '#8b949e' },
      splitLine: { lineStyle: { color: '#21262d' } },
    },
    series: [
      {
        name: 'Prompt',
        type: 'line',
        smooth: true,
        showSymbol: false,
        areaStyle: { opacity: 0.15 },
        lineStyle: { width: 2 },
        itemStyle: { color: '#58a6ff' },
        data: promptData,
      },
      {
        name: 'Completion',
        type: 'line',
        smooth: true,
        showSymbol: false,
        areaStyle: { opacity: 0.15 },
        lineStyle: { width: 2 },
        itemStyle: { color: '#3fb950' },
        data: completionData,
      },
    ],
  }, true);
}

function renderEmotionDistribution(container, distribution) {
  if (!container) return;
  if (!Object.keys(distribution).length || typeof echarts === 'undefined') {
    container.innerHTML = '<div style="color:var(--text-2);padding:12px">暂无情感分布数据</div>';
    return;
  }
  let chart = echarts.getInstanceByDom(container);
  if (!chart) {
    chart = echarts.init(container, 'dark');
    const onResize = () => chart.resize();
    window.removeEventListener('resize', container._edResize);
    window.addEventListener('resize', onResize);
    container._edResize = onResize;
  }

  const data = Object.entries(distribution).map(([name, value]) => ({ name: name || '未知', value }));
  const colors = ['#58a6ff', '#3fb950', '#d29922', '#f85149', '#a371f7', '#e3b341', '#8b949e'];

  chart.setOption({
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'item',
      formatter: (p) => `<b>${p.name}</b><br/>${p.value} 次 (${p.percent}%)`,
    },
    series: [{
      type: 'pie',
      radius: ['30%', '60%'],
      center: ['40%', '50%'],
      avoidLabelOverlap: true,
      itemStyle: { borderRadius: 6, borderColor: '#0d1117', borderWidth: 2 },
      label: { show: false },
      emphasis: {
        label: { show: true, fontSize: 13, fontWeight: 'bold', color: '#e8eaf0' },
      },
      data: data.map((d, i) => ({ ...d, itemStyle: { color: colors[i % colors.length] } })),
    }],
  }, true);
}

function renderEmotionTimeline(container, events) {
  if (!container) return;
  if (!events.length || typeof echarts === 'undefined') {
    container.innerHTML = '<div style="color:var(--text-2);padding:12px">暂无情感时间线数据</div>';
    return;
  }
  let chart = echarts.getInstanceByDom(container);
  if (!chart) {
    chart = echarts.init(container, 'dark');
    const onResize = () => chart.resize();
    window.removeEventListener('resize', container._etResize);
    window.addEventListener('resize', onResize);
    container._etResize = onResize;
  }

  const sorted = [...events].sort((a, b) => (a.timestamp || 0) - (b.timestamp || 0));
  const dates = sorted.map((e) => new Date((e.timestamp || 0) * 1000).toLocaleString('zh-CN', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }));
  const valenceData = sorted.map((e) => e.valence || 0);
  const arousalData = sorted.map((e) => e.arousal || 0);
  const intensityData = sorted.map((e) => e.intensity || 0);

  chart.setOption({
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'cross', label: { backgroundColor: '#6a7985' } },
    },
    legend: { data: ['Valence', 'Arousal', 'Intensity'], textStyle: { color: '#c9d1d9', fontSize: 11 }, top: 0 },
    grid: { left: 10, right: 10, bottom: 10, top: 32, containLabel: true },
    xAxis: {
      type: 'category',
      data: dates,
      axisLabel: { fontSize: 10, color: '#8b949e', rotate: 30 },
      axisLine: { lineStyle: { color: '#30363d' } },
    },
    yAxis: {
      type: 'value',
      min: -1, max: 1,
      axisLabel: { fontSize: 10, color: '#8b949e' },
      splitLine: { lineStyle: { color: '#21262d' } },
    },
    series: [
      { name: 'Valence', type: 'line', smooth: true, showSymbol: false, lineStyle: { width: 2 }, itemStyle: { color: '#58a6ff' }, data: valenceData },
      { name: 'Arousal', type: 'line', smooth: true, showSymbol: false, lineStyle: { width: 2 }, itemStyle: { color: '#f85149' }, data: arousalData },
      { name: 'Intensity', type: 'line', smooth: true, showSymbol: false, lineStyle: { width: 2, type: 'dashed' }, itemStyle: { color: '#e3b341' }, data: intensityData },
    ],
  }, true);
}

function renderActiveHours(container, distribution) {
  if (!container) return;
  if (!distribution.length || typeof echarts === 'undefined') {
    container.innerHTML = '<div style="color:var(--text-2);padding:12px">暂无活跃时段数据</div>';
    return;
  }
  let chart = echarts.getInstanceByDom(container);
  if (!chart) {
    chart = echarts.init(container, 'dark');
    const onResize = () => chart.resize();
    window.removeEventListener('resize', container._ahResize);
    window.addEventListener('resize', onResize);
    container._ahResize = onResize;
  }

  const hours = Array.from({ length: 24 }, (_, i) => `${i}时`);
  const callsMap = Object.fromEntries(distribution.map((d) => [d.hour, d.calls || 0]));
  const data = hours.map((_, i) => callsMap[i] || 0);

  chart.setOption({
    backgroundColor: 'transparent',
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    grid: { left: 10, right: 10, bottom: 10, top: 10, containLabel: true },
    xAxis: {
      type: 'category',
      data: hours,
      axisLabel: { fontSize: 10, color: '#8b949e', interval: 2 },
      axisLine: { lineStyle: { color: '#30363d' } },
    },
    yAxis: {
      type: 'value',
      axisLabel: { fontSize: 10, color: '#8b949e' },
      splitLine: { lineStyle: { color: '#21262d' } },
    },
    series: [{
      type: 'bar',
      data,
      barWidth: '60%',
      itemStyle: {
        borderRadius: [3, 3, 0, 0],
        color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
          { offset: 0, color: '#58a6ff' },
          { offset: 1, color: '#1f6feb' },
        ]),
      },
    }],
  }, true);
}

function _renderExtraStats(prefix, res) {
  const ratioEl = $(`${prefix}Ratio`);
  const effEl = $(`${prefix}Efficiency`);
  const retryEl = $(`${prefix}RetryRate`);
  const durEl = $(`${prefix}AvgDuration`);
  const outRatioEl = $(`${prefix}OutputRatio`);
  const emptyEl = $(`${prefix}EmptyRate`);
  const emptyTabEl = $(`${prefix}EmptyRateTab`);
  const bloatEl = $(`${prefix}BloatAlert`);
  const failEl = $(`${prefix}FailureRate`);
  const depthEl = $(`${prefix}AvgDepth`);
  const maxDepthEl = $(`${prefix}MaxDepth`);

  const ratio = res.ratio || {};
  if (ratioEl) ratioEl.textContent = `${ratio.prompt_pct || 0}% / ${ratio.completion_pct || 0}%`;

  const eff = res.efficiency_stats || {};
  if (effEl) effEl.textContent = eff.chars_per_token ? `${eff.chars_per_token} 字/Token` : '—';

  const retry = res.retry_stats || {};
  if (retryEl) retryEl.textContent = retry.retry_rate_pct ? `${retry.retry_rate_pct}%` : '—';

  const dur = res.duration_stats || {};
  const overall = dur.overall || {};
  if (durEl) durEl.textContent = overall.avg_ms ? `${overall.avg_ms} ms` : '—';

  if (outRatioEl) outRatioEl.textContent = eff.output_ratio ? `${eff.output_ratio}` : '—';

  const empty = res.empty_reply_stats || {};
  const emptyText = empty.empty_rate_pct ? `${empty.empty_rate_pct}%` : '—';
  if (emptyEl) emptyEl.textContent = emptyText;
  if (emptyTabEl) emptyTabEl.textContent = emptyText;

  const fail = res.failure_stats || {};
  if (failEl) {
    const fr = fail.failure_rate_pct || 0;
    failEl.textContent = fr ? `${fr}%` : '—';
    failEl.style.color = fr > 5 ? 'var(--danger)' : (fr > 1 ? 'var(--warning)' : '');
  }

  const depth = res.depth_stats || {};
  if (depthEl) depthEl.textContent = depth.avg_depth ? `${depth.avg_depth}` : '—';
  if (maxDepthEl) maxDepthEl.textContent = depth.max_depth ? `最大 ${depth.max_depth}` : '—';

  const comp = res.period_comparison || {};
  if (bloatEl) {
    const chg = comp.change_total_tokens || 0;
    const calls = comp.current?.total_calls || 0;
    if (!calls) {
      bloatEl.textContent = '—';
      bloatEl.style.color = '';
    } else if (chg > 20) {
      bloatEl.textContent = `↑ ${chg}%`;
      bloatEl.style.color = 'var(--danger)';
    } else if (chg < -20) {
      bloatEl.textContent = `↓ ${Math.abs(chg)}%`;
      bloatEl.style.color = 'var(--success)';
    } else {
      bloatEl.textContent = `${chg > 0 ? '+' : ''}${chg}%`;
      bloatEl.style.color = 'var(--text-2)';
    }
  }
}

async function loadTokenStats() {
  const callsEl = $('dashTokenCalls');
  const promptEl = $('dashTokenPrompt');
  const completionEl = $('dashTokenCompletion');
  const totalEl = $('dashTokenTotal');
  const avgEl = $('dashTokenAvgRound');
  const avgDetailEl = $('dashTokenAvgRoundDetail');
  if (!callsEl || !totalEl) return;
  try {
    const res = await get('/tokens');
    const dataKey = JSON.stringify(res);
    if (_lastTokenData === dataKey) return;
    _lastTokenData = dataKey;

    const summary = res.summary || {};
    animateNumber(callsEl, summary.total_calls || 0, 500);
    animateNumber(promptEl, summary.total_prompt_tokens || 0, 500);
    animateNumber(completionEl, summary.total_completion_tokens || 0, 500);
    animateNumber(totalEl, summary.total_tokens || 0, 500);
    const avg = res.response_avg || {};
    if (avgEl) animateNumber(avgEl, avg.avg_total_tokens || 0, 500);
    if (avgDetailEl) {
      const calls = avg.total_calls || 0;
      avgDetailEl.textContent = calls ? `${calls} 次回复 · ${(avg.avg_prompt_tokens || 0).toLocaleString()} + ${(avg.avg_completion_tokens || 0).toLocaleString()}` : '暂无回复记录';
    }
  } catch (e) {
    _lastTokenData = null;
  }
}

async function loadTelemetry() {
  const container = $('dashSkillStats');
  const totalEl = $('dashSkillTotalCalls');
  if (!container) return;
  try {
    const res = await get('/telemetry');
    const dataKey = JSON.stringify(res);
    if (_lastTelemetryData === dataKey) return; // 数据未变化，跳过重建
    _lastTelemetryData = dataKey;

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
    _lastTelemetryData = null;
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

// ── Token Tracker ─────────────────────────────────────
let _ttState = { range: 'all', page: 0, data: null };

async function loadTokenTracker() {
  renderPersonaSelect();
  if (!currentPersona && personas.length > 0) {
    selectPersona(personas[0].name);
  }
  // Highlight active range button
  document.querySelectorAll('#page-token-tracker .btn[data-range]').forEach((b) => {
    b.classList.toggle('active', b.dataset.range === _ttState.range);
  });
  // Reset to overview tab on page load
  ttSwitchTab('overview');
  await ttLoadData();
}

function ttSetRange(range) {
  _ttState.range = range;
  _ttState.page = 0;
  document.querySelectorAll('#page-token-tracker .btn[data-range]').forEach((b) => {
    b.classList.toggle('active', b.dataset.range === range);
  });
  ttLoadData();
}

function ttChangePage(delta) {
  const records = (_ttState.data?.recent_with_breakdown || []);
  const maxPage = Math.max(0, Math.ceil(records.length / 10) - 1);
  _ttState.page = Math.max(0, Math.min(maxPage, _ttState.page + delta));
  ttRenderRecentTable();
}

async function ttLoadData() {
  if (!currentPersona) return;
  const name = currentPersona;

  // Compute time range
  let start = null, end = null;
  const now = Date.now() / 1000;
  if (_ttState.range === 'today') {
    const d = new Date();
    d.setHours(0, 0, 0, 0);
    start = d.getTime() / 1000;
    end = now;
  } else if (_ttState.range === '7d') {
    start = now - 7 * 86400;
    end = now;
  } else if (_ttState.range === '30d') {
    start = now - 30 * 86400;
    end = now;
  }

  const qs = start ? `?start=${Math.floor(start)}&end=${Math.floor(end)}` : '';
  try {
    const res = await get(`/personas/${name}/tokens${qs}`);
    _ttState.data = res;

    // Summary stats
    const summary = res.summary || {};
    const statEls = document.querySelectorAll('#ttSummaryStats .stat-card .value');
    if (statEls.length >= 4) {
      animateNumber(statEls[0], summary.total_calls || 0, 500);
      animateNumber(statEls[1], summary.total_prompt_tokens || 0, 500);
      animateNumber(statEls[2], summary.total_completion_tokens || 0, 500);
      animateNumber(statEls[3], summary.total_tokens || 0, 500);
    }
    const avg = res.response_avg || {};
    const avgEl = $('ttAvgRound');
    const avgDetailEl = $('ttAvgRoundDetail');
    if (avgEl) animateNumber(avgEl, avg.avg_total_tokens || 0, 500);
    if (avgDetailEl) {
      const calls = avg.total_calls || 0;
      avgDetailEl.textContent = calls ? `${calls} 次回复 · ${(avg.avg_prompt_tokens || 0).toLocaleString()} + ${(avg.avg_completion_tokens || 0).toLocaleString()}` : '暂无回复记录';
    }

    // Render charts for the currently active tab only
    ttRenderActiveTab();
  } catch (e) {
    console.error('ttLoadData', e);
    const els = ['ttTimeSeries', 'ttActiveHours', 'ttSectionBreakdown', 'ttTaskHierarchy', 'ttByModel', 'ttByGroup', 'ttByProvider'];
    els.forEach((id) => { const el = $(id); if (el) el.textContent = '加载失败'; });
  }
}

// ── Tab switching ──────────────────────────────────────
let _ttActiveTab = 'overview';

function ttSwitchTab(tab) {
  _ttActiveTab = tab;
  document.querySelectorAll('#ttTabBar .tab-btn').forEach((b) => {
    b.classList.toggle('active', b.dataset.tab === tab);
  });
  document.querySelectorAll('#page-token-tracker .tab-panel').forEach((p) => {
    p.classList.toggle('active', p.dataset.tab === tab);
  });
  // Defer chart rendering so the container has layout
  requestAnimationFrame(() => ttRenderActiveTab());
}

function ttRenderActiveTab() {
  const res = _ttState.data;
  if (!res) return;
  switch (_ttActiveTab) {
    case 'overview':
      renderTimeSeries($('ttTimeSeries'), res.hourly || []);
      renderActiveHours($('ttActiveHours'), res.hourly_distribution || []);
      _renderExtraStats('tt', res);
      break;
    case 'module':
      renderSectionBars($('ttSectionBreakdown'), res.section_breakdown || {}, res.section_breakdown_by_task || {});
      renderTaskHierarchy('ttTaskHierarchy', res.by_task || []);
      break;
    case 'dimension':
      ttRenderDimensionList('ttByModel', res.by_model || []);
      ttRenderDimensionList('ttByGroup', res.by_group || []);
      ttRenderDimensionList('ttByProvider', res.by_provider || []);
      break;
    case 'detail':
      ttRenderRecentTable();
      break;
  }
}

function ttRenderDimensionList(containerId, items) {
  const el = $(containerId);
  if (!el) return;
  if (!items.length) {
    el.innerHTML = '<div style="color:var(--text-2)">暂无数据</div>';
    return;
  }
  if (typeof echarts === 'undefined') {
    // Fallback to text list if ECharts not loaded
    el.innerHTML = items.slice(0, 8).map((it) => `
      <div style="display:flex;justify-content:space-between;align-items:center;padding:6px 0;border-bottom:1px solid var(--border);font-size:13px">
        <span style="color:var(--text);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:140px" title="${it.name}">${it.name}</span>
        <span style="color:var(--text-2);font-family:ui-monospace,monospace">${it.total_tokens || 0}</span>
      </div>
    `).join('');
    return;
  }

  const sorted = [...items].sort((a, b) => (b.total_tokens || 0) - (a.total_tokens || 0)).slice(0, 10);
  const data = sorted.map((it) => ({
    value: it.total_tokens || 0,
    name: it.name || '未知',
    calls: it.calls || 0,
    prompt: it.prompt_tokens || 0,
    completion: it.completion_tokens || 0,
  }));

  let chart = echarts.getInstanceByDom(el);
  if (!chart) {
    chart = echarts.init(el, 'dark');
    const onResize = () => chart.resize();
    window.removeEventListener('resize', el._barResize);
    window.addEventListener('resize', onResize);
    el._barResize = onResize;
  }

  chart.setOption({
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      formatter: (params) => {
        const p = params[0];
        const d = p.data;
        return `<b>${d.name}</b><br/>总 Tokens: <b>${d.value.toLocaleString()}</b><br/>调用: ${d.calls} 次<br/>Prompt: ${d.prompt.toLocaleString()}<br/>Completion: ${d.completion.toLocaleString()}`;
      },
    },
    grid: { top: 8, bottom: 8, left: 8, right: 48, containLabel: true },
    xAxis: { type: 'value', axisLabel: { fontSize: 11, color: '#8b949e' }, splitLine: { lineStyle: { color: '#30363d' } } },
    yAxis: {
      type: 'category',
      data: data.map((d) => d.name).reverse(),
      axisLabel: { fontSize: 11, color: '#c9d1d9' },
      axisLine: { show: false },
      axisTick: { show: false },
    },
    series: [{
      type: 'bar',
      data: data.reverse(),
      barWidth: 14,
      itemStyle: {
        borderRadius: [0, 4, 4, 0],
        color: new echarts.graphic.LinearGradient(0, 0, 1, 0, [
          { offset: 0, color: '#58a6ff' },
          { offset: 1, color: '#a371f7' },
        ]),
      },
      label: {
        show: true,
        position: 'right',
        fontSize: 11,
        color: '#c9d1d9',
        formatter: (p) => p.value.toLocaleString(),
      },
    }],
  }, true);
}

function renderTaskHierarchy(containerId, items) {
  const el = $(containerId);
  if (!el) return;
  if (!items.length) {
    el.innerHTML = '<div style="color:var(--text-2)">暂无数据</div>';
    return;
  }
  if (typeof echarts === 'undefined') {
    el.innerHTML = '<div style="color:var(--text-2)">图表库未加载</div>';
    return;
  }

  const labels = {
    response_generate: '主模型调用',
    cognition_analyze: '认知分析',
    diary_generate: '日记生成',
    diary_consolidate: '日记合并',
    proactive_generate: '主动/提醒生成',
    persona_generate: '人格生成',
  };

  const colors = ['#58a6ff', '#3fb950', '#d29922', '#f85149', '#a371f7', '#e3b341'];
  const data = items.map((it, i) => ({
    value: it.total_tokens || 0,
    name: labels[it.name] || it.name,
    calls: it.calls || 0,
    prompt: it.prompt_tokens || 0,
    completion: it.completion_tokens || 0,
    itemStyle: { color: colors[i % colors.length] },
  }));

  let chart = echarts.getInstanceByDom(el);
  if (!chart) {
    chart = echarts.init(el, 'dark');
    const onResize = () => chart.resize();
    window.removeEventListener('resize', el._donutResize);
    window.addEventListener('resize', onResize);
    el._donutResize = onResize;
  }

  chart.setOption({
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'item',
      formatter: (p) => {
        const d = p.data;
        return `<b>${d.name}</b><br/>占比: <b>${p.percent}%</b><br/>Tokens: ${d.value.toLocaleString()}<br/>调用: ${d.calls} 次<br/>Prompt: ${d.prompt.toLocaleString()}<br/>Completion: ${d.completion.toLocaleString()}`;
      },
    },
    legend: {
      orient: 'vertical',
      right: 10,
      top: 'center',
      textStyle: { fontSize: 12, color: '#c9d1d9' },
      itemWidth: 12,
      itemHeight: 12,
    },
    series: [{
      type: 'pie',
      radius: ['40%', '70%'],
      center: ['40%', '50%'],
      avoidLabelOverlap: true,
      itemStyle: { borderRadius: 6, borderColor: '#0d1117', borderWidth: 2 },
      label: { show: false },
      emphasis: {
        label: { show: true, fontSize: 14, fontWeight: 'bold', color: '#e8eaf0' },
        itemStyle: { shadowBlur: 10, shadowOffsetX: 0, shadowColor: 'rgba(0,0,0,0.5)' },
      },
      data,
    }],
  }, true);
}

async function loadCognition() {
  renderPersonaSelect();
  if (!currentPersona && personas.length > 0) {
    selectPersona(personas[0].name);
  }
  if (!currentPersona) return;
  try {
    const res = await get(`/personas/${currentPersona}/cognition?limit=100`);
    renderEmotionDistribution($('cogEmotionDistribution'), res.emotion_distribution || {});
    renderEmotionTimeline($('cogEmotionTimeline'), res.events || []);
  } catch (e) {
    console.error('loadCognition', e);
    const els = ['cogEmotionDistribution', 'cogEmotionTimeline'];
    els.forEach((id) => { const el = $(id); if (el) el.textContent = '加载失败'; });
  }
}

let _diaryKeywordFilter = '';

function diarySetKeyword(kw) {
  _diaryKeywordFilter = kw;
  diaryRenderKeywordBar();
  diaryRenderEntries(_diaryEntriesCache);
}

function diaryClearKeyword() {
  _diaryKeywordFilter = '';
  diaryRenderKeywordBar();
  diaryRenderEntries(_diaryEntriesCache);
}

function diaryRenderKeywordBar() {
  const bar = $('diaryKeywordFilterBar');
  const active = $('diaryActiveKeyword');
  if (bar) bar.style.display = _diaryKeywordFilter ? 'flex' : 'none';
  if (active) active.textContent = _diaryKeywordFilter;
}

function diaryRenderEntries(entries) {
  const listEl = $('diaryList');
  if (!listEl) return;
  let filtered = entries || [];
  if (_diaryKeywordFilter) {
    filtered = filtered.filter((e) => (e.keywords || []).includes(_diaryKeywordFilter));
  }
  if (!filtered.length) {
    listEl.innerHTML = '<div style="color:var(--text-2);padding:12px">暂无日记</div>';
    return;
  }
  listEl.innerHTML = filtered.map((e) => {
    const ts = e.created_at ? new Date(e.created_at).toLocaleString('zh-CN', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '—';
    const kws = (e.keywords || []).slice(0, 8).map((k) => `<span style="background:var(--bg-2);border:1px solid var(--border);border-radius:8px;padding:2px 8px;font-size:11px;color:var(--text-2)">${k}</span>`).join('');
    return `
      <div style="background:var(--bg-2);border:1px solid var(--border);border-radius:8px;padding:12px">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
          <span style="font-size:12px;color:var(--text-2)">${ts}</span>
          <span style="font-size:11px;color:var(--text-2);background:var(--bg);padding:2px 8px;border-radius:4px">${e.group_id || '—'}</span>
        </div>
        <div style="font-size:14px;font-weight:600;margin-bottom:6px;color:var(--text)">${e.summary || '无摘要'}</div>
        <div style="font-size:13px;color:var(--text);line-height:1.6;margin-bottom:8px;white-space:pre-wrap">${e.content || ''}</div>
        <div style="display:flex;flex-wrap:wrap;gap:4px">${kws}</div>
      </div>
    `;
  }).join('');
}

let _diaryEntriesCache = [];

let _diaryGroupFilter = '';

function diaryToggleDropdown() {
  const list = $('diaryDropdownList');
  const arrow = $('diaryDropdownArrow');
  if (!list) return;
  const open = list.style.display === 'block';
  list.style.display = open ? 'none' : 'block';
  if (arrow) arrow.style.transform = open ? 'rotate(0deg)' : 'rotate(180deg)';
  if (!open) {
    const close = (e) => {
      if (!list.contains(e.target) && !$('diaryGroupDropdown').contains(e.target)) {
        list.style.display = 'none';
        if (arrow) arrow.style.transform = 'rotate(0deg)';
        document.removeEventListener('click', close);
      }
    };
    setTimeout(() => document.addEventListener('click', close), 0);
  }
}

function diarySelectGroup(gid) {
  _diaryGroupFilter = gid;
  const label = $('diaryDropdownLabel');
  if (label) label.textContent = gid || '全部群聊';
  const list = $('diaryDropdownList');
  const arrow = $('diaryDropdownArrow');
  if (list) list.style.display = 'none';
  if (arrow) arrow.style.transform = 'rotate(0deg)';
  diaryLoadData();
}

async function diaryLoadData() {
  renderPersonaSelect();
  if (!currentPersona && personas.length > 0) {
    selectPersona(personas[0].name);
  }
  if (!currentPersona) return;
  try {
    const groupId = _diaryGroupFilter;
    const qs = groupId ? `?group_id=${encodeURIComponent(groupId)}` : '';
    const res = await get(`/personas/${currentPersona}/diary${qs}`);

    // Stats
    const stats = res.stats || {};
    const totalEl = $('diaryTotal');
    const groupsEl = $('diaryGroups');
    if (totalEl) totalEl.textContent = (stats.total || 0).toLocaleString();
    if (groupsEl) groupsEl.textContent = (stats.groups || 0).toLocaleString();

    // Keywords
    const kwContainer = $('diaryKeywords');
    const topKws = stats.top_keywords || [];
    if (kwContainer) {
      if (!topKws.length) {
        kwContainer.innerHTML = '<span style="color:var(--text-2)">暂无关键词</span>';
      } else {
        kwContainer.innerHTML = topKws.map(([kw, cnt]) => {
          const active = kw === _diaryKeywordFilter;
          return `
            <span onclick="diarySetKeyword('${kw.replace(/'/g, "\\'")}')" style="cursor:pointer;background:${active ? 'var(--accent)' : 'var(--bg-2)'};border:1px solid var(--border);border-radius:12px;padding:3px 10px;font-size:12px;color:${active ? '#fff' : 'var(--text)'};transition:.15s"
              onmouseenter="this.style.opacity='0.85'" onmouseleave="this.style.opacity='1'">${kw} <span style="opacity:0.7">${cnt}</span></span>
          `;
        }).join('');
      }
    }

    // Group filter dropdown list
    const listEl = $('diaryDropdownList');
    const labelEl = $('diaryDropdownLabel');
    if (listEl) {
      const groups = res.groups || [];
      const items = [{ gid: '', label: '全部群聊' }].concat(groups.map((g) => ({ gid: g, label: g })));
      listEl.innerHTML = items.map((it) => {
        const active = it.gid === _diaryGroupFilter;
        return `<div onclick="diarySelectGroup('${it.gid.replace(/'/g, "\\'")}')" class="diary-dropdown-item" style="padding:8px 12px;font-size:13px;cursor:pointer;color:${active ? 'var(--accent)' : 'var(--text)'};background:${active ? 'var(--surface-2)' : 'transparent'};border-radius:6px;margin:2px 4px"
          onmouseenter="this.style.background='var(--surface-2)'" onmouseleave="this.style.background='${active ? 'var(--surface-2)' : 'transparent'}'">${it.label}</div>`;
      }).join('');
    }
    if (labelEl) labelEl.textContent = _diaryGroupFilter || '全部群聊';

    // Entries
    _diaryEntriesCache = res.entries || [];
    diaryRenderKeywordBar();
    diaryRenderEntries(_diaryEntriesCache);
  } catch (e) {
    console.error('diaryLoadData', e);
    const listEl = $('diaryList');
    if (listEl) listEl.innerHTML = '<div style="color:var(--text-2);padding:12px">加载失败</div>';
  }
}

function ttRenderRecentTable() {
  const tbody = document.querySelector('#ttRecentTable tbody');
  const pgEl = $('ttPagination');
  if (!tbody) return;
  const records = (_ttState.data?.recent_with_breakdown || []);
  if (!records.length) {
    tbody.innerHTML = '<tr><td colspan="6" style="text-align:center;color:var(--text-2)">暂无记录</td></tr>';
    if (pgEl) pgEl.style.display = 'none';
    return;
  }

  const pageSize = 10;
  const totalPages = Math.max(1, Math.ceil(records.length / pageSize));
  const page = Math.min(_ttState.page, totalPages - 1);
  _ttState.page = page;
  const slice = records.slice(page * pageSize, (page + 1) * pageSize);

  const top3 = (bd) => {
    if (!bd || typeof bd !== 'object') return '—';
    return Object.entries(bd)
      .filter(([k]) => !['total', 'system_prompt_total', 'user_message'].includes(k))
      .sort((a, b) => b[1] - a[1])
      .slice(0, 3)
      .map(([k, v]) => `${k} ${v}`)
      .join(', ') || '—';
  };

  tbody.innerHTML = slice.map((r) => {
    const ts = r.timestamp ? new Date(r.timestamp * 1000).toLocaleString('zh-CN', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '—';
    return `
      <tr>
        <td>${ts}</td>
        <td>${r.task_name || '—'}</td>
        <td>${r.model || '—'}</td>
        <td class="mono">${r.prompt_tokens || 0}</td>
        <td class="mono">${r.completion_tokens || 0}</td>
        <td style="font-size:12px;color:var(--text-2);max-width:200px;overflow:hidden;text-overflow:ellipsis" title="${top3(r.breakdown).replace(/"/g, '&quot;')}">${top3(r.breakdown)}</td>
      </tr>
    `;
  }).join('');

  if (pgEl) {
    pgEl.style.display = totalPages > 1 ? 'flex' : 'none';
    const info = $('ttPageInfo');
    if (info) info.textContent = `第 ${page + 1} / ${totalPages} 页`;
    const prev = $('ttPrevPage');
    const next = $('ttNextPage');
    if (prev) prev.disabled = page <= 0;
    if (next) next.disabled = page >= totalPages - 1;
  }
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
    loadTokenStats();
    ncLoadLogs();
  }, 5000);
})();
