"""Repositório de schedules — SQLite, tabela schedules_v2."""
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
            CREATE TABLE IF NOT EXISTS schedules_v2 (
                schedule_id  INTEGER PRIMARY KEY AUTOINCREMENT,
                label        TEXT NOT NULL,
                task_id      TEXT NOT NULL,
                task_name    TEXT NOT NULL,
                category     TEXT NOT NULL,
                params_json  TEXT NOT NULL,
                frequency    TEXT NOT NULL DEFAULT 'daily',
                time_of_day  TEXT NOT NULL DEFAULT '08:00',
                day_of_week  INTEGER,
                day_of_month INTEGER,
                enabled      INTEGER NOT NULL DEFAULT 1,
                next_run_at  TEXT,
                last_run_at  TEXT,
                last_status  TEXT
            )
        """)


def create_schedule(**fields: Any) -> int:
    columns = ["label","task_id","task_name","category","params_json",
                "frequency","time_of_day","day_of_week","day_of_month","enabled","next_run_at"]
    values  = [fields.get(c) for c in columns]
    placeholders = ",".join("?" * len(columns))
    with _conn() as conn:
        cur = conn.execute(
            f"INSERT INTO schedules_v2 ({','.join(columns)}) VALUES ({placeholders})",
            values,
        )
        return cur.lastrowid


def list_schedules() -> list[dict]:
    with _conn() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM schedules_v2 ORDER BY schedule_id").fetchall()]


def toggle_schedule(schedule_id: int, enabled: bool) -> None:
    with _conn() as conn:
        conn.execute("UPDATE schedules_v2 SET enabled=? WHERE schedule_id=?", (int(enabled), schedule_id))


def delete_schedule(schedule_id: int) -> None:
    with _conn() as conn:
        conn.execute("DELETE FROM schedules_v2 WHERE schedule_id=?", (schedule_id,))


def update_after_run(schedule_id: int, next_run_at: str, status: str) -> None:
    with _conn() as conn:
        conn.execute(
            "UPDATE schedules_v2 SET last_run_at=?, last_status=?, next_run_at=? WHERE schedule_id=?",
            (dt.datetime.now().isoformat(sep=" ", timespec="seconds"), status, next_run_at, schedule_id),
        )
