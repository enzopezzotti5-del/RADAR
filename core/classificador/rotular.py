"""
rotular.py — Classificação BT/MT por evidências textuais com hierarquia em camadas.

Camadas de evidência (prioridade decrescente):
  Tier 1 — DETERMINANTE  (+0.80): subgrupo explícito com prefixo estrutural
  Tier 2 — FORTE         (+0.50): modalidade tarifária, demanda com kW
  Tier 3 — COMPLEMENTAR  (+0.25): custo de disponibilidade, monômia, residencial
  Tier 4 — SECUNDÁRIA    (+0.10): nome de pasta/arquivo

Palavras que NÃO decidem isoladamente:
  demanda, ponta, fora ponta, energia reativa, tarifa, grupo, tensão, "AS" (artigo)

Regras obrigatórias:
  "SUBGRUPO B3"  → BT (determinante)
  "SUBGRUPO A4"  → MT (determinante)
  "TARIFA BRANCA" + ponta = Tarifa Branca BT, não MT
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Tier 1 — DETERMINANTE: subgrupo com prefixo estrutural
# ---------------------------------------------------------------------------
# MT: A1–A4, AS, A3a — exigem prefixo explícito ou formato de tabela de fatura
_MT_T1 = re.compile(
    r"(?:"
    # Prefixo semântico explícito (SUBGRUPO A4, CLASSIFICAÇÃO: AS, etc.)
    r"(?:SUBGRUPO|CLASSIFICA[CÇ][AÃ]O|MODALIDADE|GRUPO\s+TARIFÁRIO|CLASSE)\s*:?\s*A?\s*(A[S1-4]|A3a)"
    # "Grupo A" isolado (CELESC: Grupo/Subgrupo; LIGHT: Grupo A4)
    r"|GRUPO\s+A[S1-4a]"                        # "Grupo A4", "Grupo A3a", "Grupo AS"
    r"|GRUPO\s+A\b(?!\s+[Ee]\s+B)"              # "Grupo A" genérico
    # Formato ENEL: "A - A4 - VERDE" ou "A - AS - AZUL"
    r"|\bA\s*-\s*A[S1-4](?:a)?\s*-"
    # Formato LIGHT: "A4 / A4 - Verde" (subgrupo standalone com barra)
    r"|/\s*A[S1-4](?:a)?\s*-"
    r")",
    re.IGNORECASE,
)

# BT: B1–B4 — exigem prefixo estrutural OU formatos conhecidos de fatura
_BT_T1 = re.compile(
    r"(?:"
    # Prefixo semântico explícito (SUBGRUPO B3, CLASSIFICAÇÃO: B3, etc.)
    r"(?:SUBGRUPO|CLASSIFICA[CÇ][AÃ]O|MODALIDADE|GRUPO\s+TARIFÁRIO|CLASSE)\s*:?\s*(?:B-)?B[1-4]"
    # "Grupo B" isolado
    r"|GRUPO\s+B\b(?!\s+[Ee]\s+A)"
    # Formato "Convencional B3 Comercial" (CPFL, RGE)
    r"|(?:CONVENCIONAL(?:\s+\w+)?)\s+B[1-4]"
    # Tarifa Branca B3 = BT
    r"|TARIFA\s+BRANCA\s+B[1-4]"
    # Formato ENEL SP: "B - B3 - BRANCA" ou "B - B1 - Convencional"
    r"|\bB\s*-\s*B[1-4]\s*-"
    # Formato CELESC: "Grupo/Subgrupo Tensão:B/B3"
    r"|(?:GRUPO/SUBGRUPO[^\n]{0,20}:?\s*)?B/B[1-4]\b"
    # Formato COPEL/NEOENERGIA: "B3 Comercial", "B3 Residencial" como classe
    r"|\bB[1-4]\s+(?:COMERCIAL|RESIDENCIAL|INDUSTRIAL|RURAL|OUTROS|BANCOS|ILUMINA[CÇ][AÃ]O)\b"
    r")",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Tier 2 — FORTE: modalidade tarifária e demanda com contexto kW
# ---------------------------------------------------------------------------
_MT_T2 = re.compile(
    r"(?:"
    r"(?:MODALIDADE|TARIFA)[^\n]{0,10}(?:AZUL|VERDE)"  # Tarifa Azul/Verde
    r"|THS[_\s]*(?:AZUL|VERDE)"                         # THS_VERDE ou THS VERDE
    r"|DEMANDA\s+(?:CONTRATADA|FATURADA|REGISTRADA|MEDIDA|RESERVADA)(?:\s|[:\d([/])"  # Demanda com valor/unidade
    r"|DEMANDA\s+(?:PONTA|FORA\s*PONTA)[^\n]{0,20}kW"  # Demanda Ponta em kW (não kWh)
    r"|DEMANDA\s+REATIVA\s+(?:EXC|EXCEDENTE|MEDIDA|REGISTRADA)"  # Demanda Reativa = MT
    r"|USO\s+(?:DO\s+)?SISTEMA[^\n]{0,10}kW(?!h)"      # TUSD Fio em kW (não kWh) = MT
    r"|TUSD[^\n]{0,10}kW(?!h)"                          # TUSD em kW = MT
    r"|M[EÉ]DIA\s*TENS[ÃA]O|ALTA\s*TENS[ÃA]O|TENS[ÃA]O\s+PRIM[AÁ]RIA"
    r")",
    re.IGNORECASE,
)

# Tarifa Branca = BT com ponta/fora ponta em kWh (não confundir com MT)
_BT_T2 = re.compile(
    r"(?:"
    r"TARIFA\s+BRANCA"                                  # Modalidade Tarifa Branca (BT)
    r"|TARIFA\s+CONVENCIONAL"                           # Modalidade Convencional = BT
    r"|MODALIDADE\s+CONVENCIONAL"
    r"|CONSUMO\s+(?:PONTA|FORA\s*PONTA)[^\n]{0,20}kWh" # Consumo ponta em kWh = Tarifa Branca BT
    r"|CR[EÉ]DITO\s+FORA\s+PONTA[^\n]{0,20}kWh"        # Crédito Fora Ponta GD em kWh = BT
    r"|BAIXA\s*TENS[ÃA]O"
    # ENEL RJ (Ampla) — layout garbled: "OUTROS-CONV." aparece como "OS-CONV." no texto
    # extraído por pdfplumber devido ao entrelaçamento de colunas. "Outros-Convencional"
    # é uma modalidade tarifária exclusivamente BT (subgrupo B3 comercial).
    r"|OUTROS[-\s]+CONV(?:ENCIONAL)?"
    r"|(?<![A-Za-z])OS-CONV(?:ENCIONAL)?"
    r")",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Tier 3 — COMPLEMENTAR
# ---------------------------------------------------------------------------
_BT_T3 = re.compile(
    r"(?:"
    r"CUSTO\s+DE\s+DISPONIBILIDADE"
    r"|MO?N[ÔO]?MIA"
    r"|RESIDENCIAL\s+B[12]"
    r"|CONSUMO\s+M[IÍ]NIMO"
    r"|CONVENCIONAL"           # sem subgrupo — complementar apenas
    r")",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Extração de subgrupo individual (para o campo "subgrupo" no resultado)
# ---------------------------------------------------------------------------
_SUBGRUPO_MT_RE = re.compile(
    r"(?:SUBGRUPO|CLASSIFICA[CÇ][AÃ]O|MODALIDADE)\s*:?\s*(A[S1-4]|A3a)",
    re.IGNORECASE,
)
_SUBGRUPO_BT_RE = re.compile(
    r"(?:SUBGRUPO|CLASSIFICA[CÇ][AÃ]O|MODALIDADE)\s*:?\s*(?:B-)?B([1-4])",
    re.IGNORECASE,
)


def _normaliza(texto: str) -> str:
    return unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii").upper()


@dataclass
class ResultadoRotulagem:
    grupo: str | None
    subgrupo: str | None
    confianca: float
    evidencias: list[str]
    status: str
    penalidades: list[str] = field(default_factory=list)


# Pontuações por tier
_SCORE = {1: 0.80, 2: 0.50, 3: 0.25, 4: 0.10}


def _coletar(padrao: re.Pattern, texto: str, tier: int) -> tuple[list[str], float]:
    evs: list[str] = []
    score = 0.0
    for m in padrao.finditer(texto):
        tok = " ".join(m.group(0).upper().split())
        if tok not in evs:
            evs.append(tok)
            score += _SCORE[tier]
    return evs, score


def rotular(
    texto: str,
    metodo_texto: str = "pdf_text",
    nome_arquivo: str = "",
    nome_pasta: str = "",
) -> ResultadoRotulagem:
    if metodo_texto == "pdf_nao_localizado":
        return ResultadoRotulagem(None, None, 0.0, [], "pdf_nao_localizado")

    if metodo_texto in ("sem_texto", "texto_insuficiente", "sem_pdfplumber", "erro_pdf", "erro_leitura"):
        return ResultadoRotulagem(None, None, 0.0, [], "texto_insuficiente")

    evidencias_mt: list[str] = []
    evidencias_bt: list[str] = []
    score_mt = 0.0
    score_bt = 0.0

    # Tier 1 — Determinante
    evs, sc = _coletar(_MT_T1, texto, 1)
    evidencias_mt.extend(evs); score_mt += sc

    evs, sc = _coletar(_BT_T1, texto, 1)
    evidencias_bt.extend(evs); score_bt += sc

    # Tier 2 — Forte
    evs, sc = _coletar(_MT_T2, texto, 2)
    evidencias_mt.extend(evs); score_mt += sc

    evs, sc = _coletar(_BT_T2, texto, 2)
    evidencias_bt.extend(evs); score_bt += sc

    # Tier 3 — Complementar BT (MT tier 3 é raro — T1/T2 já cobrem)
    evs, sc = _coletar(_BT_T3, texto, 3)
    evidencias_bt.extend(evs); score_bt += sc

    # Tier 4 — Secundária (pasta/arquivo)
    txt_sec = _normaliza(f"{nome_pasta} {nome_arquivo}")
    if re.search(r"\bMT\b|\bGRUPO.?A\b|ALTA.?TENSAO|MEDIA.?TENSAO", txt_sec):
        evidencias_mt.append(f"[arquivo/pasta: {nome_arquivo or nome_pasta}]")
        score_mt += _SCORE[4]
    if re.search(r"\bBT\b|\bGRUPO.?B\b|BAIXA.?TENSAO", txt_sec):
        evidencias_bt.append(f"[arquivo/pasta: {nome_arquivo or nome_pasta}]")
        score_bt += _SCORE[4]

    # Subgrupo específico para o campo de retorno
    subgrupo_mt: str | None = None
    m = _SUBGRUPO_MT_RE.search(texto)
    if m:
        subgrupo_mt = m.group(1).upper()

    subgrupo_bt: str | None = None
    m = _SUBGRUPO_BT_RE.search(texto)
    if m:
        subgrupo_bt = f"B{m.group(1).upper()}"

    # --- Decisão ---
    penalidades: list[str] = []

    # Conflito real: evidências de AMBOS os lados com tier 1 ou 2
    mt_estrutural = score_mt >= _SCORE[2]
    bt_estrutural = score_bt >= _SCORE[2]

    if mt_estrutural and bt_estrutural:
        pen = f"conflito: MT({score_mt:.2f}) vs BT({score_bt:.2f})"
        penalidades.append(pen)
        if score_mt >= score_bt:
            score_mt -= 0.30
        else:
            score_bt -= 0.30

    if score_mt <= 0 and score_bt <= 0:
        return ResultadoRotulagem(None, None, 0.0, [], "grupo_desconhecido", penalidades)

    if score_mt > score_bt:
        grupo = "MT"
        subgrupo = subgrupo_mt
        confianca = min(1.0, score_mt)
        evidencias = evidencias_mt
        if penalidades:
            status = "conflito_de_evidencias" if confianca < 0.60 else "rotulado"
        elif evidencias_bt:
            status = "rotulado_com_baixa_confianca" if confianca < 0.50 else "rotulado"
        else:
            status = "rotulado_com_baixa_confianca" if confianca < 0.40 else "rotulado"
    elif score_bt > score_mt:
        grupo = "BT"
        subgrupo = subgrupo_bt
        confianca = min(1.0, score_bt)
        evidencias = evidencias_bt
        if penalidades:
            status = "conflito_de_evidencias" if confianca < 0.60 else "rotulado"
        elif evidencias_mt:
            status = "rotulado_com_baixa_confianca" if confianca < 0.50 else "rotulado"
        else:
            status = "rotulado_com_baixa_confianca" if confianca < 0.40 else "rotulado"
    else:
        grupo = None
        subgrupo = None
        confianca = 0.0
        evidencias = evidencias_mt + evidencias_bt
        status = "conflito_de_evidencias"

    return ResultadoRotulagem(
        grupo=grupo,
        subgrupo=subgrupo,
        confianca=round(confianca, 4),
        evidencias=evidencias,
        status=status,
        penalidades=penalidades,
    )
