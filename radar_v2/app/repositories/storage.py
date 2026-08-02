"""
Storage V2 — usa o MESMO SQLite do Radar v1.

Mesmas tabelas: `runs` e `schedules`.
Histórico e agendamentos existentes são preservados integralmente.
"""
from __future__ import annotations

import datetime as dt
import shutil
import sqlite3
from pathlib import Path

# __file__ = radar_v2/app/repositories/storage.py → .parent×4 = ENERGIA/
_ROOT        = Path(__file__).resolve().parent.parent.parent.parent
APP_DATA_DIR = _ROOT / "logs" / "web_app"
DB_PATH      = APP_DATA_DIR / "history.sqlite3"
LEGACY_DB    = _ROOT / "logs" / "desktop_app" / "history.sqlite3"

MAX_LOG_CHARS = 200_000
MAX_LOG_RUNS  = 300


# ── bootstrap ─────────────────────────────────────────────────────────────────

def ensure_db() -> None:
    APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not DB_PATH.exists() and LEGACY_DB.exists():
        try:
            shutil.copy2(LEGACY_DB, DB_PATH)
        except OSError:
            pass
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS runs (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at  TEXT NOT NULL,
                finished_at TEXT,
                task_id     TEXT NOT NULL,
                task_name   TEXT NOT NULL,
                category    TEXT NOT NULL,
                command     TEXT NOT NULL,
                status      TEXT NOT NULL,
                exit_code   INTEGER,
                duration_s  REAL,
                notes       TEXT DEFAULT '',
                log_text    TEXT DEFAULT ''
            )
        """)
        try:
            conn.execute("ALTER TABLE runs ADD COLUMN log_text TEXT DEFAULT ''")
        except sqlite3.OperationalError:
            pass

        conn.execute("""
            CREATE TABLE IF NOT EXISTS schedules (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                label        TEXT NOT NULL,
                task_id      TEXT NOT NULL,
                task_name    TEXT NOT NULL,
                category     TEXT NOT NULL,
                params_json  TEXT NOT NULL,
                frequency    TEXT NOT NULL,
                time_of_day  TEXT NOT NULL,
                day_of_week  INTEGER,
                day_of_month INTEGER,
                enabled      INTEGER NOT NULL DEFAULT 1,
                created_at   TEXT NOT NULL,
                last_run_at  TEXT,
                next_run_at  TEXT NOT NULL,
                run_count    INTEGER NOT NULL DEFAULT 0
            )
        """)
        conn.commit()


# ── runs ──────────────────────────────────────────────────────────────────────

def create_run(started_at: str, task_id: str, task_name: str, category: str, command: str) -> int:
    ensure_db()
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute(
            "INSERT INTO runs (started_at,task_id,task_name,category,command,status) VALUES (?,?,?,?,?,'running')",
            (started_at, task_id, task_name, category, command),
        )
        conn.commit()
        return int(cur.lastrowid)


def finish_run(run_id: int, finished_at: str, status: str, exit_code: int | None, duration_s: float) -> None:
    ensure_db()
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "UPDATE runs SET finished_at=?, status=?, exit_code=?, duration_s=? WHERE id=?",
            (finished_at, status, exit_code, duration_s, run_id),
        )
        conn.commit()


def save_run_log(run_id: int, log_text: str) -> None:
    ensure_db()
    text = log_text or ""
    if len(text) > MAX_LOG_CHARS:
        h = MAX_LOG_CHARS // 2
        text = text[:h] + "\n[... log truncado ...]\n" + text[-h:]
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("UPDATE runs SET log_text=? WHERE id=?", (text, run_id))
        conn.commit()


def get_run_log(run_id: int) -> str:
    ensure_db()
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute("SELECT log_text FROM runs WHERE id=?", (run_id,)).fetchone()
    return (row[0] or "") if row else ""


def list_runs(limit: int = 150) -> list[dict]:
    ensure_db()
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """SELECT id, started_at, finished_at, task_id, task_name, category,
                      status, exit_code, duration_s, command
               FROM runs ORDER BY id DESC LIMIT ?""",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def compact_run_history() -> None:
    ensure_db()
    with sqlite3.connect(DB_PATH) as conn:
        recent = [int(r[0]) for r in conn.execute(
            "SELECT id FROM runs ORDER BY id DESC LIMIT ?", (MAX_LOG_RUNS,)
        ).fetchall()]
        for run_id, log_text in conn.execute(
            "SELECT id, log_text FROM runs WHERE coalesce(length(log_text),0) > ?",
            (MAX_LOG_CHARS,)
        ).fetchall():
            h = MAX_LOG_CHARS // 2
            t = log_text or ""
            conn.execute("UPDATE runs SET log_text=? WHERE id=?",
                         (t[:h] + "\n[... truncado ...]\n" + t[-h:], int(run_id)))
        if recent:
            ph = ",".join("?" * len(recent))
            conn.execute(
                f"UPDATE runs SET log_text='' WHERE id NOT IN ({ph}) AND coalesce(length(log_text),0)>0",
                tuple(recent),
            )
        conn.commit()
        try:
            conn.execute("VACUUM")
        except Exception:
            pass


# ── schedules ─────────────────────────────────────────────────────────────────

def compute_next_run(frequency: str, time_of_day: str,
                     day_of_week: int | None = None,
                     day_of_month: int | None = None) -> str:
    now = dt.datetime.now()
    try:
        hh, mm = map(int, time_of_day.split(":"))
    except Exception:
        hh, mm = 8, 0

    if frequency == "daily":
        c = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if c <= now:
            c += dt.timedelta(days=1)
        return c.strftime("%Y-%m-%d %H:%M:%S")

    if frequency == "weekly":
        dow = int(day_of_week or 0) % 7
        ahead = (dow - now.weekday()) % 7
        c = (now + dt.timedelta(days=ahead)).replace(hour=hh, minute=mm, second=0, microsecond=0)
        if c <= now:
            c += dt.timedelta(weeks=1)
        return c.strftime("%Y-%m-%d %H:%M:%S")

    if frequency == "monthly":
        dom = min(int(day_of_month or 1), 28)
        try:
            c = now.replace(day=dom, hour=hh, minute=mm, second=0, microsecond=0)
        except ValueError:
            c = now.replace(day=1, hour=hh, minute=mm, second=0, microsecond=0)
        if c <= now:
            c = c.replace(month=now.month % 12 + 1, year=now.year + (now.month // 12))
        return c.strftime("%Y-%m-%d %H:%M:%S")

    raise ValueError(f"Frequência desconhecida: {frequency}")


def create_schedule(*, label: str, task_id: str, task_name: str, category: str,
                    params_json: str, frequency: str, time_of_day: str,
                    day_of_week: int | None, day_of_month: int | None) -> int:
    ensure_db()
    created_at  = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    next_run_at = compute_next_run(frequency, time_of_day, day_of_week, day_of_month)
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute(
            """INSERT INTO schedules
               (label,task_id,task_name,category,params_json,frequency,
                time_of_day,day_of_week,day_of_month,enabled,created_at,next_run_at)
               VALUES (?,?,?,?,?,?,?,?,?,1,?,?)""",
            (label, task_id, task_name, category, params_json, frequency,
             time_of_day, day_of_week, day_of_month, created_at, next_run_at),
        )
        conn.commit()
        return int(cur.lastrowid)


def list_schedules() -> list[dict]:
    ensure_db()
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM schedules ORDER BY next_run_at ASC").fetchall()
    return [dict(r) for r in rows]


def count_enabled_schedules() -> int:
    ensure_db()
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute("SELECT COUNT(*) FROM schedules WHERE enabled=1").fetchone()
    return int(row[0]) if row else 0


def toggle_schedule(schedule_id: int, enabled: bool) -> None:
    ensure_db()
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("UPDATE schedules SET enabled=? WHERE id=?", (int(enabled), schedule_id))
        conn.commit()


def delete_schedule(schedule_id: int) -> None:
    ensure_db()
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("DELETE FROM schedules WHERE id=?", (schedule_id,))
        conn.commit()


def update_schedule_after_run(schedule_id: int, last_run_at: str) -> None:
    ensure_db()
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT frequency, time_of_day, day_of_week, day_of_month FROM schedules WHERE id=?",
            (schedule_id,)
        ).fetchone()
        if row is None:
            return
        next_run_at = compute_next_run(row[0], row[1], row[2], row[3])
        conn.execute(
            "UPDATE schedules SET last_run_at=?, next_run_at=?, run_count=run_count+1 WHERE id=?",
            (last_run_at, next_run_at, schedule_id),
        )
        conn.commit()


def defer_schedule_after_preflight(schedule_id: int) -> None:
    """Move um schedule bloqueado para a proxima janela sem contar execucao."""
    ensure_db()
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT frequency, time_of_day, day_of_week, day_of_month FROM schedules WHERE id=?",
            (schedule_id,),
        ).fetchone()
        if row is None:
            return
        next_run_at = compute_next_run(row[0], row[1], row[2], row[3])
        conn.execute("UPDATE schedules SET next_run_at=? WHERE id=?", (next_run_at, schedule_id))
        conn.commit()


def upsert_schedule(*, task_id: str, label: str, task_name: str, category: str,
                    params_json: str, frequency: str, time_of_day: str,
                    day_of_week: int | None, day_of_month: int | None) -> int:
    """Atualiza se já existir agendamento para o task_id, senão cria."""
    ensure_db()
    next_run_at = compute_next_run(frequency, time_of_day, day_of_week, day_of_month)
    now_str = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        existing = conn.execute(
            "SELECT id FROM schedules WHERE task_id=? ORDER BY id ASC", (task_id,)
        ).fetchall()
        ids = [int(r["id"]) for r in existing]

        if ids:
            conn.execute(
                """UPDATE schedules
                   SET label=?,task_name=?,category=?,params_json=?,frequency=?,
                       time_of_day=?,day_of_week=?,day_of_month=?,enabled=1,
                       last_run_at=NULL,run_count=0,next_run_at=?
                   WHERE id=?""",
                (label, task_name, category, params_json, frequency,
                 time_of_day, day_of_week, day_of_month, next_run_at, ids[0]),
            )
            for extra_id in ids[1:]:
                conn.execute("DELETE FROM schedules WHERE id=?", (extra_id,))
            conn.commit()
            return ids[0]

        cur = conn.execute(
            """INSERT INTO schedules
               (label,task_id,task_name,category,params_json,frequency,
                time_of_day,day_of_week,day_of_month,enabled,created_at,next_run_at)
               VALUES (?,?,?,?,?,?,?,?,?,1,?,?)""",
            (label, task_id, task_name, category, params_json, frequency,
             time_of_day, day_of_week, day_of_month, now_str, next_run_at),
        )
        conn.commit()
        return int(cur.lastrowid)
