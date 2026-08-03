import { store } from '../store.js';
import { get, post } from '../app.js';
import { toast, flashSuccess } from '../components.js';
import { createScopedPage } from '../page-context.js';

const scopedPage = createScopedPage();
const CACHE_KEY = 'sirius-create-persona-draft';
const $ = scopedPage.$;

export function dispose() {
  scopedPage.use(null, null);
}

export async function init(container, params = {}) {
  scopedPage.use(params?.ctx, container);
  const root = container.querySelector('#createPersonaRoot') || container;
  root.innerHTML = buildFormHTML();
  restoreDraft();
  bindEvents();
}

function buildFormHTML() {
  return `
    <div class="card">
      <div class="card-header">
        <div>
          <div class="card-title">新建人格</div>
          <div class="card-subtitle">直接编写完整的人格提示词</div>
        </div>
      </div>
      <div style="padding:16px">
        <div style="display:flex;gap:12px;flex-wrap:wrap;margin-bottom:20px">
          <div class="form-group" style="margin:0;flex:1;min-width:200px">
            <label>标识名称 <span style="color:var(--danger)">*</span></label>
            <input type="text" id="personaId" placeholder="英文/数字，用于目录名">
          </div>
          <div class="form-group" style="margin:0;flex:1;min-width:200px">
            <label>显示名称</label>
            <input type="text" id="personaName" placeholder="人格的中文名称">
          </div>
          <div class="form-group" style="margin:0;flex:1;min-width:200px">
            <label>别名（空格分隔）</label>
            <input type="text" id="personaAliases" placeholder="小名 昵称 爱称">
          </div>
        </div>

        <div class="form-group" style="margin-bottom:20px">
          <label>身份锚定（完整人格提示词）</label>
          <textarea id="fullSystemPrompt" rows="16" placeholder="直接编写完整的人格设定、行为边界、表达方式和回应原则。这里的内容会原样放入【身份锚定】。"></textarea>
        </div>

        <div style="display:flex;gap:12px;align-items:center">
          <button class="btn btn-primary" id="createBtn">创建人格</button>
          <span id="createHint" style="font-size:12px;color:var(--text-3)"></span>
        </div>

        <div id="previewArea" style="margin-top:20px;display:none">
          <div class="card-header" style="padding:0">
            <div class="card-title">已保存配置</div>
          </div>
          <pre id="previewContent" style="background:var(--surface-2);padding:16px;border-radius:8px;max-height:500px;overflow:auto;font-size:13px;white-space:pre-wrap;margin-top:12px"></pre>
        </div>
      </div>
    </div>
  `;
}

function saveDraft() {
  const draft = {
    personaId: $('personaId')?.value || '',
    personaName: $('personaName')?.value || '',
    personaAliases: $('personaAliases')?.value || '',
    fullSystemPrompt: $('fullSystemPrompt')?.value || '',
  };
  try {
    localStorage.setItem(CACHE_KEY, JSON.stringify(draft));
  } catch {}
}

function restoreDraft() {
  try {
    const raw = localStorage.getItem(CACHE_KEY);
    if (!raw) return;
    const draft = JSON.parse(raw);
    if (draft.personaId && $('personaId')) $('personaId').value = draft.personaId;
    if (draft.personaName && $('personaName')) $('personaName').value = draft.personaName;
    if (draft.personaAliases && $('personaAliases')) $('personaAliases').value = draft.personaAliases;
    if (draft.fullSystemPrompt && $('fullSystemPrompt')) $('fullSystemPrompt').value = draft.fullSystemPrompt;
  } catch {}
}

function clearDraft() {
  try {
    localStorage.removeItem(CACHE_KEY);
  } catch {}
}

function bindEvents() {
  const createBtn = $('createBtn');
  if (!createBtn) return;
  createBtn.addEventListener('click', createPersona);
  ['personaId', 'personaName', 'personaAliases', 'fullSystemPrompt'].forEach(id => {
    const el = $(id);
    if (el) el.addEventListener('input', saveDraft);
  });
}

async function createPersona() {
  const personaId = $('personaId')?.value?.trim() || '';
  const personaName = $('personaName')?.value?.trim() || personaId;
  const personaAliases = $('personaAliases')?.value?.trim() || '';
  const fullSystemPrompt = $('fullSystemPrompt')?.value?.trim() || '';

  if (!personaId) {
    toast('请填写标识名称', 'error');
    return;
  }
  if (!personaId.replace(/[_-]/g, '').match(/^[a-zA-Z0-9\u4e00-\u9fff]+$/)) {
    toast('标识名称只能包含字母、数字、下划线和连字符', 'error');
    return;
  }

  const btn = $('createBtn');
  const hint = $('createHint');
  if (btn) {
    btn.disabled = true;
    btn.textContent = '创建中...';
  }
  try {
    await post('/personas', { name: personaId, display_name: personaName });
    if (hint) hint.textContent = '正在保存身份锚定...';
    const persona = {
      name: personaName,
      aliases: personaAliases.split(/\s+/).filter(Boolean),
      full_system_prompt: fullSystemPrompt,
    };
    await post('/persona/persona/save', { persona });

    const previewContent = $('previewContent');
    const previewArea = $('previewArea');
    if (previewContent) previewContent.textContent = JSON.stringify(persona, null, 2);
    if (previewArea) previewArea.style.display = '';

    clearDraft();
    if (btn) flashSuccess(btn);
    toast('人格创建成功');
    try {
      const list = await get('/personas');
      store.personas = list.personas || [];
    } catch {}
  } catch (e) {
    if (e?.name === 'AbortError') return;
    toast('创建失败: ' + e.message, 'error');
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = '创建人格';
    }
    if (hint) hint.textContent = '';
  }
}
