/**
 * Página Execuções — catálogo + formulário + live runs + histórico.
 */
import { toast }       from "../components/toast.js";
import { LogDrawer }   from "../components/drawer.js";
import { statusBadge } from "../components/badge.js";
import { apiJson }     from "../app.js";

const API    = "/api";
const drawer = new LogDrawer();

let allTasks   = {};
let liveRuns   = [];
let histRuns   = [];
let activeTask = null;
let dashData   = {};

// ── boot ─────────────────────────────────────────────────────────────────────

document.addEventListener("DOMContentLoaded", async () => {
  await Promise.all([loadTasks(), loadLive(), loadHistory(), loadDash()]);
  renderCatalog();
  renderKpis();
  renderLive();
  renderHistory();
  setInterval(tick, 3000);
});

async function tick() {
  await Promise.all([loadLive(), loadHistory(), loadDash()]);
  renderKpis();
  renderLive();
  renderHistory();
}

// ── data ──────────────────────────────────────────────────────────────────────

async function loadTasks() {
  try {
    const d = await apiJson(`${API}/tasks`);
    allTasks = d.tasks || {};
  } catch (error) {
    allTasks = {};
    if (error.message !== "NAO_AUTENTICADO") {
      toast(error.message || "Erro ao carregar tarefas", "error");
    }
  }
}
async function loadLive() {
  try {
    const d = await apiJson(`${API}/runs/live`);
    liveRuns = d.runs || [];
  } catch (error) {
    liveRuns = [];
    if (error.message !== "NAO_AUTENTICADO") {
      toast(error.message || "Erro ao carregar execuções", "error");
    }
  }
}
async function loadHistory() {
  try {
    const d = await apiJson(`${API}/runs/history?limit=150`);
    histRuns = d.runs || [];
  } catch (error) {
    histRuns = [];
    if (error.message !== "NAO_AUTENTICADO") {
      toast(error.message || "Erro ao carregar histórico", "error");
    }
  }
}
async function loadDash() {
  try {
    dashData = await apiJson(`${API}/dashboard`);
  } catch (error) {
    dashData = {};
    if (error.message !== "NAO_AUTENTICADO") {
      toast(error.message || "Erro ao carregar painel", "error");
    }
  }
}

// ── KPIs ──────────────────────────────────────────────────────────────────────

function renderKpis() {
  set("kpi-running",  dashData.running_now    ?? "—");
  set("kpi-success",  dashData.success_today  ?? "—");
  set("kpi-error",    dashData.failed_today   ?? "—");
  const todaySuccess = Number(dashData.success_today || 0);
  const todayFailed  = Number(dashData.failed_today || 0);
  const todayTotal   = todaySuccess + todayFailed;
  const todayRate    = todayTotal > 0 ? Math.round((todaySuccess / todayTotal) * 100) : null;
  set("kpi-rate",     todayRate != null ? todayRate + "%" : "—");
  set("kpi-scheds",   dashData.scheduled_count ?? "—");
  set("kpi-avg",      formatDuration(dashData.avg_duration_s));
}
function set(id, val) {
  const el = document.getElementById(id);
  if (el) el.textContent = val;
}

// ── catálogo ──────────────────────────────────────────────────────────────────

function renderCatalog() {
  const container = document.getElementById("catalog");
  if (!container) return;
  container.innerHTML = "";

  for (const [category, tasks] of Object.entries(allTasks)) {
    const section = document.createElement("div");
    section.innerHTML = `<div class="nav-section-label">${category}</div>`;

    for (const task of tasks) {
      const item = document.createElement("div");
      const isActive = activeTask?.task_id === task.task_id;
      item.className = "radar-nav-item" + (isActive ? " active" : "") + (!task.exists ? " task-missing" : "");
      item.innerHTML = `
        <span style="flex:1;font-size:var(--text-sm);line-height:1.3">${task.name}</span>
        ${!task.exists ? `<span title="Script não encontrado" style="color:var(--c-error);font-size:10px">✕</span>` : ""}
      `;
      item.addEventListener("click", () => selectTask(task));
      section.appendChild(item);
    }
    container.appendChild(section);
  }
}

function selectTask(task) {
  activeTask = task;
  renderCatalog();
  renderForm(task);
}

// ── formulário de execução ────────────────────────────────────────────────────

const STAGE_OPTIONS = [
  { label: "Pipeline Completo", value: "" },
  { label: "Só OCR",            value: "--so-ocr" },
  { label: "Só Digitação",      value: "--so-digitacao" },
  { label: "Só Filtro",         value: "--so-filtro" },
];

function renderForm(task) {
  const area = document.getElementById("task-form-area");
  if (!area) return;

  area.innerHTML = `
    <div class="card">
      <div class="card-header">
        <span class="card-title">${task.name}</span>
        <span class="badge badge-category">${task.category}</span>
      </div>

      ${task.notes ? `<p class="task-notes">${task.notes}</p>` : ""}
      ${!task.exists ? `<div class="alert-missing">⚠ Script não encontrado: ${task.script}</div>` : ""}

      <div class="form-fields">
        ${task.supports_month_year ? `
          <div class="form-row">
            <div class="form-group">
              <label class="form-label">Mês</label>
              <input id="p-month" class="form-input" type="text" maxlength="2"
                     value="${String(new Date().getMonth()+1).padStart(2,"0")}">
            </div>
            <div class="form-group">
              <label class="form-label">Ano</label>
              <input id="p-year" class="form-input" type="text" maxlength="4"
                     value="${new Date().getFullYear()}">
            </div>
          </div>` : ""}

        ${task.supports_type ? `
          <div class="form-group">
            <label class="form-label">Tipo</label>
            <select id="p-type" class="form-select">
              <option value="bt" ${task.default_type==="bt"?"selected":""}>BT</option>
              <option value="mt" ${task.default_type==="mt"?"selected":""}>MT</option>
              <option value="ambos" ${task.default_type==="ambos"?"selected":""}>Ambos</option>
            </select>
          </div>` : ""}

        ${task.supports_stage_flags ? `
          <div class="form-group">
            <label class="form-label">Etapa</label>
            <select id="p-stage" class="form-select">
              ${STAGE_OPTIONS.map(o=>`<option value="${o.value}">${o.label}</option>`).join("")}
            </select>
          </div>` : ""}

        ${task.supports_pasta ? `
          <div class="form-group">
            <label class="form-label">Pasta ${task.pasta_template?"(auto por mês)":""}</label>
            <input id="p-pasta" class="form-input" type="text"
                   placeholder="${task.pasta_template || "Caminho da pasta..."}">
          </div>` : ""}

        ${task.download_condition_options?.length ? `
          <div class="form-group">
            <label class="form-label">Condição de download</label>
            <select id="p-cond" class="form-select">
              <option value="">Padrão</option>
              ${task.download_condition_options.map(o=>`<option value="${o}">${o}</option>`).join("")}
            </select>
          </div>` : ""}

        <div class="form-group">
          <label class="form-label">Args extras (opcional)</label>
          <input id="p-extra" class="form-input" type="text" placeholder="ex: --debug">
        </div>
      </div>

      <div class="form-actions">
        <button id="btn-run" class="btn btn-primary" ${!task.exists?"disabled":""}>
          <svg width="13" height="13" viewBox="0 0 16 16" fill="currentColor"><path d="M3.5 2l10 6-10 6V2z"/></svg>
          Executar
        </button>
      </div>
    </div>
  `;

  document.getElementById("btn-run")?.addEventListener("click", () => submitRun(task));
}

async function submitRun(task) {
  const g = id => document.getElementById(id)?.value ?? "";
  const payload = {
    task_id:            task.task_id,
    month:              g("p-month"),
    year:               g("p-year"),
    selected_type:      g("p-type"),
    stage_flag:         g("p-stage"),
    pasta:              g("p-pasta"),
    download_condition: g("p-cond"),
    extra_text:         g("p-extra"),
  };

  const btn = document.getElementById("btn-run");
  if (!btn) return;
  btn.disabled = true;
  btn.innerHTML = `<span class="spinner"></span> Iniciando…`;

  try {
    const data = await apiJson(`${API}/runs/start`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (data.ok) {
      toast(`${task.name} iniciado (#${data.run.run_id})`, "success");
      drawer.open(data.run.run_id, task.name);
      await tick();
    } else {
      toast(data.error || "Erro ao iniciar", "error");
    }
  } catch {
    toast("Erro de rede", "error");
  } finally {
    btn.disabled = false;
    btn.innerHTML = `<svg width="13" height="13" viewBox="0 0 16 16" fill="currentColor"><path d="M3.5 2l10 6-10 6V2z"/></svg> Executar`;
  }
}

// ── live runs ─────────────────────────────────────────────────────────────────

function renderLive() {
  const tbody = document.getElementById("live-runs-body");
  if (!tbody) return;

  if (!liveRuns.length) {
    tbody.innerHTML = `<tr><td colspan="6" style="text-align:center;color:var(--c-text-dim);padding:var(--space-6)">Nenhuma execução ativa</td></tr>`;
    return;
  }

  tbody.innerHTML = liveRuns.map(r => `
    <tr>
      <td style="color:var(--c-text-muted);font-size:var(--text-xs)">#${r.run_id}</td>
      <td>${r.task_name}</td>
      <td><span class="badge badge-category">${r.category}</span></td>
      <td>${badgeHtml(r.is_running ? "running" : statusKey(r.status_text))}</td>
      <td style="color:var(--c-text-muted);font-size:var(--text-xs)">${(r.started_at||"").slice(11,19)}</td>
      <td>
        <button class="btn btn-sm btn-ghost" onclick="openLog(${r.run_id},'${esc(r.task_name)}')">Logs</button>
        ${r.is_running
          ? `<button class="btn btn-sm btn-danger" onclick="stopRun(${r.run_id})">Stop</button>`
          : `<button class="btn btn-sm btn-secondary" onclick="rerunRun(${r.run_id})">Rerun</button>`}
      </td>
    </tr>
  `).join("");
}

// ── histórico ─────────────────────────────────────────────────────────────────

function renderHistory() {
  const tbody = document.getElementById("history-body");
  if (!tbody) return;

  const finishedRuns = histRuns.filter((r) => r.status !== "running" && r.status !== "pending");

  tbody.innerHTML = finishedRuns.map(r => `
    <tr>
      <td style="color:var(--c-text-muted);font-size:var(--text-xs)">#${r.id}</td>
      <td>${r.task_name}</td>
      <td><span class="badge badge-category">${r.category}</span></td>
      <td>${badgeHtml(r.status)}</td>
      <td style="color:var(--c-text-muted);font-size:var(--text-xs)">${(r.started_at||"").slice(0,16)}</td>
      <td style="color:var(--c-text-muted);font-size:var(--text-xs)">${formatDuration(r.duration_s)}</td>
      <td>
        <button class="btn btn-sm btn-ghost" onclick="openLog(${r.id},'${esc(r.task_name)}')">Logs</button>
        <button class="btn btn-sm btn-secondary" onclick="rerunRun(${r.id})">Rerun</button>
      </td>
    </tr>
  `).join("");
}

// ── helpers ───────────────────────────────────────────────────────────────────

function badgeHtml(status) {
  return statusBadge(status).outerHTML;
}

function statusKey(statusText) {
  const m = { "Rodando":"running","Concluído":"success","Concluido":"success","Falhou":"error","Parado":"stopped" };
  return m[statusText] ?? "pending";
}

function formatDuration(value) {
  const seconds = Number(value);
  if (!Number.isFinite(seconds) || seconds < 0) return "—";
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const totalMinutes = Math.round(seconds / 60);
  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;
  if (!hours) return `${totalMinutes} min`;
  return minutes ? `${hours} h ${minutes} min` : `${hours} h`;
}

function esc(str) {
  return (str||"").replace(/'/g,"&#39;");
}

// ── ações globais (onclick inline) ───────────────────────────────────────────

window.openLog  = (id, name) => drawer.open(id, name);

window.stopRun  = async (id) => {
  try {
    await apiJson(`${API}/runs/${id}/stop`, { method: "POST" });
    toast("Processo encerrado", "info");
    await tick();
  } catch (error) {
    if (error.message !== "NAO_AUTENTICADO") {
      toast(error.message || "Falha ao encerrar", "error");
    }
  }
};

window.rerunRun = async (id) => {
  try {
    const data = await apiJson(`${API}/runs/${id}/rerun`, { method: "POST" });
    if (data.ok) {
      toast(`Rerun iniciado (#${data.run.run_id})`, "success");
      drawer.open(data.run.run_id, data.run.task_name);
      await tick();
    }
  } catch (error) {
    if (error.message !== "NAO_AUTENTICADO") {
      toast(error.message || "Falha ao rerodar", "error");
    }
  }
};
