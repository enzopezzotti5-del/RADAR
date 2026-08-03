# Competencia agosto de 2026

O frontend inicia novas execucoes com `VITE_RADAR_DEFAULT_COMPETENCY=2026-08`.
O formulario envia mes `08` e ano `2026` ao backend; o calendario e os filtros
usam chaves completas `YYYY-MM`.

Julho (`2026-07`) e agosto (`2026-08`) sao consultas independentes. Quando nao
ha evidencia de faturas para uma data, a API responde `has_metrics=false` e
`metrics_complete=false`; ausencia nao e apresentada como processamento zero.
