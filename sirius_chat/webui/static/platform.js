// ── Theme ─────────────────────────────────────────────
const THEMES = [
  { id: 'dark',  label: '暗色',  icon: '🌙' },
  { id: 'light', label: '亮色',  icon: '☀️' },
  { id: 'blue',  label: '午夜蓝', icon: '🔷' },
  { id: 'green', label: '森林绿', icon: '🌿' },
  { id: 'pink',  label: '樱花粉', icon: '🌸' },
];

function applyTheme(themeId) {
  const html = document.documentElement;
  const t = THEMES.find((t) => t.id === themeId) || THEMES[0];
  if (themeId === 'dark') {
    html.removeAttribute('data-theme');
  } else {
    html.setAttribute('data-theme', themeId);
  }
  const label = $('themeDropdownLabel');
  if (label) label.textContent = `${t.icon} ${t.label}`;
  themeSyncList();
}

function themeToggleDropdown() {
  const list = $('themeDropdownList');
  const arrow = $('themeDropdownArrow');
  if (!list) return;
  const open = list.style.display === 'block';
  list.style.display = open ? 'none' : 'block';
  if (arrow) arrow.style.transform = open ? 'rotate(0deg)' : 'rotate(180deg)';
  if (!open) {
    const close = (e) => {
      if (!list.contains(e.target) && !$('themeDropdown').contains(e.target)) {
        list.style.display = 'none';
        if (arrow) arrow.style.transform = 'rotate(0deg)';
        document.removeEventListener('click', close);
      }
    };
    setTimeout(() => document.addEventListener('click', close), 0);
  }
}

function themeSyncList() {
  const list = $('themeDropdownList');
  if (!list) return;
  const current = document.documentElement.getAttribute('data-theme') || 'dark';
  list.innerHTML = THEMES.map((t) => {
    const active = t.id === current;
    return `<div onclick="themeSelect('${t.id}')" class="diary-dropdown-item" style="padding:8px 12px;font-size:13px;cursor:pointer;color:${active ? 'var(--accent)' : 'var(--text)'};background:${active ? 'var(--surface-2)' : 'transparent'};border-radius:6px;margin:2px 4px"
      onmouseenter="this.style.background='var(--surface-2)'" onmouseleave="this.style.background='${active ? 'var(--surface-2)' : 'transparent'}'">${t.icon} ${t.label}</div>`;
  }).join('');
}

function themeSelect(id) {
  applyTheme(id);
  try { localStorage.setItem('sirius-theme', id); } catch (e) {}
  const list = $('themeDropdownList');
  const arrow = $('themeDropdownArrow');
  if (list) list.style.display = 'none';
  if (arrow) arrow.style.transform = 'rotate(0deg)';
}

function initTheme() {
  let theme = 'dark';
  try { theme = localStorage.getItem('sirius-theme') || 'dark'; } catch (e) {}
  applyTheme(theme);
}

// ── Init ──────────────────────────────────────────────
(async function init() {
  initTheme();
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
