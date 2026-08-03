#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OCR CELESC
==========

Extrai campos das faturas CELESC (Grupo A/B3, tarifa monômia) para alimentar
o fluxo de digitação do Consen.

Uso:
    python ocr_celesc.py
    python ocr_celesc.py --mes 04 --ano 2026
    python ocr_celesc.py --pasta "\\\\servidor\\DOWNLOAD CELESC\\04.2026\\MT"
    python ocr_celesc.py --carimbo BB_2003260

Saída:
    \\\\servidor\\OCR CELESC\\ocr_celesc_042026.xlsx
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import re
import sys
import unicodedata
from pathlib import Path

import pandas as pd
import pdfplumber


ROOT_DIR = Path(r"\\10.10.250.21\Energia\ARQUIVOS ENZO")
DOWNLOAD_DIR = ROOT_DIR / "DOWNLOAD CELESC"
OCR_DIR = ROOT_DIR / "OCR CELESC"
TENSAO = "MT"

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
    "fatDemFPontaIndUltraValorReais",
    "fatDemPontaValorReais", "fatDemFPontaIndValorReais",
    "fatConPontaRegistrado", "fatConFPontaIndRegistrado",
    "fatConFPontaCapRegistrado", "fatConIntermediarioRegistrado",
    "fatConPontaFaturado", "fatConFPontaIndFaturado",
    "fatConFPontaCapFaturado", "fatConIntermediarioFaturado",
    "fatConPontaExcRegistrado", "fatConFPontaIndExcRegistrado",
    "fatConFPontaCapExcRegistrado", "fatConPontaExcFaturado",
    "fatConFPontaIndExcFaturado", "fatConFPontaCapExcFaturado",
    "fatConPontaValorReais", "fatConFPontaIndValorReais",
    "fatConPontaExcValorReais", "fatConFPontaIndExcValorReais",
    "fatICMS", "fatPIS", "fatCOFINS", "fatValorNotaFiscal",
    "fatBeneficioTarifarioBrutoValorReais", "fatBeneficioLiquidoValorReais",
    "fatEscassezHidrica", "fatEscassezHidricaValorReais",
    "fatDescontoFio", "fatMultasDiversas",
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
    "fatDescConsumoPercRetImposto", "fatDescDemandaPercRetImposto",
    "fatDescPisValRetImposto", "fatDescCofinsValRetImposto",
    "fatDescCsllValRetImposto", "fatDescIrpjValRetImposto",
    "fatDescConsumoValRetImposto", "fatDescDemandaValRetImposto",
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
    "cadTarifaCod", "cadSubGrupoCod",
    "TARIFA_DETECTADA", "ARQUIVO", "ERRO",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("ocr_celesc")


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
    match = re.search(r"(\d{7})", path.stem)
    return match.group(1) if match else path.stem


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


# ── Parsers específicos CELESC ────────────────────────────────────────────────

def _parse_ref_vcto_valor(text_ascii: str) -> tuple[str, str, float | None]:
    """Extrai referência (MM/YYYY), vencimento (DD/MM/YYYY) e valor total."""
    match = re.search(
        r"(\d{2}/\d{4})\s+(\d{2}/\d{2}/\d{4})\s+R\$\s*([\d\.]+,\d+)",
        text_ascii,
    )
    if match:
        return match.group(1), match.group(2), _to_float_br(match.group(3))
    # fallback: TOTAL na última linha
    m_total = re.search(r"\bTOTAL\s+([\d\.]+,\d+)", text_ascii)
    m_vcto = re.search(r"(\d{2}/\d{2}/\d{4})\s+(?:[\d\.]+,\d+)\s*$", text_ascii)
    return "", (m_vcto.group(1) if m_vcto else ""), (_to_float_br(m_total.group(1)) if m_total else None)


def _parse_emissao_nf(text_ascii: str) -> tuple[str, str]:
    """Extrai número da NF e data de emissão.
    Padrão: NOTA FISCAL N|No086188090 SERIE:001 DATA EMISSAO:02/04/2026
    """
    match = re.search(
        r"NOTA FISCAL\s+[N°No]+\s*(\d+)\s+SERIE[:\s]*\d+\s+DATA EMISSAO[:\s]*(\d{2}/\d{2}/\d{4})",
        text_ascii,
    )
    if match:
        return match.group(1), match.group(2)
    # fallback: procura só a data de emissão
    match2 = re.search(r"DATA EMISSAO[:\s]*(\d{2}/\d{2}/\d{4})", text_ascii)
    return "", (match2.group(1) if match2 else "")


def _parse_leitura_datas(text_ascii: str) -> tuple[str, str]:
    """Extrai datas de leitura anterior e atual.
    Padrão: DD/MM/YYYY DD/MM/YYYY NN Lida DD/MM/YYYY ...
    """
    match = re.search(
        r"(\d{2}/\d{2}/\d{4})\s+(\d{2}/\d{2}/\d{4})\s+\d+\s+[A-Z\s]+?\s+\d{2}/\d{2}/\d{4}",
        text_ascii,
        re.IGNORECASE,
    )
    if match:
        return match.group(1), match.group(2)
    return "", ""


def _parse_instalacao(lines_ascii: list[str], text_ascii: str) -> str:
    """Número da UC / instalação.
    Aparece como número standalone logo após a linha NOME: no PDF CELESC,
    ou na linha 'Unidade Consumidora XXXXXXXXXX' no canhoto.
    """
    # Canhoto: Unidade Consumidora 0000075884
    match = re.search(r"UNIDADE CONSUMIDORA\s+(\d{7,12})", text_ascii)
    if match:
        return match.group(1).lstrip("0") or match.group(1)

    # Número standalone logo após NOME:
    for idx, line in enumerate(lines_ascii):
        if line.startswith("NOME:"):
            for prox in lines_ascii[idx + 1: idx + 5]:
                m = re.fullmatch(r"\d{5,10}", prox.strip())
                if m:
                    return m.group(0)

    # Número standalone genérico próximo a ENDERECO: ou NOME:
    for idx, line in enumerate(lines_ascii):
        m = re.fullmatch(r"\d{5,10}", line.strip())
        if not m:
            continue
        prev = lines_ascii[idx - 1] if idx > 0 else ""
        nxt = lines_ascii[idx + 1] if idx + 1 < len(lines_ascii) else ""
        if prev.startswith("NOME:") or nxt.startswith("ENDERECO:"):
            return m.group(0)

    return ""


def _parse_cnpj(text_ascii: str) -> str:
    """CNPJ do cliente (não da Celesc) — mantém formato XX.XXX.XXX/XXXX-XX."""
    match = re.search(r"CPF/CNPJ[:\s]*([\d]{2}\.[\d]{3}\.[\d]{3}/[\d]{4}-[\d]{2})", text_ascii)
    if match:
        return match.group(1)
    # Fallback: qualquer sequência após CPF/CNPJ
    match2 = re.search(r"CPF/CNPJ[:\s]+([\d\./-]+)", text_ascii)
    if not match2:
        return ""
    raw = re.sub(r"\D", "", match2.group(1))
    if len(raw) == 14:
        return f"{raw[:2]}.{raw[2:5]}.{raw[5:8]}/{raw[8:12]}-{raw[12:]}"
    return match2.group(1).strip()


def _parse_codigo_cliente(text_ascii: str) -> str:
    """Código do cliente / número do contrato (Cliente:XXXXXXXX)."""
    match = re.search(r"Cliente[:\s]+(\d{5,10})", text_ascii, re.IGNORECASE)
    return match.group(1) if match else ""


def _parse_endereco(lines_original: list[str]) -> str:
    """Endereço da UC."""
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
    """Extrai o valor do tributo da linha: MARCADOR base% aliq% valor
    Padrão CELESC: PIS 71,02 0,35 0,24  → último número = valor
    """
    for line in lines_ascii:
        if not line.startswith(marcador):
            continue
        nums = re.findall(r"[\d\.]+,\d+", line)
        if nums:
            return _to_float_br(nums[-1])
    return None


def _parse_tributo_componentes(lines_ascii: list[str], marcador: str) -> tuple[float | None, float | None, float | None]:
    for line in lines_ascii:
        if not line.startswith(marcador):
            continue
        nums = re.findall(r"-?[\d\.]+,\d+", line)
        if len(nums) >= 3:
            return _to_float_br(nums[0]), _to_float_br(nums[1]), _to_float_br(nums[2])
    return None, None, None


def _parse_tributos_aliquota(lines_ascii: list[str], marcador: str) -> float | None:
    """Extrai a alíquota do tributo (segundo número)."""
    for line in lines_ascii:
        if not line.startswith(marcador):
            continue
        nums = re.findall(r"[\d\.]+,\d+", line)
        if len(nums) >= 2:
            return _to_float_br(nums[1])
    return None


def _normalizar_aliquota_cofins_celesc(valor: float | None) -> float:
    """COFINS da CELESC deve sair como 1,63 ou 1,78; fora disso tratamos como ruído."""
    if valor is not None:
        for permitido in (1.63, 1.78):
            if abs(valor - permitido) <= 0.03:
                return permitido
    return 1.63


def _parse_saldo_final_beneficiaria(lines_ascii: list[str]) -> float | None:
    for line in lines_ascii:
        if "SALDO FINAL BENEFICIARIA" not in line:
            continue
        nums = re.findall(r"-?[\d\.]+,\d+|-?\d+", line)
        if nums:
            return _to_float_br(nums[0])
    return None


def _parse_cosip(lines_ascii: list[str]) -> float | None:
    """COSIP Municipal — linha: (C0) COSIP Municipal 0,000 0,000000 300,16 ..."""
    for line in lines_ascii:
        if "(C0) COSIP" not in line and "COSIP MUNICIPAL" not in line:
            continue
        # 4º número após o código é o valor
        nums = re.findall(r"-?[\d\.]+,\d+", line)
        # Descarta os zeros (base e unitário) e pega o primeiro positivo não-zero
        for n in nums:
            v = _to_float_br(n)
            if v and abs(v) > 0.001:
                return v
    return None


def _parse_consumo_faturado(
    lines_ascii: list[str],
) -> tuple[float | None, float | None, float | None, float | None]:
    """Consumo TE (0D) e TUSD (0E) faturados em kWh.
    CONSEN mapeamento: TE → fatConPontaFaturado, TUSD → fatConFPontaIndFaturado.
    """
    consumo_te = 0.0
    consumo_tusd = 0.0
    valor_te = 0.0
    valor_tusd = 0.0
    found_te = False
    found_tusd = False
    for line in lines_ascii:
        if "(0D) CONSUMO TE" in line:
            nums = re.findall(r"[\d\.]+,\d+", line)
            if nums:
                qtd = _to_float_br(nums[0])
                val = _to_float_br(nums[-1]) if len(nums) > 1 else None
                if qtd is not None:
                    consumo_te += abs(qtd)
                    found_te = True
                if val is not None:
                    valor_te += abs(val)
        elif "(0E) CONSUMO TUSD" in line:
            nums = re.findall(r"[\d\.]+,\d+", line)
            if nums:
                qtd = _to_float_br(nums[0])
                val = _to_float_br(nums[-1]) if len(nums) > 1 else None
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
    for line in lines_ascii:
        if "(0D) CONSUMO TE" in line:
            nums = re.findall(r"[\d\.]+,\d+", line)
            if nums:
                consumo_te = _to_float_br(nums[0])  # primeiro número = kWh
        elif "(0E) CONSUMO TUSD" in line:
            nums = re.findall(r"[\d\.]+,\d+", line)
            if nums:
                consumo_tusd = _to_float_br(nums[0])
    return consumo_te, consumo_tusd


def _parse_energia_injetada(lines_ascii: list[str]) -> tuple[float | None, float | None, float | None]:
    """Energia injetada TE (0R) e TUSD (0S) — soma total de kWh (todas as linhas).
    CONSEN: TE → fatConFPontaInjetadoFaturado, TUSD → fatConFPontaInjetadoRegistrado.
    """
    kwh_te = 0.0
    kwh_tusd = 0.0
    valor_total = 0.0
    found_te = False
    found_tusd = False
    for line in lines_ascii:
        if "(0R) ENERGIA INJET" in line or "(0R) ENERGIA INJ" in line:
            nums = re.findall(r"[\d\.]+,\d+", line)
            if nums:
                v = _to_float_br(nums[0])
                if v is not None:
                    kwh_te += abs(v)
                    found_te = True
                val = _to_float_br(nums[-1]) if len(nums) > 1 else None
                if val is not None:
                    valor_total += abs(val)
        elif "(0S) ENERGIA INJ" in line:
            nums = re.findall(r"[\d\.]+,\d+", line)
            if nums:
                v = _to_float_br(nums[0])
                if v is not None:
                    kwh_tusd += abs(v)
                    found_tusd = True
                val = _to_float_br(nums[-1]) if len(nums) > 1 else None
                if val is not None:
                    valor_total += abs(val)
    return (
        round(kwh_te, 3) if found_te else None,
        round(kwh_tusd, 3) if found_tusd else None,
        round(valor_total, 2) if valor_total else None,
    )
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


def _parse_retidos(lines_ascii: list[str]) -> dict[str, float | None]:
    """Tributos retidos: COFINS, CSLL, IRPJ, PIS retidos."""
    mapa = {
        "cofins": None,
        "csll": None,
        "irpj": None,
        "pis": None,
    }
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
                        mapa[chave] = v
    return mapa


def _parse_obs_valor(lines_ascii: list[str]) -> float | None:
    """Observações / outras cobranças — valor líquido acumulado.
    Inclui parcelamentos (AW), créditos (crédito VIOL 204), ajustes etc.
    Gabarito usa coluna única 'obsValor'.
    """
    OBS_PREFIXES = ("(AW)", "(A1)", "(A2)", "(A3)", "(AI)", "(AX)", "CRED VIOL", "COBRANCA AJUSTE")
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
    """Linha digitável / código de barras CELESC.
    Padrão: 23790.XXXXX XXXXX.XXXXXX XXXXX.XXXXXX X XXXXXXXXXXXXXX
    ou compacto: 237-2 23790.XXXXX...
    """
    # Linha digitável separada por espaços (formato apresentação)
    match = re.search(
        r"(23790\.\d{5}\s+\d{5}\.\d{6}\s+\d{5}\.\d{6}\s+\d\s+\d{14})",
        text_ascii,
    )
    if match:
        return re.sub(r"\s+", " ", match.group(1)).strip()
    match_compacto_pontuado = re.search(r"(23790\.\d{5}\d{5}\.\d{6}\d{5}\.\d{6}\d{15})", text_ascii)
    if match_compacto_pontuado:
        return match_compacto_pontuado.group(1)
    match_compacto = re.search(r"(23790\d{43})", re.sub(r"\s+", "", text_ascii))
    if match_compacto:
        return match_compacto.group(1)
    # Código compacto numérico
    match2 = re.search(r"237-2\s+([\d\.\s]+)", text_ascii)
    if match2:
        return re.sub(r"\s+", "", match2.group(1))[:47]
    return ""


def _parse_subgrupo(text_ascii: str) -> str:
    """Subgrupo tarifário: B3, B1, A4 etc."""
    match = re.search(r"GRUPO/SUBGRUPO\s+TENSAO[:\s]+[AB]/([A-Z]\d+)", text_ascii, re.IGNORECASE)
    if match:
        return match.group(1)
    match2 = re.search(r"\b(B3|B1|B2|B4|A4|A3|A2|A1)\b", text_ascii)
    return match2.group(1) if match2 else ""


def _parse_tarifa(text_ascii: str) -> str:
    """Modalidade tarifária: Verde, Azul, Convencional."""
    if "VERDE" in text_ascii:
        return "HS - Verde"
    if "AZUL" in text_ascii:
        return "HS - Azul"
    return "Convencional"


# ── Parsers específicos CELESC MT ────────────────────────────────────────────

def _parse_consumo_mt(
    lines_ascii: list[str],
) -> tuple[float | None, float | None, float | None, float | None, float | None, float | None]:
    """Consumo MT Verde.

    Quantidades devem vir sem duplicar TE/TUSD, mas o valor em R$ precisa somar
    os componentes TE + TUSD:
      FP  -> (03) + (04)
      P   -> (09) + (0A)
    Returns (fp_qty, fp_val, p_qty, p_val, cofins_aliq, pis_aliq).
    """
    fp_qty = p_qty = cofins_aliq = pis_aliq = None
    fp_val_total = 0.0
    p_val_total = 0.0
    found_fp_val = False
    found_p_val = False
    for line in lines_ascii:
        if line.startswith("(03) CONSUMO") or line.startswith("(04) CONSUMO"):
            nums = re.findall(r"-?[\d\.]+,\d+", line)
            if len(nums) >= 3:
                qty = _to_float_br(nums[0])
                val = _to_float_br(nums[2])
                if fp_qty is None:
                    fp_qty = qty
                if val is not None:
                    fp_val_total += abs(val)
                    found_fp_val = True
            if len(nums) >= 6:
                if cofins_aliq is None:
                    cofins_aliq = _to_float_br(nums[4])
                if pis_aliq is None:
                    pis_aliq = _to_float_br(nums[5])
        elif line.startswith("(09) CONSUMO") or line.startswith("(0A) CONSUMO"):
            nums = re.findall(r"-?[\d\.]+,\d+", line)
            if len(nums) >= 3:
                qty = _to_float_br(nums[0])
                val = _to_float_br(nums[2])
                if p_qty is None:
                    p_qty = qty
                if val is not None:
                    p_val_total += abs(val)
                    found_p_val = True
            if len(nums) >= 6:
                if cofins_aliq is None:
                    cofins_aliq = _to_float_br(nums[4])
                if pis_aliq is None:
                    pis_aliq = _to_float_br(nums[5])
    return (
        fp_qty,
        round(fp_val_total, 2) if found_fp_val else None,
        p_qty,
        round(p_val_total, 2) if found_p_val else None,
        cofins_aliq,
        pis_aliq,
    )


def _parse_demanda_mt(
    lines_ascii: list[str],
) -> tuple[float | None, float | None, float | None, float | None]:
    """Parses Verde demand block.
    Pattern: 'VERDE \\d+ DEMANDA \\d+KW MEDIDA \\d+KW \\d+KW' + 'FATURADA \\d+KW'.
    Returns (contratada, medida_P, medida_FP, faturada).
    """
    contratada = medida_P = medida_FP = faturada = None
    for line in lines_ascii:
        m = re.search(
            r"VERDE\s+\d+\s+DEMANDA\s+([\d,]+)\s*KW\s+MEDIDA\s+([\d,]+)KW\s+([\d,]+)KW",
            line,
        )
        if m:
            contratada = _to_float_br(m.group(1))
            medida_P = _to_float_br(m.group(2))
            medida_FP = _to_float_br(m.group(3))
        m2 = re.search(r"FATURADA\s+([\d,]+)KW", line)
        if m2:
            faturada = _to_float_br(m2.group(1))
    return contratada, medida_P, medida_FP, faturada


def _parse_demanda_registrada_mt(lines_ascii: list[str]) -> tuple[float | None, float | None]:
    """Returns registrada demand qty and valor R$ from (0T) line."""
    for line in lines_ascii:
        if "(0T) DEMANDA" in line:
            nums = re.findall(r"-?[\d\.]+,\d+", line)
            if len(nums) >= 3:
                return _to_float_br(nums[0]), _to_float_br(nums[2])
            if nums:
                return _to_float_br(nums[0]), None
    return None, None


def _parse_ultrapassagem_mt(lines_ascii: list[str]) -> tuple[float | None, float | None]:
    """Returns (qty_kW, valor_R$) from (0Y) ultrapassagem line."""
    for line in lines_ascii:
        if "(0Y)" in line and "ULTRAPASSAGEM" in line:
            nums = re.findall(r"-?[\d\.]+,\d+", line)
            if len(nums) >= 3:
                return _to_float_br(nums[0]), _to_float_br(nums[2])
    return None, None


def _parse_reativo_mt(lines_ascii: list[str]) -> tuple[float | None, float | None]:
    """Returns (qty_kWh, valor_R$) from reactive excess billing line.

    Só corresponde a linhas com código de cobrança (1O)/(10)/(IO) para evitar
    falsos positivos da tabela histórico (que traz leituras acumuladas de medidor).
    """
    for line in lines_ascii:
        if not any(codigo in line for codigo in ("(1O)", "(10)", "(IO)")):
            continue
        if "REATIV" not in line:
            continue
        if "DEMANDA" in line:
            continue
        nums = re.findall(r"-?[\d\.]+,\d+", line)
        if len(nums) >= 3:
            return _to_float_br(nums[0]), _to_float_br(nums[2])
    return None, None


def _aplicar_reativo_consumo_generico(
    row: dict[str, object],
    registrada: float | None,
    faturada: float | None,
    valor_reais: float | None,
) -> None:
    """Espelha a única linha genérica de reativo nos aliases usados pela digitação."""
    row["fatConFPontaIndExcRegistrado"] = registrada
    row["fatConFPontaIndExcFaturado"] = faturada
    row["fatConFPontaIndExcValorReais"] = valor_reais

    if row.get("fatConPontaExcRegistrado") in (None, ""):
        row["fatConPontaExcRegistrado"] = registrada
    if row.get("fatConPontaExcFaturado") in (None, ""):
        row["fatConPontaExcFaturado"] = faturada
    if row.get("fatConPontaExcValorReais") in (None, ""):
        row["fatConPontaExcValorReais"] = valor_reais


def _parse_escassez_mt(lines_ascii: list[str]) -> tuple[float | None, float | None]:
    """Returns (qty_kWh, valor_R$) from (2Y) escassez hídrica line."""
    for line in lines_ascii:
        if "(2Y)" in line and "ESCASSEZ" in line:
            nums = re.findall(r"-?[\d\.]+,\d+", line)
            if len(nums) >= 3:
                return _to_float_br(nums[0]), _to_float_br(nums[2])
    return None, None


def _parse_beneficio_mt(lines_ascii: list[str]) -> tuple[float | None, float | None]:
    """Sums benefício bruto ((2Z) all + (AA) BRUTO) and líquido ((31)+(32)+(AA) LIQUIDO).
    Returns bruto as positive and liquido as a discount (negative).
    """
    bruto = 0.0
    liquido = 0.0
    found_bruto = found_liquido = False
    for line in lines_ascii:
        is_2z = line.startswith("(2Z)")
        is_31 = line.startswith("(31)")
        is_32 = line.startswith("(32)")
        is_aa = line.startswith("(AA)")
        if not (is_2z or is_31 or is_32 or is_aa):
            continue
        nums = re.findall(r"-?[\d\.]+,\d+", line)
        if len(nums) < 3:
            continue
        val = _to_float_br(nums[2])
        if val is None:
            continue
        if is_2z or (is_aa and "BRUTO" in line):
            bruto += abs(val)
            found_bruto = True
        elif is_31 or is_32 or (is_aa and "LIQUIDO" in line):
            liquido -= abs(val)
            found_liquido = True
    return (
        round(bruto, 2) if found_bruto else None,
        round(liquido, 2) if found_liquido else None,
    )


def _parse_acl_consumo_net(lines_ascii: list[str]) -> tuple[float | None, float | None]:
    """Retorna (acl_fp_net, acl_p_net): valor líquido ACL por componente.
    FP: (0H) energia ACL FP + (1Z) dedução FP (negativo)
    P:  (0I) energia ACL P  + (20) dedução P  (negativo)
    Esses valores devem ser somados ao TUSD para compor o total de consumo R$.
    """
    fp = 0.0
    p = 0.0
    found_fp = found_p = False
    for line in lines_ascii:
        nums = re.findall(r"-?[\d\.]+,\d+", line)
        if len(nums) < 3:
            continue
        v = _to_float_br(nums[2])
        if v is None:
            continue
        if line.startswith("(0H)"):
            fp += v
            found_fp = True
        elif line.startswith("(1Z)"):
            fp += v
            found_fp = True
        elif line.startswith("(0I)"):
            p += v
            found_p = True
        elif line.startswith("(20)"):
            p += v
            found_p = True
    return (round(fp, 2) if found_fp else None, round(p, 2) if found_p else None)


def _parse_acl_multasDiversas(lines_ascii: list[str]) -> float | None:
    """fatMultasDiversas = net ACL = abs(sum of (0H)+(0I)+(1Z)+(20) values).
    (1Z) and (20) are negative in the invoice so the net is the actual spread.
    """
    total = 0.0
    found = False
    for line in lines_ascii:
        for code in ("(0H)", "(0I)", "(1Z)", "(20)"):
            if line.startswith(code):
                nums = re.findall(r"-?[\d\.]+,\d+", line)
                if len(nums) >= 3:
                    v = _to_float_br(nums[2])
                    if v is not None:
                        total += v
                        found = True
    return round(abs(total), 2) if found else None


def _parse_ccee_difs_mt(lines_ascii: list[str]) -> float | None:
    """Sums (2F) + (2G) CCEE dif values for obsValor."""
    total = 0.0
    found = False
    for line in lines_ascii:
        if line.startswith("(2F)") or line.startswith("(2G)"):
            nums = re.findall(r"-?[\d\.]+,\d+", line)
            if len(nums) >= 3:
                v = _to_float_br(nums[2])
                if v is not None:
                    total += v
                    found = True
    return round(total, 2) if found else None


def _parse_historico_registrado_mt(lines_ascii: list[str]) -> dict[str, float | None]:
    """Extrai valores registrados (medidos) da tabela HISTORICO DE CONSUMO.

    Cada linha da tabela: LABEL [acumulado_1] [acumulado_2] [periodo_atual] ...
    O 3º token numérico = valor do período atual de faturamento.
    Usado para preencher campos registrados que diferem dos faturados.
    """
    r: dict[str, float | None] = {
        "consumo_fp": None,
        "consumo_p": None,
        "exc_fp": None,
        "dem_p": None,
    }
    in_historico = False
    for line in lines_ascii:
        if "HISTORICO DE CONSUMO" in line:
            in_historico = True
            continue
        if not in_historico:
            continue
        # Pula linhas de legenda/rodapé que repetem os nomes dos itens com "(XX)"
        if line.startswith("(") or " | " in line:
            continue
        nums = re.findall(r"\d+(?:\.\d{3})*(?:,\d+)?", line)
        if len(nums) < 3:
            continue
        val = _to_float_br(nums[2])
        if "CONSUMO FORA PONTA" in line:
            r["consumo_fp"] = val
        elif "CONSUMO PONTA" in line and "FORA" not in line:
            r["consumo_p"] = val
        elif "REATIVO EXCEDENTE FORA PONTA" in line or "CONSUMO REATIVO FORA PONTA" in line:
            r["exc_fp"] = val
        elif r["exc_fp"] is None and "REATIV" in line and "EXCEDENTE" in line:
            r["exc_fp"] = val
        elif "DEMANDA PONTA" in line and "FORA" not in line and "REATIVA" not in line:
            r["dem_p"] = val
    return r


def _parse_demanda_diferenca_mt(lines_ascii: list[str]) -> float | None:
    """Retorna valor R$ da linha de diferença/ajuste da demanda contratada.

    CELESC usa códigos variáveis: (29) em faturas cativas e (2D) em faturas ACL.
    Ambas compõem o total faturado de demanda junto com a linha (0T).
    """
    for line in lines_ascii:
        if ("(29)" in line or "(2D)" in line) and "DEMANDA" in line:
            nums = re.findall(r"-?[\d\.]+,\d+", line)
            if len(nums) >= 3:
                return _to_float_br(nums[2])
    return None


def _parse_demanda_reativa_mt(lines_ascii: list[str]) -> tuple[float | None, float | None]:
    """Retorna (qty_kW, valor_R$) da linha (1T) Demanda Reativa.

    Essa linha representa a demanda reativa excedente cobrada e mapeia para
    fatDemFPontaExcFaturada / fatDemFPontaExcRegistrada no CONSEN.
    """
    for line in lines_ascii:
        if "(1T)" in line and "DEMANDA" in line:
            nums = re.findall(r"-?[\d\.]+,\d+", line)
            if len(nums) >= 3:
                return _to_float_br(nums[0]), _to_float_br(nums[2])
    return None, None


def _parse_retidos_mt(lines_ascii: list[str]) -> dict[str, float | None]:
    """Parses MT retention lines (BC)/(BD)/(BE)/(BF).
    Line structure: CODE DESC PERC% 0,000 0,000000 VALUE ...
    Percentage = nums[0], value = nums[3].
    (BE) split: 1,20% → consumo; 4,80% → demanda.
    """
    r: dict[str, float | None] = {
        "cofins_perc": None, "cofins_val": None,
        "csll_perc": None, "csll_val": None,
        "consumo_perc": None, "consumo_val": None,
        "demanda_perc": None, "demanda_val": None,
        "pis_perc": None, "pis_val": None,
    }
    for line in lines_ascii:
        nums = re.findall(r"-?[\d\.]+,\d+", line)
        if len(nums) < 4:
            continue
        perc = _to_float_br(nums[0]) if nums else None
        raw_val = _to_float_br(nums[3])
        val = -abs(raw_val) if raw_val is not None else None
        if "(BC)" in line and "COFINS" in line:
            r["cofins_perc"] = perc
            r["cofins_val"] = val
        elif "(BD)" in line and "CSLL" in line:
            r["csll_perc"] = perc
            r["csll_val"] = val
        elif "(BE)" in line and "IRPJ" in line:
            if "1,20%" in line:
                r["consumo_perc"] = perc
                r["consumo_val"] = val
            elif "4,80%" in line:
                r["demanda_perc"] = perc
                r["demanda_val"] = val
        elif "(BF)" in line and "PIS" in line:
            r["pis_perc"] = perc
            r["pis_val"] = val
    return r


def _parse_desconto_fio(lines_ascii: list[str]) -> float | None:
    """Searches for DESC. FIO or DESCONTO FIO percentage."""
    for line in lines_ascii:
        if "FIO" in line and ("DESC" in line or "DESCONTO" in line):
            m = re.search(r"([\d]+(?:,\d+)?)%", line)
            if m:
                return _to_float_br(m.group(1))
    if any(
        line.startswith(("(2Z)", "(31)", "(32)", "(AA)")) and "BENEFICIO TARIF" in line
        for line in lines_ascii
    ):
        return 43.25
    return None


def _eh_mercado_cativo_mt(row: dict[str, object]) -> bool:
    """Mercado cativo nao traz bloco de ACL/beneficio/subvencao."""
    campos_livre = (
        "fatBeneficioTarifarioBrutoValorReais",
        "fatBeneficioLiquidoValorReais",
        "fatMultasDiversas",
        "obsValor",
        "fatDescontoFio",
    )
    return all(valor in (None, "") for valor in (row.get(campo) for campo in campos_livre))


# ── Extração principal ───────────────────────────────────────────────────────

def _parse_emissao_nf_corrigido(text_ascii: str) -> tuple[str, str]:
    match = re.search(
        r"NOTA FISCAL\s+N(?:O|º|°)?\s*(\d+)\s+SERIE[:\s]*\d+\s+DATA EMISSAO[:\s]*(\d{2}/\d{2}/\d{4})",
        text_ascii,
        re.IGNORECASE,
    )
    if match:
        return match.group(1), match.group(2)
    match_nf = re.search(r"NOTA FISCAL\s+N(?:O|º|°)?\s*(\d+)", text_ascii, re.IGNORECASE)
    match_data = re.search(r"DATA EMISSAO[:\s]*(\d{2}/\d{2}/\d{4})", text_ascii, re.IGNORECASE)
    return (match_nf.group(1) if match_nf else ""), (match_data.group(1) if match_data else "")


def _resolver_tarifa_celesc(text_ascii: str, subgrupo: str) -> str:
    sub = (subgrupo or "").strip().upper()
    if sub.startswith("B"):
        return "Convencional"
    return _parse_tarifa(text_ascii)


def _parse_consumo_faturado_corrigido(
    lines_ascii: list[str],
) -> tuple[float | None, float | None, float | None, float | None]:
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
                val = _to_float_br(nums[2]) if len(nums) >= 3 else None
                if qtd is not None:
                    consumo_te += abs(qtd)
                    found_te = True
                if val is not None:
                    valor_te += abs(val)
        elif "(0E) CONSUMO TUSD" in line:
            nums = re.findall(r"-?[\d\.]+,\d+", line)
            if nums:
                qtd = _to_float_br(nums[0])
                val = _to_float_br(nums[2]) if len(nums) >= 3 else None
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


def _parse_energia_injetada_corrigido(
    lines_ascii: list[str],
) -> tuple[float | None, float | None, float | None]:
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
                val = _to_float_br(nums[2]) if len(nums) >= 3 else None
                if qtd is not None:
                    kwh_te += abs(qtd)
                    found_te = True
                if val is not None:
                    valor_total += abs(val)
        elif "(0S) ENERGIA INJ" in line:
            nums = re.findall(r"-?[\d\.]+,\d+", line)
            if nums:
                qtd = _to_float_br(nums[0])
                val = _to_float_br(nums[2]) if len(nums) >= 3 else None
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


def _is_bt_subgrupo(subgrupo: str) -> bool:
    return (subgrupo or "").strip().upper().startswith("B")


def _validar_campos_criticos_bt(row: dict[str, object]) -> list[str]:
    faltantes: list[str] = []
    for campo in (
        "cadTarifaCod",
        "cadSubGrupoCod",
        "fatValorNotaFiscal",
        "fatICMS",
        "fatCodigoBarras",
    ):
        valor = row.get(campo)
        if valor is None or str(valor).strip() == "":
            faltantes.append(campo)
    return faltantes


def _normalizar_subgrupo_consen(subgrupo: str) -> str:
    sub = (subgrupo or "").strip().upper()
    if not sub:
        return ""
    if sub == "B3":
        return "B3 [<2,3kV]"
    if sub == "B1":
        return "B1"
    if sub == "B2":
        return "B2"
    if sub == "B4":
        return "B4"
    if sub == "A4":
        return "A4 [2,3kV a 25kV]"
    if sub == "A3A":
        return "A3a [30kV a 44kV]"
    if sub == "A3":
        return "A3"
    if sub == "A2":
        return "A2 [88 kV a 138 kV]"
    if sub == "A1":
        return "A1"
    return sub


def extrair_campos(pdf_path: Path) -> dict:
    row: dict[str, object] = {h: None for h in HEADERS}
    row["ARQUIVO"] = str(pdf_path)
    row["fatCarimbo"] = _carimbo_from_path(pdf_path)
    row["ERRO"] = ""
    row["concCod"] = "CELESC"

    try:
        lines_original, lines_ascii, text_original, text_ascii = _extract_pages(pdf_path)
    except Exception as exc:
        row["ERRO"] = f"Falha ao ler PDF: {exc}"
        log.error("  Erro ao ler %s: %s", pdf_path.name, exc)
        return row

    try:
        # ── Referência, vencimento, valor total ─────────────────────────────
        referencia, vencimento, valor_total = _parse_ref_vcto_valor(text_ascii)
        row["fatDataVcto"] = _to_date(vencimento)
        row["fatValorFatura"] = valor_total
        row["fatValorNotaFiscal"] = valor_total  # NF ≈ fatura; sem campo explícito no PDF

        if referencia:
            try:
                m, a = referencia.split("/")
                row["fatDataReferencia"] = dt.date(int(a), int(m), 1)
            except Exception:
                row["fatDataReferencia"] = None

        # ── Nota fiscal e data emissão ───────────────────────────────────────
        nf_num, nf_data = _parse_emissao_nf_corrigido(text_ascii)
        row["NOTAFISCAL"] = nf_num
        row["fatDataEmissao"] = _to_date(nf_data)

        # ── Datas de leitura ─────────────────────────────────────────────────
        leitura_ant, leitura_at = _parse_leitura_datas(text_ascii)
        row["fatDataLeituraAnterior"] = _to_date(leitura_ant)
        row["fatDataLeituraAtual"] = _to_date(leitura_at)

        # ── Instalação / UC ──────────────────────────────────────────────────
        row["Instalacao"] = _parse_instalacao(lines_ascii, text_ascii)
        row["CNPJ"] = _parse_cnpj(text_ascii)
        row["CODIGOCLIENTE"] = _parse_codigo_cliente(text_ascii)
        row["ENDERECO"] = _parse_endereco(lines_original)

        # ── Códigos CONSEN fixos para CELESC ─────────────────────────────────
        row["concCod"] = 35          # código CELESC no CONSEN
        row["cadTarifaCod"] = 1      # convencional/monômio (B3)
        row["cadSubGrupoCod"] = 5    # subgrupo CELESC no CONSEN

        # ── Subgrupo e tarifa (diagnóstico) ──────────────────────────────────
        subgrupo_detectado = _parse_subgrupo(text_ascii)
        tarifa_detectada = _resolver_tarifa_celesc(text_ascii, subgrupo_detectado)
        row["cadTarifaCod"] = tarifa_detectada
        row["cadSubGrupoCod"] = _normalizar_subgrupo_consen(subgrupo_detectado)
        row["TARIFA_DETECTADA"] = tarifa_detectada

        # ── Tributos ─────────────────────────────────────────────────────────
        icms_base, icms_aliquota, icms_valor = _parse_tributo_componentes(lines_ascii, "ICMS")
        row["fatICMS"] = icms_valor
        row["fatDesIcmsAliquota"] = icms_aliquota
        row["fatPIS"] = _parse_tributos_linha(lines_ascii, "PIS")
        row["fatDescPisAliquota"] = _parse_tributos_aliquota(lines_ascii, "PIS")
        row["fatCOFINS"] = _parse_tributos_linha(lines_ascii, "COFINS")
        row["fatDescCofinsAliquota"] = _normalizar_aliquota_cofins_celesc(
            _parse_tributos_aliquota(lines_ascii, "COFINS")
        )

        # ── COSIP ────────────────────────────────────────────────────────────
        row["fatIlumPublica"] = _parse_cosip(lines_ascii)

        if _is_bt_subgrupo(subgrupo_detectado):
            # ── BT: consumo (0D)/(0E) ─────────────────────────────────────────
            consumo_te, consumo_tusd, valor_te, valor_tusd = _parse_consumo_faturado_corrigido(lines_ascii)
            consumo_bt = consumo_tusd if consumo_tusd is not None else consumo_te
            valor_bt = round((valor_te or 0.0) + (valor_tusd or 0.0), 2) if (valor_te is not None or valor_tusd is not None) else None
            row["fatConPontaFaturado"] = None
            row["fatConPontaRegistrado"] = None
            row["fatConPontaValorReais"] = None
            row["fatConFPontaIndFaturado"] = consumo_bt
            row["fatConFPontaIndRegistrado"] = consumo_bt
            row["fatConFPontaIndValorReais"] = valor_bt

            # BT: energia injetada (0R)/(0S)
            kwh_te_inj, kwh_tusd_inj, valor_injetado = _parse_energia_injetada_corrigido(lines_ascii)
            injetado_bt = kwh_tusd_inj if kwh_tusd_inj is not None else kwh_te_inj
            row["fatConFPontaInjetadoFaturado"] = injetado_bt
            row["fatConFPontaInjetadoRegistrado"] = injetado_bt
            row["fatConFPontaInjetadoValorReais"] = valor_injetado
            row["fatConFPontaInjetadoUsina"] = injetado_bt
            row["fatConFPontaInjetadoUsinaSaldoAcumulado"] = _parse_saldo_final_beneficiaria(lines_ascii)

            # BT: tributos retidos
            retidos = _parse_retidos(lines_ascii)
            row["fatDescCofinsValRetImposto"] = retidos.get("cofins")
            row["fatDescCsllValRetImposto"] = retidos.get("csll")
            row["fatDescIrpjValRetImposto"] = retidos.get("irpj")
            row["fatDescPisValRetImposto"] = retidos.get("pis")
            row["fatDescPisPercRetImposto"] = 0.65
            row["fatDescCofinsPercRetImposto"] = 3.0
            row["fatDescCsllPercRetImposto"] = 1.0
            row["fatDescIrpjPercRetImposto"] = 1.2
            if icms_base is not None:
                row["fatValorNotaFiscal"] = icms_base

            row["obsValor"] = _parse_obs_valor(lines_ascii)
            row["fatCodigoBarras"] = _parse_codigo_barras(text_ascii)

            faltantes_bt = _validar_campos_criticos_bt(row)
            if faltantes_bt and not row.get("ERRO"):
                row["ERRO"] = f"Campos BT faltantes: {', '.join(faltantes_bt)}"

        else:
            # ── MT/AT: consumo (04)/(0A) ──────────────────────────────────────
            fp_qty, fp_val, p_qty, p_val, cofins_aliq, pis_aliq = _parse_consumo_mt(lines_ascii)
            # ACL MT: adicionar componente líquido da energia ACL ao valor de consumo
            # (0H/0I = energia ACL; 1Z/20 = deduções do preço regulado correspondente)
            acl_fp_net, acl_p_net = _parse_acl_consumo_net(lines_ascii)
            if acl_fp_net is not None and fp_val is not None:
                fp_val = round(fp_val + acl_fp_net, 2)
            if acl_p_net is not None and p_val is not None:
                p_val = round(p_val + acl_p_net, 2)
            hist = _parse_historico_registrado_mt(lines_ascii)
            row["fatConFPontaIndFaturado"] = fp_qty
            row["fatConFPontaIndRegistrado"] = hist["consumo_fp"] if hist["consumo_fp"] is not None else fp_qty
            row["fatConFPontaIndValorReais"] = fp_val
            row["fatConPontaFaturado"] = p_qty
            row["fatConPontaRegistrado"] = hist["consumo_p"] if hist["consumo_p"] is not None else p_qty
            row["fatConPontaValorReais"] = p_val
            # MT: não normalizar COFINS — alíquota real varia (2,64% ACL, 1,63% cativo)
            aliq_cofins = cofins_aliq if cofins_aliq is not None else row.get("fatDescCofinsAliquota")
            row["fatDescCofinsAliquota"] = aliq_cofins
            if pis_aliq is not None:
                row["fatDescPisAliquota"] = pis_aliq

            # MT: demanda Verde
            contratada, medida_P, medida_FP, faturada = _parse_demanda_mt(lines_ascii)
            registrada, demanda_valor = _parse_demanda_registrada_mt(lines_ascii)
            diferenca_dem = _parse_demanda_diferenca_mt(lines_ascii)
            dem_val_total = (
                round((demanda_valor or 0.0) + (diferenca_dem or 0.0), 2)
                if demanda_valor is not None or diferenca_dem is not None
                else None
            )
            row["fatDemContratadaFPonta"] = contratada
            row["fatDemContratadaPonta"] = contratada
            row["fatDemFPontaIndRegistrada"] = registrada
            row["fatDemPontaRegistrada"] = hist["dem_p"] if hist["dem_p"] is not None else medida_P
            row["fatDemFPontaIndFaturada"] = faturada
            row["fatDemFPontaIndValorReais"] = dem_val_total

            # MT: ultrapassagem (0Y)
            ultra_qty, ultra_val = _parse_ultrapassagem_mt(lines_ascii)
            row["fatDemFPontaIndUltra"] = ultra_qty
            row["fatDemFPontaIndUltraValorReais"] = ultra_val

            # MT: demanda reativa (1T) → excedente de demanda FP no CONSEN
            reativa_qty, _reativa_val = _parse_demanda_reativa_mt(lines_ascii)
            row["fatDemFPontaExcFaturada"] = reativa_qty
            row["fatDemFPontaExcRegistrada"] = reativa_qty

            # MT: reativo excedente (1O) — só FP; Ponta fica None (não espelhar FP→Ponta)
            reativo_qty, reativo_val = _parse_reativo_mt(lines_ascii)
            reativo_registrado = hist["exc_fp"] if hist["exc_fp"] is not None else reativo_qty
            row["fatConFPontaIndExcRegistrado"] = reativo_registrado
            row["fatConFPontaIndExcFaturado"] = reativo_qty
            row["fatConFPontaIndExcValorReais"] = reativo_val

            # MT: escassez hídrica (2Y)
            esc_qty, esc_val = _parse_escassez_mt(lines_ascii)
            row["fatEscassezHidrica"] = esc_qty
            row["fatEscassezHidricaValorReais"] = esc_val

            # MT: benefício tarifário
            beneficio_bruto, beneficio_liquido = _parse_beneficio_mt(lines_ascii)
            row["fatBeneficioTarifarioBrutoValorReais"] = beneficio_bruto
            row["fatBeneficioLiquidoValorReais"] = beneficio_liquido

            # MT: desconto fio
            row["fatDescontoFio"] = _parse_desconto_fio(lines_ascii)

            # MT: ACL → fatMultasDiversas
            row["fatMultasDiversas"] = _parse_acl_multasDiversas(lines_ascii)

            # MT: CCEE difs → obsValor
            row["obsValor"] = _parse_ccee_difs_mt(lines_ascii)

            # MT: energia injetada (se houver)
            kwh_te_inj, kwh_tusd_inj, valor_injetado = _parse_energia_injetada_corrigido(lines_ascii)
            row["fatConFPontaInjetadoFaturado"] = kwh_te_inj
            row["fatConFPontaInjetadoRegistrado"] = kwh_tusd_inj
            row["fatConFPontaInjetadoValorReais"] = valor_injetado

            # MT: tributos retidos (BC)/(BD)/(BE)/(BF)
            retidos_mt = _parse_retidos_mt(lines_ascii)
            row["fatDescCofinsPercRetImposto"] = retidos_mt["cofins_perc"]
            row["fatDescCofinsValRetImposto"] = retidos_mt["cofins_val"]
            row["fatDescCsllPercRetImposto"] = retidos_mt["csll_perc"]
            row["fatDescCsllValRetImposto"] = retidos_mt["csll_val"]
            row["fatDescConsumoPercRetImposto"] = retidos_mt["consumo_perc"]
            row["fatDescConsumoValRetImposto"] = retidos_mt["consumo_val"]
            row["fatDescDemandaPercRetImposto"] = retidos_mt["demanda_perc"]
            row["fatDescDemandaValRetImposto"] = retidos_mt["demanda_val"]
            row["fatDescPisPercRetImposto"] = retidos_mt["pis_perc"]
            row["fatDescPisValRetImposto"] = retidos_mt["pis_val"]

            # MT: fatValorNotaFiscal = base ICMS (tabela pág 3)
            if icms_base is not None:
                row["fatValorNotaFiscal"] = icms_base

        # ── Código de barras ─────────────────────────────────────────────────
        if not row.get("fatCodigoBarras"):
            row["fatCodigoBarras"] = _parse_codigo_barras(text_ascii)

    except Exception as exc:
        row["ERRO"] = str(exc)
        log.error("  Erro ao extrair campos de %s: %s", pdf_path.name, exc)

    return row


def _to_date(valor: str) -> dt.date | None:
    """Converte 'DD/MM/YYYY' para dt.date."""
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
        df.to_excel(writer, index=False, sheet_name="OCR_CELESC")
        ws = writer.sheets["OCR_CELESC"]

        # Formata colunas de data
        from openpyxl.styles import numbers as xl_numbers
        date_cols = {h: idx + 1 for idx, h in enumerate(HEADERS) if h in DATE_HEADERS}
        for col_name, col_num in date_cols.items():
            for row_num in range(2, len(linhas) + 2):
                cell = ws.cell(row=row_num, column=col_num)
                if cell.value is not None:
                    cell.number_format = "DD/MM/YYYY"

        # Ajusta largura das colunas
        for col_cells in ws.columns:
            max_len = max((len(str(c.value or "")) for c in col_cells), default=8)
            ws.column_dimensions[col_cells[0].column_letter].width = min(max_len + 2, 30)

    log.info("XLSX salvo: %s (%s faturas)", caminho, len(linhas))


# ── CLI ──────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    hoje = dt.date.today()
    p = argparse.ArgumentParser(description="OCR CELESC — extrai campos das faturas")
    p.add_argument("--mes", type=str, default=f"{hoje.month:02d}")
    p.add_argument("--ano", type=str, default=str(hoje.year))
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
    xlsx_saida = Path(args.saida.strip()) if args.saida.strip() else OCR_DIR / f"ocr_celesc_{mes}{ano}.xlsx"

    log.info("=" * 60)
    log.info("  OCR CELESC  %s/%s", mes, ano)
    log.info("=" * 60)
    log.info("  Pasta PDFs : %s", pasta)
    log.info("  XLSX saída : %s", xlsx_saida)

    if not pasta.exists():
        log.error("Pasta não encontrada: %s", pasta)
        return 1

    # Lista PDFs
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
