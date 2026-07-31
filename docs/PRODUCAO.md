# Producao

Diretorio operacional: `C:\Users\Revit\Desktop\Radar`.

Inicie pelo atalho de inicializacao ou por:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\Users\Revit\Desktop\Radar\scripts\start_radar.ps1
```

O watchdog inicia `radar_v2\run_server.py` na porta 5000 com a venv local.
Verifique `http://127.0.0.1:5000/health`, `http://127.0.0.1:5000/login` e
`logs\radar_v2_stdout.log` antes de considerar a publicacao saudavel.

O frontend React compilado fica em `radar_v2\web\react`; as rotas `/api/*`
continuam sendo atendidas pelo Flask no mesmo host, sem proxy para ENERGIA.

Para homologacao, defina `RADAR_V2_SCHEDULER_ENABLED=false` somente no processo
de teste. Em producao a variavel deve permanecer `true`.
