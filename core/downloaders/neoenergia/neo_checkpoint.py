"""
neo_checkpoint.py - Retomada idempotente para workers Neoenergia.

Salva resultados de jobs em um arquivo JSONL para permitir retomada
de execuções parciais sem re-baixar o que já foi processado.

NUNCA salva senhas, cookies ou tokens.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import List, Optional

from core.downloaders.neoenergia.neo_types import JobResult, JobStatus

log = logging.getLogger(__name__)

STATUSES_CONCLUIDOS = {
    JobStatus.BAIXADO,
    JobStatus.JA_EXISTIA,
    JobStatus.SEM_FATURA,
    JobStatus.SEM_ACESSO,
    JobStatus.CREDENCIAL_INVALIDA,
}


def _chave_job(instalacao: str, referencia: str) -> str:
    inst = str(instalacao).strip().lstrip("0") or "0"
    ref = str(referencia).strip().upper()
    return f"{inst}|{ref}"


class Checkpoint:
    """
    Gerencia um arquivo JSONL de checkpoint para retomada idempotente.

    Parameters
    ----------
    path : Path
        Caminho completo do arquivo JSONL de checkpoint.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._concluidos: dict[str, JobStatus] = {}
        self._carregar_existente()

    @property
    def path(self) -> Path:
        return self._path

    def _carregar_existente(self) -> None:
        """Lê o arquivo JSONL e popula o dicionário interno."""
        if not self._path.exists():
            return
        try:
            with open(self._path, encoding="utf-8") as f:
                for linha in f:
                    linha = linha.strip()
                    if not linha:
                        continue
                    try:
                        dados = json.loads(linha)
                        # Nunca deve ter senha — sanidade extra
                        dados.pop("_senha", None)
                        dados.pop("senha", None)
                        inst = dados.get("instalacao", "")
                        ref = dados.get("referencia", "")
                        status_str = dados.get("status", "")
                        if inst and ref and status_str:
                            try:
                                status = JobStatus(status_str)
                            except ValueError:
                                status = JobStatus.ERRO_INESPERADO
                            self._concluidos[_chave_job(inst, ref)] = status
                    except json.JSONDecodeError:
                        continue
        except OSError as e:
            log.warning(f"Checkpoint: erro ao ler {self._path}: {e}")

    def registrar(self, result: JobResult) -> None:
        """
        Salva *result* no arquivo JSONL e atualiza o índice interno.
        Nunca persiste o campo _senha.
        """
        chave = _chave_job(result.instalacao, result.referencia)
        dados = result.to_dict()
        # Garantia extra: nenhum campo sensível
        dados.pop("_senha", None)
        dados.pop("senha", None)

        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._path, "a", encoding="utf-8") as f:
                f.write(json.dumps(dados, ensure_ascii=False) + "\n")
            self._concluidos[chave] = result.status
        except OSError as e:
            log.warning(f"Checkpoint: erro ao gravar {self._path}: {e}")

    def ja_concluido(self, instalacao: str, referencia: str) -> bool:
        """
        Retorna True se o job já foi registrado com status de conclusão.
        Jobs com PENDENTE_RETRY ou ERRO_INESPERADO NÃO são considerados concluídos.
        """
        chave = _chave_job(instalacao, referencia)
        status = self._concluidos.get(chave)
        return status in STATUSES_CONCLUIDOS

    def pendentes(self, jobs: list) -> list:
        """
        Filtra *jobs* removendo os que já foram concluídos.

        Cada item de *jobs* deve ser um dict com chaves 'instalacao' e 'referencia',
        ou um objeto com atributos equivalentes.
        """
        resultado = []
        for job in jobs:
            if isinstance(job, dict):
                inst = job.get("instalacao", "")
                ref = job.get("referencia", "")
            else:
                inst = getattr(job, "instalacao", "")
                ref = getattr(job, "referencia", "")
            if not self.ja_concluido(inst, ref):
                resultado.append(job)
        return resultado

    @classmethod
    def carregar(cls, path: Path) -> List[JobResult]:
        """
        Carrega todos os JobResult registrados no arquivo JSONL.

        Returns
        -------
        Lista de JobResult. Linhas inválidas são ignoradas.
        """
        resultados: List[JobResult] = []
        if not path.exists():
            return resultados
        try:
            with open(path, encoding="utf-8") as f:
                for linha in f:
                    linha = linha.strip()
                    if not linha:
                        continue
                    try:
                        dados = json.loads(linha)
                        dados.pop("_senha", None)
                        dados.pop("senha", None)
                        status_str = dados.get("status", "ERRO_INESPERADO")
                        try:
                            status = JobStatus(status_str)
                        except ValueError:
                            status = JobStatus.ERRO_INESPERADO
                        resultados.append(
                            JobResult(
                                concessionaria=dados.get("concessionaria", ""),
                                job_id=dados.get("job_id", ""),
                                cnpj=dados.get("cnpj", ""),
                                instalacao=dados.get("instalacao", ""),
                                referencia=dados.get("referencia", ""),
                                tentativa=dados.get("tentativa", 1),
                                arquivo=dados.get("arquivo"),
                                tamanho=dados.get("tamanho"),
                                hash_md5=dados.get("hash_md5"),
                                duracao_s=dados.get("duracao_s"),
                                status=status,
                                mensagem=dados.get("mensagem", ""),
                                excecao_resumida=dados.get("excecao_resumida", ""),
                                retentavel=dados.get("retentavel", True),
                            )
                        )
                    except (json.JSONDecodeError, TypeError, KeyError):
                        continue
        except OSError as e:
            log.warning(f"Checkpoint.carregar: erro ao ler {path}: {e}")
        return resultados
