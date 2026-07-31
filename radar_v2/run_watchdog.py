"""
Watchdog do Radar V2.

- Mantém o servidor vivo: reinicia se o processo morrer ou o healthcheck falhar.
- Lock de arquivo impede múltiplas instâncias simultâneas.
- Configurável por variáveis de ambiente (todas com prefixo RADAR_).

Variáveis de ambiente:
    RADAR_PORT           porta do servidor     (padrão: 5000)
    RADAR_HOST           interface de escuta   (padrão: 0.0.0.0)
    RADAR_PROBE_HOST     host do healthcheck   (padrão: 127.0.0.1)
    RADAR_THREADS        threads do Waitress   (padrão: 8)
    RADAR_RESTART_DELAY  segundos antes de reiniciar (padrão: 5)
    RADAR_HEALTH_INTERVAL intervalo de healthcheck em segundos (padrão: 15)
    RADAR_MAX_FAILED_CHECKS checks falhos antes de reiniciar  (padrão: 3)
"""
from __future__ import annotations

import datetime as dt
import os
import socket
import subprocess
import time
from pathlib import Path

import requests

ROOT_DIR         = Path(__file__).resolve().parent.parent
LOG_DIR          = ROOT_DIR / "logs"
WATCHDOG_LOG     = LOG_DIR / "radar_v2_watchdog.log"
SERVER_STDOUT    = LOG_DIR / "radar_v2_stdout.log"
SERVER_STDERR    = LOG_DIR / "radar_v2_stderr.log"
LOCK_FILE        = LOG_DIR / "radar_v2_watchdog.lock"


def _log(message: str) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line  = f"[{stamp}] {message}\n"
    with WATCHDOG_LOG.open("a", encoding="utf-8") as fh:
        fh.write(line)
    print(line, end="")


def _bind_lock():
    """Retorna handle do lock se conseguiu adquirir; None se já há outra instância."""
    if os.name != "nt":
        return None
    import msvcrt
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    fh = LOCK_FILE.open("a+", encoding="utf-8")
    try:
        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        fh.close()
        return None
    fh.seek(0)
    fh.truncate()
    fh.write(str(os.getpid()))
    fh.flush()
    return fh


def _port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=2):
            return True
    except OSError:
        return False


def _health_ok(health_url: str) -> bool:
    try:
        resp = requests.get(health_url, timeout=5)
        if resp.status_code != 200:
            return False
        data = resp.json()
        return bool(data.get("ok"))
    except Exception:
        return False


def _start_child(host: str, port: int, threads: int) -> subprocess.Popen:
    cmd = [
        str(ROOT_DIR / ".venv" / "Scripts" / "python.exe"),
        str(ROOT_DIR / "radar_v2" / "run_server.py"),
        "--host",   host,
        "--port",   str(port),
        "--threads", str(threads),
    ]
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    with SERVER_STDOUT.open("a", encoding="utf-8") as out, \
         SERVER_STDERR.open("a", encoding="utf-8") as err:
        _log(f"Iniciando Radar V2: porta={port} pid=?")
        proc = subprocess.Popen(
            cmd, cwd=str(ROOT_DIR),
            stdout=out, stderr=err,
            env=env, creationflags=flags,
        )
    _log(f"Radar V2 iniciado pid={proc.pid}")
    return proc


def _stop_child(proc: subprocess.Popen, reason: str) -> None:
    _log(f"Encerrando Radar V2 ({reason}) pid={proc.pid}")
    proc.terminate()
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        _log(f"Processo não encerrou após terminate(); forçando kill pid={proc.pid}")
        proc.kill()
        proc.wait(timeout=10)


def main() -> int:
    lock = _bind_lock()
    if lock is None:
        _log("Watchdog já está em execução; nova instância ignorada.")
        return 0

    host             = os.environ.get("RADAR_HOST", "0.0.0.0")
    probe_host       = os.environ.get("RADAR_PROBE_HOST", "127.0.0.1")
    port             = int(os.environ.get("RADAR_PORT", "5000"))
    threads          = int(os.environ.get("RADAR_THREADS", "8"))
    health_url       = os.environ.get("RADAR_HEALTH_URL", f"http://{probe_host}:{port}/health")
    restart_delay    = int(os.environ.get("RADAR_RESTART_DELAY", "5"))
    health_interval  = int(os.environ.get("RADAR_HEALTH_INTERVAL", "15"))
    max_failed       = int(os.environ.get("RADAR_MAX_FAILED_CHECKS", "3"))

    _log(f"Watchdog iniciado — healthcheck: {health_url} intervalo={health_interval}s")

    proc: subprocess.Popen | None = None
    failed_checks = 0

    while True:
        # ── sem processo filho ────────────────────────────────────────────────
        if proc is None:
            if _port_open(probe_host, port) and _health_ok(health_url):
                _log(f"Radar já saudável em {health_url}; monitorando.")
                time.sleep(health_interval)
                continue
            if _port_open(probe_host, port):
                _log(f"Porta {port} ocupada mas healthcheck falhou; aguardando liberar.")
                time.sleep(restart_delay)
                continue
            proc = _start_child(host, port, threads)
            failed_checks = 0
            time.sleep(3)
            continue

        # ── processo filho existe ─────────────────────────────────────────────
        retcode = proc.poll()
        if retcode is not None:
            _log(f"Processo encerrou inesperadamente (exit={retcode}); reiniciando em {restart_delay}s.")
            proc = None
            failed_checks = 0
            time.sleep(restart_delay)
            continue

        if not _health_ok(health_url):
            failed_checks += 1
            _log(f"Healthcheck falhou ({failed_checks}/{max_failed}): {health_url}")
            if failed_checks >= max_failed:
                _stop_child(proc, f"{failed_checks} healthchecks falhos consecutivos")
                proc = None
                failed_checks = 0
                time.sleep(restart_delay)
            else:
                time.sleep(health_interval)
        else:
            if failed_checks > 0:
                _log("Healthcheck recuperado.")
            failed_checks = 0
            time.sleep(health_interval)


if __name__ == "__main__":
    raise SystemExit(main())
