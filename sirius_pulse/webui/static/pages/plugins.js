import { store } from '../store.js';
import { get, post, put } from '../app.js';
import {
  toast,
  flashSuccess,
  confirmDanger,
  DynamicConfigForm,
  ModelSelect,
} from '../components.js';
import { createScopedPage } from '../page-context.js';

const scopedPage = createScopedPage();

export function dispose() {
  closeModal();
  scopedPage.use(null, null);
}
const $ = scopedPage.$;

let currentModal = null;
let configForm = null;

function isActiveModal(overlay) {
  return currentModal === overlay && Boolean(overlay?.isConnected);
}

function modalElement(id, overlay = currentModal) {
  return overlay?.querySelector(`#${id}`) || null;
}

function escapeHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function safeDomId(value, prefix = 'plugin_pwd_') {
  const encoded = Array.from(String(value ?? ''))
    .map(char => char.codePointAt(0).toString(16))
    .join('_');
  return `${prefix}${encoded || 'empty'}`;
}

function pluginPath(name, suffix = '') {
  return `/plugins/${encodeURIComponent(String(name ?? ''))}${suffix}`;
}

function resolveModelValue(value, choices) {
  const rawValue = String(value ?? '');
  if (!rawValue || !Array.isArray(choices)) return rawValue;
  const exact = choices.find(choice => String(choice?.value ?? '') === rawValue);
  if (exact) return String(exact.value);
  if (rawValue.includes('/')) return rawValue;
  const matches = choices.filter(choice => String(choice?.value ?? '').endsWith(`/${rawValue}`));
  return matches.length === 1 ? String(matches[0].value) : rawValue;
}

class SafeModelSelect extends ModelSelect {
  _appendTags(parent, tags) {
    if (!Array.isArray(tags)) return;
    tags.forEach(tag => {
      const tagEl = document.createElement('span');
      tagEl.className = 'cap-tag-inline';
      tagEl.textContent = String(tag ?? '');
      parent.appendChild(tagEl);
    });
  }

  _syncTriggerText() {
    if (!this._el) return;
    const textEl = this._el.querySelector('.msel-text');
    const trigger = this._el.querySelector('.msel-trigger');
    if (!textEl) return;

    textEl.replaceChildren();
    const selected = this.options.find(option => option.value === this.value);
    if (selected) {
      const label = document.createElement('span');
      label.className = 'msel-label';
      label.textContent = String(selected.label ?? '');
      textEl.appendChild(label);
      this._appendTags(textEl, selected.tags);
    } else {
      textEl.textContent = String(this.placeholder ?? '');
    }
    if (trigger) trigger.classList.toggle('msel-placeholder', !selected);
  }

  render() {
    if (!this._el) return;
    const selected = this.options.find(option => option.value === this.value);
    this._el.replaceChildren();

    const wrap = document.createElement('div');
    wrap.className = 'msel-wrap';
    const trigger = document.createElement('button');
    trigger.type = 'button';
    trigger.className = `msel-trigger${this._open ? ' msel-open' : ''}${selected ? '' : ' msel-placeholder'}`;
    const text = document.createElement('span');
    text.className = 'msel-text';
    const arrow = document.createElement('span');
    arrow.className = 'msel-arrow';
    arrow.textContent = '▾';
    trigger.append(text, arrow);

    const dropdown = document.createElement('div');
    dropdown.className = 'msel-dropdown';
    dropdown.style.display = this._open ? 'block' : 'none';
    const search = document.createElement('input');
    search.type = 'text';
    search.className = 'msel-search';
    search.placeholder = '搜索模型…';
    const list = document.createElement('div');
    list.className = 'msel-list';
    dropdown.append(search, list);
    wrap.append(trigger, dropdown);
    this._el.appendChild(wrap);
    this._syncTriggerText();

    trigger.addEventListener('click', (e) => {
      e.stopPropagation();
      const willOpen = !this._open;
      ModelSelect.closeAll(this);
      this._open = willOpen;
      this._renderDropdown();
      if (this._open) {
        search.value = '';
        search.focus();
      }
    });
    search.addEventListener('input', () => this._renderList(search.value));
    search.addEventListener('click', (e) => e.stopPropagation());
    this._renderList('');
  }

  _renderList(query) {
    const list = this._el?.querySelector('.msel-list');
    if (!list) return;
    const q = String(query ?? '').trim().toLowerCase();
    const filtered = this.options.filter(option => {
      if (!q) return true;
      const label = String(option.label ?? '').toLowerCase();
      const value = String(option.value ?? '').toLowerCase();
      return label.includes(q) || value.includes(q);
    });

    list.replaceChildren();
    if (!filtered.length) {
      const empty = document.createElement('div');
      empty.className = 'msel-empty';
      empty.textContent = '无匹配模型';
      list.appendChild(empty);
      return;
    }

    filtered.forEach(option => {
      const item = document.createElement('div');
      item.className = `msel-option${option.value === this.value ? ' msel-active' : ''}`;
      item.dataset.value = String(option.value ?? '');
      const label = document.createElement('span');
      label.className = 'msel-option-label';
      label.textContent = String(option.label ?? '');
      item.appendChild(label);
      if (Array.isArray(option.tags) && option.tags.length) {
        const tags = document.createElement('span');
        tags.className = 'msel-option-tags';
        this._appendTags(tags, option.tags);
        item.appendChild(tags);
      }
      item.addEventListener('click', (e) => {
        e.stopPropagation();
        this.value = option.value;
        this._open = false;
        this.render();
        if (this.onChange) this.onChange(this.value);
      });
      list.appendChild(item);
    });
  }
}

class SafeDynamicConfigForm extends DynamicConfigForm {
  async init() {
    await super.init();
    this._rawModelChoices = Array.isArray(this.modelChoices) ? this.modelChoices : [];
  }

  _buildForm(settings) {
    const renderedKeys = new Set();
    const groups = new Map();
    const ungrouped = [];

    for (const param of this.parameters || []) {
      if (!param || typeof param !== 'object') continue;
      const key = String(param.name ?? '');
      const value = settings[key];
      if (renderedKeys.has(key)) continue;
      renderedKeys.add(key);

      const type = param.type || 'str';
      const desc = param.description || '';
      const defaultVal = param.default;
      const required = Boolean(param.required);
      const group = String(param.group ?? '');
      let fieldHtml = '';

      if (type === 'model') {
        const modelValue = value || defaultVal || '';
        fieldHtml = this.modelChoices?.length
          ? this._renderModelSelect(key, modelValue, desc, required)
          : this._renderText(key, param.name, modelValue, defaultVal, desc, required);
      } else if (type === 'password' || type === 'secret') {
        fieldHtml = this._renderPassword(key, param.name, value, defaultVal, desc, required);
      } else if (type === 'boolean' || type === 'bool') {
        fieldHtml = this._renderCheckbox(key, param.name, value, desc);
      } else if (type === 'int' || type === 'float' || type === 'number') {
        fieldHtml = this._renderNumber(
          key,
          param.name,
          value,
          defaultVal,
          desc,
          required,
          param.minimum,
          param.maximum,
          type === 'int' ? 1 : 'any',
        );
      } else if (type === 'string' || type === 'str') {
        fieldHtml = this._renderText(key, param.name, value, defaultVal, desc, required);
      } else if (type === 'list' || type === 'array') {
        fieldHtml = this._renderList(key, param.name, value, defaultVal, desc);
      } else if (type === 'object_array' && Array.isArray(param.fields)) {
        fieldHtml = this._renderObjectArray(key, param.name, value, defaultVal, desc, param.fields);
      } else if (type === 'checkbox_group' && Array.isArray(param.choices)) {
        fieldHtml = this._renderCheckboxGroup(key, param.name, value, defaultVal, desc, param.choices);
      }

      if (!fieldHtml) continue;
      if (group) {
        if (!groups.has(group)) groups.set(group, []);
        groups.get(group).push(fieldHtml);
      } else {
        ungrouped.push(fieldHtml);
      }
    }

    for (const [key, value] of Object.entries(settings || {})) {
      if (renderedKeys.has(key)) continue;
      renderedKeys.add(key);
      const schedule = this._scheduleData?.[key];
      const isSchedule = Array.isArray(schedule) || (
        Array.isArray(value) && value.length > 0 &&
        typeof value[0] === 'object' && value[0] !== null &&
        'time' in value[0] && 'duration' in value[0]
      );
      const fieldHtml = isSchedule
        ? this._renderSchedule(key, Array.isArray(schedule) ? schedule : value)
        : this._renderJson(key, value);
      ungrouped.push(fieldHtml);
    }

    const sections = [];
    if (ungrouped.length) {
      sections.push(`<div class="config-fields">${ungrouped.join('')}</div>`);
    }
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
    const resolvedValue = resolveModelValue(value, this.modelChoices);
    return `
      <div class="config-field">
        <div class="config-field-header">
          <label class="config-field-label">${escapeHtml(key)}${required ? '<span class="config-required">*</span>' : ''}</label>
          ${desc ? `<span class="config-field-desc">${escapeHtml(desc)}</span>` : ''}
        </div>
        <div data-model-select="${escapeHtml(key)}" data-model-value="${escapeHtml(resolvedValue)}"></div>
      </div>
    `;
  }

  _renderPassword(key, label, value, defaultVal, desc, required) {
    // Plugin secrets are deliberately environment/secret-manager only. Never
    // render a stored value or offer an editor that the API will reject.
    return `
      <div class="config-field">
        <div class="config-field-header">
          <label class="config-field-label">${escapeHtml(label)}${required ? '<span class="config-required">*</span>' : ''}</label>
          ${desc ? `<span class="config-field-desc">${escapeHtml(desc)}</span>` : ''}
        </div>
        <div class="config-input" role="note" style="color:var(--text-3);background:var(--surface-2);cursor:default">
          由环境变量或受支持的 Secret 管理器提供，不能在此保存。
        </div>
      </div>
    `;
  }

  _renderCheckbox(key, label, value, desc) {
    return `
      <div class="config-field">
        <label style="display:flex;align-items:center;gap:10px;cursor:pointer">
          <input type="checkbox" data-setting-key="${escapeHtml(key)}"${value ? ' checked' : ''}
            style="width:18px;height:18px;accent-color:var(--accent)">
          <div>
            <span style="font-weight:500">${escapeHtml(label)}</span>
            ${desc ? `<div style="color:var(--text-3);font-size:12px;margin-top:2px">${escapeHtml(desc)}</div>` : ''}
          </div>
        </label>
      </div>
    `;
  }

  _renderNumber(key, label, value, defaultVal, desc, required, minimum, maximum, step) {
    const inputId = safeDomId(key, 'plugin_number_');
    const hasMinimum = minimum !== null && minimum !== undefined && minimum !== '';
    const hasMaximum = maximum !== null && maximum !== undefined && maximum !== '';
    const minAttr = hasMinimum && Number.isFinite(Number(minimum)) ? ` min="${escapeHtml(minimum)}"` : '';
    const maxAttr = hasMaximum && Number.isFinite(Number(maximum)) ? ` max="${escapeHtml(maximum)}"` : '';
    const stepAttr = step !== undefined && step !== null ? ` step="${escapeHtml(step)}"` : '';
    return `
      <div class="config-field">
        <div class="config-field-header">
          <label class="config-field-label">${escapeHtml(label)}${required ? '<span class="config-required">*</span>' : ''}</label>
          ${desc ? `<span class="config-field-desc">${escapeHtml(desc)}</span>` : ''}
        </div>
        <div class="number-input-group">
          <button type="button" class="number-spin-btn" data-spin-target="${escapeHtml(inputId)}" data-spin-dir="-1">−</button>
          <input type="number" id="${escapeHtml(inputId)}" data-setting-key="${escapeHtml(key)}" value="${escapeHtml(value ?? defaultVal ?? 0)}"${minAttr}${maxAttr}${stepAttr} class="config-input">
          <button type="button" class="number-spin-btn" data-spin-target="${escapeHtml(inputId)}" data-spin-dir="1">+</button>
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
    const listId = safeDomId(key, 'plugin_list_');
    return `
      <div class="config-field">
        <div class="config-field-header">
          <label class="config-field-label">${escapeHtml(label)}</label>
          ${desc ? `<span class="config-field-desc">${escapeHtml(desc)}</span>` : ''}
        </div>
        <div id="${escapeHtml(listId)}" data-list-container="${escapeHtml(key)}" style="display:flex;flex-direction:column;gap:6px;margin-bottom:8px">
          ${listVal.map((item, index) => `
            <div style="display:flex;gap:6px;align-items:center">
              <input type="text" value="${escapeHtml(item)}" data-list-key="${escapeHtml(key)}" data-list-index="${index}" class="config-input" style="flex:1">
              <button type="button" class="btn btn-sm btn-ghost" data-list-remove="${escapeHtml(key)}" style="padding:6px 8px;color:var(--danger)">✕</button>
            </div>
          `).join('')}
        </div>
        <button type="button" class="btn btn-sm btn-ghost" data-list-add="${escapeHtml(key)}" style="padding:4px 12px;font-size:12px;color:var(--accent)">+ 添加</button>
      </div>
    `;
  }

  _renderSchedule(key, value) {
    const escapedKey = escapeHtml(key);
    const scheduleId = safeDomId(key, 'plugin_schedule_');
    return `
      <div class="config-field">
        <div class="config-field-header">
          <label class="config-field-label">${escapeHtml(this._formatKey(key))}</label>
        </div>
        <div id="${escapeHtml(scheduleId)}" data-schedule-container="${escapedKey}" style="display:flex;flex-direction:column;gap:8px;margin-bottom:8px">
          ${value.map((schedule, index) => {
            const durationId = safeDomId(`${key}_${index}`, 'plugin_schedule_duration_');
            return `
            <div style="display:flex;gap:8px;align-items:center;padding:8px;background:var(--surface-2);border-radius:6px">
              <input type="time" value="${escapeHtml(schedule?.time || '22:00')}" data-schedule-key="${escapedKey}" data-schedule-idx="${index}" data-schedule-field="time"
                class="config-input" style="width:auto">
              <span style="color:var(--text-3);font-size:12px;white-space:nowrap">时长</span>
              <div class="number-input-group" style="max-width:120px">
                <button type="button" class="number-spin-btn" data-spin-target="${escapeHtml(durationId)}" data-spin-dir="-1">−</button>
                <input type="number" id="${escapeHtml(durationId)}" value="${escapeHtml(schedule?.duration || 1440)}" min="1" max="10080" data-schedule-key="${escapedKey}" data-schedule-idx="${index}" data-schedule-field="duration"
                  class="config-input">
                <button type="button" class="number-spin-btn" data-spin-target="${escapeHtml(durationId)}" data-spin-dir="1">+</button>
              </div>
              <span style="color:var(--text-3);font-size:12px">分钟</span>
              <button type="button" class="btn btn-sm btn-ghost" data-schedule-remove="${index}" data-schedule-remove-key="${escapedKey}" style="margin-left:auto;padding:4px 8px;color:var(--danger)">✕</button>
            </div>
          `;
          }).join('')}
        </div>
        <button type="button" class="btn btn-sm btn-ghost" data-schedule-add="${escapedKey}" style="padding:4px 12px;font-size:12px;color:var(--accent)">+ 添加定时</button>
      </div>
    `;
  }

  _renderObjectArray(key, label, value, defaultVal, desc, fields) {
    const items = Array.isArray(value) ? value : (Array.isArray(defaultVal) ? defaultVal : []);
    const escapedKey = escapeHtml(key);
    const fieldDefs = Array.isArray(fields) ? fields : [];

    const renderFieldInput = (field, fieldValue, index) => {
      const fieldName = String(field?.name ?? '');
      const fieldType = field?.type || 'str';
      const escapedFieldName = escapeHtml(fieldName);
      const listId = safeDomId(`${key}_${index}_${fieldName}`, 'plugin_object_list_');
      const currentValue = fieldValue ?? field?.default ?? '';

      if (fieldType === 'checkbox_group' && Array.isArray(field?.choices)) {
        const selected = new Set(Array.isArray(currentValue) ? currentValue : []);
        return `<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:6px;flex:1">
          ${field.choices.map(choice => `
            <label style="display:flex;align-items:center;gap:4px;cursor:pointer;font-size:12px;background:var(--surface-3);border:1px solid var(--border);border-radius:4px;padding:3px 8px;min-width:0">
              <input type="checkbox" data-obj-array-key="${escapedKey}" data-obj-idx="${index}" data-obj-field="${escapedFieldName}" data-obj-checkbox="${escapeHtml(choice)}"${selected.has(choice) ? ' checked' : ''}
                style="flex-shrink:0">
              <span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${escapeHtml(choice)}</span>
            </label>
          `).join('')}
        </div>`;
      }
      if (fieldType === 'list' || fieldType === 'array') {
        const listItems = Array.isArray(currentValue) ? currentValue : [];
        return `<div style="flex:1">
          <div id="${escapeHtml(listId)}" data-obj-list-container style="display:flex;flex-direction:column;gap:4px;margin-bottom:4px">
            ${listItems.map((item, listIndex) => `
              <div style="display:flex;gap:4px;align-items:center">
                <input type="text" value="${escapeHtml(item)}" data-obj-array-key="${escapedKey}" data-obj-idx="${index}" data-obj-field="${escapedFieldName}" data-obj-list-idx="${listIndex}" class="config-input" style="flex:1;font-size:12px;padding:4px 6px">
                <button type="button" class="btn btn-sm btn-ghost" data-obj-list-remove="${escapedKey}" data-obj-idx="${index}" data-obj-field="${escapedFieldName}" data-obj-list-idx="${listIndex}" style="padding:2px 6px;color:var(--danger);font-size:11px">✕</button>
              </div>
            `).join('')}
          </div>
          <button type="button" class="btn btn-sm btn-ghost" data-obj-list-add="${escapedKey}" data-obj-idx="${index}" data-obj-field="${escapedFieldName}" style="padding:2px 8px;font-size:11px;color:var(--accent)">+ 添加</button>
        </div>`;
      }
      if (fieldType === 'password' || fieldType === 'secret') {
        return `<div class="config-input" role="note" style="flex:1;color:var(--text-3);background:var(--surface-3);cursor:default">由环境变量或 Secret 管理器提供</div>`;
      }
      if (fieldType === 'boolean' || fieldType === 'bool') {
        return `<label style="display:flex;align-items:center;gap:6px;flex:1;padding-top:6px">
          <input type="checkbox" data-obj-array-key="${escapedKey}" data-obj-idx="${index}" data-obj-field="${escapedFieldName}"${currentValue ? ' checked' : ''}>
          <span style="font-size:12px;color:var(--text-2)">启用</span>
        </label>`;
      }
      if (fieldType === 'int' || fieldType === 'float' || fieldType === 'number') {
        return `<input type="number" data-obj-array-key="${escapedKey}" data-obj-idx="${index}" data-obj-field="${escapedFieldName}" value="${escapeHtml(currentValue)}" class="config-input" style="flex:1">`;
      }
      return `<input type="text" data-obj-array-key="${escapedKey}" data-obj-idx="${index}" data-obj-field="${escapedFieldName}" value="${escapeHtml(currentValue)}" placeholder="${escapeHtml(field?.description || '')}" class="config-input" style="flex:1">`;
    };

    return `
      <div class="config-field">
        <div class="config-field-header">
          <label class="config-field-label">${escapeHtml(label)}</label>
          ${desc ? `<span class="config-field-desc">${escapeHtml(desc)}</span>` : ''}
        </div>
        <div id="${escapeHtml(safeDomId(key, 'plugin_object_array_'))}" data-obj-array-container="${escapedKey}" style="display:flex;flex-direction:column;gap:8px;margin-bottom:8px">
          ${items.map((item, index) => `
            <div style="border:1px solid var(--border);border-radius:8px;padding:12px;background:var(--surface-2)" data-obj-array-item="${escapedKey}" data-obj-idx="${index}">
              <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
                <span style="font-size:12px;color:var(--text-3)">#${index + 1}</span>
                <button type="button" class="btn btn-sm btn-ghost" data-obj-array-remove="${escapedKey}" data-obj-idx="${index}" style="padding:2px 8px;color:var(--danger)">✕</button>
              </div>
              <div style="display:grid;gap:8px">
                ${fieldDefs.map(field => `
                  <div style="display:flex;align-items:flex-start;gap:8px">
                    <label style="font-size:12px;color:var(--text-3);min-width:80px;padding-top:6px;flex-shrink:0">${escapeHtml(field?.name)}</label>
                    ${renderFieldInput(field, item?.[field?.name], index)}
                  </div>
                `).join('')}
              </div>
            </div>
          `).join('')}
        </div>
        <button type="button" class="btn btn-sm btn-ghost" data-obj-array-add="${escapedKey}" style="padding:4px 12px;font-size:12px;color:var(--accent)">+ 添加</button>
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
        <div data-checkbox-group-container="${escapeHtml(key)}" style="display:grid;grid-template-columns:repeat(3,1fr);gap:8px">
          ${(Array.isArray(choices) ? choices : []).map(choice => `
            <label style="display:flex;align-items:center;gap:6px;cursor:pointer;font-size:13px;background:var(--surface-2);border:1px solid var(--border);border-radius:6px;padding:6px 12px;min-width:0">
              <input type="checkbox" data-checkbox-group="${escapeHtml(key)}" data-checkbox-value="${escapeHtml(choice)}"${selected.has(choice) ? ' checked' : ''}
                style="width:16px;height:16px;accent-color:var(--accent);flex-shrink:0">
              <span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${escapeHtml(choice)}</span>
            </label>
          `).join('')}
        </div>
      </div>
    `;
  }

  _renderJson(key, value) {
    const serializedValue = typeof value === 'object' ? JSON.stringify(value) : value;
    return `
      <div class="config-field">
        <div class="config-field-header">
          <label class="config-field-label">${escapeHtml(key)}</label>
        </div>
        <input type="text" data-setting-key="${escapeHtml(key)}" value="${escapeHtml(serializedValue)}" class="config-input">
      </div>
    `;
  }

  _formContainer() {
    return document.getElementById(this.containerId);
  }

  _snapshotFormValues() {
    const container = this._formContainer();
    if (!container) return;

    container.querySelectorAll('[data-setting-key]').forEach(input => {
      const key = input.dataset.settingKey;
      if (!key) return;
      if (input.type === 'checkbox') this.settings[key] = input.checked;
      else if (input.type === 'number') this.settings[key] = parseFloat(input.value) || 0;
      else this.settings[key] = input.value;
    });

    container.querySelectorAll('[data-list-container]').forEach(listContainer => {
      const key = listContainer.dataset.listContainer;
      if (!key) return;
      this.settings[key] = Array.from(listContainer.querySelectorAll('[data-list-key]'))
        .map(input => input.value.trim())
        .filter(Boolean);
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
        const index = parseInt(input.dataset.scheduleIdx, 10);
        const field = input.dataset.scheduleField;
        if (!Number.isInteger(index) || !field) return;
        if (!schedules[index]) schedules[index] = { time: '22:00', duration: 1440 };
        schedules[index][field] = field === 'duration' ? parseInt(input.value, 10) || 0 : input.value;
      });
      this._scheduleData[key] = schedules.filter(Boolean);
    });
  }

  destroy() {
    this._modelSelects?.forEach(select => select.destroy());
    this._modelSelects = [];
  }

  _bindEvents() {
    const container = this._formContainer();
    if (!container) return;

    container.querySelectorAll('.config-group-header').forEach(header => {
      header.addEventListener('click', () => {
        const group = header.closest('.config-group');
        const body = group?.querySelector('.config-group-body');
        const toggle = header.querySelector('.config-group-toggle');
        if (!body || !toggle) return;
        const isCollapsed = body.style.display === 'none';
        body.style.display = isCollapsed ? '' : 'none';
        toggle.textContent = isCollapsed ? '▼' : '▶';
        toggle.style.transform = isCollapsed ? '' : 'rotate(-90deg)';
      });
    });

    container.querySelectorAll('[data-setting-key]').forEach(input => {
      input.addEventListener(input.type === 'checkbox' ? 'change' : 'input', () => {
        this._snapshotFormValues();
      });
    });

    container.querySelectorAll('[data-list-add]').forEach(button => {
      button.addEventListener('click', () => {
        const key = button.dataset.listAdd;
        const listContainer = button.closest('.config-field')?.querySelector('[data-list-container]');
        if (!listContainer) return;
        const index = listContainer.querySelectorAll('[data-list-key]').length;
        const row = document.createElement('div');
        row.style.cssText = 'display:flex;gap:4px;margin-bottom:4px';
        const input = document.createElement('input');
        input.type = 'text';
        input.dataset.listKey = key;
        input.dataset.listIndex = String(index);
        input.className = 'config-input';
        input.style.cssText = 'background:var(--surface-2);color:var(--text);border:1px solid var(--border);border-radius:6px;padding:6px 8px;font-size:13px;flex:1';
        input.addEventListener('input', () => this._snapshotFormValues());
        const remove = document.createElement('button');
        remove.type = 'button';
        remove.className = 'btn btn-sm';
        remove.dataset.listRemove = key;
        remove.style.cssText = 'padding:2px 8px';
        remove.textContent = '✕';
        remove.addEventListener('click', () => {
          if (confirmDanger()) {
            row.remove();
            this._snapshotFormValues();
          }
        });
        row.append(input, remove);
        listContainer.appendChild(row);
      });
    });

    container.querySelectorAll('[data-list-remove]').forEach(button => {
      button.addEventListener('click', () => {
        if (confirmDanger()) {
          button.parentElement?.remove();
          this._snapshotFormValues();
        }
      });
    });

    container.querySelectorAll('[data-schedule-add]').forEach(button => {
      button.addEventListener('click', () => {
        const key = button.dataset.scheduleAdd;
        this._snapshotFormValues();
        if (!this._scheduleData[key]) this._scheduleData[key] = [];
        this._scheduleData[key].push({ time: '22:00', duration: 1440 });
        this.render();
      });
    });

    container.querySelectorAll('[data-schedule-remove]').forEach(button => {
      button.addEventListener('click', () => {
        const key = button.dataset.scheduleRemoveKey;
        const index = parseInt(button.dataset.scheduleRemove, 10);
        if (this._scheduleData[key] && confirmDanger()) {
          this._snapshotFormValues();
          this._scheduleData[key].splice(index, 1);
          this.render();
        }
      });
    });

    container.querySelectorAll('[data-schedule-field]').forEach(input => {
      input.addEventListener('change', () => {
        const key = input.dataset.scheduleKey;
        const index = parseInt(input.dataset.scheduleIdx, 10);
        const field = input.dataset.scheduleField;
        if (this._scheduleData[key]?.[index]) {
          this._scheduleData[key][index][field] = field === 'duration' ? parseInt(input.value, 10) : input.value;
        }
      });
    });

    container.querySelectorAll('[data-obj-array-add]').forEach(button => {
      button.addEventListener('click', () => {
        const key = button.dataset.objArrayAdd;
        this._snapshotFormValues();
        const param = this.parameters.find(item => String(item?.name ?? '') === key);
        if (!param || !Array.isArray(param.fields)) return;
        const newItem = {};
        param.fields.forEach(field => {
          const fieldName = String(field?.name ?? '');
          if (field.type === 'checkbox_group' || field.type === 'list' || field.type === 'array') {
            newItem[fieldName] = [];
          } else if (field.type === 'boolean' || field.type === 'bool') {
            newItem[fieldName] = false;
          } else {
            newItem[fieldName] = field.default || '';
          }
        });
        if (!this.settings[key]) this.settings[key] = [];
        this.settings[key].push(newItem);
        this.render();
      });
    });

    container.querySelectorAll('[data-obj-array-remove]').forEach(button => {
      button.addEventListener('click', () => {
        const key = button.dataset.objArrayRemove;
        const index = parseInt(button.dataset.objIdx, 10);
        if (this.settings[key] && confirmDanger()) {
          this._snapshotFormValues();
          this.settings[key].splice(index, 1);
          this.render();
        }
      });
    });

    container.querySelectorAll('[data-obj-array-key]').forEach(input => {
      const eventType = input.type === 'checkbox' ? 'change' : 'input';
      input.addEventListener(eventType, () => {
        const key = input.dataset.objArrayKey;
        const index = parseInt(input.dataset.objIdx, 10);
        const field = input.dataset.objField;
        if (!this.settings[key]?.[index]) return;

        if (input.dataset.objCheckbox !== undefined) {
          const checkboxValue = input.dataset.objCheckbox;
          let values = this.settings[key][index][field] || [];
          if (!Array.isArray(values)) values = [];
          if (input.checked) {
            if (!values.includes(checkboxValue)) values.push(checkboxValue);
          } else {
            values = values.filter(value => value !== checkboxValue);
          }
          this.settings[key][index][field] = values;
        } else if (input.dataset.objListIdx !== undefined) {
          const listIndex = parseInt(input.dataset.objListIdx, 10);
          const values = this.settings[key][index][field] || [];
          if (!Array.isArray(values)) return;
          values[listIndex] = input.value;
          this.settings[key][index][field] = values;
        } else if (input.type === 'checkbox') {
          this.settings[key][index][field] = input.checked;
        } else if (input.type === 'number') {
          this.settings[key][index][field] = parseFloat(input.value) || 0;
        } else {
          this.settings[key][index][field] = input.value;
        }
      });
    });

    container.querySelectorAll('[data-obj-list-add]').forEach(button => {
      button.addEventListener('click', () => {
        const key = button.dataset.objListAdd;
        this._snapshotFormValues();
        const index = parseInt(button.dataset.objIdx, 10);
        const field = button.dataset.objField;
        if (!this.settings[key]?.[index]) return;
        const values = this.settings[key][index][field] || [];
        if (!Array.isArray(values)) this.settings[key][index][field] = [''];
        else values.push('');
        this.render();
      });
    });

    container.querySelectorAll('[data-obj-list-remove]').forEach(button => {
      button.addEventListener('click', () => {
        const key = button.dataset.objListRemove;
        const index = parseInt(button.dataset.objIdx, 10);
        const field = button.dataset.objField;
        const listIndex = parseInt(button.dataset.objListIdx, 10);
        const values = this.settings[key]?.[index]?.[field];
        if (Array.isArray(values) && confirmDanger()) {
          this._snapshotFormValues();
          values.splice(listIndex, 1);
          this.render();
        }
      });
    });

    container.querySelectorAll('[data-checkbox-group]').forEach(checkbox => {
      checkbox.addEventListener('change', () => {
        const key = checkbox.dataset.checkboxGroup;
        const value = checkbox.dataset.checkboxValue;
        if (!Array.isArray(this.settings[key])) this.settings[key] = [];
        if (checkbox.checked) {
          if (!this.settings[key].includes(value)) this.settings[key].push(value);
        } else {
          this.settings[key] = this.settings[key].filter(item => item !== value);
        }
      });
    });

    container.querySelectorAll('[data-spin-target]').forEach(button => {
      button.addEventListener('click', () => {
        const targetName = button.dataset.spinTarget;
        const target = Array.from(container.querySelectorAll('input')).find(input =>
          input.id === targetName || input.dataset.settingKey === targetName
        );
        if (!target) return;
        const direction = parseInt(button.dataset.spinDir, 10);
        const step = parseFloat(target.step) || 1;
        const min = target.min !== '' ? parseFloat(target.min) : -Infinity;
        const max = target.max !== '' ? parseFloat(target.max) : Infinity;
        const current = parseFloat(target.value) || 0;
        target.value = Math.min(max, Math.max(min, current + step * direction));
        target.dispatchEvent(new Event('change', { bubbles: true }));
      });
    });

    this._modelSelects?.forEach(select => select.destroy());
    this._modelSelects = [];
    container.querySelectorAll('[data-model-select]').forEach(element => {
      const key = element.dataset.modelSelect;
      const value = element.dataset.modelValue || '';
      const options = Array.isArray(this.modelChoices) ? [...this.modelChoices] : [];
      if (value && !options.some(option => String(option?.value ?? '') === value)) {
        options.unshift({ value, label: `${value} (当前配置)`, tags: [] });
      }
      const select = new SafeModelSelect({
        options,
        value,
        onChange: selectedValue => {
          this.settings[key] = selectedValue;
        },
      });
      select.mount(element);
      this._modelSelects.push(select);
    });
  }

  collectValues() {
    this._snapshotFormValues();
    const values = {};
    const container = this._formContainer();
    if (!container) return values;

    container.querySelectorAll('[data-setting-key]').forEach(input => {
      const key = input.dataset.settingKey;
      if (!key) return;
      if (input.type === 'checkbox') values[key] = input.checked;
      else if (input.type === 'number') values[key] = parseFloat(input.value) || 0;
      else if (input.tagName === 'SELECT') values[key] = input.value || '';
      else values[key] = input.value;
    });

    // Containers keep their key even after the last item has been removed, so
    // an explicit empty list/choice group is persisted instead of merged away.
    container.querySelectorAll('[data-list-container]').forEach(listContainer => {
      const key = listContainer.dataset.listContainer;
      if (key) values[key] = Array.isArray(this.settings[key]) ? this.settings[key] : [];
    });
    container.querySelectorAll('[data-checkbox-group-container]').forEach(groupContainer => {
      const key = groupContainer.dataset.checkboxGroupContainer;
      if (key) values[key] = Array.isArray(this.settings[key]) ? this.settings[key] : [];
    });

    for (const [key, schedule] of Object.entries(this._scheduleData || {})) {
      if (Array.isArray(schedule)) values[key] = schedule;
    }
    for (const param of this.parameters || []) {
      const key = String(param?.name ?? '');
      if (param?.type === 'object_array' && Array.isArray(this.settings?.[key])) {
        values[key] = this.settings[key];
      }
    }

    this._modelSelects?.forEach(select => {
      const key = select._el?.dataset.modelSelect;
      if (key) values[key] = select.value || '';
    });
    return values;
  }
}

export async function init(container, params = {}) {
  scopedPage.use(params?.ctx, container);
  container.innerHTML = `
    <div class="card">
      <div class="card-header">
        <div>
          <div class="card-title">插件管理</div>
          <div class="card-subtitle">管理系统插件的启用状态和配置</div>
        </div>
        <button class="btn btn-sm" id="reloadPlugins">刷新</button>
      </div>
      <div id="pluginList" style="padding:16px">
        <div style="color:var(--text-3)">加载中...</div>
      </div>
    </div>
  `;

  const reloadBtn = $('reloadPlugins');
  if (reloadBtn) {
    reloadBtn.addEventListener('click', () => loadPlugins());
  }
  await loadPlugins();
}

async function loadPlugins() {
  const el = $('pluginList');
  try {
    const res = await get('/plugins');
    const plugins = Array.isArray(res.plugins) ? res.plugins : [];

    if (!plugins.length) {
      el.innerHTML = '<div style="padding:24px;text-align:center;color:var(--text-3)">暂无插件</div>';
      return;
    }

    el.innerHTML = `<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:12px">${plugins.map(p => {
      const name = escapeHtml(p.name);
      const version = escapeHtml(p.version || '—');
      const displayName = escapeHtml(p.display_name || p.name);
      const description = p.description ? escapeHtml(p.description) : '';
      const commandCount = Array.isArray(p.commands) ? p.commands.length : 0;
      const parameterCount = Array.isArray(p.parameters) ? p.parameters.length : 0;
      return `
      <div class="card plugin-detail-btn" data-name="${name}" style="margin:0;cursor:pointer">
        <div style="padding:16px">
          <div style="display:flex;align-items:center;gap:12px;margin-bottom:8px">
            <span class="plugin-toggle tag" data-name="${name}" style="font-size:11px;background:${p.enabled ? 'var(--success)' : 'var(--text-3)'};color:#fff;padding:2px 8px;border-radius:4px;flex-shrink:0">${p.enabled ? '已启用' : '已禁用'}</span>
            <span class="tag" style="font-size:11px;background:var(--accent);color:#fff;padding:2px 8px;border-radius:4px;flex-shrink:0">${version}</span>
            <span style="font-size:15px;font-weight:600;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${displayName}</span>
          </div>
          ${description ? `<div style="font-size:13px;color:var(--text-2);line-height:1.4">${description}</div>` : ''}
          <div style="display:flex;gap:12px;font-size:12px;color:var(--text-2);margin-top:12px">
            <span>命令: ${commandCount}</span>
            <span>参数: ${parameterCount}</span>
          </div>
        </div>
      </div>
    `;
    }).join('')}</div>`;

    el.querySelectorAll('.plugin-toggle').forEach(tag => {
      tag.addEventListener('click', (e) => {
        e.stopPropagation();
        const name = tag.dataset.name;
        const newState = tag.textContent === '已启用' ? false : true;
        tag.textContent = newState ? '已启用' : '已禁用';
        tag.style.background = newState ? 'var(--success)' : 'var(--text-3)';
        togglePlugin(name, newState, tag);
      });
    });

    el.querySelectorAll('.plugin-detail-btn').forEach(btn => {
      btn.addEventListener('click', () => openDetail(btn.dataset.name));
    });
  } catch {
    el.innerHTML = '<div style="color:var(--danger);padding:12px">插件列表加载失败</div>';
  }
}

async function togglePlugin(name, enabled, tagEl) {
  try {
    const res = await post(pluginPath(name, '/toggle'), { enabled });
    if (res.success) {
      toast(`${name} 已${enabled ? '启用' : '禁用'}`, 'success');
    } else {
      toast(res.error || '操作失败', 'error');
      if (tagEl) {
        tagEl.textContent = enabled ? '已禁用' : '已启用';
        tagEl.style.background = enabled ? 'var(--text-3)' : 'var(--success)';
      }
    }
  } catch (e) {
    if (e?.name === 'AbortError') return;
    toast('操作失败: ' + e.message, 'error');
    if (tagEl) {
      tagEl.textContent = enabled ? '已禁用' : '已启用';
      tagEl.style.background = enabled ? 'var(--text-3)' : 'var(--success)';
    }
  }
}

async function openDetail(name) {
  closeModal();
  const overlay = document.createElement('div');
  overlay.className = 'modal-overlay';
  overlay.innerHTML = `
    <div class="modal" style="max-width:720px">
      <div class="modal-header">
        <span id="modalTitle" style="font-size:16px;font-weight:600">加载中...</span>
        <button type="button" class="btn btn-sm" id="modalClose">✕</button>
      </div>
      <div class="modal-body" id="modalBody">
        <div style="padding:20px;text-align:center;color:var(--text-3)">加载中...</div>
      </div>
      <div class="modal-footer" id="modalFooter"></div>
    </div>
  `;
  document.body.appendChild(overlay);
  currentModal = overlay;

  overlay.addEventListener('click', (e) => {
    if (e.target === overlay) closeModal();
  });
  overlay.querySelector('#modalClose').addEventListener('click', closeModal);

  try {
    const detail = await get(pluginPath(name));
    if (!isActiveModal(overlay)) return;
    renderModalContent(detail, overlay);
  } catch {
    if (!isActiveModal(overlay)) return;
    const body = modalElement('modalBody', overlay);
    if (body) body.innerHTML = '<div style="color:var(--danger);padding:12px">加载失败</div>';
  }
}

function closeModal() {
  configForm?.destroy?.();
  configForm = null;
  if (currentModal) {
    currentModal.remove();
    currentModal = null;
  }
}

function renderModalContent(d, overlay) {
  if (!isActiveModal(overlay)) return;
  const title = modalElement('modalTitle', overlay);
  if (title) title.textContent = d.display_name || d.name;

  const commands = Array.isArray(d.commands) ? d.commands : [];
  const parameters = Array.isArray(d.parameters) ? d.parameters : [];
  const nlExamples = Array.isArray(d.nl_examples) ? d.nl_examples : [];
  const permissions = d.permissions && typeof d.permissions === 'object' ? d.permissions : {};
  const settings = d.settings && typeof d.settings === 'object' ? d.settings : {};
  const version = escapeHtml(d.version || '—');
  const author = escapeHtml(d.author || '—');
  const rateLimit = escapeHtml(permissions.rate_limit_calls_per_minute || 60);
  const groupBlacklist = escapeHtml(
    Array.isArray(permissions.group_blacklist) ? permissions.group_blacklist.join(',') : ''
  );
  const developerOnlyLocked = Boolean(permissions.developer_only_locked);
  const hiddenFromIntentLocked = Boolean(permissions.hidden_from_intent_locked);

  const body = modalElement('modalBody', overlay);
  if (!body) return;
  body.innerHTML = `
    <div class="stat-grid" style="margin-bottom:16px">
      <div class="stat-card">
        <div class="stat-label">版本</div>
        <div class="stat-value" style="font-size:14px">${version}</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">作者</div>
        <div class="stat-value" style="font-size:14px">${author}</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">命令数</div>
        <div class="stat-value">${commands.length}</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">状态</div>
        <div class="stat-value" style="font-size:14px;color:${d.enabled ? 'var(--success)' : 'var(--text-3)'}">${d.enabled ? '已启用' : '已禁用'}</div>
      </div>
    </div>

    ${commands.length ? `
      <div style="margin-bottom:16px">
        <div style="font-size:14px;font-weight:600;margin-bottom:8px">命令列表</div>
        <div style="display:grid;gap:8px">
          ${commands.map(command => {
            const commandName = escapeHtml(command?.name || '');
            const patternType = escapeHtml(command?.pattern_type || 'text');
            const description = command?.description ? escapeHtml(command.description) : '';
            const patterns = Array.isArray(command?.patterns) ? command.patterns : [];
            return `
            <div style="padding:10px 12px;background:var(--surface-2,rgba(255,255,255,0.03));border-radius:6px">
              <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px">
                <span style="font-weight:500;font-size:13px">${commandName}</span>
                <span class="tag" style="font-size:11px">${patternType}</span>
              </div>
              ${description ? `<div style="font-size:12px;color:var(--text-2);margin-bottom:4px">${description}</div>` : ''}
              ${patterns.length ? `<div style="display:flex;flex-wrap:wrap;gap:4px;margin-top:4px">${patterns.map(pattern => `<code style="font-size:11px;padding:2px 6px;background:var(--surface-3,rgba(255,255,255,0.06));border-radius:4px">${escapeHtml(pattern)}</code>`).join('')}</div>` : ''}
            </div>
          `;
          }).join('')}
        </div>
      </div>
    ` : ''}

    ${nlExamples.length ? `
      <div style="margin-bottom:16px">
        <div style="font-size:14px;font-weight:600;margin-bottom:8px">自然语言示例</div>
        <div style="display:grid;gap:6px">
          ${nlExamples.map(example => `
            <div style="font-size:13px;padding:8px 12px;background:var(--surface-2,rgba(255,255,255,0.03));border-radius:6px;color:var(--text-2)">"${escapeHtml(example)}"</div>
          `).join('')}
        </div>
      </div>
    ` : ''}

    <div id="pluginParamsSection" style="margin-bottom:16px">
      <div style="font-size:14px;font-weight:600;margin-bottom:8px">参数配置</div>
      <form id="paramsForm" style="display:grid;gap:10px"></form>
    </div>

    <div style="margin-bottom:16px">
      <div style="font-size:14px;font-weight:600;margin-bottom:8px">权限配置</div>
      <form id="permForm" style="display:grid;gap:12px">
        <label style="display:flex;align-items:center;gap:8px;font-size:13px;cursor:pointer">
          <input type="checkbox" name="developer_only" ${permissions.developer_only ? 'checked' : ''}${developerOnlyLocked ? ' disabled' : ''}>
          仅开发者可用${developerOnlyLocked ? '（由插件清单强制）' : ''}
        </label>
        <label style="display:flex;align-items:center;gap:8px;font-size:13px;cursor:pointer">
          <input type="checkbox" name="hidden_from_intent" ${permissions.hidden_from_intent ? 'checked' : ''}${hiddenFromIntentLocked ? ' disabled' : ''}>
          意图识别中隐藏${hiddenFromIntentLocked ? '（由插件清单强制）' : ''}
        </label>
        <div class="form-group" style="margin:0">
          <label>频率限制 (次/分钟)</label>
          <div class="number-input-group">
            <button type="button" class="number-spin-btn" data-spin-target="rate_limit" data-spin-dir="-1">−</button>
            <input type="number" id="rate_limit" name="rate_limit" value="${rateLimit}" min="1" max="1000">
            <button type="button" class="number-spin-btn" data-spin-target="rate_limit" data-spin-dir="1">+</button>
          </div>
        </div>
        <div class="form-group" style="margin:0">
          <label>群组黑名单</label>
          <input type="text" name="group_blacklist" placeholder="群号用逗号分隔" value="${groupBlacklist}">
        </div>
      </form>
    </div>
  `;

  // 使用 DynamicConfigForm 组件渲染参数配置（复用参数列表）。异步模型
  // 选项加载完成前禁用保存，避免关闭/切换弹窗后的陈旧回调写入新弹窗。
  const section = modalElement('pluginParamsSection', overlay);
  const modalSaveButton = () => modalElement('modalSave', overlay);
  if (parameters.length || Object.keys(settings).length) {
    const form = new SafeDynamicConfigForm({
      containerId: 'pluginParamsSection',
      parameters,
      settings,
      get,
    });
    configForm = form;
    if (modalSaveButton()) modalSaveButton().disabled = true;
    form.init().then(() => {
      if (!isActiveModal(overlay) || configForm !== form) return;
      form.render();
      const paramsForm = modalElement('paramsForm', overlay);
      if (paramsForm && !paramsForm.children.length && section) {
        section.style.display = 'none';
      }
    }).catch(() => {
      if (!isActiveModal(overlay) || configForm !== form) return;
      toast('插件参数初始化失败', 'error');
    }).finally(() => {
      const saveButton = modalSaveButton();
      if (isActiveModal(overlay) && configForm === form && saveButton) {
        saveButton.disabled = false;
      }
    });
  } else if (section) {
    section.style.display = 'none';
  }

  const footer = modalElement('modalFooter', overlay);
  if (footer) {
    footer.innerHTML = `
      <button type="button" class="btn" id="modalCancel">取消</button>
      <button type="button" class="btn btn-primary" id="modalSave">保存配置</button>
    `;
  }
  if (configForm) {
    const saveButton = modalElement('modalSave', overlay);
    if (saveButton) saveButton.disabled = true;
  }

  modalElement('modalCancel', overlay)?.addEventListener('click', closeModal);
  modalElement('modalSave', overlay)?.addEventListener('click', () => savePluginConfig(d.name, overlay));

  // 数字调节按钮事件
  overlay.querySelectorAll('[data-spin-target]').forEach(btn => {
    btn.addEventListener('click', () => {
      const target = overlay.querySelector(`#${btn.dataset.spinTarget}`);
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
}

async function savePluginConfig(name, overlay) {
  if (!isActiveModal(overlay)) return;
  const saveBtn = modalElement('modalSave', overlay);
  const form = configForm;
  if (saveBtn) {
    saveBtn.disabled = true;
    saveBtn.textContent = '保存中...';
  }

  let settingsSaved = false;
  try {
    if (form) {
      const newSettings = form.collectValues();
      if (Object.keys(newSettings).length > 0) {
        await post(pluginPath(name, '/settings'), { settings: newSettings });
      }
    }
    settingsSaved = true;
    if (!isActiveModal(overlay)) return;

    const permForm = modalElement('permForm', overlay);
    if (permForm) {
      const bl = permForm.group_blacklist.value.trim();
      const permissions = {
        developer_only: permForm.developer_only.checked,
        hidden_from_intent: permForm.hidden_from_intent.checked,
        rate_limit_calls_per_minute: parseInt(permForm.rate_limit.value, 10) || 60,
        group_blacklist: bl ? bl.split(',').map(s => s.trim()).filter(Boolean) : [],
      };
      await put(pluginPath(name, '/config'), permissions);
    }
    if (!isActiveModal(overlay)) return;

    flashSuccess(saveBtn);
    toast('配置已保存', 'success');
    scopedPage.timeout(() => {
      if (isActiveModal(overlay)) closeModal();
    }, 1200);
  } catch (e) {
    if (e?.name === 'AbortError') return;
    const prefix = settingsSaved ? '参数已保存，但权限保存失败：' : '保存失败：';
    toast(prefix + (e?.message || '未知错误'), 'error');
    if (isActiveModal(overlay) && saveBtn) {
      saveBtn.disabled = false;
      saveBtn.textContent = '保存配置';
    }
  }
}
