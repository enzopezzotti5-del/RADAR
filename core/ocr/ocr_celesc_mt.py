#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OCR CELESC MT
=============

Extrai campos das faturas CELESC MT (Média Tensão, subgrupo A4/A3a, tarifas
Verde ou Azul com demanda) para alimentar o fluxo de digitação do Consen.

Uso:
    python ocr_celesc_mt.py
    python ocr_celesc_mt.py --mes 04 --ano 2026
    python ocr_celesc_mt.py --pasta "\\\\servidor\\DOWNLOAD CELESC\\04.2026\\MT"
    python ocr_celesc_mt.py --carimbo BB_2003260

Saída:
    \\\\servidor\\OCR CELESC\\ocr_celesc_MT_042026.xlsx
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import logging
import re
import sys
import unicodedata
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pandas as pd
import pdfplumber
from core.project_paths import resolve_indice_master_csv


ROOT_DIR     = Path(r"\\10.10.250.21\Energia\ARQUIVOS ENZO")
DOWNLOAD_DIR = ROOT_DIR / "DOWNLOAD CELESC"
OCR_DIR      = ROOT_DIR / "OCR CELESC"
TENSAO       = "MT"

# Mapa tarifa detectada → cadTarifaCod no CONSEN para CELESC MT
# TODO: confirmar codes corretos com equipe CONSEN
TARIFA_COD = {
    "Verde":        2,
    "Azul":         3,
    "Convencional": 1,
}

# Mapa subgrupo detectado → cadSubGrupoCod no CONSEN para CELESC MT
# TODO: confirmar codes corretos com equipe CONSEN
SUBGRUPO_COD = {
    "A4": 10,
    "A3": 11,
    "A3A": 12,
    "A2": 13,
    "A1": 14,
}

HEADERS = [
    "Instalacao", "fatDataEmissao", "fatDataVcto", "fatValorFatura", "concCod",
    "fatDataCadastro", "fatDataLeituraAnterior", "fatDataLeituraAtual", "fatIlumPublica",
    "cadTarifaCod", "cadSubGrupoCod",
    "fatDemContratadaPonta", "fatDemContratadaFPonta",
    "fatDemPontaRegistrada", "fatDemFPontaIndRegistrada", "fatDemFPontaCapRegistrada",
    "fatDemPontaExcFaturada", "fatDemFPontaExcFaturada",
    "fatDemPontaExcRegistrada", "fatDemFPontaExcRegistrada",
    "fatDemPontaFaturada", "fatDemFPontaIndFaturada",
    "fatDemPontaUltra", "fatDemFPontaIndUltra",
    "fatConPontaRegistrado", "fatConFPontaIndRegistrado",
    "fatConFPontaCapRegistrado", "fatConIntermediarioRegistrado",
    "fatConPontaFaturado", "fatConFPontaIndFaturado",
    "fatConFPontaCapFaturado", "fatConIntermediarioFaturado",
    "fatConPontaExcRegistrado", "fatConFPontaIndExcRegistrado",
    "fatConFPontaCapExcRegistrado", "fatConPontaExcFaturado",
    "fatConFPontaIndExcFaturado", "fatConFPontaCapExcFaturado",
    "fatConFPontaIndExcValorReais",
    "fatICMS", "fatPIS", "fatCOFINS", "fatValorNotaFiscal",
    "fatDemPontaValorReais", "fatDemFPontaIndValorReais",
    "fatBeneficioTarifarioBrutoValorReais", "fatBeneficioLiquidoValorReais",
    "fatDescontoFio",
    "obsValor",
    "CNPJ", "fatDataReferencia",
    "fatConPontaInjetadoRegistrado", "fatConPontaInjetadoFaturado",
    "fatConFPontaInjetadoRegistrado", "fatConFPontaInjetadoFaturado",
    "fatCodigoBarras", "fatCarimbo", "usuCod",
    "fatDescPisAliquota", "fatDescCofinsAliquota", "fatDesIcmsAliquota",
    "fatDescPisValRetImposto", "fatDescCofinsValRetImposto",
    "fatDescCsllValRetImposto", "fatDescIrpjValRetImposto",
    "ENDERECO", "NOTAFISCAL", "CODIGOCLIENTE",
    "TARIFA_DETECTADA", "SUBGRUPO_DETECTADO", "ARQUIVO", "ERRO",
]

DATE_HEADERS = {
    "fatDataEmissao", "fatDataVcto", "fatDataCadastro",
    "fatDataLeituraAnterior", "fatDataLeituraAtual", "fatDataReferencia",
}
TEXT_HEADERS = {
    "Instalacao", "CNPJ", "ENDERECO", "NOTAFISCAL", "CODIGOCLIENTE",
    "fatCodigoBarras", "fatCarimbo", "usuCod",
    "TARIFA_DETECTADA", "SUBGRUPO_DETECTADO", "ARQUIVO", "ERRO",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("ocr_celesc_mt")


# ── Utilitários ──────────────────────────────────────────────────────────────

def _mkdir_seguro(path: Path) -> None:
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass


def _to_ascii_upper(text: str) -> str:
    return "".join(
        ch for ch in unicodedata.normalize("NFKD", text or "") if not unicodedata.combining(ch)
    ).upper()


def _to_float_br(value: str | float | int | None) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    txt = str(value).strip()
    if not txt:
        return None
    neg = txt.endswith("-") or txt.startswith("-")
    txt = txt.replace("R$", "").replace(" ", "").replace(".", "").replace(",", ".").replace("%", "")
    txt = txt.lstrip("-").rstrip("-")
    try:
        number = float(txt)
        return -number if neg else number
    except ValueError:
        return None


def _carimbo_from_path(path: Path) -> str:
    bb_match = re.search(r"(BB_\d+)", path.stem, flags=re.IGNORECASE)
    if bb_match:
        return bb_match.group(1).upper()
    match = re.search(r"(\d{7})", path.stem)
    return match.group(1) if match else path.stem


def _mes_ref_master(data_ref: object) -> str:
    if hasattr(data_ref, "month") and hasattr(data_ref, "year"):
        return f"{int(data_ref.month):02d}-{int(data_ref.year)}"
    txt = str(data_ref or "").strip()
    if re.fullmatch(r"\d{2}-\d{4}", txt):
        return txt
    return ""


def _resolver_carimbo_master(pdf_path: Path, instalacao: object, data_ref: object) -> str:
    carimbo_nome = _carimbo_from_path(pdf_path)
    if str(carimbo_nome).upper().startswith("BB_"):
        return str(carimbo_nome).upper()

    uc_digits = re.sub(r"\D", "", str(instalacao or ""))
    mes_ref = _mes_ref_master(data_ref)
    if not uc_digits and not pdf_path.name:
        return carimbo_nome

    master_csv = resolve_indice_master_csv(prefer_network=False)
    if not master_csv.exists():
        return carimbo_nome

    arquivo_nome = pdf_path.name.strip().lower()
    try:
        for enc in ("utf-8-sig", "utf-8", "latin-1"):
            try:
                with open(master_csv, newline="", encoding=enc) as f:
                    rows = list(csv.DictReader(f))
                break
            except UnicodeDecodeError:
                continue
        else:
            rows = []
    except OSError:
        return carimbo_nome

    for row in reversed(rows):
        indice = str(row.get("INDICE") or "").strip().upper()
        if not indice.startswith("BB_"):
            continue
        sistema = str(row.get("SISTEMA") or "").strip().upper()
        if sistema != "CELESC":
            continue
        arquivo_master = Path(str(row.get("ARQUIVO") or "").strip()).name.lower()
        if arquivo_master and arquivo_master == arquivo_nome:
            return indice
        uc_master = re.sub(r"\D", "", str(row.get("UC") or ""))
        mes_master = str(row.get("MES_REF") or "").strip()
        if uc_digits and uc_master == uc_digits and mes_ref and mes_master == mes_ref:
            return indice

    if uc_digits and mes_ref:
        try:
            from indice_master import MasterIndice
            master = MasterIndice()
            if not master.ja_foi_baixado(str(instalacao or ""), mes_ref, "CELESC"):
                novo = master.consumir_carimbo()
                master.registrar(
                    indice_bb=novo,
                    sistema="CELESC",
                    uc=str(instalacao or ""),
                    mes_ref=mes_ref,
                    estado="SANTA CATARINA",
                    arquivo=pdf_path.name,
                )
                return novo
        except Exception:
            pass

    return carimbo_nome


def _extract_pages(path: Path) -> tuple[list[str], list[str], str, str]:
    lines_original: list[str] = []
    pages_original: list[str] = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            text = page.extract_text(x_tolerance=1, y_tolerance=1) or ""
            pages_original.append(text)
            lines_original.extend(line.strip() for line in text.splitlines() if line.strip())
    text_original = "\n".join(pages_original)
    lines_ascii = [_to_ascii_upper(line) for line in lines_original]
    text_ascii = _to_ascii_upper(text_original)
    return lines_original, lines_ascii, text_original, text_ascii


# ── Parsers compartilhados com BT ────────────────────────────────────────────

def _parse_ref_vcto_valor(text_ascii: str) -> tuple[str, str, float | None]:
    match = re.search(
        r"(\d{2}/\d{4})\s+(\d{2}/\d{2}/\d{4})\s+R\$\s*([\d\.]+,\d+)",
        text_ascii,
    )
    if match:
        return match.group(1), match.group(2), _to_float_br(match.group(3))
    m_total = re.search(r"\bTOTAL\s+([\d\.]+,\d+)", text_ascii)
    m_vcto = re.search(r"(\d{2}/\d{2}/\d{4})\s+(?:[\d\.]+,\d+)\s*$", text_ascii)
    return "", (m_vcto.group(1) if m_vcto else ""), (_to_float_br(m_total.group(1)) if m_total else None)


def _parse_emissao_nf(text_ascii: str) -> tuple[str, str]:
    match = re.search(
        r"NOTA FISCAL\s+[N°No]+\s*(\d+)\s+SERIE[:\s]*\d+\s+DATA EMISSAO[:\s]*(\d{2}/\d{2}/\d{4})",
        text_ascii,
    )
    if match:
        return match.group(1), match.group(2)
    match2 = re.search(r"DATA EMISSAO[:\s]*(\d{2}/\d{2}/\d{4})", text_ascii)
    return "", (match2.group(1) if match2 else "")


def _parse_leitura_datas(text_ascii: str) -> tuple[str, str]:
    match = re.search(
        r"(\d{2}/\d{2}/\d{4})\s+(\d{2}/\d{2}/\d{4})\s+\d+\s+Lida",
        text_ascii,
        re.IGNORECASE,
    )
    if match:
        return match.group(1), match.group(2)
    return "", ""


def _parse_instalacao(lines_ascii: list[str], text_ascii: str) -> str:
    match = re.search(r"\b(\d\.\d{3}\.\d{3}\.\d{3}-\d{2})\b", text_ascii)
    if match:
        return match.group(1)
    match = re.search(r"UNIDADE CONSUMIDORA\s+(\d{7,12})", text_ascii)
    if match:
        return match.group(1).lstrip("0") or match.group(1)
    for idx, line in enumerate(lines_ascii):
        if line.startswith("NOME:"):
            for prox in lines_ascii[idx + 1: idx + 5]:
                m = re.fullmatch(r"\d\.\d{3}\.\d{3}\.\d{3}-\d{2}", prox.strip())
                if m:
                    return m.group(0)
                m = re.fullmatch(r"\d{5,10}", prox.strip())
                if m:
                    return m.group(0)
    for idx, line in enumerate(lines_ascii):
        m = re.fullmatch(r"\d\.\d{3}\.\d{3}\.\d{3}-\d{2}", line.strip())
        if m:
            prev = lines_ascii[idx - 1] if idx > 0 else ""
            nxt = lines_ascii[idx + 1] if idx + 1 < len(lines_ascii) else ""
            if prev.startswith("NOME:") or nxt.startswith("CPF/CNPJ:") or nxt.startswith("ENDERECO:"):
                return m.group(0)
        m = re.fullmatch(r"\d{5,10}", line.strip())
        if not m:
            continue
        prev = lines_ascii[idx - 1] if idx > 0 else ""
        nxt = lines_ascii[idx + 1] if idx + 1 < len(lines_ascii) else ""
        if prev.startswith("NOME:") or nxt.startswith("ENDERECO:"):
            return m.group(0)
    return ""


def _parse_cnpj(text_ascii: str) -> str:
    match = re.search(r"CPF/CNPJ[:\s]*([\d]{2}\.[\d]{3}\.[\d]{3}/[\d]{4}-[\d]{2})", text_ascii)
    if match:
        return match.group(1)
    match2 = re.search(r"CPF/CNPJ[:\s]+([\d\./-]+)", text_ascii)
    if not match2:
        return ""
    raw = re.sub(r"\D", "", match2.group(1))
    if len(raw) == 14:
        return f"{raw[:2]}.{raw[2:5]}.{raw[5:8]}/{raw[8:12]}-{raw[12:]}"
    return match2.group(1).strip()


def _parse_codigo_cliente(text_ascii: str) -> str:
    match = re.search(r"Cliente[:\s]+(\d{5,10})", text_ascii, re.IGNORECASE)
    return match.group(1) if match else ""


def _parse_endereco(lines_original: list[str]) -> str:
    stop_prefixes = (
        "CEP:", "CIDADE:", "CNPJ:", "CPF/", "CLIENTE:", "ETAPA:",
        "PROTOCOLO", "GRUPO/SUBGRUPO", "CONSULTE", "CHAVE DE ACESSO",
        "DATA DOCUMENTO", "PAGUE COM PIX", "NOTA FISCAL",
    )
    for idx, line in enumerate(lines_original):
        line_ascii = _to_ascii_upper(line)
        if line_ascii.startswith("ENDERECO:") or line_ascii.startswith("ENDERECO "):
            partes = [line.split(":", 1)[1].strip() if ":" in line else line.strip()]
            j = idx + 1
            while j < len(lines_original):
                nxt_ascii = _to_ascii_upper(lines_original[j])
                if re.fullmatch(r"\d{5,10}", nxt_ascii.strip()):
                    break
                if any(nxt_ascii.startswith(p) for p in stop_prefixes):
                    break
                partes.append(lines_original[j].strip())
                j += 1
            endereco = " ".join(p for p in partes if p)
            endereco = re.sub(r"\s+", " ", endereco).strip(" -")
            if endereco:
                return endereco

    for idx, line in enumerate(lines_original):
        line_ascii = _to_ascii_upper(line)
        if not line_ascii.startswith("ENDERECO:"):
            continue
        endereco = line.split(":", 1)[1].strip() if ":" in line else line.strip()
        endereco = re.sub(r"\s+", " ", endereco).strip(" -")
        if endereco:
            return endereco

    return ""


def _parse_tributos_linha(lines_ascii: list[str], marcador: str) -> float | None:
    """Extrai o valor R$ (último número) do tributo.
    Aceita tanto 'ICMS ...' quanto '(XX) ICMS ...' (prefixo de código).
    """
    pat = re.compile(r"(?:[\(\[].{0,6}[\)\]]\s*)?" + re.escape(marcador) + r"\b")
    for line in lines_ascii:
        if not pat.match(line):
            continue
        nums = re.findall(r"[\d\.]+,\d+", line)
        if nums:
            return _to_float_br(nums[-1])
    return None


def _parse_tributos_aliquota(lines_ascii: list[str], marcador: str) -> float | None:
    """Extrai a alíquota % — primeiro número se ≤ 35, senão o segundo.
    Aceita tanto 'ICMS ...' quanto '(XX) ICMS ...' (prefixo de código).
    """
    pat = re.compile(r"(?:[\(\[].{0,6}[\)\]]\s*)?" + re.escape(marcador) + r"\b")
    for line in lines_ascii:
        if not pat.match(line):
            continue
        nums = re.findall(r"[\d\.]+,\d+", line)
        if len(nums) < 2:
            continue
        v0 = abs(_to_float_br(nums[0]) or 0.0)
        v1 = abs(_to_float_br(nums[1]) or 0.0)
        return v0 if v0 <= 35 else v1
    return None


def _parse_tributo_componentes(lines_ascii: list[str], marcador: str) -> tuple[float | None, float | None, float | None]:
    """Retorna (base, aliquota, valor) do tributo.
    Detecta automaticamente a ordem: se o 1º número ≤ 35 é alíquota, senão é base.
    """
    pat = re.compile(r"(?:[\(\[].{0,6}[\)\]]\s*)?" + re.escape(marcador) + r"\b")
    for line in lines_ascii:
        if not pat.match(line):
            continue
        nums = re.findall(r"-?[\d\.]+,\d+", line)
        if len(nums) >= 3:
            v0 = abs(_to_float_br(nums[0]) or 0.0)
            v1 = abs(_to_float_br(nums[1]) or 0.0)
            v2 = abs(_to_float_br(nums[2]) or 0.0)
            if v0 <= 35 and v1 > v0:
                return v1, v0, v2  # base, aliquota, valor
            return v0, v1, v2
    return None, None, None


def _parse_cosip(lines_ascii: list[str]) -> float | None:
    for line in lines_ascii:
        if "(C0) COSIP" not in line and "COSIP MUNICIPAL" not in line:
            continue
        nums = re.findall(r"-?[\d\.]+,\d+", line)
        for n in nums:
            v = _to_float_br(n)
            if v and abs(v) > 0.001:
                return v
    return None


def _parse_retidos(lines_ascii: list[str]) -> dict[str, float | None]:
    # CELESC format: "(BC) TRIBUTO RETIDO COFINS 3,00%  0,000  0,00000  -87,14 ..."
    mapa: dict[str, float | None] = {"cofins": None, "csll": None, "irpj": None, "pis": None}
    code_map = {
        "(BC)": ("cofins", "COFINS"),
        "(BD)": ("csll",   "CSLL"),
        "(BE)": ("irpj",   "IRPJ"),
        "(BF)": ("pis",    "PIS"),
    }
    for line in lines_ascii:
        if "RETIDO" not in line:
            continue
        for code, (chave, nome) in code_map.items():
            if code not in line or nome not in line:
                continue
            nums = re.findall(r"-?[\d\.]+,\d+", line)
            # Columns: aliquota%  qty  tariff  valor_R$  ...  (valor is first negative)
            val: float | None = None
            for n in nums:
                v = _to_float_br(n)
                if v is not None and v < 0:
                    val = abs(v)
                    break
            if val is None and len(nums) >= 4:
                val = abs(_to_float_br(nums[3]) or 0.0) or None
            if val:
                mapa[chave] = round((mapa[chave] or 0.0) + val, 2)
    return mapa


def _parse_obs_valor(lines_ascii: list[str]) -> float | None:
    # (AM) = MULTA POR ATRASO, (AH) = JUROS DE MORA; demais códigos de ajuste/crédito
    OBS_PREFIXES = ("(AH)", "(AM)", "(AW)", "(A1)", "(A2)", "(A3)", "(AI)", "(AX)", "CRED VIOL", "COBRANCA AJUSTE")
    total = 0.0
    found = False
    for line in lines_ascii:
        if not any(line.startswith(p) for p in OBS_PREFIXES):
            continue
        nums = re.findall(r"-?[\d\.]+,\d+", line)
        if len(nums) >= 3:
            v = _to_float_br(nums[2])
            if v is not None:
                total += v
                found = True
    return round(total, 2) if found else None


def _parse_codigo_barras(text_ascii: str) -> str:
    match = re.search(
        r"(23790\.\d{5}\s+\d{5}\.\d{6}\s+\d{5}\.\d{6}\s+\d\s+\d{14})",
        text_ascii,
    )
    if match:
        return re.sub(r"\s+", " ", match.group(1)).strip()
    match2 = re.search(r"237-2\s+([\d\.\s]+)", text_ascii)
    if match2:
        return re.sub(r"\s+", "", match2.group(1))[:47]
    return ""


# ── Parsers específicos MT (demanda) ─────────────────────────────────────────

def _parse_tarifa_subgrupo(text_ascii: str) -> tuple[str, str]:
    """Detecta tarifa (Verde/Azul) e subgrupo (A4, A3a, etc.)."""
    tarifa = "Convencional"
    if "VERDE" in text_ascii:
        tarifa = "Verde"
    elif "AZUL" in text_ascii:
        tarifa = "Azul"

    subgrupo = ""
    # Padrão: GRUPO/SUBGRUPO TENSAO: A/A4 ou similar
    match = re.search(r"GRUPO/SUBGRUPO\s+TENSAO[:\s]+([AB])/([A-Z0-9]+)", text_ascii, re.IGNORECASE)
    if match:
        grupo = match.group(1).upper()
        candidato = match.group(2).upper()
        if grupo == "A":
            subgrupo = candidato
        else:
            subgrupo = candidato
    else:
        match2 = re.search(r"\b(A4|A3A|A3|A2|A1)\b", text_ascii)
        if match2:
            subgrupo = match2.group(1).upper()

    return tarifa, subgrupo


def _parse_demanda_contratada(lines_ascii: list[str]) -> tuple[float | None, float | None]:
    """Demanda contratada ponta e fora-ponta (kW).
    Padrão CELESC MT: linha com 'DEMANDA CONTRATADA' contendo dois valores kW.
    Verde: apenas FP. Azul: ponta e FP.
    """
    dem_ponta = None
    dem_fp = None
    for line in lines_ascii:
        if "DEMANDA CONTRATADA" not in line or "(2D)" in line or "DIFERENCA" in line:
            continue
        nums = re.findall(r"[\d\.]+,\d+", line)
        if len(nums) == 1:
            dem_fp = _to_float_br(nums[0])
        elif len(nums) >= 2:
            dem_ponta = _to_float_br(nums[0])
            dem_fp = _to_float_br(nums[1])
        break

    if dem_ponta is None and dem_fp is None:
        for line in lines_ascii:
            match = re.search(
                r"DEMANDA\s+([\d\.]+,\d+|\d+)\s*KW\s+MEDIDA\s+([\d\.]+,\d+|\d+)\s*KW\s+([\d\.]+,\d+|\d+)\s*KW",
                line,
            )
            if match:
                dem_fp = _to_float_br(match.group(1))
                break

    # Layout antigo CELESC A4 Verde: "Demanda - KW 39,000 ..."
    if dem_ponta is None and dem_fp is None:
        for line in lines_ascii:
            m = re.match(r"DEMANDA\s*-\s*KW\s+([\d\.]+,\d+|\d+)", line)
            if m:
                dem_fp = _to_float_br(m.group(1))
                break
    return dem_ponta, dem_fp


def _parse_demanda_registrada(lines_ascii: list[str], tarifa: str) -> tuple[float | None, float | None]:
    """Demanda registrada ponta e fora-ponta.
    Padrão: (01) DEMANDA PONTA  kW  val  ou (02) DEMANDA FP kW val.
    """
    dem_p_reg = None
    dem_fp_reg = None
    for line in lines_ascii:
        if "(01)" in line and "DEMANDA" in line and "PONTA" in line and "FP" not in line:
            nums = re.findall(r"[\d\.]+,\d+", line)
            if nums:
                dem_p_reg = _to_float_br(nums[0])
        elif "(02)" in line and "DEMANDA" in line:
            nums = re.findall(r"[\d\.]+,\d+", line)
            if nums:
                dem_fp_reg = _to_float_br(nums[0])
        elif "(01)" in line and "DEMANDA" in line and tarifa == "Verde":
            # Verde: campo único
            nums = re.findall(r"[\d\.]+,\d+", line)
            if nums:
                dem_fp_reg = _to_float_br(nums[0])

    if dem_p_reg is None or dem_fp_reg is None:
        for line in lines_ascii:
            match = re.search(
                r"DEMANDA\s+([\d\.]+,\d+|\d+)\s*KW\s+MEDIDA\s+([\d\.]+,\d+|\d+)\s*KW\s+([\d\.]+,\d+|\d+)\s*KW",
                line,
            )
            if not match:
                continue
            if dem_p_reg is None:
                dem_p_reg = _to_float_br(match.group(2))
            if dem_fp_reg is None:
                dem_fp_reg = _to_float_br(match.group(3))
            break

    if dem_fp_reg is None:
        for line in lines_ascii:
            if "(0T)" not in line or "DEMANDA" not in line:
                continue
            nums = re.findall(r"[\d\.]+,\d+", line)
            if nums:
                dem_fp_reg = _to_float_br(nums[0])
                break

    # Layout antigo CELESC A4 Verde: "Demanda - KW 39,000 ..."
    # Quando "faturada pela demanda contratada", registrada = contratada.
    if dem_fp_reg is None:
        for line in lines_ascii:
            m = re.match(r"DEMANDA\s*-\s*KW\s+([\d\.]+,\d+|\d+)", line)
            if m:
                dem_fp_reg = _to_float_br(m.group(1))
                break
    return dem_p_reg, dem_fp_reg


def _parse_demanda_faturada(lines_ascii: list[str]) -> tuple[float | None, float | None]:
    """Demanda faturada ponta e fora-ponta (kW) — itens da fatura."""
    dem_p_fat = None
    dem_fp_fat = None
    for line in lines_ascii:
        up = line.upper()
        if "DEMANDA" not in up:
            continue
        # (03) DEMANDA FATURADA PONTA  ou (04) DEMANDA FATURADA FP
        if ("(03)" in line or "FATURADA PONTA" in up) and "FP" not in up:
            nums = re.findall(r"[\d\.]+,\d+", line)
            if nums:
                dem_p_fat = _to_float_br(nums[0])
        elif "(04)" in line or ("FATURADA" in up and ("FP" in up or "FORA" in up)):
            nums = re.findall(r"[\d\.]+,\d+", line)
            if nums:
                dem_fp_fat = _to_float_br(nums[0])

    if dem_p_fat is None and dem_fp_fat is None:
        for line in lines_ascii:
            if "FATURADA" not in line or "KW" not in line:
                continue
            match = re.search(r"FATURADA\s+([\d\.]+,\d+|\d+)\s*KW", line)
            if match:
                dem_fp_fat = _to_float_br(match.group(1))
                break

    if dem_fp_fat is None:
        for line in lines_ascii:
            if "(0T)" not in line or "DEMANDA" not in line:
                continue
            nums = re.findall(r"[\d\.]+,\d+", line)
            if nums:
                dem_fp_fat = _to_float_br(nums[0])
                break
    return dem_p_fat, dem_fp_fat


def _parse_consumo_mt(lines_ascii: list[str]) -> tuple[float | None, float | None, float | None, float | None]:
    """Consumo ativo ponta e fora-ponta, registrado e faturado.
    Padrão CELESC MT:
      (0D) CONSUMO TE PONTA kWh val
      (0E) CONSUMO TE FP kWh val
      ou (0D)/(0E) TUSD
    Retorna: con_p_fat, con_fp_fat, con_p_reg, con_fp_reg
    """
    con_p_fat  = None
    con_fp_fat = None
    con_p_reg  = None
    con_fp_reg = None
    for line in lines_ascii:
        if "CONSUMO" not in line:
            continue
        nums = re.findall(r"[\d\.]+,\d+", line)
        if not nums:
            continue
        qtd = _to_float_br(nums[0])
        if qtd is None:
            continue

        if line.startswith("(04)") or line.startswith("(0E)"):
            con_fp_fat = qtd
            con_fp_reg = qtd
        elif line.startswith("(0A)") or line.startswith("(0D)"):
            con_p_fat = qtd
            con_p_reg = qtd
    return con_p_fat, con_fp_fat, con_p_reg, con_fp_reg


def _parse_demanda_excedente(lines_ascii: list[str]) -> tuple[float | None, float | None]:
    # (2D) "Diferença de Demanda Contratada" é o mínimo contratado não utilizado
    # (contratada - medida), NÃO ultrapassagem (que é medida > contratada).
    # Para CELESC MT Verde, este campo não deve ser mapeado como excedente/ultrapassagem.
    return None, None


def _parse_energia_reativa_ufer(
    lines_ascii: list[str],
) -> tuple[float | None, float | None, float | None]:
    """Extrai consumo de energia reativa excedente (UFER) fora-ponta:
      registrado (kVArh), faturado (kVArh) e valor R$.

    Padrões CELESC MT:
      '(1O) ENERGIA REATIVA EXCEDENTE kVArh  qty  tarif  valor_R$  ...'
      '(0M) ENERGIA REATIVA FP  kVArh  qty  tarif  valor_R$  ...'
      '(0N) ENERGIA REATIVA PONTA ...' (ponta — ignorado aqui, foco em FP)
    Retorna (registrado_kvarh, faturado_kvarh, valor_reais).
    """
    kwh_reg = None
    kwh_fat = None
    valor = None

    REATIVA_FP_KEYS = ("(0M)", "(1O)", "ENERGIA REATIVA FP", "ENERGIA REATIVA FORA", "REATIVA EXCEDENTE")

    for line in lines_ascii:
        if not any(k in line for k in REATIVA_FP_KEYS):
            continue
        # Ignora linhas de ponta pura
        if "(0N)" in line and "FP" not in line and "FORA" not in line:
            continue
        nums = re.findall(r"[\d\.]+,\d+", line)
        if len(nums) < 3:
            continue
        # Colunas: qty  tarif  valor_R$  componentes...  ICMS_aliq  ICMS_val  tarif_unit
        qtd = abs(_to_float_br(nums[0]) or 0.0)
        val = abs(_to_float_br(nums[2]) or 0.0)   # índice 2 = R$ total da linha
        if qtd > 0:
            kwh_reg = qtd
            kwh_fat = qtd
        if val > 0 and val != qtd:
            valor = val
        break  # só a primeira linha FP importa

    return kwh_reg, kwh_fat, valor


def _parse_demanda_valor_reais(lines_ascii: list[str]) -> tuple[float | None, float | None]:
    """Extrai os valores R$ de demanda ponta e fora-ponta das linhas de fatura.
    Colunas CELESC: CODE DESC UNIT qty tarif valor_R$  componentes...
    O R$ total está sempre no índice 2 (terceiro número).
    Padrões:
      Verde: '(0T) DEMANDA KW  qty  tarif  valor_R$  ...'
      Azul:  '(03) DEMANDA FATURADA PONTA KW  qty  tarif  valor_R$  ...'
             '(04) DEMANDA FATURADA FP    KW  qty  tarif  valor_R$  ...'
    """
    val_p = None
    val_fp = None
    for line in lines_ascii:
        if "DEMANDA" not in line:
            continue
        # Ignora linhas de diferença/contratada (sem valor monetário de fatura)
        if "(2D)" in line or "CONTRATADA" in line or "DIFERENCA" in line:
            continue
        nums = re.findall(r"[\d\.]+,\d+", line)
        if len(nums) < 3:
            continue
        # Índice 2 = valor R$ total da linha de cobrança
        v_reais = _to_float_br(nums[2])
        if v_reais is None or v_reais <= 0:
            continue
        up = line.upper()
        if "(03)" in line or ("PONTA" in up and "FP" not in up and "FORA" not in up and "FATURADA" in up):
            val_p = v_reais
        elif "(04)" in line or (("FP" in up or "FORA" in up) and "FATURADA" in up):
            val_fp = v_reais
        elif "(0T)" in line:
            # Verde: demanda única — vai para FP (não há ponta separada)
            val_fp = v_reais
    return val_p, val_fp


def _parse_beneficio_desconto(lines_ascii: list[str]) -> tuple[float | None, float | None, float | None]:
    """Retorna (beneficio_bruto, beneficio_liquido, desconto_fio).
    CELESC MT usa abreviação "TARIF." em vez de "TARIFARIO":
      '(2Z) BENEFICIO TARIF. BRUTO  0,000  0,000000  538,91  ...'
      '(31) BENEFICIO TARIF. LIQUIDO  0,000  0,000000  -438,43  ...'
      '(AA) BENEFICIO TARIF. BRUTO - TUSD / LIQUIDO - TUSD  ...'
    Colunas: qty tarif valor_R$  ...  → R$ no índice 2 (terceiro número).
    Acumula múltiplas linhas de bruto e liquido.
    """
    bruto: float | None = None
    liquido: float | None = None
    desconto_fio: float | None = None
    for line in lines_ascii:
        nums = re.findall(r"-?[\d\.]+,\d+", line)
        if len(nums) < 3:
            continue
        # R$ total está no índice 2 para linhas de fatura CELESC
        v = _to_float_br(nums[2])
        if v is None:
            continue
        up = line.upper()
        is_bruto   = "BENEFICIO" in up and "BRUTO" in up and "LIQUIDO" not in up
        is_liquido = "BENEFICIO" in up and "LIQUIDO" in up
        if is_bruto:
            bruto = round((bruto or 0.0) + v, 2)
        elif is_liquido:
            liquido = round((liquido or 0.0) + v, 2)
        elif "DESCONTO FIO" in up or "SUBSIDIO FIO" in up or "DESC FIO" in up:
            v_last = _to_float_br(nums[-1])
            if v_last is not None:
                desconto_fio = v_last
    return bruto, liquido, desconto_fio


def _parse_energia_injetada(lines_ascii: list[str]) -> tuple[float | None, float | None]:
    """(0R) TE injetada → fatConFPontaInjetadoFaturado; (0S) TUSD → fatConFPontaInjetadoRegistrado."""
    kwh_te = 0.0
    kwh_tusd = 0.0
    found_te = found_tusd = False
    for line in lines_ascii:
        if "(0R) ENERGIA INJET" in line or "(0R) ENERGIA INJ" in line:
            nums = re.findall(r"[\d\.]+,\d+", line)
            if nums:
                v = _to_float_br(nums[0])
                if v:
                    kwh_te += v
                    found_te = True
        elif "(0S) ENERGIA INJ" in line:
            nums = re.findall(r"[\d\.]+,\d+", line)
            if nums:
                v = _to_float_br(nums[0])
                if v:
                    kwh_tusd += v
                    found_tusd = True
    return (round(kwh_te, 3) if found_te else None), (round(kwh_tusd, 3) if found_tusd else None)


# ── Extração principal ───────────────────────────────────────────────────────

def extrair_campos(pdf_path: Path) -> dict:
    row: dict[str, object] = {h: None for h in HEADERS}
    row["ARQUIVO"] = str(pdf_path)
    row["fatCarimbo"] = _carimbo_from_path(pdf_path)
    row["ERRO"] = ""

    try:
        lines_original, lines_ascii, text_original, text_ascii = _extract_pages(pdf_path)
    except Exception as exc:
        row["ERRO"] = f"Falha ao ler PDF: {exc}"
        log.error("  Erro ao ler %s: %s", pdf_path.name, exc)
        return row

    try:
        referencia, vencimento, valor_total = _parse_ref_vcto_valor(text_ascii)
        row["fatDataVcto"]       = _to_date(vencimento)
        row["fatValorFatura"]    = valor_total
        row["fatValorNotaFiscal"] = valor_total

        if referencia:
            try:
                m, a = referencia.split("/")
                row["fatDataReferencia"] = dt.date(int(a), int(m), 1)
            except Exception:
                pass

        nf_num, nf_data = _parse_emissao_nf(text_ascii)
        row["NOTAFISCAL"]    = nf_num
        row["fatDataEmissao"] = _to_date(nf_data)

        leitura_ant, leitura_at = _parse_leitura_datas(text_ascii)
        row["fatDataLeituraAnterior"] = _to_date(leitura_ant)
        row["fatDataLeituraAtual"]    = _to_date(leitura_at)

        row["Instalacao"]    = _parse_instalacao(lines_ascii, text_ascii)
        row["fatCarimbo"] = _resolver_carimbo_master(
            pdf_path,
            row.get("Instalacao"),
            row.get("fatDataReferencia"),
        )
        row["CNPJ"]          = _parse_cnpj(text_ascii)
        row["CODIGOCLIENTE"] = _parse_codigo_cliente(text_ascii)
        row["ENDERECO"]      = _parse_endereco(lines_original)

        # Tarifa e subgrupo detectados
        tarifa, subgrupo = _parse_tarifa_subgrupo(text_ascii)
        row["TARIFA_DETECTADA"]   = tarifa
        row["SUBGRUPO_DETECTADO"] = subgrupo

        # Códigos CONSEN para CELESC MT — derivados da tarifa/subgrupo detectados
        # TODO: confirmar mapeamento com equipe CONSEN e ajustar TARIFA_COD/SUBGRUPO_COD acima
        row["concCod"]        = 35
        row["cadTarifaCod"]   = TARIFA_COD.get(tarifa)
        row["cadSubGrupoCod"] = SUBGRUPO_COD.get(subgrupo)

        icms_base, icms_aliq, icms_val = _parse_tributo_componentes(lines_ascii, "ICMS")
        row["fatICMS"]              = icms_val if icms_val is not None else _parse_tributos_linha(lines_ascii, "ICMS")
        row["fatDesIcmsAliquota"]   = icms_aliq if icms_aliq is not None else _parse_tributos_aliquota(lines_ascii, "ICMS")
        row["fatPIS"]               = _parse_tributos_linha(lines_ascii, "PIS")
        row["fatDescPisAliquota"]   = _parse_tributos_aliquota(lines_ascii, "PIS")
        row["fatCOFINS"]            = _parse_tributos_linha(lines_ascii, "COFINS")
        row["fatDescCofinsAliquota"] = _parse_tributos_aliquota(lines_ascii, "COFINS")

        # Base de Cálculo: base ICMS quando disponível; senão total da fatura
        if icms_base is not None:
            row["fatValorNotaFiscal"] = icms_base

        row["fatIlumPublica"] = _parse_cosip(lines_ascii)

        # Demanda contratada
        dem_p_cont, dem_fp_cont = _parse_demanda_contratada(lines_ascii)
        row["fatDemContratadaPonta"]  = dem_p_cont
        row["fatDemContratadaFPonta"] = dem_fp_cont

        # Demanda registrada
        dem_p_reg, dem_fp_reg = _parse_demanda_registrada(lines_ascii, tarifa)
        row["fatDemPontaRegistrada"]     = dem_p_reg
        row["fatDemFPontaIndRegistrada"] = dem_fp_reg

        # Demanda faturada e valor R$
        dem_p_fat, dem_fp_fat = _parse_demanda_faturada(lines_ascii)
        # CELESC imprime demanda faturada arredondada no PDF.
        # O correto é max(registrada, contratada) — usa valor exato registrado quando Reg > Cont.
        if dem_fp_reg is not None and dem_fp_cont is not None:
            dem_fp_fat_correto = max(dem_fp_reg, dem_fp_cont)
            if dem_fp_fat is None or round(dem_fp_fat) == round(dem_fp_fat_correto):
                dem_fp_fat = dem_fp_fat_correto
        if dem_p_reg is not None and dem_p_cont is not None:
            dem_p_fat_correto = max(dem_p_reg, dem_p_cont)
            if dem_p_fat is None or round(dem_p_fat) == round(dem_p_fat_correto):
                dem_p_fat = dem_p_fat_correto
        row["fatDemPonta"]         = dem_p_fat
        row["fatDemFPontaIndutivo"] = dem_fp_fat

        dem_p_val, dem_fp_val = _parse_demanda_valor_reais(lines_ascii)
        row["fatDemPontaValorReais"]     = dem_p_val
        row["fatDemFPontaIndValorReais"] = dem_fp_val

        # Diferença/excedente de demanda contratada
        dem_p_exc, dem_fp_exc = _parse_demanda_excedente(lines_ascii)
        row["fatDemPontaExcFaturada"]     = dem_p_exc
        row["fatDemFPontaExcFaturada"]    = dem_fp_exc
        row["fatDemPontaExcRegistrada"]   = dem_p_exc
        row["fatDemFPontaExcRegistrada"]  = dem_fp_exc

        # Consumo ativo
        con_p_fat, con_fp_fat, con_p_reg, con_fp_reg = _parse_consumo_mt(lines_ascii)
        row["fatConPontaFaturado"]       = con_p_fat
        row["fatConFPontaIndFaturado"]   = con_fp_fat
        row["fatConPontaRegistrado"]     = con_p_reg
        row["fatConFPontaIndRegistrado"] = con_fp_reg

        # Energia Reativa Excedente UFER (fora-ponta) → campos Exc FP
        reativa_reg, reativa_fat, reativa_val = _parse_energia_reativa_ufer(lines_ascii)
        row["fatConFPontaIndExcRegistrado"] = reativa_reg
        row["fatConFPontaIndExcFaturado"]   = reativa_fat
        row["fatConFPontaIndExcValorReais"] = reativa_val

        # Energia injetada (GD)
        kwh_te_inj, kwh_tusd_inj = _parse_energia_injetada(lines_ascii)
        row["fatConFPontaInjetadoFaturado"]   = kwh_te_inj
        row["fatConFPontaInjetadoRegistrado"] = kwh_tusd_inj

        retidos = _parse_retidos(lines_ascii)
        row["fatDescCofinsValRetImposto"] = retidos.get("cofins")
        row["fatDescCsllValRetImposto"]   = retidos.get("csll")
        row["fatDescIrpjValRetImposto"]   = retidos.get("irpj")
        row["fatDescPisValRetImposto"]    = retidos.get("pis")

        # Benefício Bruto/Líquido e Desconto Fio B
        ben_bruto, ben_liquido, desconto_fio = _parse_beneficio_desconto(lines_ascii)
        row["fatBeneficioTarifarioBrutoValorReais"] = ben_bruto
        row["fatBeneficioLiquidoValorReais"]        = ben_liquido
        row["fatDescontoFio"]                       = desconto_fio

        row["obsValor"] = _parse_obs_valor(lines_ascii)

        row["fatCodigoBarras"] = _parse_codigo_barras(text_ascii)

        if subgrupo and not subgrupo.startswith("A"):
            row["ERRO"] = f"Fatura fora do escopo MT (subgrupo {subgrupo})"

    except Exception as exc:
        row["ERRO"] = str(exc)
        log.error("  Erro ao extrair campos de %s: %s", pdf_path.name, exc)

    return row


def _to_date(valor: str) -> dt.date | None:
    if not valor:
        return None
    try:
        d, m, a = valor.strip().split("/")
        return dt.date(int(a), int(m), int(d))
    except Exception:
        return None


# ── Geração do XLSX ──────────────────────────────────────────────────────────

def gerar_xlsx(linhas: list[dict], caminho: Path) -> None:
    _mkdir_seguro(caminho.parent)
    df = pd.DataFrame(linhas, columns=HEADERS)

    for col in HEADERS:
        if col in DATE_HEADERS:
            df[col] = pd.to_datetime(df[col], errors="coerce")
        elif col not in TEXT_HEADERS:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    with pd.ExcelWriter(caminho, engine="openpyxl", date_format="DD/MM/YYYY", datetime_format="DD/MM/YYYY") as writer:
        df.to_excel(writer, index=False, sheet_name="OCR_CELESC_MT")
        ws = writer.sheets["OCR_CELESC_MT"]

        date_cols = {h: idx + 1 for idx, h in enumerate(HEADERS) if h in DATE_HEADERS}
        for col_name, col_num in date_cols.items():
            for row_num in range(2, len(linhas) + 2):
                cell = ws.cell(row=row_num, column=col_num)
                if cell.value is not None:
                    cell.number_format = "DD/MM/YYYY"

        for col_cells in ws.columns:
            max_len = max((len(str(c.value or "")) for c in col_cells), default=8)
            ws.column_dimensions[col_cells[0].column_letter].width = min(max_len + 2, 30)

    log.info("XLSX salvo: %s (%s faturas)", caminho, len(linhas))


# ── CLI ──────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    hoje = dt.date.today()
    p = argparse.ArgumentParser(description="OCR CELESC MT — extrai campos das faturas média tensão")
    p.add_argument("--mes",  type=str, default=f"{hoje.month:02d}")
    p.add_argument("--ano",  type=str, default=str(hoje.year))
    p.add_argument("--pasta", type=str, default="",
                   help="Pasta com os PDFs (override do padrão MM.YYYY/MT)")
    p.add_argument("--carimbo", action="append", default=[],
                   help="Processa só este(s) carimbo(s). Ex: --carimbo BB_2003260")
    p.add_argument("--saida", type=str, default="",
                   help="Caminho completo do XLSX de saída (override)")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    mes = f"{int(args.mes):02d}"
    ano = str(int(args.ano))
    pasta_mes = f"{mes}.{ano}"

    pasta = Path(args.pasta.strip()) if args.pasta.strip() else DOWNLOAD_DIR / pasta_mes / TENSAO
    xlsx_saida = Path(args.saida.strip()) if args.saida.strip() else OCR_DIR / f"ocr_celesc_MT_{mes}{ano}.xlsx"

    log.info("=" * 60)
    log.info("  OCR CELESC MT  %s/%s", mes, ano)
    log.info("=" * 60)
    log.info("  Pasta PDFs : %s", pasta)
    log.info("  XLSX saída : %s", xlsx_saida)

    if not pasta.exists():
        log.error("Pasta não encontrada: %s", pasta)
        return 1

    carimbos_filtro = {c.strip().upper() for c in args.carimbo if c.strip()}
    pdfs = sorted(pasta.glob("*.pdf"))
    if carimbos_filtro:
        pdfs = [p for p in pdfs if p.stem.upper() in carimbos_filtro]
    if not pdfs:
        log.warning("Nenhum PDF encontrado em: %s", pasta)
        return 2

    log.info("  PDFs encontrados: %s", len(pdfs))

    linhas: list[dict] = []
    erros = 0
    for idx, pdf in enumerate(pdfs, start=1):
        log.info("[%s/%s] %s", idx, len(pdfs), pdf.name)
        row = extrair_campos(pdf)
        linhas.append(row)
        if row.get("ERRO"):
            erros += 1

    gerar_xlsx(linhas, xlsx_saida)

    log.info("Concluído: %s PDFs, %s erros.", len(linhas), erros)
    return 0 if erros == 0 else 3


if __name__ == "__main__":
    raise SystemExit(main())
