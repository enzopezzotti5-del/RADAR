#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ocr_equatorial_pi_mt.py
-----------------------
OCR de faturas MT Equatorial Piauí → XLSX no schema CEMIG/Consen.

Layout: tabela com colunas  qty | tariff1 | tariff2 | val1 | val2 | total
        fiscal (ICMS/PIS/COFINS) aparece embutido na coluna direita da mesma
        linha de alguns itens.

Uso:
    python ocr_equatorial_pi_mt.py --pasta "\\\\srv\\pasta\\MT" --saida ocr_pi_mt.xlsx
    python ocr_equatorial_pi_mt.py --pasta "..." --mes 05 --ano 2026

Env vars:
    EQUATORIAL_PI_MT_PASTA_PDF   — pasta-raiz com os PDFs
    EQUATORIAL_PI_MT_PASTA_SAIDA — pasta para o XLSX de saída
    EQUATORIAL_PI_CONC_COD       — código da concessionária no Consen
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import os
import re
import sys
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pdfplumber

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from core.ocr.OCR_Cemig import HEADERS_REF, salvar_excel
from indice_master import MasterIndice

# =============================================================================
# CONFIGURAÇÃO
# =============================================================================

PASTA_PDF_DEFAULT = Path(os.environ.get(
    "EQUATORIAL_PI_MT_PASTA_PDF",
    "//10.10.250.21/Energia/CONTASDEENERGIAELETRICA/BB/ENZO/Baixa Tensão/EQUATORIAL/PIAUI/MT",
))
PASTA_SAIDA_DEFAULT = Path(os.environ.get(
    "EQUATORIAL_PI_MT_PASTA_SAIDA",
    "//10.10.250.21/Energia/ARQUIVOS ENZO/OCR EQUATORIAL PI",
))
CONC_COD: str = os.environ.get("EQUATORIAL_PI_CONC_COD", "EQUATORIAL PI")
SISTEMA_MASTER = "EQUATORIAL PI MT"
MAX_WORKERS = 4

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

# =============================================================================
# NORMALIZAÇÃO
# =============================================================================

def _norm(text: str) -> str:
    """ASCII upper — elimina acentos/encoding para comparação robusta."""
    return unicodedata.normalize("NFKD", text or "").encode("ASCII", "ignore").decode("ASCII").upper()


def _br2f(valor: str) -> float:
    """Converte string BR (1.234,56) para float."""
    try:
        return float(str(valor).strip().replace(".", "").replace(",", "."))
    except (ValueError, AttributeError):
        return 0.0


# Captura valores com exatamente 2 casas decimais (exclui tarifas unitárias com 3–8 dp)
_RE_MONEY = re.compile(r"(?<!\d)-?[\d.]+,\d{2}(?!\d)")

# Padrão para linhas de medição da tabela de leituras (começa com código de medidor)
_RE_MEDICAO = re.compile(r"^\w{8,}\s+(?:TUSD|Consumo|Demanda|Reat|UFER)\s+", re.IGNORECASE)

# =============================================================================
# EXTRAÇÃO DE TEXTO
# =============================================================================

def _extrair_texto(pdf_path: str | Path, max_paginas: int = 2) -> str:
    partes: list[str] = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        for page in pdf.pages[:max_paginas]:
            partes.append(page.extract_text() or "")
    return "\n".join(partes)


# =============================================================================
# HELPERS DE INSTALAÇÃO / CARIMBO
# =============================================================================

def _instalacao_formatada_do_nome(filename: str) -> str:
    """Extrai instalação com formatação original para o Consen.
    Remove sufixo de data 'DD.MM' independente do separador usado:
      '1.023.103.019-50 - 20.06.pdf'  → '1.023.103.019-50'
      '105.142.019-13- 20.06.pdf'     → '105.142.019-13'
    """
    stem = Path(filename).stem
    return re.sub(r"\s*-+\s*\d{1,2}\.\d{2}\s*$", "", stem).strip()


def _instalacao_do_nome(filename: str) -> str:
    """Extrai número da instalação sem formatação (só dígitos) — para indice_master."""
    return re.sub(r"\D", "", _instalacao_formatada_do_nome(filename))


def _instalacao_do_texto(text: str) -> str:
    """Tenta extrair instalação do corpo da fatura (após PERDAS DE TRANSFORMACAO)."""
    upper = _norm(text)
    m = re.search(
        r"PERDAS\s+DE\s+TRANSFORMACAO\s*/\s*RAMAL\s*:\s*[\d.,% ]+\s+([\d.\-]+)",
        upper,
    )
    if m:
        return re.sub(r"\D", "", m.group(1))
    # Fallback: UC na seção UNIDADE CONSUMIDORA do boleto
    m2 = re.search(r"UNIDADE\s+CONSUMIDORA\s+([\d.\-]+)", upper)
    if m2:
        return re.sub(r"\D", "", m2.group(1))
    return ""


# =============================================================================
# EXTRAÇÃO DE CAMPOS BÁSICOS
# =============================================================================

def _extrair_mes_ref(text: str) -> dt.date | None:
    """Referência: padrão primário 'MM/YYYY DD/MM/YYYY R$'; fallback boleto."""

    def _to_date(mm: str, yyyy: str) -> dt.date | None:
        try:
            return dt.date(int(yyyy), int(mm), 1)
        except ValueError:
            return None

    # Primário: "MM/YYYY DD/MM/YYYY R$"
    m = re.search(
        r"\b(0[1-9]|1[0-2])/(20\d{2})\s+\d{2}/\d{2}/20\d{2}\s+R\$",
        text,
    )
    if m:
        d = _to_date(m.group(1), m.group(2))
        if d:
            return d

    # Fallback: "REFERÊNCIA/REFERENCIA ... MM/YYYY" na linha do beneficiário do boleto
    m2 = re.search(
        r"EQUATORIAL\s+PIAU[^\n]+\s+(0[1-9]|1[0-2])/(20\d{2})\b",
        text, re.IGNORECASE,
    )
    if m2:
        d = _to_date(m2.group(1), m2.group(2))
        if d:
            return d

    return None


def _extrair_valor_fatura(text: str) -> float:
    """Valor total da fatura: múltiplos padrões de extração."""
    # Primário: "MM/YYYY DD/MM/YYYY R$ X.XXX,XX"
    m = re.search(
        r"\b(?:0[1-9]|1[0-2])/20\d{2}\s+\d{2}/\d{2}/20\d{2}\s+R\$\s*([\d.]+,\d{2})",
        text,
    )
    if m:
        return abs(_br2f(m.group(1)))
    # Fallback: linha do boleto "17 R$ X.XXX,XX" (VALOR DOCUMENTO)
    m2 = re.search(r"(?:^|\s)17\s+R\$\s*([\d.]+,\d{2})", text)
    if m2:
        return abs(_br2f(m2.group(1)))
    # Fallback: "(=) VALOR COBRADO" precedido pelo valor
    m3 = re.search(r"R\$\s*([\d.]+,\d{2})\s*\n.*?(?:VALOR\s+COBRADO|VALOR\s+DOCUMENTO)", text)
    if m3:
        return abs(_br2f(m3.group(1)))
    return 0.0


def _extrair_nota_fiscal(text: str) -> str:
    upper = _norm(text)
    # Primário: "NOTA FISCAL Nº XXXXXXXX"
    m = re.search(r"NOTA\s+FISCAL\s+N[Oº°]*\s*(\d{6,20})", upper)
    if m:
        return m.group(1).strip()
    # Fallback: NUMERO DE REFERENCIA "0YYYYMMxxxxxxxx" → últimos 9 dígitos = NF
    m2 = re.search(r"\b0(20\d{2})(0[1-9]|1[0-2])(\d{9,10})\b", text)
    if m2:
        return m2.group(3)
    return ""


def _extrair_datas_leitura(text: str) -> tuple[str, str]:
    """Retorna (leitura_anterior, leitura_atual) da linha de medição do medidor."""
    for ln in text.splitlines():
        m = re.search(
            r"(\d{2}/\d{2}/20\d{2})\s+(\d{2}/\d{2}/20\d{2})\s+\d{1,3}\s+\d{2}/\d{2}/20\d{2}",
            ln,
        )
        if m:
            return m.group(1), m.group(2)
    return "", ""


def _extrair_data_vcto(text: str) -> str:
    """Vencimento: primário da linha 'MM/YYYY DD/MM/YYYY R$'; fallbacks para boleto."""
    # Primário: "MM/YYYY DD/MM/YYYY R$" — segundo campo é o vencimento
    m = re.search(
        r"\b(?:0[1-9]|1[0-2])/20\d{2}\s+(\d{2}/\d{2}/20\d{2})\s+R\$",
        text,
    )
    if m:
        return m.group(1)
    # Fallback 1: "VENCIMENTO DD.MM.YYYY" ou "VENCIMENTO DD/MM/YYYY" na mesma linha
    m2 = re.search(r"VENCIMENTO\s+(\d{2}[./]\d{2}[./]20\d{2})", text, re.IGNORECASE)
    if m2:
        return m2.group(1).replace(".", "/")
    # Fallback 2: VENCIMENTO no final de uma linha, data no fim da próxima
    # ("LOCAL DE PAGAMENTO VENCIMENTO\nPAGÁVEL ... 20/06/2026")
    m3 = re.search(
        r"VENCIMENTO[^\n]*\n[^\n]*?(\d{2}/\d{2}/20\d{2})",
        text, re.IGNORECASE,
    )
    if m3:
        return m3.group(1)
    return ""


def _extrair_data_emissao(text: str) -> str:
    """Data de emissão da NF: 'DATA DE EMISSÃO: DD/MM/YYYY'; fallback DATA PROCESSAMENTO."""
    upper = _norm(text)
    # Primário: linha dedicada da NF
    m = re.search(r"DATA\s+DE\s+EMISSAO\s*:\s*(\d{2}/\d{2}/\d{4})", upper)
    if m:
        return m.group(1)
    # Fallback 1: boleto — "DATA DOCUMENTO" é cabeçalho de coluna; valor está na próxima linha
    linhas = text.splitlines()
    for i, ln in enumerate(linhas):
        if re.search(r"DATA\s+DOCUMENTO", ln, re.IGNORECASE) and i + 1 < len(linhas):
            dm = re.match(r"(\d{2}[./]\d{2}[./]20\d{2})", linhas[i + 1].strip())
            if dm:
                return dm.group(1).replace(".", "/")
    # Fallback 2: DATA PROCESSAMENTO na linha seguinte ao cabeçalho
    for i, ln in enumerate(linhas):
        if re.search(r"DATA\s+PROCESSAMENTO", ln, re.IGNORECASE) and i + 1 < len(linhas):
            nxt = linhas[i + 1].strip()
            dts = re.findall(r"\d{2}[./]\d{2}[./]20\d{2}", nxt)
            if dts:
                return dts[-1].replace(".", "/")
    return ""


def _extrair_codigo_barras(text: str) -> str:
    """Extrai linha digitável do boleto (47 dígitos)."""
    linhas = text.splitlines()
    # P1: padrão "XXXXX.XXXXX XXXXX.XXXXXX XXXXX.XXXXXX X XXXXXXXXXXXXXX"
    for ln in linhas:
        stripped = ln.strip()
        if "." not in stripped:
            continue
        digits = re.sub(r"\D", "", stripped)
        if len(digits) in (47, 48):
            if len(re.sub(r"[\d.\- ]", "", stripped)) <= 4:
                return digits
    # P2: 44 dígitos contíguos
    m = re.search(r"(?<!\d)(\d{44})(?!\d)", text)
    if m:
        return m.group(1)
    return ""


def _extrair_demanda_contratada(text: str) -> float:
    """Demanda Contratada Única / FP: retorna o valor em kW."""
    m = re.search(
        r"Demanda\s+Contratada\s+[^:\n]{0,30}:\s*([\d.,]+)",
        text, re.IGNORECASE,
    )
    if m:
        v = _br2f(m.group(1))
        return v if v > 0 else 0.0
    return 0.0


def _extrair_demanda_registrada_pi_mt(text: str) -> tuple[float, float]:
    """
    Extrai demanda registrada FP e Ponta da tabela de leituras do medidor.

    Formatos reconhecidos:
      CODIGO Demanda Fora Ponta Demanda Ativa FP Reg ANT ATU FATOR VAL kW
      Dem. Máx. F. Ponta (kW): VAL
    Retorna (fp_kw, ponta_kw).
    """
    fp = ponta = 0.0

    # Tabela de medições: "Demanda Ativa FP Reg ... 21,60 kW"
    m = re.search(
        r"Demanda\s+Ativa\s+FP\s+Reg\s+\d+\s+\d+\s+[\d,]+\s+([\d.,]+)\s+kW",
        text, re.IGNORECASE,
    )
    if m:
        fp = _br2f(m.group(1))

    m = re.search(
        r"Demanda\s+Ativa\s+NP\s+Reg\s+\d+\s+\d+\s+[\d,]+\s+([\d.,]+)\s+kW",
        text, re.IGNORECASE,
    )
    if m:
        ponta = _br2f(m.group(1))

    # Fallback: "Dem. Máx. F. Ponta (kW): 21,60"  ([ \t]* para não cruzar linhas)
    if not fp:
        m = re.search(r"Dem\.\s*M[aá]x\.\s*F\.\s*Ponta\s*\(kW\)[ \t]*:[ \t]*([\d.,]+)", text, re.IGNORECASE)
        if m:
            fp = _br2f(m.group(1))
    if not ponta:
        m = re.search(r"Dem\.\s*M[aá]x\.\s*Ponta\s*\(kW\)[ \t]*:[ \t]*([\d.,]+)", text, re.IGNORECASE)
        if m:
            ponta = _br2f(m.group(1))

    return round(fp, 2), round(ponta, 2)


# =============================================================================
# FISCAL (ICMS / PIS / COFINS)
# =============================================================================

def _extrair_fiscal(text: str) -> dict:
    """
    ICMS/PIS/COFINS: aparecem em linhas dedicadas ou embutidos na coluna direita.
    Formato: 'NOME base aliquota valor' (sem sinal %).
    Usa regex no texto completo para capturar independente da posição.
    """
    out = {
        "fatICMS":              0.0,
        "fatDesIcmsAliquota":   0.0,
        "fatPIS":               0.0,
        "fatDescPisAliquota":   0.0,
        "fatCOFINS":            0.0,
        "fatDesCofinsAliquota": 0.0,
        "_icms_base":           0.0,   # base de cálculo do ICMS → usado como fatValorNotaFiscal
    }

    # Padrão: NOME  base  aliquota  valor
    # aliquota pode ter 2–4 casas decimais; base e valor têm 2dp
    _PAT = r"\b{nome}\b\s+([\d.]+,\d{{2}})\s+([\d.]+,\d+)\s+([\d.]+,\d{{2}})"

    for nome, campo_v, campo_a in [
        ("ICMS",   "fatICMS",   "fatDesIcmsAliquota"),
        ("PIS",    "fatPIS",    "fatDescPisAliquota"),
        ("COFINS", "fatCOFINS", "fatDesCofinsAliquota"),
    ]:
        m = re.search(_PAT.format(nome=nome), text, re.IGNORECASE)
        if not m:
            continue
        base  = abs(_br2f(m.group(1)))
        aliq  = abs(_br2f(m.group(2)))
        valor = abs(_br2f(m.group(3)))
        if base > 0 and valor < base:
            out[campo_v] = round(valor, 2)
            out[campo_a] = round(aliq, 4)
            if nome == "ICMS":
                out["_icms_base"] = round(base, 2)

    return out


# =============================================================================
# DESCONTO FIO B
# =============================================================================

def _extrair_desconto_fio_b(text: str) -> tuple[float, float]:
    """
    Extrai o percentual do Desconto Fio B para demanda (kW) e energia (kWh).
    CEPISA MT usa 50% ou 46,95% dependendo da modalidade.

    Busca padrões como:
      "Desconto Fio B 50%", "Desc. Fio B 46,95%", "FIO B ... 50,00%"
    Retorna (perc_kw, perc_kwh). Quando apenas um percentual é encontrado, aplica
    a ambos os campos.
    """
    upper = _norm(text)
    # Procura percentuais associados a "FIO" na fatura
    percs: list[float] = []
    for m in re.finditer(
        r"FIO\s*B?[^%\n]{0,40}?(\d{1,3}(?:[,.]\d{1,4})?)\s*%",
        upper,
    ):
        v = _br2f(m.group(1))
        if 1.0 <= v <= 100.0:
            percs.append(round(v, 4))

    # Remove duplicatas preservando ordem
    seen: set[float] = set()
    percs_uniq = [x for x in percs if not (x in seen or seen.add(x))]  # type: ignore[func-returns-value]

    if len(percs_uniq) >= 2:
        return percs_uniq[0], percs_uniq[1]
    if len(percs_uniq) == 1:
        return percs_uniq[0], percs_uniq[0]
    return 0.0, 0.0


# =============================================================================
# RETENÇÕES (LEI 9430 via "Tributo a Reter")
# =============================================================================

def _extrair_retencoes(text: str) -> dict:
    out = {
        "fatDescIrpjValRetImposto":   0.0,
        "fatDescCsllValRetImposto":   0.0,
        "fatDescPisValRetImposto":    0.0,
        "fatDescCofinsValRetImposto": 0.0,
    }

    mapeamento = {
        "IRPJ":   "fatDescIrpjValRetImposto",
        "CSLL":   "fatDescCsllValRetImposto",
        "PIS":    "fatDescPisValRetImposto",
        "COFINS": "fatDescCofinsValRetImposto",
    }

    for cod, campo in mapeamento.items():
        m = re.search(
            r"Tributo\s+a\s+Reter\s+" + cod + r"\s+-?\s*([\d.,]+)",
            text, re.IGNORECASE,
        )
        if m:
            v = abs(_br2f(m.group(1)))
            if v > 0:
                out[campo] = round(-v, 2)

    return out


# =============================================================================
# ITENS DE FATURAMENTO (tabela principal)
# =============================================================================

def _truncar_fiscal(ln: str) -> str:
    """Remove 'PIS/COFINS base aliquota valor' embutido no final da linha."""
    m = re.search(
        r"\s+(?:PIS|COFINS)\s+([\d.]+,\d{2})\s+([\d.]+,\d+)\s+([\d.]+,\d{2})\s*$",
        ln, re.IGNORECASE,
    )
    if m:
        return ln[:m.start()].rstrip()
    return ln


# Captura qty (int ou decimal) imediatamente após a unidade entre parênteses
_RE_QTY_UNIT = re.compile(r'\([kK][wWhHvVaArR]+\)\s+([\d.]+(?:,\d+)?)', re.IGNORECASE)


def _qty_from_line(ln: str, monies: list) -> float:
    """Extrai qty da linha: usa padrão (unit) N se disponível, senão monies[0]."""
    m = _RE_QTY_UNIT.search(ln)
    if m:
        return abs(_br2f(m.group(1)))
    return abs(_br2f(monies[0])) if monies else 0.0


def _extrair_itens(text: str) -> dict:
    """
    Percorre linhas da fatura extraindo itens de faturamento.

    Formato das linhas de item:
      Descrição (unidade) qty tariff1 tariff2 val1 val2 total
    onde RE_MONEY captura: val1, val2, total (tarifas têm 3+ dp, excluídas).
    qty pode ser inteiro (sem vírgula) — extraído via _RE_QTY_UNIT.
    → qty = _qty_from_line(), total = monies[-1]

    Linhas de medição (código medidor + Consumo/Demanda) são ignoradas.
    """
    out = {
        # Demanda
        "fatDemContratadaFPonta":        0.0,
        "fatDemFPontaIndFaturada":        0.0,
        "fatDemFPontaIndValorReais":      0.0,
        "fatDemFPontaIndUltra":           0.0,
        "fatDemFPontaIndUltraValorReais": 0.0,
        # Energia FP
        "fatConFPontaIndFaturado":        0.0,
        "fatConFPontaIndValorReais":      0.0,
        # Energia Ponta
        "fatConPontaFaturado":            0.0,
        "fatConPontaValorReais":          0.0,
        # Reativo excedente FP
        "fatConFPontaIndExcFaturado":     0.0,
        "fatConFPontaIndExcValorReais":   0.0,
        # Reativo excedente NP (Ponta)
        "fatConPontaExcFaturado":         0.0,
        "fatConPontaExcValorReais":       0.0,
        # Escassez hídrica
        "fatEscassezHidrica":             0.0,
        "fatEscassezHidricaValorReais":   0.0,
        # Benefício tarifário
        "fatBeneficioTarifarioBrutoValorReais": 0.0,
        "fatBeneficioLiquidoValorReais":        0.0,
        # Bandeira
        "fatValBandeira":                 0.0,
        # CIP
        "fatIlumPublica":                 0.0,
        # Multas/ajustes
        "fatMultas":                      0.0,
        # Tributação federal (Piauí não usa 5,85%)
        "fatTributoFederalPerc":          0.0,
        "fatTributoFederalVal":           0.0,
    }

    linhas = [ln.strip() for ln in text.splitlines() if ln.strip()]

    for i, ln in enumerate(linhas):
        # Ignora linhas de tabela de medição (código medidor na frente)
        if _RE_MEDICAO.match(ln):
            continue

        # Remove fiscal inline (PIS/COFINS no final) e sidebar (após "Demanda Contratada")
        ln_limpa = _truncar_fiscal(ln)
        # Remove sidebar "Demanda Contratada... (kW): N" do final
        ln_limpa = re.sub(
            r"\s+Demanda\s+Contratada\s+.*$", "", ln_limpa, flags=re.IGNORECASE,
        ).rstrip()
        # Remove sidebar "Dem. Reserva Cap..." e "Dem. de Dist..." etc.
        ln_limpa = re.sub(
            r"\s+Dem[.]?\s+(?:Reserva|de\s+Dist|de\s+Gera).*$",
            "", ln_limpa, flags=re.IGNORECASE,
        ).rstrip()

        upper = _norm(ln_limpa)
        monies = _RE_MONEY.findall(ln_limpa)

        def _qty(_ln=ln_limpa, _mon=monies) -> float:
            return _qty_from_line(_ln, _mon)

        def _total(_mon=monies) -> float:
            return abs(_br2f(_mon[-1])) if _mon else 0.0

        # ── Demanda: distribui̧ção / ativa (não isenta, não ultrapassagem) ──────
        if (
            ("DEMANDA DISTRIBUI" in upper or "DEMANDA ATIVA" in upper)
            and "ULT" not in upper
            and "ISENTA" not in upper
            and "FORA PONTA" not in upper   # linha de medição
            and "RESERVADO" not in upper
            and "GERACAO" not in upper
            and len(monies) >= 2
        ):
            out["fatDemFPontaIndFaturada"]   += _qty()
            out["fatDemFPontaIndValorReais"]  += _total()

        # Demanda isenta de ICMS (Piauí cobra parte isenta separado): soma à demanda
        elif (
            ("DEMANDA DISTRIB" in upper or "DEMANDA ATIVA" in upper)
            and "ISENTA" in upper
            and len(monies) >= 2
        ):
            out["fatDemFPontaIndFaturada"]   += _qty()
            out["fatDemFPontaIndValorReais"]  += _total()

        # Demanda ultrapassagem
        elif (
            ("DEMANDA DISTRIBUI" in upper or "DEMANDA" in upper)
            and "ULT" in upper
            and len(monies) >= 2
        ):
            out["fatDemFPontaIndUltra"]           += _qty()
            out["fatDemFPontaIndUltraValorReais"]  += _total()

        # ── Energia FP: "TUSD Energia Fora Ponta" ou "Consumo Fora Ponta" ──────
        elif (
            ("TUSD" in upper or "CONSUMO" in upper)
            and "FORA" in upper and "PONTA" in upper
            and "REATIVO" not in upper
            and len(monies) >= 2
        ):
            out["fatConFPontaIndFaturado"]   += _qty()
            out["fatConFPontaIndValorReais"]  += _total()

        # ── Energia Ponta: "TUSD Energia Ponta" ou "Consumo Ponta" ─────────────
        elif (
            ("TUSD" in upper or "CONSUMO" in upper)
            and "PONTA" in upper
            and "FORA" not in upper
            and "REATIVO" not in upper
            and "NP" not in upper
            and len(monies) >= 2
        ):
            out["fatConPontaFaturado"]   += _qty()
            out["fatConPontaValorReais"]  += _total()

        # ── Reativo excedente FP ─────────────────────────────────────────────
        elif (
            "REATIVO" in upper and "EXCEDENTE" in upper
            and ("FP" in upper or "FORA" in upper)
            and len(monies) >= 2
        ):
            out["fatConFPontaIndExcFaturado"]  += _qty()
            out["fatConFPontaIndExcValorReais"] += _total()

        # ── Reativo excedente NP (Ponta) ─────────────────────────────────────
        elif (
            "REATIVO" in upper and "EXCEDENTE" in upper
            and ("NP" in upper or ("PONTA" in upper and "FORA" not in upper))
            and "FP" not in upper
            and len(monies) >= 2
        ):
            out["fatConPontaExcFaturado"]  += _qty()
            out["fatConPontaExcValorReais"] += _total()

        # ── Escassez hídrica ─────────────────────────────────────────────────
        elif "ESCASSEZ" in upper and "HIDRIC" in upper and len(monies) >= 2:
            out["fatEscassezHidrica"]         += _qty()
            out["fatEscassezHidricaValorReais"] += _total()

        # ── Benefício tarifário bruto (pode ter 3 ou 4 monies sem qty explícita)
        elif "BENEF" in upper and "BRUTO" in upper and monies:
            out["fatBeneficioTarifarioBrutoValorReais"] = abs(_br2f(monies[-1]))

        # ── Benefício tarifário líquido (valor negativo) ─────────────────────
        elif "BENEF" in upper and "LIQUID" in upper and monies:
            # Pode ser negativo na fatura, mas armazenamos como negativo mesmo
            out["fatBeneficioLiquidoValorReais"] = _br2f(monies[-1])

        # ── Bandeira tarifária ───────────────────────────────────────────────
        elif "BAND" in upper and "TARIF" in upper and monies:
            out["fatValBandeira"] = abs(_br2f(monies[-1]))
        elif "ADICIONAL" in upper and "BAND" in upper and monies:
            out["fatValBandeira"] = abs(_br2f(monies[-1]))

        # ── CIP / Iluminação pública ─────────────────────────────────────────
        elif "CIP" in upper and "ILUM" in upper and monies:
            out["fatIlumPublica"] += abs(_br2f(monies[0]))

        # ── Multas / juros / correção / encargos por atraso ─────────────────
        elif monies and (
            "MULTA" in upper
            # Juros: qualquer variação ("Juros de Mora", "Juros Atraso", "Juros s/")
            # Exclui linhas de alíquota/informação fiscal que contenham "JUROS"
            or ("JUROS" in upper and not any(t in upper for t in ("TRIB", "ICMS", "FISCAL", "SELIC", "ANUAL", "AO ANO")))
            # Correção monetária
            or ("CORRECAO" in upper and "MONETAR" in upper)
            # Cobrança de ajuste / atraso / mora
            or ("COBRANCA" in upper and any(t in upper for t in ("AJUSTE", "ATRASO", "MORA", "ATRASO")))
            # Encargo por atraso / mora
            or ("ENCARGO" in upper and any(t in upper for t in ("ATRASO", "MORA")))
        ):
            out["fatMultas"] += abs(_br2f(monies[-1]))

    # Demanda contratada única → fatDemContratadaFPonta
    out["fatDemContratadaFPonta"] = _extrair_demanda_contratada(text)

    # Demanda registrada da tabela de leituras do medidor
    dem_reg_fp, dem_reg_pta = _extrair_demanda_registrada_pi_mt(text)
    if dem_reg_fp:
        out["fatDemFPontaIndRegistrada"] = dem_reg_fp
    if dem_reg_pta:
        out["fatDemPontaRegistrada"] = dem_reg_pta

    for k, v in out.items():
        if isinstance(v, float):
            out[k] = round(v, 2)
    return out


# =============================================================================
# TARIFA DETECTADA
# =============================================================================

def _detectar_tarifa(text: str) -> str:
    upper = _norm(text)
    m = re.search(r"MODALIDADE\s+TARIF[^\n:]*:\s*(\S+)", upper)
    if m:
        return m.group(1).upper()
    return "A4"


# =============================================================================
# CARIMBO VIA INDICE MASTER
# =============================================================================

_indice_master: MasterIndice | None = None
# Cache de lookup: (uc_norm, ref_str) → "BB_XXXXXXX"
_carimbo_lookup: dict[tuple[str, str], str] = {}
_lookup_construido = False


def _get_indice() -> MasterIndice:
    global _indice_master
    if _indice_master is None:
        _indice_master = MasterIndice()
    return _indice_master


def _construir_lookup() -> None:
    """Carrega o CSV do indice_master e constrói dict de lookup UC+mes → BB."""
    global _lookup_construido
    if _lookup_construido:
        return
    _lookup_construido = True
    from indice_master import MASTER_FILE
    import csv as _csv
    if not MASTER_FILE.exists():
        return
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with open(MASTER_FILE, newline="", encoding=enc) as f:
                for row in _csv.DictReader(f):
                    sist = (row.get("SISTEMA") or "").strip().upper()
                    if sist != SISTEMA_MASTER.upper():
                        continue
                    uc  = (row.get("UC") or "").strip()
                    ref = (row.get("MES_REF") or "").strip()
                    bb  = (row.get("INDICE") or "").strip()
                    if uc and ref and bb.startswith("BB_"):
                        _carimbo_lookup[(uc, ref)] = bb
            break
        except UnicodeDecodeError:
            continue
        except Exception:
            break


def _obter_carimbo(instalacao: str, mes_ref: dt.date | None, filename: str) -> str:
    """
    Retorna BB existente se já registrado para este sistema/UC/mes,
    caso contrário consome um novo carimbo e registra.
    """
    indice = _get_indice()
    ref_str = mes_ref.strftime("%m-%Y") if mes_ref else ""

    if instalacao and ref_str:
        _construir_lookup()
        existente = _carimbo_lookup.get((instalacao, ref_str))
        if existente:
            log.debug(f"  [carimbo] Reutilizando {existente} para {instalacao}/{ref_str}")
            return existente

    carimbo = indice.consumir_carimbo()
    if instalacao and ref_str:
        _carimbo_lookup[(instalacao, ref_str)] = carimbo
        try:
            indice.registrar(
                indice_bb=carimbo,
                sistema=SISTEMA_MASTER,
                uc=instalacao,
                mes_ref=ref_str,
                arquivo=filename,
                estado="PIAUÍ",
                concessionaria="Equatorial Piauí",
            )
        except Exception as exc:
            log.warning(f"  [indice_master] Falha ao registrar {filename}: {exc}")
    return carimbo


# =============================================================================
# PROCESSAMENTO DE UM PDF
# =============================================================================

def processar_pdf(pdf_path: str | Path) -> dict:
    """Extrai dados de uma fatura MT Equatorial Piauí."""
    filename = Path(pdf_path).name
    try:
        text = _extrair_texto(pdf_path)
        upper_text = _norm(text)

        # ── Campos de identificação ──────────────────────────────────────────
        instalacao_fmt = _instalacao_formatada_do_nome(filename)   # com pontos/traço → Consen
        instalacao     = _instalacao_do_nome(filename) or _instalacao_do_texto(text)  # dígitos → indice_master
        mes_ref    = _extrair_mes_ref(text)
        notafiscal = _extrair_nota_fiscal(text)
        cod_barras = _extrair_codigo_barras(text)
        val_fatura   = _extrair_valor_fatura(text)
        leit_ant, leit_at = _extrair_datas_leitura(text)
        data_vcto    = _extrair_data_vcto(text)
        data_emissao = _extrair_data_emissao(text)
        tarifa_det   = _detectar_tarifa(text)

        # ── Carimbo ──────────────────────────────────────────────────────────
        carimbo_bb = _obter_carimbo(instalacao, mes_ref, filename)
        carimbo_num = int(carimbo_bb.replace("BB_", "")) if carimbo_bb.startswith("BB_") else 0

        # ── Campos de faturamento ────────────────────────────────────────────
        dados = {h: "" for h in HEADERS_REF}
        dados.update(_extrair_itens(text))
        fiscal = _extrair_fiscal(text)
        icms_base = fiscal.pop("_icms_base", 0.0)
        dados.update(fiscal)
        dados.update(_extrair_retencoes(text))

        # ── Detecta se é "livre" (benefício tarifário ou escassez hídrica) ──
        _is_livre = bool(
            dados.get("fatBeneficioTarifarioBrutoValorReais")
            or dados.get("fatBeneficioLiquidoValorReais")
            or dados.get("fatEscassezHidricaValorReais")
        )

        # ── Desconto Fio B: 50% ou 46,95% conforme aparece na fatura ────────
        _fio_kw, _fio_kwh = _extrair_desconto_fio_b(text)
        if not _is_livre and (_fio_kw or _fio_kwh):
            dados["fatDescontoFio"]    = _fio_kw
            dados["fatDescontoFioKWh"] = _fio_kwh
        else:
            dados["fatDescontoFio"]    = 0.0
            dados["fatDescontoFioKWh"] = 0.0

        # ── Alíquotas de retenção (fixas por regulação CEPISA MT) ───────────
        if not _is_livre:
            dados["fatDescPisPercRetImposto"]    = 0.65
            dados["fatDescCofinsPercRetImposto"] = 3.0
            dados["fatDescCsllPercRetImposto"]   = 1.0
            dados["fatDescIrpjPercRetImposto"]   = -1.0
        else:
            # Livres com benefício/escassez: sem retenção
            for _campo in (
                "fatDescPisPercRetImposto",    "fatDescPisValRetImposto",
                "fatDescCofinsPercRetImposto", "fatDescCofinsValRetImposto",
                "fatDescCsllPercRetImposto",   "fatDescCsllValRetImposto",
                "fatDescIrpjPercRetImposto",   "fatDescIrpjValRetImposto",
            ):
                dados[_campo] = 0.0

        # ── Campos padronizados schema CEMIG ─────────────────────────────────
        dados["fatCarimbo"]         = carimbo_num
        dados["Instalacao"]         = instalacao_fmt or instalacao
        dados["CODIGOCLIENTE"]      = instalacao_fmt or instalacao
        dados["concCod"]            = CONC_COD
        dados["cadSubGrupoCod"]     = "A4 [2,3kV a 25kV]"
        dados["cadTarifaCod"]       = "Verde"
        dados["TARIFA_DETECTADA"]   = tarifa_det
        dados["NOTAFISCAL"]         = notafiscal
        dados["fatCodigoBarras"]    = cod_barras
        dados["fatValorFatura"]     = val_fatura
        # Valor NF = base de cálculo do ICMS (não o total da fatura)
        dados["fatValorNotaFiscal"] = icms_base if icms_base > 0 else val_fatura

        if mes_ref:
            dados["fatDataReferencia"] = mes_ref.strftime("%d/%m/%Y")
        if data_emissao:
            dados["fatDataEmissao"] = data_emissao
        if data_vcto:
            dados["fatDataVcto"] = data_vcto
        if leit_ant:
            dados["fatDataLeituraAnterior"] = leit_ant
        if leit_at:
            dados["fatDataLeituraAtual"] = leit_at

        # Consen: Registrado sempre espelha o Faturado para demanda e consumo
        dados["fatDemFPontaIndRegistrada"] = dados.get("fatDemFPontaIndFaturada") or dados.get("fatDemFPontaIndRegistrada") or 0.0
        if dados.get("fatConFPontaIndFaturado") and not dados.get("fatConFPontaIndRegistrado"):
            dados["fatConFPontaIndRegistrado"] = dados["fatConFPontaIndFaturado"]
        if dados.get("fatConPontaFaturado") and not dados.get("fatConPontaRegistrado"):
            dados["fatConPontaRegistrado"] = dados["fatConPontaFaturado"]

        dados["ARQUIVO"] = filename
        dados["ERRO"]    = ""

        log.info(
            f"  OK  {filename}  →  {tarifa_det}"
            f"  | carimbo {carimbo_bb}"
            f"  | FP={dados.get('fatConFPontaIndFaturado',0):.0f}kWh"
            f"  | Pta={dados.get('fatConPontaFaturado',0):.0f}kWh"
            f"  | Dem={dados.get('fatDemFPontaIndFaturada',0):.2f}kW"
            f"  | R$={val_fatura:.2f}"
        )
        return dados

    except Exception as exc:
        log.error(f"  ERRO  {filename}: {exc}", exc_info=True)
        return {
            "fatCarimbo": 0,
            "Instalacao": _instalacao_do_nome(filename),
            "concCod":    CONC_COD,
            "TARIFA_DETECTADA": "ERRO",
            "ARQUIVO":    filename,
            "ERRO":       str(exc),
        }


# =============================================================================
# PROCESSAMENTO EM LOTE
# =============================================================================

def processar_pasta(pasta: Path, xlsx_saida: Path) -> None:
    pdfs = sorted(pasta.glob("*.pdf"))
    if not pdfs:
        log.warning(f"Nenhum PDF encontrado em: {pasta}")
        return

    log.info(f"Processando {len(pdfs)} PDFs em: {pasta}")

    registros: list[dict] = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = {ex.submit(processar_pdf, p): p for p in pdfs}
        for f in as_completed(futures):
            registros.append(f.result())

    registros.sort(key=lambda r: int(r.get("fatCarimbo") or 0))
    salvar_excel(registros, xlsx_saida)
    log.info(f"Salvo: {xlsx_saida}  ({len(registros)} faturas)")


# =============================================================================
# CLI
# =============================================================================

def _parse_args() -> argparse.Namespace:
    hoje = dt.date.today()
    p = argparse.ArgumentParser(
        description="OCR Equatorial Piauí MT → XLSX (schema CEMIG/Consen)",
    )
    p.add_argument("--pasta",  type=str, default=str(PASTA_PDF_DEFAULT),
                   help="Pasta com os PDFs MT")
    p.add_argument("--saida",  type=str, default="",
                   help="Caminho do XLSX de saída (opcional)")
    p.add_argument("--mes",    type=str, default=f"{hoje.month:02d}")
    p.add_argument("--ano",    type=str, default=str(hoje.year))
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    pasta = Path(str(args.pasta).strip())
    if not pasta.exists():
        log.error(f"Pasta não encontrada: {pasta}")
        return 1

    mes = f"{int(args.mes):02d}"
    ano = str(int(args.ano))

    if args.saida:
        xlsx = Path(args.saida)
    else:
        PASTA_SAIDA_DEFAULT.mkdir(parents=True, exist_ok=True)
        xlsx = PASTA_SAIDA_DEFAULT / f"ocr_equatorial_pi_MT_{mes}{ano}.xlsx"

    # FileHandler
    xlsx.parent.mkdir(parents=True, exist_ok=True)
    fh = logging.FileHandler(
        xlsx.parent / "ocr_equatorial_pi_mt.log", encoding="utf-8", errors="replace",
    )
    fh.setFormatter(logging.Formatter(
        "%(asctime)s  %(levelname)-8s  %(message)s", datefmt="%Y-%m-%d %H:%M:%S",
    ))
    log.addHandler(fh)

    log.info("=" * 60)
    log.info("  OCR EQUATORIAL PIAUÍ — MT".center(60))
    log.info("=" * 60)
    log.info(f"  Pasta   : {pasta}")
    log.info(f"  Saída   : {xlsx}")
    log.info(f"  concCod : {CONC_COD}")

    processar_pasta(pasta, xlsx)
    log.info("Concluído.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
