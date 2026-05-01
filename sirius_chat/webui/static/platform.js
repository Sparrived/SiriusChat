// ── Theme ─────────────────────────────────────────────
function applyTheme(theme) {
  const html = document.documentElement;
  const btn = $('themeToggle');
  if (theme === 'light') {
    html.setAttribute('data-theme', 'light');
    if (btn) btn.textContent = '☀️';
  } else {
    html.removeAttribute('data-theme');
    if (btn) btn.textContent = '🌙';
  }
}

function toggleTheme() {
  const current = document.documentElement.getAttribute('data-theme');
  const next = current === 'light' ? 'dark' : 'light';
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
