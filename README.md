# Radar

Radar é a aplicação Flask/React que agenda, executa e acompanha downloaders e
pipelines de faturas. A produção integrada usa uma única aplicação em `:5000`.

## Estrutura

- `src/`: fonte React/Vite; `npm run build` gera `dist/`.
- `radar_v2/`: Flask, API, scheduler, executor e watchdog.
- `core/`: downloaders, parsers, pipelines e utilitários compartilhados.
- `radar_v2/web/react/`: artefato React publicado pelo Flask.
- `logs/web_app/`: banco e logs locais, criados em runtime e não versionados.

## Requisitos

Python 3.13.13 e Node.js 22.22.0 LTS foram homologados. Node não é versionado.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
npm ci
npm run lint
npm run build
```

O último passo de publicação do artefato deve ser feito somente em uma janela
de manutenção, após backup e com o Radar parado: `robocopy /MIR` substitui o
conteúdo publicado em `radar_v2/web/react/`.

Copie `.env.example` para `.env` e preencha somente as integrações necessárias.
Nunca versione esse arquivo.

## Produção

`scripts/start_radar.ps1` determina a raiz via `$PSScriptRoot` e inicia o
watchdog. O watchdog inicia `radar_v2/run_server.py`, verifica `/health` e usa
porta 5000 por padrão. Consulte `docs/PRODUCAO.md` e `docs/INSTALACAO.md`.

## Testes

```powershell
.\.venv\Scripts\python.exe -m compileall -q radar_v2 core
.\.venv\Scripts\python.exe -m pytest tests -q
npm run check:api-contract
npm test
```

`npm test` ainda não possui testes de interface; os testes de regressão do
backend devem usar banco temporário, nunca o SQLite operacional.
