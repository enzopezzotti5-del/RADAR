"""Gestão de locks para o Watcher V2.

Lock próprio V2: impede duas instâncias V2 simultâneas.
Lock global: compartilhado com o watcher oficial antes de carimbo/CONSEN.
"""
from __future__ import annotations

import contextlib
import os
import sys
import threading
import time
from pathlib import Path

_thread_lock = threading.Lock()
_LOCK_GLOBAL_DEFAULT = Path(__file__).resolve().parents[2] / "watcher.lock"
_LOCK_GLOBAL_ENV = "WATCHER_V2_GLOBAL_LOCK_PATH"


def caminho_lock_global(lock_path: Path | None = None) -> Path:
    """Resolve o caminho do lock global compartilhado com o watcher oficial."""
    if lock_path is not None:
        return lock_path
    override = os.environ.get(_LOCK_GLOBAL_ENV, "").strip()
    if override:
        return Path(override)
    return _LOCK_GLOBAL_DEFAULT


def _pid_ativo(pid: int) -> bool:
    """Verifica se um PID ainda está rodando (Windows/Linux)."""
    try:
        if sys.platform == "win32":
            import ctypes

            synchronize = 0x00100000
            handle = ctypes.windll.kernel32.OpenProcess(synchronize, 0, pid)
            if handle:
                ctypes.windll.kernel32.CloseHandle(handle)
                return True
            return False
        os.kill(pid, 0)
        return True
    except (PermissionError, ProcessLookupError, OSError):
        return False


def _tentar_adquirir(lock_path: Path, timeout_s: float = 0.0) -> bool:
    """Tenta criar o arquivo de lock. Retorna True se adquiriu."""
    deadline = time.monotonic() + timeout_s
    while True:
        with _thread_lock:
            try:
                lock_path.parent.mkdir(parents=True, exist_ok=True)
                fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    fh.write(str(os.getpid()))
                return True
            except FileNotFoundError:
                lock_path.parent.mkdir(parents=True, exist_ok=True)
                continue
            except FileExistsError:
                try:
                    pid = int(lock_path.read_text(encoding="utf-8").strip())
                    if not _pid_ativo(pid):
                        lock_path.unlink(missing_ok=True)
                        continue
                except (ValueError, OSError):
                    lock_path.unlink(missing_ok=True)
                    continue

        if time.monotonic() >= deadline:
            return False
        time.sleep(0.1)


def lock_proprio_v2_ocupado(lock_path: Path) -> bool:
    """Verifica se o lock V2 está ocupado por outro processo."""
    if not lock_path.exists():
        return False
    try:
        pid = int(lock_path.read_text(encoding="utf-8").strip())
        return _pid_ativo(pid) and pid != os.getpid()
    except (ValueError, OSError):
        return False


def lock_global_ocupado(lock_path: Path | None = None) -> bool:
    """Verifica se o watcher oficial está rodando."""
    return lock_proprio_v2_ocupado(caminho_lock_global(lock_path))


@contextlib.contextmanager
def adquirir_lock_proprio(lock_path: Path):
    """Adquire lock próprio V2 ou levanta RuntimeError."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    if not _tentar_adquirir(lock_path, timeout_s=2.0):
        raise RuntimeError(f"Outra instância V2 está rodando (lock: {lock_path})")
    try:
        yield
    finally:
        lock_path.unlink(missing_ok=True)


@contextlib.contextmanager
def adquirir_lock_global(lock_path: Path | None = None, timeout_s: float = 30.0):
    """Adquire lock global sem forçar remoção quando estiver ocupado."""
    resolved = caminho_lock_global(lock_path)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    if not _tentar_adquirir(resolved, timeout_s=timeout_s):
        raise RuntimeError(
            f"Não foi possível adquirir lock global em {timeout_s}s "
            f"(watcher oficial pode estar rodando). "
            "V2 encerra este ciclo e tentará novamente no próximo agendamento."
        )
    try:
        yield
    finally:
        try:
            pid = int(resolved.read_text(encoding="utf-8").strip())
            if pid == os.getpid():
                resolved.unlink(missing_ok=True)
        except (ValueError, OSError):
            resolved.unlink(missing_ok=True)
