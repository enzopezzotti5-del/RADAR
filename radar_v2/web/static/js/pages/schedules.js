/** Página Agendamentos */
import { toast } from "../components/toast.js";
import { apiJson } from "../app.js";

const API = "/api";
let schedules = [];
let allTasks  = {};

document.addEventListener("DOMContentLoaded", async () => {
  await Promise.all([loadTasks(), loadSchedules()]);
  renderSchedules();
  setupForm();
  setupPresets();
});

async function loadSchedules() {
  try {
    const data = await apiJson(`${API}/schedules`);
    schedules = data.schedules || [];
  } catch (error) {
    schedules = [];
    if (error.message !== "NAO_AUTENTICADO") {
      toast(error.message || "Erro ao carregar agendamentos", "error");
    }
  }
}

async function loadTasks() {
  try {
    const data = await apiJson(`${API}/tasks`);
    allTasks = data.tasks || {};
  } catch (error) {
    allTasks = {};
    if (error.message !== "NAO_AUTENTICADO") {
      toast(error.message || "Erro ao carregar tarefas", "error");
    }
  }
}

function renderSchedules() {
  const tbody = document.getElementById("schedules-body");
  if (!tbody) return;

  if (!schedules.length) {
    tbody.innerHTML = `<tr><td colspan="7" style="text-align:center;color:var(--c-text-dim);padding:var(--space-8)">Nenhum agendamento configurado</td></tr>`;
    return;
  }

  tbody.innerHTML = schedules.map(s => `
    <tr>
      <td>${s.label}</td>
      <td><span class="badge badge-category">${s.category}</span></td>
      <td>${s.freq_label ?? s.frequency}</td>
      <td>${s.time_of_day}</td>
      <td style="color:var(--c-text-muted);font-size:var(--text-xs)">${s.next_run_label ?? s.next_run_at ?? "-"}</td>
      <td style="color:var(--c-text-muted);font-size:var(--text-xs)">${s.last_run_at?.slice(0,16) ?? "—"}</td>
      <td>
        <label style="display:inline-flex;align-items:center;gap:var(--space-2);font-size:var(--text-xs);cursor:pointer;margin-right:var(--space-3)">
          <input type="checkbox" ${s.enabled ? "checked" : ""} onchange="toggleSched(${s.id}, this.checked)">
          ${s.enabled ? "Ativo" : "Inativo"}
        </label>
        <button class="btn btn-sm btn-danger" onclick="deleteSched(${s.id})">✕</button>
      </td>
    </tr>
  `).join("");

  const total = schedules.filter(s=>s.enabled).length;
  const badge = document.getElementById("sched-active-count");
  if (badge) { badge.textContent = total + " ativo" + (total!==1?"s":""); }
}

function setupForm() {
  const select = document.getElementById("sched-task");
  if (!select) return;

  for (const [category, tasks] of Object.entries(allTasks)) {
    const group = document.createElement("optgroup");
    group.label = category;
    for (const t of tasks) {
      const opt = document.createElement("option");
      opt.value = t.task_id;
      opt.textContent = t.name;
      group.appendChild(opt);
    }
    select.appendChild(group);
  }

  document.getElementById("sched-form")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const g = id => document.getElementById(id)?.value ?? "";

    const task_id = g("sched-task");
    if (!task_id) { toast("Selecione uma tarefa", "error"); return; }

    const payload = {
      task_id,
      label:        g("sched-label"),
      frequency:    g("sched-freq"),
      time_of_day:  g("sched-time"),
      day_of_week:  g("sched-dow")  !== "" ? parseInt(g("sched-dow"))  : null,
      day_of_month: g("sched-dom")  !== "" ? parseInt(g("sched-dom"))  : null,
      use_current_date: document.getElementById("sched-current-date")?.checked ?? false,
      month: g("sched-month"),
      year:  g("sched-year"),
    };

    const data = await apiJson(`${API}/schedules`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (data.ok) {
      toast("Agendamento criado", "success");
      e.target.reset();
      await loadSchedules();
      renderSchedules();
    } else {
      toast(data.error || "Erro", "error");
    }
  });
}

function setupPresets() {
  document.getElementById("btn-preset-neo")?.addEventListener("click", async () => {
    if (!confirm("Aplicar preset Neoenergia 21h? Isso vai reconfigurar/criar os agendamentos Neoenergia.")) return;
    const data = await apiJson(`${API}/schedules/presets/neoenergia-21h`, { method: "POST" });
    if (data.ok) {
      toast(`Preset aplicado: ${data.result.applied.length} agendamentos`, "success");
      schedules = data.schedules || [];
      renderSchedules();
    } else {
      toast(data.error, "error");
    }
  });

  document.getElementById("btn-preset-celesc")?.addEventListener("click", async () => {
    if (!confirm("Aplicar preset CELESC Pipelines?")) return;
    const data = await apiJson(`${API}/schedules/presets/celesc-pipelines`, { method: "POST" });
    if (data.ok) {
      toast(`Preset aplicado: ${data.result.applied.length} agendamentos`, "success");
      schedules = data.schedules || [];
      renderSchedules();
    } else {
      toast(data.error, "error");
    }
  });

  document.getElementById("btn-preset-light")?.addEventListener("click", async () => {
    if (!confirm("Aplicar preset LIGHT Madrugada? Isso vai criar ou atualizar o agendamento oficial do downloader LIGHT.")) return;
    const data = await apiJson(`${API}/schedules/presets/light-madrugada`, { method: "POST" });
    if (data.ok) {
      toast(`Preset aplicado: ${data.result.applied.length} agendamento`, "success");
      schedules = data.schedules || [];
      renderSchedules();
    } else {
      toast(data.error, "error");
    }
  });
}

window.toggleSched = async (id, enabled) => {
  try {
    await apiJson(`${API}/schedules/${id}/toggle`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled }),
    });
    toast(enabled ? "Agendamento ativado" : "Agendamento desativado", "info");
    await loadSchedules();
    renderSchedules();
  } catch (error) {
    if (error.message !== "NAO_AUTENTICADO") {
      toast(error.message || "Falha ao alterar agendamento", "error");
    }
  }
};

window.deleteSched = async (id) => {
  if (!confirm("Remover agendamento?")) return;
  try {
    await apiJson(`${API}/schedules/${id}`, { method: "DELETE" });
    toast("Agendamento removido", "info");
    await loadSchedules();
    renderSchedules();
  } catch (error) {
    if (error.message !== "NAO_AUTENTICADO") {
      toast(error.message || "Falha ao remover agendamento", "error");
    }
  }
};
