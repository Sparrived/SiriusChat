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
    const entries = Object.entries(bd)
      .filter(([k]) => !['total', 'system_prompt_total', 'user_message'].includes(k))
      .sort((a, b) => b[1] - a[1]);
    const nonzero = entries.filter(([, v]) => v > 0);
    if (!nonzero.length) return '—';
    return nonzero
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


// ── Users ─────────────────────────────────────────────
let _usersGroupFilter = '';

function loadUsers() {
  renderPersonaSelect();
  if (!currentPersona && personas.length > 0) {
    selectPersona(personas[0].name);
  }
  usersLoadData();
}

function usersToggleDropdown() {
  const list = $('usersDropdownList');
  const arrow = $('usersDropdownArrow');
  if (!list) return;
  const open = list.style.display === 'block';
  list.style.display = open ? 'none' : 'block';
  if (arrow) arrow.style.transform = open ? 'rotate(0deg)' : 'rotate(180deg)';
  if (!open) {
    const close = (e) => {
      if (!list.contains(e.target) && !$('usersGroupDropdown').contains(e.target)) {
        list.style.display = 'none';
        if (arrow) arrow.style.transform = 'rotate(0deg)';
        document.removeEventListener('click', close);
      }
    };
    setTimeout(() => document.addEventListener('click', close), 0);
  }
}

function usersSelectGroup(gid) {
  _usersGroupFilter = gid;
  const label = $('usersDropdownLabel');
  if (label) label.textContent = gid || '全部群聊';
  const list = $('usersDropdownList');
  const arrow = $('usersDropdownArrow');
  if (list) list.style.display = 'none';
  if (arrow) arrow.style.transform = 'rotate(0deg)';
  usersLoadData();
}

function usersBarColor(score) {
  if (score >= 0.7) return 'var(--success)';
  if (score >= 0.4) return 'var(--accent)';
  return 'var(--danger)';
}

function usersRenderList(users) {
  const listEl = $('usersList');
  if (!listEl) return;
  if (!users.length) {
    listEl.innerHTML = '<div style="color:var(--text-2);padding:12px">暂无用户画像数据</div>';
    return;
  }
  listEl.innerHTML = users.map((u) => {
    const rs = u.relationship_state || {};
    const familiarity = Math.min(1.0, 0.3 + (rs.interaction_frequency_7d || 0) * 0.5 + (rs.emotional_intimacy || 0) * 0.2);
    const interests = (u.interest_graph || []).map((n) => `
      <span style="background:var(--bg-2);border:1px solid var(--border);border-radius:8px;padding:2px 8px;font-size:11px;color:var(--text-2)">${n.topic || ''} <span style="opacity:0.7">${((n.participation || 0) * 100).toFixed(0)}%</span></span>
    `).join('');

    const bar = (label, score) => `
      <div style="display:flex;align-items:center;gap:8px;margin-top:6px">
        <span style="font-size:11px;color:var(--text-2);width:60px;flex-shrink:0">${label}</span>
        <div style="flex:1;height:6px;background:var(--bg-2);border-radius:3px;overflow:hidden">
          <div style="width:${(score * 100).toFixed(0)}%;height:100%;background:${usersBarColor(score)};border-radius:3px;transition:width .3s"></div>
        </div>
        <span style="font-size:11px;color:var(--text-2);width:36px;text-align:right">${(score * 100).toFixed(0)}%</span>
      </div>
    `;

    const firstAt = rs.first_interaction_at ? new Date(rs.first_interaction_at).toLocaleDateString('zh-CN') : '—';
    const lastAt = rs.last_interaction_at ? new Date(rs.last_interaction_at).toLocaleDateString('zh-CN') : '—';

    return `
      <div style="background:var(--bg-2);border:1px solid var(--border);border-radius:8px;padding:14px">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">
          <div style="display:flex;align-items:center;gap:10px">
            <div style="width:36px;height:36px;border-radius:50%;background:var(--accent);display:flex;align-items:center;justify-content:center;font-size:16px;color:#fff;font-weight:700">${(u.user_id || '?')[0].toUpperCase()}</div>
            <div>
              <div style="font-size:14px;font-weight:600;color:var(--text)">${u.user_id || '未知用户'}</div>
              <div style="font-size:12px;color:var(--text-2);margin-top:2px">沟通风格: ${u.communication_style || '未知'}</div>
            </div>
          </div>
          <div style="text-align:right">
            <div style="font-size:18px;font-weight:700;color:${usersBarColor(familiarity)}">${(familiarity * 100).toFixed(0)}%</div>
            <div style="font-size:11px;color:var(--text-2)">熟悉度</div>
          </div>
        </div>
        <div style="background:var(--bg);border-radius:6px;padding:10px 12px">
          ${bar('信任度', rs.trust_score || 0)}
          ${bar('亲密度', rs.emotional_intimacy || 0)}
          ${bar('依赖度', rs.dependency_score || 0)}
          ${bar('7天互动', Math.min(1, rs.interaction_frequency_7d || 0))}
        </div>
        ${interests ? `<div style="display:flex;flex-wrap:wrap;gap:4px;margin-top:10px">${interests}</div>` : ''}
        <div style="display:flex;gap:16px;margin-top:10px;font-size:11px;color:var(--text-2)">
          <span>首次互动: ${firstAt}</span>
          <span>最近互动: ${lastAt}</span>
        </div>
      </div>
    `;
  }).join('');
}

async function usersLoadData() {
  renderPersonaSelect();
  if (!currentPersona && personas.length > 0) {
    selectPersona(personas[0].name);
  }
  if (!currentPersona) return;
  try {
    const groupId = _usersGroupFilter;
    const qs = groupId ? `?group_id=${encodeURIComponent(groupId)}` : '';
    const res = await get(`/personas/${currentPersona}/users${qs}`);

    const users = res.users || [];
    const groups = res.groups || [];

    // Stats
    const totalEl = $('usersTotal');
    const groupsEl = $('usersGroups');
    if (totalEl) totalEl.textContent = users.length.toLocaleString();
    if (groupsEl) groupsEl.textContent = groups.length.toLocaleString();

    // Dropdown
    const listEl = $('usersDropdownList');
    const labelEl = $('usersDropdownLabel');
    if (listEl) {
      const items = [{ gid: '', label: '全部群聊' }].concat(groups.map((g) => ({ gid: g, label: g })));
      listEl.innerHTML = items.map((it) => {
        const active = it.gid === _usersGroupFilter;
        return `<div onclick="usersSelectGroup('${it.gid.replace(/'/g, "\\'")}')" class="diary-dropdown-item" style="padding:8px 12px;font-size:13px;cursor:pointer;color:${active ? 'var(--accent)' : 'var(--text)'};background:${active ? 'var(--surface-2)' : 'transparent'};border-radius:6px;margin:2px 4px"
          onmouseenter="this.style.background='var(--surface-2)'" onmouseleave="this.style.background='${active ? 'var(--surface-2)' : 'transparent'}'">${it.label}</div>`;
      }).join('');
    }
    if (labelEl) labelEl.textContent = _usersGroupFilter || '全部群聊';

    usersRenderList(users);
  } catch (e) {
    console.error('usersLoadData', e);
    const listEl = $('usersList');
    if (listEl) listEl.innerHTML = '<div style="color:var(--text-2);padding:12px">加载失败</div>';
  }
}
