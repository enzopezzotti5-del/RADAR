"""Guardas operacionais puras para o lote CPFL/RGE."""

from __future__ import annotations

import os


# Evidencia de producao em 01/08 e 02/08/2026: maximo e p95 normais de 184
# UCs por titular. O limite padrao preserva 100% de folga e bloqueia a expansao
# anomala de 603 UCs antes do processamento individual.
DEFAULT_MAX_UCS_PER_TITULAR = 368
ENV_MAX_UCS_PER_TITULAR = "CPFL_MAX_UCS_PER_TITULAR"


class ExpansaoUcsError(RuntimeError):
    """Um titular excedeu o limite operacional antes do processamento."""


def resolver_max_ucs_por_titular(valor_cli: int = 0) -> int:
    """Resolve CLI > ambiente > limite operacional comprovado."""
    if valor_cli > 0:
        return valor_cli
    bruto = os.environ.get(ENV_MAX_UCS_PER_TITULAR, "").strip()
    if bruto:
        try:
            valor = int(bruto)
        except ValueError as exc:
            raise ValueError(
                f"{ENV_MAX_UCS_PER_TITULAR} deve ser um inteiro positivo"
            ) from exc
        if valor <= 0:
            raise ValueError(
                f"{ENV_MAX_UCS_PER_TITULAR} deve ser um inteiro positivo"
            )
        return valor
    return DEFAULT_MAX_UCS_PER_TITULAR


def validar_expansao_ucs(
    *,
    titular_id: str,
    titular_texto: str,
    total_ucs: int,
    max_ucs: int,
) -> None:
    """Bloqueia um titular anomalo antes de qualquer UC ser navegada."""
    if total_ucs <= max_ucs:
        return
    origem = titular_id or "sem-id"
    nome = titular_texto or "sem-descricao"
    raise ExpansaoUcsError(
        "EXPANSAO_UCS_BLOQUEADA: "
        f"titular_id={origem} titular={nome!r} total_ucs={total_ucs} "
        f"limite={max_ucs}; ajuste --max-ucs-por-titular ou "
        f"{ENV_MAX_UCS_PER_TITULAR} somente apos validacao operacional"
    )
