# Integração Radar → Orbit

O contrato completo está versionado no repositório Orbit:
`docs/CONTRATO_RADAR_ORBIT.md`

## Resumo (lado Radar)

O publisher (`radar_v2/app/services/orbit_handoff.py`) é `fail-open`:
qualquer falha é isolada e não afeta o resultado do download.

**Ativação:** variável de ambiente `RADAR_ORBIT_HANDOFF_ENABLED=true`  
**Padrão:** desabilitado (`false`)

**Allowlist de tarefas:**

| task_id | Concessionária |
|---|---|
| dl_enel_sp | ENEL |
| dl_copel_bt | COPEL |
| dl_neo_coelba | NEOENERGIA/COELBA |
| dl_neo_celpe | NEOENERGIA/CELPE |
| dl_neo_cosern | NEOENERGIA/COSERN |
| dl_neo_elektro | NEOENERGIA/ELEKTRO |
| dl_celesc_mt | CELESC |
| dl_celesc_bt | CELESC |
| dl_light_rj | LIGHT |
| dl_cpfl_bt | CPFL |

**Idempotência:** mesmo PDF entregue duas vezes retorna `ALREADY_DELIVERED`.  
**Outbox:** `handoff/orbit/outbox/` — estado operacional, não versionado.  
**Archive:** `handoff/orbit/archive/` — entregas confirmadas, não versionado.  
**Banco:** `handoff/orbit/handoff.sqlite3` — não versionado.

O PDF original em `source_path` nunca é movido ou deletado.
