# Arquitetura

- `src/`, `public/` e `package.json`: frontend React/Vite.
- `radar_v2/`: Flask, API, scheduler e assets publicados.
- `core/` e `scripts/`: robos e regras de negocio preservados da producao.
- `logs/web_app/history.sqlite3`: historico e agendamentos locais do Radar.
- `.venv/` e `.tools/node/`: dependencias locais, nao versionadas.

O Radar nao acessa o Orbit, seus dados, seus logs ou suas configuracoes.
