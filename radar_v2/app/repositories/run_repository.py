"""
Repositório de runs — SQLite (mesma engine atual, mesma BD).

A interface é intencionalmente simples:
  - insert_run / update_run_status / get_run / list_runs

Migrar para outro banco = trocar só este arquivo.
"""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
from pathlib import Path
from typing import Any

DB_PATH = Path(__file__).resolve().parent.parent.parent.parent / "logs" / "web_app" / "history.sqlite3"


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_schema() -> None:
    with _conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS runs_v2 (
                run_id      INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id     TEXT NOT NULL,
                task_name   TEXT NOT NULL,
                category    TEXT NOT NULL,
                command     TEXT NOT NULL,
                params_json TEXT NOT NULL,
                origin      TEXT NOT NULL DEFAULT 'manual',
                status      TEXT NOT NULL DEFAULT 'pending',
                started_at  TEXT NOT NULL,
                ended_at    TEXT,
                exit_code   INTEGER,
                pid         INTEGER,
                log_path    TEXT
            )
        """)


def insert_run(
    task_id: str,
    task_name: str,
    category: str,
    command: list[str],
    params: dict,
    origin: str,
) -> int:
    with _conn() as conn:
        cur = conn.execute(
            """INSERT INTO runs_v2
               (task_id, task_name, category, command, params_json, origin, status, started_at)
               VALUES (?,?,?,?,?,?,'pending',?)""",
            (task_id, task_name, category, json.dumps(command),
             json.dumps(params), origin, dt.datetime.now().isoformat(sep=" ", timespec="seconds")),
        )
        return cur.lastrowid


def update_run(run_id: int, **fields: Any) -> None:
    allowed = {"status", "ended_at", "exit_code", "pid", "log_path"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return
    set_clause = ", ".join(f"{k}=?" for k in updates)
    with _conn() as conn:
        conn.execute(
            f"UPDATE runs_v2 SET {set_clause} WHERE run_id=?",
            (*updates.values(), run_id),
        )


def get_run(run_id: int) -> dict | None:
    with _conn() as conn:
        row = conn.execute("SELECT * FROM runs_v2 WHERE run_id=?", (run_id,)).fetchone()
        return dict(row) if row else None


def list_runs(limit: int = 150, status: str | None = None) -> list[dict]:
    query = "SELECT * FROM runs_v2"
    params: list[Any] = []
    if status:
        query += " WHERE status=?"
        params.append(status)
    query += " ORDER BY run_id DESC LIMIT ?"
    params.append(limit)
    with _conn() as conn:
        return [dict(r) for r in conn.execute(query, params).fetchall()]
