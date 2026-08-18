"""
Radar_Orbit_Daily_Publisher
Publica novos PDFs baixados pelo Radar para o handoff outbox Radar->Orbit.

Uso:
    python scripts/infra/radar_orbit_daily_publisher.py [--batch N] [--dry-run]

Protecoes:
  - instancia unica (lock de arquivo)
  - nenhum downloader Radar ativo (por PID, porta 5000, Task Scheduler, DB)
  - nenhum run CPFL ativo (DB + sessoes de pipeline)
  - banco do Radar acessivel e nao bloqueado
  - handoff outbox acessivel
  - master acessivel
  - idempotencia por BB / SHA / handoff_id (handoff DB cobre todos os estados)
  - BB fora do indice_master (numeracao de fallback local) e ignorado com log,
    nao publicado cegamente
  - erro de um PDF nao impede os demais
  - log estruturado com inicio, fim, elegivel, publicado, ignorado, erro
  - exit code 0: sucesso ou skip controlado
  - exit code 1: falha real (sistema indisponivel)

Este script era historicamente um utilitario nao versionado (mantido apenas em
runtime/scripts/infra/). Foi movido para o repositorio para deixar de ser um
mecanismo invisivel a git log/code review: SCAN_SOURCES e o unico lugar onde
concessionarias sao adicionadas/removidas da publicacao em lote, e mudancas
aqui agora passam por revisao normal. Ele usa exatamente o mesmo contrato
canonico (publish_to_outbox / outbox-archive) que a chamada inline feita por
cada downloader logo apos salvar o PDF final - nao e uma integracao paralela,
e sim uma rede de seguranca/backfill para o mesmo contrato.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import logging
import os
import re
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

# ── paths ─────────────────────────────────────────────────────────────────────
# RADAR_ROOT derivado da posicao do script: scripts/infra/publisher.py -> [2]= Radar root
RADAR_ROOT = Path(__file__).resolve().parents[2]

# ORBIT_ROOT e HANDOFF_ROOT sao configurados via env var (atualizados na migracao)
ORBIT_ROOT   = Path(os.environ.get("ORBIT_RUNTIME_ROOT",
                                    r"C:\Users\Revit\Desktop\Orbit"))
HANDOFF_ROOT = Path(os.environ.get("ORBIT_RADAR_HANDOFF_ROOT",
                                    r"C:\Users\Revit\Desktop\handoff\orbit"))

RADAR_DB      = RADAR_ROOT / "logs" / "web_app" / "history.sqlite3"
HANDOFF_DB    = ORBIT_ROOT / "runtime" / "radar_handoff.sqlite3"
LOG_DIR       = RADAR_ROOT / "logs" / "batch_publisher"
LOCK_FILE     = RADAR_ROOT / ".batch_publisher.lock"
PIPELINE_SESS = ORBIT_ROOT / "logs" / "pipeline_sessoes"

AE      = "//10.10.250.21/Energia/ARQUIVOS ENZO"
MASTER  = AE + "/indice_master.csv"

SCAN_SOURCES = [
    ("dl_cpfl_bt",    "CPFL",              AE + "/DOWNLOAD CPFL/BT/07-2026"),
    ("dl_cpfl_bt",    "CPFL",              AE + "/DOWNLOAD CPFL/BT/08-2026"),
    ("dl_enel_sp",    "ENEL",              AE + "/DOWNLOAD ENEL/07-2026"),
    ("dl_enel_sp",    "ENEL",              AE + "/DOWNLOAD ENEL/08-2026"),
    ("dl_neo_coelba", "NEOENERGIA/COELBA", AE + "/DOWNLOAD NEOENERGIA/COELBA/2026-07"),
    ("dl_neo_coelba", "NEOENERGIA/COELBA", AE + "/DOWNLOAD NEOENERGIA/COELBA/2026-08"),
    ("dl_neo_celpe",  "NEOENERGIA/CELPE",  AE + "/DOWNLOAD NEOENERGIA/CELPE/2026-07"),
    ("dl_neo_celpe",  "NEOENERGIA/CELPE",  AE + "/DOWNLOAD NEOENERGIA/CELPE/2026-08"),
    ("dl_neo_cosern", "NEOENERGIA/COSERN", AE + "/DOWNLOAD NEOENERGIA/COSERN/2026-07"),
    ("dl_neo_cosern", "NEOENERGIA/COSERN", AE + "/DOWNLOAD NEOENERGIA/COSERN/2026-08"),
    # COPEL BT: adicionado para restaurar o backfill perdido silenciosamente
    # (esta lista nao era versionada antes; a ausencia de COPEL aqui nunca
    # apareceu em nenhum git log/code review).
    ("dl_copel_bt",   "COPEL",             AE + "/DOWNLOAD COPEL/07.2026/BT"),
    ("dl_copel_bt",   "COPEL",             AE + "/DOWNLOAD COPEL/08.2026/BT"),
    # CELESC BT/MT: nunca esteve nesta lista; a entrega dependia so do
    # pipeline legado pl_celesc_bt/pl_celesc_mt dentro do proprio Radar.
    ("dl_celesc_bt",  "CELESC",            AE + "/DOWNLOAD CELESC/07.2026/BT"),
    ("dl_celesc_bt",  "CELESC",            AE + "/DOWNLOAD CELESC/08.2026/BT"),
    ("dl_celesc_mt",  "CELESC",            AE + "/DOWNLOAD CELESC/07.2026/MT"),
    ("dl_celesc_mt",  "CELESC",            AE + "/DOWNLOAD CELESC/08.2026/MT"),
    # CEMIG: nunca teve nenhum caminho automatico de entrega ao Orbit.
    ("dl_cemig",      "CEMIG",             AE + "/DOWNLOAD CEMIG/07.2026/BT"),
    ("dl_cemig",      "CEMIG",             AE + "/DOWNLOAD CEMIG/08.2026/BT"),
    ("dl_cemig",      "CEMIG",             AE + "/DOWNLOAD CEMIG/07.2026/MT"),
    ("dl_cemig",      "CEMIG",             AE + "/DOWNLOAD CEMIG/08.2026/MT"),
]

ACTIVE_DOWNLOADER_TASK_PREFIXES = (
    "dl_cpfl", "dl_enel", "dl_neo", "dl_celesc", "dl_copel", "dl_light", "dl_cemig",
)
# Window for DB-based recency check. Records older than this are STALE unless
# corroborated by a live PID or Task Scheduler state.
RECENT_RUN_WINDOW_HOURS = 4
TERM_DIRS = ("Digitadas", "Ja_existiam_no_Consen")


# ── logging ───────────────────────────────────────────────────────────────────

def _setup_logging(log_path: Path) -> logging.Logger:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("publisher")
    logger.setLevel(logging.DEBUG)
    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-7s %(message)s", datefmt="%Y-%m-%dT%H:%M:%S"
    )
    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    logger.addHandler(fh)
    logger.addHandler(sh)
    return logger


# ── single-instance lock ──────────────────────────────────────────────────────

class _InstanceLock:
    def __init__(self) -> None:
        self._acquired = False

    def try_acquire(self) -> bool:
        if LOCK_FILE.exists():
            try:
                pid = int(LOCK_FILE.read_text().strip())
                import ctypes
                handle = ctypes.windll.kernel32.OpenProcess(1, False, pid)  # type: ignore[attr-defined]
                if handle:
                    ctypes.windll.kernel32.CloseHandle(handle)  # type: ignore[attr-defined]
                    return False  # process still alive
            except Exception:
                pass  # stale lock
        LOCK_FILE.write_text(str(os.getpid()))
        self._acquired = True
        return True

    def release(self) -> None:
        if self._acquired and LOCK_FILE.exists():
            try:
                LOCK_FILE.unlink()
            except Exception:
                pass


# ── operational state helpers ─────────────────────────────────────────────────

def _port_5000_alive() -> bool:
    """True if something is listening on port 5000 (Radar Flask)."""
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-NetTCPConnection -LocalPort 5000 -State Listen "
             "-EA SilentlyContinue | Measure-Object).Count"],
            capture_output=True, text=True, timeout=8,
        )
        return r.stdout.strip() not in ("0", "", "False")
    except Exception:
        return False


def _scheduler_downloader_running() -> str | None:
    """Returns task name if any downloader task is currently Running in Task Scheduler."""
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-ScheduledTask | Where-Object { $_.State -eq 'Running' } "
             "| Select-Object -ExpandProperty TaskName"],
            capture_output=True, text=True, timeout=10,
        )
        for line in r.stdout.splitlines():
            name = line.strip().lower()
            if any(name.startswith(p) for p in ACTIVE_DOWNLOADER_TASK_PREFIXES):
                return line.strip()
        return None
    except Exception:
        return None


def _radar_log_heartbeat_age_minutes() -> float | None:
    """Seconds since last line written to Radar's latest log file, or None."""
    try:
        log_dir = RADAR_ROOT / "logs" / "web_app"
        logs = sorted(log_dir.glob("*.log"), key=lambda f: f.stat().st_mtime)
        if not logs:
            return None
        age_s = (dt.datetime.now() - dt.datetime.fromtimestamp(logs[-1].stat().st_mtime)).total_seconds()
        return age_s / 60
    except Exception:
        return None


# ── guards ────────────────────────────────────────────────────────────────────

def _check_radar_db_accessible() -> tuple[bool, str]:
    if not RADAR_DB.exists():
        return False, f"Radar DB not found: {RADAR_DB}"
    try:
        con = sqlite3.connect(str(RADAR_DB), timeout=3)
        con.execute("BEGIN EXCLUSIVE")
        con.execute("COMMIT")
        con.close()
        return True, "ok"
    except sqlite3.OperationalError as e:
        return False, f"Radar DB locked: {e}"
    except Exception as e:
        return False, f"Radar DB error: {e}"


def _check_active_radar_runs() -> tuple[bool, str]:
    """
    Multi-evidence active run check. A run is ACTIVE only when multiple signals agree:

    Evidence sources (in priority order):
      1. Task Scheduler: any downloader task currently in Running state
      2. Radar DB: run with status=running, finished_at IS NULL, started recently (< RECENT_RUN_WINDOW_HOURS)
         AND port 5000 is alive (Radar Flask responding)
      3. Pipeline sessoes: em_execucao session for a downloader

    Stale DB records (started_at > RECENT_RUN_WINDOW_HOURS ago) are classified as STALE
    unless Task Scheduler shows a Running state.
    The 83 historical stale records (April-July 2026) have started_at >> 4h ago — excluded.
    """
    reasons: list[str] = []

    # Evidence 1: Task Scheduler Running state (strongest signal)
    sched_running = _scheduler_downloader_running()
    if sched_running:
        return True, f"Task Scheduler Running: {sched_running}"

    # Evidence 2: DB + port 5000
    port_alive = _port_5000_alive()
    try:
        con = sqlite3.connect(str(RADAR_DB), timeout=3)
        cutoff = (dt.datetime.now() - dt.timedelta(hours=RECENT_RUN_WINDOW_HOURS)).strftime(
            "%Y-%m-%dT%H:%M:%S"
        )
        rows = con.execute(
            "SELECT id, task_id, started_at FROM runs "
            "WHERE finished_at IS NULL AND started_at >= ? AND status='running'",
            (cutoff,),
        ).fetchall()
        con.close()

        recent_downloaders = [
            r for r in rows
            if any(str(r[1]).startswith(p) for p in ACTIVE_DOWNLOADER_TASK_PREFIXES)
        ]
        if recent_downloaders and port_alive:
            detail = "; ".join(
                f"run_id={r[0]} task={r[1]} started={r[2]}"
                for r in recent_downloaders
            )
            return True, f"DB+port5000: {detail}"
        if recent_downloaders and not port_alive:
            reasons.append(
                f"DB has {len(recent_downloaders)} recent null-finished runs "
                "but port 5000 dead — classifying as STALE (Radar not running)"
            )
    except Exception as e:
        reasons.append(f"DB query error: {e}")

    # Evidence 3: pipeline sessions
    try:
        for sess_dir in PIPELINE_SESS.iterdir():
            if not sess_dir.is_dir():
                continue
            for sf in sess_dir.glob("*.json"):
                try:
                    data = json.loads(sf.read_text(encoding="utf-8"))
                    if data.get("status") == "em_execucao":
                        tid = data.get("task_id", "")
                        if any(tid.startswith(p) for p in ACTIVE_DOWNLOADER_TASK_PREFIXES):
                            return True, f"pipeline session em_execucao: {sf.name}"
                except Exception:
                    pass
    except Exception:
        pass

    detail = "; ".join(reasons) if reasons else "none"
    return False, detail


def _check_active_cpfl_pipeline() -> tuple[bool, str]:
    try:
        for sess_dir in sorted(PIPELINE_SESS.iterdir(), reverse=True):
            if not sess_dir.is_dir():
                continue
            for sf in sess_dir.glob("*.json"):
                try:
                    data = json.loads(sf.read_text(encoding="utf-8"))
                    if data.get("status") == "em_execucao":
                        return True, str(sf)
                except Exception:
                    continue
        return False, "none"
    except Exception as e:
        return False, f"could not check ({e})"


def _check_handoff_accessible() -> tuple[bool, str]:
    try:
        outbox = HANDOFF_ROOT / "outbox"
        outbox.mkdir(parents=True, exist_ok=True)
        test = outbox / ".publisher_probe"
        test.write_text("probe")
        test.unlink()
        return True, str(outbox)
    except Exception as e:
        return False, f"handoff inaccessible: {e}"


def _check_master_accessible() -> tuple[bool, str]:
    try:
        p = Path(MASTER)
        if not p.exists():
            return False, f"not found: {MASTER}"
        return True, f"size={p.stat().st_size}"
    except Exception as e:
        return False, f"master inaccessible: {e}"


# ── state loading ─────────────────────────────────────────────────────────────

def _load_all_master_bbs() -> set[str]:
    """All BB_ carimbos that exist in indice_master.csv, regardless of status.

    Used to catch PDFs named by a local fallback counter (e.g. IndiceLocalCelesc
    starting at 2_000_000 when the shared master index could not be reached at
    download time) — those BBs never got a real reservation in the master and
    must never be delivered to Orbit as if they were legitimate carimbos.
    """
    bbs: set[str] = set()
    p = Path(MASTER)
    if not p.exists():
        return bbs
    try:
        with p.open(encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                bb = row.get("INDICE", "")
                if bb.startswith("BB_"):
                    bbs.add(bb)
    except Exception:
        pass
    return bbs


def _load_terminal_bbs() -> set[str]:
    """
    BBs that should be skipped:
      1. Terminal in master (ARQUIVO in Digitadas/Ja_existiam_no_Consen and file exists)
      2. Already in handoff DB (any status: DELIVERED or ACKNOWLEDGED)
      3. Currently in outbox (published but not yet drained — already_staged would catch
         them at runtime, but excluding here gives accurate ELIGIBLE counts in dry-run)
    """
    terminal: set[str] = set()
    p = Path(MASTER)
    if p.exists():
        try:
            with p.open(encoding="utf-8-sig") as f:
                for row in csv.DictReader(f):
                    bb = row.get("INDICE", "")
                    if not bb.startswith("BB_"):
                        continue
                    arquivo = row.get("ARQUIVO", "")
                    if arquivo and any(d in arquivo for d in TERM_DIRS) and os.path.exists(arquivo):
                        terminal.add(bb)
        except Exception:
            pass
    if HANDOFF_DB.exists():
        try:
            con = sqlite3.connect(str(HANDOFF_DB))
            for (sn,) in con.execute("SELECT source_name FROM handoffs").fetchall():
                m = re.search(r"BB_\d+", sn or "")
                if m:
                    terminal.add(m.group(0))
            con.close()
        except Exception:
            pass
    # Exclude BBs currently in outbox (published, awaiting drain).
    outbox = HANDOFF_ROOT / "outbox"
    if outbox.exists():
        for jf in outbox.glob("*.json"):
            try:
                d = json.loads(jf.read_text(encoding="utf-8"))
                sname = d.get("source_name", "")
                m = re.search(r"BB_\d+", sname)
                if m:
                    terminal.add(m.group(0))
            except Exception:
                pass
    return terminal


def _scan_ready(skip: set[str], known_bbs: set[str], log: logging.Logger) -> list[tuple[str, str, str, Path]]:
    seen: set[str] = set()
    ready: list[tuple[str, str, str, Path]] = []
    for task_id, utility, directory in SCAN_SOURCES:
        d = Path(directory)
        if not d.exists():
            continue
        for f in sorted(d.rglob("BB_*.pdf")):
            bb = f.stem
            if bb in seen or bb in skip:
                continue
            if known_bbs and bb not in known_bbs:
                log.warning(
                    f"  SKIP_UNKNOWN_BB bb={bb} task={task_id} path={f} "
                    "(nao encontrado em indice_master.csv — possivel numeracao "
                    "de fallback local; nao sera publicado)"
                )
                continue
            seen.add(bb)
            ready.append((bb, task_id, utility, f))
    return ready


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch", type=int, default=500)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = LOG_DIR / f"{stamp}.log"
    log = _setup_logging(log_path)
    started_at = dt.datetime.now()

    log.info("=== Radar_Orbit_Daily_Publisher START ===")
    log.info(
        f"batch={args.batch} dry_run={args.dry_run} "
        f"radar_root={RADAR_ROOT} orbit_root={ORBIT_ROOT} "
        f"handoff_root={HANDOFF_ROOT} log={log_path}"
    )

    lock = _InstanceLock()
    if not lock.try_acquire():
        log.warning("PUBLISHER_RESULT=SKIPPED_ALREADY_RUNNING")
        return 0

    result_code = 0
    try:
        # guard 1 already acquired above (single instance)

        # guard 2: Radar DB
        db_ok, db_reason = _check_radar_db_accessible()
        if not db_ok:
            log.error(f"GUARD FAIL radar_db: {db_reason}")
            log.error("PUBLISHER_RESULT=FAIL_RADAR_DB_UNAVAILABLE")
            return 1

        # guard 3: no active Radar downloader
        active_run, run_detail = _check_active_radar_runs()
        if active_run:
            log.warning(f"GUARD SKIP active_radar_run: {run_detail}")
            log.warning("PUBLISHER_RESULT=SKIPPED_ACTIVE_RADAR_RUN")
            return 0

        # guard 4: no active CPFL pipeline session
        active_cpfl, cpfl_detail = _check_active_cpfl_pipeline()
        if active_cpfl:
            log.warning(f"GUARD SKIP active_cpfl_pipeline: {cpfl_detail}")
            log.warning("PUBLISHER_RESULT=SKIPPED_ACTIVE_RADAR_RUN")
            return 0

        # guard 5: handoff
        hf_ok, hf_reason = _check_handoff_accessible()
        if not hf_ok:
            log.error(f"GUARD FAIL handoff: {hf_reason}")
            log.error("PUBLISHER_RESULT=FAIL_HANDOFF_UNAVAILABLE")
            return 1

        # guard 6: master
        master_ok, master_reason = _check_master_accessible()
        if not master_ok:
            log.error(f"GUARD FAIL master: {master_reason}")
            log.error("PUBLISHER_RESULT=FAIL_MASTER_UNAVAILABLE")
            return 1

        log.info("All guards passed.")
        heartbeat_age = _radar_log_heartbeat_age_minutes()
        if heartbeat_age is not None:
            log.info(f"Radar heartbeat: last log {heartbeat_age:.1f}min ago")

        # load Orbit publisher
        sys.path.insert(0, str(ORBIT_ROOT))
        os.environ.setdefault("ORBIT_RADAR_HANDOFF_ROOT", str(HANDOFF_ROOT))
        os.environ.setdefault("ORBIT_RUNTIME_ROOT", str(ORBIT_ROOT / "runtime"))
        from core.integrations.radar_handoff import default_config, publish_to_outbox  # noqa

        cfg = default_config()

        terminal = _load_terminal_bbs()
        known_bbs = _load_all_master_bbs()
        ready = _scan_ready(terminal, known_bbs, log)

        log.info(f"ELIGIBLE={len(ready)} (terminal_skipped={len(terminal)})")
        batch = ready[: args.batch]

        n_published = 0
        n_already   = 0
        n_error     = 0

        for bb, task_id, utility, path in batch:
            if args.dry_run:
                log.info(f"  DRY_RUN bb={bb} task={task_id} path={path.name}")
                n_published += 1
                continue
            try:
                r = publish_to_outbox(
                    cfg, path, task_id=task_id, utility=utility, run_id="daily_publisher"
                )
                if r.already_staged:
                    log.debug(f"  ALREADY_STAGED bb={bb} handoff={r.handoff_id[:16]}")
                    n_already += 1
                else:
                    log.info(f"  PUBLISHED bb={bb} handoff={r.handoff_id[:16]}")
                    n_published += 1
            except Exception as exc:
                log.error(f"  ERROR bb={bb}: {exc}")
                n_error += 1

        elapsed = (dt.datetime.now() - started_at).total_seconds()
        publisher_result = "SUCCESS" if n_error == 0 else "PARTIAL_ERROR"
        result_code = 1 if n_error > 0 and n_published == 0 else 0

        log.info("=== SUMMARY ===")
        log.info(f"ELIGIBLE={len(ready)}")
        log.info(f"PUBLISHED={n_published}")
        log.info(f"ALREADY_STAGED={n_already}")
        log.info(f"ERRORS={n_error}")
        log.info(f"ELAPSED_S={elapsed:.1f}")
        log.info(f"PUBLISHER_RESULT={publisher_result}")
        log.info(f"=== Radar_Orbit_Daily_Publisher END exit={result_code} ===")

        return result_code

    except Exception as exc:
        log.exception(f"PUBLISHER_RESULT=FAIL_UNEXPECTED: {exc}")
        return 1
    finally:
        lock.release()


if __name__ == "__main__":
    raise SystemExit(main())
