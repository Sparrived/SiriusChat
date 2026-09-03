import { store } from '../store.js';
import { get, post, put } from '../app.js';
import {
  toast,
  flashSuccess,
  confirmDanger,
  DynamicConfigForm,
  ModelSelect,
  createObjectArrayItem,
  isSafeConfigFieldName,
  isSecretConfigField,
  parseConfigNumber,
} from '../components.js';
import { createScopedPage } from '../page-context.js';
import { normalizePluginUISchema } from '../plugin-ui-schema.js';

const scopedPage = createScopedPage();

export function dispose() {
  // Page teardown must not focus a button that the router is about to remove.
  closeModal(false);
  scopedPage.use(null, null);
}
const $ = scopedPage.$;

let currentModal = null;
let configForm = null;
let modalReturnFocus = null;
let modalInertSiblings = [];

function isolateModal(overlay) {
  modalInertSiblings = Array.from(document.body.children)
    .filter(element => element !== overlay)
    .map(element => ({
      element,
      inert: element.inert === true,
      ariaHidden: element.getAttribute('aria-hidden'),
    }));
  modalInertSiblings.forEach(({ element }) => {
    element.inert = true;
    element.setAttribute('aria-hidden', 'true');
  });
}

function restoreModalSiblings() {
  modalInertSiblings.forEach(({ element, inert, ariaHidden }) => {
    element.inert = inert;
    if (ariaHidden === null) element.removeAttribute('aria-hidden');
    else element.setAttribute('aria-hidden', ariaHidden);
  });
  modalInertSiblings = [];
}

function focusPageFallback() {
  const fallback = globalThis['document']?.querySelector('#main, #pluginList, #pluginsList, #app');
  if (!fallback) return;
  if (fallback.tabIndex < 0) fallback.setAttribute('tabindex', '-1');
  fallback.focus();
}

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

function setPluginToggleVisual(button, enabled) {
  if (!button) return;
  const label = button.dataset.label || '';
  button.textContent = enabled ? '已启用' : '已禁用';
  button.setAttribute('aria-pressed', String(enabled));
  button.setAttribute('aria-label', `${enabled ? '禁用' : '启用'}${label}`);
  button.classList.toggle('plugin-toggle--enabled', enabled);
}

function isVisibleFocusable(element) {
  if (!element || element.disabled || (typeof element.tabIndex === 'number' && element.tabIndex < 0)) return false;
  for (let node = element; node; node = node.parentElement) {
    if (node.hidden || node.getAttribute?.('aria-hidden') === 'true') return false;
    if (typeof window !== 'undefined' && typeof window.getComputedStyle === 'function') {
      const style = window.getComputedStyle(node);
      if (style.display === 'none' || style.visibility === 'hidden') return false;
    }
  }
  return true;
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
    if (trigger) {
      trigger.classList.toggle('msel-placeholder', !selected);
      trigger.setAttribute('aria-label', selected
        ? `${this.ariaLabel}：${String(selected.label ?? '')}`
        : `${this.ariaLabel}：${this.placeholder}`);
    }
  }

  render() {
    if (!this._el) return;
    const selected = this.options.find(option => option.value === this.value);
    this._activeIndex = -1;
    const controlId = safeDomId(this._el.dataset.modelSelect || 'model', 'plugin_model_');
    const listId = `${controlId}_list`;
    this._el.replaceChildren();

    const wrap = document.createElement('div');
    wrap.className = 'msel-wrap';
    const trigger = document.createElement('button');
    trigger.type = 'button';
    trigger.className = `msel-trigger${this._open ? ' msel-open' : ''}${selected ? '' : ' msel-placeholder'}`;
    trigger.setAttribute('role', 'combobox');
    trigger.setAttribute('aria-haspopup', 'listbox');
    trigger.setAttribute('aria-expanded', String(this._open));
    trigger.setAttribute('aria-controls', listId);
    trigger.setAttribute('aria-label', this.ariaLabel);
    const text = document.createElement('span');
    text.className = 'msel-text';
    const arrow = document.createElement('span');
    arrow.className = 'msel-arrow';
    arrow.setAttribute('aria-hidden', 'true');
    arrow.textContent = '▾';
    trigger.append(text, arrow);

    const dropdown = document.createElement('div');
    dropdown.className = 'msel-dropdown';
    dropdown.style.display = this._open ? 'block' : 'none';
    const search = document.createElement('input');
    search.type = 'text';
    search.className = 'msel-search';
    search.placeholder = '搜索模型…';
    search.setAttribute('aria-label', '搜索模型');
    const list = document.createElement('div');
    list.className = 'msel-list';
    list.id = listId;
    list.setAttribute('role', 'listbox');
    list.setAttribute('aria-label', '可用模型');
    dropdown.append(search, list);
    wrap.append(trigger, dropdown);
    this._el.appendChild(wrap);
    this._syncTriggerText();

    const open = ({ focus = 'search', last = false } = {}) => {
      ModelSelect.closeAll(this);
      this._open = true;
      search.value = '';
      this._renderList('');
      this._renderDropdown();
      if (focus === 'option') this._focusOption(last ? this._filteredOptions.length - 1 : 0);
      else search.focus();
    };
    trigger.addEventListener('click', (event) => {
      event.stopPropagation();
      if (this._open) this._closeDropdown(false);
      else open();
    });
    trigger.addEventListener('keydown', (event) => {
      if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
        event.preventDefault();
        if (!this._open) open({ focus: 'option', last: event.key === 'ArrowUp' });
        else this._focusOption(event.key === 'ArrowUp' ? this._filteredOptions.length - 1 : 0);
      } else if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        if (!this._open) open();
        else search.focus();
      } else if (event.key === 'Escape' && this._open) {
        event.preventDefault();
        event.stopPropagation();
        this._closeDropdown(true);
      }
    });
    search.addEventListener('input', () => this._renderList(search.value));
    search.addEventListener('keydown', (event) => {
      const options = this._el?.querySelectorAll('[role="option"]') || [];
      if (event.key === 'Escape') {
        event.preventDefault();
        event.stopPropagation();
        this._closeDropdown(true);
      } else if (event.key === 'ArrowDown') {
        event.preventDefault();
        this._focusOption(options.length ? 0 : -1);
      } else if (event.key === 'ArrowUp') {
        event.preventDefault();
        this._focusOption(options.length ? options.length - 1 : -1);
      } else if (event.key === 'Enter' && this._activeIndex >= 0) {
        event.preventDefault();
        const option = this._filteredOptions?.[this._activeIndex];
        if (option) this._selectOption(option);
      } else if (event.key === 'Tab') {
        this._closeDropdown(false);
      }
    });
    search.addEventListener('click', event => event.stopPropagation());
    wrap.addEventListener('focusout', event => {
      const next = event.relatedTarget;
      if (this._open && (!next || !wrap.contains(next))) this._closeDropdown(false);
    });
    this._renderList('');
  }

  _renderDropdown() {
    super._renderDropdown();
    const trigger = this._el?.querySelector('.msel-trigger');
    if (trigger) trigger.setAttribute('aria-expanded', String(this._open));
  }

  _focusOption(index) {
    const options = Array.from(this._el?.querySelectorAll('[role="option"]') || []);
    if (!options.length || index < 0) return;
    this._activeIndex = Math.max(0, Math.min(index, options.length - 1));
    options.forEach((option, optionIndex) => {
      option.classList.toggle('msel-highlighted', optionIndex === this._activeIndex);
    });
    const active = options[this._activeIndex];
    active.focus();
    this._el?.querySelector('.msel-trigger')?.setAttribute('aria-activedescendant', active.id);
  }

  _selectOption(option) {
    this.value = option.value;
    this._open = false;
    this.render();
    if (this.onChange) this.onChange(this.value);
    this._el?.querySelector('.msel-trigger')?.focus();
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
    this._filteredOptions = filtered;
    this._activeIndex = -1;
    this._el?.querySelector('.msel-trigger')?.removeAttribute('aria-activedescendant');

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
      item.id = `${safeDomId(this._el.dataset.modelSelect || 'model', 'plugin_model_option_')}_${index}`;
      item.setAttribute('role', 'option');
      item.setAttribute('aria-selected', String(option.value === this.value));
      item.tabIndex = -1;
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
      item.addEventListener('click', (event) => {
        event.stopPropagation();
        this._selectOption(option);
      });
      item.addEventListener('keydown', (event) => {
        if (event.key === 'ArrowDown') {
          event.preventDefault();
          this._focusOption(index + 1);
        } else if (event.key === 'ArrowUp') {
          event.preventDefault();
          if (index === 0) this._el?.querySelector('.msel-search')?.focus();
          else this._focusOption(index - 1);
        } else if (event.key === 'Home') {
          event.preventDefault();
          this._focusOption(0);
        } else if (event.key === 'End') {
          event.preventDefault();
          this._focusOption(filtered.length - 1);
        } else if (event.key === 'Escape') {
          event.preventDefault();
          event.stopPropagation();
          this._open = false;
          this._renderDropdown();
          this._el?.querySelector('.msel-trigger')?.focus();
        } else if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          this._selectOption(option);
        }
      });
      list.appendChild(item);
    });
  }
}

class SafeDynamicConfigForm extends DynamicConfigForm {
  constructor(options) {
    super(options);
    this._containerElement = options?.containerElement || null;
    this._objectRowIds = new WeakMap();
    this._nextObjectRowId = 1;
    this._jsonErrors = new Set();
    this._invalidJsonValues = new Map();
    this.uiSchema = normalizePluginUISchema(options?.uiSchema, this.parameters);
  }

  async init() {
    await super.init();
    if (this.initError) throw this.initError;
    this._rawModelChoices = Array.isArray(this.modelChoices) ? this.modelChoices : [];
  }

  _formContainer() {
    return this._containerElement || super._formContainer();
  }

  _captureViewState() {
    const container = this._formContainer();
    const state = { sections: new Map(), fieldsets: new Map(), focus: null };
    if (!container) return state;
    container.querySelectorAll('[data-plugin-section-toggle]').forEach(button => {
      const bodyId = button.getAttribute('aria-controls');
      if (bodyId) state.sections.set(bodyId, button.getAttribute('aria-expanded') !== 'false');
    });
    container.querySelectorAll('[data-plugin-fieldset]').forEach(fieldset => {
      state.fieldsets.set(fieldset.dataset.pluginFieldset, fieldset.open);
    });
    const active = document.activeElement;
    if (active && container.contains(active)) {
      state.focus = {
        setting: active.dataset?.settingKey || '',
        array: active.dataset?.objArrayKey || '',
        rowKey: active.dataset?.objRowKey || '',
        index: active.dataset?.objIdx || '',
        field: active.dataset?.objField || '',
        listIndex: active.dataset?.objListIdx || '',
      };
    }
    return state;
  }

  _restoreViewState(state) {
    const container = this._formContainer();
    if (!container || !state) return;
    container.querySelectorAll('[data-plugin-section-toggle]').forEach(button => {
      const bodyId = button.getAttribute('aria-controls');
      const body = bodyId ? container.querySelector(`#${CSS.escape(bodyId)}`) : null;
      const expanded = state.sections.get(button.getAttribute('aria-controls'));
      if (body && expanded !== undefined) {
        body.hidden = !expanded;
        button.setAttribute('aria-expanded', String(expanded));
      }
    });
    container.querySelectorAll('[data-plugin-fieldset]').forEach(fieldset => {
      const open = state.fieldsets.get(fieldset.dataset.pluginFieldset);
      if (open !== undefined) fieldset.open = open;
    });
    const focus = this._focusAfterRender || state.focus;
    this._focusAfterRender = null;
    if (!focus) return;
    const objectControls = Array.from(container.querySelectorAll(
      '[data-obj-array-key], [data-obj-array-remove], [data-obj-list-add], [data-obj-list-remove], [data-plugin-object-summary]',
    ));
    const controlArray = control => (
      control.dataset.objArrayKey || control.dataset.objArrayRemove ||
      control.dataset.objListAdd || control.dataset.objListRemove || ''
    );
    const matchesControl = (control, array, rowKey, index, field, listIndex) => (
      controlArray(control) === array &&
      (rowKey ? control.dataset.objRowKey === rowKey : control.dataset.objIdx === String(index)) &&
      (!field || control.dataset.objField === field) &&
      (listIndex === '' || listIndex === null || listIndex === undefined ||
        control.dataset.objListIdx === String(listIndex))
    );
    let target = null;
    if (focus.setting) target = container.querySelector(`[data-setting-key="${CSS.escape(focus.setting)}"]`);
    if (!target && focus.array) {
      target = objectControls.find(control => matchesControl(
        control,
        focus.array,
        focus.rowKey,
        focus.index,
        focus.field,
        focus.listIndex,
      ));
    }
    if (!target && focus.objListAdd) {
      target = objectControls.find(control => matchesControl(
        control,
        focus.objListAdd.key,
        focus.objListAdd.rowKey,
        focus.objListAdd.index,
        focus.objListAdd.field,
        '',
      ));
    }
    if (!target && focus.objArrayAdd) {
      target = Array.from(container.querySelectorAll('[data-obj-array-add]')).find(button => (
        button.dataset.objArrayAdd === focus.objArrayAdd
      ));
    }
    if (target) {
      target.closest('details')?.setAttribute('open', '');
      target.focus();
    }
  }

  render() {
    const state = this._captureViewState();
    super.render();
    this._restoreViewState(state);
  }

  _renderJson(key, value, label = key, desc = '', expected = 'any') {
    let serializedValue = '';
    const rawValue = this._invalidJsonValues?.get(key);
    if (rawValue !== undefined) {
      serializedValue = rawValue;
    } else {
      const fallbackValue = expected === 'array' ? [] : {};
      try {
        serializedValue = JSON.stringify(value === undefined ? fallbackValue : value, null, 2);
      } catch {
        serializedValue = '';
      }
    }
    const inputId = safeDomId(key, 'plugin_json_');
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

  _parameterPresentation(key) {
    return this.uiSchema?.parameters?.[key] || Object.create(null);
  }

  _renderParameter(param, settings, { schema = false, defaultSpan = 12 } = {}) {
    if (!param || typeof param !== 'object') return '';
    const key = String(param.name ?? '');
    const type = String(param.type || 'str').toLowerCase();
    const presentation = schema ? this._parameterPresentation(key) : Object.create(null);
    const label = presentation.label || param.name;
    const desc = presentation.help ?? param.description ?? '';
    const defaultVal = param.default;
    const value = settings[key];
    const required = Boolean(param.required);
    let fieldHtml = '';

    if (isSecretConfigField(param)) {
      fieldHtml = this._renderPassword(key, label, value, defaultVal, desc, required);
    } else if (type === 'model') {
      const modelValue = value || defaultVal || '';
      fieldHtml = this.modelChoices?.length
        ? this._renderModelSelect(key, label, modelValue, desc, required)
        : this._renderText(key, label, modelValue, defaultVal, desc, required, presentation);
    } else if (type === 'password' || type === 'secret') {
      fieldHtml = this._renderPassword(key, label, value, defaultVal, desc, required);
    } else if (type === 'boolean' || type === 'bool') {
      fieldHtml = this._renderCheckbox(key, label, value, defaultVal, desc, presentation);
    } else if (type === 'int' || type === 'float' || type === 'number') {
      fieldHtml = this._renderNumber(
        key,
        label,
        value,
        defaultVal,
        desc,
        required,
        param.minimum,
        param.maximum,
        type === 'int' ? 1 : 'any',
        presentation,
      );
    } else if (type === 'string' || type === 'str') {
      fieldHtml = this._renderText(key, label, value, defaultVal, desc, required, presentation);
    } else if (type === 'list' || type === 'array') {
      fieldHtml = this._renderList(key, label, value, defaultVal, desc, presentation);
    } else if (type === 'schedule') {
      fieldHtml = this._renderSchedule(
        key,
        Array.isArray(value) ? value : (Array.isArray(defaultVal) ? defaultVal : []),
      );
    } else if (type === 'object_array') {
      fieldHtml = Array.isArray(param.fields)
        ? this._renderObjectArray(
          key,
          label,
          value,
          defaultVal,
          desc,
          param.fields,
          presentation,
        )
        : this._renderJson(key, value ?? defaultVal, label, desc, 'array');
    } else if (type === 'object' || type === 'json') {
      fieldHtml = this._renderJson(
         key,
         value === undefined ? defaultVal : value,
         label,
         desc,
         type === 'object' ? 'object' : 'any',
       );
    } else if (type === 'checkbox_group' && Array.isArray(param.choices)) {
      fieldHtml = this._renderCheckboxGroup(
        key,
        label,
        value,
        defaultVal,
        desc,
        param.choices,
      );
    } else if (type === 'checkbox_group') {
      fieldHtml = this._renderJson(key, value ?? defaultVal ?? [], label, desc, 'array');
    } else {
      fieldHtml = this._renderJson(key, value ?? defaultVal ?? {}, label, desc, 'any');
    }
    if (!fieldHtml || !schema) return fieldHtml;
    const requestedSpan = Number.isInteger(presentation.span) ? presentation.span : defaultSpan;
    const span = Math.min(12, Math.max(1, requestedSpan));
    return `<div class="plugin-config-cell" style="--plugin-field-span:${span}">${fieldHtml}</div>`;
  }

  _unknownSetting(key, value) {
    const schedule = this._scheduleData?.[key];
    const isSchedule = Array.isArray(schedule) || (
      Array.isArray(value) && value.length > 0 &&
      typeof value[0] === 'object' && value[0] !== null &&
      'time' in value[0] && 'duration' in value[0]
    );
    return isSchedule
      ? this._renderSchedule(key, Array.isArray(schedule) ? schedule : value)
      : this._renderJson(key, value);
  }

  _renderSchemaSection(section, fields) {
    if (!fields.length) return '';
    const title = section.title || '配置';
    const description = section.description || '';
    const collapsed = Boolean(section.collapsed);
    const tone = ['accent', 'muted'].includes(section.tone) ? section.tone : 'default';
    const bodyId = safeDomId(section.id || title, 'plugin_section_');
    return `
      <section class="plugin-config-section plugin-config-section--${tone}">
        <button type="button" class="plugin-config-section-header" data-plugin-section-toggle aria-expanded="${collapsed ? 'false' : 'true'}" aria-controls="${escapeHtml(bodyId)}">
          <span class="plugin-config-section-index" aria-hidden="true"></span>
          <span class="plugin-config-section-copy">
            <span class="plugin-config-section-title">${escapeHtml(title)}</span>
            ${description ? `<span class="plugin-config-section-description">${escapeHtml(description)}</span>` : ''}
          </span>
          <span class="plugin-config-section-count">${fields.length} 项</span>
          <span class="plugin-config-section-chevron" aria-hidden="true">⌄</span>
        </button>
        <div id="${escapeHtml(bodyId)}" class="plugin-config-section-body"${collapsed ? ' hidden' : ''}>
          <div class="plugin-config-grid">${fields.join('')}</div>
        </div>
      </section>
    `;
  }

  _buildSchemaForm(settings) {
    const parameterMap = new Map((this.parameters || []).map(param => [String(param?.name ?? ''), param]));
    const renderedKeys = new Set();
    const sections = [];
    for (const section of this.uiSchema.sections || []) {
      const defaultSpan = section.columns === 2 ? 6 : 12;
      const fields = [];
      for (const name of section.parameters || []) {
        const param = parameterMap.get(name);
        if (!param || renderedKeys.has(name)) continue;
        renderedKeys.add(name);
        const field = this._renderParameter(param, settings, { schema: true, defaultSpan });
        if (field) fields.push(field);
      }
      sections.push(this._renderSchemaSection(section, fields));
    }

    const remaining = [];
    for (const param of this.parameters || []) {
      const key = String(param?.name ?? '');
      if (renderedKeys.has(key)) continue;
      renderedKeys.add(key);
      const field = this._renderParameter(param, settings, { schema: true, defaultSpan: 12 });
      if (field) remaining.push(field);
    }
    if (remaining.length) {
      sections.push(this._renderSchemaSection({
        title: (this.uiSchema.sections || []).length ? '其他配置' : '配置项',
        description: (this.uiSchema.sections || []).length ? '未单独分区的插件参数。' : '',
        tone: 'muted',
      }, remaining));
    }

    const unknown = [];
    for (const [key, value] of Object.entries(settings || {})) {
      if (renderedKeys.has(key)) continue;
      renderedKeys.add(key);
      unknown.push(`<div class="plugin-config-cell" style="--plugin-field-span:12">${this._unknownSetting(key, value)}</div>`);
    }
    if (unknown.length) {
      sections.push(this._renderSchemaSection({
        title: '兼容配置',
        description: '这些值没有可用的可视化参数声明。',
        tone: 'muted',
        collapsed: true,
      }, unknown));
    }

    const title = this.uiSchema.title || '';
    const description = this.uiSchema.description || '';
    const intro = title || description ? `
      <div class="plugin-config-intro">
        <span class="plugin-config-kicker">VISUAL CONFIG</span>
        ${title ? `<h3>${escapeHtml(title)}</h3>` : ''}
        ${description ? `<p>${escapeHtml(description)}</p>` : ''}
      </div>
    ` : '';
    return `${intro}<div class="plugin-config-sections">${sections.join('')}</div>`;
  }

  _buildForm(settings) {
    if (this.uiSchema) return this._buildSchemaForm(settings);

    const renderedKeys = new Set();
    const groups = new Map();
    const ungrouped = [];
    for (const param of this.parameters || []) {
      const key = String(param?.name ?? '');
      if (!key || renderedKeys.has(key)) continue;
      renderedKeys.add(key);
      const fieldHtml = this._renderParameter(param, settings);
      if (!fieldHtml) continue;
      const group = String(param?.group ?? '');
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
      ungrouped.push(this._unknownSetting(key, value));
    }

    const sections = [];
    if (ungrouped.length) sections.push(`<div class="config-fields">${ungrouped.join('')}</div>`);
    for (const [groupName, fields] of groups) {
      sections.push(`
        <div class="config-group">
          <div class="config-group-header" data-group="${escapeHtml(groupName)}">
            <span class="config-group-title">${escapeHtml(groupName)}</span>
            <span class="config-group-toggle">▼</span>
          </div>
          <div class="config-group-body"><div class="config-fields">${fields.join('')}</div></div>
        </div>
      `);
    }
    return sections.join('') || '<div class="plugin-config-empty">暂无可配置项</div>';
  }

  _renderModelSelect(key, label, value, desc, required) {
    const resolvedValue = resolveModelValue(value, this.modelChoices);
    return `
      <div class="config-field">
        <div class="config-field-header">
          <label class="config-field-label">${escapeHtml(label)}${required ? '<span class="config-required">*</span>' : ''}</label>
          ${desc ? `<span class="config-field-desc">${escapeHtml(desc)}</span>` : ''}
        </div>
        <div data-model-select="${escapeHtml(key)}" data-model-label="${escapeHtml(label)}" data-model-value="${escapeHtml(resolvedValue)}"></div>
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

  _renderCheckbox(key, label, value, defaultVal, desc, presentation = {}) {
    const trueLabel = presentation.true_label || '已启用';
    const falseLabel = presentation.false_label || '已停用';
    const checked = (value ?? defaultVal ?? false) === true;
    const inputId = safeDomId(key, 'plugin_switch_');
    return `
      <div class="config-field plugin-switch-field">
        <div class="plugin-switch-copy">
          <span class="config-field-label">${escapeHtml(label)}</span>
          ${desc ? `<span class="plugin-field-help">${escapeHtml(desc)}</span>` : ''}
        </div>
        <label class="plugin-switch" for="${escapeHtml(inputId)}">
          <input type="checkbox" id="${escapeHtml(inputId)}" data-setting-key="${escapeHtml(key)}"${checked ? ' checked' : ''} aria-label="${escapeHtml(label)}"
            data-true-label="${escapeHtml(trueLabel)}" data-false-label="${escapeHtml(falseLabel)}">
          <span class="plugin-switch-track" aria-hidden="true"><span></span></span>
          <span class="plugin-switch-state" data-switch-state>${escapeHtml(checked ? trueLabel : falseLabel)}</span>
        </label>
      </div>
    `;
  }

  _renderNumber(
    key,
    label,
    value,
    defaultVal,
    desc,
    required,
    minimum,
    maximum,
    step,
    presentation = {},
  ) {
    const inputId = safeDomId(key, 'plugin_number_');
    const hasMinimum = minimum !== null && minimum !== undefined && minimum !== '';
    const hasMaximum = maximum !== null && maximum !== undefined && maximum !== '';
    const minAttr = hasMinimum && Number.isFinite(Number(minimum)) ? ` min="${escapeHtml(minimum)}"` : '';
    const maxAttr = hasMaximum && Number.isFinite(Number(maximum)) ? ` max="${escapeHtml(maximum)}"` : '';
    const stepAttr = step !== undefined && step !== null ? ` step="${escapeHtml(step)}"` : '';
    const unit = presentation.unit || '';
    return `
      <div class="config-field">
        <div class="config-field-header">
          <label class="config-field-label" for="${escapeHtml(inputId)}">${escapeHtml(label)}${required ? '<span class="config-required">*</span>' : ''}</label>
          ${desc ? `<span class="config-field-desc">${escapeHtml(desc)}</span>` : ''}
        </div>
        <div class="plugin-number-row">
          <div class="number-input-group">
            <button type="button" class="number-spin-btn" data-spin-target="${escapeHtml(inputId)}" data-spin-dir="-1" aria-label="减少${escapeHtml(label)}">−</button>
            <input type="number" id="${escapeHtml(inputId)}" data-setting-key="${escapeHtml(key)}" value="${escapeHtml(value ?? defaultVal ?? '')}"${minAttr}${maxAttr}${stepAttr} class="config-input">
            <button type="button" class="number-spin-btn" data-spin-target="${escapeHtml(inputId)}" data-spin-dir="1" aria-label="增加${escapeHtml(label)}">+</button>
          </div>
          ${unit ? `<span class="plugin-field-unit">${escapeHtml(unit)}</span>` : ''}
        </div>
      </div>
    `;
  }

  _renderText(key, label, value, defaultVal, desc, required, presentation = {}) {
    const inputId = safeDomId(key, 'plugin_text_');
    const widget = presentation.widget || 'text';
    const placeholder = presentation.placeholder || '';
    const currentValue = value ?? defaultVal ?? '';
    const codeLike = ['url', 'path', 'code'].includes(widget);
    const inputClass = `config-input${codeLike ? ' plugin-code-input' : ''}`;
    const common = `id="${escapeHtml(inputId)}" data-setting-key="${escapeHtml(key)}" placeholder="${escapeHtml(placeholder)}" class="${inputClass}"${codeLike ? ' spellcheck="false"' : ''}`;
    const control = widget === 'textarea'
      ? `<textarea ${common} rows="3">${escapeHtml(currentValue)}</textarea>`
      : `<input type="${widget === 'url' ? 'url' : 'text'}" ${common} value="${escapeHtml(currentValue)}">`;
    return `
      <div class="config-field">
        <div class="config-field-header">
          <label class="config-field-label" for="${escapeHtml(inputId)}">${escapeHtml(label)}${required ? '<span class="config-required">*</span>' : ''}</label>
          ${desc ? `<span class="config-field-desc">${escapeHtml(desc)}</span>` : ''}
        </div>
        ${control}
      </div>
    `;
  }

  _renderList(key, label, value, defaultVal, desc, presentation = {}) {
    const listVal = Array.isArray(value)
      ? value
      : (Array.isArray(defaultVal) ? defaultVal : (defaultVal ? [defaultVal] : []));
    const listId = safeDomId(key, 'plugin_list_');
    const placeholder = presentation.item_placeholder || '';
    const addLabel = presentation.add_label || '添加一项';
    return `
      <div class="config-field plugin-list-field">
        <div class="config-field-header">
          <label class="config-field-label">${escapeHtml(label)}</label>
          ${desc ? `<span class="config-field-desc">${escapeHtml(desc)}</span>` : ''}
        </div>
        <div id="${escapeHtml(listId)}" data-list-container="${escapeHtml(key)}" class="plugin-list-rows">
          ${listVal.map((item, index) => `
            <div class="plugin-list-row">
              <input type="text" value="${escapeHtml(item)}" placeholder="${escapeHtml(placeholder)}" data-list-key="${escapeHtml(key)}" data-list-index="${index}" class="config-input" aria-label="${escapeHtml(label)}第 ${index + 1} 项">
              <button type="button" class="btn btn-sm btn-ghost plugin-icon-button" data-list-remove="${escapeHtml(key)}" aria-label="删除${escapeHtml(label)}第 ${index + 1} 项">✕</button>
            </div>
          `).join('')}
        </div>
        <button type="button" class="btn btn-sm btn-ghost plugin-add-inline" data-list-add="${escapeHtml(key)}">＋ ${escapeHtml(addLabel)}</button>
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

  _objectFieldValue(item, field) {
    const name = String(field?.name ?? '');
    if (item && Object.prototype.hasOwnProperty.call(item, name)) return item[name];
    if (field && Object.prototype.hasOwnProperty.call(field, 'default')) return field.default;
    const type = String(field?.type || 'str').toLowerCase();
    if (type === 'object') return {};
    if (type === 'json') return null;
    if (type === 'list' || type === 'array' || type === 'checkbox_group') return [];
    return '';
  }

  _objectCardSummary(item, index, fields, presentation) {
    const map = new Map(fields.map(field => [String(field?.name ?? ''), field]));
    const valueFor = name => {
      const field = map.get(String(name ?? ''));
      const value = field ? this._objectFieldValue(item, field) : '';
      if (value === '********' || value === '[已隐藏]' || value === '••••••••') return '';
      return value === null || value === undefined ? '' : String(value).trim();
    };
    const title = valueFor(presentation.item_title_field) ||
      valueFor(presentation.item_fallback_field) ||
      presentation.item_placeholder || `配置项 ${index + 1}`;
    const subtitle = valueFor(presentation.item_subtitle_field);
    const badge = valueFor(presentation.item_badge_field);
    const statusField = map.get(String(presentation.item_status_field ?? ''));
    const statusValue = statusField
      ? this._objectFieldValue(item, statusField) === true
      : null;
    const statusUi = presentation.fields?.[presentation.item_status_field] || {};
    const status = statusValue === null
      ? ''
      : (statusValue ? (statusUi.true_label || '已启用') : (statusUi.false_label || '已停用'));
    return { title, subtitle, badge, status, active: statusValue !== false };
  }

  _renderObjectFieldInput(key, index, field, fieldValue, presentation = {}, rowKey = `row${index}`) {
    const fieldName = String(field?.name ?? '');
    const fieldType = String(field?.type || 'str').toLowerCase();
    const escapedKey = escapeHtml(key);
    const escapedFieldName = escapeHtml(fieldName);
    const rowData = ` data-obj-row-key="${escapeHtml(rowKey)}"`;
    const inputId = safeDomId(`${key}_${rowKey}_${fieldName}`, 'plugin_object_input_');
    const fieldLabel = `${presentation.label || fieldName}（第 ${index + 1} 项）`;
    const currentValue = fieldValue !== undefined
      ? fieldValue
      : (field?.default !== undefined ? field.default : (fieldType === 'object' ? {} : null));
    if (isSecretConfigField(field)) {
      return `<div class="plugin-secret-note" role="note">由环境变量或 Secret 管理器提供，WebUI 不读取或保存该值。</div>`;
    }
    if (fieldType === 'checkbox_group' && Array.isArray(field?.choices) && field.choices.length) {
      const selected = new Set(Array.isArray(currentValue) ? currentValue : []);
      const groupId = safeDomId(`${key}_${rowKey}_${fieldName}`, 'plugin_object_choices_');
      const legendId = `${groupId}_label`;
      return `<fieldset class="plugin-choice-field plugin-choice-field--object" aria-labelledby="${escapeHtml(legendId)}">
        <legend id="${escapeHtml(legendId)}" class="sr-only">${escapeHtml(fieldLabel)}</legend>
        <div class="plugin-choice-grid">
          ${field.choices.map((choice, choiceIndex) => {
            const choiceId = `${groupId}_${choiceIndex}`;
            return `
            <label class="plugin-choice-pill" for="${escapeHtml(choiceId)}">
              <input id="${escapeHtml(choiceId)}" type="checkbox" data-obj-array-key="${escapedKey}" data-obj-idx="${index}"${rowData} data-obj-field="${escapedFieldName}" data-obj-checkbox="${escapeHtml(choice)}"${selected.has(choice) ? ' checked' : ''} aria-label="${escapeHtml(fieldLabel)}：${escapeHtml(choice)}">
              <span>${escapeHtml(choice)}</span>
            </label>
          `;
          }).join('')}
        </div>
      </fieldset>`;
    }
    if (fieldType === 'checkbox_group') {
      const jsonPath = `${key}.${rowKey}.${fieldName}`;
      const errorId = `${inputId}_error`;
      const rawValue = this._invalidJsonValues?.get(jsonPath);
      let serializedValue = rawValue === undefined ? '' : rawValue;
      if (rawValue === undefined) {
        try {
          serializedValue = JSON.stringify(Array.isArray(currentValue) ? currentValue : [], null, 2);
        } catch {
          serializedValue = '';
        }
      }
      return `<textarea id="${escapeHtml(inputId)}" data-obj-array-key="${escapedKey}" data-obj-idx="${index}"${rowData} data-obj-field="${escapedFieldName}" data-obj-json="true" data-json-path="${escapeHtml(jsonPath)}" data-json-expected="array" aria-label="${escapeHtml(fieldLabel)}" aria-describedby="${escapeHtml(errorId)}" class="config-input plugin-code-input" rows="4" spellcheck="false">${escapeHtml(serializedValue)}</textarea>
        <span id="${escapeHtml(errorId)}" class="plugin-json-error" data-json-error="${escapeHtml(jsonPath)}" role="alert" hidden></span>`;
    }
    if (fieldType === 'list' || fieldType === 'array') {
      const listItems = Array.isArray(currentValue) ? currentValue : [];
      const placeholder = presentation.item_placeholder || '';
      const addLabel = presentation.add_label || '添加一项';
      return `<div class="plugin-object-list">
        <div data-obj-list-container class="plugin-list-rows">
          ${listItems.map((item, listIndex) => `
            <div class="plugin-list-row">
              <input type="text" value="${escapeHtml(item)}" placeholder="${escapeHtml(placeholder)}" data-obj-array-key="${escapedKey}" data-obj-idx="${index}"${rowData} data-obj-field="${escapedFieldName}" data-obj-list-idx="${listIndex}" class="config-input" aria-label="${escapeHtml(fieldLabel)}第 ${listIndex + 1} 项">
              <button type="button" class="btn btn-sm btn-ghost plugin-icon-button" data-obj-list-remove="${escapedKey}" data-obj-idx="${index}"${rowData} data-obj-field="${escapedFieldName}" data-obj-list-idx="${listIndex}" aria-label="删除${escapeHtml(fieldLabel)}第 ${listIndex + 1} 项">✕</button>
            </div>
          `).join('')}
        </div>
        <button type="button" class="btn btn-sm btn-ghost plugin-add-inline" data-obj-list-add="${escapedKey}" data-obj-idx="${index}"${rowData} data-obj-field="${escapedFieldName}" aria-label="为${escapeHtml(fieldLabel)}添加列表项">＋ ${escapeHtml(addLabel)}</button>
      </div>`;
    }
    if (fieldType === 'boolean' || fieldType === 'bool') {
      const trueLabel = presentation.true_label || '已启用';
      const falseLabel = presentation.false_label || '已停用';
      const checked = currentValue === true;
      return `<label class="plugin-switch plugin-switch--compact">
        <input type="checkbox" id="${escapeHtml(inputId)}" data-obj-array-key="${escapedKey}" data-obj-idx="${index}"${rowData} data-obj-field="${escapedFieldName}"${checked ? ' checked' : ''}
          aria-label="${escapeHtml(fieldLabel)}" data-true-label="${escapeHtml(trueLabel)}" data-false-label="${escapeHtml(falseLabel)}">
        <span class="plugin-switch-track" aria-hidden="true"><span></span></span>
        <span class="plugin-switch-state" data-switch-state>${escapeHtml(checked ? trueLabel : falseLabel)}</span>
      </label>`;
    }
    if (fieldType === 'int' || fieldType === 'float' || fieldType === 'number') {
      const hasMinimum = field?.minimum !== null && field?.minimum !== undefined && field?.minimum !== '';
      const hasMaximum = field?.maximum !== null && field?.maximum !== undefined && field?.maximum !== '';
      const minimum = hasMinimum && Number.isFinite(Number(field.minimum)) ? ` min="${escapeHtml(field.minimum)}"` : '';
      const maximum = hasMaximum && Number.isFinite(Number(field.maximum)) ? ` max="${escapeHtml(field.maximum)}"` : '';
      const step = fieldType === 'int' ? '1' : 'any';
      return `<div class="plugin-number-row">
        <input type="number" id="${escapeHtml(inputId)}" data-obj-array-key="${escapedKey}" data-obj-idx="${index}"${rowData} data-obj-field="${escapedFieldName}" value="${escapeHtml(currentValue)}"${minimum}${maximum} step="${step}" class="config-input">
        ${presentation.unit ? `<span class="plugin-field-unit">${escapeHtml(presentation.unit)}</span>` : ''}
      </div>`;
    }
    if (fieldType === 'object' || fieldType === 'json' || (
      currentValue !== null && typeof currentValue === 'object'
    )) {
      const jsonPath = `${key}.${rowKey}.${fieldName}`;
      const rawValue = this._invalidJsonValues?.get(jsonPath);
      let serializedValue = rawValue === undefined ? '' : rawValue;
      if (rawValue === undefined) {
        try {
          serializedValue = JSON.stringify(
            currentValue === undefined ? (fieldType === 'object' ? {} : null) : currentValue,
            null,
            2,
          );
        } catch {
          serializedValue = '';
        }
      }
      const errorId = `${inputId}_error`;
      const expected = fieldType === 'object' ? 'object' : 'any';
      return `<textarea id="${escapeHtml(inputId)}" data-obj-array-key="${escapedKey}" data-obj-idx="${index}"${rowData} data-obj-field="${escapedFieldName}" data-obj-json="true" data-json-path="${escapeHtml(jsonPath)}" data-json-expected="${expected}" aria-describedby="${escapeHtml(errorId)}" class="config-input plugin-code-input" rows="4" spellcheck="false">${escapeHtml(serializedValue)}</textarea>
        <span id="${escapeHtml(errorId)}" class="plugin-json-error" data-json-error="${escapeHtml(jsonPath)}" role="alert" hidden></span>`;
    }
    const widget = presentation.widget || 'text';
    const placeholder = presentation.placeholder || field?.description || '';
    const codeLike = ['url', 'path', 'code'].includes(widget);
    const common = `id="${escapeHtml(inputId)}" data-obj-array-key="${escapedKey}" data-obj-idx="${index}"${rowData} data-obj-field="${escapedFieldName}" placeholder="${escapeHtml(placeholder)}" class="config-input${codeLike ? ' plugin-code-input' : ''}"${codeLike ? ' spellcheck="false"' : ''}`;
    return widget === 'textarea'
      ? `<textarea ${common} rows="3">${escapeHtml(currentValue)}</textarea>`
      : `<input type="${widget === 'url' ? 'url' : 'text'}" ${common} value="${escapeHtml(currentValue)}">`;
  }

  _renderObjectField(key, index, item, field, presentation = {}) {
    const fieldName = String(field?.name ?? '');
    const fieldType = String(field?.type || 'str').toLowerCase();
    const label = presentation.label || fieldName;
    const fieldLabel = `${label}（第 ${index + 1} 项）`;
    const help = presentation.help ?? field?.description ?? '';
    const defaultSpan = ['bool', 'boolean', 'int', 'float', 'number'].includes(fieldType) ? 6 : 12;
    const span = Number.isInteger(presentation.span) ? presentation.span : defaultSpan;
    const rowKey = this._objectRowStateId(item, index);
    const inputId = safeDomId(`${key}_${rowKey}_${fieldName}`, 'plugin_object_input_');
    const labelFor = ['str', 'string', 'bool', 'boolean', 'int', 'float', 'number', 'object', 'json'].includes(fieldType)
      && !isSecretConfigField(field)
      ? ` for="${escapeHtml(inputId)}"`
      : '';
    return `
      <div class="plugin-object-field${field?.identity === true ? ' plugin-object-field--identity' : ''}" style="--plugin-field-span:${Math.min(12, Math.max(1, span))}">
        <div class="plugin-object-field-copy">
          <label class="config-field-label"${labelFor}>${escapeHtml(fieldLabel)}${field?.required ? '<span class="config-required">*</span>' : ''}</label>
          ${help ? `<span class="plugin-field-help">${escapeHtml(help)}</span>` : ''}
        </div>
        ${this._renderObjectFieldInput(
          key,
          index,
          field,
          this._objectFieldValue(item, field),
          presentation,
          rowKey,
        )}
      </div>
    `;
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

  _renderObjectFieldset(key, index, item, fields, fieldset, presentation) {
    if (!fields.length) return '';
    const fieldHtml = fields.map(field => this._renderObjectField(
      key,
      index,
      item,
      field,
      presentation.fields?.[field?.name] || {},
    )).join('');
    const title = fieldset.title || '配置';
    const description = fieldset.description || '';
    const stateKey = `${key}_${this._objectRowStateId(item, index)}_${fieldset.id || title}`;
    return `
      <details class="plugin-object-fieldset" data-plugin-fieldset="${escapeHtml(stateKey)}"${fieldset.collapsed ? '' : ' open'}>
        <summary data-plugin-object-summary data-obj-array-key="${escapeHtml(key)}" data-obj-idx="${index}" data-obj-row-key="${escapeHtml(this._objectRowStateId(item, index))}">
          <span>${escapeHtml(title)}</span>
          ${description ? `<small>${escapeHtml(description)}</small>` : ''}
        </summary>
        <div class="plugin-object-grid">${fieldHtml}</div>
      </details>
    `;
  }

  _renderLegacyObjectArray(key, label, value, defaultVal, desc, fields) {
    const items = Array.isArray(value) ? value : (Array.isArray(defaultVal) ? defaultVal : []);
    const escapedKey = escapeHtml(key);
    const fieldDefs = Array.isArray(fields)
      ? fields.filter(field => isSafeConfigFieldName(field?.name))
      : [];
    return `
      <div class="config-field">
        <div class="config-field-header">
          <label class="config-field-label">${escapeHtml(label)}</label>
          ${desc ? `<span class="config-field-desc">${escapeHtml(desc)}</span>` : ''}
        </div>
        <div data-obj-array-container="${escapedKey}" class="plugin-object-cards">
          ${items.map((item, index) => {
            const rowKey = this._objectRowStateId(item, index);
            return `
            <div class="plugin-object-card" data-obj-array-item="${escapedKey}" data-obj-idx="${index}" data-obj-row-key="${escapeHtml(rowKey)}">
              <div class="plugin-object-card-header">
                <span class="plugin-object-card-number">#${index + 1}</span>
                <span class="plugin-object-card-copy"><strong>配置项 ${index + 1}</strong></span>
                <button type="button" class="btn btn-sm btn-ghost plugin-icon-button" data-obj-array-remove="${escapedKey}" data-obj-idx="${index}" data-obj-row-key="${escapeHtml(rowKey)}" aria-label="删除配置项 ${index + 1}">✕</button>
              </div>
              <div class="plugin-object-card-body"><div class="plugin-object-grid">
                ${fieldDefs.map(field => this._renderObjectField(
                  key,
                  index,
                  item,
                  field,
                  Object.create(null),
                )).join('')}
              </div></div>
            </div>
          `).join('')}
        </div>
        <button type="button" class="btn btn-sm btn-ghost plugin-add-inline" data-obj-array-add="${escapedKey}">＋ 添加</button>
      </div>
    `;
  }

  _renderObjectArray(key, label, value, defaultVal, desc, fields, presentation = {}) {
    if (!this.uiSchema) {
      return this._renderLegacyObjectArray(key, label, value, defaultVal, desc, fields);
    }
    const items = Array.isArray(value) ? value : (Array.isArray(defaultVal) ? defaultVal : []);
    const escapedKey = escapeHtml(key);
    const fieldDefs = Array.isArray(fields)
      ? fields.filter(field => isSafeConfigFieldName(field?.name))
      : [];
    const fieldMap = new Map(fieldDefs.map(field => [String(field?.name ?? ''), field]));
    const assigned = new Set();
    const fieldsets = [];
    for (const fieldset of presentation.fieldsets || []) {
      const selected = [];
      for (const name of fieldset.fields || []) {
        const field = fieldMap.get(name);
        if (field && !assigned.has(name)) {
          assigned.add(name);
          selected.push(field);
        }
      }
      if (selected.length) fieldsets.push({ ...fieldset, fields: selected });
    }
    const remaining = fieldDefs.filter(field => !assigned.has(String(field?.name ?? '')));
    if (remaining.length) {
      fieldsets.push({
        id: 'other',
        title: fieldsets.length ? '其他设置' : '站点设置',
        description: '',
        collapsed: false,
        fields: remaining,
      });
    }
    const addLabel = presentation.add_label || '添加配置项';
    const emptyTitle = presentation.empty_title || '还没有配置项';
    const emptyDescription = presentation.empty_description || '添加第一项后即可开始配置。';

    return `
      <div class="config-field plugin-object-array-field">
        <div class="config-field-header plugin-object-array-heading">
          <div>
            <label class="config-field-label">${escapeHtml(label)}</label>
            ${desc ? `<span class="plugin-field-help">${escapeHtml(desc)}</span>` : ''}
          </div>
          <span class="plugin-object-count">${items.length} 项</span>
        </div>
        <div id="${escapeHtml(safeDomId(key, 'plugin_object_array_'))}" data-obj-array-container="${escapedKey}" class="plugin-object-cards">
          ${items.length ? items.map((item, index) => {
            const rowKey = this._objectRowStateId(item, index);
            const summary = this._objectCardSummary(item, index, fieldDefs, presentation);
            const cardTitleId = safeDomId(`${key}_${rowKey}_title`, 'plugin_object_card_');
            const rowLabel = `${summary.title || '配置项'}（第 ${index + 1} 项）`;
            return `
              <article class="plugin-object-card${summary.active ? '' : ' is-disabled'}" data-obj-array-item="${escapedKey}" data-obj-idx="${index}" data-obj-row-key="${escapeHtml(rowKey)}" aria-labelledby="${escapeHtml(cardTitleId)}">
                <header class="plugin-object-card-header">
                  <span class="plugin-object-card-number" aria-hidden="true">${String(index + 1).padStart(2, '0')}</span>
                  <span class="plugin-object-card-copy">
                    <strong id="${escapeHtml(cardTitleId)}" data-obj-card-title>${escapeHtml(summary.title)}</strong>
                    <small data-obj-card-subtitle${summary.subtitle ? '' : ' hidden'}>${escapeHtml(summary.subtitle)}</small>
                  </span>
                  <span class="plugin-object-badge" data-obj-card-badge${summary.badge ? '' : ' hidden'}>${escapeHtml(summary.badge)}</span>
                  <span class="plugin-object-status${summary.active ? ' is-active' : ''}" data-obj-card-status${summary.status ? '' : ' hidden'}><i></i>${escapeHtml(summary.status)}</span>
                  <button type="button" class="btn btn-sm btn-ghost plugin-icon-button" data-obj-array-remove="${escapedKey}" data-obj-idx="${index}" data-obj-row-key="${escapeHtml(rowKey)}" aria-label="删除${escapeHtml(rowLabel)}">✕</button>
                </header>
                <div class="plugin-object-card-body">
                  ${fieldsets.map(fieldset => this._renderObjectFieldset(
                    key,
                    index,
                    item,
                    fieldset.fields,
                    fieldset,
                    presentation,
                  )).join('')}
                </div>
              </article>
            `;
          }).join('') : `
            <div class="plugin-object-empty">
              <span class="plugin-object-empty-mark" aria-hidden="true">＋</span>
              <strong>${escapeHtml(emptyTitle)}</strong>
              <p>${escapeHtml(emptyDescription)}</p>
            </div>
          `}
        </div>
        <button type="button" class="btn btn-primary plugin-object-add" data-obj-array-add="${escapedKey}">＋ ${escapeHtml(addLabel)}</button>
      </div>
    `;
  }

  _renderCheckboxGroup(key, label, value, defaultVal, desc, choices) {
    const selected = new Set(Array.isArray(value) ? value : (Array.isArray(defaultVal) ? defaultVal : []));
    const groupId = safeDomId(key, 'plugin_choices_');
    const helpId = `${groupId}_help`;
    const describedBy = desc ? ` aria-describedby="${escapeHtml(helpId)}"` : '';
    return `
      <fieldset class="config-field plugin-choice-field"${describedBy}>
        <legend class="config-field-label">${escapeHtml(label)}</legend>
        ${desc ? `<span id="${escapeHtml(helpId)}" class="config-field-desc">${escapeHtml(desc)}</span>` : ''}
        <div id="${escapeHtml(groupId)}" data-checkbox-group-container="${escapeHtml(key)}" class="plugin-choice-grid">
          ${(Array.isArray(choices) ? choices : []).map((choice, index) => {
            const choiceId = `${groupId}_${index}`;
            return `
            <label class="plugin-choice-pill" for="${escapeHtml(choiceId)}">
              <input id="${escapeHtml(choiceId)}" type="checkbox" data-checkbox-group="${escapeHtml(key)}" data-checkbox-value="${escapeHtml(choice)}"${selected.has(choice) ? ' checked' : ''}>
              <span>${escapeHtml(choice)}</span>
            </label>
          `;
          }).join('')}
        </div>
      </fieldset>
    `;
  }

  _normalizeObjectArrayListFields() {
    // List fields do not declare an element type. Never coerce or filter their
    // values here; untouched numeric/object elements must survive a snapshot.
  }

  _reindexListRows(listContainer, key, label) {
    if (!listContainer) return;
    listContainer.querySelectorAll('[data-list-key]').forEach((input, index) => {
      input.dataset.listIndex = String(index);
      input.setAttribute('aria-label', `${label}第 ${index + 1} 项`);
      const remove = input.closest('.plugin-list-row')?.querySelector('[data-list-remove]');
      if (remove) {
        remove.dataset.listRemove = key;
        remove.setAttribute('aria-label', `删除${label}第 ${index + 1} 项`);
      }
    });
  }

  _objectRowIndex(key, rowKey, fallbackIndex = -1) {
    const rows = this.settings?.[key];
    if (!Array.isArray(rows)) return -1;
    if (rowKey) {
      const matched = rows.findIndex((item, index) => (
        this._objectRowStateId(item, index) === String(rowKey)
      ));
      return matched;
    }
    const fallback = Number.parseInt(fallbackIndex, 10);
    return Number.isInteger(fallback) && fallback >= 0 && fallback < rows.length ? fallback : -1;
  }

  _objectRowKey(key, index) {
    const row = this.settings?.[key]?.[index];
    return row ? this._objectRowStateId(row, index) : `row${index}`;
  }

  _focusObjectField(key, index, field, listIndex = null, rowKey = '') {
    this._focusAfterRender = { array: key, index, field, rowKey };
    if (listIndex !== null && listIndex !== undefined) {
      this._focusAfterRender.listIndex = listIndex;
    }
  }

  _removeListRow(button) {
    const row = button?.closest('.plugin-list-row');
    const listContainer = row?.parentElement;
    if (!row || !listContainer || !confirmDanger()) return;
    const key = button.dataset.listRemove || '';
    const param = this.parameters.find(item => String(item?.name ?? '') === key);
    const presentation = this._parameterPresentation(key);
    const label = presentation.label || param?.name || key;
    const rows = Array.from(listContainer.querySelectorAll('.plugin-list-row'));
    const rowIndex = rows.indexOf(row);
    const neighbor = rows[rowIndex + 1] || rows[rowIndex - 1] || null;
    row.remove();
    this._reindexListRows(listContainer, key, label);
    this._snapshotFormValues();
    const target = neighbor?.isConnected
      ? neighbor.querySelector('[data-list-key]')
      : button.closest('.config-field')?.querySelector('[data-list-add]');
    target?.focus();
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
      if (input && this._invalidJsonValues) this._invalidJsonValues.set(key, input.value);
      if (error) {
        error.hidden = false;
        error.textContent = message;
      }
    } else {
      this._jsonErrors.delete(key);
      this._invalidJsonValues?.delete(key);
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

  _pruneJsonErrors(container) {
    const liveKeys = new Set(Array.from(container.querySelectorAll(
      '[data-json-setting-key], [data-obj-json]',
    )).map(input => input.dataset.jsonSettingKey || input.dataset.jsonPath));
    for (const key of this._jsonErrors) {
      if (!liveKeys.has(key)) this._jsonErrors.delete(key);
    }
    for (const key of this._invalidJsonValues?.keys() || []) {
      if (!liveKeys.has(key)) this._invalidJsonValues.delete(key);
    }
  }

  _syncJsonValues(container) {
    this._pruneJsonErrors(container);
    container.querySelectorAll('[data-json-setting-key]').forEach(input => {
      const key = input.dataset.jsonSettingKey;
      if (!key) return;
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
    const path = input.dataset.jsonPath || `${key}.${input.dataset.objRowKey || input.dataset.objIdx}.${field}`;
    const index = this._objectRowIndex(key, input.dataset.objRowKey, input.dataset.objIdx);
    if (!key || index < 0 || !field || !this.settings[key]?.[index]) {
      this._setJsonError(container, path, '此配置项已不存在，请重新添加。');
      return false;
    }
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

  _snapshotFormValues() {
    const container = this._formContainer();
    if (!container) return;

    this._syncJsonValues(container);
    this._syncObjectJsonValues(container);
    container.querySelectorAll('[data-setting-key]').forEach(input => {
      const key = input.dataset.settingKey;
      if (!key) return;
      if (input.type === 'checkbox') {
        this.settings[key] = input.checked;
      } else if (input.type === 'number') {
        const numberValue = parseConfigNumber(input.value);
        if (numberValue === undefined) delete this.settings[key];
        else this.settings[key] = numberValue;
      } else {
        this.settings[key] = input.value;
      }
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
    this._normalizeObjectArrayListFields();
  }

  destroy() {
    this._modelSelects?.forEach(select => select.destroy());
    this._modelSelects = [];
  }

  _syncSwitchState(input) {
    const state = input.closest('.plugin-switch')?.querySelector('[data-switch-state]');
    if (!state) return;
    state.textContent = input.checked
      ? (input.dataset.trueLabel || '已启用')
      : (input.dataset.falseLabel || '已停用');
  }

  _syncObjectCardSummary(key, index, rowKey = '') {
    const param = (this.parameters || []).find(item => String(item?.name ?? '') === key);
    if (!param || !Array.isArray(param.fields) || !this.settings[key]?.[index]) return;
    const presentation = this._parameterPresentation(key);
    const summary = this._objectCardSummary(
      this.settings[key][index],
      index,
      param.fields,
      presentation,
    );
    const card = Array.from(this._formContainer()?.querySelectorAll('[data-obj-array-item]') || [])
      .find(item => item.dataset.objArrayItem === key && (
        rowKey ? item.dataset.objRowKey === rowKey : Number(item.dataset.objIdx) === index
      ));
    if (!card) return;
    const title = card.querySelector('[data-obj-card-title]');
    const subtitle = card.querySelector('[data-obj-card-subtitle]');
    const badge = card.querySelector('[data-obj-card-badge]');
    const status = card.querySelector('[data-obj-card-status]');
    if (title) title.textContent = summary.title;
    if (subtitle) {
      subtitle.textContent = summary.subtitle;
      subtitle.hidden = !summary.subtitle;
    }
    if (badge) {
      badge.textContent = summary.badge;
      badge.hidden = !summary.badge;
    }
    if (status) {
      const marker = document.createElement('i');
      status.replaceChildren(marker, document.createTextNode(summary.status));
      status.hidden = !summary.status;
      status.classList.toggle('is-active', summary.active);
    }
    card.classList.toggle('is-disabled', !summary.active);
    const remove = card.querySelector('[data-obj-array-remove]');
    if (remove) remove.setAttribute('aria-label', `删除${summary.title}（第 ${index + 1} 项）`);
  }

  _bindEvents() {
    const container = this._formContainer();
    if (!container) return;
    if (container.tagName === 'FORM') {
      container.onsubmit = event => event.preventDefault();
    }

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

    container.querySelectorAll('[data-plugin-section-toggle]').forEach(header => {
      header.addEventListener('click', () => {
        const section = header.closest('.plugin-config-section');
        const body = section?.querySelector('.plugin-config-section-body');
        if (!body) return;
        body.hidden = !body.hidden;
        header.setAttribute('aria-expanded', body.hidden ? 'false' : 'true');
      });
    });

    container.querySelectorAll('[data-setting-key]').forEach(input => {
      input.addEventListener(input.type === 'checkbox' ? 'change' : 'input', () => {
        this._snapshotFormValues();
        if (input.type === 'checkbox') this._syncSwitchState(input);
      });
    });
    container.querySelectorAll('[data-json-setting-key]').forEach(input => {
      input.addEventListener('input', () => this._syncJsonValues(container));
    });
    this._syncJsonValues(container);
    this._syncObjectJsonValues(container);

    container.querySelectorAll('[data-list-add]').forEach(button => {
      button.addEventListener('click', () => {
        const key = button.dataset.listAdd;
        const listContainer = button.closest('.config-field')?.querySelector('[data-list-container]');
        if (!listContainer) return;
        const index = listContainer.querySelectorAll('[data-list-key]').length;
        const param = this.parameters.find(item => String(item?.name ?? '') === key);
        const presentation = this._parameterPresentation(key);
        const label = presentation.label || param?.name || key;
        const row = document.createElement('div');
        row.className = 'plugin-list-row';
        const input = document.createElement('input');
        input.type = 'text';
        input.dataset.listKey = key;
        input.dataset.listIndex = String(index);
        input.className = 'config-input';
        input.placeholder = presentation.item_placeholder || '';
        input.setAttribute('aria-label', `${label}第 ${index + 1} 项`);
        input.addEventListener('input', () => this._snapshotFormValues());
        const remove = document.createElement('button');
        remove.type = 'button';
        remove.className = 'btn btn-sm btn-ghost plugin-icon-button';
        remove.dataset.listRemove = key;
        remove.setAttribute('aria-label', `删除${label}第 ${index + 1} 项`);
        remove.textContent = '✕';
        remove.addEventListener('click', () => this._removeListRow(remove));
        row.append(input, remove);
        listContainer.appendChild(row);
        input.focus();
      });
    });

    container.querySelectorAll('[data-list-remove]').forEach(button => {
      button.addEventListener('click', () => this._removeListRow(button));
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
        const newItem = createObjectArrayItem(param.fields);
        if (!Array.isArray(this.settings[key])) this.settings[key] = [];
        this.settings[key].push(newItem);
        const newIndex = this.settings[key].length - 1;
        const firstField = param.fields.find(field => (
          isSafeConfigFieldName(field?.name) && !isSecretConfigField(field)
        ));
        this._focusAfterRender = firstField
          ? {
            array: key,
            index: newIndex,
            rowKey: this._objectRowStateId(newItem, newIndex),
            field: String(firstField.name),
          }
          : { objArrayAdd: key };
        this.render();
      });
    });

    container.querySelectorAll('[data-obj-array-remove]').forEach(button => {
      button.addEventListener('click', () => {
        const key = button.dataset.objArrayRemove;
         const index = this._objectRowIndex(key, button.dataset.objRowKey, button.dataset.objIdx);
        if (Array.isArray(this.settings[key]) && index >= 0 && confirmDanger()) {
          this._snapshotFormValues();
          this.settings[key].splice(index, 1);
          if (this.settings[key].length) {
            const param = this.parameters.find(item => String(item?.name ?? '') === key);
            const firstField = param?.fields?.find(field => (
              isSafeConfigFieldName(field?.name) && !isSecretConfigField(field)
            ));
            this._focusAfterRender = firstField
              ? {
                 array: key,
                 index: Math.min(index, this.settings[key].length - 1),
                 rowKey: this._objectRowKey(key, Math.min(index, this.settings[key].length - 1)),
                 field: String(firstField.name),
               }
              : null;
          } else {
            this._focusAfterRender = { objArrayAdd: key };
          }
          this.render();
        }
      });
    });

    container.querySelectorAll('[data-obj-array-key]').forEach(input => {
      const eventType = input.type === 'checkbox' ? 'change' : 'input';
      input.addEventListener(eventType, () => {
        const key = input.dataset.objArrayKey;
        const index = this._objectRowIndex(key, input.dataset.objRowKey, input.dataset.objIdx);
        const field = input.dataset.objField;
        if (!isSafeConfigFieldName(field) || !this.settings[key]?.[index]) return;

        if (input.dataset.objJson !== undefined) {
          this._syncObjectJsonValue(input, container);
          return;
        }
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
          const numberValue = parseConfigNumber(input.value);
          if (numberValue === undefined) delete this.settings[key][index][field];
          else this.settings[key][index][field] = numberValue;
        } else {
          this.settings[key][index][field] = input.value;
        }
        if (input.type === 'checkbox') this._syncSwitchState(input);
        this._syncObjectCardSummary(key, index, input.dataset.objRowKey || '');
      });
    });

    container.querySelectorAll('[data-obj-list-add]').forEach(button => {
      button.addEventListener('click', () => {
        const key = button.dataset.objListAdd;
        this._snapshotFormValues();
        const index = this._objectRowIndex(key, button.dataset.objRowKey, button.dataset.objIdx);
        const field = button.dataset.objField;
        if (!this.settings[key]?.[index]) return;
        let values = this.settings[key][index][field];
        if (!Array.isArray(values)) {
          values = [''];
          this.settings[key][index][field] = values;
        } else {
          values.push('');
        }
        this._focusAfterRender = {
          array: key,
          index,
          rowKey: button.dataset.objRowKey || this._objectRowKey(key, index),
          field,
          listIndex: Array.isArray(this.settings[key][index][field])
            ? this.settings[key][index][field].length - 1
            : 0,
        };
        this.render();
      });
    });

    container.querySelectorAll('[data-obj-list-remove]').forEach(button => {
      button.addEventListener('click', () => {
        const key = button.dataset.objListRemove;
        const index = this._objectRowIndex(key, button.dataset.objRowKey, button.dataset.objIdx);
        const field = button.dataset.objField;
        const listIndex = parseInt(button.dataset.objListIdx, 10);
        const values = this.settings[key]?.[index]?.[field];
        if (Array.isArray(values) && confirmDanger()) {
          this._snapshotFormValues();
          values.splice(listIndex, 1);
          if (values.length) {
            this._focusAfterRender = {
              array: key,
              index,
              field,
              rowKey: button.dataset.objRowKey || this._objectRowKey(key, index),
               listIndex: Math.min(listIndex, values.length - 1),
            };
          } else {
            this._focusAfterRender = { objListAdd: { key, index, field } };
          }
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
        target.dispatchEvent(new Event('input', { bubbles: true }));
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
        ariaLabel: element.dataset.modelLabel || key,
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
    if (this._jsonErrors.size) {
      throw new Error('参数中包含无效的 JSON。');
    }
    const values = {};
    const container = this._formContainer();
    if (!container) return values;

    container.querySelectorAll('[data-setting-key]').forEach(input => {
      const key = input.dataset.settingKey;
      if (!key) return;
      if (input.type === 'checkbox') {
        values[key] = input.checked;
      } else if (input.type === 'number') {
        const numberValue = parseConfigNumber(input.value);
        if (numberValue !== undefined) values[key] = numberValue;
      } else if (input.tagName === 'SELECT') values[key] = input.value || '';
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
    container.querySelectorAll('[data-json-setting-key]').forEach(input => {
      const key = input.dataset.jsonSettingKey;
      if (key && Object.prototype.hasOwnProperty.call(this.settings, key)) {
        values[key] = this.settings[key];
      }
    });

    for (const [key, schedule] of Object.entries(this._scheduleData || {})) {
      if (Array.isArray(schedule)) values[key] = schedule;
    }
    for (const param of this.parameters || []) {
      const key = String(param?.name ?? '');
      const type = String(param?.type || 'str').toLowerCase();
      if (type === 'object_array' && Array.isArray(this.settings?.[key])) {
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

    el.innerHTML = `<div class="plugin-list-grid">${plugins.map(p => {
      const name = escapeHtml(p.name);
      const version = escapeHtml(p.version || '—');
      const displayName = escapeHtml(p.display_name || p.name);
      const description = p.description ? escapeHtml(p.description) : '';
      const commandCount = Array.isArray(p.commands) ? p.commands.length : 0;
      const parameterCount = Array.isArray(p.parameters) ? p.parameters.length : 0;
      const enabled = p.enabled === true;
      const toggleLabel = enabled ? `禁用${displayName}` : `启用${displayName}`;
      return `
      <article class="card plugin-card">
        <button type="button" class="plugin-detail-btn" data-name="${name}" aria-label="打开${displayName}配置">
          <span class="plugin-card-body">
            <span class="plugin-card-heading">
              <span class="tag plugin-version-tag">${version}</span>
              <span class="plugin-card-title">${displayName}</span>
            </span>
            ${description ? `<span class="plugin-card-description">${description}</span>` : ''}
            <span class="plugin-card-meta">
              <span>命令：${commandCount}</span>
              <span>参数：${parameterCount}</span>
            </span>
          </span>
        </button>
        <button type="button" class="plugin-toggle tag${enabled ? ' plugin-toggle--enabled' : ''}" data-name="${name}" data-label="${displayName}" aria-pressed="${enabled ? 'true' : 'false'}" aria-label="${toggleLabel}">${enabled ? '已启用' : '已禁用'}</button>
      </article>
    `;
    }).join('')}</div>`;

    el.querySelectorAll('.plugin-toggle').forEach(toggle => {
      toggle.addEventListener('click', (event) => {
        event.stopPropagation();
        const name = toggle.dataset.name;
        const newState = toggle.getAttribute('aria-pressed') !== 'true';
        setPluginToggleVisual(toggle, newState);
        togglePlugin(name, newState, toggle);
      });
    });

    el.querySelectorAll('.plugin-detail-btn').forEach(button => {
      button.addEventListener('click', () => openDetail(button.dataset.name));
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
      if (tagEl) setPluginToggleVisual(tagEl, !enabled);
    }
  } catch (e) {
    if (e?.name === 'AbortError') return;
    toast('操作失败: ' + e.message, 'error');
    if (tagEl) setPluginToggleVisual(tagEl, !enabled);
  }
}

async function openDetail(name) {
  const opener = document.activeElement;
  closeModal(false);
  modalReturnFocus = opener && opener.isConnected ? opener : null;
  const overlay = document.createElement('div');
  overlay.className = 'modal-overlay';
  overlay.innerHTML = `
    <div class="modal" style="max-width:720px" role="dialog" aria-modal="true" aria-labelledby="modalTitle" tabindex="-1">
      <div class="modal-header">
        <span id="modalTitle" style="font-size:16px;font-weight:600">加载中...</span>
        <button type="button" class="btn btn-sm" id="modalClose" aria-label="关闭插件配置">✕</button>
      </div>
      <div class="modal-body" id="modalBody">
        <div style="padding:20px;text-align:center;color:var(--text-3)">加载中...</div>
      </div>
      <div class="modal-footer" id="modalFooter"></div>
    </div>
  `;
  document.body.appendChild(overlay);
  currentModal = overlay;
  isolateModal(overlay);

  overlay.addEventListener('click', (e) => {
    if (e.target === overlay) closeModal();
  });
  overlay.addEventListener('focusin', (event) => {
    if (!isActiveModal(overlay)) return;
    const modal = overlay.querySelector('.modal');
    if (!modal || modal.contains(event.target)) return;
    const fallback = Array.from(modal.querySelectorAll(
      'button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [href], [tabindex]:not([tabindex="-1"])',
    )).find(isVisibleFocusable);
    (fallback || modal).focus();
  });
  overlay.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') {
      event.preventDefault();
      event.stopPropagation();
      closeModal();
      return;
    }
    if (event.key !== 'Tab') return;
    const modal = overlay.querySelector('.modal');
    if (!modal) return;
    const focusable = Array.from(modal.querySelectorAll(
      'button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [href], [tabindex]:not([tabindex="-1"])',
    )).filter(isVisibleFocusable);
    if (!focusable.length) {
      event.preventDefault();
      modal.focus();
      return;
    }
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  });
  overlay.querySelector('#modalClose').addEventListener('click', closeModal);
  overlay.querySelector('#modalClose').focus();

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

function closeModal(restoreFocus = true) {
  configForm?.destroy?.();
  configForm = null;
  const returnFocus = modalReturnFocus;
  modalReturnFocus = null;
  if (currentModal) {
    currentModal.remove();
    currentModal = null;
  }
  restoreModalSiblings();
  if (restoreFocus && returnFocus?.isConnected && isVisibleFocusable(returnFocus)) returnFocus.focus();
  else if (restoreFocus) focusPageFallback();
}

function renderModalContent(d, overlay) {
  if (!isActiveModal(overlay)) return;
  const title = modalElement('modalTitle', overlay);
  if (title) title.textContent = d.display_name || d.name;

  const commands = Array.isArray(d.commands) ? d.commands : [];
  const parameters = Array.isArray(d.parameters) ? d.parameters : [];
  const uiSchema = normalizePluginUISchema(d.ui_schema, parameters);
  const modal = overlay.querySelector('.modal');
  if (modal) modal.classList.toggle('plugin-config-modal--wide', uiSchema?.layout === 'wide');
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

    <section id="pluginParamsSection" class="plugin-params-panel">
      <div class="plugin-panel-heading">
        <span>参数配置</span>
        <small>${uiSchema ? '可视化 Schema' : '通用表单'}</small>
      </div>
      <form id="paramsForm" class="plugin-params-form"></form>
    </section>

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
      containerId: 'paramsForm',
      containerElement: modalElement('paramsForm', overlay),
      parameters,
      settings,
      uiSchema,
      get,
    });
    let formReady = false;
    configForm = form;
    if (modalSaveButton()) modalSaveButton().disabled = true;
    form.init().then(() => {
      if (!isActiveModal(overlay) || configForm !== form) return;
      form.render();
      const paramsForm = modalElement('paramsForm', overlay);
      if (paramsForm && !paramsForm.children.length && section) {
        section.style.display = 'none';
      }
      formReady = true;
    }).catch(() => {
      formReady = false;
      if (!isActiveModal(overlay) || configForm !== form) return;
      toast('插件参数初始化失败', 'error');
    }).finally(() => {
      const saveButton = modalSaveButton();
      if (isActiveModal(overlay) && configForm === form && saveButton) {
        saveButton.disabled = !formReady;
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
      // The settings endpoint is a full-form replacement. Send an explicit
      // empty object too, so clearing the last ordinary field is persisted;
      // the backend retains only fields the browser cannot safely edit.
      await post(pluginPath(name, '/settings'), { settings: newSettings });
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
