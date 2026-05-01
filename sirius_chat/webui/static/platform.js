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
  const btn = $('themeToggle');
  const t = THEMES.find((t) => t.id === themeId) || THEMES[0];
  if (themeId === 'dark') {
    html.removeAttribute('data-theme');
  } else {
    html.setAttribute('data-theme', themeId);
  }
  if (btn) {
    btn.textContent = t.icon;
    btn.title = `主题: ${t.label} (点击切换)`;
  }
}

function toggleTheme() {
  const current = document.documentElement.getAttribute('data-theme') || 'dark';
  const idx = THEMES.findIndex((t) => t.id === current);
  const next = THEMES[(idx + 1) % THEMES.length].id;
  applyTheme(next);
  try { localStorage.setItem('sirius-theme', next); } catch (e) {}
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
