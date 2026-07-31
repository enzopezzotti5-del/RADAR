/**
 * LogDrawer — drawer lateral com streaming incremental de logs.
 *
 * Formato do endpoint GET /api/runs/{id}/log:
 *   { log, start_line, next_line, total_lines, is_running, is_live, status_text }
 *
 * Polling incremental: passa after=next_line para buscar apenas linhas novas.
 */
export class LogDrawer {
  constructor() {
    this._overlay = document.getElementById("drawer-overlay");
    this._drawer  = document.getElementById("drawer");
    this._title   = document.getElementById("drawer-title");
    this._console = document.getElementById("drawer-console");
    this._badge   = document.getElementById("drawer-status-badge");
    this._btnRerun = document.getElementById("drawer-rerun");
    this._interval = null;
    this._runId    = null;
    this._nextLine = 0;
    this._done     = false;

    document.getElementById("drawer-close")?.addEventListener("click", () => this.close());
    this._overlay?.addEventListener("click", () => this.close());
    this._btnRerun?.addEventListener("click", () => this._rerun());
  }

  open(runId, taskName) {
    this._runId    = runId;
    this._nextLine = 0;
    this._done     = false;
    this._console.innerHTML = "";
    this._console.classList.remove("log-console--empty");
    if (this._title)  this._title.textContent  = taskName || `Run #${runId}`;
    if (this._badge)  this._badge.textContent  = "…";
    if (this._btnRerun) this._btnRerun.style.display = "none";

    this._overlay?.classList.add("open");
    this._drawer?.classList.add("open");

    clearInterval(this._interval);
    this._poll();
    this._interval = setInterval(() => this._poll(), 1000);
  }

  close() {
    clearInterval(this._interval);
    this._overlay?.classList.remove("open");
    this._drawer?.classList.remove("open");
    this._runId = null;
    this._done = true;
  }

  async _poll() {
    if (!this._runId || this._done) {
      clearInterval(this._interval);
      return;
    }
    try {
      const data = await window.RadarApp.apiJson(`/api/runs/${this._runId}/log?after=${this._nextLine}&max_lines=400`);

      if (data.log) this._append(data.log);
      this._nextLine = data.next_line ?? this._nextLine;

      if (this._badge) {
        this._badge.textContent  = data.status_text || "";
        this._badge.className    = this._statusClass(data);
      }

      if (!data.is_running) {
        this._done = true;
        clearInterval(this._interval);
        if (this._btnRerun) this._btnRerun.style.display = "";
      }
    } catch {/* rede */}
  }

  _append(text) {
    const isBottom = this._console.scrollHeight - this._console.scrollTop
                     <= this._console.clientHeight + 60;
    const frag = document.createDocumentFragment();
    for (const line of text.split("\n")) {
      if (line === "" && frag.childNodes.length === 0) continue;
      const el = document.createElement("div");
      el.className = "log-line" + (
        /\[ERR\]|error|erro|exception|traceback/i.test(line) ? " log-line--error" :
        /warn|aviso/i.test(line)                              ? " log-line--warn"  :
        /\[START\]|\[END\]|\[STOP\]/i.test(line)             ? " log-line--info"  : ""
      );
      el.textContent = line;
      frag.appendChild(el);
    }
    this._console.appendChild(frag);
    if (isBottom) this._console.scrollTop = this._console.scrollHeight;
  }

  _statusClass(data) {
    if (data.is_running)         return "badge bg-primary-lt text-primary";
    const s = (data.status_text||"").toLowerCase();
    if (s.includes("conclu"))    return "badge bg-success-lt text-success";
    if (s.includes("falhou") || s.includes("erro")) return "badge bg-danger-lt text-danger";
    if (s.includes("parado"))    return "badge bg-secondary-lt text-secondary";
    return "badge bg-surface-secondary text-secondary";
  }

  async _rerun() {
    if (!this._runId) return;
    try {
      const data = await window.RadarApp.apiJson(`/api/runs/${this._runId}/rerun`, { method: "POST" });
      if (data.ok) {
        this.open(data.run.run_id, data.run.task_name);
        // toast importado dinamicamente para evitar dependência circular
        const { toast } = await import("./toast.js");
        toast(`Rerun iniciado (#${data.run.run_id})`, "success");
      }
    } catch (error) {
      if (error.message !== "NAO_AUTENTICADO") {
        // ignore network noise
      }
    }
  }
}
