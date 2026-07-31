"""
neo_pdf.py - Utilitários de validação e manipulação de PDFs Neoenergia.

Funções:
  validar_pdf(path)           -> tuple[bool, str]
  calcular_hash(path)         -> str
  aguardar_download(pasta, timeout) -> list[Path]
  deduplicar(path, destino)   -> str ('novo', 'identico', 'diferente')
"""
from __future__ import annotations

import hashlib
import time
from pathlib import Path
from typing import List, Tuple

PDF_MIN_BYTES = 5 * 1024  # 5 KB


def validar_pdf(path: Path) -> Tuple[bool, str]:
    """
    Valida se *path* é um PDF genuíno.

    Verificações:
    - Arquivo existe e não está vazio
    - Tamanho mínimo de 5 KB
    - Começa com a assinatura '%PDF'
    - Não é HTML disfarçado (cabeçalho <!DOCTYPE ou <html)

    Returns
    -------
    (True, "ok") em caso de sucesso ou (False, motivo) em caso de falha.
    """
    try:
        if not path.exists():
            return False, "arquivo_nao_encontrado"

        tamanho = path.stat().st_size
        if tamanho == 0:
            return False, "arquivo_vazio"

        if tamanho < PDF_MIN_BYTES:
            return False, f"arquivo_pequeno_{tamanho}B"

        with open(path, "rb") as f:
            cabecalho = f.read(1024)

        # Verifica assinatura PDF
        if not cabecalho.startswith(b"%PDF"):
            # Verifica se é HTML
            cab_lower = cabecalho.lower()
            if cab_lower.startswith(b"<!doctype") or cab_lower.startswith(b"<html"):
                return False, "arquivo_html"
            return False, "assinatura_pdf_ausente"

        return True, "ok"

    except OSError as e:
        return False, f"erro_leitura:{e}"
    except Exception as e:
        return False, f"erro_inesperado:{e}"


def calcular_hash(path: Path) -> str:
    """
    Calcula o MD5 do arquivo em *path*.

    Returns
    -------
    String hexadecimal de 32 caracteres.
    """
    h = hashlib.md5()
    with open(path, "rb") as f:
        for bloco in iter(lambda: f.read(65536), b""):
            h.update(bloco)
    return h.hexdigest()


def aguardar_download(pasta: Path, timeout: int = 60) -> List[Path]:
    """
    Aguarda o desaparecimento de arquivos *.crdownload* na *pasta*.

    Returns
    -------
    Lista de arquivos PDF presentes após o download concluir.
    """
    inicio = time.time()
    while time.time() - inicio < timeout:
        pendentes = list(pasta.glob("*.crdownload"))
        if not pendentes:
            break
        time.sleep(0.5)

    return sorted(pasta.glob("*.pdf"), key=lambda p: p.stat().st_mtime, reverse=True)


def deduplicar(path: Path, destino: Path) -> str:
    """
    Compara *path* com *destino* para determinar duplicidade.

    Returns
    -------
    'novo'      - destino não existe
    'identico'  - conteúdo idêntico (mesmo hash MD5)
    'diferente' - destino existe mas com conteúdo diferente
    """
    if not destino.exists():
        return "novo"

    hash_origem = calcular_hash(path)
    hash_destino = calcular_hash(destino)

    if hash_origem == hash_destino:
        return "identico"

    return "diferente"
