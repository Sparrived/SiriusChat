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

  await loadTools();
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


