#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ocr_equatorial_go.py
--------------------
OCR de faturas Equatorial Goiás (BT e MT).

Reutiliza os extratores de ocr_enel.py via importlib (mesmo layout de fatura,
herança ENEL GO). Apenas caminhos e concCod diferem.

Estrutura esperada em DOWNLOAD EQUATORIAL:
    03-2026 / BT / BB_2000001.pdf
    03-2026 / MT / BB_2000006.pdf

Saída em OCR EQUATORIAL GO:
    ocr_equatorial_go_BT_032026.xlsx
    ocr_equatorial_go_MT_032026.xlsx

Uso:
    python ocr_equatorial_go.py                      # mês atual, BT+MT
    python ocr_equatorial_go.py --mes 03 --ano 2026
    python ocr_equatorial_go.py --pasta 03-2026
    python ocr_equatorial_go.py --tipo bt
    python ocr_equatorial_go.py --todos
    python ocr_equatorial_go.py --recriar
"""

from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import logging
import os
import re
import sys
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# =============================================================================
# IMPORTAR EXTRATORES DO ocr_enel (mesmo layout de fatura)
# Usa importlib para ser robusto independente de sys.path / cwd
# =============================================================================

_ENEL_PATH = Path(__file__).resolve().parent / "ocr_enel.py"
_spec = importlib.util.spec_from_file_location("ocr_enel", str(_ENEL_PATH))
_ocr_enel = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_ocr_enel)

extrair_bt          = _ocr_enel.extrair_bt
extrair_mt          = _ocr_enel.extrair_mt
salvar_excel        = _ocr_enel.salvar_excel
HEADERS_REF         = _ocr_enel.HEADERS_REF
_carimbo_do_nome    = _ocr_enel._carimbo_do_nome
_detectar_subtarifa_mt = _ocr_enel._detectar_subtarifa_mt
_extract_text       = _ocr_enel._extract_text
_get_codigo_barras_enel = _ocr_enel._get_codigo_barras_enel


# =============================================================================
# CONFIGURAÇÃO
# =============================================================================

PASTA_DOWNLOAD = Path(os.environ.get(
    "OCR_EQUATORIAL_DOWNLOAD_DIR",
    "//10.10.250.21/Energia/ARQUIVOS ENZO/DOWNLOAD EQUATORIAL",
))
PASTA_SAIDA = Path(os.environ.get(
    "OCR_EQUATORIAL_SAIDA_DIR",
    "//10.10.250.21/Energia/ARQUIVOS ENZO/OCR EQUATORIAL GO",
))
MAX_WORKERS = 4

# Código da Equatorial Goiás no sistema Consen.
# TODO: confirmar o código correto — verificar no Consen em
#       Cadastros > Concessionárias > Equatorial Goiás
CONC_COD_EQUATORIAL_GO: str = os.environ.get("EQUATORIAL_GO_CONC_COD", "EQUATORIAL")

NOMES_BT = {"bt", "b3", "baixa tensao", "baixa_tensao"}
NOMES_MT = {"mt", "a4", "media tensao", "media_tensao", "mt_a4"}

# Importar helpers do ocr_enel
_br2f    = _ocr_enel._br2f
RE_MONEY = _ocr_enel.RE_MONEY
import re as _re

# Padrões compilados uma única vez (evita recompilação dentro de loops/funções)
_PAT_KW_MT  = _re.compile(r"KW\s+([\d.]+(?:,\d+)?)\s+([\d.]+,\d+)\s+([\d.]+,\d+)",  _re.IGNORECASE)
_PAT_KWH_MT = _re.compile(r"KWH?\s+([\d.]+(?:,\d+)?)\s+([\d.]+,\d+)\s+([\d.]+,\d+)", _re.IGNORECASE)
_PAT_ESC_EQ = _re.compile(
    r"(?:KWH\s+)?([\d.]+(?:,\d+)?)\s+[\d.]+,\d{3,}\s+([\d.]+,\d{2})",
    _re.IGNORECASE,
)

# Palavras-chave para detectar linhas de energia FP, Cap e P no layout Equatorial GO MT.
# Linhas com sufixo "- TE" representam apenas a parcela TE (R$ somente, kWh já contado na
# linha TUSD correspondente). Linhas "PARCELA TE *" têm a mesma semântica.
_FP_ENERGY_KEYS = frozenset({
    "PARCELA TUSD FP",            # ENEL / backward compat
    "CONSUMO FP",                  # layout simples (não-GD)
    "CONSUMO NAO COMPENSADO FP",   # layout GD — porção não compensada
    "ENERGIA ATIVA FORNECIDA FP",  # layout TUSD+TE split — linha TUSD
})
# Hora Reservada (capacitivo) — campo separado de indutivo FP
_CAP_ENERGY_KEYS = frozenset({
    "CONSUMO HR",
    "ENERGIA ATIVA FORNECIDA HR",  # layout TUSD+TE split — linha TUSD para HR
})
# Parcela TE: mesmo kWh da linha TUSD, acumula só o R$ extra de TE
_TE_FP_KEYS  = frozenset({"PARCELA TE FP"})
_TE_CAP_KEYS = frozenset({"PARCELA TE HR"})
_TE_PTA_KEYS = frozenset({"PARCELA TE P"})
_P_ENERGY_KEYS = frozenset({
    "PARCELA TUSD P",
    "CONSUMO P",
    "CONSUMO NAO COMPENSADO P",
    "ENERGIA ATIVA FORNECIDA P",
})


# =============================================================================
# LOGGING
# =============================================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)


def _eq_ascii_upper(text: str) -> str:
    return unicodedata.normalize("NFKD", text or "").encode("ASCII", "ignore").decode("ASCII").upper()


def _eq_normalizar_instalacao(valor: str | None) -> str:
    dig = re.sub(r"\D", "", str(valor or ""))
    return dig if len(dig) >= 8 else ""


def _eq_mes_ref_texto(text: str) -> str:
    linhas = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    meses = {
        "JAN": "01", "JANEIRO": "01",
        "FEV": "02", "FEVEREIRO": "02",
        "MAR": "03", "MARCO": "03", "MARCOO": "03", "MARCO ": "03",
        "ABR": "04", "ABRIL": "04",
        "MAI": "05", "MAIO": "05",
        "JUN": "06", "JUNHO": "06",
        "JUL": "07", "JULHO": "07",
        "AGO": "08", "AGOSTO": "08",
        "SET": "09", "SETEMBRO": "09",
        "OUT": "10", "OUTUBRO": "10",
        "NOV": "11", "NOVEMBRO": "11",
        "DEZ": "12", "DEZEMBRO": "12",
    }

    def _buscar_ref(linha: str) -> str:
        upper = _eq_ascii_upper(linha)
        m = re.search(r"\b(" + "|".join(sorted(meses, key=len, reverse=True)) + r")[/\-. ](20\d{2})\b", upper)
        if m:
            return f"{meses[m.group(1)]}-{m.group(2)}"
        m = re.search(r"(?<!/)\b(0[1-9]|1[0-2])[/-](20\d{2})\b", upper)
        if m:
            return f"{m.group(1)}-{m.group(2)}"
        return ""

    for linha in linhas:
        ref = _buscar_ref(linha)
        if ref and ("R$" in linha.upper() or re.search(r"\d{2}/\d{2}/20\d{2}", linha)):
            return ref

    for linha in linhas:
        ref = _buscar_ref(linha)
        if ref:
            return ref

    return ""


def _resolver_ref_equatorial_go(text: str) -> dt.date | None:
    ref = _eq_mes_ref_texto(text)
    if not ref:
        return None
    mm, yyyy = ref.split("-", 1)
    try:
        return dt.date(int(yyyy), int(mm), 1)
    except ValueError:
        return None


def _extrair_nota_fiscal_equatorial_go(text: str) -> str:
    upper = _eq_ascii_upper(text)
    match = re.search(r"NOTA\s+FISCAL\s+N[Oº°]*\s*(\d{6,20})", upper)
    if match:
        return match.group(1).strip()
    return ""


def _extrair_codigo_cliente_equatorial_go(text: str) -> str:
    linhas = [ln.strip() for ln in text.splitlines() if ln.strip()]

    for idx, ln in enumerate(linhas):
        digits = re.sub(r"\D", "", ln)
        if not re.fullmatch(r"\d{7,10}", digits):
            continue
        if idx > 0 and re.search(
            r"\b(?:JAN|FEV|MAR|ABR|MAI|JUN|JUL|AGO|SET|OUT|NOV|DEZ)/20\d{2}\b",
            _eq_ascii_upper(linhas[idx - 1]),
        ):
            return digits

    match = re.search(
        r"\b(?:JAN|FEV|MAR|ABR|MAI|JUN|JUL|AGO|SET|OUT|NOV|DEZ)/20\d{2}\b\s+(\d{7,10})",
        _eq_ascii_upper(text),
    )
    if match:
        return match.group(1).strip()

    return ""


def _extrair_codigo_barras_equatorial_go(text: str) -> str:
    linhas = text.splitlines()

    # P0: linha digitável padrão AAAAA.BBBBB CCCCC.DDDDDD EEEEE.EEEEEE F GGGGGGGGGGGGGG
    # Funciona mesmo quando há texto extra na linha (ex: "BANCO DO BRASIL 001-9 ...")
    for ln in linhas:
        m = re.search(
            r"(\d{5}\.\d{5})\s+(\d{5}\.\d{6})\s+(\d{5}\.\d{6})\s+(\d)\s+(\d{14})",
            ln,
        )
        if m:
            return re.sub(r"\D", "", "".join(m.groups()))

    # P1: linha com dashes — formato concessionária (47-48 dígitos)
    for ln in linhas:
        if "-" not in ln:
            continue
        digits = re.sub(r"\D", "", ln)
        if len(digits) in (47, 48):
            return digits

    # P2: boleto com pontos (AAAAA.BBBBB CCCCC.DDDDD ...) — 47-48 dígitos
    # Garante que a linha é quase toda dígitos/separadores (≤3 chars estranhos)
    for ln in linhas:
        stripped = ln.strip()
        if "." not in stripped:
            continue
        digits = re.sub(r"\D", "", stripped)
        if len(digits) in (47, 48):
            if len(re.sub(r"[\d.\- ]", "", stripped)) <= 3:
                return digits

    # P3: âncora por label — "LINHA DIGITAVEL" ou "CODIGO DE BARRAS"
    upper_lines = [_eq_ascii_upper(ln) for ln in linhas]
    for i, upper in enumerate(upper_lines):
        if "LINHA DIGIT" in upper or ("CODIGO" in upper and "BARRAS" in upper):
            for candidate in (linhas[i], linhas[i + 1] if i + 1 < len(linhas) else ""):
                d = re.sub(r"\D", "", candidate)
                if len(d) in (44, 47, 48):
                    return d

    # P4: código de barras raw — 44 dígitos contíguos (sem separadores)
    m = re.search(r"(?<!\d)(\d{44})(?!\d)", text)
    if m:
        return m.group(1)

    # Fallback legado
    legado = _get_codigo_barras_enel(text)
    if legado:
        digits = re.sub(r"\D", "", str(legado))
        if len(digits) >= 44:
            return digits

    return ""


def _extrair_valor_nota_fiscal_equatorial_go(text: str) -> float:
    upper = _eq_ascii_upper(text)

    # T1: linha explícita "TOTAL DA NOTA FISCAL" ou "VALOR TOTAL DA NOTA"
    m = re.search(r"(?:VALOR\s+)?TOTAL\s+(?:DA\s+)?NOTA\s+FISCAL[:\s]+([\d.,]+)", upper)
    if m:
        return round(abs(_br2f(m.group(1))), 2)

    # T2: base PIS(/PASEP) = total da nota fiscal.
    # GO padrão: "PIS/PASEP 7404,17 0,949% 70,27" — primeiro número = base = NF total.
    # Amapá/Piauí: "PIS 2.979,46 1,6416 48,91" (sem /PASEP, sem %); base = primeiro número.
    # BT GO puro pode ter só a alíquota ("PIS/PASEP 0,39%") — ignorar valores < 10.
    for ln in text.splitlines():
        u = _eq_ascii_upper(ln)
        if "PIS/PASEP" in u or "PIS PASEP" in u:
            nums = _re.findall(r"[\d.]+,\d{2}", ln)
            if nums:
                val = _br2f(nums[0])
                if val >= 10.0:
                    return round(abs(val), 2)
        # Amapá/Piauí: linha com "PIS base aliq valor" sem /PASEP
        m_pis = _re.search(r"\bPIS\s+([\d.]+,\d{2})\s+[\d.,]+\s+[\d.,]+", ln)
        if m_pis:
            val = _br2f(m_pis.group(1))
            if val >= 10.0:
                return round(abs(val), 2)

    # T3: linha TOTAL com 4 colunas — col1 é o valor da fatura (fallback)
    m = re.search(r"\bTOTAL\s+([\d.,]+)\s+([\d.,]+)\s+([\d.,]+)\s+([\d.,]+)", upper)
    if m:
        return round(abs(_br2f(m.group(1))), 2)

    return 0.0


def _detectar_tipo_equatorial_go(text: str) -> str:
    upper = _eq_ascii_upper(text)
    if (
        re.search(r"CLASSIFICACAO\s*:\s*A\b", upper)
        or re.search(r"\b(A[1-4])\s*[-–]", upper)
        or " A4 " in f" {upper} "
        or "THS_VERDE" in upper
        or "THS_AZUL" in upper
    ):
        return "mt"
    if re.search(r"CLASSIFICACAO\s*:\s*B\b", upper) or " B3 " in f" {upper} " or "CONVENCIONAL" in upper or "BRANCA" in upper:
        return "bt"

    m = re.search(r"TENSAO\s+NOMINAL\s+DISP\s*:\s*([\d.,]+)\s*V", upper)
    if m:
        try:
            tensao = float(m.group(1).replace(".", "").replace(",", "."))
            return "mt" if tensao >= 1000 else "bt"
        except ValueError:
            pass

    return "bt"


def _resolver_instalacao_equatorial_go(pdf_path: str | Path, text: str, tipo: str | None = None) -> str:
    filename = Path(pdf_path).name
    tipo = (tipo or _detectar_tipo_equatorial_go(text)).lower()

    # Prioridade 1: UC NOVA no filename (mais confiável pois vem do operador)
    for pat_fn in [
        r"UC\s*NOVA\s*[-:]?\s*([0-9.\-]{6,})",
        r"UC\s*ANTIGA\s*[-:]?\s*([0-9.\-]+)",
    ]:
        m = re.search(pat_fn, _eq_ascii_upper(filename))
        if m:
            inst = _eq_normalizar_instalacao(m.group(1))
            if inst:
                return inst

    candidatos = []
    upper = _eq_ascii_upper(text)

    # Prioriza UC nova/padronizada quando presente no corpo da fatura.
    for padrao in [
        r"PERDAS\s+DE\s+TRANSFORMACAO\s*/\s*RAMAL\s*:\s*[0-9.,% ]+\s+([0-9.\-]{8,})",
        r"\bUC\s+NOVA\s+([0-9.\-]{8,})\b",
        # Amapá: "FATOR DE POTÊNCIA: 236.481.007-78"
        r"FATOR\s+DE\s+POTENCIA\s*:\s*([0-9.\-]{8,})",
        r"(?m)^\s*([0-9]{1,3}(?:\.[0-9]{3}){1,3}-[0-9]{2})\s*$",
    ]:
        m = re.search(padrao, upper)
        if m:
            inst = _eq_normalizar_instalacao(m.group(1))
            if inst:
                return inst

    if tipo == "mt":
        candidatos.extend([
            _ocr_enel._get_instalacao_mt(text),
            _ocr_enel._get_instalacao_bt(text),
        ])
    else:
        candidatos.extend([
            _ocr_enel._get_instalacao_bt(text),
            _ocr_enel._get_instalacao_mt(text),
        ])

    for cand in candidatos:
        inst = _eq_normalizar_instalacao(cand)
        if inst:
            return inst

    return ""


def _eh_layout_equatorial_go(text: str, filename: str = "") -> bool:
    upper = _eq_ascii_upper(text)
    score = 0
    if "EQUATORIAL" in upper:
        score += 2
    if "PERDAS DE TRANSFORMACAO / RAMAL" in upper:
        score += 1
    if "PROTOCOLO DE AUTORIZACAO" in upper:
        score += 1
    if "CFOP 5258" in upper:
        score += 1
    if "CLASSIFICACAO:" in upper:
        score += 1
    if "UC ANTIGA" in _eq_ascii_upper(filename) and "UC NOVA" in _eq_ascii_upper(filename):
        score += 1
    return score >= 3


# =============================================================================
# EXTRATOR DE CAMPOS ESPECÍFICOS DA EQUATORIAL GO (MT)
# =============================================================================

def _resolver_obs_equatorial_go_mt(text: str) -> list:
    """
    Produz obs específicas para MT Equatorial GO:

      obs 97 — Dif Fatur Tusd / Encargo - Homolog CCEE
        • (97, sum_fp)  → soma de todas as linhas DIF (TUSD|DESC) CCEE com "FP"
        • (97, val_p)   → linha DIF (TUSD|DESC) CCEE "P" (ponta) — entrada separada

    O valor pode estar na PRÓPRIA linha ou na linha SEGUINTE (layout Equatorial GO).

    TODO: UFER FP (Fora Ponta Reativo Excedente) — confirmar código obs no Consen.
    """
    val_fp = 0.0
    val_p  = 0.0

    linhas = [ln.strip() for ln in text.splitlines() if ln.strip()]

    for i, ln in enumerate(linhas):
        upper = _eq_ascii_upper(ln)
        if not ("DIF" in upper and "CCEE" in upper):
            continue
        # valor pode estar na própria linha ou na seguinte
        monies = RE_MONEY.findall(ln)
        if not monies and i + 1 < len(linhas):
            monies = RE_MONEY.findall(linhas[i + 1])
        if not monies:
            continue
        valor = abs(_br2f(monies[-1]))
        # Ponta pura: linha tem "KWH P" ou "KW P" sem "FP"
        if _re.search(r'\bKW[H]?\s+P\b', upper) and "FP" not in upper:
            val_p += valor
        else:
            val_fp += valor

    obs: list = []
    if abs(val_fp) > 0.005:
        obs.append(("97", round(val_fp, 2)))
    if abs(val_p) > 0.005:
        obs.append(("97", round(val_p, 2)))
    return obs


def _extras_equatorial_go_mt(text: str) -> dict:
    """
    Extrai campos presentes nas faturas MT Equatorial GO que o extrator
    ENEL base não captura:

    - fatBeneficioTarifarioBrutoValorReais  → "BENEFÍCIO TARIFÁRIO BRUTO"  (inline ou até 3 linhas seguintes)
    - fatBeneficioLiquidoValorReais         → "BENEFÍCIO TARIFÁRIO LÍQUIDO" (inline ou até 3 linhas seguintes)
    - fatEscassezHidrica                    → kWh de escassez hídrica (prox linha, índice 0)
    - fatEscassezHidricaValorReais          → R$ escassez hídrica (prox linha, índice 1; ou mesmo inline)
    - fatDescontoFio                        → DIF CCEE / DESCONTO FIO B na demanda (kW)
    - fatDescontoFioKWh                     → DIF CCEE / DESCONTO FIO B na energia (kWh)
    - _obs_equatorial_mt                    → [(97, fp), (97, p)] separados por posto

    Notas:
      - Equatorial GO pode ter "BENEFÍCIO TARIFÁRIO" numa linha e "BRUTO"/"LÍQUIDO" nas linhas seguintes
      - RE_MONEY só captura valores com 2 casas decimais (e.g. 1.234,56) — tarifas unitárias ficam de fora
    """
    out = {
        "fatBeneficioTarifarioBrutoValorReais": 0.0,
        "fatBeneficioLiquidoValorReais":        0.0,
        "fatEscassezHidrica":                   0.0,
        "fatEscassezHidricaValorReais":         0.0,
        "fatDescontoFio":                       0.0,
        "fatDescontoFioKWh":                    0.0,
        "_obs_equatorial_mt":                   [],
    }

    linhas = [ln.strip() for ln in text.splitlines() if ln.strip()]

    _em_beneficio = False   # state machine: encontrou cabeçalho BENEFÍCIO sem BRUTO/LÍQUIDO ainda

    for i, ln in enumerate(linhas):
        upper       = _eq_ascii_upper(ln)
        monies      = RE_MONEY.findall(ln)
        prox        = linhas[i + 1] if i + 1 < len(linhas) else ""
        prox_monies = RE_MONEY.findall(prox)

        # ── Benefício tarifário ───────────────────────────────────────────────
        # Layout inline:   "BENEFÍCIO TARIFÁRIO BRUTO  1.234,56"
        # Layout multi-linha:
        #   "BENEFÍCIO TARIFÁRIO:"
        #   "Bruto (R$):  1.234,56"
        #   "Líquido (R$): -567,89"
        if "BENEF" in upper and "TARIF" in upper:
            _em_beneficio = True
            # Pode já ter BRUTO ou LÍQUIDO na mesma linha
            if "BRUTO" in upper:
                fonte = monies or prox_monies
                if fonte:
                    out["fatBeneficioTarifarioBrutoValorReais"] = abs(_br2f(fonte[0]))
                    _em_beneficio = False
            elif "LIQUID" in upper:
                fonte = monies or prox_monies
                if fonte:
                    out["fatBeneficioLiquidoValorReais"] = _br2f(fonte[-1])
                    _em_beneficio = False

        elif _em_beneficio and "BRUTO" in upper:
            fonte = monies or prox_monies
            if fonte:
                out["fatBeneficioTarifarioBrutoValorReais"] = abs(_br2f(fonte[0]))

        elif _em_beneficio and "LIQUID" in upper:
            fonte = monies or prox_monies
            if fonte:
                out["fatBeneficioLiquidoValorReais"] = _br2f(fonte[-1])
            _em_beneficio = False   # assume que líquido vem após bruto

        # ── Escassez Hídrica ─────────────────────────────────────────────────
        # Linha header: "ESCASSEZ HIDRICA FP" ou "ESCASSEZ HIDRICA P"
        # Linha dados (próxima ou mesma): "KWH  1.000,00  0,05987  59,87  ..."
        # RE_MONEY capturaria "0,05" de "0,05987" (tarifa unitária), deslocando os índices.
        # Padrão específico: qty(2dp)  tariff(3+dp)  valor(2dp)
        elif "ESCASSEZ" in upper and "HIDRIC" in upper:
            for fonte_ln in (prox, ln):
                m_esc = _PAT_ESC_EQ.search(fonte_ln)
                if m_esc:
                    out["fatEscassezHidrica"] += abs(_br2f(m_esc.group(1)))
                    out["fatEscassezHidricaValorReais"] += abs(_br2f(m_esc.group(2)))
                    break
            else:
                # Fallback: apenas valores com 2dp (sem tarifa unitária na linha)
                fm = prox_monies or monies
                if fm:
                    out["fatEscassezHidrica"] += abs(_br2f(fm[0]))
                    if len(fm) >= 2:
                        out["fatEscassezHidricaValorReais"] += abs(_br2f(fm[1]))

        # ── Desconto Fio B / DIF CCEE ─────────────────────────────────────────
        # kW (demanda) → fatDescontoFio
        # kWh (energia) → fatDescontoFioKWh
        # Equatorial GO PCH50: 50% kW, 45,48% kWh
        elif "DIF" in upper and "CCEE" in upper:
            fonte = monies or prox_monies
            if fonte:
                val = abs(_br2f(fonte[-1]))
                if "KWH" in upper or ("KWH" in _eq_ascii_upper(prox) and "KW" not in upper):
                    out["fatDescontoFioKWh"] += val
                else:
                    out["fatDescontoFio"] += val

        elif ("DESCONTO" in upper or "DESC" in upper) and "FIO" in upper:
            fonte = monies or prox_monies
            if fonte:
                val = abs(_br2f(fonte[-1]))
                if "KWH" in upper:
                    out["fatDescontoFioKWh"] += val
                else:
                    out["fatDescontoFio"] += val

    out["_obs_equatorial_mt"] = _resolver_obs_equatorial_go_mt(text)

    for k, v in out.items():
        if isinstance(v, float):
            out[k] = round(v, 2)

    return out


def _extras_equatorial_go_bt(text: str) -> dict:
    """
    Extrai campos GD/SCEE de faturas BT Equatorial GO:

    Consumo:
    - "Consumo não compensado" + "Consumo SCEE"  → somados em:
      fatConFPontaIndRegistrado / fatConFPontaIndFaturado

    SCEE (bloco "INFORMAÇÕES DO SCEE" ou tabela "Mensagens Importantes"):
    - "CRÉDITO RECEBIDO KWH XX"     → fatConPontaInjetadoFaturado (energia injetada)
    - "SALDO KWH: XX"               → fatConPontaInjetadoUsinaSaldoAcumulado

    Retenções LEI 9430 são tratadas em _retencoes_equatorial_go().

    Nota: requer "NAO" explícito para distinguir "Consumo não compensado" de
    "Consumo Compensado" (layouts Piauí GD).
    """
    out = {
        # Consumo (soma não-compensado + SCEE)
        "_kwh_nao_compensado": 0,
        "_kwh_scee":           0,
        # SCEE / GD
        "fatConFPontaInjetadoRegistrado":           0.0,
        "fatConFPontaInjetadoFaturado":             0.0,
        "fatConFPontaInjetadoValorReais":           0.0,
        "fatConFPontaInjetadoUsina":                0.0,
        "fatConFPontaInjetadoUsinaSaldoAcumulado":  0.0,
        "fatTributoFederalPerc":                    0.0,
        "fatTributoFederalVal":                     0.0,
    }

    linhas = [ln.strip() for ln in text.splitlines() if ln.strip()]

    for i, ln in enumerate(linhas):
        upper  = _eq_ascii_upper(ln)
        monies = RE_MONEY.findall(ln)
        prox = linhas[i + 1] if i + 1 < len(linhas) else ""

        # ── Consumos que se somam ─────────────────────────────────────────────
        if "CONSUMO" in upper and "NAO" in upper and "COMPENSAD" in upper:
            # "Consumo não compensado  XX KWH"
            m = _re.search(r"([\d.]+,\d+)\s*KWH", ln, _re.IGNORECASE)
            if m:
                out["_kwh_nao_compensado"] = int(_br2f(m.group(1)))
            elif monies:
                out["_kwh_nao_compensado"] = int(abs(_br2f(monies[0])))

        elif "CONSUMO" in upper and "SCEE" in upper:
            m = _re.search(r"([\d.]+,\d+)\s*KWH", ln, _re.IGNORECASE)
            if m:
                out["_kwh_scee"] = int(_br2f(m.group(1)))
            elif monies:
                out["_kwh_scee"] = int(abs(_br2f(monies[0])))

        # ── SCEE — crédito recebido (energia injetada) ────────────────────────
        elif "CREDITO RECEBIDO" in upper and "KWH" in upper:
            # "CRÉDITO RECEBIDO KWH 3.841,00"
            m_credito = _re.search(r"CREDITO\s+RECEBIDO\s+KWH[: ]+([\d.]+,\d+)", upper)
            if m_credito:
                val = abs(_br2f(m_credito.group(1)))
                out["fatConFPontaInjetadoFaturado"] = val
                out["fatConFPontaInjetadoRegistrado"] = val
                out["fatConFPontaInjetadoUsina"] = val
            m_saldo = _re.search(r"SALDO\s+KWH[: ]+([\d.]+,\d+)", upper)
            if m_saldo:
                out["fatConFPontaInjetadoUsinaSaldoAcumulado"] = abs(_br2f(m_saldo.group(1)))

        # ── SCEE — saldo acumulado ────────────────────────────────────────────
        elif "INJECAO SCEE" in upper and prox.upper().startswith("KWH"):
            m_injecao = _re.search(r"KWH\s+([\d.]+,\d+)\s+[\d.]+,\d+\s+(-?[\d.]+,\d+)", prox, _re.IGNORECASE)
            if m_injecao:
                kwh = abs(_br2f(m_injecao.group(1)))
                out["fatConFPontaInjetadoRegistrado"] = kwh
                out["fatConFPontaInjetadoFaturado"] = kwh
                out["fatConFPontaInjetadoUsina"] = kwh
                out["fatConFPontaInjetadoValorReais"] = _br2f(m_injecao.group(2))

        elif "SALDO KWH" in upper and "EXPIRAR" not in upper:
            m_saldo = _re.search(r"SALDO\s+KWH[: ]+([\d.]+,\d+)", upper)
            if m_saldo:
                out["fatConFPontaInjetadoUsinaSaldoAcumulado"] = abs(_br2f(m_saldo.group(1)))

    # Consumo total = não compensado + SCEE
    consumo_total = out.pop("_kwh_nao_compensado") + out.pop("_kwh_scee")
    if consumo_total > 0:
        out["fatConFPontaIndRegistrado"] = consumo_total
        out["fatConFPontaIndFaturado"]   = consumo_total

    # Arredondamento dos floats
    for k, v in out.items():
        if isinstance(v, float):
            out[k] = round(v, 2)

    return out


def _fisco_equatorial_go(text: str) -> dict:
    """
    Extrai ICMS/PIS/COFINS do layout Equatorial GO.

    O bloco costuma aparecer tanto em linhas dedicadas:
      PIS/PASEP 91,2 0,3918% 0,36
      ICMS 112,59 19% 21,39
      COFINS 91,2 1,822% 1,66

    quanto embutido no fim de outras linhas (principalmente MT).
    """
    out = {
        "fatICMS": 0.0,
        "fatDesIcmsAliquota": 0.0,
        "fatPIS": 0.0,
        "fatDescPisAliquota": 0.0,
        "fatCOFINS": 0.0,
        "fatDesCofinsAliquota": 0.0,
    }

    padroes = [
        # Aceita "PIS/PASEP" (GO padrão) e "PIS" sem /PASEP (Amapá/Piauí); % opcional
        (r"PIS(?:\s*/\s*PASEP)?\s+([\d.,]+)\s+([\d.,]+)\s*%?\s+([\d.,]+)", "fatPIS", "fatDescPisAliquota"),
        (r"(?:FORNECIMENTO\s+)?ICMS\s+([\d.,]+)\s+([\d.,]+)\s*%\s+([\d.,]+)", "fatICMS", "fatDesIcmsAliquota"),
        (r"COFINS\s+([\d.,]+)\s+([\d.,]+)\s*%?\s+([\d.,]+)", "fatCOFINS", "fatDesCofinsAliquota"),
    ]

    for ln in text.splitlines():
        upper = _eq_ascii_upper(ln)
        for padrao, campo_valor, campo_aliquota in padroes:
            m = _re.search(padrao, upper)
            if not m:
                continue
            valor = round(_br2f(m.group(3)), 4)
            aliquota = round(_br2f(m.group(2)), 4)
            if abs(valor) >= abs(out[campo_valor]):
                out[campo_valor] = valor
                out[campo_aliquota] = aliquota

    for k, v in out.items():
        if isinstance(v, float):
            out[k] = round(v, 4 if "Aliquota" in k else 2)
    return out


def _retencoes_equatorial_go(text: str) -> dict:
    """
    Extrai retenções da Lei 9430 mesmo quando o rótulo está em uma linha e o
    valor negativo vem na linha seguinte.
    """
    out = {
        "fatDescPisPercRetImposto": 0.0,
        "fatDescPisValRetImposto": 0.0,
        "fatDescCofinsPercRetImposto": 0.0,
        "fatDescCofinsValRetImposto": 0.0,
        "fatDescCsllPercRetImposto": 0.0,
        "fatDescCsllValRetImposto": 0.0,
        "fatDescIrpjPercRetImposto": 0.0,
        "fatDescIrpjValRetImposto": 0.0,
    }

    linhas = [ln.strip() for ln in text.splitlines() if ln.strip()]

    def _valor_retencao(idx: int) -> float:
        for pos in (idx, idx + 1):
            if pos >= len(linhas):
                continue
            linha = linhas[pos]
            negativos = _re.findall(r"-\s*[\d.]+,\d+", linha)
            if negativos:
                return round(_br2f(negativos[-1]), 2)
            if pos > idx:
                monies = RE_MONEY.findall(linha)
                if len(monies) == 1:
                    return round(-abs(_br2f(monies[0])), 2)
        return 0.0

    for i, ln in enumerate(linhas):
        upper = _eq_ascii_upper(ln)
        # Aceita tanto "Lei 9.430" quanto "Tributo a Reter" (layout Amapá/Celg)
        if "9430" not in upper and "TRIBUTO A RETER" not in upper and "TRIB A RETER" not in upper:
            continue

        m_aliq = _re.search(r"(\d+[.,]\d+)\s*%", ln)
        aliq = _br2f(m_aliq.group(1)) if m_aliq else 0.0
        valor = _valor_retencao(i)

        if "COFINS" in upper:
            if aliq:
                out["fatDescCofinsPercRetImposto"] = round(aliq, 4)
            if valor:
                out["fatDescCofinsValRetImposto"] = valor
        elif "CSLL" in upper:
            if aliq:
                out["fatDescCsllPercRetImposto"] = round(aliq, 4)
            if valor:
                out["fatDescCsllValRetImposto"] = valor
        elif "PIS" in upper or "PASEP" in upper:
            if aliq:
                out["fatDescPisPercRetImposto"] = round(aliq, 4)
            if valor:
                out["fatDescPisValRetImposto"] = valor
        elif _re.search(r"\bIR\b", upper):
            out["fatDescIrpjPercRetImposto"] = -1.0
            if valor:
                out["fatDescIrpjValRetImposto"] = valor

    return out


# =============================================================================
# PROCESSAMENTO  — injeta concCod correto após chamar extratores ENEL
# =============================================================================

def _core_equatorial_go_bt(text: str) -> dict:
    out = {
        "fatValorFatura": 0.0,
        "fatConFPontaIndRegistrado": 0.0,
        "fatConFPontaIndFaturado": 0.0,
        "fatConFPontaIndValorReais": 0.0,
        "fatIlumPublica": 0.0,
        "fatValBandeira": 0.0,
        "fatMultas": 0.0,
    }

    linhas = [ln.strip() for ln in text.splitlines() if ln.strip()]

    for i, ln in enumerate(linhas):
        upper = _eq_ascii_upper(ln)
        monies = RE_MONEY.findall(ln)
        prox = linhas[i + 1] if i + 1 < len(linhas) else ""
        prox_monies = RE_MONEY.findall(prox)

        if out["fatValorFatura"] == 0.0:
            m_total = _re.search(
                r"\b(?:JAN|FEV|MAR|ABR|MAI|JUN|JUL|AGO|SET|OUT|NOV|DEZ)[A-Z]*/20\d{2}\b.*?R\$\**\s*([\d.]+,\d+)",
                upper,
            )
            if m_total:
                out["fatValorFatura"] = _br2f(m_total.group(1))
        # Fallback Amapá/Piauí: linha "MM/YYYY DD/MM/YYYY R$ X.XXX,XX"
        if out["fatValorFatura"] == 0.0:
            m_total = _re.search(
                r"\b(?:0[1-9]|1[0-2])/20\d{2}\b.*?R\$\*{0,2}\s*([\d.]+,\d+)",
                upper,
            )
            if m_total:
                out["fatValorFatura"] = _br2f(m_total.group(1))

        # Layout BT simples em uma linha: "CONSUMO kWh kWh 1,126017 9277,00 187,99 10446,06"
        if "CONSUMO" in upper and "KWH" in upper and "COMPENSAD" not in upper and "SCEE" not in upper:
            m_linha_simples = _re.search(
                r"\bCONSUMO\b\s+KWH\s+KWH\s+[\d.]+,\d+\s+([\d.]+,\d+)\s+[\d.]+,\d+\s+(-?[\d.]+,\d+)",
                upper,
                _re.IGNORECASE,
            )
            if m_linha_simples:
                consumo = abs(_br2f(m_linha_simples.group(1)))
                valor = abs(_br2f(m_linha_simples.group(2)))
                if consumo > 0:
                    out["fatConFPontaIndRegistrado"] += consumo
                    out["fatConFPontaIndFaturado"] += consumo
                if valor > 0:
                    out["fatConFPontaIndValorReais"] += valor
            else:
                # Layout BT simples em duas linhas:
                #   CONSUMO kWh
                #   kWh 9277,00 1,126017 10.446,06 187,99 10446,06 ...
                m_duas_linhas = _re.search(
                    r"\bKWH\s+([\d.]+,\d+)\s+[\d.]+,\d+\s+([\d.]+,\d+)\s+[\d.]+,\d+\s+([\d.]+,\d+)",
                    prox,
                    _re.IGNORECASE,
                )
                if m_duas_linhas:
                    consumo = abs(_br2f(m_duas_linhas.group(1)))
                    valor = abs(_br2f(m_duas_linhas.group(3) or m_duas_linhas.group(2)))
                    if consumo > 0:
                        out["fatConFPontaIndRegistrado"] += consumo
                        out["fatConFPontaIndFaturado"] += consumo
                    if valor > 0:
                        out["fatConFPontaIndValorReais"] += valor
                else:
                    # Amapá/Piauí: "Consumo (kWh) 3.207 tarifa1 tarifa2 v1 v2 total"
                    # qty é o primeiro número; total (último) é o R$ de consumo.
                    m_ap = _re.search(
                        r"\bCONSUMO\s*\([^)]*\)\s+([\d.]+)\s+[\d.,]+\s+[\d.,]+\s+[\d.,]+\s+[\d.,]+\s+([\d.]+,\d+)",
                        upper,
                    )
                    if m_ap:
                        consumo = abs(_br2f(m_ap.group(1)))
                        valor = abs(_br2f(m_ap.group(2)))
                        if consumo > 0:
                            out["fatConFPontaIndRegistrado"] += consumo
                            out["fatConFPontaIndFaturado"] += consumo
                        if valor > 0:
                            out["fatConFPontaIndValorReais"] += valor

        # "Consumo Compensado (kWh)" sem "NAO" é layout Piauí GD — não é SCEE/não-compensado GO
        elif "CONSUMO" in upper and ("SCEE" in upper or ("COMPENSAD" in upper and "NAO" in upper)):
            m = _re.search(r"KWH\s+([\d.]+,\d+)", prox, _re.IGNORECASE)
            if m:
                out["fatConFPontaIndRegistrado"] += _br2f(m.group(1))
                out["fatConFPontaIndFaturado"] += _br2f(m.group(1))
            if len(prox_monies) >= 3:
                out["fatConFPontaIndValorReais"] += abs(_br2f(prox_monies[2]))

        elif (
            ("CONTRIB." in upper or "CONTRIBUICAO" in upper or "CIP" in upper or "COSIP" in upper)
            and ("ILUM" in upper or "COSIP" in upper)
            and ("PUBLIC" in upper or "MUNICIPAL" in upper or "MUNIC" in upper or "PREF" in upper or "CIP" in upper)
        ):
            fonte = monies or prox_monies
            if fonte:
                out["fatIlumPublica"] += _br2f(fonte[0])

        # Bandeira BT CELG/Equatorial: pode vir como "ADICIONAL BANDEIRA",
        # "AD. BAND." ou junto da descrição do item faturado.
        elif (
            ("BAND" in upper or "BANDEIRA" in upper)
            and ("ADIC" in upper or "AD." in upper or "ESCASSEZ" in upper or "VERMELHA" in upper or "AMARELA" in upper or "VERDE" in upper)
        ):
            fonte = monies or prox_monies
            if fonte:
                candidatos = [_br2f(v) for v in fonte if abs(_br2f(v)) > 0.01]
                if candidatos:
                    # Formato CELG: "Adicional Bandeira Amarela kWh qty rate R$_TE ... TUSD"
                    # R$_TE está sempre no índice 2; funciona tb para formato split (kWh na prox linha)
                    kwh_in_upper = "KWH" in upper
                    kwh_in_prox = not monies and "KWH" in prox.upper()
                    if (kwh_in_upper or kwh_in_prox) and len(candidatos) >= 3:
                        out["fatValBandeira"] += abs(candidatos[2])
                    else:
                        out["fatValBandeira"] += abs(candidatos[-1])

        # Multas/juros Amapá: "Multa 117,53" / "Juros 5,88" / "Correção Monetária 1,72"
        # sem sufixo "ATRASO" (diferente do layout MT GO)
        elif upper.startswith("MULTA") and monies:
            out["fatMultas"] += abs(_br2f(monies[-1]))
        elif upper.startswith("JUROS") and monies:
            out["fatMultas"] += abs(_br2f(monies[-1]))
        elif ("CORRECAO" in upper or "CORREÇÃO" in upper) and ("MONETAR" in upper or "IPCA" in upper) and monies:
            out["fatMultas"] += abs(_br2f(monies[-1]))

    for k, v in out.items():
        if isinstance(v, float):
            out[k] = round(v, 2)
    return out


def _demanda_registrada_linha(linha: str) -> float | None:
    # Formato: "XXXXXXXXX-X DEMANDA - KW FORA PONTA  022807  024267  0,028800  42,048"
    # Fator multiplicador pode ter 2–8 casas decimais; valor registrado após o fator.
    m = _re.search(
        r"\bDEMANDA\s*-\s*KW\b.*?\s+\d+\s+\d+\s+[\d.]+,\d{2,8}\s+([\d.]+,\d{1,4})(?:\s|$)",
        linha,
        _re.IGNORECASE,
    )
    if m:
        val = _br2f(m.group(1))
        return val if val > 0 else None

    # Fallback: mesma estrutura sem leituras anterior/atual (tabela simplificada)
    m = _re.search(
        r"\bDEMANDA\s*-\s*KW\b.*?\s+[\d.]+,\d{2,8}\s+([\d.]+,\d{1,4})(?:\s|$)",
        linha,
        _re.IGNORECASE,
    )
    if m:
        val = _br2f(m.group(1))
        return val if val > 0 else None

    return None


def _core_equatorial_go_mt(text: str) -> dict:
    """
    Extrai campos estruturais do faturamento MT Equatorial GO.

    Demanda:
      - fatDemContratadaFPonta / fatDemFPontaIndFaturada  → linhas "DEMANDA LIVRE" (mesma ou próxima linha)
      - fatDemFPontaIndRegistrada                          → tabela medidor "DEMANDA - KW FORA PONTA"
      - fatDemFPontaIndValorReais                          → R$ da linha DEMANDA LIVRE
      - fatDemPontaIndRegistrada / Faturada                → tabela medidor "DEMANDA - KW PONTA"

    Energia:
      - fatConFPontaInd* → "CONSUMO FP", "CONSUMO NAO COMPENSADO FP", "ENERGIA ATIVA FORNECIDA FP", "PARCELA TUSD FP"
      - fatConPonta*     → "CONSUMO P",  "CONSUMO NAO COMPENSADO P",  "ENERGIA ATIVA FORNECIDA P",  "PARCELA TUSD P"

    Notas:
      - Dados KW/KWH podem estar na MESMA linha do cabeçalho (layout mais comum) ou na linha seguinte
      - Multas/juros capturados aqui com fallback para linha seguinte (Equatorial GO usa "ENCARGO DE MORA")
      - fatDescontoFio    = desconto PCH na demanda (kW, 50%)
      - fatDescontoFioKWh = desconto PCH na energia (kWh, 45,48%)
    """
    out = {
        "fatDemContratadaFPonta":         0.0,
        "fatDemFPontaIndRegistrada":       0.0,
        "fatDemFPontaIndFaturada":         0.0,
        "fatDemFPontaIndValorReais":       0.0,
        "fatDemPontaIndRegistrada":        0.0,
        "fatDemPontaIndFaturada":          0.0,
        "fatDemFPontaIndUltra":            0.0,
        "fatDemFPontaIndUltraValorReais":  0.0,
        "fatDemPontaUltra":                0.0,
        "fatDemPontaUltraValorReais":      0.0,
        "fatConFPontaIndRegistrado":       0.0,
        "fatConFPontaIndFaturado":         0.0,
        "fatConFPontaIndValorReais":       0.0,
        "fatConFPontaCapRegistrado":       0.0,
        "fatConFPontaCapFaturado":         0.0,
        "fatConFPontaCapValorReais":       0.0,
        "fatConPontaRegistrado":           0.0,
        "fatConPontaFaturado":             0.0,
        "fatConPontaValorReais":           0.0,
        "fatConFPontaIndExcRegistrado":    0.0,
        "fatConFPontaIndExcFaturado":      0.0,
        "fatConFPontaIndExcValorReais":    0.0,
        "fatConPontaExcRegistrado":        0.0,
        "fatConPontaExcFaturado":          0.0,
        "fatConPontaExcValorReais":        0.0,
        "fatIlumPublica":                  0.0,
        "fatMultas":                       0.0,
    }

    linhas = [ln.strip() for ln in text.splitlines() if ln.strip()]

    def _buscar_kw(linha_a, linha_b=""):
        return _PAT_KW_MT.search(linha_a) or (_PAT_KW_MT.search(linha_b) if linha_b else None)

    def _buscar_kwh(linha_a, linha_b=""):
        return _PAT_KWH_MT.search(linha_a) or (_PAT_KWH_MT.search(linha_b) if linha_b else None)

    for i, ln in enumerate(linhas):
        upper       = _eq_ascii_upper(ln)
        monies      = RE_MONEY.findall(ln)
        prox        = linhas[i + 1] if i + 1 < len(linhas) else ""
        prox_monies = RE_MONEY.findall(prox)

        # ── Itens de fatura ──────────────────────────────────────────────────
        # "DEMANDA LIVRE PCH50 C/ DESC. 50%" — dados podem estar na mesma linha ou na próxima
        # Exclui a linha-resumo "DEMANDA LIVRE - kW 46"
        if "DEMANDA LIVRE" in upper and "DEMANDA LIVRE -" not in upper:
            m = _buscar_kw(ln, prox)
            if m:
                kw = _br2f(m.group(1))
                out["fatDemContratadaFPonta"]   += kw
                out["fatDemFPontaIndFaturada"]   += kw
                out["fatDemFPontaIndValorReais"] += _br2f(m.group(3))

        # "DEMANDA kW 92,7625 26,52... 2.460,43" — linha de cobrança sem "LIVRE" nem traço
        # Presente nos layouts TE_SPLIT onde a demanda FP é faturada diretamente (não via DEMANDA LIVRE)
        elif ("DEMANDA" in upper and "KW" in upper
              and "- KW" not in upper and "LIVRE" not in upper
              and "ULTRA" not in upper and "PONTA" not in upper
              and "RESERVADO" not in upper and "ISENTO" not in upper
              and "SCEE" not in upper and "GERACAO" not in upper):
            m = _buscar_kw(ln, prox)
            if m:
                kw = _br2f(m.group(1))
                if kw > 0:
                    out["fatDemFPontaIndFaturada"]   += kw
                    out["fatDemFPontaIndValorReais"] += _br2f(m.group(3))

        # Linha TUSD (ou tarifa única): acumula kWh E R$
        elif any(kw in upper for kw in _FP_ENERGY_KEYS) and "- TE" not in upper:
            m = _buscar_kwh(ln, prox)
            if m:
                kwh = _br2f(m.group(1))
                out["fatConFPontaIndRegistrado"] += kwh
                out["fatConFPontaIndFaturado"]   += kwh
                out["fatConFPontaIndValorReais"] += _br2f(m.group(3))

        elif any(kw in upper for kw in _CAP_ENERGY_KEYS) and "- TE" not in upper:
            m = _buscar_kwh(ln, prox)
            if m:
                kwh = _br2f(m.group(1))
                out["fatConFPontaCapRegistrado"] += kwh
                out["fatConFPontaCapFaturado"]   += kwh
                out["fatConFPontaCapValorReais"] += _br2f(m.group(3))

        elif any(kw in upper for kw in _P_ENERGY_KEYS) and "FP" not in upper and "- TE" not in upper:
            m = _buscar_kwh(ln, prox)
            if m:
                kwh = _br2f(m.group(1))
                out["fatConPontaRegistrado"] += kwh
                out["fatConPontaFaturado"]   += kwh
                out["fatConPontaValorReais"] += _br2f(m.group(3))

        # Linha TE: mesmo kWh da linha TUSD — acumula só o R$ adicional de TE
        elif ("ENERGIA ATIVA FORNECIDA FP" in upper and "- TE" in upper) or any(kw in upper for kw in _TE_FP_KEYS):
            m = _buscar_kwh(ln, prox)
            if m:
                out["fatConFPontaIndValorReais"] += _br2f(m.group(3))

        elif ("ENERGIA ATIVA FORNECIDA HR" in upper and "- TE" in upper) or any(kw in upper for kw in _TE_CAP_KEYS):
            m = _buscar_kwh(ln, prox)
            if m:
                out["fatConFPontaCapValorReais"] += _br2f(m.group(3))

        elif ("ENERGIA ATIVA FORNECIDA P" in upper and "- TE" in upper) or any(kw in upper for kw in _TE_PTA_KEYS):
            m = _buscar_kwh(ln, prox)
            if m:
                out["fatConPontaValorReais"] += _br2f(m.group(3))

        elif (
            ("CONTRIB." in upper or "CONTRIBUICAO" in upper or "CIP" in upper or "COSIP" in upper)
            and ("ILUM" in upper or "COSIP" in upper)
            and ("PUBLIC" in upper or "MUNICIPAL" in upper or "MUNIC" in upper or "PREF" in upper or "CIP" in upper)
        ):
            fonte = monies or prox_monies
            if fonte:
                out["fatIlumPublica"] += _br2f(fonte[0])

        # ── Multas / encargos por atraso ─────────────────────────────────────
        # Equatorial GO pode usar "ENCARGO DE MORA" além de "MULTA"/"JUROS"
        elif "MULTA" in upper and ("ATRASO" in upper or re.search(r"\bMULTA\b", upper)):
            fonte = monies or prox_monies
            if fonte:
                out["fatMultas"] += abs(_br2f(fonte[-1]))

        elif ("ENCARGO" in upper and ("MORA" in upper or "ATRASO" in upper)):
            fonte = monies or prox_monies
            if fonte:
                out["fatMultas"] += abs(_br2f(fonte[-1]))

        elif "JUROS" in upper and ("MORA" in upper or "ATRASO" in upper or "MORATOR" in upper):
            fonte = monies or prox_monies
            if fonte:
                out["fatMultas"] += abs(_br2f(fonte[-1]))

        elif ("ATUALIZACAO" in upper or "CORRECAO" in upper or "CORREC" in upper) and ("MONETARIA" in upper or "IPCA" in upper):
            fonte = monies or prox_monies
            if fonte:
                out["fatMultas"] += abs(_br2f(fonte[-1]))

        # ── Tabela de leitura — demanda registrada ───────────────────────────
        # "13772381-4 DEMANDA - KW FORA PONTA 022807 024267 0,028800 42,048"
        # Pega a medicao logo apos o fator de 6 casas para nao confundir com R$.
        elif ("DEMANDA" in upper and "KW" in upper and "FORA" in upper
              and "PONTA" in upper and "GERACAO" not in upper and "DMCR" not in upper
              and "ULTRA" not in upper and "LIVRE" not in upper):
            val = _demanda_registrada_linha(ln)
            if val is not None:
                out["fatDemFPontaIndRegistrada"] = val

        # "13772381-4 DEMANDA - KW PONTA 012152 012681 0,028800 15,2352 361,71"
        elif ("DEMANDA" in upper and "KW" in upper and "PONTA" in upper
              and "FORA" not in upper and "GERACAO" not in upper
              and "DMCR" not in upper and "RESERVADO" not in upper
              and "LIVRE" not in upper and "ULTRA" not in upper):
            val = _demanda_registrada_linha(ln)
            if val is not None:
                out["fatDemPontaIndRegistrada"] = val
                out["fatDemPontaIndFaturada"]   = val

        # Demanda ultrapassagem FP e P — dados podem estar na mesma linha ou na próxima
        elif "DEMANDA" in upper and "ULTRA" in upper and "KW" in upper:
            m = _buscar_kw(ln, prox)
            if m:
                kw_ultra  = _br2f(m.group(1))
                val_ultra = _br2f(m.group(3))
                if "PONTA" in upper and "FORA" not in upper:
                    if kw_ultra > 0:
                        out["fatDemPontaUltra"] = kw_ultra
                    if val_ultra > 0:
                        out["fatDemPontaUltraValorReais"] = val_ultra
                else:
                    if kw_ultra > 0:
                        out["fatDemFPontaIndUltra"] = kw_ultra
                    if val_ultra > 0:
                        out["fatDemFPontaIndUltraValorReais"] = val_ultra
        elif "DEMANDA" in upper and "ULTRA" in upper:
            m = _buscar_kw(ln, prox)
            if m:
                kw_ultra  = _br2f(m.group(1))
                val_ultra = _br2f(m.group(3))
                if "PONTA" in upper and "FORA" not in upper:
                    out["fatDemPontaUltra"] = kw_ultra
                    out["fatDemPontaUltraValorReais"] = val_ultra
                else:
                    out["fatDemFPontaIndUltra"] = kw_ultra
                    out["fatDemFPontaIndUltraValorReais"] = val_ultra

        # UFER — Consumo Excedente de Reativo FP e P
        # Linhas de medição ("UFER RESERVADO 005496 ...") não têm "KVARH" — ignorar.
        # A linha de faturamento ("UFER HR kVArh 558,42 0,276308 154,30") tem os dados inline.
        elif "UFER" in upper or ("REATIVO" in upper and "EXCEDENTE" in upper):
            if "KVARH" not in upper and "KVAR" not in upper:
                pass  # linha de tabela de medição — sem dados de faturamento
            else:
                _PAT_UFER = r"KV?A?[Rr][Hh]?\s+([\d.]+,\d+)\s+([\d.]+,\d+)\s+([\d.]+,\d+)"
                m = (_re.search(_PAT_UFER, ln, _re.IGNORECASE)
                     or _re.search(_PAT_UFER, prox, _re.IGNORECASE))
                fonte_ufer = None
                if m:
                    fonte_ufer = (m.group(1), m.group(3))
                elif monies and len(monies) >= 2:
                    fonte_ufer = (monies[0], monies[-1])
                if fonte_ufer:
                    kwh_exc = abs(_br2f(fonte_ufer[0]))
                    val_exc = abs(_br2f(fonte_ufer[1]))
                    if "PONTA" in upper and "FORA" not in upper and "FP" not in upper:
                        out["fatConPontaExcRegistrado"] = kwh_exc
                        out["fatConPontaExcFaturado"]   = kwh_exc
                        out["fatConPontaExcValorReais"] = val_exc
                    else:
                        out["fatConFPontaIndExcRegistrado"] = kwh_exc
                        out["fatConFPontaIndExcFaturado"]   = kwh_exc
                        out["fatConFPontaIndExcValorReais"] = val_exc

        # Bandeira tarifária — "AD. BAND. VERMELHA EN. ATIVA FORN. FP - kWh ..."
        elif "AD." in upper and "BAND" in upper and (
                "VERMELHA" in upper or "AMARELA" in upper or "ESCASSEZ" in upper or "VERDE" in upper):
            m = _buscar_kwh(ln, prox)
            if m:
                out.setdefault("fatValBandeira", 0.0)
                out["fatValBandeira"] = round((out.get("fatValBandeira") or 0.0) + _br2f(m.group(3)), 2)

    for k, v in out.items():
        if isinstance(v, float):
            out[k] = round(v, 2)
    return out


def processar_pdf(pdf_path: str, tipo: str) -> dict:
    """
    Extrai dados do PDF usando os extratores de ocr_enel e sobrescreve
    concCod com o código da Equatorial Goiás.
    """
    filename = Path(pdf_path).name
    try:
        text = _extract_text(pdf_path)   # extrai uma vez, reutilizado
        tipo = (tipo or _detectar_tipo_equatorial_go(text)).lower()

        if tipo == "bt":
            dados = extrair_bt(pdf_path)
            dados["TARIFA_DETECTADA"] = (
                "B3_BRANCA" if dados.get("cadTarifaCod") == "Branca" else "B3"
            )
            # Campos GD/SCEE e retenções LEI 9430 — sobrescreve zeros do extrator base
            dados.update(_extras_equatorial_go_bt(text))
            dados.update({k: v for k, v in _core_equatorial_go_bt(text).items() if v})
            dados.update({k: v for k, v in _fisco_equatorial_go(text).items() if v})
            dados.update({k: v for k, v in _retencoes_equatorial_go(text).items() if v})
        else:
            dados = extrair_mt(pdf_path)
            dados["TARIFA_DETECTADA"] = _detectar_subtarifa_mt(text)
            # ocr_enel._itens_mt hardcoda 5,85% — Equatorial GO não usa esse tributo
            dados["fatTributoFederalPerc"] = 0.0
            dados["fatTributoFederalVal"]  = 0.0
            if not dados.get("fatCodigoBarras"):
                dados["fatCodigoBarras"] = _get_codigo_barras_enel(text)
            # Campos específicos Equatorial GO MT — sobrescreve zeros do extrator base
            dados.update(_extras_equatorial_go_mt(text))
            dados.update({k: v for k, v in _core_equatorial_go_mt(text).items() if v})
            dados.update({k: v for k, v in _fisco_equatorial_go(text).items() if v})
            dados.update({k: v for k, v in _retencoes_equatorial_go(text).items() if v})
            # Detecta se o PDF já separa os postos horários em linhas distintas.
            # Nesses layouts, Ponta e Fora Ponta precisam continuar separados no Consen;
            # consolidar tudo em "Ind" derruba a auditoria.
            _text_up = text.upper()
            _has_fp_split = any(
                token in _text_up
                for token in (
                    "PARCELA TUSD FP",
                    "ENERGIA ATIVA FORNECIDA FP",
                    "CONSUMO FP",
                    "CONSUMO NAO COMPENSADO FP",
                )
            )
            _has_p_split = any(
                token in _text_up
                for token in (
                    "PARCELA TUSD P",
                    "ENERGIA ATIVA FORNECIDA P",
                    "CONSUMO P",
                    "CONSUMO NAO COMPENSADO P",
                )
            )
            _is_split_por_posto = (
                "PARCELA TE FP" in _text_up
                or "SCEE" in _text_up
                or "ENERGIA ATIVA FORNECIDA FP" in _text_up
                or (_has_fp_split and _has_p_split)
            )
            if not _is_split_por_posto:
                _g = dados.get
                dados["fatConFPontaIndFaturado"]  = round((_g("fatConFPontaIndFaturado",0) or 0) + (_g("fatConFPontaCapFaturado",0) or 0) + (_g("fatConPontaFaturado",0) or 0), 2)
                dados["fatConFPontaIndValorReais"] = round((_g("fatConFPontaIndValorReais",0) or 0) + (_g("fatConFPontaCapValorReais",0) or 0) + (_g("fatConPontaValorReais",0) or 0), 2)
                dados["fatConFPontaCapFaturado"]  = 0.0
                dados["fatConFPontaCapValorReais"] = 0.0
                dados["fatConPontaFaturado"]       = 0.0
                dados["fatConPontaValorReais"]     = 0.0
                dados["fatDemFPontaIndRegistrada"] = 0.0
            else:
                # TE_SPLIT/GD: "DEMANDA - KW PONTA" vai para fatDemPontaRegistrada
                ponta_dem = dados.get("fatDemPontaIndRegistrada") or 0
                if ponta_dem:
                    dados["fatDemPontaRegistrada"]    = ponta_dem
                    dados["fatDemPontaIndRegistrada"] = 0.0
                    dados["fatDemPontaIndFaturada"]   = 0.0

        instalacao = _resolver_instalacao_equatorial_go(pdf_path, text, tipo)
        if instalacao:
            dados["Instalacao"] = instalacao
        nota_fiscal = _extrair_nota_fiscal_equatorial_go(text)
        if nota_fiscal:
            dados["NOTAFISCAL"] = nota_fiscal
        codigo_cliente = _extrair_codigo_cliente_equatorial_go(text)
        if codigo_cliente:
            dados["CODIGOCLIENTE"] = codigo_cliente
        codigo_barras = _extrair_codigo_barras_equatorial_go(text)
        if codigo_barras:
            dados["fatCodigoBarras"] = codigo_barras
        valor_nota = _extrair_valor_nota_fiscal_equatorial_go(text)
        if valor_nota > 0:
            dados["fatValorNotaFiscal"] = valor_nota

        # Referência e leitura atual:
        # Consen determina "Mês já processado" pelo mês da leitura atual preenchida no form.
        # Quando a leitura cai no último dia do mês (ex: 31/03), o mês de referência já existe.
        # Regra: +1 dia na leitura atual → se avançou de mês, corrige fatDataLeituraAtual também.
        # Fallback para varredura de texto quando a leitura não está disponível.
        ref_date: dt.date | None = None
        leitura_atual = dados.get("fatDataLeituraAtual")
        if leitura_atual:
            try:
                if isinstance(leitura_atual, (dt.datetime, dt.date)):
                    d = (leitura_atual.date()
                         if isinstance(leitura_atual, dt.datetime) else leitura_atual)
                else:
                    partes = str(leitura_atual)[:10].split("-")
                    d = dt.date(int(partes[0]), int(partes[1]), int(partes[2]))
                d_ref = d + dt.timedelta(days=1)
                ref_date = dt.date(d_ref.year, d_ref.month, 1)
                # Se avançou de mês (último dia), corrige a data de leitura no xlsx
                if d_ref.month != d.month or d_ref.year != d.year:
                    dados["fatDataLeituraAtual"] = d_ref
            except Exception:
                pass
        if not ref_date:
            ref_date = _resolver_ref_equatorial_go(text)
        if ref_date:
            dados["fatDataReferencia"] = ref_date

        # ── Sobrescreve concCod com o código Equatorial GO ──────────────────
        dados["concCod"] = CONC_COD_EQUATORIAL_GO

        # ── Distribui lista de obs nos pares de colunas ──────────────────────
        # obs_base: gerada pelo extrator ENEL (DIC/FIC, multas, etc.)
        # obs_extra: obs específicas Equatorial GO MT (DIF TUSD FP + P separados)
        obs_base  = dados.pop("_obs_list", None) or []
        obs_extra = dados.pop("_obs_equatorial_mt", None) or []
        obs_final = obs_base + obs_extra        # base vem primeiro, extras no final
        if obs_final:
            for _i, (_cod, _val) in enumerate(obs_final[:5], start=1):
                dados[f"obsCod_{_i}"]   = _cod
                dados[f"obsValor_{_i}"] = round(float(_val), 2) if _val else 0

        dados["ARQUIVO"] = filename
        dados["ERRO"]    = ""
        log.info(
            f"    OK  {filename}  ->  {dados['TARIFA_DETECTADA']}"
            f"  (carimbo {dados['fatCarimbo']})"
        )
        return dados

    except Exception as exc:
        log.error(f"    ERRO  {filename}: {exc}")
        return {
            "fatCarimbo":       _carimbo_do_nome(filename),
            "concCod":          CONC_COD_EQUATORIAL_GO,
            "TARIFA_DETECTADA": "ERRO",
            "ARQUIVO":          filename,
            "ERRO":             str(exc),
        }


# =============================================================================
# NAVEGAÇÃO DE PASTAS
# =============================================================================

def _pasta_label(pasta: Path) -> str:
    m = re.search(r"(\d{2})[_\-\s]?(\d{4})", pasta.name)
    return f"{m.group(1)}{m.group(2)}" if m else pasta.name


def _listar_pastas_mes(base: Path) -> list:
    padrao = re.compile(r"^(\d{2})[_\-\s]?(\d{4})$")
    return sorted(
        [p for p in base.iterdir() if p.is_dir() and padrao.match(p.name.strip())],
        key=lambda p: p.name,
    )


def _subpasta(pasta_mes: Path, nomes_aceitos: set) -> Path | None:
    for sub in pasta_mes.iterdir():
        if sub.is_dir() and sub.name.strip().lower() in nomes_aceitos:
            return sub
    return None


# =============================================================================
# PROCESSAMENTO DE UM MÊS
# =============================================================================

def _processar_subpasta(pasta_sub: Path, tipo: str, xlsx_saida: Path):
    pdfs = sorted(pasta_sub.glob("*.pdf"))
    if not pdfs:
        log.warning(f"  Sem PDFs em: {pasta_sub}")
        return

    label = "BT" if tipo == "bt" else "MT"
    log.info(f"  {label}  ->  {pasta_sub.name}  ({len(pdfs)} PDFs)")

    registros = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = {ex.submit(processar_pdf, str(p), tipo): p for p in pdfs}
        for f in as_completed(futures):
            registros.append(f.result())

    def _sort_key(r):
        try:
            return int(r.get("fatCarimbo", 0) or 0)
        except (ValueError, TypeError):
            return 0

    registros.sort(key=_sort_key)
    salvar_excel(registros, xlsx_saida)


def processar_pasta_flat(pasta: Path, tipo: str, mes: str, ano: str) -> bool:
    """Processa exatamente os PDFs diretos de um lote tecnico do Watcher."""
    pdfs = sorted(pasta.glob("*.pdf"))
    if not pdfs:
        log.error(f"Pasta de lote vazia: {pasta}")
        return False
    xlsx = PASTA_SAIDA / f"ocr_equatorial_go_{tipo.upper()}_{mes}{ano}.xlsx"
    log.info(f"Pasta de entrada efetiva: {pasta}")
    log.info(f"PDFs encontrados: {len(pdfs)} -> {[p.name for p in pdfs]}")
    _processar_subpasta(pasta, tipo, xlsx)
    if not xlsx.exists():
        log.error(f"XLSX nao criado: {xlsx}")
        return False
    return True


def processar_mes(pasta_mes: Path, fazer_bt: bool = True, fazer_mt: bool = True):
    label = _pasta_label(pasta_mes)
    log.info(f"\n{'='*60}")
    log.info(f"  {pasta_mes.name}  ->  {label}")
    log.info(f"{'='*60}")

    if fazer_bt:
        sub = _subpasta(pasta_mes, NOMES_BT)
        if sub:
            xlsx = PASTA_SAIDA / f"ocr_equatorial_go_BT_{label}.xlsx"
            _processar_subpasta(sub, "bt", xlsx)
        else:
            log.warning(f"  Subpasta BT não encontrada em: {pasta_mes.name}")

    if fazer_mt:
        sub = _subpasta(pasta_mes, NOMES_MT)
        if sub:
            xlsx = PASTA_SAIDA / f"ocr_equatorial_go_MT_{label}.xlsx"
            _processar_subpasta(sub, "mt", xlsx)
        else:
            log.warning(f"  Subpasta MT não encontrada em: {pasta_mes.name}")


# =============================================================================
# CLI
# =============================================================================

def parse_args():
    p = argparse.ArgumentParser(description="OCR Equatorial Goiás -> planilhas BT e MT")
    p.add_argument("--mes",     type=str, help="Mês (ex: 03)")
    p.add_argument("--ano",     type=str, help="Ano (ex: 2026)")
    p.add_argument("--pasta",   type=str, help="Nome exato da subpasta (ex: 03-2026)")
    p.add_argument("--todos",   action="store_true", help="Processa todos os meses")
    p.add_argument("--tipo",    choices=["bt", "mt", "ambos"], default="ambos")
    p.add_argument("--recriar", action="store_true",
                   help="Apaga o xlsx existente antes de processar (recria do zero)")
    return p.parse_args()


def _resolver_pasta(args) -> Path:
    if args.pasta:
        explicit = Path(args.pasta)
        p = explicit if explicit.is_dir() else PASTA_DOWNLOAD / args.pasta
        if not p.is_dir():
            log.error(f"Pasta não encontrada: {p}")
            sys.exit(1)
        return p

    hoje = dt.date.today()
    mes  = args.mes or f"{hoje.month:02d}"
    ano  = args.ano or str(hoje.year)

    for nome in [f"{mes}-{ano}", f"{mes}_{ano}", f"{mes}{ano}", f"{mes} {ano}"]:
        p = PASTA_DOWNLOAD / nome
        if p.is_dir():
            return p

    log.error(f"Pasta {mes}/{ano} não encontrada em {PASTA_DOWNLOAD}.")
    sys.exit(1)


def main():
    args = parse_args()
    PASTA_SAIDA.mkdir(parents=True, exist_ok=True)

    fh = logging.FileHandler(
        PASTA_SAIDA / "ocr_equatorial_go.log", encoding="utf-8", errors="replace"
    )
    fh.setFormatter(logging.Formatter(
        "%(asctime)s  %(levelname)-8s  %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    ))
    log.addHandler(fh)

    fazer_bt = args.tipo in ("bt", "ambos")
    fazer_mt = args.tipo in ("mt", "ambos")

    if getattr(args, "recriar", False):
        hoje  = dt.date.today()
        mes   = args.mes or f"{hoje.month:02d}"
        ano   = args.ano or str(hoje.year)
        label = f"{mes}{ano}"
        for xlsx in [
            PASTA_SAIDA / f"ocr_equatorial_go_BT_{label}.xlsx",
            PASTA_SAIDA / f"ocr_equatorial_go_MT_{label}.xlsx",
        ]:
            if xlsx.exists():
                xlsx.unlink()
                log.info(f"  [recriar] Removido: {xlsx.name}")

    log.info("=" * 60)
    log.info("  OCR EQUATORIAL GOIÁS  -  BT + MT".center(60))
    log.info("=" * 60)
    log.info(f"  Tipo     : {args.tipo.upper()}")
    log.info(f"  concCod  : {CONC_COD_EQUATORIAL_GO}")
    log.info(f"  Download : {PASTA_DOWNLOAD}")
    log.info(f"  Saída    : {PASTA_SAIDA}")

    modo_todos = args.todos or (not args.mes and not args.ano and not args.pasta)

    if modo_todos:
        pastas = _listar_pastas_mes(PASTA_DOWNLOAD)
        if not pastas:
            log.error(f"Nenhuma pasta de mês encontrada em: {PASTA_DOWNLOAD}")
            sys.exit(1)
        log.info(f"  Meses    : {len(pastas)}")
        for pasta in pastas:
            processar_mes(pasta, fazer_bt, fazer_mt)
    elif args.pasta and Path(args.pasta).is_dir():
        if args.tipo == "ambos":
            log.error("Lote flat exige --tipo bt ou --tipo mt.")
            sys.exit(1)
        mes = args.mes or f"{dt.date.today().month:02d}"
        ano = args.ano or str(dt.date.today().year)
        if not processar_pasta_flat(Path(args.pasta), args.tipo, mes, ano):
            sys.exit(1)
    else:
        pasta = _resolver_pasta(args)
        processar_mes(pasta, fazer_bt, fazer_mt)

    log.info("\nConcluído.")


if __name__ == "__main__":
    main()
