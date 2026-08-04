import { store } from '../store.js';
import { get, post } from '../app.js';
import { toast, flashSuccess } from '../components.js';
import { createScopedPage } from '../page-context.js';

const scopedPage = createScopedPage();

export function dispose() {
  closeModal();
  scopedPage.use(null, null);
}
const $ = scopedPage.$;

let currentModal = null;
const modal$ = (id) => currentModal?.querySelector(`#${id}`);
let mcpServers = {};

export async function init(container, params = {}) {
  scopedPage.use(params?.ctx, container);
  const name = store.currentPersona;
  if (!name) {
    container.innerHTML = `
      <div class="card">
        <div style="padding:60px;text-align:center;color:var(--text-3)">请先选择人格</div>
      </div>
    `;
    return;
  }

  container.innerHTML = `
    <div class="card mcp-page" style="margin-bottom:20px">
      <div class="card-header mcp-section-header">
        <div>
          <div class="card-title">MCP 服务</div>
          <div class="card-subtitle">为当前人格连接外部工具服务，保存后自动重载运行时</div>
        </div>
        <div class="mcp-header-actions">
          <span id="mcpLoadStatus" style="color:var(--text-3);font-size:12px"></span>
          <button class="btn btn-sm" id="refreshMcp">刷新</button>
          <button class="btn btn-primary btn-sm" id="addMcp">+ 添加服务</button>
        </div>
      </div>
      <div id="mcpList"></div>
    </div>
    <div class="card" style="margin-bottom:20px">
      <div class="card-header">
        <div>
          <div class="card-title">工具列表</div>
          <div class="card-subtitle">管理当前人格已安装的工具</div>
        </div>
        <button class="btn btn-sm" id="refreshTools">刷新</button>
      </div>
      <div id="toolList" style="padding:16px">
        <div style="color:var(--text-3)">加载中...</div>
      </div>
    </div>
  `;

  $('refreshTools').addEventListener('click', () => loadTools());
  $('refreshMcp').addEventListener('click', () => loadMcp());
  $('addMcp').addEventListener('click', () => openMcpModal());

  await Promise.all([loadMcp(), loadTools()]);
}

async function loadMcp() {
  const list = $('mcpList');
  const status = $('mcpLoadStatus');
  if (!list) return;
  if (status) status.textContent = '读取中...';
  try {
    const data = await get('/persona/mcp');
    mcpServers = data.servers && typeof data.servers === 'object' ? data.servers : {};
    renderMcpList();
    if (status) status.textContent = `${Object.keys(mcpServers).length} 个服务`;
  } catch (e) {
    if (e?.name === 'AbortError') return;
    list.innerHTML = `<div style="color:var(--danger);padding:12px">加载 MCP 配置失败: ${escapeHtml(e.message)}</div>`;
    if (status) status.textContent = '读取失败';
  }
}

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, ch => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[ch]));
}

function mcpTransportLabel(transport) {
  return ({
    stdio: 'STDIO',
    streamable_http: 'Streamable HTTP',
    sse: 'SSE',
  })[transport] || transport || 'STDIO';
}

function renderMcpList() {
  const list = $('mcpList');
  if (!list) return;
  const entries = Object.entries(mcpServers);
  if (!entries.length) {
    list.innerHTML = `
      <div class="mcp-empty">
        <div class="mcp-empty-mark">MCP</div>
        <div>暂无 MCP 服务</div>
        <div style="font-size:12px;color:var(--text-3);margin-top:4px">添加一个 stdio、Streamable HTTP 或 SSE 服务</div>
      </div>
    `;
    return;
  }

  list.innerHTML = `<div class="mcp-server-grid">${entries.map(([name, config]) => {
    const server = config && typeof config === 'object' ? config : {};
    const enabled = server.enabled !== false;
    const transport = server.transport || 'stdio';
    const entry = transport === 'stdio'
      ? [server.command, ...(Array.isArray(server.args) ? server.args : [])].filter(Boolean).join(' ')
      : server.url || '';
    const headers = server.headers && typeof server.headers === 'object' ? Object.keys(server.headers).length : 0;
    const env = server.env && typeof server.env === 'object' ? Object.keys(server.env).length : 0;
    const args = Array.isArray(server.args) ? server.args.length : 0;
    return `
      <article class="mcp-server-card">
        <div class="mcp-server-topline">
          <div class="mcp-server-title-wrap">
            <span class="mcp-status-dot ${enabled ? 'is-enabled' : ''}"></span>
            <strong title="${escapeHtml(name)}">${escapeHtml(name)}</strong>
          </div>
          <span class="tag ${enabled ? 'tag-success' : 'tag-danger'}">${enabled ? '已启用' : '已禁用'}</span>
        </div>
        <div class="mcp-server-meta">
          <span class="tag tag-accent">${escapeHtml(mcpTransportLabel(transport))}</span>
          <span>${args ? `${args} 个参数` : ''}${headers ? `${args ? ' · ' : ''}${headers} 个 Header` : ''}${env ? `${args || headers ? ' · ' : ''}${env} 个环境变量` : ''}</span>
        </div>
        <div class="mcp-server-entry" title="${escapeHtml(entry)}">${escapeHtml(entry || '未配置连接入口')}</div>
        <div class="mcp-server-actions">
          <button class="btn btn-sm" data-mcp-action="edit" data-name="${escapeHtml(name)}">编辑</button>
          <button class="btn btn-sm btn-danger" data-mcp-action="delete" data-name="${escapeHtml(name)}">删除</button>
        </div>
      </article>
    `;
  }).join('')}</div>`;

  list.querySelectorAll('[data-mcp-action="edit"]').forEach(btn => {
    btn.addEventListener('click', () => openMcpModal(btn.dataset.name));
  });
  list.querySelectorAll('[data-mcp-action="delete"]').forEach(btn => {
    btn.addEventListener('click', () => deleteMcpServer(btn.dataset.name));
  });
}

async function deleteMcpServer(name) {
  if (!name || !window.confirm(`确定删除 MCP 服务“${name}”？`)) return;
  const next = { ...mcpServers };
  delete next[name];
  await saveMcpServers(next);
}

async function saveMcpServers(servers, closeAfterSave = false) {
  try {
    const data = await post('/persona/mcp', { servers });
    mcpServers = data.servers && typeof data.servers === 'object' ? data.servers : {};
    renderMcpList();
    const status = $('mcpLoadStatus');
    if (status) status.textContent = `${Object.keys(mcpServers).length} 个服务 · 已保存`;
    toast('MCP 配置已保存，运行时将自动重载', 'success');
    if (closeAfterSave) scopedPage.timeout(closeModal, 800);
    return true;
  } catch (e) {
    if (e?.name === 'AbortError') return false;
    toast('保存 MCP 配置失败: ' + e.message, 'error');
    return false;
  }
}

function jsonEditorValue(value) {
  return escapeHtml(JSON.stringify(value && typeof value === 'object' ? value : {}, null, 2));
}

function openMcpModal(name = null) {
  closeModal();
  const source = name && mcpServers[name] ? mcpServers[name] : {
    enabled: true,
    transport: 'stdio',
    command: '',
    args: [],
    cwd: '',
    env: {},
    url: '',
    headers: {},
  };
  const transport = source.transport || 'stdio';
  const overlay = document.createElement('div');
  overlay.className = 'modal-overlay';
  overlay.innerHTML = `
    <div class="modal mcp-modal">
      <div class="modal-header">
        <div>
          <div style="font-size:16px;font-weight:600">${name ? '编辑 MCP 服务' : '添加 MCP 服务'}</div>
          <div style="font-size:12px;color:var(--text-3);margin-top:3px">配置完成后会重新发现该服务提供的工具</div>
        </div>
        <button class="btn btn-sm" id="modalClose" title="关闭">✕</button>
      </div>
      <div class="modal-body" id="modalBody">
        <div class="mcp-form-grid">
          <div class="form-group">
            <label for="mcpName">服务名称</label>
            <input id="mcpName" type="text" value="${escapeHtml(name || '')}" placeholder="例如 filesystem" ${name ? 'readonly' : ''}>
          </div>
          <div class="form-group mcp-enabled-field">
            <label>状态</label>
            <label class="mcp-checkbox-label"><input id="mcpEnabled" type="checkbox" ${source.enabled !== false ? 'checked' : ''}> 启用此服务</label>
          </div>
        </div>
        <div class="form-group">
          <label for="mcpTransport">Transport</label>
          <select id="mcpTransport">
            <option value="stdio" ${transport === 'stdio' ? 'selected' : ''}>STDIO · 本地进程</option>
            <option value="streamable_http" ${transport === 'streamable_http' ? 'selected' : ''}>Streamable HTTP · 远程服务</option>
            <option value="sse" ${transport === 'sse' ? 'selected' : ''}>SSE · 远程服务</option>
          </select>
        </div>
        <div id="mcpStdioFields">
          <div class="mcp-form-grid">
            <div class="form-group">
              <label for="mcpCommand">启动命令</label>
              <input id="mcpCommand" type="text" value="${escapeHtml(source.command || '')}" placeholder="例如 npx 或 python">
            </div>
            <div class="form-group">
              <label for="mcpCwd">工作目录 <span style="font-weight:400;color:var(--text-3)">可选</span></label>
              <input id="mcpCwd" type="text" value="${escapeHtml(source.cwd || '')}" placeholder="留空使用当前进程目录">
            </div>
          </div>
          <div class="form-group">
            <label for="mcpArgs">命令参数</label>
            <textarea id="mcpArgs" rows="4" placeholder="每行一个参数，例如：&#10;-y&#10;@modelcontextprotocol/server-filesystem&#10;D:\\data">${escapeHtml(Array.isArray(source.args) ? source.args.join('\n') : '')}</textarea>
          </div>
          <div class="form-group">
            <label for="mcpEnv">环境变量 JSON</label>
            <textarea id="mcpEnv" rows="5" class="mcp-code-input">${jsonEditorValue(source.env)}</textarea>
            <div class="mcp-field-hint">支持 <code>env:NAME</code> 或 <code>${'${NAME}'}</code> 引用系统环境变量。敏感值不会回显。</div>
          </div>
        </div>
        <div id="mcpHttpFields">
          <div class="form-group">
            <label for="mcpUrl">服务 URL</label>
            <input id="mcpUrl" type="url" value="${escapeHtml(source.url || '')}" placeholder="https://example.com/mcp">
          </div>
          <div class="form-group">
            <label for="mcpHeaders">请求 Headers JSON</label>
            <textarea id="mcpHeaders" rows="6" class="mcp-code-input">${jsonEditorValue(source.headers)}</textarea>
            <div class="mcp-field-hint">可在值中使用 <code>${'${TOKEN}'}</code> 引用系统环境变量。敏感值不会回显。</div>
          </div>
        </div>
      </div>
      <div class="modal-footer">
        <button class="btn" id="modalCancel">取消</button>
        <button class="btn btn-primary" id="modalSave">保存并重载</button>
      </div>
    </div>
  `;
  document.body.appendChild(overlay);
  currentModal = overlay;
  overlay.addEventListener('click', e => { if (e.target === overlay) closeModal(); });
  overlay.querySelector('#modalClose').addEventListener('click', closeModal);
  overlay.querySelector('#modalCancel').addEventListener('click', closeModal);
  overlay.querySelector('#mcpTransport').addEventListener('change', updateMcpTransportFields);
  overlay.querySelector('#modalSave').addEventListener('click', saveMcpModal);
  updateMcpTransportFields();
}

function updateMcpTransportFields() {
  const transport = modal$('mcpTransport')?.value || 'stdio';
  const stdio = modal$('mcpStdioFields');
  const http = modal$('mcpHttpFields');
  if (stdio) stdio.style.display = transport === 'stdio' ? '' : 'none';
  if (http) http.style.display = transport === 'stdio' ? 'none' : '';
}

function parseMcpJson(id, label) {
  const text = modal$(id)?.value?.trim() || '{}';
  try {
    const value = JSON.parse(text);
    if (!value || Array.isArray(value) || typeof value !== 'object') throw new Error();
    return value;
  } catch {
    toast(`${label} 必须是 JSON 对象`, 'error');
    return null;
  }
}

async function saveMcpModal() {
  const name = modal$('mcpName')?.value?.trim();
  if (!name) {
    toast('请填写服务名称', 'error');
    return;
  }
  const transport = modal$('mcpTransport')?.value || 'stdio';
  const server = {
    enabled: modal$('mcpEnabled')?.checked !== false,
    transport,
  };
  if (transport === 'stdio') {
    server.command = modal$('mcpCommand')?.value?.trim() || '';
    server.cwd = modal$('mcpCwd')?.value?.trim() || '';
    server.args = (modal$('mcpArgs')?.value || '').split(/\r?\n/).map(value => value.trim()).filter(Boolean);
    server.env = parseMcpJson('mcpEnv', '环境变量');
    if (!server.env) return;
  } else {
    server.url = modal$('mcpUrl')?.value?.trim() || '';
    server.headers = parseMcpJson('mcpHeaders', '请求 Headers');
    if (!server.headers) return;
  }
  const btn = modal$('modalSave');
  if (btn) {
    btn.disabled = true;
    btn.textContent = '保存中...';
  }
  const next = { ...mcpServers, [name]: server };
  const saved = await saveMcpServers(next);
  if (saved) {
    flashSuccess(btn);
    scopedPage.timeout(closeModal, 800);
  } else if (btn) {
    btn.disabled = false;
    btn.textContent = '保存并重载';
  }
}

async function loadTools() {
  const name = store.currentPersona;
  const el = $('toolList');
  try {
    const data = await get(`/persona/tools`);
    const tools = data.tools || [];
    if (!tools.length) {
      el.innerHTML = '<div style="padding:24px;text-align:center;color:var(--text-3)">暂无工具</div>';
      return;
    }
    el.innerHTML = `<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:12px">${tools.map(s => renderToolCard(s)).join('')}</div>`;
    el.querySelectorAll('.tool-toggle').forEach(tag => {
      tag.addEventListener('click', (e) => {
        e.stopPropagation();
        const name = tag.dataset.name;
        const newState = tag.textContent === '已启用' ? false : true;
        tag.textContent = newState ? '已启用' : '已禁用';
        tag.style.background = newState ? 'var(--success)' : 'var(--text-3)';
        toggleTool(name, newState, tag);
      });
    });
    el.querySelectorAll('.tool-config-btn').forEach(btn => {
      btn.addEventListener('click', () => openConfigModal(btn.dataset.name));
    });
  } catch (e) {
    if (e?.name === 'AbortError') return;
    el.innerHTML = `<div style="color:var(--danger);padding:12px">加载失败: ${e.message}</div>`;
  }
}

function renderToolCard(s) {
  const tags = (s.tags || []).map(t => `<span class="tag">${t}</span>`).join('');
  const paramCount = (s.parameters || []).length;
  const isEnabled = s.enabled !== false;
  return `
    <div class="card tool-config-btn" data-name="${s.name}" style="margin:0;cursor:pointer">
      <div class="card-header">
        <div>
          <div style="display:flex;align-items:center;gap:12px">
            <span class="tool-toggle tag" data-name="${s.name}" style="font-size:11px;background:${isEnabled ? 'var(--success)' : 'var(--text-3)'};color:#fff;padding:2px 8px;border-radius:4px;flex-shrink:0" onclick="event.stopPropagation()">${isEnabled ? '已启用' : '已禁用'}</span>
            <span class="tag" style="font-size:11px;background:var(--accent);color:#fff;padding:2px 8px;border-radius:4px;flex-shrink:0">${s.version || '—'}</span>
            <span style="font-size:15px;font-weight:600;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${s.display_name || s.name}</span>
            ${s.developer_only ? '<span class="tag tag-accent" style="font-size:11px;flex-shrink:0">开发者</span>' : ''}
            ${s.silent ? '<span class="tag" style="font-size:11px;color:var(--text-3);flex-shrink:0">静默</span>' : ''}
          </div>
          ${tags ? `<div style="display:flex;flex-wrap:wrap;gap:6px;margin-top:8px">${tags}</div>` : ''}
        </div>
      </div>
      ${s.description ? `<div style="padding:0 16px 8px;font-size:13px;color:var(--text-2);line-height:1.5">${s.description}</div>` : ''}
      <div style="padding:0 16px 16px;display:flex;gap:16px;font-size:12px;color:var(--text-3)">
        <span>参数: ${paramCount}</span>
      </div>
    </div>
  `;
}

async function toggleTool(toolName, enabled, tagEl) {
  const name = store.currentPersona;
  try {
    await post(`/persona/tools/${toolName}/toggle`, { enabled });
    toast(`${toolName} 已${enabled ? '启用' : '禁用'}`, 'success');
  } catch (e) {
    if (e?.name === 'AbortError') return;
    toast('操作失败: ' + e.message, 'error');
    if (tagEl) {
      tagEl.textContent = enabled ? '已禁用' : '已启用';
      tagEl.style.background = enabled ? 'var(--text-3)' : 'var(--success)';
    }
  }
}

async function openConfigModal(toolName) {
  closeModal();
  const name = store.currentPersona;

  const overlay = document.createElement('div');
  overlay.className = 'modal-overlay';
  overlay.innerHTML = `
    <div class="modal" style="max-width:600px;max-height:85vh;overflow-y:auto">
      <div class="modal-header">
        <span style="font-size:16px;font-weight:600">${toolName} 配置</span>
        <button class="btn btn-sm" id="modalClose">✕</button>
      </div>
      <div class="modal-body" id="modalBody">
        <div style="padding:20px;text-align:center;color:var(--text-3)">加载中...</div>
      </div>
      <div class="modal-footer" id="modalFooter">
        <button class="btn" id="modalCancel">取消</button>
        <button class="btn btn-primary" id="modalSave">保存</button>
      </div>
    </div>
  `;
  document.body.appendChild(overlay);
  currentModal = overlay;
  overlay.addEventListener('click', (e) => { if (e.target === overlay) closeModal(); });
  overlay.querySelector('#modalClose').addEventListener('click', closeModal);
  overlay.querySelector('#modalCancel').addEventListener('click', closeModal);

  try {
    const data = await get(`/persona/tools/${toolName}/config`);
    await renderConfigModal(data, toolName);
  } catch (e) {
    if (e?.name === 'AbortError') return;
    const body = modal$('modalBody');
    if (body) body.innerHTML = `<div style="color:var(--danger);padding:12px">加载失败: ${e.message}</div>`;
  }
}

async function renderConfigModal(config, toolName) {
  const meta = config.meta || {};
  const params = meta.config_parameters?.length ? meta.config_parameters : (meta.parameters || []);
  const toolConfig = config.config || {};
  const extraKeys = Object.keys(toolConfig);

  let html = `
    <label style="display:flex;align-items:center;gap:8px;cursor:pointer;font-size:14px;margin-bottom:16px">
      <input type="checkbox" id="cfgEnabled" ${config.enabled !== false ? 'checked' : ''}>
      <span>启用工具</span>
    </label>
  `;

  // 使用 DynamicConfigForm 渲染参数表单
  if (params.length) {
    const { DynamicConfigForm } = await import('../components.js');
    const form = new DynamicConfigForm({
      containerId: 'toolConfigForm',
      parameters: params,
      settings: toolConfig,
      get: get,
    });
    await form.init();
    html += `<div id="toolConfigForm"></div>`;

    const body = modal$('modalBody');
    if (body) body.innerHTML = html;
    form.render();

    // 保存时使用表单收集的值，包装在 config 键下以匹配 API 期望的结构
    modal$('modalSave')?.addEventListener('click', async () => {
      const values = form.collectValues();
      await saveConfig({ config: values, enabled: modal$('cfgEnabled')?.checked !== false }, toolName);
    });
  } else {
    // 无参数时显示 JSON 编辑器
    if (extraKeys.length > 0) {
      const extra = {};
      extraKeys.forEach(k => { extra[k] = toolConfig[k]; });
      const extraVal = JSON.stringify(extra, null, 2);
      html += `
        <div style="border-top:1px solid var(--border);padding-top:16px;margin-top:16px">
          <h4 style="margin:0 0 8px;font-size:14px">额外配置 (JSON)</h4>
          <textarea id="cfgExtra" rows="6" style="width:100%;box-sizing:border-box;background:var(--surface-2);border:1px solid var(--border);border-radius:6px;padding:10px;font-size:12px;font-family:monospace">${extraVal}</textarea>
        </div>
      `;
    }

    const body = modal$('modalBody');
    if (body) body.innerHTML = html;

    modal$('modalSave')?.addEventListener('click', async () => {
      const enabledEl = modal$('cfgEnabled');
      const payload = { enabled: enabledEl ? enabledEl.checked : true };
      const extraText = modal$('cfgExtra')?.value?.trim();
      if (extraText) {
        try {
          const extra = JSON.parse(extraText);
          payload.config = extra;
        } catch {
          toast('JSON 格式错误', 'error');
          return;
        }
      }
      await saveConfig(payload, toolName);
    });
  }
}

async function saveConfig(payload, toolName) {
  const name = store.currentPersona;
  const btn = modal$('modalSave');
  if (btn) {
    btn.disabled = true;
    btn.textContent = '保存中...';
  }

  try {
    await post(`/persona/tools/${toolName}/config`, payload);
    flashSuccess(btn);
    toast('配置已保存');
    scopedPage.timeout(closeModal, 800);
  } catch (e) {
    if (e?.name === 'AbortError') return;
    toast('保存失败: ' + e.message, 'error');
    if (btn) {
      btn.disabled = false;
      btn.textContent = '保存';
    }
  }
}

function closeModal() {
  if (currentModal) {
    currentModal.remove();
    currentModal = null;
  }
}


