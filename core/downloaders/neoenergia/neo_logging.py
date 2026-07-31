"""
neo_logging.py - Logger padronizado para workers Neoenergia.

Formato de log:
  [NEOENERGIA][CONCESSIONARIA][extra] mensagem

Funções:
  get_neo_logger(concessionaria) -> logging.Logger
  log_step(logger, conc, job_idx, total, inst, ref, etapa, msg, duracao=None)
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from datetime import datetime
from typing import Optional


class _NeoFilter(logging.Filter):
    """Adiciona campo 'neo_prefix' ao LogRecord."""

    def __init__(self, concessionaria: str, extra: str = ""):
        super().__init__()
        self._prefix = f"[NEOENERGIA][{concessionaria.upper()}]"
        if extra:
            self._prefix += f"[{extra}]"

    def filter(self, record: logging.LogRecord) -> bool:
        record.neo_prefix = self._prefix
        return True


def get_neo_logger(
    concessionaria: str,
    log_dir: Optional[Path] = None,
    extra: str = "",
) -> logging.Logger:
    """
    Retorna (ou cria) um logger com handlers UTF-8 e prefixo padronizado.

    Parameters
    ----------
    concessionaria : str
        Nome da concessionária (COELBA, CELPE, COSERN, ELEKTRO, ...).
    log_dir : Path, optional
        Diretório para o arquivo de log. Se None, usa o diretório pai do módulo.
    extra : str, optional
        Texto adicional entre colchetes após a concessionária.
    """
    conc_upper = concessionaria.upper()
    name = f"neoenergia_{conc_upper.lower()}"

    logger = logging.getLogger(name)
    if logger.handlers:
        # Já configurado — retorna sem reconfigurar
        return logger

    logger.setLevel(logging.INFO)
    logger.propagate = False

    prefix = f"[NEOENERGIA][{conc_upper}]"
    if extra:
        prefix += f"[{extra}]"

    fmt = logging.Formatter(f"%(asctime)s {prefix} %(message)s")

    # Handler stdout com UTF-8
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(fmt)
    try:
        stream_handler.stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except (AttributeError, TypeError):
        pass
    logger.addHandler(stream_handler)

    # Handler de arquivo (UTF-8)
    if log_dir is None:
        log_dir = Path(__file__).resolve().parent / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = log_dir / f"neoenergia_{conc_upper.lower()}_{ts}.log"
    try:
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(fmt)
        logger.addHandler(file_handler)
    except OSError as e:
        logger.warning(f"Não foi possível criar arquivo de log {log_file}: {e}")

    return logger


def log_step(
    logger: logging.Logger,
    conc: str,
    job_idx: int,
    total: int,
    inst: str,
    ref: str,
    etapa: str,
    msg: str,
    duracao: Optional[float] = None,
    erro: bool = False,
) -> None:
    """
    Emite uma linha de progresso padronizada.

    Formato:
      [JOB {job_idx}/{total}] inst={inst} ref={ref} [ERRO]{etapa}: {msg} (dur={duracao:.1f}s)
    """
    dur_str = f" (dur={duracao:.1f}s)" if duracao is not None else ""
    erro_str = "[ERRO]" if erro else ""
    linha = f"[JOB {job_idx}/{total}] inst={inst} ref={ref} {erro_str}{etapa}: {msg}{dur_str}"
    if erro:
        logger.error(linha)
    else:
        logger.info(linha)
