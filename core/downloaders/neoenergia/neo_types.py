"""
neo_types.py - Tipos compartilhados dos workers Neoenergia.

Fornece:
- JobStatus: enum de estados possíveis de um job
- JobResult: dataclass com todos os campos de resultado
- log_job: função de log padronizada
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Optional


class JobStatus(str, Enum):
    """Estados possíveis de um job de download Neoenergia."""

    BAIXADO = "BAIXADO"
    JA_EXISTIA = "JA_EXISTIA"
    SEM_FATURA = "SEM_FATURA"
    SEM_ACESSO = "SEM_ACESSO"
    CREDENCIAL_INVALIDA = "CREDENCIAL_INVALIDA"
    SESSAO_EXPIRADA = "SESSAO_EXPIRADA"
    PORTAL_INDISPONIVEL = "PORTAL_INDISPONIVEL"
    TIMEOUT = "TIMEOUT"
    ERRO_DOWNLOAD = "ERRO_DOWNLOAD"
    PDF_INVALIDO = "PDF_INVALIDO"
    JOB_INVALIDO = "JOB_INVALIDO"
    PENDENTE_RETRY = "PENDENTE_RETRY"
    ERRO_INESPERADO = "ERRO_INESPERADO"

    # Backward-compat alias
    ERRO = "ERRO_INESPERADO"


@dataclass
class JobResult:
    """Resultado completo de um job de download."""

    concessionaria: str
    job_id: str
    cnpj: str               # deve ser mascarado (ex: "12.345.678/****-**")
    instalacao: str
    referencia: str
    tentativa: int = 1
    arquivo: Optional[str] = None
    tamanho: Optional[int] = None
    hash_md5: Optional[str] = None
    duracao_s: Optional[float] = None
    status: JobStatus = JobStatus.PENDENTE_RETRY
    mensagem: str = ""
    excecao_resumida: str = ""
    retentavel: bool = True

    # Campos internos — NÃO incluídos na serialização segura
    _senha: str = field(default="", repr=False, compare=False)

    def to_dict(self) -> dict:
        """Serializa sem campos sensíveis."""
        d = asdict(self)
        d.pop("_senha", None)
        d["status"] = self.status.value if isinstance(self.status, JobStatus) else self.status
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


def mascarar_cnpj(cnpj: str) -> str:
    """Retorna CNPJ com dígitos centrais substituídos por *."""
    digitos = "".join(ch for ch in str(cnpj) if ch.isdigit())
    if len(digitos) == 14:
        return f"{digitos[:2]}.{digitos[2:5]}.{digitos[5:8]}/****-**"
    return cnpj[:4] + "****" + cnpj[-2:] if len(cnpj) > 6 else "****"


def log_job(result: JobResult, logger) -> None:
    """Emite linha padronizada de resultado de job no logger informado."""
    status = result.status.value if isinstance(result.status, JobStatus) else result.status
    duracao = f" dur={result.duracao_s:.1f}s" if result.duracao_s is not None else ""
    tamanho = f" size={result.tamanho}B" if result.tamanho is not None else ""
    arquivo = f" arquivo={result.arquivo}" if result.arquivo else ""
    excecao = f" exc={result.excecao_resumida!r}" if result.excecao_resumida else ""
    linha = (
        f"[JOB] conc={result.concessionaria} id={result.job_id}"
        f" cnpj={result.cnpj} inst={result.instalacao}"
        f" ref={result.referencia} tent={result.tentativa}"
        f" status={status}{duracao}{tamanho}{arquivo}{excecao}"
        f" msg={result.mensagem!r}"
    )
    if status in ("BAIXADO",):
        logger.info(linha)
    elif status in ("JA_EXISTIA", "SEM_FATURA"):
        logger.debug(linha)
    else:
        logger.warning(linha)
