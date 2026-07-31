/** Retorna elemento badge por status */
export function statusBadge(status) {
  const labels = {
    running: "Rodando",
    success: "Concluído",
    error:   "Falhou",
    stopped: "Parado",
    pending: "Pendente",
  };
  const el = document.createElement("span");
  const classes = {
    running: "badge bg-primary-lt text-primary",
    success: "badge bg-success-lt text-success",
    error:   "badge bg-danger-lt text-danger",
    stopped: "badge bg-secondary-lt text-secondary",
    pending: "badge bg-warning-lt text-warning",
  };
  el.className = classes[status] || "badge bg-secondary-lt text-secondary";
  el.textContent = labels[status] ?? status;
  return el;
}
