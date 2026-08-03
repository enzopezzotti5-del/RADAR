const THEME_KEY = "radar-theme";

function getThemePreference() {
  try {
    return localStorage.getItem(THEME_KEY) || "auto";
  } catch {
    return "auto";
  }
}

function setThemePreference(mode) {
  const normalized = ["light", "dark", "auto"].includes(mode) ? mode : "auto";
  try {
    localStorage.setItem(THEME_KEY, normalized);
  } catch {
    // ignore storage failures
  }
  applyTheme(normalized);
  syncThemeControls(normalized);
  return normalized;
}

function resolveTheme(mode = getThemePreference()) {
  if (mode === "light" || mode === "dark") return mode;
  return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function applyTheme(mode = getThemePreference()) {
  const theme = resolveTheme(mode);
  const root = document.documentElement;
  root.dataset.theme = theme;
  root.setAttribute("data-bs-theme", theme);
  root.style.colorScheme = theme;
  return theme;
}

function syncThemeControls(mode = getThemePreference()) {
  document.querySelectorAll("[data-radar-theme-select]").forEach((el) => {
    if (el.value !== mode) el.value = mode;
  });
}

function initTheme() {
  const preference = getThemePreference();
  applyTheme(preference);
  syncThemeControls(preference);

  if (window.matchMedia) {
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const listener = () => {
      if (getThemePreference() === "auto") {
        applyTheme("auto");
      }
    };
    if (typeof mq.addEventListener === "function") {
      mq.addEventListener("change", listener);
    } else if (typeof mq.addListener === "function") {
      mq.addListener(listener);
    }
  }

  document.addEventListener("change", (event) => {
    const select = event.target.closest?.("[data-radar-theme-select]");
    if (!select) return;
    setThemePreference(select.value);
  });
}

function currentNextPath() {
  const next = `${window.location.pathname}${window.location.search || ""}`;
  return next.startsWith("//") ? "/" : next;
}

function redirectToLogin() {
  if (window.location.pathname === "/login") return;
  window.location.assign(`/login?next=${encodeURIComponent(currentNextPath())}`);
}

async function apiFetch(input, init = {}) {
  const response = await fetch(input, init);
  if (response.status === 401) {
    redirectToLogin();
    throw new Error("NAO_AUTENTICADO");
  }
  return response;
}

async function apiJson(input, init = {}) {
  const response = await apiFetch(input, init);
  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    const message = payload?.erro || payload?.error || `Falha ${response.status}`;
    throw new Error(message);
  }
  return payload;
}

async function logout() {
  try {
    await fetch("/logout", { method: "POST" });
  } finally {
    window.location.assign("/login");
  }
}

function formatDuration(seconds) {
  const total = Number(seconds);
  if (!Number.isFinite(total) || total < 0) return "—";
  if (total < 60) return `${Math.round(total)}s`;
  const minutes = Math.round(total / 60);
  const hours = Math.floor(minutes / 60);
  const remain = minutes % 60;
  if (!hours) return `${minutes} min`;
  return remain ? `${hours} h ${remain} min` : `${hours} h`;
}

function bindThemeSelect(select) {
  if (!select) return;
  select.value = getThemePreference();
}

function init() {
  initTheme();
  document.querySelectorAll("[data-radar-theme-select]").forEach(bindThemeSelect);
}

if (typeof window !== "undefined") {
  window.RadarApp = {
    THEME_KEY,
    getThemePreference,
    setThemePreference,
    resolveTheme,
    applyTheme,
    apiFetch,
    apiJson,
    logout,
    formatDuration,
    bindThemeSelect,
    initTheme,
  };
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init, { once: true });
  } else {
    init();
  }
}

export {
  THEME_KEY,
  getThemePreference,
  setThemePreference,
  resolveTheme,
  applyTheme,
  apiFetch,
  apiJson,
  logout,
  formatDuration,
  bindThemeSelect,
  initTheme,
};
