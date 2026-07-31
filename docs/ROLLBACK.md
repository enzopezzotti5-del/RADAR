# Rollback

1. Pare somente a arvore do Radar novo que estiver escutando a porta 5000.
2. Restaure ou execute a partir de `C:\Users\Revit\Desktop\Radar_backup_pre_agosto_2026_20260731_1535`.
3. Inicie `radar_v2\run_server.py` usando a `.venv` do backup, com `--host 0.0.0.0 --port 5000 --threads 8`.
4. Confirme o dono da porta 5000, `/health`, `/login` e o SQLite em `logs\web_app\history.sqlite3`.

Nao apague o backup durante a validacao inicial de agosto.
