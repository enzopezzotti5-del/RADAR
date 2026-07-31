#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OCR CELESC BT
=============

Extrai campos das faturas CELESC BT (B3, tarifa convencional monômia) para
alimentar o fluxo de digitação do Consen.

Uso:
    python ocr_celesc_bt.py
    python ocr_celesc_bt.py --mes 04 --ano 2026
    python ocr_celesc_bt.py --pasta "\\\\servidor\\DOWNLOAD CELESC\\04.2026\\BT"
    python ocr_celesc_bt.py --carimbo BB_2003677

Saída:
    \\\\servidor\\OCR CELESC\\ocr_celesc_BT_042026.xlsx
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
TENSAO       = "BT"

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
    "fatConPontaValorReais", "fatConFPontaIndValorReais",
    "fatICMS", "fatPIS", "fatCOFINS", "fatValorNotaFiscal",
    "fatValBandeira", "fatValBandeira2",
    "fatMultasDiversas",
    "obsValor",
    "CNPJ", "fatDataReferencia",
    "fatConPontaInjetadoRegistrado", "fatConPontaInjetadoFaturado",
    "fatConFPontaInjetadoRegistrado", "fatConFPontaInjetadoFaturado",
    "fatConFPontaInjetadoValorReais", "fatConFPontaInjetadoUsina",
    "fatConFPontaInjetadoUsinaSaldoAcumulado",
    "fatCodigoBarras", "fatCarimbo", "usuCod",
    "fatDescPisAliquota", "fatDescCofinsAliquota", "fatDesIcmsAliquota",
    "fatDescPisPercRetImposto", "fatDescCofinsPercRetImposto",
    "fatDescCsllPercRetImposto", "fatDescIrpjPercRetImposto",
    "fatDescPisValRetImposto", "fatDescCofinsValRetImposto",
    "fatDescCsllValRetImposto", "fatDescIrpjValRetImposto",
    "ENDERECO", "NOTAFISCAL", "CODIGOCLIENTE",
    "TARIFA_DETECTADA", "ARQUIVO", "ERRO",
]

DATE_HEADERS = {
    "fatDataEmissao", "fatDataVcto", "fatDataCadastro",
    "fatDataLeituraAnterior", "fatDataLeituraAtual", "fatDataReferencia",
}
TEXT_HEADERS = {
    "Instalacao", "CNPJ", "ENDERECO", "NOTAFISCAL", "CODIGOCLIENTE",
    "fatCodigoBarras", "fatCarimbo", "usuCod",
    "TARIFA_DETECTADA", "ARQUIVO", "ERRO",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("ocr_celesc_bt")


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


def _detectar_grupo_subgrupo(text_ascii: str) -> tuple[str, str]:
    match = re.search(r"GRUPO/SUBGRUPO TENSAO[:\s]*([AB])/\s*([A-Z0-9]+)", text_ascii)
    if not match:
        return "", ""
    return match.group(1).strip().upper(), match.group(2).strip().upper()


def _classificar_pdf_bt(path: Path) -> tuple[bool, str]:
    try:
        _, _, _, text_ascii = _extract_pages(path)
    except Exception as exc:
        return False, f"falha_leitura:{exc}"

    grupo, subgrupo = _detectar_grupo_subgrupo(text_ascii)
    if grupo == "B" and subgrupo == "B3":
        return True, "B3"
    if grupo == "B":
        return False, f"subgrupo_bt_fora_escopo:{subgrupo or 'desconhecido'}"
    if grupo == "A":
        return False, f"grupo_a:{subgrupo or 'desconhecido'}"
    return False, "grupo_subgrupo_nao_detectado"


# ── Parsers específicos CELESC BT ────────────────────────────────────────────

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
        r"(\d{2}/\d{2}/\d{4})\s+(\d{2}/\d{2}/\d{4})\s+\d+\s+[A-Z\s]+?\s+\d{2}/\d{2}/\d{4}",
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
    for idx, line in enumerate(lines_original):
        if _to_ascii_upper(line).startswith("ENDERECO:"):
            partes = [line.split(":", 1)[1].strip()]
            j = idx + 1
            while j < len(lines_original):
                nxt_ascii = _to_ascii_upper(lines_original[j])
                if any(nxt_ascii.startswith(p) for p in ("CEP:", "CIDADE:", "CNPJ:", "CPF/")):
                    break
                partes.append(lines_original[j].strip())
                j += 1
            return " ".join(p for p in partes if p)
    return ""


def _parse_tributos_linha(lines_ascii: list[str], marcador: str) -> float | None:
    """Extrai o valor R$ (último número) da linha do tributo.
    Aceita tanto 'COFINS ...' quanto '(XX) COFINS ...' (prefixo de código).
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
    """Extrai a alíquota % do tributo (segundo número, salvo quando primeiro é alíquota).
    Aceita tanto 'COFINS ...' quanto '(XX) COFINS ...' (prefixo de código).
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
        # Alíquota % é sempre o menor entre os dois primeiros números (< base de cálculo)
        return min(v0, v1)
    return None


def _normalizar_aliquota_cofins_celesc(valor: float | None) -> float | None:
    """Retorna o valor tal qual; a normalização para 1,63/1,78 foi removida porque
    CELESC usa 2,64 % (e possivelmente outros valores) dependendo do período."""
    return valor


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


def _parse_tributo_componentes(lines_ascii: list[str], marcador: str) -> tuple[float | None, float | None, float | None]:
    """Retorna (base, aliquota, valor) do tributo.
    CELESC pode usar dois formatos:
      'ICMS  2.345,67  15,00  351,85'  → base aliq valor  (nums[0] grande)
      'ICMS  15,00  2.345,67  351,85'  → aliq base valor  (nums[0] pequeno ≤ 35)
    Detecta automaticamente pela magnitude do primeiro número.
    """
    for line in lines_ascii:
        if not (line.startswith(marcador) or
                re.match(r"[\(\[].{0,6}[\)\]]\s*" + re.escape(marcador), line)):
            continue
        nums = re.findall(r"-?[\d\.]+,\d+", line)
        if len(nums) >= 3:
            v0 = abs(_to_float_br(nums[0]) or 0.0)
            v1 = abs(_to_float_br(nums[1]) or 0.0)
            v2 = abs(_to_float_br(nums[2]) or 0.0)
            # Se nums[0] ≤ 35 e nums[1] >> nums[0], o primeiro é alíquota %
            if v0 <= 35 and v1 > v0:
                return v1, v0, v2  # base, aliquota, valor
            return v0, v1, v2
    return None, None, None


def _parse_saldo_final_beneficiaria(lines_ascii: list[str]) -> float | None:
    for line in lines_ascii:
        if "SALDO FINAL BENEFICIARIA" not in line:
            continue
        nums = re.findall(r"-?[\d\.]+,\d+|-?\d+", line)
        if nums:
            return _to_float_br(nums[0])
    return None


def _parse_consumo_faturado(
    lines_ascii: list[str],
) -> tuple[float | None, float | None, float | None, float | None]:
    """Retorna (te_kwh, tusd_kwh, te_valor_r$, tusd_valor_r$).
    CONSEN B3: TUSD qty → fatConFPontaIndFaturado; soma R$ → fatConFPontaIndValorReais.
    """
    consumo_te = 0.0
    consumo_tusd = 0.0
    valor_te = 0.0
    valor_tusd = 0.0
    found_te = False
    found_tusd = False
    for line in lines_ascii:
        if "(0D) CONSUMO TE" in line:
            nums = re.findall(r"-?[\d\.]+,\d+", line)
            if nums:
                qtd = _to_float_br(nums[0])
                val = _to_float_br(nums[2]) if len(nums) >= 3 else (_to_float_br(nums[-1]) if len(nums) > 1 else None)
                if qtd is not None:
                    consumo_te += abs(qtd)
                    found_te = True
                if val is not None:
                    valor_te += abs(val)
        elif "(0E) CONSUMO TUSD" in line:
            nums = re.findall(r"-?[\d\.]+,\d+", line)
            if nums:
                qtd = _to_float_br(nums[0])
                val = _to_float_br(nums[2]) if len(nums) >= 3 else (_to_float_br(nums[-1]) if len(nums) > 1 else None)
                if qtd is not None:
                    consumo_tusd += abs(qtd)
                    found_tusd = True
                if val is not None:
                    valor_tusd += abs(val)
    return (
        round(consumo_te, 3) if found_te else None,
        round(consumo_tusd, 3) if found_tusd else None,
        round(valor_te, 2) if valor_te else None,
        round(valor_tusd, 2) if valor_tusd else None,
    )


def _parse_energia_injetada(
    lines_ascii: list[str],
) -> tuple[float | None, float | None, float | None]:
    """Retorna (kwh_te, kwh_tusd, valor_r$_total).
    (0R) TE injetada → fatConFPontaInjetadoFaturado; (0S) TUSD → fatConFPontaInjetadoRegistrado.
    """
    kwh_te = 0.0
    kwh_tusd = 0.0
    valor_total = 0.0
    found_te = False
    found_tusd = False
    for line in lines_ascii:
        if "(0R) ENERGIA INJET" in line or "(0R) ENERGIA INJ" in line:
            nums = re.findall(r"-?[\d\.]+,\d+", line)
            if nums:
                qtd = _to_float_br(nums[0])
                val = _to_float_br(nums[2]) if len(nums) >= 3 else (_to_float_br(nums[-1]) if len(nums) > 1 else None)
                if qtd is not None:
                    kwh_te += abs(qtd)
                    found_te = True
                if val is not None:
                    valor_total += abs(val)
        elif "(0S) ENERGIA INJ" in line:
            nums = re.findall(r"-?[\d\.]+,\d+", line)
            if nums:
                qtd = _to_float_br(nums[0])
                val = _to_float_br(nums[2]) if len(nums) >= 3 else (_to_float_br(nums[-1]) if len(nums) > 1 else None)
                if qtd is not None:
                    kwh_tusd += abs(qtd)
                    found_tusd = True
                if val is not None:
                    valor_total += abs(val)
    return (
        round(kwh_te, 3) if found_te else None,
        round(kwh_tusd, 3) if found_tusd else None,
        round(valor_total, 2) if valor_total else None,
    )


def _parse_retidos(lines_ascii: list[str]) -> dict[str, float | None]:
    mapa = {"cofins": None, "csll": None, "irpj": None, "pis": None}
    markers = {
        "(BC) COFINS RETIDO": "cofins",
        "(BD) CSLL RETIDO": "csll",
        "(BE) IRPJ RETIDO": "irpj",
        "(BF) PIS RETIDO": "pis",
    }
    for line in lines_ascii:
        for marker, chave in markers.items():
            if marker in line:
                nums = re.findall(r"-?[\d\.]+,\d+", line)
                if len(nums) >= 3:
                    v = _to_float_br(nums[2])
                    if v is not None:
                        mapa[chave] = -abs(v)
    return mapa


def _parse_bandeira_celesc(lines_ascii: list[str]) -> tuple[float, float]:
    """Retorna (fatValBandeira, fatValBandeira2) = (adicional positivo, crédito negativo).

    Formato CELESC: (2L) BANDEIRA AMARELA KWH {qty} {rate} {total_R$} ...
    O total está na 3ª coluna numérica (índice 2), não na última (que é uma taxa interna).
    Linhas de injetado (2M) / INJET têm total negativo → crédito (fatValBandeira2).
    """
    band_pos = 0.0
    band_neg = 0.0
    for line in lines_ascii:
        up = line.upper()
        if "BANDEIRA" not in up and "BAND." not in up:
            continue
        nums = re.findall(r"-?[\d\.]+,\d+", line)
        if len(nums) < 3:
            continue
        # 3ª coluna numérica é o valor total em R$
        raw_val = _to_float_br(nums[2]) or 0.0
        val_abs = abs(raw_val)
        if val_abs < 0.01:
            continue
        # Injetado (crédito): negativo na fatura ou palavra "INJET" na linha
        if raw_val < 0 or "INJET" in up or "CRED" in up:
            band_neg += val_abs
        else:
            band_pos += val_abs
    return round(band_pos, 2), round(-band_neg, 2) if band_neg > 0 else 0.0


def _parse_obs_valor(lines_ascii: list[str]) -> float | None:
    OBS_PREFIXES = ("(AW)", "(A1)", "(A2)", "(A3)", "(AI)", "(AX)", "CRED VIOL", "COBRANCA AJUSTE")
    total = 0.0
    found = False
    for line in lines_ascii:
        is_obs = any(line.startswith(p) for p in OBS_PREFIXES)
        is_dmic = "DMIC" in line.upper()
        if not is_obs and not is_dmic:
            continue
        nums = re.findall(r"-?[\d\.]+,\d+", line)
        if is_obs and len(nums) >= 3:
            v = _to_float_br(nums[2])
            if v is not None:
                total += v
                found = True
        elif is_dmic:
            for n in nums:
                v = _to_float_br(n)
                if v is not None and abs(v) >= 0.5:
                    total += v
                    found = True
                    break
    return round(total, 2) if found else None


def _parse_codigo_barras(text_ascii: str) -> str:
    match = re.search(
        r"(23790\.\d{5}\s+\d{5}\.\d{6}\s+\d{5}\.\d{6}\s+\d\s+\d{14})",
        text_ascii,
    )
    if match:
        return re.sub(r"\s+", " ", match.group(1)).strip()
    match_compacto_pontuado = re.search(
        r"(23790\.\d{5}\d{5}\.\d{6}\d{5}\.\d{6}\d{15})",
        text_ascii,
    )
    if match_compacto_pontuado:
        return match_compacto_pontuado.group(1)
    match_compacto = re.search(r"(23790\d{43})", re.sub(r"\s+", "", text_ascii))
    if match_compacto:
        return match_compacto.group(1)
    match2 = re.search(r"237-2\s+([\d\.\s]+)", text_ascii)
    if match2:
        return re.sub(r"\s+", "", match2.group(1))[:47]
    return ""


def _parse_tarifa(text_ascii: str) -> str:
    # CELESC BT do pipeline atual entra sempre como B3 convencional.
    # Palavras como "Verde" podem aparecer em outros trechos da fatura e
    # contaminar a detecção textual se reutilizarmos a lógica de MT.
    return "Convencional"


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

    elegivel_bt, motivo_bt = _classificar_pdf_bt(pdf_path)
    if not elegivel_bt:
        row["ERRO"] = f"Fatura fora do escopo BT CELESC ({motivo_bt})"
        log.warning("  %s ignorado no OCR CELESC BT: %s", pdf_path.name, motivo_bt)
        return row

    try:
        referencia, vencimento, valor_total = _parse_ref_vcto_valor(text_ascii)
        row["fatDataVcto"] = _to_date(vencimento)
        row["fatValorFatura"] = valor_total
        row["fatValorNotaFiscal"] = valor_total

        if referencia:
            try:
                m, a = referencia.split("/")
                row["fatDataReferencia"] = dt.date(int(a), int(m), 1)
            except Exception:
                row["fatDataReferencia"] = None

        nf_num, nf_data = _parse_emissao_nf(text_ascii)
        row["NOTAFISCAL"] = nf_num
        row["fatDataEmissao"] = _to_date(nf_data)

        leitura_ant, leitura_at = _parse_leitura_datas(text_ascii)
        row["fatDataLeituraAnterior"] = _to_date(leitura_ant)
        row["fatDataLeituraAtual"] = _to_date(leitura_at)

        row["Instalacao"] = _parse_instalacao(lines_ascii, text_ascii)
        row["fatCarimbo"] = _resolver_carimbo_master(
            pdf_path,
            row.get("Instalacao"),
            row.get("fatDataReferencia"),
        )
        row["CNPJ"] = _parse_cnpj(text_ascii)
        row["CODIGOCLIENTE"] = _parse_codigo_cliente(text_ascii)
        row["ENDERECO"] = _parse_endereco(lines_original)

        # Códigos CONSEN fixos para CELESC BT (B3 convencional)
        row["concCod"]        = 35   # CELESC no CONSEN
        row["cadTarifaCod"]   = 1    # Convencional/monômio
        row["cadSubGrupoCod"] = 5    # B3

        row["TARIFA_DETECTADA"] = _parse_tarifa(text_ascii)

        # ICMS: extrai base (= fatValorNotaFiscal), aliquota e valor
        icms_base, icms_aliquota, icms_valor = _parse_tributo_componentes(lines_ascii, "ICMS")
        row["fatICMS"]            = icms_valor if icms_valor is not None else _parse_tributos_linha(lines_ascii, "ICMS")
        row["fatDesIcmsAliquota"] = icms_aliquota
        row["fatPIS"]             = _parse_tributos_linha(lines_ascii, "PIS")
        row["fatDescPisAliquota"] = _parse_tributos_aliquota(lines_ascii, "PIS")
        row["fatCOFINS"]          = _parse_tributos_linha(lines_ascii, "COFINS")
        row["fatDescCofinsAliquota"] = _normalizar_aliquota_cofins_celesc(
            _parse_tributos_aliquota(lines_ascii, "COFINS")
        )

        # Nota fiscal: base de cálculo ICMS (diferente do valor total da fatura para GD)
        if icms_base is not None:
            row["fatValorNotaFiscal"] = icms_base

        row["fatIlumPublica"] = _parse_cosip(lines_ascii)
        row["fatValBandeira"], row["fatValBandeira2"] = _parse_bandeira_celesc(lines_ascii)

        # Consumo B3 monômio: TUSD qty → FP; soma R$ TE + TUSD → FP valor
        consumo_te, consumo_tusd, valor_te, valor_tusd = _parse_consumo_faturado(lines_ascii)
        consumo_bt = consumo_tusd if consumo_tusd is not None else consumo_te
        valor_bt = round((valor_te or 0.0) + (valor_tusd or 0.0), 2) if (valor_te is not None or valor_tusd is not None) else None
        row["fatConPontaFaturado"]       = None
        row["fatConPontaRegistrado"]     = None
        row["fatConPontaValorReais"]     = None
        row["fatConFPontaIndFaturado"]   = consumo_bt
        row["fatConFPontaIndRegistrado"] = consumo_bt
        row["fatConFPontaIndValorReais"] = valor_bt

        # Energia injetada GD: usa TUSD qty para FP; soma R$ TE + TUSD
        kwh_te_inj, kwh_tusd_inj, valor_injetado = _parse_energia_injetada(lines_ascii)
        injetado_bt = kwh_tusd_inj if kwh_tusd_inj is not None else kwh_te_inj
        row["fatConFPontaInjetadoFaturado"]          = injetado_bt
        row["fatConFPontaInjetadoRegistrado"]         = injetado_bt
        row["fatConFPontaInjetadoValorReais"]         = valor_injetado
        row["fatConFPontaInjetadoUsina"]              = kwh_te_inj
        row["fatConFPontaInjetadoUsinaSaldoAcumulado"] = _parse_saldo_final_beneficiaria(lines_ascii)

        # Tributos retidos e percentuais fixos CELESC BT
        retidos = _parse_retidos(lines_ascii)
        row["fatDescCofinsValRetImposto"]  = retidos.get("cofins")
        row["fatDescCsllValRetImposto"]    = retidos.get("csll")
        row["fatDescIrpjValRetImposto"]    = retidos.get("irpj")
        row["fatDescPisValRetImposto"]     = retidos.get("pis")
        row["fatDescPisPercRetImposto"]    = 0.65
        row["fatDescCofinsPercRetImposto"] = 3.0
        row["fatDescCsllPercRetImposto"]   = 1.0
        row["fatDescIrpjPercRetImposto"]   = 1.2

        row["fatMultasDiversas"] = _parse_obs_valor(lines_ascii)
        row["obsValor"] = None

        row["fatCodigoBarras"] = _parse_codigo_barras(text_ascii)

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
        df.to_excel(writer, index=False, sheet_name="OCR_CELESC_BT")
        ws = writer.sheets["OCR_CELESC_BT"]

        from openpyxl.styles import numbers as xl_numbers
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
    p = argparse.ArgumentParser(description="OCR CELESC BT — extrai campos das faturas B3")
    p.add_argument("--mes",  type=str, default=f"{hoje.month:02d}")
    p.add_argument("--ano",  type=str, default=str(hoje.year))
    p.add_argument("--pasta", type=str, default="",
                   help="Pasta com os PDFs (override do padrão MM.YYYY/BT)")
    p.add_argument("--recursivo", action="store_true")
    p.add_argument("--somente-bt", action="store_true")
    p.add_argument("--carimbo", action="append", default=[],
                   help="Processa só este(s) carimbo(s). Ex: --carimbo BB_2003677")
    p.add_argument("--saida", type=str, default="",
                   help="Caminho completo do XLSX de saída (override)")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    mes = f"{int(args.mes):02d}"
    ano = str(int(args.ano))
    pasta_mes = f"{mes}.{ano}"

    pasta = Path(args.pasta.strip()) if args.pasta.strip() else DOWNLOAD_DIR / pasta_mes / TENSAO
    xlsx_saida = Path(args.saida.strip()) if args.saida.strip() else OCR_DIR / f"ocr_celesc_BT_{mes}{ano}.xlsx"

    log.info("=" * 60)
    log.info("  OCR CELESC BT  %s/%s", mes, ano)
    log.info("=" * 60)
    log.info("  Pasta PDFs : %s", pasta)
    log.info("  XLSX saída : %s", xlsx_saida)

    if not pasta.exists():
        log.error("Pasta não encontrada: %s", pasta)
        return 1

    carimbos_filtro = {c.strip().upper() for c in args.carimbo if c.strip()}
    pdfs = sorted(pasta.rglob("*.pdf")) if args.recursivo else sorted(pasta.glob("*.pdf"))
    if carimbos_filtro:
        pdfs = [p for p in pdfs if p.stem.upper() in carimbos_filtro]
    if not pdfs:
        log.warning("Nenhum PDF encontrado em: %s", pasta)
        return 2

    ignorados: list[tuple[Path, str]] = []
    pdfs_bt: list[Path] = []
    for pdf in pdfs:
        elegivel, motivo = _classificar_pdf_bt(pdf)
        if elegivel:
            pdfs_bt.append(pdf)
        else:
            ignorados.append((pdf, motivo))
    pdfs = pdfs_bt

    log.info("  PDFs encontrados: %s", len(pdfs) + len(ignorados))
    log.info("  PDFs BT B3    : %s", len(pdfs))
    log.info("  PDFs ignorados: %s", len(ignorados))
    for pdf, motivo in ignorados[:20]:
        log.info("    - ignorado %s (%s)", pdf.name, motivo)
    if len(ignorados) > 20:
        log.info("    - ... %s arquivos ignorados adicionais", len(ignorados) - 20)
    if not pdfs:
        log.warning("Nenhum PDF BT B3 encontrado em: %s", pasta)
        return 2

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
