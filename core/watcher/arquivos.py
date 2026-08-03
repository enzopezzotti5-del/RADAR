"""Operações de arquivo seguras para o Watcher V2."""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import time
from pathlib import Path


_IGNORADOS = re.compile(
    r"^(?:~\$|\.~|\.)|(?:\.part|\.tmp|\.crdownload|\.~lock\.)$",
    re.IGNORECASE,
)


def calcular_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def arquivo_ignorado(path: Path) -> bool:
    """Retorna True para arquivos temporários, ocultos ou de partes incompletas."""
    nome = path.name
    if _IGNORADOS.search(nome):
        return True
    # Pastas iniciadas por _ são ignoradas (sem processar recursivamente)
    for parte in path.parts:
        if parte.startswith("_"):
            return True
    return False


def esta_estavel(path: Path, intervalo_s: float = 10.0, tentativas: int = 2) -> bool:
    """Verifica se o arquivo está estável (tamanho e mtime inalterados)."""
    try:
        stat0 = path.stat()
    except OSError:
        return False
    for _ in range(tentativas):
        time.sleep(intervalo_s)
        try:
            stat1 = path.stat()
        except OSError:
            return False
        if stat1.st_size != stat0.st_size or stat1.st_mtime != stat0.st_mtime:
            return False
        stat0 = stat1
    return True


def mover_seguro(origem: Path, destino_dir: Path, sha256_origem: str | None = None) -> Path:
    """Move arquivo com verificação de integridade e sem sobrescrever silenciosamente.

    Mesma partição → os.replace (atômico).
    Partições diferentes → copia, verifica SHA, renomeia, exclui origem.
    Colisão de nome → sufixo __<hash8>.
    """
    destino_dir.mkdir(parents=True, exist_ok=True)
    destino = destino_dir / origem.name

    # Resolver colisão
    if destino.exists():
        hash8 = (sha256_origem or calcular_sha256(origem))[:8]
        stem = origem.stem
        sufixo = origem.suffix
        destino = destino_dir / f"{stem}__{hash8}{sufixo}"

    try:
        # Tenta mover dentro do mesmo dispositivo (atômico)
        os.replace(str(origem), str(destino))
        return destino
    except OSError:
        pass

    # Cross-device: copiar → verificar → renomear → excluir
    tmp = destino.with_suffix(destino.suffix + ".tmp")
    shutil.copy2(str(origem), str(tmp))

    sha_dest = calcular_sha256(tmp)
    sha_orig = sha256_origem or calcular_sha256(origem)

    if sha_dest != sha_orig:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(
            f"SHA-256 divergente após cópia: origem={sha_orig[:16]} dest={sha_dest[:16]}"
        )

    os.replace(str(tmp), str(destino))
    origem.unlink()
    return destino


def varrer_pdfs(raiz: Path, recursivo: bool = True) -> list[Path]:
    """Lista PDFs na raiz, excluindo arquivos ignorados."""
    if not raiz.exists():
        return []
    padrao = "**/*.pdf" if recursivo else "*.pdf"
    return sorted(
        p for p in raiz.glob(padrao)
        if p.is_file() and not arquivo_ignorado(p)
    )
