# Instalação

Crie `.venv`, instale `requirements.txt`, execute `npm ci` e `npm run build`.
Publique `dist/` em `radar_v2/web/react/`. O banco vazio é criado
automaticamente em `logs/web_app/history.sqlite3` por `ensure_db()`; não copie
um banco de produção para desenvolvimento. Consulte `.env.example` antes de
executar downloaders.
