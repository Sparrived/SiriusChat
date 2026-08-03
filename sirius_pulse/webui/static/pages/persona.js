import { store } from '../store.js';
import { get, post } from '../app.js';
import { toast } from '../components.js';
import { createScopedPage } from '../page-context.js';
import { createAutoSave } from '../autosave.js';

const scopedPage = createScopedPage();

export function dispose() {
  scopedPage.use(null, null);
}
const $ = scopedPage.$;

export async function init(container, params = {}) {
  scopedPage.use(params?.ctx, container);
  const name = store.currentPersona;
  if (!name) {
    container.innerHTML = `
      <div class="card">
        <div class="card-header">
          <div class="card-title">人格配置</div>
        </div>
        <div style="padding:40px;text-align:center;color:var(--text-3)">
          <div style="font-size:48px;margin-bottom:16px">✦</div>
          <div style="font-size:16px;margin-bottom:8px">请先选择人格</div>
          <div style="font-size:13px">在顶部导航栏中选择要配置的人格</div>
        </div>
      </div>
    `;
    return;
  }

  container.innerHTML = `
    <div class="card" id="personaStatusCard">
      <div class="card-header">
        <div>
          <div class="card-title">人格状态</div>
          <div class="card-subtitle" id="personaStatusSubtitle">${name}</div>
        </div>
        <div style="display:flex;gap:8px;align-items:center">
          <div id="personaStatus" style="display:flex;align-items:center;gap:8px;font-size:13px">
            <span class="status-dot" id="statusDot"></span>
            <span id="statusText">加载中...</span>
          </div>
          <button class="btn btn-success btn-sm" id="personaStartBtn" style="display:none">
            <span style="font-size:12px">▶</span> 启动
          </button>
          <button class="btn btn-danger btn-sm" id="personaStopBtn" style="display:none">
            <span style="font-size:12px">■</span> 停止
          </button>
        </div>
      </div>
    </div>
    <div class="card" style="margin-top:16px">
      <div class="card-header">
        <div>
          <div class="card-title">人格配置</div>
          <div class="card-subtitle">编辑 ${name} 的身份锚定</div>
        </div>
        <span id="personaAutoSaveStatus" style="color:var(--text-3);font-size:12px"></span>
      </div>
      <form id="personaForm" style="display:grid;gap:16px">
        <div class="form-group">
          <label>名称</label>
          <input type="text" name="name" readonly>
        </div>
        <div class="form-group">
          <label>别名</label>
          <input type="text" name="aliases" placeholder="多个别名用空格分隔">
        </div>
        <div class="form-group">
          <label>身份锚定（完整人格提示词）</label>
          <textarea name="full_system_prompt" rows="20" placeholder="直接编写完整的人格设定、行为边界、表达方式和回应原则。这里的内容会放入【身份锚定】。"></textarea>
        </div>
      </form>
    </div>
  `;

  const autoSave = createAutoSave({
    root: $('personaForm'),
    statusEl: $('personaAutoSaveStatus'),
    save: () => savePersona(name),
    onError: (error) => toast('保存失败: ' + error.message, 'error'),
  });

  await Promise.all([
    loadPersonaData(name),
    loadPersonaStatus(name)
  ]);
  autoSave.markReady();
  setupStatusButtons(name);
}

async function loadPersonaStatus(name) {
  try {
    const personas = store.personas || [];
    const persona = personas.find(p => p.name === name);
    const isRunning = persona?.running || false;

    const statusDot = $('statusDot');
    const statusText = $('statusText');
    const startBtn = $('personaStartBtn');
    const stopBtn = $('personaStopBtn');

    if (!statusDot || !statusText || !startBtn || !stopBtn) return;

    statusDot.className = `status-dot ${isRunning ? 'running' : ''}`;
    statusText.textContent = isRunning ? '运行中' : '已停止';
    statusText.style.color = isRunning ? 'var(--success)' : 'var(--text-3)';

    startBtn.style.display = isRunning ? 'none' : 'inline-flex';
    stopBtn.style.display = isRunning ? 'inline-flex' : 'none';
  } catch (e) {
    if (e?.name === 'AbortError') return;
    const statusText = $('statusText');
    if (statusText) statusText.textContent = '状态未知';
  }
}

function setupStatusButtons(name) {
  const startBtn = $('personaStartBtn');
  const stopBtn = $('personaStopBtn');

  if (!startBtn || !stopBtn) return;

  startBtn.addEventListener('click', async () => {
    try {
      startBtn.disabled = true;
      startBtn.textContent = '启动中...';
      const res = await post(`/persona/start`, {});
      if (res.success) {
        toast(`${name} 已启动`, 'success');
        await loadPersonaStatus(name);
        // 刷新store中的personas状态
        try {
          const list = await get('/personas');
          store.personas = list.personas || [];
        } catch {}
      } else {
        toast(res.error || '启动失败', 'error');
      }
    } catch (e) {
    if (e?.name === 'AbortError') return;
      toast('启动失败', 'error');
    } finally {
      startBtn.disabled = false;
      startBtn.innerHTML = '<span style="font-size:12px">▶</span> 启动';
    }
  });

  stopBtn.addEventListener('click', async () => {
    try {
      stopBtn.disabled = true;
      stopBtn.textContent = '停止中...';
      const res = await post(`/persona/stop`, {});
      if (res.success) {
        toast(`${name} 已停止`, 'success');
        await loadPersonaStatus(name);
        // 刷新store中的personas状态
        try {
          const list = await get('/personas');
          store.personas = list.personas || [];
        } catch {}
      } else {
        toast(res.error || '停止失败', 'error');
      }
    } catch (e) {
    if (e?.name === 'AbortError') return;
      toast('停止失败', 'error');
    } finally {
      stopBtn.disabled = false;
      stopBtn.innerHTML = '<span style="font-size:12px">■</span> 停止';
    }
  });
}

async function loadPersonaData(name) {
  try {
    const data = await get(`/persona/persona`);
    const form = $('personaForm');
    if (!form) return;

    form.name.value = data.name || name;
    form.aliases.value = (data.aliases || []).join(' ');
    form.full_system_prompt.value = data.full_system_prompt || '';
  } catch (e) {
    if (e?.name === 'AbortError') return;
    toast('加载人格数据失败: ' + e.message, 'error');
  }
}

async function savePersona(name) {
  const form = $('personaForm');
  if (!form) return;

  const persona = {
    name: form.name.value,
    aliases: form.aliases.value.split(/\s+/).filter(Boolean),
    full_system_prompt: form.full_system_prompt.value,
  };

  try {
    await post(`/persona/persona/save`, { persona });
  } catch (e) {
    if (e?.name === 'AbortError') return;
    throw e;
  }
}
