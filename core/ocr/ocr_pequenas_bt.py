#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OCR unificado para concessionarias pequenas BT.

Baseado no layout NF3e observado em:
- DEMEI
- ELFSM (Santa Maria)
- Cermissoes
- Nova Palma Energia
- Coop. Eletricidade Jacinto Machado
- Coop. Regional Elet. Rural Front. Sul
- Cerbranorte (com regras especiais)
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import re
import sys
from dataclasses import dataclass
from pathlib import Path

LOCAL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LOCAL_DIR))

import pdfplumber

from ocr.ocr_neoenergia import (
    MAX_WORKERS,
    OUTPUT_DIR as NEO_OUTPUT_DIR,
    _empty_record,
    _extract_codigo_barras,
    _norm,
    _texto_normalizado,
    _to_date,
    _to_float_br,
    _uc_por_carimbo_master,
    salvar_excel,
)


OUTPUT_DIR = NEO_OUTPUT_DIR.parent / "OCR PEQUENAS"
DEFAULT_PASTA = Path("//10.10.250.21/Energia/CONTASDEENERGIAELETRICA/BB/ENZO")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("ocr_pequenas_bt")


@dataclass(frozen=True)
class Profile:
    key: str
    conc_cod: str
    family: str
    concessionaria: str
    cnpj: str
    estado: str
    matchers: tuple[str, ...]


PROFILES: tuple[Profile, ...] = (
    Profile("DEMEI", "DEMEI", "NF3E_RS_BASE", "DEMEI", "95289500000100", "RS", ("DEPTO MUNICIPAL DE ENERGIA DE IJUI",)),
    Profile("ELFSM", "ELFSM", "NF3E_ES_ELFSM", "Empresa Luz e Forca Santa Maria", "27485069000109", "ES", ("EMPRESA LUZ E FORCA SANTA MARIA", "ELFSM")),
    Profile("AMBAR_AM", "AMBAR_AM", "NF3E_AM_AMBAR", "Ambar Energia - AM", "02341467000120", "AM", ("AMBAR ENERGIA - AM", "AMBAR ENERGIA AM")),
    Profile("CERBRANORTE", "CERBRANORTE", "NF3E_SC_CERBRANORTE", "Coop Eletrificacao de Braco do Norte", "86433042000131", "SC", ("COOP ELETRIFICACAO DE BRACO DO NORTE",)),
    Profile("CERMISSOES", "CERMISSOES", "NF3E_RS_RETENCOES", "Coop. Distr. Ger. Energia Missoes", "97081434000103", "RS", ("COOP. DISTR. GER. ENERGIA MISSOES", "CERMISSOES")),
    Profile("NOVA_PALMA", "NOVA_PALMA", "NF3E_RS_RETENCOES", "Nova Palma Energia", "89889604000144", "RS", ("NOVA PALMA ENERGIA LTDA",)),
    Profile("COOPERJAM", "COOPERJAM", "NF3E_SC_RETENCOES", "Coop. de Eletricidade Jacinto Machado", "85665990000130", "SC", ("COOP. DE ELETRICIDADE JACINTO MACHADO",)),
    Profile("CERFRON", "CERFRON", "NF3E_RS_FRONTSUL", "Coop. Reg. Elet. Rural Front. Sul", "87462750000163", "RS", ("COOP. REG. ELET. RURAL FRONT. SUL LTDA",)),
)


def _digits(text: str) -> str:
    return re.sub(r"\D", "", str(text or ""))


def _carimbo_do_arquivo(pdf_path: Path) -> str:
    stem = pdf_path.stem.strip()
    m_bb = re.search(r"[Bb][Bb]_(\d+)", stem)
    return m_bb.group(0) if m_bb else stem


def _uc_do_nome(pdf_path: Path) -> str:
    return pdf_path.stem.split(" - ", 1)[0].strip()


def _normalizar_instalacao(raw: str) -> str:
    txt = _norm(str(raw or ""))
    if not txt:
        return ""
    txt = re.sub(r"\s+\d{2}\.\d{2}$", "", txt).strip()
    txt = re.sub(r"\s+", " ", txt)
    txt = txt.rstrip("-./ ")
    if txt.upper().startswith("BB_"):
        return ""
    return txt


def _first_page_text(pdf_path: Path) -> str:
    with pdfplumber.open(str(pdf_path)) as pdf:
        if not pdf.pages:
            return ""
        return pdf.pages[0].extract_text(x_tolerance=1, y_tolerance=1) or ""


def _detect_profile(text: str) -> Profile | None:
    txt = _texto_normalizado(text)
    dig = _digits(txt)
    for profile in PROFILES:
        if profile.cnpj and profile.cnpj in dig:
            return profile
        if any(token in txt for token in profile.matchers):
            return profile
    return None


def _extract_instalacao(text: str, pdf_path: Path) -> str:
    txt = _texto_normalizado(text)
    uc_nome = _normalizar_instalacao(_uc_do_nome(pdf_path))
    patterns = (
        r"UNIDADE\s+CONSUMIDORA\s+([0-9.\-]+)",
        r"IDENTIFICACAO\s*:\s*([0-9.\-]+)",
        # UC formato X.XXX-XX ou X.XXX.XXX-XX com pontuação — mais específico que "UC: <só dígitos>"
        r"\b(\d{1,5}\.\d{3}(?:\.\d{3})?-\d{2})\b",
        r"UC:\s*([0-9.\-]+)",
        r"UNIDADE\s+CONSUMIDORA\s*[:\-]?\s*([0-9.\-]+)",
        r"NUMERO\s+DA\s+UC\s+VENCIMENTO\s+MES\s+FATURADO\s+([0-9.\-]+)\s+\d{2}/\d{2}/\d{4}\s+\d{2}/\d{4}",
        r"COMPETENCIA\s+CONTA\s+L\.E\.\s+G\.F\.\s+N[O0]\s+FATURA\s+VENCIMENTO\s+TOTAL\s+A\s+PAGAR\s+[\d/]+\s+([0-9.\-]+)",
        r"DATA\s+DO\s+DOCUMENTO\s+NOSSO\s+NUMERO\s+DATA\s+PROCESSAMENTO\s+UNIDADE\s+CONSUMIDORA\s+REFERENCIA.*?\n[^\n]*\s([0-9.\-]+)\s+\d{2}/\d{2}/\d{4}",
    )
    for pat in patterns:
        m = re.search(pat, txt, flags=re.I | re.S)
        if m:
            candidato = _normalizar_instalacao(m.group(1))
            if candidato:
                if candidato.endswith("-") and uc_nome:
                    return uc_nome
                return candidato
    return uc_nome or _uc_do_nome(pdf_path)


def _extract_referencia(text: str, mes_padrao: int, ano_padrao: int) -> dt.date:
    txt = _texto_normalizado(text)
    for pat in (
        r"\b(0[1-9]|1[0-2])/(20\d{2})\s+\d{2}/\d{2}/\d{4}\s+R\$\s*[\d.,]+",
        r"MES/ANO\s*:\s*([A-Z]{3})/(20\d{2})",
    ):
        m = re.search(pat, txt)
        if not m:
            continue
        if len(m.groups()) == 2 and m.group(1).isdigit():
            return dt.date(int(m.group(2)), int(m.group(1)), 1)
        meses = {"JAN": 1, "FEV": 2, "MAR": 3, "ABR": 4, "MAI": 5, "JUN": 6, "JUL": 7, "AGO": 8, "SET": 9, "OUT": 10, "NOV": 11, "DEZ": 12}
        mes = meses.get(m.group(1)[:3], mes_padrao)
        return dt.date(int(m.group(2)), mes, 1)
    return dt.date(ano_padrao, mes_padrao, 1)


def _extract_vencimento(text: str) -> dt.date | None:
    txt = _texto_normalizado(text)
    for pat in (
        r"VENCIMENTO\s*:\s*(\d{2}/\d{2}/\d{4})",
        r"NUMERO\s+DA\s+UC\s+VENCIMENTO\s+MES\s+FATURADO\s+[0-9.\-]+\s+(\d{2}/\d{2}/\d{4})\s+\d{2}/\d{4}",
        r"\b(?:0[1-9]|1[0-2])/\d{4}\s+(\d{2}/\d{2}/\d{4})\s+R\$\s*[\d.,]+",
    ):
        m = re.search(pat, txt)
        if m:
            return _to_date(m.group(1))
    return None


def _extract_emissao(text: str) -> dt.date | None:
    txt = _texto_normalizado(text)
    # Aceita "EMISSAO:" e "EMISSAO :" (ELFSM tem espaço antes dos dois-pontos)
    m = re.search(r"(?:DATA\s+DE\s+)?EMISSAO\s*:\s*(\d{2}/\d{2}/\d{4})", txt)
    return _to_date(m.group(1)) if m else None


def _clean_date_str(raw: str) -> str:
    """Remove espaços internos nos dígitos da data: '2 2/04/2026' → '22/04/2026'."""
    return re.sub(r"(\d)\s+(\d)", r"\1\2", raw.strip())


def _extract_leituras(text: str) -> tuple[dt.date | None, dt.date | None]:
    txt = _texto_normalizado(text)
    DATE = r"\d\s?\d/\d{2}/\d{4}"  # aceita espaço interno no dia (ELFSM)
    patterns = (
        # Layout RS/SC cooperativas: "DATAS DE <endereço interrompido> LEITURAS ant atu n prox"
        (r"\bLEITURAS\s+(" + DATE + r")\s+(" + DATE + r")\s+\d{1,3}\s+\d\s?\d/\d{2}/\d{4}", False),
        # Layout NF3E sem interrupção
        (r"DATAS\s+DE\s+LEITURAS\s+(" + DATE + r")\s+(" + DATE + r")\s+\d{1,3}\s+\d\s?\d/\d{2}/\d{4}", False),
        # Layout ORIGEM LEITURA (Nova Palma, Cooperjam, Cerbranorte)
        (r"ORIGEM\s+LEITURA\s+LEITURA\s+ANTERIOR\s+LEITURA\s+ATUAL\s+N[O]\s+DE\s+DIAS.*?\s(" + DATE + r")\s+(" + DATE + r")\s+\d{1,3}", False),
        # Layout ELFSM: "DATAS DE LEITURAS : ATUAL : DD/MM/YYYY ANTERIOR : DD/MM/YYYY"
        (r"DATAS\s+DE\s+LEITURAS\s*:\s*ATUAL\s*:\s*(" + DATE + r")\s+ANTERIOR\s*:\s*(" + DATE + r")", True),
        # Layout AMBAR: "Leitura Anterior Leitura Atual Próxima Leitura"
        (r"LEITURA\s+ANTERIOR\s+LEITURA\s+ATUAL\s+PROXIMA\s+LEITURA\s+(" + DATE + r")\s+(" + DATE + r")\s+" + DATE, False),
    )
    for pat, swap_atual_ant in patterns:
        m = re.search(pat, txt, flags=re.I | re.S)
        if not m:
            continue
        d1 = _to_date(_clean_date_str(m.group(1)))
        d2 = _to_date(_clean_date_str(m.group(2)))
        if swap_atual_ant:
            # grupo 1 = ATUAL, grupo 2 = ANTERIOR → retorna (ant, atu)
            return d2, d1
        return d1, d2
    return None, None


def _extract_total(text: str) -> float:
    txt = _texto_normalizado(text)
    for pat in (
        r"TOTAL\s+A\s+PAGAR\s*:\s*R\$\s*([\d.,]+)",
        r"\b(?:0[1-9]|1[0-2])/\d{4}\s+\d{2}/\d{2}/\d{4}\s+R\$\s*([\d.,]+)",
    ):
        m = re.search(pat, txt)
        if m:
            return abs(_to_float_br(m.group(1)))
    # Fallback para layouts em que o pdfplumber perde o valor visual do total,
    # mas preserva a linha digitavel do boleto ao final da fatura.
    m_linha = re.search(
        r"\b\d{5}\.\d{5}\s+\d{5}\.\d{6}\s+\d{5}\.\d{6}\s+\d\s+(\d{10,11})\b",
        txt,
    )
    if m_linha:
        bruto = re.sub(r"\D", "", m_linha.group(1))
        if bruto.isdigit():
            return int(bruto) / 100.0
    codigo_barras = _digits(_extract_codigo_barras(text))
    if len(codigo_barras) >= 14:
        valor_boleto = codigo_barras[-10:]
        if valor_boleto.isdigit() and int(valor_boleto) > 0:
            return int(valor_boleto) / 100.0
    return 0.0


def _extract_nota_fiscal(text: str) -> str:
    txt = _texto_normalizado(text)
    for pat in (
        # NF3E NO: XXXX (ELFSM) — "Nº" pode virar "NO", "N0" ou "N" após normalização
        r"NF3E\s+N[O0]?\s*:?\s*(\d+)",
        # Padrão geral: NOTA FISCAL NO / N0 / N (grau strip em alguns PDFs)
        r"NOTA\s+FISCAL\s+N[O0]?\s+(\d+)",
    ):
        m = re.search(pat, txt)
        if m:
            return m.group(1)
    return ""


_SUBGRUPO_MAP = {
    "A4":  "A4 [2,3kV a 25kV]",
    "A3A": "A3a [30kV a 44kV]",
    "A3":  "A3 [44kV]",
    "A2":  "A2 [88kV a 138kV]",
    "A1":  "A1 [>= 230kV]",
}

_TARIFA_MAP = {
    "HORARIA VERDE": "Horária Verde",
    "HORARIA AZUL":  "Horária Azul",
    "CONVENCIONAL":  "Convencional",
}


def _extract_classificacao(text: str) -> tuple[str, str, str]:
    txt = _texto_normalizado(text)
    subgrupo = "B3 [<2,3kV]"
    tarifa = "Convencional"
    concilia = "B3"
    m = re.search(r"CLASSIFICACAO:\s*([AB]\d[Aa]?)", txt)
    if m and m.group(1).upper().startswith("A"):
        codigo = m.group(1).upper()
        subgrupo = _SUBGRUPO_MAP.get(codigo, codigo)
        concilia = codigo
    m = re.search(r"MODALIDADE\s+TARIFARIA:\s*([A-Z\s]+?)(?:TRIFASICO|MONOFASICO|BIFASICO|$)", txt)
    if m:
        raw_tarifa = _norm(m.group(1)).upper().strip()
        tarifa = _TARIFA_MAP.get(raw_tarifa, _norm(m.group(1).title()).strip()) or tarifa
    return subgrupo, tarifa, concilia


def _extract_ilum_publica(text: str) -> float:
    txt = _texto_normalizado(text)
    patterns = (
        r"C\.I\.P\.\s*-\s*CONT\.\s+ILUM\.\s+PUBLICA\s+MUNICIPAL\s+\d+\s+([\d.,]+)",
        r"CUSTEIO\s+DE\s+ILUM\.\s+PUBLICA\s+MUNICIPAL\s+\d+\s+([\d.,]+)",
        r"CONTRIBUICAO\s+P/\s*ILUM\.\s+PUBLICA\s+MUNICIPAL\s+\d+\s+([\d.,]+)",
        r"CONTR\s+IL\s+PUB\s+MUNIC\s+UN\s+[\d.,]+\s+([\d.,]+)",
        r"COSIP\s+MUNICIPAL\s+\d+\s+([\d.,]+)",
    )
    for pat in patterns:
        m = re.search(pat, txt, flags=re.I)
        if m:
            return abs(_to_float_br(m.group(1)))
    return 0.0


def _extract_consumo_generic(text: str) -> dict[str, float]:
    txt = _texto_normalizado(text)
    out: dict[str, float] = {}
    consumo_patterns = (
        r"CONSUMO\s+([\d.,]+)\s+KWH\s+A\s+[\d.,]+\s+[\d.,]+\s+([\d.,]+)",
        r"CONSUMO\s+kWh\s+([\d.,]+)\s+[\d.,]+\s+([\d.,]+)",
        r"CONSUMO\s+KWH\s+([\d.,]+)\s+[\d.,]+\s+([\d.,]+)",
        r"ENERGIA\s+ATIVA\s+KWH\s+([\d.,]+)\s+[\d.,]+\s+([\d.,]+)",
        r"ENERGIA\s+ATIVA\s+FORN\s+CONV\s+KWH\s+([\d.,]+)\s+[\d.,]+\s+([\d.,]+)",
        r"CONSUMO\s+FATURADO\s+N[ºO]\s+DIAS\s+FAT.*?\n05/\d{4}\s+([\d.,]+)\s+\d{1,3}",
    )
    qtd = 0.0
    valor = 0.0
    for pat in consumo_patterns:
        m = re.search(pat, txt, flags=re.I | re.S)
        if not m:
            continue
        qtd = abs(_to_float_br(m.group(1)))
        if len(m.groups()) >= 2:
            valor = abs(_to_float_br(m.group(2)))
        break

    m_band = re.search(r"(?:ADICIONAL|ACRESCIMO)\s+BANDEIRA\s+[A-Z]+\s+[\d.,]+\s+[\d.,]+\s+([\d.,]+)", txt, flags=re.I)
    val_bandeira = abs(_to_float_br(m_band.group(1))) if m_band else 0.0

    if qtd > 0:
        out["fatConFPontaIndRegistrado"] = qtd
        out["fatConFPontaIndFaturado"] = qtd
        out["fatConFPontaIndValorReais"] = round(valor + val_bandeira, 2)
        if valor > 0:
            out["fatValorNotaFiscal"] = round(valor, 2)
    if val_bandeira > 0:
        out["fatValBandeira"] = val_bandeira
    return out


def _extract_consumo_cerbranorte(text: str) -> dict[str, float]:
    txt = _texto_normalizado(text)
    out: dict[str, float] = {}

    m_ponta = re.search(r"CONSUMO\s+KWH\s+PONTA\s+LIVRE\s+([\d.,-]+)\s+[\d.,]+\s+([\d.,-]+)", txt, flags=re.I)
    m_fora = re.search(r"CONSUMO\s+KWH\s+FORA\s+PONTA\s+LIVRE\s+([\d.,-]+)\s+[\d.,]+\s+([\d.,-]+)", txt, flags=re.I)
    m_conv = re.search(r"ENERGIA\s+ATIVA\s+FORN\s+CONV\s+KWH\s+([\d.,-]+)\s+[\d.,]+\s+([\d.,-]+)", txt, flags=re.I)
    m_band = re.search(r"(?:ADICIONAL|ACRESCIMO)\s+BANDEIRA\s+[A-Z]+\s+[\d.,]+\s+[\d.,]+\s+([\d.,-]+)", txt, flags=re.I)
    val_bandeira = abs(_to_float_br(m_band.group(1))) if m_band else 0.0

    if m_ponta or m_fora:
        qtd_p = abs(_to_float_br(m_ponta.group(1))) if m_ponta else 0.0
        val_p = abs(_to_float_br(m_ponta.group(2))) if m_ponta else 0.0
        qtd_fp = abs(_to_float_br(m_fora.group(1))) if m_fora else 0.0
        val_fp = abs(_to_float_br(m_fora.group(2))) if m_fora else 0.0
        if qtd_p > 0:
            out["fatConPontaRegistrado"] = qtd_p
            out["fatConPontaFaturado"] = qtd_p
            out["fatConPontaValorReais"] = val_p
        if qtd_fp > 0:
            out["fatConFPontaIndRegistrado"] = qtd_fp
            out["fatConFPontaIndFaturado"] = qtd_fp
            out["fatConFPontaIndValorReais"] = round(val_fp + val_bandeira, 2)
        return out

    if m_conv:
        qtd = abs(_to_float_br(m_conv.group(1)))
        val = abs(_to_float_br(m_conv.group(2)))
        out["fatConFPontaIndRegistrado"] = qtd
        out["fatConFPontaIndFaturado"] = qtd
        out["fatConFPontaIndValorReais"] = round(val + val_bandeira, 2)
        if val_bandeira > 0:
            out["fatValBandeira"] = val_bandeira
    return out


def _extract_tributos(text: str) -> dict[str, float]:
    txt = _texto_normalizado(text)
    out = {
        "fatICMS": 0.0,
        "fatPIS": 0.0,
        "fatCOFINS": 0.0,
        "fatDesIcmsAliquota": 0.0,
        "fatDescPisAliquota": 0.0,
        "fatDesCofinsAliquota": 0.0,
        "fatValorNotaFiscal": 0.0,
    }

    # Layout ELFSM: tributos em seção própria "TRIBUTOS BASE DE CALCULO ALIQUOTA VALOR - R$ PIS X,XX Y,YY % ZZ,ZZ"
    # ICMS aparece na tabela de itens: "... ICMS % CONSUMO KWH ... aliq icms_val"
    if "NF3E" in txt and "ELFSM" not in txt:
        # detecta ELFSM pelo layout: "TRIBUTOS BASE DE CALCULO ALIQUOTA VALOR - R$"
        pass
    if re.search(r"TRIBUTOS\s+BASE\s+DE\s+CALCULO\s+ALIQUOTA\s+VALOR", txt):
        # ELFSM: "PIS base aliq % valor" e "COFINS base aliq % valor"
        m_pis = re.search(r"\bPIS\s+([\d.,]+)\s+([\d.,]+)\s*%\s+([\d.,]+)", txt)
        if m_pis:
            out["fatDescPisAliquota"] = abs(_to_float_br(m_pis.group(2)))
            out["fatPIS"] = abs(_to_float_br(m_pis.group(3)))
        m_cof = re.search(r"\bCOFINS\s+([\d.,]+)\s+([\d.,]+)\s*%\s+([\d.,]+)", txt)
        if m_cof:
            out["fatDesCofinsAliquota"] = abs(_to_float_br(m_cof.group(2)))
            out["fatCOFINS"] = abs(_to_float_br(m_cof.group(3)))
        # ICMS % e valor no final da linha de CONSUMO: "... icms_pct icms_val 0,XXXXX"
        m_icms_el = re.search(
            r"CONSUMO\s+KWH\s+[\d.,]+\s+[\d.,]+\s+[\d.,]+\s+[\d.,]+\s+[\d.,]+\s+([\d.,]+)\s+([\d.,]+)\b",
            txt,
        )
        if m_icms_el:
            out["fatDesIcmsAliquota"] = abs(_to_float_br(m_icms_el.group(1)))
            out["fatICMS"] = abs(_to_float_br(m_icms_el.group(2)))
        # Nota fiscal = base de cálculo do ICMS (=valor consumo sem ICMS)
        m_nf_el = re.search(r"\bICMS\s+([\d.,]+)\s+([\d.,]+)\s+([\d.,]+)", txt)
        if m_nf_el:
            out["fatValorNotaFiscal"] = abs(_to_float_br(m_nf_el.group(1)))
        return out

    # Layout padrão NF3E cooperativas RS/SC:
    # "ICMS base aliq valor" na coluna de tributos
    m_icms = re.search(r"\bICMS\s+([\d.,]+)\s+([\d.,]+)\s+([\d.,]+)", txt)
    if m_icms:
        out["fatValorNotaFiscal"] = abs(_to_float_br(m_icms.group(1)))
        out["fatDesIcmsAliquota"] = abs(_to_float_br(m_icms.group(2)))
        out["fatICMS"] = abs(_to_float_br(m_icms.group(3)))

    m_pis = re.search(r"PIS(?:/PASEP)?\s+([\d.,]+)\s+([\d.,]+)\s+([\d.,]+)", txt)
    if m_pis:
        out["fatDescPisAliquota"] = abs(_to_float_br(m_pis.group(2)))
        out["fatPIS"] = abs(_to_float_br(m_pis.group(3)))

    m_cof = re.search(r"COFINS\s+([\d.,]+)\s+([\d.,]+)\s+([\d.,]+)", txt)
    if m_cof:
        out["fatDesCofinsAliquota"] = abs(_to_float_br(m_cof.group(2)))
        out["fatCOFINS"] = abs(_to_float_br(m_cof.group(3)))
    return out


_TRIB_FEDERAL_BREAKDOWN = {
    5.85: {
        "fatDescIrpjPercRetImposto": 1.20,
        "fatDescPisPercRetImposto": 0.65,
        "fatDescCofinsPercRetImposto": 3.00,
        "fatDescCsllPercRetImposto": 1.00,
    },
    9.45: {
        "fatDescIrpjPercRetImposto": 4.80,
        "fatDescPisPercRetImposto": 0.65,
        "fatDescCofinsPercRetImposto": 3.00,
        "fatDescCsllPercRetImposto": 1.00,
    },
}


def _distribuir_tributo_federal(valor_total: float, perc_total: float) -> dict[str, float]:
    out: dict[str, float] = {}
    if valor_total <= 0 or perc_total <= 0:
        return out
    breakdown = _TRIB_FEDERAL_BREAKDOWN.get(round(perc_total, 2))
    if not breakdown:
        return out
    for campo_perc, comp_perc in breakdown.items():
        campo_val = campo_perc.replace("PercRetImposto", "ValRetImposto")
        out[campo_perc] = comp_perc
        out[campo_val] = -round(valor_total * comp_perc / perc_total, 2)
    return out


def _extract_tributo_federal_total(text: str, valor_nota: float = 0.0) -> tuple[float, float]:
    txt = _texto_normalizado(text)
    m = re.search(
        r"RETENCAO\s+IMP\.\s+FEDERAIS\s*-\s*LEI\s+10\.833/03\s+GRUPO\s+%\s+\d+\s+\(-\)\s+(-?[\d.,]+)",
        txt,
        flags=re.I,
    )
    if not m:
        return 0.0, 0.0
    valor = abs(_to_float_br(m.group(1)))
    if valor <= 0:
        return 0.0, 0.0

    if valor_nota > 0:
        perc_estimado = round((valor / valor_nota) * 100.0, 2)
        for perc_ref in _TRIB_FEDERAL_BREAKDOWN:
            if abs(perc_estimado - perc_ref) <= 0.15:
                return perc_ref, valor
        return perc_estimado, valor
    return 0.0, valor


def _extract_multas_ambar(text: str) -> tuple[float, float]:
    txt = _texto_normalizado(text)
    def _capturar(label: str) -> float:
        m = re.search(rf"{label}.*?(\d+/\d{{2}}-\d{{2}})\s+(-?[\d.,]+)", txt, flags=re.I)
        if not m:
            return 0.0
        return abs(_to_float_br(m.group(2)))

    multa = _capturar(r"MULTA\s+POR\s+ATRASO")
    outras = _capturar(r"CORRECAO\s+MONETARIA") + _capturar(r"JUROS\s+DE\s+MORA(?:\s+DE\s+IMPORTE/SERVICO)?")
    return round(multa, 2), round(outras, 2)


def _extract_retencoes(text: str) -> dict[str, float]:
    txt = _texto_normalizado(text)
    out = {
        "fatDescCsllPercRetImposto": 0.0,
        "fatDescCsllValRetImposto": 0.0,
        "fatDescIrpjPercRetImposto": 0.0,
        "fatDescIrpjValRetImposto": 0.0,
        "fatDescCofinsPercRetImposto": 0.0,
        "fatDescCofinsValRetImposto": 0.0,
        "fatDescPisPercRetImposto": 0.0,
        "fatDescPisValRetImposto": 0.0,
    }

    aliases = {
        "CSLL": ("fatDescCsllPercRetImposto", "fatDescCsllValRetImposto", 1.0),
        "IRPJ": ("fatDescIrpjPercRetImposto", "fatDescIrpjValRetImposto", 1.2),
        "IRRF": ("fatDescIrpjPercRetImposto", "fatDescIrpjValRetImposto", 1.2),
        "COFINS": ("fatDescCofinsPercRetImposto", "fatDescCofinsValRetImposto", 3.0),
        "PIS": ("fatDescPisPercRetImposto", "fatDescPisValRetImposto", 0.65),
        "PIS-PA": ("fatDescPisPercRetImposto", "fatDescPisValRetImposto", 0.65),
    }

    for label, (campo_perc, campo_val, perc_padrao) in aliases.items():
        patterns = (
            rf"RETENCAO\s+{label}\s+(-?[\d.,]+)",
            rf"RET\s+INSRF\s+480\s+{label}\s+(-?[\d.,]+)",
            rf"RETENCAO\s+{label}[^\n]*?([\d.,]+%)\s+(-?[\d.,]+)",
            rf"RETENCAO\s+{label}[^\n]*?(-?[\d.,]+)",
            rf"\b{label}\s+(-?[\d.,]+)\s+-0,00\s+-0,00",
        )
        for pat in patterns:
            m = re.search(pat, txt, flags=re.I)
            if not m:
                continue
            if len(m.groups()) == 2 and "%" in m.group(1):
                out[campo_perc] = abs(_to_float_br(m.group(1).replace("%", "")))
                out[campo_val] = -abs(_to_float_br(m.group(2)))
            else:
                out[campo_perc] = out[campo_perc] or perc_padrao
                out[campo_val] = -abs(_to_float_br(m.group(len(m.groups()))))
            break
    return out


def identificacao_rapida(pdf_path: Path) -> dict:
    resultado = {"sistema": "DESCONHECIDA", "instalacao": "", "mes_ref": "", "grupo": "B", "family": ""}
    try:
        text = _first_page_text(pdf_path)
        profile = _detect_profile(text)
        if not profile:
            return resultado
        resultado["sistema"] = profile.key
        resultado["family"] = profile.family
        resultado["instalacao"] = _extract_instalacao(text, pdf_path)
        ref = _extract_referencia(text, dt.date.today().month, dt.date.today().year)
        resultado["mes_ref"] = ref.strftime("%m-%Y")
        grupo = "A" if re.search(r"CLASSIFICACAO:\s*A\d", _texto_normalizado(text)) else "B"
        resultado["grupo"] = grupo
    except Exception as exc:
        log.warning("  identificacao_rapida %s: %s", pdf_path.name, exc)
    return resultado


def processar_pdf_direto(pdf_path: Path, mes_padrao: int, ano_padrao: int) -> dict:
    rec = _empty_record()
    rec["ARQUIVO"] = pdf_path.name
    rec["fatCarimbo"] = _carimbo_do_arquivo(pdf_path)
    rec["fatDataCadastro"] = dt.date.today()

    try:
        with pdfplumber.open(str(pdf_path)) as pdf:
            text = "\n".join((page.extract_text(x_tolerance=1, y_tolerance=1) or "") for page in pdf.pages[:3])
    except Exception as exc:
        rec["ERRO"] = f"{type(exc).__name__}: {exc}"
        return rec

    if not text.strip():
        rec["ERRO"] = "PDF sem texto extraivel"
        return rec

    profile = _detect_profile(text)
    if not profile:
        rec["ERRO"] = "Nao identificado como pequena concessionaria suportada"
        return rec

    subgrupo, tarifa, tarifa_detectada = _extract_classificacao(text)
    rec["concCod"] = profile.conc_cod
    rec["cadTarifaCod"] = tarifa
    rec["cadSubGrupoCod"] = subgrupo
    rec["TARIFA_DETECTADA"] = tarifa_detectada
    instalacao = _extract_instalacao(text, pdf_path)
    if not instalacao or instalacao.upper().startswith("BB_"):
        instalacao_master = _norm(_uc_por_carimbo_master(rec["fatCarimbo"]))
        if instalacao_master:
            instalacao = instalacao_master
    # ELFSM: instalações são 6 dígitos; o OCR às vezes omite o zero à esquerda
    if profile.key == "ELFSM":
        digits_only = re.sub(r"\D", "", instalacao)
        if digits_only and len(digits_only) < 6:
            instalacao = digits_only.zfill(6)
        elif digits_only:
            instalacao = digits_only
    rec["Instalacao"] = instalacao
    rec["CODIGOCLIENTE"] = rec["Instalacao"]
    rec["NOTAFISCAL"] = _extract_nota_fiscal(text)
    rec["CNPJ"] = profile.cnpj
    rec["fatDataReferencia"] = _extract_referencia(text, mes_padrao, ano_padrao)
    rec["fatDataVcto"] = _extract_vencimento(text)
    rec["fatDataEmissao"] = _extract_emissao(text)
    leitura_ant, leitura_atu = _extract_leituras(text)
    rec["fatDataLeituraAnterior"] = leitura_ant
    rec["fatDataLeituraAtual"] = leitura_atu
    rec["fatValorFatura"] = _extract_total(text)
    rec["fatIlumPublica"] = _extract_ilum_publica(text)

    if profile.key == "CERBRANORTE":
        rec.update(_extract_consumo_cerbranorte(text))
    else:
        rec.update(_extract_consumo_generic(text))
    tributos = _extract_tributos(text)
    for campo, valor in tributos.items():
        if valor or not rec.get(campo):
            rec[campo] = valor
    trib_perc, trib_val = _extract_tributo_federal_total(text, float(rec.get("fatValorNotaFiscal") or 0.0))
    if trib_val > 0:
        rec["fatTributoFederalVal"] = trib_val
    if trib_perc > 0:
        rec["fatTributoFederalPerc"] = trib_perc
        if profile.key == "AMBAR_AM":
            # CONSEN da Amazonas usa campo consolidado de consumo, não desdobramento PIS/COFINS/CSLL/IRPJ
            rec["fatDescConsumoPercRetImposto"] = trib_perc
            rec["fatDescConsumoValRetImposto"] = -round(trib_val, 2)
        else:
            rec.update(_distribuir_tributo_federal(trib_val, trib_perc))
    # CONSEN da Amazonas rejeita ICMS=0; usa 0,01 como placeholder (ICMS recolhido via ST)
    if profile.key == "AMBAR_AM":
        rec["fatICMS"] = 0.01
    multa, multas_diversas = _extract_multas_ambar(text)
    if multa > 0:
        rec["fatMultas"] = multa
    if multas_diversas > 0:
        rec["fatMultasDiversas"] = multas_diversas
    if not rec.get("fatValorNotaFiscal") and rec.get("fatConFPontaIndValorReais"):
        rec["fatValorNotaFiscal"] = rec.get("fatConFPontaIndValorReais")
    retencoes = _extract_retencoes(text)
    for campo, valor in retencoes.items():
        if valor or not rec.get(campo):
            rec[campo] = valor

    codigo_barras = _extract_codigo_barras(text)
    rec["fatCodigoBarras"] = _digits(codigo_barras) if len(_digits(codigo_barras)) >= 44 else ""
    rec["ENDERECO"] = _norm("")
    rec["ERRO"] = ""
    rec["SISTEMA_ORIGEM"] = profile.key
    rec["LAYOUT_FAMILIA"] = profile.family
    return rec


def _listar_pdfs(pasta: Path, carimbos: set[str]) -> list[Path]:
    pdfs = sorted(p for p in pasta.glob("*.pdf") if p.is_file())
    if carimbos:
        norm = {str(c).strip().upper() for c in carimbos}
        pdfs = [p for p in pdfs if p.stem.upper() in norm]
    return pdfs


def _xlsx_saida(mes: int, ano: int) -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    return OUTPUT_DIR / f"ocr_pequenas_BT_{mes:02d}{ano}.xlsx"


def parse_args() -> argparse.Namespace:
    hoje = dt.date.today()
    parser = argparse.ArgumentParser(description="OCR PEQUENAS BT -> XLSX")
    parser.add_argument("--mes", type=int, default=hoje.month)
    parser.add_argument("--ano", type=int, default=hoje.year)
    parser.add_argument("--pasta", type=str, default=str(DEFAULT_PASTA))
    parser.add_argument("--saida", type=str, default="")
    parser.add_argument("--carimbo", action="append", default=[])
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    pasta = Path(str(args.pasta).strip())
    if not pasta.exists():
        log.error("Pasta nao encontrada: %s", pasta)
        return 1

    carimbos = {str(c).strip().upper() for c in (args.carimbo or []) if str(c).strip()}
    destino = Path(str(args.saida).strip()) if str(args.saida).strip() else _xlsx_saida(int(args.mes), int(args.ano))
    destino.parent.mkdir(parents=True, exist_ok=True)

    pdfs = _listar_pdfs(pasta, carimbos)
    if not pdfs:
        log.warning("Nenhum PDF encontrado em %s", pasta)
        return 0

    registros: list[dict] = []
    ignorados = 0
    # Pequenas concessionarias costumam vir em lotes modestos. Aqui
    # privilegiamos determinismo do XLSX sobre paralelismo agressivo.
    for pdf in pdfs:
        rec = processar_pdf_direto(pdf, int(args.mes), int(args.ano))
        if rec.get("ERRO") == "Nao identificado como pequena concessionaria suportada":
            ignorados += 1
            continue
        registros.append(rec)
        log.info("  OK  %s", pdf.name)

    if not registros:
        log.warning("Nenhuma fatura de pequenas concessionarias extraida. Ignorados=%d", ignorados)
        return 0

    registros.sort(key=lambda r: str(r.get("fatCarimbo", "")))
    salvar_excel(registros, destino, titulo="OCR_PEQUENAS_BT")
    log.info("  OK=%d | Ignorados=%d", len(registros), ignorados)
    log.info("Saida: %s", destino)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
