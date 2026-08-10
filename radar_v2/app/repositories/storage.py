"""
Storage V2 — usa o MESMO SQLite do Radar v1.

Mesmas tabelas: `runs` e `schedules`.
Histórico e agendamentos existentes são preservados integralmente.
"""
from __future__ import annotations

import datetime as dt
import shutil
import sqlite3
from contextlib import contextmanager
from pathlib import Path

# __file__ = radar_v2/app/repositories/storage.py → .parent×4 = ENERGIA/
_ROOT        = Path(__file__).resolve().parent.parent.parent.parent
APP_DATA_DIR = _ROOT / "logs" / "web_app"
DB_PATH      = APP_DATA_DIR / "history.sqlite3"
LEGACY_DB    = _ROOT / "logs" / "desktop_app" / "history.sqlite3"

MAX_LOG_CHARS = 200_000
MAX_LOG_RUNS  = 300


# ── metric helpers ────────────────────────────────────────────────────────────

@contextmanager
def _connection():
    """Short-lived SQLite connection with FK enforcement and explicit commit."""
    conn = sqlite3.connect(DB_PATH, timeout=5)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def _has_runs_cascade(conn: sqlite3.Connection, table: str) -> bool:
    return any(
        row[2] == "runs" and str(row[6]).upper() == "CASCADE"
        for row in conn.execute(f"PRAGMA foreign_key_list({table})")
    )


def _create_metric_tables(conn: sqlite3.Connection, suffix: str = "") -> None:
    conn.execute(f"""
        CREATE TABLE run_metric_items{suffix} (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id        INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
            item_key      TEXT NOT NULL,
            utility       TEXT NOT NULL,
            task_id       TEXT NOT NULL,
            competence    TEXT NOT NULL,
            outcome       TEXT NOT NULL CHECK(outcome IN ('downloaded','skipped_existing','item_error','other')),
            created_at    TEXT NOT NULL,
            updated_at    TEXT NOT NULL,
            UNIQUE(run_id, item_key)
        )
    """)
    conn.execute(f"""
        CREATE TABLE run_metrics{suffix} (
            run_id                 INTEGER PRIMARY KEY REFERENCES runs(id) ON DELETE CASCADE,
            utility                TEXT NOT NULL,
            task_id                TEXT NOT NULL,
            downloaded_count       INTEGER NOT NULL DEFAULT 0,
            skipped_existing_count INTEGER NOT NULL DEFAULT 0,
            item_error_count       INTEGER NOT NULL DEFAULT 0,
            other_count            INTEGER NOT NULL DEFAULT 0,
            processed_count        INTEGER NOT NULL DEFAULT 0,
            metrics_complete       INTEGER NOT NULL DEFAULT 0,
            metrics_version        INTEGER NOT NULL DEFAULT 1,
            updated_at             TEXT NOT NULL
        )
    """)


def _ensure_metric_schema(conn: sqlite3.Connection) -> None:
    items_exists = _table_exists(conn, "run_metric_items")
    metrics_exists = _table_exists(conn, "run_metrics")
    current = (
        items_exists and metrics_exists
        and _has_runs_cascade(conn, "run_metric_items")
        and _has_runs_cascade(conn, "run_metrics")
    )
    if not current:
        _create_metric_tables(conn, "_next")
        if metrics_exists:
            conn.execute("INSERT INTO run_metrics_next SELECT * FROM run_metrics")
        if items_exists:
            conn.execute("INSERT INTO run_metric_items_next SELECT * FROM run_metric_items")
        if items_exists:
            conn.execute("DROP TABLE run_metric_items")
        if metrics_exists:
            conn.execute("DROP TABLE run_metrics")
        conn.execute("ALTER TABLE run_metrics_next RENAME TO run_metrics")
        conn.execute("ALTER TABLE run_metric_items_next RENAME TO run_metric_items")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_run_metric_items_run_id ON run_metric_items(run_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_run_metrics_utility_task ON run_metrics(utility, task_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_runs_started_at ON runs(started_at)")


def _ensure_download_receipt_schema(conn: sqlite3.Connection) -> None:
    """Canonical evidence for one physically confirmed PDF download.

    This deliberately does not backfill historical files: a receipt exists only
    when the downloader confirms a concrete file at download time.
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS download_artifact_receipts (
            receipt_id TEXT PRIMARY KEY,
            receipt_version INTEGER NOT NULL,
            run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
            task_id TEXT NOT NULL,
            utility TEXT NOT NULL,
            original_path TEXT NOT NULL,
            filename TEXT NOT NULL,
            sha256 TEXT NOT NULL,
            size_bytes INTEGER NOT NULL,
            downloaded_at TEXT NOT NULL,
            handoff_required INTEGER NOT NULL,
            handoff_id TEXT,
            handoff_status TEXT NOT NULL,
            last_error TEXT,
            retry_count INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(task_id, sha256)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_download_receipts_run ON download_artifact_receipts(run_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_download_receipts_handoff ON download_artifact_receipts(handoff_status)")


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
        conn.execute("""
            CREATE TABLE IF NOT EXISTS invoice_run_metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL,
                task_id TEXT NOT NULL,
                utility TEXT NOT NULL,
                metric_date TEXT NOT NULL,
                downloaded INTEGER NOT NULL DEFAULT 0 CHECK(downloaded >= 0),
                processed INTEGER NOT NULL DEFAULT 0 CHECK(processed >= 0),
                errors INTEGER NOT NULL DEFAULT 0 CHECK(errors >= 0),
                run_failures INTEGER NOT NULL DEFAULT 0 CHECK(run_failures >= 0),
                metrics_complete INTEGER NOT NULL DEFAULT 0,
                source TEXT NOT NULL DEFAULT 'flow',
                started_at TEXT NOT NULL,
                finished_at TEXT,
                updated_at TEXT NOT NULL,
                details_json TEXT NOT NULL DEFAULT '{}',
                UNIQUE(run_id, task_id, utility)
            )
        """)
        _ensure_metric_schema(conn)
        _ensure_download_receipt_schema(conn)
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


def upsert_invoice_metric(*, run_id: int, task_id: str, utility: str,
                          metric_date: str, downloaded: int, processed: int,
                          errors: int, metrics_complete: bool, source: str,
                          started_at: str, finished_at: str | None,
                          details_json: str, updated_at: str) -> None:
    """Persiste uma métrica final por run/concessionária sem somas duplicadas."""
    ensure_db()
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            INSERT INTO invoice_run_metrics
            (run_id,task_id,utility,metric_date,downloaded,processed,errors,
             metrics_complete,source,started_at,finished_at,updated_at,details_json)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(run_id,task_id,utility) DO UPDATE SET
              metric_date=excluded.metric_date, downloaded=excluded.downloaded,
              processed=excluded.processed, errors=excluded.errors,
              metrics_complete=excluded.metrics_complete, source=excluded.source,
              started_at=excluded.started_at, finished_at=excluded.finished_at,
              updated_at=excluded.updated_at, details_json=excluded.details_json
        """, (run_id, task_id, utility, metric_date, downloaded, processed, errors,
              int(metrics_complete), source, started_at, finished_at, updated_at,
              details_json))
        conn.commit()


def calendar_invoice_metrics(start: str, end: str) -> list[dict]:
    ensure_db()
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT metric_date, utility, downloaded, processed, errors,
                   metrics_complete, run_id, updated_at
            FROM invoice_run_metrics
            WHERE metric_date BETWEEN ? AND ?
            ORDER BY metric_date, utility, run_id
        """, (start, end)).fetchall()
    return [dict(row) for row in rows]


# ── per-item invoice metrics (new stdout-based mechanism) ────────────────────

def _metrics_now() -> str:
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def initialize_run_metrics(run_id: int, *, utility: str, task_id: str) -> None:
    """Creates the partial metric envelope for a newly instrumented run."""
    ensure_db()
    with _connection() as conn:
        conn.execute(
            """INSERT OR IGNORE INTO run_metrics
               (run_id, utility, task_id, metrics_complete, metrics_version, updated_at)
               VALUES (?, ?, ?, 0, 1, ?)""",
            (run_id, utility, task_id, _metrics_now()),
        )


def upsert_run_metric_item(
    run_id: int,
    *,
    item_key: str,
    utility: str,
    task_id: str,
    competence: str,
    outcome: str,
) -> None:
    """Upserts one item result and re-derives run counters within the same transaction."""
    if outcome not in {"downloaded", "skipped_existing", "item_error", "other"}:
        raise ValueError(f"Resultado de metrica invalido: {outcome}")
    ensure_db()
    now = _metrics_now()
    with _connection() as conn:
        conn.execute(
            """INSERT OR IGNORE INTO run_metrics
               (run_id, utility, task_id, metrics_complete, metrics_version, updated_at)
               VALUES (?, ?, ?, 0, 1, ?)""",
            (run_id, utility, task_id, now),
        )
        conn.execute(
            """INSERT INTO run_metric_items
               (run_id, item_key, utility, task_id, competence, outcome, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(run_id, item_key) DO UPDATE SET
                 utility=excluded.utility, task_id=excluded.task_id,
                 competence=excluded.competence, outcome=excluded.outcome,
                 updated_at=excluded.updated_at""",
            (run_id, item_key, utility, task_id, competence, outcome, now, now),
        )
        counts = {row[0]: int(row[1]) for row in conn.execute(
            "SELECT outcome, COUNT(*) FROM run_metric_items WHERE run_id=? GROUP BY outcome",
            (run_id,),
        ).fetchall()}
        downloaded = counts.get("downloaded", 0)
        skipped    = counts.get("skipped_existing", 0)
        errors     = counts.get("item_error", 0)
        other      = counts.get("other", 0)
        conn.execute(
            """UPDATE run_metrics
               SET downloaded_count=?, skipped_existing_count=?, item_error_count=?,
                   other_count=?, processed_count=?, updated_at=?
               WHERE run_id=?""",
            (downloaded, skipped, errors, other,
             downloaded + skipped + errors + other, now, run_id),
        )


def set_run_metrics_complete(run_id: int, *, complete: bool) -> None:
    ensure_db()
    with _connection() as conn:
        conn.execute(
            "UPDATE run_metrics SET metrics_complete=?, updated_at=? WHERE run_id=?",
            (1 if complete else 0, _metrics_now(), run_id),
        )


def get_run_metric_counts(run_id: int) -> dict | None:
    """Returns the invoice counters for one run, or None if no metrics exist."""
    ensure_db()
    with _connection() as conn:
        row = conn.execute(
            """SELECT downloaded_count, skipped_existing_count, item_error_count,
                      other_count, utility, task_id
               FROM run_metrics WHERE run_id=?""",
            (run_id,),
        ).fetchone()
    if row is None:
        return None
    return {
        "downloaded":       int(row[0] or 0),
        "skipped_existing": int(row[1] or 0),
        "item_error":       int(row[2] or 0),
        "other":            int(row[3] or 0),
        "utility":          str(row[4] or ""),
        "task_id":          str(row[5] or ""),
    }


def calendar_metric_summary(
    start: str,
    end: str,
    *,
    utility: str | None = None,
    task_id: str | None = None,
) -> dict:
    """Aggregates metrics for a date range from both storage tables.

    Reads invoice_run_metrics (synced/legacy) and run_metrics (new per-item
    tracking for runs not yet synced). Returns sparse days — only dates that
    have data appear in the list.
    """
    ensure_db()
    util_clause_1 = " AND irm.utility = ?" if utility else ""
    util_clause_2 = " AND rm.utility = ?" if utility else ""
    task_clause_1 = " AND irm.task_id = ?" if task_id else ""
    task_clause_2 = " AND rm.task_id = ?" if task_id else ""
    p1: list = [start, end] + ([utility] if utility else []) + ([task_id] if task_id else [])
    p2: list = [start, end] + ([utility] if utility else []) + ([task_id] if task_id else [])
    query = f"""
        SELECT date, utility, run_id, downloaded, processed, errors,
               skipped_existing, other, metrics_complete, updated_at
        FROM (
            SELECT irm.metric_date as date, irm.utility, irm.run_id,
                   irm.downloaded, irm.processed, irm.errors,
                   coalesce(rm.skipped_existing_count, 0) as skipped_existing,
                   coalesce(rm.other_count, 0) as other,
                   irm.metrics_complete, irm.updated_at
            FROM invoice_run_metrics irm
            LEFT JOIN run_metrics rm ON rm.run_id = irm.run_id
            WHERE irm.metric_date BETWEEN ? AND ?
            {util_clause_1}
            {task_clause_1}

            UNION ALL

            SELECT date(r.started_at) as date, rm.utility, rm.run_id,
                   rm.downloaded_count,
                   rm.downloaded_count + rm.skipped_existing_count,
                   rm.item_error_count,
                   rm.skipped_existing_count,
                   rm.other_count,
                   rm.metrics_complete,
                   coalesce(r.finished_at, r.started_at)
            FROM run_metrics rm
            JOIN runs r ON rm.run_id = r.id
            WHERE date(r.started_at) BETWEEN ? AND ?
            {util_clause_2}
            {task_clause_2}
            AND rm.run_id NOT IN (SELECT run_id FROM invoice_run_metrics)
        )
        ORDER BY date, utility, run_id
    """
    with _connection() as conn:
        rows = conn.execute(query, p1 + p2).fetchall()
    if not rows:
        return {
            "totals": {"downloaded": 0, "skipped_existing": 0, "errors": 0, "other": 0, "processed": 0},
            "days": [], "utilities": [], "has_metrics": False, "metrics_complete": False,
        }
    by_date_util: dict[tuple, list] = {}
    for r in rows:
        by_date_util.setdefault((r[0], r[1]), []).append(r)
    by_date: dict[str, dict[str, list]] = {}
    for (d, u), u_rows in by_date_util.items():
        by_date.setdefault(d, {})[u] = u_rows
    days = []
    utilities_list = []
    totals: dict[str, int] = {"downloaded": 0, "skipped_existing": 0, "errors": 0, "other": 0, "processed": 0}
    for date_str in sorted(by_date):
        date_utils = by_date[date_str]
        d_dl = d_sk = d_err = d_oth = d_proc = d_runs = 0
        d_complete = True
        day_utils: list[str] = []
        day_by_utility: list[dict] = []
        for util_name in sorted(date_utils):
            u_rows = date_utils[util_name]
            u_dl   = sum(int(r[3] or 0) for r in u_rows)
            u_proc = sum(int(r[4] or 0) for r in u_rows)
            u_err  = sum(int(r[5] or 0) for r in u_rows)
            u_sk   = sum(int(r[6] or 0) for r in u_rows)
            u_oth  = sum(int(r[7] or 0) for r in u_rows)
            u_comp = all(bool(r[8]) for r in u_rows)
            run_ids = [int(r[2]) for r in u_rows]
            d_dl += u_dl; d_sk += u_sk; d_err += u_err
            d_oth += u_oth; d_proc += u_proc; d_runs += len(run_ids)
            d_complete = d_complete and u_comp
            day_utils.append(util_name)
            entry = {
                "utility": util_name, "downloaded": u_dl, "processed": u_proc,
                "errors": u_err, "skipped_existing": u_sk, "other": u_oth,
                "metrics_complete": u_comp, "run_ids": run_ids,
                "run_count": len(run_ids),
                "last_update": max((r[9] for r in u_rows if r[9]), default=None),
            }
            day_by_utility.append(entry)
            utilities_list.append({**entry, "date": date_str})
        days.append({
            "date": date_str, "has_metrics": True,
            "downloaded": d_dl, "skipped_existing": d_sk, "errors": d_err,
            "other": d_oth, "processed": d_proc,
            "metrics_complete": d_complete,
            "utilities": day_utils, "by_utility": day_by_utility,
            "run_count": d_runs,
            "last_update": max((r[9] for u in date_utils.values() for r in u if r[9]), default=None),
        })
        totals["downloaded"] += d_dl; totals["skipped_existing"] += d_sk
        totals["errors"] += d_err; totals["other"] += d_oth; totals["processed"] += d_proc
    return {
        "totals": totals, "days": days, "utilities": utilities_list,
        "has_metrics": True,
        "metrics_complete": all(d["metrics_complete"] for d in days),
    }


# ── run logs ──────────────────────────────────────────────────────────────────

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
        if enabled:
            row = conn.execute(
                "SELECT frequency,time_of_day,day_of_week,day_of_month FROM schedules WHERE id=?",
                (schedule_id,),
            ).fetchone()
            if row is None:
                return
            next_run_at = compute_next_run(row[0], row[1], row[2], row[3])
            conn.execute(
                "UPDATE schedules SET enabled=1,next_run_at=? WHERE id=?",
                (next_run_at, schedule_id),
            )
        else:
            conn.execute("UPDATE schedules SET enabled=0 WHERE id=?", (schedule_id,))
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
