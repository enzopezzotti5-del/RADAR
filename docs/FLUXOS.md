# Fluxos

`UI React → /api → RunService → core.pipelines/core.downloaders → run_log →
history.sqlite3 → /api → UI`.

O catálogo é `radar_v2/config/tasks.yaml`. Execuções manuais chamam
`RunService.launch`; agendadas são verificadas por `ScheduleService` e chamam
o mesmo executor. Cada lançamento registra um `run_id`, status e log em
`logs/web_app/run_logs/run_<id>.log`.
