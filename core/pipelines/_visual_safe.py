"""
_visual_safe.py - helper visual tolerante a erro de console/pipe.
"""
from __future__ import annotations

import datetime as dt
import io
import os
import subprocess
import sys
import threading
from pathlib import Path

try:
    from pipelines._session_runtime import maybe_wrap_pipeline_command
except ModuleNotFoundError:  # pragma: no cover - fallback para execucoes diretas
    from _session_runtime import maybe_wrap_pipeline_command  # type: ignore[no-redef]

if hasattr(sys.stdout, "buffer") and getattr(sys.stdout, "encoding", "").lower().replace("-", "") not in ("utf8", "utf8bom"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
if hasattr(sys.stderr, "buffer") and getattr(sys.stderr, "encoding", "").lower().replace("-", "") not in ("utf8", "utf8bom"):
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace", line_buffering=True)

_W = 64


def _ts() -> str:
    return dt.datetime.now().strftime("%H:%M:%S")


def _safe_print(msg: str = "") -> None:
    try:
        print(msg, flush=True)
    except (OSError, ValueError):
        try:
            sys.__stdout__.write(f"{msg}\n")
            sys.__stdout__.flush()
        except Exception:
            pass


def _p(msg: str = "") -> None:
    _safe_print(msg)


def _info(msg: str) -> None:
    _safe_print(f"[{_ts()}]  {msg}")


def _ok(msg: str, elapsed: float | None = None) -> None:
    sufixo = f"  ({elapsed:.0f}s)" if elapsed is not None else ""
    _safe_print(f"[{_ts()}] ✓  {msg}{sufixo}")


def _fail(msg: str, code: int | None = None) -> None:
    sufixo = f"  (exit {code})" if code is not None else ""
    _safe_print(f"[{_ts()}] ✗  {msg}{sufixo}")


def _warn(msg: str) -> None:
    _safe_print(f"[{_ts()}] ⚠  {msg}")


def _sep(char: str = "─", w: int = _W) -> None:
    _safe_print(char * w)


def _banner(titulo: str, detalhes: list[str] | None = None) -> None:
    _p()
    _sep("═")
    _p(f"  {titulo}")
    if detalhes:
        _sep("─")
        for linha in detalhes:
            _p(f"  {linha}")
    _sep("═")
    _p()


def _rodar(
    descricao: str,
    cmd: list[str],
    env_extra: dict[str, str] | None = None,
) -> int:
    cmd = maybe_wrap_pipeline_command(cmd)
    _p()
    _p(f"▶  {descricao}")
    _sep("·", _W)
    _info(f"Cmd: {' '.join(str(c) for c in cmd)}")
    _p()

    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    if env_extra:
        env.update({k: str(v) for k, v in env_extra.items()})

    t_inicio = dt.datetime.now()
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        env=env,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
    )

    def _drenar(stream, prefixo: str) -> None:
        try:
            for linha in iter(stream.readline, ""):
                linha = linha.rstrip()
                if not linha:
                    continue
                tag = f"[{prefixo}] " if prefixo else "  "
                _safe_print(f"{tag}{linha}")
        except (OSError, ValueError):
            pass
        finally:
            try:
                stream.close()
            except Exception:
                pass

    t_out = threading.Thread(target=_drenar, args=(proc.stdout, ""), daemon=True)
    t_err = threading.Thread(target=_drenar, args=(proc.stderr, "ERR"), daemon=True)
    t_out.start()
    t_err.start()

    interrupted = False
    try:
        while True:
            try:
                proc.wait(timeout=0.5)
                break
            except subprocess.TimeoutExpired:
                continue
            except KeyboardInterrupt:
                if proc.poll() is not None:
                    break
                interrupted = True
                _warn("Interrompido manualmente. Encerrando subprocesso...")
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
                break
    finally:
        t_out.join(timeout=5)
        t_err.join(timeout=5)

    elapsed = (dt.datetime.now() - t_inicio).total_seconds()
    _sep("·", _W)
    if interrupted:
        _fail(descricao)
        _p()
        return 130

    code = int(proc.returncode or 0)
    if code == 0:
        _ok(descricao, elapsed)
    else:
        _fail(descricao, code)
    _p()
    return code
