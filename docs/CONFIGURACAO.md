# Configuração

Copie `.env.example` para `.env` e preencha apenas as integrações utilizadas. O
arquivo real permanece local e é carregado por `radar_v2.app.api.server` a
partir da raiz do projeto.

As variáveis `RADAR_V2_*` controlam sessão e scheduler; `RADAR_*` controlam o
watchdog. `VITE_*` é lido no *build* React, portanto qualquer alteração exige
`npm run build` antes de reiniciar o Radar.

`ENERGIA_SECRET_KEY_FILE` e `COPEL_ACCESSOS_XLS_PATH` são caminhos locais
opcionais usados por integrações legadas. Não devem apontar para arquivos que
serão versionados.

Use `RADAR_V2_SCHEDULER_ENABLED=false` somente para testes isolados. A produção
homologada usa um único scheduler iniciado pelo watchdog.
