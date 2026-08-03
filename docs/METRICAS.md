# Métricas de faturas

O calendário usa `invoice_run_metrics` em `logs/web_app/history.sqlite3`.
Cada linha pertence a um `run_id`, uma tarefa e uma concessionária; o UPSERT
por essa chave torna reinícios e releituras idempotentes.

Os fluxos não acessam SQLite. Quando executados pelo Radar, recebem
`RADAR_METRICS_FILE`, `RADAR_RUN_ID` e `RADAR_TASK_ID`. Ao terminar, devem
chamar `core.metrics.emit_invoice_metrics(...)`. O executor valida o JSON,
persiste a métrica no fuso `America/Sao_Paulo` e remove o arquivo temporário.

`downloaded` conta somente faturas efetivamente obtidas; `processed` é a
fatura que concluiu o fluxo posterior aplicável; `errors` conta erros de itens
de fatura. Falha do processo não vira automaticamente erro de fatura.

Sem uma linha persistida, o calendário devolve `has_metrics=false`. Uma linha
válida com todos os contadores zero devolve `has_metrics=true`: zero não é
ausência. A instrumentação dos downloaders reais deve ser feita por fluxo,
após identificar seus eventos de conclusão de fatura.
