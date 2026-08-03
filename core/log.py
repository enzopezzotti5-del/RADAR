"""
core/log.py
-----------
Configuração central de logging via Loguru.

Uso em arquivos novos:
    from core.log import get_logger
    log = get_logger(__name__)
    log.info("mensagem")
    log.bind(carimbo="BB_2018233").info("processando")

Compatibilidade com código existente (logging padrão):
    Chamar `setup_logging()` uma vez no entry-point (watcher, pipeline, etc.)
    intercepta automaticamente todo `logging.getLogger(...)` já existente.
    Os 67 arquivos com `import logging` continuam funcionando sem alteração.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from loguru import Logger

_ROOT = Path(__file__).resolve().parent.parent
_LOG_DIR = _ROOT / "logs"

_FORMATO_CONSOLE = (
    "<green>{time:YYYY-MM-DD HH:mm:ss}</green>  "
    "<level>{level:<8}</level>  "
    "<cyan>{extra[contexto]}</cyan>"
    "{message}"
)
_FORMATO_ARQUIVO = (
    "{time:YYYY-MM-DD HH:mm:ss}  {level:<8}  {extra[contexto]}{message}"
)


class _InterceptHandler(logging.Handler):
    """Redireciona todo logging padrão para o loguru."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        frame, depth = sys._getframe(6), 6
        while frame and frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1

        logger.opt(depth=depth, exception=record.exc_info).log(
            level, record.getMessage()
        )


def setup_logging(
    nome: str = "energia",
    *,
    arquivo: Path | str | None = None,
    nivel: str = "INFO",
    rotacao: str = "10 MB",
    retencao: str = "30 days",
    console: bool = True,
) -> None:
    """
    Configura loguru e instala interceptador do logging padrão.

    Chamar uma vez no entry-point do processo. Chamadas subsequentes são
    ignoradas (idempotente pelo `logger.remove` + re-add).

    Args:
        nome:      prefixo do arquivo de log (ex: "watcher" → logs/watcher.log)
        arquivo:   caminho explícito do arquivo (override de nome)
        nivel:     nível mínimo ("DEBUG", "INFO", "WARNING", "ERROR")
        rotacao:   tamanho ou horário de rotação ("10 MB", "00:00", etc.)
        retencao:  quanto tempo manter logs antigos
        console:   se deve imprimir no stdout
    """
    logger.remove()  # remove handler padrão (stderr sem formato)

    logger.configure(extra={"contexto": ""})

    if console:
        logger.add(
            sys.stdout,
            level=nivel,
            format=_FORMATO_CONSOLE,
            colorize=True,
        )

    log_path = Path(arquivo) if arquivo else _LOG_DIR / f"{nome}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger.add(
        str(log_path),
        level=nivel,
        format=_FORMATO_ARQUIVO,
        rotation=rotacao,
        retention=retencao,
        encoding="utf-8",
        enqueue=True,   # thread-safe
    )

    # Intercepta todo logging padrão existente
    logging.basicConfig(handlers=[_InterceptHandler()], level=0, force=True)
    for name in logging.root.manager.loggerDict:
        logging.getLogger(name).handlers = []
        logging.getLogger(name).propagate = True


def get_logger(nome: str = "") -> "Logger":
    """
    Retorna logger contextualizado com o nome do módulo.

        log = get_logger(__name__)
        log.info("ok")
        log.bind(carimbo="BB_123", uc="MTE0001004").warning("campo zerado")
    """
    contexto = f"[{nome}] " if nome else ""
    return logger.bind(contexto=contexto)
