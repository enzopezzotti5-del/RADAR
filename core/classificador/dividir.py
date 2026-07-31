"""
dividir.py — Divisão treino/validação/teste por instalação.

Regra: toda conta da mesma instalação (UC) fica no mesmo conjunto.
Quando a instalação não está disponível, usa hash estável de
(carimbo // 100) para evitar data leakage por mês.

Proporções padrão: 70% treino / 15% validação / 15% teste.
"""
from __future__ import annotations

import hashlib
from collections import defaultdict


def _chave_instalacao(row: dict) -> str:
    """Retorna chave agrupadora que nunca mistura instalações entre conjuntos."""
    inst = str(row.get("instalacao") or "").strip()
    if inst and inst not in ("", "0", "None"):
        return f"inst:{inst}"
    # Fallback: agrupar por (carimbo // 100) — aproxima "lotes" da mesma época
    try:
        num = int(str(row.get("carimbo", "0")).replace("BB_", ""))
        return f"lote:{num // 100}"
    except (ValueError, TypeError):
        return f"lote:desconhecido"


def _bucket(chave: str, seed: int = 42) -> str:
    """Mapeia chave → bucket estável (treino/validação/teste) via hash."""
    h = hashlib.md5(f"{seed}:{chave}".encode()).hexdigest()
    val = int(h[:8], 16) % 100
    if val < 70:
        return "treino"
    elif val < 85:
        return "validacao"
    else:
        return "teste"


def dividir(rows: list[dict], seed: int = 42) -> list[dict]:
    """
    Adiciona coluna "conjunto" (treino/validacao/teste) em cada linha.
    Garante que todas as linhas da mesma instalação ficam no mesmo conjunto.
    """
    # 1. Agrupar por instalação → determinar conjunto uma vez
    grupos: dict[str, str] = {}
    for row in rows:
        chave = _chave_instalacao(row)
        if chave not in grupos:
            grupos[chave] = _bucket(chave, seed)

    # 2. Atribuir
    resultado = []
    for row in rows:
        chave = _chave_instalacao(row)
        resultado.append({**row, "conjunto": grupos[chave]})

    return resultado


def estatisticas(rows_divididos: list[dict]) -> dict[str, int]:
    contagem: dict[str, int] = defaultdict(int)
    for row in rows_divididos:
        contagem[row.get("conjunto", "desconhecido")] += 1
    return dict(contagem)
