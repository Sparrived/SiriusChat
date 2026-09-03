
export function confirmDanger(message = '确定删除吗？此操作不可撤销。') {
  return window.confirm(message);
}

export function toast(msg, type = 'success') {
  const container = document.getElementById('toastContainer');
  const el = document.createElement('div');
  el.className = `toast ${type}`;
  el.textContent = msg;
  container.appendChild(el);
  setTimeout(() => { el.style.opacity = '0'; setTimeout(() => el.remove(), 300); }, 3000);
}

export function animateNumber(el, target, duration = 600) {
  if (!el) return;
  const start = parseInt(el.textContent.replace(/,/g, '') || '0', 10) || 0;
  if (start === target) return;
  const startTime = performance.now();
  function tick(now) {
    const progress = Math.min((now - startTime) / duration, 1);
    const eased = 1 - Math.pow(1 - progress, 3);
    el.textContent = Math.round(start + (target - start) * eased).toLocaleString();
    if (progress < 1) requestAnimationFrame(tick);
  }
  requestAnimationFrame(tick);
}

export function flashSuccess(btn) {
  if (!btn) return;
  const prev = btn.textContent;
  btn.classList.add('btn-success-flash');
  btn.textContent = '✓ ' + prev;
  btn.disabled = true;
  setTimeout(() => { btn.classList.remove('btn-success-flash'); btn.textContent = prev; btn.disabled = false; }, 1200);
}

export function applyStagger(container, childSelector) {
  if (!container) return;
  container.classList.add('animate-stagger');
  const children = childSelector ? container.querySelectorAll(childSelector) : container.children;
  Array.from(children).forEach((child, i) => child.style.setProperty('--i', String(i)));
}

export function showLoginOverlay() {
  const overlay = document.getElementById('loginOverlay');
  overlay.style.display = 'flex';
  overlay.innerHTML = `
    <div class="login-card">
      <div style="font-size:28px;margin-bottom:8px">✦</div>
      <h2 style="font-family:var(--font-display);font-size:22px;margin-bottom:4px;color:var(--text-1)">Sirius Pulse</h2>
      <p style="font-size:13px;color:var(--text-2);margin-bottom:24px">请输入管理员密码以访问控制台</p>
      <div class="form-group">
        <label>密码</label>
        <input id="loginPassword" type="password" placeholder="输入密码" autofocus>
      </div>
      <div id="loginError" style="color:var(--danger);font-size:12px;margin-bottom:12px;display:none"></div>
      <button type="button" id="loginBtn" class="btn btn-primary" style="width:100%">登录</button>
    </div>
  `;
  const pwInput = document.getElementById('loginPassword');
  const loginBtn = document.getElementById('loginBtn');
  
  async function doLogin() {
    const password = pwInput.value;
    const errEl = document.getElementById('loginError');
    if (!password) { errEl.textContent = '请输入密码'; errEl.style.display = ''; return; }
    try {
      const { post, setToken } = await import('./api.js');
      const data = await post('/auth/login', { username: 'admin', password });
      if (data.success && data.token) {
        setToken(data.token);
        overlay.style.display = 'none';
        toast('登录成功');
        window.dispatchEvent(new CustomEvent('auth:login'));
      } else {
        errEl.textContent = data.error || '登录失败';
        errEl.style.display = '';
      }
    } catch (e) {
      errEl.textContent = '网络错误';
      errEl.style.display = '';
    }
  }
  
  loginBtn.onclick = doLogin;
  pwInput.onkeydown = (e) => { if (e.key === 'Enter') doLogin(); };
  setTimeout(() => pwInput.focus(), 100);
}

export function hideLoginOverlay() {
  document.getElementById('loginOverlay').style.display = 'none';
}

export function formatHeartbeat(ts) {
  if (!ts) return '—';
  const diff = (Date.now() - new Date(ts)) / 1000;
  if (diff < 5) return '刚刚';
  if (diff < 60) return `${Math.floor(diff)}秒前`;
  if (diff < 3600) return `${Math.floor(diff / 60)}分钟前`;
  return new Date(ts).toLocaleString('zh-CN');
}

export function statCard(label, value, detail = '', icon = '') {
  return `
    <div class="stat-card">
      <div class="stat-label">${icon ? `<span>${icon}</span>` : ''}${label}</div>
      <div class="stat-value">${value}</div>
      ${detail ? `<div class="stat-detail">${detail}</div>` : ''}
    </div>
  `;
}

export const $ = (id) => document.getElementById(id);
export const $$ = (sel, root = document) => root.querySelectorAll(sel);

const HTML_ESCAPE_MAP = {
  '&': '&amp;',
  '<': '&lt;',
  '>': '&gt;',
  '"': '&quot;',
  "'": '&#39;',
};

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, (char) => HTML_ESCAPE_MAP[char]);
}

function configDomId(value, prefix = 'plugin_config_') {
  const encoded = Array.from(String(value ?? ''))
    .map(char => char.codePointAt(0).toString(16))
    .join('_');
  return `${prefix}${encoded || 'empty'}`;
}

const UNSAFE_CONFIG_FIELD_NAMES = new Set(['__proto__', 'prototype', 'constructor']);
const SECRET_CONFIG_NAMES = new Set([
  'password', 'passwords', 'secret', 'secrets', 'token', 'tokens', 'key', 'keys',
  'api_key', 'api_keys', 'access_token', 'refresh_token', 'authorization', 'auth',
  'authentication', 'bearer', 'client_secret', 'credential', 'credentials', 'session',
  'session_id',
]);
const SECRET_CONFIG_SUFFIXES = [
  '_token', '_tokens', '_key', '_keys', '_secret', '_secrets', '_password',
  '_passwords', '_credential', '_credentials', '_auth', '_session',
];
const MAX_JSON_EDITOR_CHARS = 128 * 1024;

export function isSafeConfigFieldName(value) {
  const name = String(value ?? '');
  return Boolean(name) && !UNSAFE_CONFIG_FIELD_NAMES.has(name);
}

function normalizeConfigFieldName(value) {
  return String(value ?? '')
    .replace(/([a-z0-9])([A-Z])/g, '$1_$2')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '');
}

export function isSecretConfigField(field) {
  const type = String(field?.type ?? '').toLowerCase();
  if (type === 'password' || type === 'secret') return true;
  const name = normalizeConfigFieldName(field?.name);
  return SECRET_CONFIG_NAMES.has(name) || SECRET_CONFIG_SUFFIXES.some(suffix => name.endsWith(suffix));
}

export function cloneSafeConfigValue(value) {
  if (Array.isArray(value)) return value.map(item => cloneSafeConfigValue(item));
  if (!value || typeof value !== 'object') return value;
  const clone = Object.create(null);
  for (const [key, child] of Object.entries(value)) {
    if (isSafeConfigFieldName(key)) clone[key] = cloneSafeConfigValue(child);
  }
  return clone;
}

export function parseConfigNumber(value) {
  if (value === null || value === undefined || String(value).trim() === '') return undefined;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : undefined;
}

function configParameterType(parameter) {
  return String(parameter?.type || 'str').toLowerCase();
}

export function createObjectArrayItem(fields, source = null) {
  const item = Object.create(null);
  if (!Array.isArray(fields)) return item;
  const sourceObject = source && typeof source === 'object' && !Array.isArray(source) ? source : null;
  fields.forEach(field => {
    const name = String(field?.name ?? '');
    if (!isSafeConfigFieldName(name) || isSecretConfigField(field)) return;
    const type = String(field?.type ?? '').toLowerCase();
    const hasSourceValue = sourceObject && Object.prototype.hasOwnProperty.call(sourceObject, name);
    const hasDefault = field && Object.prototype.hasOwnProperty.call(field, 'default') &&
      field.default !== undefined && field.default !== null;
    if (hasSourceValue) {
      item[name] = cloneSafeConfigValue(sourceObject[name]);
    } else if (hasDefault) {
      item[name] = cloneSafeConfigValue(field.default);
    } else if (type === 'checkbox_group' || type === 'list' || type === 'array') {
      item[name] = [];
    } else if (type === 'boolean' || type === 'bool') {
      item[name] = false;
    } else if (type === 'object') {
      item[name] = Object.create(null);
    } else if (type === 'json') {
      item[name] = null;
    } else if (type !== 'int' && type !== 'float' && type !== 'number') {
      item[name] = '';
    }
  });
  return item;
}

/**
 * 动态配置表单组件
 * 支持的参数类型: str/string, int/number, boolean, list/array, model, password, schedule
 * 支持分组: 参数可通过 group 属性分组显示
 */
export class DynamicConfigForm {
  /**
   * @param {Object} options
   * @param {string} options.containerId - 表单容器元素 ID
   * @param {Array} options.parameters - 参数定义列表
   * @param {Object} options.settings - 当前配置值
   * @param {Array} [options.modelChoices] - 模型选项列表 [{label, value}]
   * @param {Function} [options.get] - GET 请求函数
   */
  constructor({ containerId, containerElement, parameters, settings, modelChoices, get }) {
    this.containerId = containerId;
    this.containerElement = containerElement || null;
    this.parameters = Array.isArray(parameters)
      ? parameters.filter(parameter => isSafeConfigFieldName(parameter?.name))
      : [];
    const clonedSettings = cloneSafeConfigValue(settings || {});
    this.settings = clonedSettings && typeof clonedSettings === 'object'
      ? clonedSettings
      : Object.create(null);
    for (const parameter of this.parameters) {
      if (configParameterType(parameter) !== 'object_array' || !Array.isArray(parameter.fields)) continue;
      const key = String(parameter.name);
      const rows = Array.isArray(this.settings[key])
        ? this.settings[key]
        : (Array.isArray(parameter.default) && parameter.default.length > 0
          ? parameter.default
          : null);
      if (rows) {
        this.settings[key] = rows.map(row => createObjectArrayItem(parameter.fields, row));
      }
    }
    this.modelChoices = modelChoices || null;
    this.get = get;
    this.initError = null;
    this._scheduleData = Object.create(null);
    this._jsonErrors = new Set();
    this._invalidJsonValues = new Map();
    this._objectRowIds = new WeakMap();
    this._nextObjectRowId = 1;
    this._initScheduleData();
  }

  _initScheduleData() {
    for (const parameter of this.parameters) {
      if (configParameterType(parameter) !== 'schedule') continue;
      const key = String(parameter.name);
      const value = this.settings[key] ?? parameter.default;
      if (Array.isArray(value)) this._scheduleData[key] = value.map(item => ({ ...item }));
    }
    for (const [key, value] of Object.entries(this.settings)) {
      if (this._scheduleData[key] || !Array.isArray(value) || value.length === 0) continue;
      if (typeof value[0] === 'object' && value[0] !== null &&
          'time' in value[0] && 'duration' in value[0]) {
        this._scheduleData[key] = value
          .filter(item => item && typeof item === 'object' && !Array.isArray(item))
          .map(item => ({ ...item }));
      }
    }
  }

  _objectRowStateId(item, index) {
    if (item && typeof item === 'object') {
      let rowId = this._objectRowIds.get(item);
      if (!rowId) {
        rowId = `row${this._nextObjectRowId++}`;
        this._objectRowIds.set(item, rowId);
      }
      return rowId;
    }
    return `row${index}`;
  }

  _objectRowIndex(key, rowKey, fallbackIndex = -1) {
    const rows = this.settings?.[key];
    if (!Array.isArray(rows)) return -1;
    if (rowKey) return rows.findIndex((item, index) => this._objectRowStateId(item, index) === String(rowKey));
    const index = Number.parseInt(fallbackIndex, 10);
    return Number.isInteger(index) && index >= 0 && index < rows.length ? index : -1;
  }

  _resolveCompositeValue(bareName) {
    if (!bareName || !this.modelChoices) return bareName || '';
    const exact = this.modelChoices.find(o => o.value === bareName);
    if (exact) return exact.value;
    if (bareName.includes('/')) return bareName;
    const matches = this.modelChoices.filter(o => o.value.endsWith('/' + bareName));
    return matches.length === 1 ? matches[0].value : bareName;
  }

  /**
   * 异步初始化（获取远程数据）
   */
  async init() {
    if (!this.modelChoices && this.parameters.some(p => configParameterType(p) === 'model') && this.get) {
      try {
        const res = await this.get('/models');
        this.modelChoices = res.model_choices || [];
      } catch (e) {
        this.initError = e;
        console.warn('获取可用模型列表失败', e);
      }
    }
  }

  _formContainer() {
    return this.containerElement || document.getElementById(this.containerId);
  }

  /**
   * 渲染表单到容器
   */
  render() {
    const container = this._formContainer();
    if (!container) return;

    const effectiveSettings = this._getEffectiveSettings();
    if (!Object.keys(effectiveSettings).length && !this.parameters.length) {
      container.style.display = 'none';
      container.innerHTML = '';
      return;
    }

    container.style.display = 'block';
    container.innerHTML = this._buildForm(effectiveSettings);
    this._bindEvents();
  }

  _getEffectiveSettings() {
    // 合并默认值和用户设置，用户设置优先
    const defaults = {};
    this.parameters.forEach(p => {
      if (p.default !== undefined && p.default !== null) {
        defaults[p.name] = p.default;
      }
    });
    return { ...defaults, ...this.settings };
  }

  _buildForm(settings) {
    const renderedKeys = new Set();
    // 按 group 分组
    const groups = new Map(); // group -> fields[]
    const ungrouped = [];

    for (const param of this.parameters) {
      const key = String(param?.name ?? '');
      const value = settings[key];
      if (!key || renderedKeys.has(key)) continue;

      const type = configParameterType(param);
      const desc = param.description || '';
      const defaultVal = param.default;
      const required = param.required || false;
      const group = param.group || '';

      let fieldHtml = '';
      if (type === 'model') {
        // model 类型：有选项时渲染下拉框，否则回退到文本输入
        const modelValue = value || defaultVal || '';
        if (this.modelChoices?.length) {
          fieldHtml = this._renderModelSelect(key, modelValue, desc, required);
        } else {
          fieldHtml = this._renderText(key, param.name, modelValue, defaultVal, desc, required);
        }
      } else if (type === 'password' || type === 'secret') {
        fieldHtml = this._renderPassword(key, param.name, value, defaultVal, desc, required);
      } else if (type === 'boolean' || type === 'bool') {
        fieldHtml = this._renderCheckbox(key, param.name, value, desc);
      } else if (type === 'int' || type === 'float' || type === 'number') {
        fieldHtml = this._renderNumber(key, param.name, value, defaultVal, desc, required);
      } else if (type === 'string' || type === 'str') {
        fieldHtml = this._renderText(key, param.name, value, defaultVal, desc, required);
      } else if (type === 'list' || type === 'array') {
        fieldHtml = this._renderList(key, param.name, value, defaultVal, desc);
       } else if (type === 'schedule') {
         fieldHtml = this._renderSchedule(
           key,
           Array.isArray(value) ? value : (Array.isArray(defaultVal) ? defaultVal : []),
         );
      } else if (type === 'object_array') {
        fieldHtml = Array.isArray(param.fields)
          ? this._renderObjectArray(key, param.name, value, defaultVal, desc, param.fields)
          : this._renderJson(
            key,
            value === undefined ? defaultVal : value,
            param.name,
            desc,
            'array',
          );
      } else if (type === 'object' || type === 'json') {
        fieldHtml = this._renderJson(
          key,
          value === undefined ? defaultVal : value,
          param.name,
          desc,
          type === 'object' ? 'object' : 'any',
        );
      } else if (type === 'checkbox_group' && Array.isArray(param.choices)) {
        fieldHtml = this._renderCheckboxGroup(key, param.name, value, defaultVal, desc, param.choices);
      } else if (type === 'checkbox_group') {
        fieldHtml = this._renderJson(key, value ?? defaultVal, param.name, desc, 'array');
      } else {
        fieldHtml = this._renderJson(key, value ?? defaultVal, param.name, desc, 'any');
      }

      if (fieldHtml) {
        renderedKeys.add(key);
        if (group) {
          if (!groups.has(group)) groups.set(group, []);
          groups.get(group).push(fieldHtml);
        } else {
          ungrouped.push(fieldHtml);
        }
      }
    }

    // 处理 settings 中未在 parameters 定义的字段
    for (const [key, value] of Object.entries(settings)) {
      if (renderedKeys.has(key)) continue;
      renderedKeys.add(key);
      let fieldHtml = '';
      if (Array.isArray(value) && value.length > 0 &&
          typeof value[0] === 'object' && value[0] !== null &&
          'time' in value[0] && 'duration' in value[0]) {
        fieldHtml = this._renderSchedule(key, value);
      } else {
        fieldHtml = this._renderJson(key, value);
      }
      ungrouped.push(fieldHtml);
    }

    // 渲染分组
    const sections = [];
    // 无分组的字段放在前面
    if (ungrouped.length) {
      sections.push(`<div class="config-fields">${ungrouped.join('')}</div>`);
    }
    // 各分组
    for (const [groupName, fields] of groups) {
      sections.push(`
        <div class="config-group">
          <div class="config-group-header" data-group="${escapeHtml(groupName)}">
            <span class="config-group-title">${escapeHtml(groupName)}</span>
            <span class="config-group-toggle">▼</span>
          </div>
          <div class="config-group-body">
            <div class="config-fields">${fields.join('')}</div>
          </div>
        </div>
      `);
    }

    return sections.join('') || '<div style="color:var(--text-3);font-size:13px;text-align:center;padding:20px">暂无可配置项</div>';
  }

  _renderModelSelect(key, value, desc, required) {
    // value 可能是裸模型名，需要匹配复合格式 provider_type/model_name
    const resolvedValue = this._resolveCompositeValue(value);
    // 如果当前值不在选项列表中，添加一个额外的选项
    const valueInChoices = this.modelChoices.some(m => m.value === resolvedValue);
    const options = [...this.modelChoices];
    if (resolvedValue && !valueInChoices) {
      options.unshift({
        value: resolvedValue,
        label: String(resolvedValue) + ' (当前配置)',
        tags: [],
      });
    }
    
    return `
      <div class="config-field">
        <div class="config-field-header">
          <label class="config-field-label">${escapeHtml(key)}${required ? '<span class="config-required">*</span>' : ''}</label>
          ${desc ? `<span class="config-field-desc">${escapeHtml(desc)}</span>` : ''}
        </div>
        <div data-model-select="${escapeHtml(key)}" data-model-label="${escapeHtml(key)}" data-model-value="${escapeHtml(resolvedValue || '')}"></div>
      </div>
    `;
  }

  _renderPassword(key, label, value, defaultVal, desc, required) {
    const id = `pwd_${key}`;
    // Keep stored masked secrets out of the DOM; blank preserves them on the server.
    return `
      <div class="config-field">
        <div class="config-field-header">
          <label class="config-field-label">${escapeHtml(label)}${required ? '<span class="config-required">*</span>' : ''}</label>
          ${desc ? `<span class="config-field-desc">${escapeHtml(desc)}</span>` : ''}
        </div>
        <div style="position:relative">
          <input type="password" id="${escapeHtml(id)}" data-setting-key="${escapeHtml(key)}" value="" class="config-input" style="padding-right:36px">
          <button type="button" class="pwd-toggle" data-password-toggle="${escapeHtml(id)}"
            style="position:absolute;right:8px;top:50%;transform:translateY(-50%);background:none;border:none;cursor:pointer;font-size:16px;padding:4px;opacity:0.6">
            🙈
          </button>
        </div>
      </div>
    `;
  }

  _renderCheckbox(key, label, value, desc) {
    return `
      <div class="config-field">
        <label style="display:flex;align-items:center;gap:10px;cursor:pointer">
          <input type="checkbox" data-setting-key="${escapeHtml(key)}" ${value ? 'checked' : ''}
            style="width:18px;height:18px;accent-color:var(--accent)">
          <div>
            <span style="font-weight:500">${escapeHtml(label)}</span>
            ${desc ? `<div style="color:var(--text-3);font-size:12px;margin-top:2px">${escapeHtml(desc)}</div>` : ''}
          </div>
        </label>
      </div>
    `;
  }

  _renderNumber(key, label, value, defaultVal, desc, required) {
    return `
      <div class="config-field">
        <div class="config-field-header">
          <label class="config-field-label">${escapeHtml(label)}${required ? '<span class="config-required">*</span>' : ''}</label>
          ${desc ? `<span class="config-field-desc">${escapeHtml(desc)}</span>` : ''}
        </div>
        <div class="number-input-group">
          <button type="button" class="number-spin-btn" data-spin-target="${escapeHtml(key)}" data-spin-dir="-1">−</button>
          <input type="number" data-setting-key="${escapeHtml(key)}" value="${escapeHtml(value ?? defaultVal ?? '')}" class="config-input">
          <button type="button" class="number-spin-btn" data-spin-target="${escapeHtml(key)}" data-spin-dir="1">+</button>
        </div>
      </div>
    `;
  }

  _renderText(key, label, value, defaultVal, desc, required) {
    return `
      <div class="config-field">
        <div class="config-field-header">
          <label class="config-field-label">${escapeHtml(label)}${required ? '<span class="config-required">*</span>' : ''}</label>
          ${desc ? `<span class="config-field-desc">${escapeHtml(desc)}</span>` : ''}
        </div>
        <input type="text" data-setting-key="${escapeHtml(key)}" value="${escapeHtml(value ?? defaultVal ?? '')}" class="config-input">
      </div>
    `;
  }

  _renderList(key, label, value, defaultVal, desc) {
    const listVal = Array.isArray(value) ? value : (defaultVal ? [defaultVal] : []);
    return `
      <div class="config-field">
        <div class="config-field-header">
          <label class="config-field-label">${escapeHtml(label)}</label>
          ${desc ? `<span class="config-field-desc">${escapeHtml(desc)}</span>` : ''}
        </div>
        <div id="dynamicList_${escapeHtml(key)}" data-list-container="${escapeHtml(key)}" style="display:flex;flex-direction:column;gap:6px;margin-bottom:8px">
          ${listVal.map((v, i) => `
            <div style="display:flex;gap:6px;align-items:center">
              <input type="text" value="${escapeHtml(v)}" data-list-key="${escapeHtml(key)}" data-list-index="${i}" class="config-input" style="flex:1">
              <button type="button" class="btn btn-sm btn-ghost" data-list-remove="${escapeHtml(key)}" aria-label="删除列表项" style="padding:6px 8px;color:var(--danger)">✕</button>
            </div>
          `).join('')}
        </div>
        <button type="button" class="btn btn-sm btn-ghost" data-list-add="${escapeHtml(key)}" aria-label="添加列表项" style="padding:4px 12px;font-size:12px;color:var(--accent)">+ 添加</button>
      </div>
    `;
  }

  _renderSchedule(key, value) {
    const scheduleId = configDomId(key, 'dynamic_schedule_');
    const escapedKey = escapeHtml(key);
    const schedules = Array.isArray(value)
      ? value.filter(item => item && typeof item === 'object' && !Array.isArray(item))
      : [];
    return `
      <div class="config-field plugin-schedule-field">
        <div class="config-field-header">
          <label class="config-field-label" for="${escapeHtml(`${scheduleId}_add`)}">${escapeHtml(this._formatKey(key))}</label>
        </div>
        <div id="${escapeHtml(scheduleId)}" data-schedule-container="${escapedKey}" class="plugin-schedule-rows">
          ${schedules.map((schedule, index) => {
            const timeId = `${scheduleId}_${index}_time`;
            const durationId = `${scheduleId}_${index}_duration`;
            return `
            <div class="plugin-schedule-row">
              <label for="${escapeHtml(timeId)}" class="plugin-schedule-label">时间</label>
              <input id="${escapeHtml(timeId)}" type="time" value="${escapeHtml(schedule.time || '22:00')}" data-schedule-key="${escapedKey}" data-schedule-idx="${index}" data-schedule-field="time" class="config-input">
              <label for="${escapeHtml(durationId)}" class="plugin-schedule-label">时长</label>
              <div class="number-input-group plugin-schedule-duration">
                <button type="button" class="number-spin-btn" data-spin-target="${escapeHtml(durationId)}" data-spin-dir="-1" aria-label="减少时长">−</button>
                <input id="${escapeHtml(durationId)}" type="number" value="${escapeHtml(schedule.duration || 1440)}" min="1" max="10080" data-schedule-key="${escapedKey}" data-schedule-idx="${index}" data-schedule-field="duration" class="config-input" aria-label="第 ${index + 1} 项时长（分钟）">
                <button type="button" class="number-spin-btn" data-spin-target="${escapeHtml(durationId)}" data-spin-dir="1" aria-label="增加时长">+</button>
              </div>
              <span class="plugin-schedule-unit">分钟</span>
              <button type="button" class="btn btn-sm btn-ghost" data-schedule-remove="${index}" data-schedule-remove-key="${escapedKey}" aria-label="删除第 ${index + 1} 个定时">✕</button>
            </div>`;
          }).join('')}
        </div>
        <button id="${escapeHtml(`${scheduleId}_add`)}" type="button" class="btn btn-sm btn-ghost" data-schedule-add="${escapedKey}" aria-label="添加定时">+ 添加定时</button>
      </div>
    `;
  }

  _renderObjectArray(key, label, value, defaultVal, desc, fields) {
    const items = Array.isArray(value) ? value : (Array.isArray(defaultVal) ? defaultVal : []);
    const fieldDefs = Array.isArray(fields)
      ? fields.filter(field => isSafeConfigFieldName(field?.name))
      : [];
    const escapedKey = escapeHtml(key);

    const renderFieldInput = (field, val, idx, rowKey) => {
      const fieldType = String(field?.type || 'str').toLowerCase();
      const structuredField = fieldType === 'object' || fieldType === 'json';
      const fieldVal = val !== undefined
        ? val
        : (field?.default !== undefined ? field.default : (fieldType === 'object' ? {} : structuredField ? null : ''));
      const escapedFieldName = escapeHtml(field.name);
      const rowData = ` data-obj-row-key="${escapeHtml(rowKey)}"`;
      const fieldLabel = `${field.name}（第 ${idx + 1} 项）`;
      const inputId = configDomId(`${key}_${rowKey}_${field.name}`, 'plugin_object_input_');
      const listId = `objList_${configDomId(`${key}_${rowKey}_${field.name}`)}`;

      if (structuredField || (fieldVal !== null && typeof fieldVal === 'object' && !Array.isArray(fieldVal))) {
        const jsonPath = `${key}.${rowKey}.${field.name}`;
        const errorId = `${inputId}_error`;
        const rawValue = this._invalidJsonValues.get(jsonPath);
        let serializedValue = rawValue === undefined ? '' : rawValue;
        if (rawValue === undefined) {
          try {
            serializedValue = JSON.stringify(fieldVal, null, 2);
          } catch {
            serializedValue = '{}';
          }
        }
        const expected = fieldType === 'object' ? 'object' : 'any';
        return `<div style="flex:1;min-width:0">
          <textarea id="${escapeHtml(inputId)}" data-obj-array-key="${escapedKey}" data-obj-idx="${idx}"${rowData} data-obj-field="${escapedFieldName}" data-obj-json="true" data-json-path="${escapeHtml(jsonPath)}" data-json-expected="${expected}" aria-label="${escapeHtml(fieldLabel)}" aria-describedby="${escapeHtml(errorId)}" class="config-input plugin-code-input" rows="4" spellcheck="false">${escapeHtml(serializedValue)}</textarea>
          <span id="${escapeHtml(errorId)}" class="plugin-json-error" data-json-error="${escapeHtml(jsonPath)}" role="alert" hidden></span>
        </div>`;
      }
      if (fieldType === 'checkbox_group' && Array.isArray(field.choices) && field.choices.length) {
        const selected = new Set(Array.isArray(fieldVal) ? fieldVal : []);
        const groupId = configDomId(`${key}_${rowKey}_${field.name}`, 'plugin_object_choices_');
        const legendId = `${groupId}_label`;
        return `<fieldset class="plugin-choice-field plugin-choice-field--object" aria-labelledby="${escapeHtml(legendId)}">
          <legend id="${escapeHtml(legendId)}" class="sr-only">${escapeHtml(fieldLabel)}</legend>
          <div class="plugin-choice-grid">
            ${field.choices.map((choice, choiceIndex) => {
              const choiceId = `${groupId}_${choiceIndex}`;
              return `<label class="plugin-choice-pill" for="${escapeHtml(choiceId)}">
                <input id="${escapeHtml(choiceId)}" type="checkbox" data-obj-array-key="${escapedKey}" data-obj-idx="${idx}"${rowData} data-obj-field="${escapedFieldName}" data-obj-checkbox="${escapeHtml(choice)}"${selected.has(choice) ? ' checked' : ''} aria-label="${escapeHtml(fieldLabel)}：${escapeHtml(choice)}">
                <span>${escapeHtml(choice)}</span>
              </label>`;
            }).join('')}
          </div>
        </fieldset>`;
      }
      if (fieldType === 'checkbox_group') {
        const jsonPath = `${key}.${rowKey}.${field.name}`;
        const errorId = `${inputId}_error`;
        const rawValue = this._invalidJsonValues.get(jsonPath);
        let serializedValue = rawValue === undefined ? '' : rawValue;
        if (rawValue === undefined) {
          try {
            serializedValue = JSON.stringify(Array.isArray(fieldVal) ? fieldVal : [], null, 2);
          } catch {
            serializedValue = '[]';
          }
        }
        return `<textarea id="${escapeHtml(inputId)}" data-obj-array-key="${escapedKey}" data-obj-idx="${idx}"${rowData} data-obj-field="${escapedFieldName}" data-obj-json="true" data-json-path="${escapeHtml(jsonPath)}" data-json-expected="array" aria-label="${escapeHtml(fieldLabel)}" aria-describedby="${escapeHtml(errorId)}" class="config-input plugin-code-input" rows="4" spellcheck="false">${escapeHtml(serializedValue)}</textarea>
          <span id="${escapeHtml(errorId)}" class="plugin-json-error" data-json-error="${escapeHtml(jsonPath)}" role="alert" hidden></span>`;
      }
      if (fieldType === 'list' || fieldType === 'array') {
        const listItems = Array.isArray(fieldVal) ? fieldVal : [];
        return `<div style="flex:1">
          <div id="${escapeHtml(listId)}" style="display:flex;flex-direction:column;gap:4px;margin-bottom:4px">
            ${listItems.map((item, listIndex) => `
              <div style="display:flex;gap:4px;align-items:center">
                <input type="text" value="${escapeHtml(item)}" data-obj-array-key="${escapedKey}" data-obj-idx="${idx}"${rowData} data-obj-field="${escapedFieldName}" data-obj-list-idx="${listIndex}" class="config-input" aria-label="${escapeHtml(fieldLabel)}第 ${listIndex + 1} 项" style="flex:1;font-size:12px;padding:4px 6px">
                <button type="button" class="btn btn-sm btn-ghost" data-obj-list-remove="${escapedKey}" data-obj-idx="${idx}"${rowData} data-obj-field="${escapedFieldName}" data-obj-list-idx="${listIndex}" aria-label="删除${escapeHtml(fieldLabel)}第 ${listIndex + 1} 项" style="padding:2px 6px;color:var(--danger);font-size:11px">✕</button>
              </div>
            `).join('')}
          </div>
          <button type="button" class="btn btn-sm btn-ghost" data-obj-list-add="${escapedKey}" data-obj-idx="${idx}"${rowData} data-obj-field="${escapedFieldName}" aria-label="为${escapeHtml(fieldLabel)}添加列表项" style="padding:2px 8px;font-size:11px;color:var(--accent)">+ 添加</button>
        </div>`;
      }
      if (fieldType === 'password' || fieldType === 'secret') {
        return `<input type="password" data-obj-array-key="${escapedKey}" data-obj-idx="${idx}"${rowData} data-obj-field="${escapedFieldName}" value="" aria-label="${escapeHtml(fieldLabel)}" class="config-input" style="flex:1">`;
      }
      if (fieldType === 'boolean' || fieldType === 'bool') {
        return `<label class="plugin-switch plugin-switch--compact" for="${escapeHtml(inputId)}">
          <input type="checkbox" id="${escapeHtml(inputId)}" data-obj-array-key="${escapedKey}" data-obj-idx="${idx}"${rowData} data-obj-field="${escapedFieldName}"${fieldVal === true ? ' checked' : ''} aria-label="${escapeHtml(fieldLabel)}">
          <span class="plugin-switch-track" aria-hidden="true"><span></span></span>
        </label>`;
      }
      if (fieldType === 'int' || fieldType === 'float' || fieldType === 'number') {
        return `<input type="number" id="${escapeHtml(inputId)}" data-obj-array-key="${escapedKey}" data-obj-idx="${idx}"${rowData} data-obj-field="${escapedFieldName}" value="${escapeHtml(fieldVal)}" step="${fieldType === 'int' ? '1' : 'any'}" aria-label="${escapeHtml(fieldLabel)}" class="config-input" style="flex:1">`;
      }
      return `<input type="text" id="${escapeHtml(inputId)}" data-obj-array-key="${escapedKey}" data-obj-idx="${idx}"${rowData} data-obj-field="${escapedFieldName}" value="${escapeHtml(fieldVal)}" placeholder="${escapeHtml(field.description || '')}" aria-label="${escapeHtml(fieldLabel)}" class="config-input" style="flex:1">`;
    };

    return `
      <div class="config-field">
        <div class="config-field-header">
          <label class="config-field-label">${escapeHtml(label)}</label>
          ${desc ? `<span class="config-field-desc">${escapeHtml(desc)}</span>` : ''}
        </div>
        <div id="${escapeHtml(`objectArray_${key}`)}" style="display:flex;flex-direction:column;gap:8px;margin-bottom:8px">
          ${items.map((item, index) => {
            const rowKey = this._objectRowStateId(item, index);
            return `<article style="border:1px solid var(--border);border-radius:8px;padding:12px;background:var(--surface-2)" data-obj-array-item="${escapedKey}" data-obj-idx="${index}" data-obj-row-key="${escapeHtml(rowKey)}">
              <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
                <span style="font-size:12px;color:var(--text-3)">#${index + 1}</span>
                <button type="button" class="btn btn-sm btn-ghost" data-obj-array-remove="${escapedKey}" data-obj-idx="${index}" data-obj-row-key="${escapeHtml(rowKey)}" aria-label="删除配置项 ${index + 1}" style="padding:2px 8px;color:var(--danger)">✕</button>
              </div>
              <div style="display:grid;gap:8px">
                ${fieldDefs.map(field => {
                  const fieldType = String(field?.type || 'str').toLowerCase();
                  const inputId = configDomId(`${key}_${rowKey}_${field.name}`, 'plugin_object_input_');
                  const labelFor = ['str', 'string', 'bool', 'boolean', 'int', 'float', 'number', 'object', 'json'].includes(fieldType)
                    && !isSecretConfigField(field)
                    ? ` for="${escapeHtml(inputId)}"`
                    : '';
                  return `<div style="display:flex;align-items:flex-start;gap:8px">
                    <label${labelFor} style="font-size:12px;color:var(--text-3);min-width:80px;padding-top:6px;flex-shrink:0">${escapeHtml(field.name)}</label>
                    ${renderFieldInput(field, item && typeof item === 'object' ? item[field.name] : undefined, index, rowKey)}
                  </div>`;
                }).join('')}
              </div>
            </article>`;
          }).join('')}
        </div>
        <button type="button" class="btn btn-sm btn-ghost" data-obj-array-add="${escapedKey}" aria-label="添加配置项" style="padding:4px 12px;font-size:12px;color:var(--accent)">+ 添加</button>
      </div>
    `;
  }

  _renderCheckboxGroup(key, label, value, defaultVal, desc, choices) {
    const selected = new Set(Array.isArray(value) ? value : (Array.isArray(defaultVal) ? defaultVal : []));
    return `
      <div class="config-field">
        <div class="config-field-header">
          <label class="config-field-label">${escapeHtml(label)}</label>
          ${desc ? `<span class="config-field-desc">${escapeHtml(desc)}</span>` : ''}
        </div>
        <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:8px">
          ${choices.map(c => `
            <label style="display:flex;align-items:center;gap:6px;cursor:pointer;font-size:13px;background:var(--surface-2);border:1px solid var(--border);border-radius:6px;padding:6px 12px;min-width:0">
              <input type="checkbox" data-checkbox-group="${escapeHtml(key)}" data-checkbox-value="${escapeHtml(c)}"${selected.has(c) ? ' checked' : ''}
                style="width:16px;height:16px;accent-color:var(--accent);flex-shrink:0">
              <span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${escapeHtml(c)}</span>
            </label>
          `).join('')}
        </div>
      </div>
    `;
  }

  _renderJson(key, value, label = key, desc = '', expected = 'any') {
    let serializedValue = this._invalidJsonValues?.get(key) ?? '';
    if (!this._invalidJsonValues?.has(key)) {
      const fallbackValue = expected === 'array' ? [] : {};
      try {
        serializedValue = JSON.stringify(value === undefined ? fallbackValue : value, null, 2);
      } catch {
        serializedValue = '';
      }
    }
    const inputId = configDomId(key, 'plugin_json_');
    const errorId = `${inputId}_error`;
    return `
      <div class="config-field plugin-json-field">
        <div class="config-field-header">
          <label class="config-field-label" for="${escapeHtml(inputId)}">${escapeHtml(label)}</label>
          ${desc ? `<span class="config-field-desc">${escapeHtml(desc)}</span>` : ''}
        </div>
        <textarea id="${escapeHtml(inputId)}" data-json-setting-key="${escapeHtml(key)}" data-json-expected="${escapeHtml(expected)}" aria-describedby="${escapeHtml(errorId)}" class="config-input plugin-code-input" rows="4" spellcheck="false">${escapeHtml(serializedValue)}</textarea>
        <span id="${escapeHtml(errorId)}" class="plugin-json-error" data-json-error="${escapeHtml(key)}" role="alert" hidden></span>
      </div>
    `;
  }

  _jsonErrorElement(container, key) {
    return Array.from(container.querySelectorAll('[data-json-error]')).find(
      element => element.dataset.jsonError === key,
    ) || null;
  }

  _setJsonError(container, key, message = '') {
    const error = this._jsonErrorElement(container, key);
    const input = Array.from(container.querySelectorAll('[data-json-setting-key], [data-obj-json]')).find(
      element => element.dataset.jsonSettingKey === key || element.dataset.jsonPath === key,
    );
    if (message) {
      this._jsonErrors.add(key);
      if (input && input.value.length <= MAX_JSON_EDITOR_CHARS) {
        this._invalidJsonValues.set(key, input.value);
      }
      if (error) {
        error.hidden = false;
        error.textContent = message;
      }
    } else {
      this._jsonErrors.delete(key);
      this._invalidJsonValues.delete(key);
      if (error) {
        error.hidden = true;
        error.textContent = '';
      }
    }
    if (input) input.setAttribute('aria-invalid', message ? 'true' : 'false');
  }

  _isSafeJsonValue(value, depth = 0, seen = new Set(), nodes = { count: 0 }) {
    nodes.count += 1;
    if (nodes.count > 4096 || depth > 16) return false;
    if (value === null || typeof value === 'string' || typeof value === 'boolean') return true;
    if (typeof value === 'number') return Number.isFinite(value);
    if (typeof value !== 'object' || seen.has(value)) return false;
    seen.add(value);
    if (Array.isArray(value)) {
      return value.every(item => this._isSafeJsonValue(item, depth + 1, seen, nodes));
    }
    return Object.keys(value).every(key => (
      isSafeConfigFieldName(key) && this._isSafeJsonValue(value[key], depth + 1, seen, nodes)
    ));
  }

  _syncJsonValues(container) {
    const liveKeys = new Set(Array.from(container.querySelectorAll(
      '[data-json-setting-key], [data-obj-json]',
    )).map(input => input.dataset.jsonSettingKey || input.dataset.jsonPath));
    for (const key of this._jsonErrors) {
      if (!liveKeys.has(key)) this._jsonErrors.delete(key);
    }
    for (const key of this._invalidJsonValues.keys()) {
      if (!liveKeys.has(key)) this._invalidJsonValues.delete(key);
    }
    container.querySelectorAll('[data-json-setting-key]').forEach(input => {
      const key = input.dataset.jsonSettingKey;
      if (!key) return;
      if (input.value.length > MAX_JSON_EDITOR_CHARS) {
        this._setJsonError(container, key, 'JSON 文本过长，请缩短后重试。');
        return;
      }
      let parsed;
      try {
        parsed = JSON.parse(input.value);
      } catch {
        this._setJsonError(container, key, '请输入有效的 JSON。');
        return;
      }
      if (!this._isSafeJsonValue(parsed)) {
        this._setJsonError(container, key, 'JSON 包含不安全的字段或结构过深。');
        return;
      }
      const expected = input.dataset.jsonExpected || 'any';
      if (expected === 'object' && (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed))) {
        this._setJsonError(container, key, '此项必须是 JSON 对象。');
        return;
      }
      if (expected === 'array' && !Array.isArray(parsed)) {
        this._setJsonError(container, key, '此项必须是 JSON 数组。');
        return;
      }
      this.settings[key] = parsed;
      this._setJsonError(container, key);
    });
  }

  _syncObjectJsonValue(input, container) {
    const key = input.dataset.objArrayKey;
    const field = input.dataset.objField;
    const rowKey = input.dataset.objRowKey || '';
    const index = this._objectRowIndex(key, rowKey, input.dataset.objIdx);
    const path = input.dataset.jsonPath || `${key}.${rowKey || index}.${field}`;
    if (!key || index < 0 || !field || !this.settings[key]?.[index]) return false;
    let parsed;
    try {
      parsed = JSON.parse(input.value);
    } catch {
      this._setJsonError(container, path, '请输入有效的 JSON。');
      return false;
    }
    if (!this._isSafeJsonValue(parsed)) {
      this._setJsonError(container, path, 'JSON 包含不安全的字段或结构过深。');
      return false;
    }
    if (input.dataset.jsonExpected === 'object' && (
      parsed === null || typeof parsed !== 'object' || Array.isArray(parsed)
    )) {
      this._setJsonError(container, path, '此项必须是 JSON 对象。');
      return false;
    }
    this.settings[key][index][field] = parsed;
    this._setJsonError(container, path);
    return true;
  }

  _syncObjectJsonValues(container) {
    container.querySelectorAll('[data-obj-json]').forEach(input => {
      this._syncObjectJsonValue(input, container);
    });
  }

  _formatKey(key) {
    return String(key ?? '').replace(/_/g, ' ').replace(/^\w/, c => c.toUpperCase());
  }

  _snapshotFormValues() {
    const container = this._formContainer();
    if (!container) return;

    this._syncJsonValues(container);
    this._syncObjectJsonValues(container);
    container.querySelectorAll('[data-setting-key]').forEach(input => {
      const key = input.dataset.settingKey;
      if (!key) return;
      if (input.type === 'checkbox') this.settings[key] = input.checked;
      else if (input.type === 'number') {
        const numberValue = parseConfigNumber(input.value);
        if (numberValue === undefined) delete this.settings[key];
        else this.settings[key] = numberValue;
      } else this.settings[key] = input.value;
    });

    container.querySelectorAll('[data-list-container]').forEach(listContainer => {
      const key = listContainer.dataset.listContainer;
      if (!key) return;
      const previous = Array.isArray(this.settings[key]) ? this.settings[key] : [];
      this.settings[key] = Array.from(listContainer.querySelectorAll('[data-list-key]'))
        .map((input, index) => (
          previous[index] !== undefined && String(previous[index]) === input.value
            ? previous[index]
            : input.value
        ));
    });

    container.querySelectorAll('[data-obj-array-key]').forEach(input => {
      const key = input.dataset.objArrayKey;
      const field = input.dataset.objField;
      const index = this._objectRowIndex(key, input.dataset.objRowKey, input.dataset.objIdx);
      if (!key || !isSafeConfigFieldName(field) || index < 0 || !this.settings[key]?.[index]) return;
      if (input.dataset.objJson !== undefined) return;
      if (input.dataset.objCheckbox !== undefined) {
        const checkboxValue = input.dataset.objCheckbox;
        let values = this.settings[key][index][field];
        if (!Array.isArray(values)) values = [];
        if (input.checked && !values.includes(checkboxValue)) values.push(checkboxValue);
        if (!input.checked) values = values.filter(value => value !== checkboxValue);
        this.settings[key][index][field] = values;
      } else if (input.dataset.objListIdx !== undefined) {
        const listIndex = Number.parseInt(input.dataset.objListIdx, 10);
        if (!Number.isInteger(listIndex) || listIndex < 0) return;
        const values = Array.isArray(this.settings[key][index][field])
          ? this.settings[key][index][field]
          : [];
        const previous = values[listIndex];
        values[listIndex] = previous !== undefined && String(previous) === input.value
          ? previous
          : input.value;
        this.settings[key][index][field] = values;
      } else if (input.type === 'checkbox') {
        this.settings[key][index][field] = input.checked;
      } else if (input.type === 'number') {
        const numberValue = parseConfigNumber(input.value);
        if (numberValue === undefined) delete this.settings[key][index][field];
        else this.settings[key][index][field] = numberValue;
      } else {
        this.settings[key][index][field] = input.value;
      }
    });

    container.querySelectorAll('[data-checkbox-group-container]').forEach(groupContainer => {
      const key = groupContainer.dataset.checkboxGroupContainer;
      if (!key) return;
      this.settings[key] = Array.from(groupContainer.querySelectorAll('[data-checkbox-group]'))
        .filter(checkbox => checkbox.checked)
        .map(checkbox => checkbox.dataset.checkboxValue);
    });

    container.querySelectorAll('[data-schedule-container]').forEach(scheduleContainer => {
      const key = scheduleContainer.dataset.scheduleContainer;
      if (!key) return;
      const schedules = [];
      scheduleContainer.querySelectorAll('[data-schedule-field]').forEach(input => {
        const index = Number.parseInt(input.dataset.scheduleIdx, 10);
        const field = input.dataset.scheduleField;
        if (!Number.isInteger(index) || !field) return;
        if (!schedules[index]) schedules[index] = { time: '22:00', duration: 1440 };
        schedules[index][field] = field === 'duration'
          ? Number.parseInt(input.value, 10) || 0
          : input.value;
      });
      this._scheduleData[key] = schedules.filter(Boolean);
    });
  }

  _bindEvents() {
    const container = this._formContainer();
    if (!container) return;

    container.querySelectorAll('[data-json-setting-key]').forEach(input => {
      input.addEventListener('input', () => this._syncJsonValues(container));
    });
    this._syncJsonValues(container);

    // 分组折叠
    container.querySelectorAll('.config-group-header').forEach(header => {
      header.addEventListener('click', () => {
        const group = header.closest('.config-group');
        const body = group.querySelector('.config-group-body');
        const toggle = header.querySelector('.config-group-toggle');
        const isCollapsed = body.style.display === 'none';
        body.style.display = isCollapsed ? '' : 'none';
        toggle.textContent = isCollapsed ? '▼' : '▶';
        toggle.style.transform = isCollapsed ? '' : 'rotate(-90deg)';
      });
    });

    container.querySelectorAll('[data-password-toggle]').forEach(btn => {
      btn.addEventListener('click', () => {
        const input = container.querySelector(`#${CSS.escape(btn.dataset.passwordToggle || '')}`);
        if (!input) return;
        const show = input.type === 'password';
        input.type = show ? 'text' : 'password';
        btn.textContent = show ? '👁' : '🙈';
      });
    });

    container.querySelectorAll('[data-list-add]').forEach(btn => {
      btn.addEventListener('click', () => {
        this._snapshotFormValues();
         const key = btn.dataset.listAdd;
        const escapedKey = escapeHtml(key);
        const listContainer = Array.from(container.querySelectorAll('[data-list-container]'))
           .find(candidate => candidate.dataset.listContainer === key);
        if (!listContainer) return;
        const idx = listContainer.querySelectorAll('[data-list-key]').length;
        const div = document.createElement('div');
        div.style.cssText = 'display:flex;gap:4px;margin-bottom:4px';
        div.innerHTML = `
          <input type="text" data-list-key="${escapedKey}" data-list-index="${idx}"
            style="background:var(--surface-2);color:var(--text);border:1px solid var(--border);border-radius:6px;padding:6px 8px;font-size:13px;flex:1">
          <button type="button" class="btn btn-sm" data-list-remove="${escapedKey}" style="padding:2px 8px">✕</button>
        `;
        listContainer.appendChild(div);
        div.querySelector('[data-list-remove]').addEventListener('click', () => {
          if (confirmDanger()) div.remove();
        });
      });
    });

    container.querySelectorAll('[data-list-remove]').forEach(btn => {
      btn.addEventListener('click', () => {
        if (confirmDanger()) btn.parentElement.remove();
      });
    });

    container.querySelectorAll('[data-schedule-add]').forEach(btn => {
      btn.addEventListener('click', () => {
        this._snapshotFormValues();
         const key = btn.dataset.scheduleAdd;
        if (!this._scheduleData[key]) this._scheduleData[key] = [];
        this._scheduleData[key].push({ time: '22:00', duration: 1440 });
        this.render();
      });
    });

    container.querySelectorAll('[data-schedule-remove]').forEach(btn => {
      btn.addEventListener('click', () => {
        const key = btn.dataset.scheduleRemoveKey;
        const idx = parseInt(btn.dataset.scheduleRemove, 10);
        if (this._scheduleData[key] && confirmDanger()) {
          this._scheduleData[key].splice(idx, 1);
          this.render();
        }
      });
    });

    container.querySelectorAll('[data-schedule-field]').forEach(inp => {
      inp.addEventListener('change', () => {
        const key = inp.dataset.scheduleKey;
        const idx = parseInt(inp.dataset.scheduleIdx, 10);
        const field = inp.dataset.scheduleField;
        if (this._scheduleData[key]?.[idx]) {
          this._scheduleData[key][idx][field] = field === 'duration' ? parseInt(inp.value, 10) : inp.value;
        }
      });
    });

    // Object Array 添加按钮
    container.querySelectorAll('[data-obj-array-add]').forEach(btn => {
      btn.addEventListener('click', () => {
        this._snapshotFormValues();
        const key = btn.dataset.objArrayAdd;
        const param = this.parameters.find(p => p.name === key);
        if (!param || !Array.isArray(param.fields)) return;

        const newItem = createObjectArrayItem(param.fields);
        if (!Array.isArray(this.settings[key])) this.settings[key] = [];
        this.settings[key].push(newItem);
        this.render();
      });
    });

    // Object Array 删除按钮
    container.querySelectorAll('[data-obj-array-remove]').forEach(btn => {
      btn.addEventListener('click', () => {
        const key = btn.dataset.objArrayRemove;
        const idx = this._objectRowIndex(key, btn.dataset.objRowKey, btn.dataset.objIdx);
        if (Array.isArray(this.settings[key]) && idx >= 0 && confirmDanger()) {
          this._snapshotFormValues();
          this.settings[key].splice(idx, 1);
          this.render();
        }
      });
    });

    this._syncObjectJsonValues(container);

    // Object Array 字段输入
    container.querySelectorAll('[data-obj-array-key]').forEach(inp => {
      const eventType = inp.type === 'checkbox' ? 'change' : 'input';
      inp.addEventListener(eventType, () => {
        const key = inp.dataset.objArrayKey;
        const idx = this._objectRowIndex(inp.dataset.objArrayKey, inp.dataset.objRowKey, inp.dataset.objIdx);
        const field = inp.dataset.objField;
        
        if (!isSafeConfigFieldName(field) || !this.settings[key]?.[idx]) return;
        
        if (inp.dataset.objJson !== undefined) {
          this._syncObjectJsonValue(inp, container);
        } else if (inp.dataset.objCheckbox !== undefined) {
          // checkbox_group 字段内的单个复选框
          const checkboxVal = inp.dataset.objCheckbox;
          let arr = this.settings[key][idx][field] || [];
          if (!Array.isArray(arr)) arr = [];
          if (inp.checked) {
            if (!arr.includes(checkboxVal)) arr.push(checkboxVal);
          } else {
            arr = arr.filter(v => v !== checkboxVal);
          }
          this.settings[key][idx][field] = arr;
        } else if (inp.dataset.objListIdx !== undefined) {
          // list 类型内的单个输入项
          const listIdx = parseInt(inp.dataset.objListIdx, 10);
          const arr = this.settings[key][idx][field] || [];
          if (!Array.isArray(arr)) return;
          arr[listIdx] = inp.value;
          this.settings[key][idx][field] = arr;
        } else if (inp.type === 'checkbox') {
          this.settings[key][idx][field] = inp.checked;
        } else if (inp.type === 'number') {
          const numberValue = parseConfigNumber(inp.value);
          if (numberValue === undefined) delete this.settings[key][idx][field];
          else this.settings[key][idx][field] = numberValue;
        } else {
          this.settings[key][idx][field] = inp.value;
        }
      });
    });

    // Object Array 内 List 添加按钮
    container.querySelectorAll('[data-obj-list-add]').forEach(btn => {
      btn.addEventListener('click', () => {
        this._snapshotFormValues();
        const key = btn.dataset.objListAdd;
        const idx = this._objectRowIndex(key, btn.dataset.objRowKey, btn.dataset.objIdx);
        const field = btn.dataset.objField;
        
        if (idx < 0 || !this.settings[key]?.[idx]) return;
        const arr = this.settings[key][idx][field];
        if (!Array.isArray(arr)) {
          this.settings[key][idx][field] = [''];
        } else {
          arr.push('');
        }
        this.render();
      });
    });

    // Object Array 内 List 删除按钮
    container.querySelectorAll('[data-obj-list-remove]').forEach(btn => {
      btn.addEventListener('click', () => {
        this._snapshotFormValues();
        const key = btn.dataset.objListRemove;
        const idx = this._objectRowIndex(key, btn.dataset.objRowKey, btn.dataset.objIdx);
        const field = btn.dataset.objField;
        const listIdx = parseInt(btn.dataset.objListIdx, 10);
        
        if (idx < 0 || !this.settings[key]?.[idx]) return;
        const arr = this.settings[key][idx][field];
        if (Array.isArray(arr) && Number.isInteger(listIdx) && listIdx >= 0 && confirmDanger()) {
          arr.splice(listIdx, 1);
          this.render();
        }
      });
    });

    // Checkbox Group
    container.querySelectorAll('[data-checkbox-group]').forEach(cb => {
      cb.addEventListener('change', () => {
        const key = cb.dataset.checkboxGroup;
        const val = cb.dataset.checkboxValue;
        if (!this.settings[key]) this.settings[key] = [];
        if (!Array.isArray(this.settings[key])) this.settings[key] = [];
        
        if (cb.checked) {
          if (!this.settings[key].includes(val)) this.settings[key].push(val);
        } else {
          this.settings[key] = this.settings[key].filter(v => v !== val);
        }
      });
    });

    // 数字调节按钮
    container.querySelectorAll('[data-spin-target]').forEach(btn => {
      btn.addEventListener('click', () => {
        const target = Array.from(container.querySelectorAll('input[data-setting-key]'))
          .find(input => input.dataset.settingKey === btn.dataset.spinTarget);
        if (!target) return;
        const dir = parseInt(btn.dataset.spinDir, 10);
        const step = parseFloat(target.step) || 1;
        const min = target.min !== '' ? parseFloat(target.min) : -Infinity;
        const max = target.max !== '' ? parseFloat(target.max) : Infinity;
        const cur = parseFloat(target.value) || 0;
        target.value = Math.min(max, Math.max(min, cur + step * dir));
        target.dispatchEvent(new Event('change', { bubbles: true }));
      });
    });

    // 初始化 ModelSelect 组件
    if (this._modelSelects) {
      this._modelSelects.forEach(ms => ms.destroy());
    }
    this._modelSelects = [];
    container.querySelectorAll('[data-model-select]').forEach(el => {
      const key = el.dataset.modelSelect;
      const value = el.dataset.modelValue || '';
      const options = this.modelChoices || [];
      
      // 如果当前值不在选项列表中，添加一个额外的选项
      const valueInChoices = options.some(o => o.value === value);
      const allOptions = [...options];
      if (value && !valueInChoices) {
        allOptions.unshift({
          value: value,
          label: String(value) + ' (当前配置)',
          tags: [],
        });
      }
      
      const ms = new ModelSelect({
        options: allOptions,
        value: value,
        ariaLabel: el.dataset.modelLabel || key,
        onChange: (val) => {
          this.settings[key] = val;
        },
      });
      ms.mount(el);
      this._modelSelects.push(ms);
    });
  }

  /**
   * 收集表单所有值
   * @returns {Object} 配置对象
   */
  collectValues() {
    const values = {};
    const container = this._formContainer();
    if (!container) return values;

    this._snapshotFormValues();
    if (this._jsonErrors.size) {
      throw new Error('参数中包含无效的 JSON。');
    }

    for (const [key, schedule] of Object.entries(this._scheduleData)) {
      if (Array.isArray(schedule)) values[key] = schedule;
    }

    container.querySelectorAll('[data-setting-key]').forEach(input => {
      const key = input.dataset.settingKey;
      if (input.type === 'checkbox') {
        values[key] = input.checked;
      } else if (input.type === 'number') {
        const numberValue = parseConfigNumber(input.value);
        if (numberValue !== undefined) values[key] = numberValue;
      } else if (input.tagName === 'SELECT') {
        values[key] = input.value || '';
      } else {
        values[key] = input.value;
      }
    });

    // Containers keep their key even after the last item has been removed, so
    // an explicit empty list is persisted instead of merged away. Untouched
    // non-string elements survive through the snapshot comparison.
    container.querySelectorAll('[data-list-container]').forEach(listContainer => {
      const key = listContainer.dataset.listContainer;
      if (key) values[key] = Array.isArray(this.settings[key]) ? this.settings[key] : [];
    });

    // 收集 object_array 值（从 settings 中同步）
    for (const param of this.parameters) {
      if (configParameterType(param) === 'object_array' && Array.isArray(param.fields)) {
        if (Array.isArray(this.settings[param.name])) {
          values[param.name] = this.settings[param.name];
        }
      }
    }

    // 收集顶层 JSON/object 值，避免通用表单丢失结构化参数。
    container.querySelectorAll('[data-json-setting-key]').forEach(input => {
      const key = input.dataset.jsonSettingKey;
      if (key && Object.prototype.hasOwnProperty.call(this.settings, key)) {
        values[key] = this.settings[key];
      }
    });

    // 收集 checkbox_group 值
    const checkboxInputs = Array.from(container.querySelectorAll('[data-checkbox-group]'));
    const checkboxGroups = new Set(checkboxInputs.map(cb => cb.dataset.checkboxGroup));
    checkboxGroups.forEach(key => {
      const checked = checkboxInputs.filter(
        cb => cb.dataset.checkboxGroup === key && cb.checked
      );
      values[key] = checked.map(cb => cb.dataset.checkboxValue);
    });

    // 收集 ModelSelect 值（保留 provider 前缀，避免同名模型歧义）
    if (this._modelSelects) {
      this._modelSelects.forEach(ms => {
        const el = ms._el;
        if (el) {
          const key = el.dataset.modelSelect;
          if (key && ms.value) {
            values[key] = ms.value;
          }
        }
      });
    }

    return values;
  }
}

export class ModelSelect {
  static _instances = new Set();

  static closeAll(except) {
    for (const inst of ModelSelect._instances) {
      if (inst !== except && inst._open) inst._closeDropdown(false);
    }
  }

  constructor({ options = [], value = '', onChange = null, placeholder = '请选择模型…', ariaLabel = '选择模型' }) {
    this.options = options;
    this.value = value;
    this.onChange = onChange;
    this.placeholder = placeholder;
    this.ariaLabel = ariaLabel;
    this._open = false;
    this._activeIndex = -1;
    this._el = null;
    this._onDocClick = this._onDocClick.bind(this);
  }

  mount(container) {
    this._el = typeof container === 'string' ? document.getElementById(container) : container;
    this.render();
    ModelSelect._instances.add(this);
    document.addEventListener('click', this._onDocClick);
  }

  destroy() {
    ModelSelect._instances.delete(this);
    document.removeEventListener('click', this._onDocClick);
    this._open = false;
    this._activeIndex = -1;
    this._el = null;
  }

  setValue(val) {
    this.value = val;
    this.render();
  }

  _onDocClick(e) {
    if (this._el?.contains && !this._el.contains(e.target)) this._closeDropdown(false);
  }

  _selectedLabel() {
    const opt = this.options.find(o => o.value === this.value);
    if (!opt) return escapeHtml(this.placeholder);
    const tags = (Array.isArray(opt.tags) ? opt.tags : [])
      .map(t => `<span class="cap-tag-inline">${escapeHtml(t)}</span>`)
      .join('');
    return `${escapeHtml(opt.label)}${tags ? ' ' + tags : ''}`;
  }

  _syncTriggerText() {
    if (!this._el) return;
    const textEl = this._el.querySelector('.msel-text');
    const trigger = this._el.querySelector('.msel-trigger');
    if (!textEl) return;
    const sel = this.options.find(o => o.value === this.value);
    textEl.innerHTML = sel ? this._renderTriggerContent(sel) : escapeHtml(this.placeholder);
    if (trigger) {
       trigger.classList.toggle('msel-placeholder', !sel);
       trigger.setAttribute('aria-label', sel
         ? `${this.ariaLabel}：${String(sel.label ?? '')}`
         : `${this.ariaLabel}：${this.placeholder}`);
     }
  }

  _renderTriggerContent(opt) {
    const tags = (Array.isArray(opt.tags) ? opt.tags : [])
      .map(t => `<span class="cap-tag-inline">${escapeHtml(t)}</span>`)
      .join('');
    return `<span class="msel-label">${escapeHtml(opt.label)}</span>${tags}`;
  }

  render() {
    if (!this._el) return;
    const sel = this.options.find(o => o.value === this.value);
    const triggerHtml = sel ? this._renderTriggerContent(sel) : escapeHtml(this.placeholder);
    const controlId = configDomId(this._el.dataset.modelSelect || 'model', 'plugin_model_');
    const listId = `${controlId}_list`;
    this._el.innerHTML = `
      <div class="msel-wrap">
        <button type="button" class="msel-trigger${this._open ? ' msel-open' : ''}${!sel ? ' msel-placeholder' : ''}" role="combobox" aria-haspopup="listbox" aria-expanded="${String(this._open)}" aria-controls="${escapeHtml(listId)}" aria-label="${escapeHtml(this.ariaLabel)}">
          <span class="msel-text">${triggerHtml}</span>
          <span class="msel-arrow">▾</span>
        </button>
        <div class="msel-dropdown" style="display:${this._open ? 'block' : 'none'}">
          <input type="text" class="msel-search" placeholder="搜索模型…" aria-label="${escapeHtml(this.ariaLabel)}：搜索模型">
          <div id="${escapeHtml(listId)}" class="msel-list" role="listbox" aria-label="${escapeHtml(this.ariaLabel)}：可用模型"></div>
        </div>
      </div>
    `;
    const trigger = this._el.querySelector('.msel-trigger');
    const searchInput = this._el.querySelector('.msel-search');
    const open = (focus = 'search') => {
      ModelSelect.closeAll(this);
      this._open = true;
      if (searchInput) { searchInput.value = ''; }
      this._renderDropdown();
      this._renderList('');
      if (focus === 'option') this._focusOption(0);
      else searchInput?.focus();
    };
    const close = (focusTrigger = false) => {
      this._closeDropdown(focusTrigger);
      if (!focusTrigger) trigger.focus();
    };
    trigger.addEventListener('click', (e) => {
      e.stopPropagation();
      if (this._open) close();
      else open();
    });
    trigger.addEventListener('keydown', (event) => {
      if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
        event.preventDefault();
        if (!this._open) open('option');
        else this._focusOption(event.key === 'ArrowUp' ? this._filteredOptions.length - 1 : 0);
      } else if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        if (!this._open) open();
        else searchInput?.focus();
      } else if (event.key === 'Escape' && this._open) {
        event.preventDefault();
        this._closeDropdown(true);
      }
    });
    this._renderList('');
    if (searchInput) {
      searchInput.addEventListener('input', () => this._renderList(searchInput.value));
      searchInput.addEventListener('click', (e) => e.stopPropagation());
      searchInput.addEventListener('keydown', (event) => {
        if (event.key === 'Escape') {
          event.preventDefault();
          this._closeDropdown(true);
        } else if (event.key === 'ArrowDown') {
          event.preventDefault();
          this._focusOption(0);
        } else if (event.key === 'ArrowUp') {
          event.preventDefault();
          this._focusOption(this._filteredOptions.length - 1);
        } else if (event.key === 'Enter' && this._activeIndex >= 0) {
          event.preventDefault();
          this._selectOption(this._filteredOptions[this._activeIndex].value);
        }
      });
    }
    this._el.querySelector('.msel-wrap')?.addEventListener('focusout', (event) => {
      const next = event.relatedTarget;
      if (this._open && (!next || !this._el.contains(next))) this._closeDropdown(false);
    });
    this._syncTriggerText();
  }

  _closeDropdown(focusTrigger = false) {
    this._open = false;
    this._activeIndex = -1;
    this._renderDropdown();
    this._syncTriggerText();
    if (focusTrigger) this._el?.querySelector('.msel-trigger')?.focus();
  }

  _focusOption(index) {
    const options = Array.from(this._el?.querySelectorAll('[role="option"]') || []);
    if (!options.length) return;
    this._activeIndex = Math.max(0, Math.min(index, options.length - 1));
    options.forEach((option, optionIndex) => {
      option.classList.toggle('msel-highlighted', optionIndex === this._activeIndex);
      option.tabIndex = optionIndex === this._activeIndex ? 0 : -1;
    });
    options[this._activeIndex].focus();
  }

  _selectOption(value) {
    this.value = value;
    this._closeDropdown(false);
    this.render();
    if (this.onChange) this.onChange(this.value);
    this._el?.querySelector('.msel-trigger')?.focus();
  }

  _renderDropdown() {
    const dd = this._el.querySelector('.msel-dropdown');
    const trigger = this._el.querySelector('.msel-trigger');
    if (!dd || !trigger) return;
    if (this._open) {
      const rect = trigger.getBoundingClientRect();
      const spaceBelow = window.innerHeight - rect.bottom;
      const spaceAbove = rect.top;
      if (spaceBelow < 280 && spaceAbove > spaceBelow) {
        dd.style.bottom = '100%';
        dd.style.top = 'auto';
        dd.style.marginBottom = '4px';
        dd.style.marginTop = '0';
      } else {
        dd.style.top = '100%';
        dd.style.bottom = 'auto';
        dd.style.marginTop = '4px';
        dd.style.marginBottom = '0';
      }
      dd.style.display = 'block';
    } else {
      dd.style.display = 'none';
      this._activeIndex = -1;
    }
    trigger.classList.toggle('msel-open', this._open);
    trigger.setAttribute('aria-expanded', String(this._open));
    if (!this._open) trigger.removeAttribute('aria-activedescendant');
  }

  _renderList(query) {
    const list = this._el.querySelector('.msel-list');
    if (!list) return;
    const q = String(query ?? '').trim().toLowerCase();
    const filtered = this.options.filter(o => {
      if (!q) return true;
      const label = String(o.label ?? '').toLowerCase();
      const value = String(o.value ?? '').toLowerCase();
      return label.includes(q) || value.includes(q);
    });
    this._filteredOptions = filtered;
    this._activeIndex = -1;
    list.replaceChildren();
    if (!filtered.length) {
      const empty = document.createElement('div');
      empty.className = 'msel-empty';
      empty.textContent = '无匹配模型';
      list.appendChild(empty);
      return;
    }
    filtered.forEach((option, index) => {
      const item = document.createElement('div');
      item.className = `msel-option${option.value === this.value ? ' msel-active' : ''}`;
      item.dataset.value = String(option.value ?? '');
      item.id = `${configDomId(this._el.dataset.modelSelect || 'model', 'plugin_model_option_')}_${index}`;
      item.setAttribute('role', 'option');
      item.setAttribute('aria-selected', String(option.value === this.value));
      item.tabIndex = -1;
      item.textContent = String(option.label ?? '');
      item.addEventListener('click', event => {
        event.stopPropagation();
        this._selectOption(item.dataset.value);
      });
      item.addEventListener('keydown', event => {
        if (event.key === 'ArrowDown') {
          event.preventDefault();
          this._focusOption(index + 1);
        } else if (event.key === 'ArrowUp') {
          event.preventDefault();
          if (index === 0) this._el.querySelector('.msel-search')?.focus();
          else this._focusOption(index - 1);
        } else if (event.key === 'Home') {
          event.preventDefault();
          this._focusOption(0);
        } else if (event.key === 'End') {
          event.preventDefault();
          this._focusOption(filtered.length - 1);
        } else if (event.key === 'Escape') {
          event.preventDefault();
          this._closeDropdown(true);
        } else if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          this._selectOption(item.dataset.value);
        }
      });
      list.appendChild(item);
    });
  }
}
