"""
_visual.py  —  Helpers visuais compartilhados pelos pipelines
=============================================================
Importe com:
    from core.pipelines._visual import _p, _info, _ok, _fail, _warn, _sep, _banner, _step, _rodar
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
    from core.pipelines._session_runtime import maybe_wrap_pipeline_command
except ModuleNotFoundError:  # pragma: no cover - fallback para execucoes diretas
    from _session_runtime import maybe_wrap_pipeline_command  # type: ignore[no-redef]

# Garante UTF-8 no stdout/stderr mesmo quando o terminal usa cp1252 (Windows pipe)
if hasattr(sys.stdout, "buffer") and getattr(sys.stdout, "encoding", "").lower().replace("-", "") not in ("utf8", "utf8bom"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
if hasattr(sys.stderr, "buffer") and getattr(sys.stderr, "encoding", "").lower().replace("-", "") not in ("utf8", "utf8bom"):
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace", line_buffering=True)

_W = 64  # largura dos separadores


def _ts() -> str:
    return dt.datetime.now().strftime("%H:%M:%S")


def _p(msg: str = "") -> None:
    print(msg, flush=True)


def _info(msg: str) -> None:
    print(f"[{_ts()}]  {msg}", flush=True)


def _ok(msg: str, elapsed: float | None = None) -> None:
    sufixo = f"  ({elapsed:.0f}s)" if elapsed is not None else ""
    print(f"[{_ts()}] ✓  {msg}{sufixo}", flush=True)


def _fail(msg: str, code: int | None = None) -> None:
    sufixo = f"  (exit {code})" if code is not None else ""
    print(f"[{_ts()}] ✗  {msg}{sufixo}", flush=True)


def _warn(msg: str) -> None:
    print(f"[{_ts()}] ⚠  {msg}", flush=True)


def _sep(char: str = "─", w: int = _W) -> None:
    print(char * w, flush=True)


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


def _step(nome: str) -> None:
    _p()
    _p(f"▶  {nome}")
    _sep("·", _W)


def _step_fim(nome: str, ok: bool, elapsed: float) -> None:
    _sep("·", _W)
    if ok:
        _ok(nome, elapsed)
    else:
        _fail(nome)
    _p()


def _mkdir_seguro(pasta: Path) -> None:
    try:
        pasta.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass


def _resetar_auditoria(pasta_saida: Path) -> None:
    arq = pasta_saida / "auditoria_resultados.csv"
    if arq.exists():
        try:
            arq.unlink()
            _info(f"[reset] auditoria_resultados.csv removido ({pasta_saida.name})")
        except Exception as exc:
            _warn(f"[reset] Falha ao remover auditoria: {exc}")


def _atualizar_master(pasta_saida: Path, local_dir: Path) -> None:  # noqa: ARG001
    """Atualiza STATUS_DIGITACAO no índice mestre a partir do auditoria_resultados.csv.

    Usa import absoluto de core.indice_master para evitar resolução ambígua entre
    core/indice_master.py e scripts/infra/indice_master.py quando core/ precede a
    raiz do projeto no sys.path (caso do subprocess do pipeline).
    """
    auditoria = pasta_saida / "auditoria_resultados.csv"
    if not auditoria.exists():
        _warn(f"[MASTER] auditoria_resultados.csv não encontrado em {pasta_saida.name} — índice NÃO atualizado")
        return
    from core.indice_master import IndiceMasterError, marcar_digitados_auditoria
    try:
        contadores = marcar_digitados_auditoria(auditoria)
        _info(f"[MASTER] {pasta_saida.name} → {contadores}")
    except IndiceMasterError as exc:
        _fail(f"[MASTER] Erro ao atualizar índice master ({pasta_saida.name}): {exc}")
        raise
    except Exception as exc:
        _fail(f"[MASTER] Erro inesperado ao atualizar índice master ({pasta_saida.name}): {exc}")
        raise IndiceMasterError(str(exc)) from exc


def _rodar(
    descricao: str,
    cmd: list[str],
    env_extra: dict[str, str] | None = None,
) -> int:
    """Executa subprocesso com saída visual. Retorna exit code."""
    cmd = maybe_wrap_pipeline_command(cmd)
    _step(descricao)
    _info(f"Cmd: {' '.join(str(c) for c in cmd)}")
    _p()

    env = os.environ.copy()
    env["PYTHONUTF8"]       = "1"
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
        for linha in iter(stream.readline, ""):
            linha = linha.rstrip()
            if linha:
                tag = f"[{prefixo}] " if prefixo else "  "
                print(f"{tag}{linha}", flush=True)

    t_out = threading.Thread(target=_drenar, args=(proc.stdout, ""),    daemon=True)
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

    if interrupted:
        _step_fim(descricao, ok=False, elapsed=elapsed)
        return 130

    code = int(proc.returncode or 0)
    _step_fim(descricao, ok=(code == 0), elapsed=elapsed)
    return code
