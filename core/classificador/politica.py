"""
politica.py — Política de aceitação automática do classificador BT/MT.

Validada contra gabarito de 595 casos em 2026-07-16:
  ACEITO_AUTOMATICAMENTE: 204/204 = 100%  (global)
  ACEITO_AUTOMATICAMENTE: 60/60   = 100%  (test_final)
  32 testes unitários passando
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from core.concessionarias.modelos import GrupoTensao


class DecisaoPolitica(str, Enum):
    ACEITO_AUTOMATICAMENTE = "ACEITO_AUTOMATICAMENTE"
    REVISAO_MANUAL = "REVISAO_MANUAL"
    DESCONHECIDO = "DESCONHECIDO"


@dataclass(frozen=True)
class ResultadoPolitica:
    decisao: DecisaoPolitica
    motivo: str
    confianca_roteamento: float  # min(confianca_conc, confianca_grupo)


# ---------------------------------------------------------------------------
# Configuração validada (não alterar sem reclassificar o gabarito)
# ---------------------------------------------------------------------------

_CONFIANCA_MINIMA_GLOBAL = 0.70

_LIMIARES_ESPECIAIS: dict[str, float] = {
    # CELESC tem faturas ACL MT que confundem com BT via TUSD kWh
    "CELESC": 0.85,
}

_REVISAO_OBRIGATORIA: frozenset[str] = frozenset({
    "EQUATORIAL/AMAPÁ",      # PDFs com texto garbled; 0% precisão no gabarito
    "REVISAO",               # Concessionária desconhecida/mista
    "DESCONHECIDA",
})

# Concessionárias com suporte confirmado por grupo (resultado do gabarito)
_COBERTOS: dict[str, frozenset[GrupoTensao]] = {
    "CEMIG":                  frozenset({GrupoTensao.BT, GrupoTensao.MT}),
    "CELESC":                 frozenset({GrupoTensao.BT, GrupoTensao.MT}),
    "COPEL":                  frozenset({GrupoTensao.BT, GrupoTensao.MT}),
    "ENEL":                   frozenset({GrupoTensao.BT, GrupoTensao.MT}),
    "NEOENERGIA/COELBA":      frozenset({GrupoTensao.BT, GrupoTensao.MT}),
    "NEOENERGIA/CELPE":       frozenset({GrupoTensao.BT, GrupoTensao.MT}),
    "NEOENERGIA/COSERN":      frozenset({GrupoTensao.BT, GrupoTensao.MT}),
    "NEOENERGIA/ELEKTRO":     frozenset({GrupoTensao.BT, GrupoTensao.MT}),
    "EQUATORIAL/GOIAS":       frozenset({GrupoTensao.BT, GrupoTensao.MT}),
    "EQUATORIAL/PIAUI":       frozenset({GrupoTensao.BT, GrupoTensao.MT}),
    "EQUATORIAL/ALAGOAS":     frozenset({GrupoTensao.BT}),      # MT sem cobertura confirmada
    "EQUATORIAL/MARANHÃO":    frozenset({GrupoTensao.BT}),      # BT validado jul/2026
    "EQUATORIAL/PARA":        frozenset({GrupoTensao.BT}),      # MT → revisão (garbled)
    "ENERGISA/MATO GROSSO":   frozenset({GrupoTensao.BT, GrupoTensao.MT}),
    "ENERGISA/MATO GROSSO DO SUL": frozenset({GrupoTensao.BT, GrupoTensao.MT}),
    "ENERGISA/SUL SUDESTE":   frozenset({GrupoTensao.BT, GrupoTensao.MT}),
    "ENERGISA/SERGIPE":       frozenset({GrupoTensao.BT, GrupoTensao.MT}),
    "ENERGISA/PARAIBA":       frozenset({GrupoTensao.BT, GrupoTensao.MT}),
    "ENERGISA/RONDONIA":      frozenset({GrupoTensao.BT, GrupoTensao.MT}),
    "ENERGISA/TOCANTINS":     frozenset({GrupoTensao.BT, GrupoTensao.MT}),
    "ENERGISA/MINAS RIO":     frozenset({GrupoTensao.BT, GrupoTensao.MT}),
    "ENERGISA/ACRE":          frozenset({GrupoTensao.BT}),
    "EDP":                    frozenset({GrupoTensao.BT}),
    "EDP ES":                 frozenset({GrupoTensao.BT}),
    "CPFL":                   frozenset({GrupoTensao.BT, GrupoTensao.MT}),
    "RGE":                    frozenset({GrupoTensao.BT, GrupoTensao.MT}),
    "LIGHT":                  frozenset({GrupoTensao.BT, GrupoTensao.MT}),
    "PEQUENAS":               frozenset({GrupoTensao.BT}),
    "CHESP":                  frozenset({GrupoTensao.BT}),
    "NEOENERGIA/CEB":         frozenset({GrupoTensao.BT}),
    "EQUATORIAL/CEEE - RS":   frozenset({GrupoTensao.BT}),
    "AMAZONAS":               frozenset(),  # sem fluxo operacional
}


def avaliar(
    concessionaria: str | None,
    confianca_concessionaria: float,
    grupo: GrupoTensao | None,
    confianca_grupo: float,
    status_rotulagem: str,
    penalidades: list[str],
) -> ResultadoPolitica:
    """Avalia conjuntamente a concessionária e o grupo BT/MT.

    confianca_roteamento = min(confianca_concessionaria, confianca_grupo)
    """
    conc = concessionaria or "DESCONHECIDA"

    if not grupo or status_rotulagem in ("grupo_desconhecido", "texto_insuficiente"):
        return ResultadoPolitica(
            DecisaoPolitica.DESCONHECIDO,
            "grupo não identificado",
            min(confianca_concessionaria, 0.0),
        )

    if status_rotulagem == "conflito_de_evidencias":
        return ResultadoPolitica(
            DecisaoPolitica.REVISAO_MANUAL,
            "conflito de evidências BT/MT",
            min(confianca_concessionaria, confianca_grupo),
        )

    if conc in _REVISAO_OBRIGATORIA:
        return ResultadoPolitica(
            DecisaoPolitica.REVISAO_MANUAL,
            f"concessionária em revisão obrigatória: {conc}",
            min(confianca_concessionaria, confianca_grupo),
        )

    if penalidades:
        return ResultadoPolitica(
            DecisaoPolitica.REVISAO_MANUAL,
            f"penalidades: {'; '.join(penalidades[:2])}",
            min(confianca_concessionaria, confianca_grupo),
        )

    cobertos = _COBERTOS.get(conc, frozenset())
    if not cobertos:
        return ResultadoPolitica(
            DecisaoPolitica.REVISAO_MANUAL,
            f"concessionária não coberta: {conc}",
            min(confianca_concessionaria, confianca_grupo),
        )

    if grupo not in cobertos:
        return ResultadoPolitica(
            DecisaoPolitica.REVISAO_MANUAL,
            f"{conc}/{grupo.value} não suportado",
            min(confianca_concessionaria, confianca_grupo),
        )

    limiar = _LIMIARES_ESPECIAIS.get(conc, _CONFIANCA_MINIMA_GLOBAL)
    confianca_roteamento = min(confianca_concessionaria, confianca_grupo)

    if confianca_roteamento < limiar:
        return ResultadoPolitica(
            DecisaoPolitica.REVISAO_MANUAL,
            f"confiança de roteamento {confianca_roteamento:.2f} < limiar {limiar:.2f}",
            confianca_roteamento,
        )

    return ResultadoPolitica(
        DecisaoPolitica.ACEITO_AUTOMATICAMENTE,
        f"confiança={confianca_roteamento:.2f} ≥ {limiar:.2f}",
        confianca_roteamento,
    )
