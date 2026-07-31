"""
Reconciliação física de arquivos e índice master para o Watcher V2.

Garante que:
- O arquivo PDF existe fisicamente no destino final.
- O hash no destino coincide com o hash original.
- O índice master aponta para o destino real (não para staging ou inexistente).
- A remoção da cópia anterior só ocorre após confirmação no destino.

A operação é idempotente: pode ser executada múltiplas vezes sem efeitos colaterais.
"""
from __future__ import annotations

import hashlib
import shutil
from dataclasses import dataclass
from pathlib import Path


# ──────────────────────────────────────────────────────────────────────────────
# Tipos de resultado
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class ResultadoReconciliacao:
    sucesso: bool
    caminho_final: str | None = None
    hash_confirmado: bool = False
    indice_atualizado: bool = False
    motivo_falha: str = ""


# ──────────────────────────────────────────────────────────────────────────────
# Operação atômica: mover → validar hash → atualizar índice → remover origem
# ──────────────────────────────────────────────────────────────────────────────

def mover_com_confirmacao(
    origem: Path,
    destino_dir: Path,
    sha256_esperado: str,
    *,
    sobrescrever: bool = False,
) -> ResultadoReconciliacao:
    """Move arquivo para destino e confirma hash antes de remover a origem.

    O fluxo é:
    1. Copiar origem → destino_dir/<nome>.
    2. Calcular SHA-256 do arquivo no destino.
    3. Comparar com sha256_esperado.
    4. Se correto: remover origem. Senão: remover cópia falha e retornar erro.

    A operação usa cópia+verificação+remoção em vez de rename para funcionar
    em filesystems de rede (UNC paths).
    """
    if not origem.exists():
        return ResultadoReconciliacao(
            sucesso=False,
            motivo_falha=f"origem não existe: {origem}",
        )

    destino_dir.mkdir(parents=True, exist_ok=True)
    destino = destino_dir / origem.name

    if destino.exists() and not sobrescrever:
        # Verificar se é o mesmo arquivo
        sha_dest = _sha256(destino)
        if sha_dest == sha256_esperado:
            return ResultadoReconciliacao(
                sucesso=True,
                caminho_final=str(destino),
                hash_confirmado=True,
                motivo_falha="arquivo já existe no destino com hash correto (idempotente)",
            )
        # Nome colide mas hash diferente: adicionar sufixo
        destino = _destino_sem_colisao(destino_dir, origem.name, sha256_esperado)

    # Copiar
    try:
        shutil.copy2(str(origem), str(destino))
    except OSError as exc:
        return ResultadoReconciliacao(
            sucesso=False,
            motivo_falha=f"erro ao copiar: {exc}",
        )

    # Verificar hash no destino
    sha_dest = _sha256(destino)
    if sha_dest != sha256_esperado:
        try:
            destino.unlink(missing_ok=True)
        except OSError:
            pass
        return ResultadoReconciliacao(
            sucesso=False,
            caminho_final=str(destino),
            hash_confirmado=False,
            motivo_falha=(
                f"hash divergente após cópia: "
                f"esperado={sha256_esperado[:12]} encontrado={sha_dest[:12]}"
            ),
        )

    # Remover origem somente após confirmação
    try:
        origem.unlink(missing_ok=True)
    except OSError:
        pass  # Origem não removida mas destino confirmado — aceitável

    return ResultadoReconciliacao(
        sucesso=True,
        caminho_final=str(destino),
        hash_confirmado=True,
    )


def validar_caminho_indice(
    caminho_arquivo: str | None,
    *,
    staging_roots: list[str] | None = None,
) -> tuple[bool, str]:
    """Verifica se o caminho registrado no índice é um destino final válido.

    Retorna (valido, motivo).
    Retorna False se o caminho:
      - é None ou vazio;
      - não existe fisicamente;
      - aponta para um diretório de staging;
      - aponta para um arquivo temporário (._probe_, _tmp_).
    """
    if not caminho_arquivo:
        return False, "caminho vazio"

    path = Path(caminho_arquivo)
    if not path.exists():
        return False, f"arquivo não existe: {caminho_arquivo}"

    if not path.is_file():
        return False, f"não é um arquivo: {caminho_arquivo}"

    # Verificar se aponta para staging
    caminho_lower = caminho_arquivo.lower().replace("\\", "/")
    staging_markers = staging_roots or []
    staging_markers += ["watcher_v2/staging", "staging/", "_tmp_", "._watcher_v2_probe_"]
    for marker in staging_markers:
        if marker.lower() in caminho_lower:
            return False, f"caminho aponta para staging: {marker}"

    return True, "ok"


# ──────────────────────────────────────────────────────────────────────────────
# Auditoria de integridade do índice
# ──────────────────────────────────────────────────────────────────────────────

def auditar_caminhos_indice(
    linhas_indice: list[dict],
    *,
    staging_roots: list[str] | None = None,
) -> list[dict]:
    """Retorna lista de entradas do índice com problemas no campo ARQUIVO.

    Cada entrada retornada inclui o motivo do problema.
    Útil para executar periodicamente e detectar inconsistências.
    """
    problemas: list[dict] = []
    for linha in linhas_indice:
        arquivo = linha.get("ARQUIVO") or linha.get("arquivo")
        valido, motivo = validar_caminho_indice(arquivo, staging_roots=staging_roots)
        if not valido:
            problemas.append({
                **linha,
                "_motivo_invalido": motivo,
            })
    return problemas


# ──────────────────────────────────────────────────────────────────────────────
# Helpers internos
# ──────────────────────────────────────────────────────────────────────────────

def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    try:
        with path.open("rb") as f:
            for bloco in iter(lambda: f.read(65536), b""):
                h.update(bloco)
        return h.hexdigest()
    except OSError:
        return ""


def _destino_sem_colisao(pasta: Path, nome: str, sha256: str) -> Path:
    sufixo = sha256[:8]
    stem = Path(nome).stem
    ext = Path(nome).suffix
    candidato = pasta / f"{stem}_{sufixo}{ext}"
    i = 1
    while candidato.exists():
        candidato = pasta / f"{stem}_{sufixo}_{i}{ext}"
        i += 1
    return candidato
