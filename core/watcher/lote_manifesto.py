"""
Gerenciamento de lote fechado para o Watcher V2.

Conceito:
    Um lote é um snapshot imutável do conjunto de PDFs a processar no momento
    em que o ciclo começa. Qualquer PDF adicionado depois é detectado como
    "incremental" e não incluído silenciosamente no lote em andamento.

Fluxo:
    1. fechar_lote(pasta) → LoteManifesto com hash e horário de cada PDF.
    2. Processar somente os PDFs do lote fechado.
    3. detectar_incrementais(pasta, lote) → lista de novos PDFs adicionados depois.
    4. Persistir o manifesto em JSON para auditoria e reconciliação.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class EntradaLote:
    nome: str
    caminho: str
    tamanho_bytes: int
    sha256: str
    horario_inclusao: str  # ISO 8601


@dataclass
class LoteManifesto:
    lote_id: str
    horario_fechamento: str  # ISO 8601
    pasta_origem: str
    entradas: list[EntradaLote] = field(default_factory=list)

    @property
    def sha256_set(self) -> set[str]:
        return {e.sha256 for e in self.entradas}

    @property
    def nomes(self) -> set[str]:
        return {e.nome for e in self.entradas}

    def to_dict(self) -> dict:
        return {
            "lote_id": self.lote_id,
            "horario_fechamento": self.horario_fechamento,
            "pasta_origem": self.pasta_origem,
            "total": len(self.entradas),
            "entradas": [asdict(e) for e in self.entradas],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "LoteManifesto":
        lote = cls(
            lote_id=d["lote_id"],
            horario_fechamento=d["horario_fechamento"],
            pasta_origem=d["pasta_origem"],
        )
        for e in d.get("entradas", []):
            lote.entradas.append(EntradaLote(**e))
        return lote

    def salvar(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @classmethod
    def carregar(cls, path: Path) -> "LoteManifesto":
        return cls.from_dict(json.loads(path.read_text("utf-8")))


def _sha256_arquivo(path: Path) -> str:
    h = hashlib.sha256()
    try:
        with path.open("rb") as f:
            for bloco in iter(lambda: f.read(65536), b""):
                h.update(bloco)
        return h.hexdigest()
    except OSError:
        return ""


def fechar_lote(pasta: Path, *, lote_id: str | None = None) -> LoteManifesto:
    """Cria um LoteManifesto com snapshot dos PDFs presentes na pasta no momento.

    Nenhum arquivo é movido ou alterado. O manifesto é apenas o inventário.
    PDFs adicionados depois desta chamada serão detectados como incrementais.
    """
    agora = dt.datetime.now()
    lote_id = lote_id or agora.strftime("%Y%m%d_%H%M%S")
    lote = LoteManifesto(
        lote_id=lote_id,
        horario_fechamento=agora.isoformat(),
        pasta_origem=str(pasta),
    )

    if not pasta.exists():
        return lote

    for pdf in sorted(pasta.rglob("*.pdf")):
        if not pdf.is_file():
            continue
        stat = pdf.stat()
        sha = _sha256_arquivo(pdf)
        if not sha:
            continue
        lote.entradas.append(
            EntradaLote(
                nome=pdf.name,
                caminho=str(pdf),
                tamanho_bytes=stat.st_size,
                sha256=sha,
                horario_inclusao=dt.datetime.fromtimestamp(stat.st_mtime).isoformat(),
            )
        )

    return lote


def detectar_incrementais(
    pasta: Path,
    lote: LoteManifesto,
) -> list[Path]:
    """Detecta PDFs adicionados à pasta APÓS o fechamento do lote.

    Retorna lista de caminhos de PDFs que não estavam no manifesto original.
    Estes arquivos não devem ser incluídos no lote em andamento.
    """
    if not pasta.exists():
        return []

    incrementais: list[Path] = []
    sha_originais = lote.sha256_set
    nomes_originais = lote.nomes

    for pdf in sorted(pasta.rglob("*.pdf")):
        if not pdf.is_file():
            continue
        if pdf.name in nomes_originais:
            # Mesmo nome: verificar se é o mesmo conteúdo
            sha = _sha256_arquivo(pdf)
            if sha in sha_originais:
                continue
        # Nome novo ou conteúdo diferente = incremental
        incrementais.append(pdf)

    return incrementais


def verificar_hash_no_destino(
    origem: Path,
    destino: Path,
    sha256_esperado: str,
) -> bool:
    """Confirma que o arquivo no destino tem o mesmo SHA-256 que o original.

    Deve ser chamado antes de remover o arquivo de origem.
    """
    if not destino.exists():
        return False
    sha_destino = _sha256_arquivo(destino)
    return sha_destino == sha256_esperado
