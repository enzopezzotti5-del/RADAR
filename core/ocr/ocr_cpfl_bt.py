#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OCR CPFL BT (Companhia Paulista de Forca e Luz)
-> XLSX para digitacao no Consen.

Identificacao: "COMPANHIA PAULISTA DE FORCA E LUZ"
               CNPJ: 33.050.196/0001-88

Suporta dois subtipos:
  - Convencional B3: Consumo Uso Sistema (unico posto)
  - Tarifa Branca B3: Ponta + Intermediario + Fora Ponta
  Ambos entram como 'Convencional' no Consen (campo fora ponta unico).
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

LOCAL_DIR = Path(__file__).resolve().parent.parent
ROOT_DIR = LOCAL_DIR.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))
sys.path.insert(0, str(LOCAL_DIR))

import pdfplumber

from ocr.ocr_neoenergia import (
    MAX_WORKERS,
    OUTPUT_DIR as NEO_OUTPUT_DIR,
    _empty_record,
    _texto_normalizado,
    _to_date,
    _to_float_br,
    salvar_excel,
)

OUTPUT_DIR    = NEO_OUTPUT_DIR.parent / "OCR CPFL"
DEFAULT_PASTA = Path("//10.10.250.21/Energia/CONTASDEENERGIAELETRICA/BB/ENZO")

CNPJ_CPFL = "33050196000188"
CARIMBO_BB_RE = re.compile(r"(?i)\bBB_(\d{7})\b")

MESES_PT = {
    "JAN": "01", "FEV": "02", "MAR": "03", "ABR": "04",
    "MAI": "05", "JUN": "06", "JUL": "07", "AGO": "08",
    "SET": "09", "OUT": "10", "NOV": "11", "DEZ": "12",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("ocr_cpfl_bt")


def _digits(text: str) -> str:
    return re.sub(r"\D", "", str(text or ""))


def _is_cpfl(txt: str) -> bool:
    return (
        "COMPANHIA PAULISTA DE FORCA E LUZ" in txt
        or "CPFL" in txt
        or CNPJ_CPFL in _digits(txt)
        or "02328280000197" in _digits(txt)  # CPFL Piratininga/subsidiaria
    )


def _extract_instalacao(txt: str) -> str:
    """
    Prioridade (da mais específica para a mais genérica):
    1. "Número da UC" no formato pontuado X.XXX.XXX.XXX-XX (REN ANEEL 1095/24 — novo código)
       Aparece como: "BANCO DO BRASIL 1.402.183.035-40" ou após label "Número da UC"
    2. Layout NF3e com datas: '... 20015690 04/05/2026 01/04/2026 33 ...'
    3. Layout antigo: 'www.cpfl.com.br 19397232 ...'
    4. INSTALACAO label
    5. "BANCO DO BRASIL SA 12532460 HTTPS://..."
    6. NF3e cabeçalho: "01 SAABU001-00000203 30941628 1/3 ..." (Nº Medidor — último recurso)
    """
    # 1. Número da UC com formato pontuado (X.XXX.XXX.XXX-XX ou X.XXX.XXX-XX)
    # Aparece na linha do cliente: "BANCO DO BRASIL 1.402.183.035-40"
    m = re.search(r"\b(\d{1,3}\.\d{3}\.\d{3}\.\d{3}-\d{2})\b", txt)
    if m:
        return m.group(1)
    # Formato mais curto: X.XXX.XXX-XX (UCs com prefixo 1-3 dígitos)
    m = re.search(r"\b(\d{1,3}\.\d{3}\.\d{3}-\d{2})\b", txt)
    if m:
        return m.group(1)

    # 2. Layout com datas de leitura: UC_NUM DATA_ATU DATA_ANT N_DIAS
    m = re.search(r"\b(\d{5,10})\s+\d{2}/\d{2}/\d{4}\s+\d{2}/\d{2}/\d{4}\s+\d{1,3}\b", txt)
    if m:
        return m.group(1)
    # Layout antigo: UC aparece apos 'cpfl.com.br'
    m = re.search(r"cpfl\.com\.br\s+(\d{5,10})\b", txt, re.I)
    if m:
        return m.group(1)
    m = re.search(r"INSTALACAO[:\s]+(\d+)", txt)
    if m:
        return m.group(1)
    # Layout NF3e: "BANCO DO BRASIL SA 12532460 HTTPS://..."
    m = re.search(r"\bBANCO DO BRASIL\b\s+(?:SA\b\s+)?(\d{7,10})\b", txt, re.I)
    if m:
        return m.group(1)
    # NF3e CPFL recente: "01 SAABU001-00000203 30941628 1/3 16/07/2026 ..."
    # ATENÇÃO: captura Nº Medidor (não UC) — usado apenas como último recurso
    m = re.search(
        r"\b\d{2}\s+[A-Z0-9-]{6,}\s+(\d{7,10})\s+\d/\d\s+\d{2}/\d{2}/\d{4}\s+\d{2}/\d{2}/\d{4}\s+\d{2}/\d{2}/\d{4}",
        txt,
        re.I,
    )
    if m:
        return m.group(1)
    return ""


def _mes_ref_master(data_ref: object) -> str:
    if hasattr(data_ref, "month") and hasattr(data_ref, "year"):
        return f"{int(data_ref.month):02d}-{int(data_ref.year)}"
    txt = str(data_ref or "").strip()
    if re.fullmatch(r"\d{2}-\d{4}", txt):
        return txt
    if re.fullmatch(r"\d{2}/\d{4}", txt):
        return txt[:2] + "-" + txt[3:]
    return txt


def _resolver_carimbo_master(filename: str, instalacao: object, data_ref: object) -> str:
    stem = Path(filename).stem
    m_bb = CARIMBO_BB_RE.search(stem)
    if m_bb:
        return f"BB_{m_bb.group(1)}"
    if re.search(r"(?i)\bBB_\d+\b", stem):
        raise ValueError(f"Carimbo BB invalido no nome do arquivo: {filename}")

    uc = str(instalacao or "").strip()
    mes_ref = _mes_ref_master(data_ref)
    if not uc or not mes_ref:
        raise ValueError(
            f"Nao foi possivel resolver carimbo BB para {filename}: "
            f"instalacao={uc!r}, mes_ref={mes_ref!r}"
        )

    from indice_master import MasterIndice

    master = MasterIndice()
    if not master.ja_foi_baixado(uc, mes_ref, "CPFL"):
        novo = master.consumir_carimbo()
        master.registrar(
            indice_bb=novo,
            sistema="CPFL",
            uc=uc,
            mes_ref=mes_ref,
            estado="SÃO PAULO",
            arquivo=Path(filename).name,
        )
        return novo

    # Se já existir no índice, recupera o carimbo existente por UC+ref+sistema.
    rows: list[dict[str, str]] = []
    master_csv = Path(master.master_file)
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with master_csv.open("r", newline="", encoding=enc) as f:
                import csv

                rows = list(csv.DictReader(f))
            break
        except UnicodeDecodeError:
            continue
    uc_digits = _digits(uc)
    for row in reversed(rows):
        if str(row.get("SISTEMA") or "").strip().upper() != "CPFL":
            continue
        if str(row.get("MES_REF") or "").strip() != mes_ref:
            continue
        row_uc = str(row.get("UC") or "").strip()
        if row_uc == uc or (_digits(row_uc) and _digits(row_uc) == uc_digits):
            idx = str(row.get("INDICE") or "").strip().upper()
            if re.fullmatch(r"BB_\d{7}", idx):
                return idx
    raise ValueError(f"CPFL ja baixado, mas carimbo nao localizado: {filename} UC={uc} ref={mes_ref}")


def _extract_mes_ref(txt: str) -> dt.date | None:
    """MAI/2026 na linha de cabecalho."""
    m = re.search(r"\b([A-Z]{3})/(20\d{2})\b", txt)
    if m and m.group(1) in MESES_PT:
        return dt.date(int(m.group(2)), int(MESES_PT[m.group(1)]), 1)
    m = re.search(r"\b(0[1-9]|1[0-2])/(20\d{2})\b", txt)
    if m:
        return dt.date(int(m.group(2)), int(m.group(1)), 1)
    return None


def _extract_datas(txt: str) -> tuple[dt.date | None, dt.date | None, dt.date | None]:
    """
    Leitura : '... 7086520 04/05/2026 01/04/2026 33 ...'  -> atu=grupo2 / ant=grupo3
    Vcto    : '... MAI/2026 25/05/2026 R$ ...'
    Texto e uma so linha apos _texto_normalizado — sem anchors de inicio.
    """
    m_leit = re.search(
        r"\b\d{5,10}\s+(\d{2}/\d{2}/\d{4})\s+(\d{2}/\d{2}/\d{4})\s+\d{1,3}\b",
        txt,
    )
    leit_atu = _to_date(m_leit.group(1)) if m_leit else None
    leit_ant = _to_date(m_leit.group(2)) if m_leit else None
    # Fallback layout antigo: 'LEITURA ... DD/MM/AAAA DD/MM/AAAA MULTIPL'
    if not m_leit:
        m_leit2 = re.search(r"(\d{2}/\d{2}/\d{4})\s+(\d{2}/\d{2}/\d{4})\s+MULTIPL", txt)
        if m_leit2:
            leit_atu = _to_date(m_leit2.group(1))
            leit_ant = _to_date(m_leit2.group(2))
    # Fallback NF3e: 'TRIFASICO/MONOFASICO/BIFASICO ANT_DATE ATU_DATE DIAS'
    # (sem numero de UC antes das datas, ordem invertida: anterior primeiro)
    if not m_leit and leit_atu is None:
        m_nf3e = re.search(
            r"(?:TRIFASICO|MONOFASICO|BIFASICO)\s+(\d{2}/\d{2}/\d{4})\s+(\d{2}/\d{2}/\d{4})\s+\d{1,3}\b",
            txt,
        )
        if m_nf3e:
            leit_ant = _to_date(m_nf3e.group(1))
            leit_atu = _to_date(m_nf3e.group(2))
    # NF3e recente: datas de leitura em linha isolada "03/07/2026 02/06/2026 31"
    if leit_atu is None:
        m_nf3e_solto = re.search(
            r"\b(\d{2}/\d{2}/\d{4})\s+(\d{2}/\d{2}/\d{4})\s+\d{1,3}\s+(?:BANCO\b|PROXIMA\s+LEITURA|RUA\b|CNPJ:)",
            txt,
            re.I,
        )
        if m_nf3e_solto:
            leit_atu = _to_date(m_nf3e_solto.group(1))
            leit_ant = _to_date(m_nf3e_solto.group(2))

    # Novo layout: 'MAI/2026 25/05/2026 R$ ...' ou '* 25/05/2026 R$'
    # Antigo layout: '701820664 SET/2025 20/10/2025 7.072,89'
    m_vcto = re.search(r"[A-Z]{3}/20\d{2}\s+(\d{2}/\d{2}/\d{4})\s+(?:R\$|\*)", txt)
    if not m_vcto:
        m_vcto = re.search(r"\d{5,10}\s+[A-Z]{3}/20\d{2}\s+(\d{2}/\d{2}/\d{4})\s+[\d\.,]+", txt)
    # NF3e: 'ABRIL/2026 R$687,64 08/06/2026' (nome longo do mes, vcto apos o valor)
    if not m_vcto:
        m_vcto = re.search(r"[A-Z]{4,9}/20\d{2}\s+R\$[\d\.,]+\s+(\d{2}/\d{2}/\d{4})", txt)
    vcto = _to_date(m_vcto.group(1)) if m_vcto else None

    return leit_ant, leit_atu, vcto


def _extract_emissao(txt: str) -> dt.date | None:
    # Alguns layouts NF3e inserem o CNPJ entre "DATA DE EMISSAO:" e a data.
    m = re.search(r"DATA DE EMISSAO:?.{0,140}?(\d{2}/\d{2}/\d{4})", txt)
    return _to_date(m.group(1)) if m else None


def _extract_notafiscal(txt: str) -> str:
    m = re.search(r"NOTA FISCAL N[°O]?\s*([\d.]+)", txt)
    return _digits(m.group(1)) if m else ""


def _extract_total(txt: str) -> float:
    """
    Novo layout:  'MAI/2026 25/05/2026 R$ 7.823,09'
    NF3e layout:  'ABRIL/2026 R$687,64 08/06/2026'
    Antigo layout: '<instalacao> SET/2025 20/10/2025 7.072,89'
    """
    for m in re.finditer(r"[A-Z]{3}/20\d{2}\s+\d{2}/\d{2}/\d{4}\s+R\$\s*([\d\.,]+)", txt):
        v = abs(_to_float_br(m.group(1)))
        if v > 0:
            return v
    # NF3e layout: "MES/AAAA R$VALOR DATA" (nome longo do mes, R$ antes da data)
    for m in re.finditer(r"[A-Z]{3,9}/20\d{2}\s+R\$\s*([\d\.,]+)\s+\d{2}/\d{2}/\d{4}", txt):
        v = abs(_to_float_br(m.group(1)))
        if v > 0:
            return v
    # Antigo layout: UC SET/AAAA DD/MM/AAAA VALOR
    for m in re.finditer(r"\d{5,10}\s+[A-Z]{3}/20\d{2}\s+\d{2}/\d{2}/\d{4}\s+([\d\.,]+)", txt):
        v = abs(_to_float_br(m.group(1)))
        if v > 0:
            return v
    # Fallback: "TOTAL CONSOLIDADO <valor>" (primeiro número após o label)
    m = re.search(r"TOTAL\s+CONSOLIDADO\s+([\d\.,]+)", txt)
    if m:
        v = abs(_to_float_br(m.group(1)))
        if v > 0:
            return v
    # Fallback NF3e parcelada: "Não Pague.Valor de R$ 506,54 será cobrado em parcelas"
    m = re.search(r"N[ãa]o\s+Pague[.\s]+Valor\s+de\s+R\$\s*([\d\.,]+)", txt, re.I)
    if m:
        v = abs(_to_float_br(m.group(1)))
        if v > 0:
            return v
    return 0.0


def _extract_total_distribuidora(txt: str) -> float:
    """'TOTAL DISTRIBUIDORA <valor>' = valor Nota Fiscal CPFL (antes das retenções)."""
    m = re.search(r"TOTAL\s+DISTRIBUIDORA\s+([\d\.,]+)", txt)
    if m:
        v = abs(_to_float_br(m.group(1)))
        if v > 0:
            return v
    return 0.0


def _consumo_linha(txt: str, *labels: str) -> tuple[float, float]:
    """
    Extrai (kWh, valor_R$) de linha de consumo CPFL.
    Novo layout:  '<label> ... kWh <qtd> <tarif1> <tarif2> <val>'
    Antigo layout: '<label> ... <qtd> KWH <tarifa> <val> <base> <icms_aliq>'
    Aceita multiplos labels alternativos (tenta o primeiro que casar).
    """
    for label in labels:
        # Antigo layout: '<label> ... <qty> KWH <tarifa> <valor>'
        pat_old = re.escape(label) + r"[^\n]*?([\d\.]+,\d{3})\s+KWH\s+[\d\.,]+\s+([\d\.,]+)"
        m = re.search(pat_old, txt, re.I)
        if m:
            return abs(_to_float_br(m.group(1))), abs(_to_float_br(m.group(2)))
        # Layout ELEKTRO/NF3e: 'kWh qty tarif val_base adj val_icms ...'
        # A coluna 'adj' (ex: 2,84) é descartada; val_icms repete val_base.
        pat_elektro = re.escape(label) + r"[^\n]*?kWh\s+([\d\.,]+)\s+[\d\.,]+\s+[\d\.,]+\s+[\d\.,]+\s+([\d\.,]+)"
        m_el = re.search(pat_elektro, txt, re.I)
        if m_el:
            qty = abs(_to_float_br(m_el.group(1)))
            val = abs(_to_float_br(m_el.group(2)))
            if qty > 0 and val >= 1.0:
                return qty, val
        # Novo layout 3 colunas: '<label> ... kWh <qty> <tarif1> <tarif2> <valor>'
        pat_new = re.escape(label) + r"[^\n]*?kWh\s+([\d\.,]+)\s+[\d\.,]+\s+[\d\.,]+\s+([\d\.,]+)"
        m2 = re.search(pat_new, txt, re.I)
        if m2:
            return abs(_to_float_br(m2.group(1))), abs(_to_float_br(m2.group(2)))
        # Novo layout 2 colunas: '<label> ... kWh <qty> <tarif> <valor>' (sem tarifa dupla)
        pat_2col = re.escape(label) + r"[^\n]*?kWh\s+([\d\.,]+)\s+[\d\.,]+\s+([\d\.,]+)"
        m3 = re.search(pat_2col, txt, re.I)
        if m3:
            # Verificar se terceiro numero e razoavelmente alto (valor, nao tarifa)
            v = abs(_to_float_br(m3.group(2)))
            if v >= 1.0:
                return abs(_to_float_br(m3.group(1))), v
    return 0.0, 0.0


def _bandeira_valor(txt: str, label: str) -> float:
    """
    Formatos conhecidos CPFL:
      DANF3E: 'ADICIONAL DE BANDEIRA AMARELA JUN/26 KWH 59,95 59,95 18,00 ...'
              → primeiro número após KWH é o valor R$ (59,95)
      Antigo: 'ADICIONAL BAND AMARELA FPONTA MAI/26 168 KWH 0,01874 3,15'
              → último número é o valor R$ (3,15)
    """
    pat_linha = re.compile(re.escape(label) + r"[^\n]*", re.I)
    m = pat_linha.search(txt)
    if not m:
        return 0.0
    linha = m.group(0)

    # DANF3E: "... KWH <valor> <valor_rep> 18,00 <icms> <pis> <cofins>"
    # O primeiro número após KWH é o valor monetário da bandeira.
    m_danf = re.search(r"\bKWH\s+([\d\.]+,\d+)", linha, re.I)
    if m_danf:
        v = abs(_to_float_br(m_danf.group(1)) or 0.0)
        if 0.01 <= v <= 5000:
            return v

    # Fallback formato antigo: pegar o último número da linha
    nums = re.findall(r"[\d\.]+,\d+", linha)
    if not nums:
        return 0.0
    v = abs(_to_float_br(nums[-1]) or 0.0)
    if v > 5000 and len(nums) >= 2:
        v2 = abs(_to_float_br(nums[-2]) or 0.0)
        if 0 < v2 <= 5000:
            return v2
        return 0.0
    return v if v >= 0.01 else 0.0


def _extract_consumo(txt: str) -> dict[str, float]:
    """
    Convencional B3: tudo em fora ponta (sem bandeira no valor).
    Tarifa Branca B3: campos separados por posto (ponta/intermediario/fora ponta).
    Bandeira sempre separada em fatValBandeira.
    """
    out: dict[str, float] = {}

    # --- Convencional B3 (posto unico) ---
    kwh_tusd, val_tusd = _consumo_linha(
        txt,
        "Consumo Uso Sistema",
        "Custo Disp Uso Sistema TUSD",
        "Custo Disp. Uso Sistema TUSD",
    )
    kwh_te,   val_te   = _consumo_linha(
        txt,
        "Consumo - TE",
        "Consumo TE",
        "Disp Sistema-TE",
        "Disp Sistema - TE",
        "Custo Disp TE",
    )
    banda = (
        _bandeira_valor(txt, "Adicional de Bandeira")
        or _bandeira_valor(txt, "Adicional D Sist Band")
        or _bandeira_valor(txt, "Adicional Band Amarela")
        or _bandeira_valor(txt, "Bandeira Tarifaria")
    )

    if kwh_tusd > 0 or kwh_te > 0:
        kwh = kwh_tusd or kwh_te
        val = round(val_tusd + val_te, 2)
        out["fatConFPontaIndRegistrado"] = kwh
        out["fatConFPontaIndFaturado"]   = kwh
        out["fatConFPontaIndValorReais"] = val
        out["fatConFPontaCapValorReais"] = 0.0  # fix(cpfl-bt): TUSD já somado em Ind; gravar em Cap duplicava ValorConsumo
        out["fatConFPontaCapRegistrado"] = kwh
        out["fatConFPontaCapFaturado"]   = kwh
        out["fatValBandeira"]            = banda
        return out

    # --- Tarifa Branca B3 (ponta + intermediario + fora ponta) ---
    kwh_p, val_tusd_p = _consumo_linha(txt, "Consumo Ponta")
    kwh_i, val_tusd_i = _consumo_linha(txt, "Consumo Interm", "Consumo Intermediario")
    kwh_f, val_tusd_f = _consumo_linha(txt, "Consumo Fora Ponta", "Consumo F Ponta", "Consumo FPonta")
    _,     val_te_p   = _consumo_linha(txt, "Cons Ponta - TE", "Cons Ponta TE")
    _,     val_te_i   = _consumo_linha(txt, "Cons Interm - TE", "Cons Interm TE")
    _,     val_te_f   = _consumo_linha(txt, "Cons FPonta TE", "Cons F Ponta TE", "Cons Fora Ponta TE")
    banda_p = (
        _bandeira_valor(txt, "Adicional Band Amarela Ponta")
        or _bandeira_valor(txt, "Adicional Band Amarela P")
    )
    banda_i = (
        _bandeira_valor(txt, "Adicional Band Amarela Interm")
        or _bandeira_valor(txt, "Adicional Band Amarela I")
    )
    banda_f = (
        _bandeira_valor(txt, "Adicional Band Amarela FPonta")
        or _bandeira_valor(txt, "Adicional Band Amarela F Ponta")
        or _bandeira_valor(txt, "Adicional Band Amarela FP")
    )

    kwh_total = kwh_p + kwh_i + kwh_f
    banda_total = round(banda_p + banda_i + banda_f, 2)

    if kwh_total > 0:
        if kwh_p > 0:
            out["fatConPontaRegistrado"]  = kwh_p
            out["fatConPontaFaturado"]    = kwh_p
            out["fatConPontaValorReais"]  = round(val_tusd_p + val_te_p, 2)
        if kwh_i > 0:
            out["fatConIntermediarioRegistrado"] = kwh_i
            out["fatConIntermediarioFaturado"]   = kwh_i
            out["fatConIntermediarioValorReais"] = round(val_tusd_i + val_te_i, 2)
        if kwh_f > 0:
            out["fatConFPontaIndRegistrado"] = kwh_f
            out["fatConFPontaIndFaturado"]   = kwh_f
            out["fatConFPontaIndValorReais"] = round(val_tusd_f + val_te_f, 2)
        out["fatValBandeira"] = banda_total

    return out


def _extract_tributos(txt: str) -> dict[str, float]:
    """
    Novo layout:
      ICMS 4.193,70 18,00 754,87
      PIS/PASEP 3.438,83 0,93 31,98
      COFINS 3.438,83 4,32 148,56

    Antigo layout (linha resumo):
      <total> <base_icms> <icms_val> <base_pis> <pis_val> <cofins_val>
      PIS/COFINS 1,22% 5,64%   (aliquotas no cabecalho)
      ICMS  18,00% nas linhas de item
    """
    out = {
        "fatDesIcmsAliquota":   0.0,
        "fatDescPisAliquota":   0.0,
        "fatDesCofinsAliquota": 0.0,
        "_icms_valor":          0.0,
        "_icms_base":           0.0,
        "_pis_valor":           0.0,
        "_cofins_valor":        0.0,
        "_base_pis_cofins":     0.0,
    }

    m = re.search(r"\bICMS\s+([\d\.,]+)\s+([\d\.,]+)\s+([\d\.,]+)", txt)
    if m:
        v0 = abs(_to_float_br(m.group(1)) or 0.0)
        v1 = abs(_to_float_br(m.group(2)) or 0.0)
        v2 = abs(_to_float_br(m.group(3)) or 0.0)
        # Se o primeiro número é pequeno (≤ 35) é alíquota %; base vem depois
        if v0 <= 35 and v1 > v0:
            out["fatDesIcmsAliquota"] = v0
            out["_icms_base"]         = v1
            out["_icms_valor"]        = v2
        else:
            out["_icms_base"]         = v0
            out["fatDesIcmsAliquota"] = v1
            out["_icms_valor"]        = v2

    m = re.search(r"\bPIS(?:/PASEP)?\s+([\d\.,]+)\s+([\d\.,]+)\s+([\d\.,]+)", txt)
    if m:
        out["fatDescPisAliquota"] = abs(_to_float_br(m.group(2)))
        out["_base_pis_cofins"]   = abs(_to_float_br(m.group(1)))
        out["_pis_valor"]         = abs(_to_float_br(m.group(3)))

    m = re.search(r"\bCOFINS\s+([\d\.,]+)\s+([\d\.,]+)\s+([\d\.,]+)", txt)
    if m:
        out["fatDesCofinsAliquota"] = abs(_to_float_br(m.group(2)))
        out["_cofins_valor"]        = abs(_to_float_br(m.group(3)))
        if out["_base_pis_cofins"] == 0.0:
            out["_base_pis_cofins"] = abs(_to_float_br(m.group(1)))

    # --- Antigo layout: fallback via linha resumo + aliquotas no header ---
    if out["_icms_valor"] == 0.0 and out["_pis_valor"] == 0.0:
        # PIS/COFINS aliquotas: 'PIS/COFINS 1,22% 5,64%'
        m_pc = re.search(r"PIS/COFINS\s+([\d\.,]+)%\s+([\d\.,]+)%", txt)
        pis_aliq   = abs(_to_float_br(m_pc.group(1))) if m_pc else 0.0
        cofins_aliq = abs(_to_float_br(m_pc.group(2))) if m_pc else 0.0
        # ICMS aliquota nas linhas de item: 'ICMS ... 18,00%' ou valor tabular
        m_ia = re.search(r"\bICMS\b[^\d]*([\d\.,]+)%", txt)
        if not m_ia:
            m_ia = re.search(r"\b(1[0-9]|2[0-9]|[89])[,\.]\d{2}\b", txt)
        icms_aliq = abs(_to_float_br(m_ia.group(1))) if m_ia else 0.0

        # Linha resumo apos 'TOTAL CONSOLIDADO': total base_icms icms_val base_pis pis_val cofins_val
        m_res = re.search(
            r"TOTAL CONSOLIDADO\s+([\d\.,]+)\s+([\d\.,]+)\s+([\d\.,]+)\s+([\d\.,]+)\s+([\d\.,]+)\s+([\d\.,]+)",
            txt
        )
        if m_res:
            base_icms  = abs(_to_float_br(m_res.group(2)))
            icms_val   = abs(_to_float_br(m_res.group(3)))
            base_pis   = abs(_to_float_br(m_res.group(4)))
            pis_val    = abs(_to_float_br(m_res.group(5)))
            cofins_val = abs(_to_float_br(m_res.group(6)))
            # sanity: icms_val deve ser ~18% de base_icms
            if icms_aliq == 0.0 and base_icms > 0:
                icms_aliq = round(icms_val / base_icms * 100, 2)
            out["fatDesIcmsAliquota"]   = icms_aliq
            out["_icms_base"]           = base_icms
            out["_icms_valor"]          = icms_val
            out["fatDescPisAliquota"]   = pis_aliq
            out["fatDesCofinsAliquota"] = cofins_aliq
            out["_base_pis_cofins"]     = base_pis
            out["_pis_valor"]           = pis_val
            out["_cofins_valor"]        = cofins_val

    return out


def _extract_retencoes(txt: str) -> dict[str, float]:
    """
    Soma TODAS as retencoes do mesmo tributo:
      RETENCAO CONSUMO IRRF-1,2%      → IRPJ (não IRRF — convenção CONSEN)
      RET. OUT. FORNEC IRRF -1,2%     → idem, somado ao mesmo campo
      RETENCAO CONSUMO COFINS-3,0%    → COFINS retido
      RET. OUT. FORNEC COFINS -3,0%   → idem
    """
    out = {
        "fatDescCsllPercRetImposto":   0.0,
        "fatDescCsllValRetImposto":    0.0,
        "fatDescIrpjPercRetImposto":   0.0,
        "fatDescIrpjValRetImposto":    0.0,
        "fatDescCofinsPercRetImposto": 0.0,
        "fatDescCofinsValRetImposto":  0.0,
        "fatDescPisPercRetImposto":    0.0,
        "fatDescPisValRetImposto":     0.0,
    }
    mapa = {
        "CSLL":   ("fatDescCsllPercRetImposto",   "fatDescCsllValRetImposto",   1.0),
        "IRRF":   ("fatDescIrpjPercRetImposto",   "fatDescIrpjValRetImposto",   1.2),
        "COFINS": ("fatDescCofinsPercRetImposto", "fatDescCofinsValRetImposto", 3.0),
        "PIS":    ("fatDescPisPercRetImposto",    "fatDescPisValRetImposto",    0.65),
    }
    for label, (campo_perc, campo_val, perc) in mapa.items():
        # Formato 1: "RETENCAO CONSUMO/DEMANDA/... IRRF-1,2% 28,93-"
        m1 = re.findall(rf"RETENCAO\s+\w+\s+{label}[^\n]*?([\d\.,]+)-", txt)
        # Formato 2: "RET. OUT. FORNEC IRRF -1,2% 0,86-"
        m2 = re.findall(rf"RET\.\s+OUT\.\s+FORNEC\s+{label}[^\n]*?([\d\.,]+)-", txt)
        matches = m1 + m2
        if matches:
            total = round(sum(abs(_to_float_br(v)) for v in matches), 2)
            out[campo_perc] = perc
            out[campo_val]  = -total
    return out


def _extract_multas(txt: str) -> float:
    """
    Multa por atraso cobrada como item de debito na fatura.
    Suporta dois layouts:
      Antigo: label + MES/ANO + valor  ('MULTA MORA ABR/26 123,45')
      DANF3E: label + FORNEC + valor   ('MULTA POR ATRASO PGTO FORNEC 197,88')
                                       ('JUROS DE MORA FORNEC 91,42')
                                       ('ATUALIZACAO MONETARIA FOR IPCA 42,27')
    """
    total = 0.0
    pats = [
        # Antigo: com mês/ano
        r"\bMULTA\b(?:\s+(?:MORA|POR\s+ATRASO|FINANCEIRA))?\s+[A-Z]{3}/\d{2}\s+([\d\.,]+)\b",
        r"ACRESCIMOS\s+FINANCEIROS\s+[A-Z]{3}/\d{2}\s+([\d\.,]+)\b",
        r"JUROS\s+(?:DE\s+)?MORA\s+[A-Z]{3}/\d{2}\s+([\d\.,]+)\b",
        # DANF3E: sem mês/ano, sufixo FORNEC
        r"JUROS\s+DE\s+MORA\s+FORNEC\s+([\d\.,]+)\b",
        r"MULTA\s+POR\s+ATRASO\s+(?:PGTO\s+)?FORNEC\s+([\d\.,]+)\b",
        r"ATUALIZACAO\s+MONETARIA\s+(?:FOR\s+)?IPCA\s+([\d\.,]+)\b",
    ]
    for pat in pats:
        for m in re.finditer(pat, txt, re.I):
            v = abs(_to_float_br(m.group(1)))
            if v >= 0.50:
                total += v
    return round(total, 2)


def _extract_devolucao_fat_maior(txt: str) -> float:
    """
    CPFL Piratininga DANF3E: créditos de devolução por faturamento maior (REN 1000).
      'Dev. dobro Fat. Maior 1.038,18-'
      'Devolução Atual Monetária Fat. Maior IPCA 91,66-'
    Retorna soma positiva dos créditos (serão inseridos como obs código 109).
    """
    total = 0.0
    pats = [
        # 'DEV. DOBRO FAT. MAIOR 1.038,18-'
        r"DEV\.?\s+DOBRO\s+FAT\.?\s+MAIOR\s+([\d\.]+,\d+)-",
        # 'DEVOLUCAO ATUAL MONETARIA FAT. MAIOR IPCA 91,66-'
        r"DEVOLUCAO\s+ATUAL\s+MONETARIA\s+FAT\.?\s+MAIOR\s+\w+\s+([\d\.]+,\d+)-",
    ]
    for pat in pats:
        for m in re.finditer(pat, txt, re.I):
            v = abs(_to_float_br(m.group(1)) or 0.0)
            if v >= 0.01:
                total += v
    return round(total, 2)


def _extract_observacoes(txt: str) -> list[tuple[int, float]]:
    """Extrai observações (código, valor) do bloco de itens da fatura CPFL."""
    resultado: list[tuple[int, float]] = []
    # Padrões conhecidos CPFL: IMP.SOM/DIM, IMPORTE A SOMAR/DIMINUIR, COMP.DIC, COMP.FIC
    rules = [
        ("IMP.SOM",          131, True),
        ("IMPORTE A SOMAR",  131, True),
        ("COMP.DIC",          58, True),
        ("COMP.FIC",          11, True),
        ("DIC/FIC",           11, True),
        ("SEGUNDA VIA",       51, False),
        ("PAGAMENTO INDEVIDO", 109, True),
    ]
    for line in txt.splitlines():
        if len(resultado) >= 5:
            break
        line_up = line.upper()
        for pattern, code, negative in rules:
            if pattern in line_up:
                vals = re.findall(r"[\d\.]+,\d{2}", line)
                if vals:
                    v = abs(_to_float_br(vals[-1]))
                    resultado.append((code, -v if negative else v))
                break
    return resultado


def _extract_injetado(txt: str) -> dict[str, float]:
    """
    Soma todas as linhas de energia ativa injetada (GD/solar):
      'ENERG ATV INJ. OUC MPT - TUSD MAI/26 KWH 2.027,6853 0,44925000 0,47239580 957,87-'
      'ENERG ATV INJ. OUC MPT - TE   MAI/26 KWH 2.027,6853 0,29020000 0,37213369 754,57-'

    kWh: contado apenas nas linhas TUSD (evita dupla contagem com TE).
    Valor R$: soma TUSD + TE (crédito total deduzido da fatura).

    Crédito de bandeira sobre injetado ('CRED ADC BAND AMARELA') é retornado
    separadamente para ser descontado do fatValBandeira.
    """
    out: dict[str, float] = {}
    kwh_total = 0.0
    val_total  = 0.0

    # Linhas (após _texto_normalizado, sem newlines):
    # 'ENERG ATV INJ. OUC MPT - TUSD MAI/26 KWH 2.027,6853 0,44925000 0,47239580 957,87-'
    # Padrão preciso para evitar match guloso quando texto está numa linha só.
    pat = re.compile(
        r"ENERG\s+AT[V]?\s+INJ\.\s+OUC\s+\w+\s+-\s+(TUSD|TE)\s+[A-Z]{3}/\d{2}\s+KWH\s+"
        r"([\d\.]+,\d+)\s+[\d\.,]+\s+[\d\.,]+\s+([\d\.]+,\d+)-",
        re.I,
    )
    for m in pat.finditer(txt):
        tipo  = m.group(1).upper()
        kwh   = abs(_to_float_br(m.group(2)) or 0.0)
        valor = abs(_to_float_br(m.group(3)) or 0.0)
        if tipo == "TUSD":
            kwh_total += kwh
        val_total += valor

    if kwh_total > 0 or val_total > 0:
        out["fatConFPontaInjetadoRegistrado"] = round(kwh_total, 4)
        out["fatConFPontaInjetadoFaturado"]   = round(kwh_total, 4)
        out["fatConFPontaInjetadoValorReais"] = round(val_total, 2)

    # Crédito de bandeira sobre injetado: 'CRED ADC BAND AMARELA JUN/26 KWH 94,96- ...'
    m_cb = re.search(r"CRED\s+ADC\s+BAND\s+AMARELA\s+[A-Z]{3}/\d{2}\s+KWH\s+([\d\.]+,\d+)-", txt, re.I)
    if m_cb:
        out["_cred_band_injetado"] = abs(_to_float_br(m_cb.group(1)) or 0.0)

    # Saldo acumulado de energia: 'Saldo em Energia da Instalação: Convencional X,XXXXkWh'
    m_saldo = re.search(
        r"SALDO\s+EM\s+ENERGIA\s+DA\s+INSTALACAO:?\s+CONVENCIONAL\s+([\d,\.]+)\s+KWH",
        txt, re.I,
    )
    if m_saldo:
        saldo = abs(_to_float_br(m_saldo.group(1)) or 0.0)
        if saldo > 0:
            out["fatConFPontaInjetadoUsinaSaldoAcumulado"] = saldo

    return out


def _extract_cip(txt: str) -> float:
    """
    Novo layout:   'IP-CIP MAI/26 23,06'
    Antigo layout: 'IP-CIP Municipal SET/25 6,00'
    """
    m = re.search(r"IP-CIP\s+(?:\w+\s+)?[A-Z]{3}/\d{2}\s+([\d\.,]+)", txt)
    return abs(_to_float_br(m.group(1))) if m else 0.0


def identificacao_rapida(pdf_path: Path) -> dict:
    resultado = {"sistema": "DESCONHECIDA", "instalacao": "", "mes_ref": "", "grupo": "B"}
    try:
        with pdfplumber.open(str(pdf_path)) as pdf:
            if not pdf.pages:
                return resultado
            text = pdf.pages[0].extract_text(x_tolerance=1, y_tolerance=1) or ""
        if not text:
            return resultado
        txt = _texto_normalizado(text)
        if not _is_cpfl(txt):
            return resultado
        resultado["sistema"]    = "CPFL"
        resultado["instalacao"] = _extract_instalacao(txt)
        ref = _extract_mes_ref(txt)
        if ref:
            resultado["mes_ref"] = ref.strftime("%m-%Y")
        resultado["grupo"] = "B"
    except Exception as exc:
        log.warning("  identificacao_rapida %s: %s", pdf_path.name, exc)
    return resultado


def processar_pdf(pdf_path: Path, mes_padrao: int, ano_padrao: int) -> dict:
    rec = _empty_record()
    rec["ARQUIVO"]         = pdf_path.name
    rec["fatDataCadastro"] = dt.date.today()
    rec["concCod"]         = "CPFL"

    try:
        with pdfplumber.open(str(pdf_path)) as pdf:
            partes = [
                page.extract_text(x_tolerance=1, y_tolerance=1) or ""
                for page in pdf.pages
            ]
        text = "\n".join(p for p in partes if p.strip())
    except Exception as exc:
        rec["ERRO"] = f"{type(exc).__name__}: {exc}"
        return rec

    if not text.strip():
        rec["ERRO"] = "PDF sem texto extraivel"
        return rec

    txt = _texto_normalizado(text)

    if not _is_cpfl(txt):
        rec["ERRO"] = "Nao identificado como CPFL"
        return rec

    # Detecta Tarifa Branca pela presença de texto indicativo na fatura
    _e_branca = bool(re.search(r"TARIFA\s+BRANCA|Tarifa\s+Branca", txt, re.I))
    rec["cadTarifaCod"]   = "Branca" if _e_branca else "Convencional"
    rec["cadSubGrupoCod"] = "B3 [<2,3kV]"
    rec["TARIFA_DETECTADA"] = "B3 Branca" if _e_branca else "B3"

    rec["Instalacao"]    = _extract_instalacao(txt)
    rec["CODIGOCLIENTE"] = rec["Instalacao"]
    rec["NOTAFISCAL"]    = _extract_notafiscal(txt)
    rec["CNPJ"]          = ""

    ref = _extract_mes_ref(txt)
    rec["fatDataReferencia"] = ref if ref else dt.date(ano_padrao, mes_padrao, 1)
    rec["fatCarimbo"] = _resolver_carimbo_master(
        pdf_path.name,
        rec["Instalacao"],
        rec["fatDataReferencia"],
    )

    rec["fatDataEmissao"]         = _extract_emissao(txt)
    leit_ant, leit_atu, vcto      = _extract_datas(txt)
    rec["fatDataLeituraAnterior"] = leit_ant
    rec["fatDataLeituraAtual"]    = leit_atu
    rec["fatDataVcto"]            = vcto

    rec["fatValorFatura"] = _extract_total(txt)
    rec["fatIlumPublica"] = _extract_cip(txt)

    tributos = _extract_tributos(txt)
    rec["fatICMS"]              = tributos["_icms_valor"]
    rec["fatPIS"]               = tributos["_pis_valor"]
    rec["fatCOFINS"]            = tributos["_cofins_valor"]
    rec["fatDesIcmsAliquota"]   = tributos["fatDesIcmsAliquota"]
    rec["fatDescPisAliquota"]   = tributos["fatDescPisAliquota"]
    rec["fatDesCofinsAliquota"] = tributos["fatDesCofinsAliquota"]
    nf_distrib = _extract_total_distribuidora(txt)
    rec["fatValorNotaFiscal"] = nf_distrib if nf_distrib > 0 else rec["fatValorFatura"]

    multa = _extract_multas(txt)
    if multa > 0:
        rec["fatMultasDiversas"] = multa

    rec.update(_extract_retencoes(txt))
    rec.update(_extract_consumo(txt))

    injetado = _extract_injetado(txt)
    cred_band = injetado.pop("_cred_band_injetado", 0.0)
    rec.update(injetado)
    # fatValBandeira permanece como valor bruto positivo;
    # o crédito do injetado vai para fatValBandeira2 (negativo)
    if cred_band > 0:
        rec["fatValBandeira2"] = -round(cred_band, 2)

    for _i, (_code, _val) in enumerate(_extract_observacoes(txt)[:5], start=1):
        rec.setdefault(f"obsCod_{_i}", _code)
        rec.setdefault(f"obsValor_{_i}", _val)
        rec[f"obsCod_{_i}"] = _code
        rec[f"obsValor_{_i}"] = _val

    dev_fat = _extract_devolucao_fat_maior(txt)
    if dev_fat > 0:
        _slot = 1
        while _slot <= 5 and rec.get(f"obsCod_{_slot}"):
            _slot += 1
        if _slot <= 5:
            rec[f"obsCod_{_slot}"] = 109
            rec[f"obsValor_{_slot}"] = -dev_fat

    # Codigo de barras: 4 grupos de 12 digitos no rodape
    m_cb = re.search(r"(\d{12})\s+(\d{12})\s+(\d{12})\s+(\d{12})", txt)
    if m_cb:
        cb = "".join(m_cb.groups())
        rec["fatCodigoBarras"] = cb if len(cb) >= 44 else ""

    rec["ERRO"] = ""
    return rec


def _listar_pdfs(pasta: Path, carimbos: set[str]) -> list[Path]:
    pdfs = sorted(p for p in pasta.glob("*.pdf") if p.is_file())
    if carimbos:
        norm = {str(c).strip().upper() for c in carimbos}
        # aceita tanto "2011459" quanto "BB_2011459" no filtro
        norm_sem_bb = {c[3:] if c.startswith("BB_") else c for c in norm}
        def _bate(p: Path) -> bool:
            s = p.stem.upper()
            s_sem = s[3:] if s.startswith("BB_") else s
            return s in norm or s_sem in norm_sem_bb
        pdfs = [p for p in pdfs if _bate(p)]
    return pdfs


def _xlsx_saida(mes: int, ano: int) -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    return OUTPUT_DIR / f"ocr_cpfl_BT_{mes:02d}{ano}.xlsx"


def parse_args() -> argparse.Namespace:
    hoje = dt.date.today()
    parser = argparse.ArgumentParser(description="OCR CPFL BT -> XLSX")
    parser.add_argument("--mes",     type=int, default=hoje.month)
    parser.add_argument("--ano",     type=int, default=hoje.year)
    parser.add_argument("--pasta",   type=str, default=str(DEFAULT_PASTA))
    parser.add_argument("--saida",   type=str, default="")
    parser.add_argument("--carimbo", action="append", default=[])
    return parser.parse_args()


def main() -> int:
    args     = parse_args()
    pasta    = Path(str(args.pasta).strip())
    carimbos = {str(c).strip().upper() for c in (args.carimbo or []) if str(c).strip()}

    if not pasta.exists():
        log.error("Pasta nao encontrada: %s", pasta)
        return 1

    pdfs = _listar_pdfs(pasta, carimbos)
    if not pdfs:
        log.warning("Nenhum PDF encontrado.")
        return 0

    log.info("=" * 64)
    log.info("  OCR CPFL BT")
    log.info("=" * 64)
    log.info("  Pasta          : %s", pasta)
    log.info("  PDFs candidatos: %d", len(pdfs))

    registros: list[dict] = []
    ignorados = 0
    sem_bb_estrito = [
        pdf for pdf in pdfs
        if not CARIMBO_BB_RE.search(pdf.stem)
    ]
    if sem_bb_estrito:
        log.info(
            "  Modo sequencial: %d PDF(s) sem BB_ estrito exigem reserva atomica no indice",
            len(sem_bb_estrito),
        )
        iterable = (processar_pdf(pdf, int(args.mes), int(args.ano)) for pdf in pdfs)
        for rec in iterable:
            if rec.get("ERRO") == "Nao identificado como CPFL":
                ignorados += 1
                continue
            registros.append(rec)
    else:
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futuros = [
                executor.submit(processar_pdf, pdf, int(args.mes), int(args.ano))
                for pdf in pdfs
            ]
            for futuro in as_completed(futuros):
                rec = futuro.result()
                if rec.get("ERRO") == "Nao identificado como CPFL":
                    ignorados += 1
                    continue
                registros.append(rec)

    registros.sort(key=lambda r: str(r.get("fatCarimbo", "")))
    if not registros:
        log.warning("Nenhuma fatura CPFL extraida.")
        return 0

    destino = (
        Path(str(args.saida).strip())
        if str(args.saida).strip()
        else _xlsx_saida(int(args.mes), int(args.ano))
    )
    try:
        salvar_excel(registros, destino, titulo="OCR_CPFL_BT")
    except Exception as exc:
        log.error("Falha ao salvar XLSX: %s", exc)
        return 1

    ok   = sum(1 for r in registros if not r.get("ERRO"))
    erro = len(registros) - ok
    log.info("  XLSX salvo    : %s", destino)
    log.info("  Resumo        : total=%d ok=%d erro=%d ignorados=%d",
             len(registros), ok, erro, ignorados)
    return 0 if erro == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
