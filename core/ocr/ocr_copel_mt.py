#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
OCR COPEL MT
============

Extrai campos das faturas COPEL MT (A4 / tarifa horaria verde) para alimentar
o fluxo de digitacao do Consen usando o mesmo contrato de colunas do OCR BT.
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

from ocr_copel_bt import (
    DATE_HEADERS,
    DOWNLOAD_DIR,
    HEADER_DISPLAY,
    HEADERS,
    OCR_DIR,
    TEXT_HEADERS,
    _carimbo_from_path,
    _extract_pages,
    _guess_valor_nota_fiscal,
    _mkdir_seguro,
    _parse_cnpj,
    _parse_codigo_barras,
    _parse_codigo_cliente,
    _parse_debitos_anteriores,
    _parse_endereco,
    _parse_header_dates,
    _parse_ilum_publica,
    _parse_instalacao,
    _parse_nota_fiscal,
    _parse_ref_vcto_valor,
    _parse_retencoes,
    _parse_tax_line,
    _to_float_br,
)


NUMERIC_HEADERS = set(HEADERS) - DATE_HEADERS - TEXT_HEADERS
_RE_NUMBER = re.compile(r"-?[\d\.]+,\d+|-?\d+")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("ocr_copel_mt")


def _to_float_mixed(value: str | float | int | None) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    txt = str(value).strip()
    if not txt:
        return None
    neg = txt.startswith("-") or txt.endswith("-")
    txt = txt.replace("R$", "").replace("%", "").replace(" ", "")
    txt = txt.lstrip("-").rstrip("-")
    if "," in txt:
        txt = txt.replace(".", "").replace(",", ".")
    elif "." in txt and not re.fullmatch(r"\d+\.\d{1,6}", txt):
        txt = txt.replace(".", "")
    try:
        number = float(txt)
        return -number if neg else number
    except ValueError:
        return None


def _line_last_decimal(line: str) -> float | None:
    match = re.search(r"(-?\d+(?:[.,]\d+)?)\s*$", line.strip())
    return _to_float_mixed(match.group(1)) if match else None


def _parse_line_with_dotted_decimal(line: str, marker: str) -> float | None:
    if marker not in f" {line} ":
        return None
    tail = line.split(marker, 1)[1]
    matches = re.findall(r"-?(?:\d+[.,]\d+|\.\d+)", tail)
    if matches:
        return _to_float_mixed(matches[-1])
    return _line_last_decimal(tail)


def _sum_started_line_values(lines_ascii: list[str], prefixes: tuple[str, ...]) -> float | None:
    total = 0.0
    found = False
    for line in lines_ascii:
        if not any(line.startswith(prefix) for prefix in prefixes):
            continue
        nums = _num_tokens(line)
        if len(nums) >= 3:
            total += nums[2]
            found = True
    return round(total, 2) if found else None


def _parse_single_value_after_prefix(lines_ascii: list[str], prefix: str) -> float | None:
    for line in lines_ascii:
        if line.startswith(prefix):
            tail = line[len(prefix):]
            nums = _num_tokens(tail)
            if nums:
                return abs(nums[0]) or None
    return None


def _parse_signed_value_after_prefix(lines_ascii: list[str], prefix: str) -> float | None:
    for line in lines_ascii:
        if line.startswith(prefix):
            tail = line[len(prefix):]
            currency_like = re.findall(r"(?<![\d,])-?\d{1,3}(?:\.\d{3})*,\d{2}(?!\d)-?", tail)
            if currency_like:
                return _to_float_mixed(currency_like[0])
            matches = re.findall(r"-?[\d\.]+,\d+|-?\d+,\d+|-?\d+", tail)
            if matches:
                # Nessas linhas da COPEL MT, o primeiro numero costuma ser a quantidade
                # e o valor monetario relevante pode vir antes de outros inteiros
                # anexados ao fim da linha.
                return _to_float_mixed(matches[-1])
            nums = _num_tokens(tail)
            if nums:
                return nums[-1]
    return None


def _parse_signed_value_matching(
    lines_ascii: list[str],
    required_terms: tuple[str, ...],
    excluded_terms: tuple[str, ...] = (),
) -> float | None:
    for line in lines_ascii:
        if not all(term in line for term in required_terms):
            continue
        if any(term in line for term in excluded_terms):
            continue
        match = re.search(r"(-?[\d\.,]+-?)\s*$", line)
        if match:
            return _to_float_mixed(match.group(1))
    return None


def _parse_last_value_matching(lines_ascii: list[str], required_terms: tuple[str, ...]) -> float | None:
    for line in lines_ascii:
        if not all(term in line for term in required_terms):
            continue
        nums = _num_tokens(line)
        if nums:
            return nums[-1]
    return None


def _parse_qtd_valor_matching(
    lines_ascii: list[str],
    required_terms: tuple[str, ...],
    excluded_terms: tuple[str, ...] = (),
) -> tuple[float | None, float | None]:
    best: tuple[float, float] | None = None
    for line in lines_ascii:
        if not all(term in line for term in required_terms):
            continue
        if any(term in line for term in excluded_terms):
            continue
        nums = _num_tokens(line)
        if len(nums) < 2:
            continue
        candidate = (nums[0], nums[-1])
        if best is None or abs(candidate[1]) > abs(best[1]):
            best = candidate
    return best if best is not None else (None, None)


def _extract_word_lines(pdf_path: Path) -> list[list[str]]:
    grouped: list[list[dict]] = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            words = sorted(
                page.extract_words(x_tolerance=1, y_tolerance=1, keep_blank_chars=False, use_text_flow=False),
                key=lambda w: (round(float(w.get("top", 0.0)), 1), float(w.get("x0", 0.0))),
            )
            for word in words:
                top = float(word.get("top", 0.0))
                if grouped and abs(float(grouped[-1][0].get("top", 0.0)) - top) <= 1.0:
                    grouped[-1].append(word)
                else:
                    grouped.append([word])

    lines: list[list[str]] = []
    for group in grouped:
        tokens = [_strip_accents(str(w.get("text", "")).strip()).upper() for w in sorted(group, key=lambda w: float(w.get("x0", 0.0)))]
        tokens = [token for token in tokens if token]
        if tokens:
            lines.append(tokens)
    return lines


def _parse_demanda_medidor_registrada_words(pdf_path: Path) -> tuple[float | None, float | None]:
    ponta = None
    fponta = None
    for tokens in _extract_word_lines(pdf_path):
        if "DN" not in tokens or "KW" not in tokens:
            continue
        if "PT" in tokens and ponta is None:
            for token in reversed(tokens):
                valor = _to_float_mixed(token)
                if valor is not None:
                    ponta = valor
                    break
        if "FP" in tokens and fponta is None:
            for token in reversed(tokens):
                valor = _to_float_mixed(token)
                if valor is not None:
                    fponta = valor
                    break
        if ponta is not None and fponta is not None:
            break
    return ponta, fponta


def _parse_cliente_livre(text_ascii: str) -> bool:
    return "CLIENTE LIVRE" in text_ascii or "ACL-COM ICMS ST" in text_ascii


def _parse_desconto_fio_percentuais(text_ascii: str) -> tuple[float | None, float | None]:
    match = re.search(
        r"DESCONTO DE\s*([\d\.,]+)%.*?TUSD DE DEMANDA.*?DESCONTO DE\s*([\d\.,]+)%.*?TUSD DE CONSUMO",
        text_ascii,
        re.IGNORECASE,
    )
    if not match:
        return None, None
    return _to_float_br(match.group(1)), _to_float_br(match.group(2))


def _parse_observacao_ajuste(lines_ascii: list[str]) -> tuple[str, float | None]:
    for line in lines_ascii:
        if "COBRANCA AJUSTE DE FATURAMENTO" not in line:
            continue
        match = re.search(r"COBRANCA AJUSTE DE FATURAMENTO\s+(-?[\d\.,]+)", line)
        if match:
            return "139", _to_float_br(match.group(1))
    return "", None


def _parse_observacao_credito_viol(lines_ascii: list[str]) -> tuple[str, float | None]:
    for line in lines_ascii:
        if not line.startswith("CRED VIOL"):
            continue
        match = re.search(r"(-?[\d\.,]+)\s*$", line)
        if match:
            return "204", _to_float_br(match.group(1))
    return "", None


def _parse_subsidio_tarifario(lines_ascii: list[str]) -> tuple[float | None, float | None]:
    _, subsidio_tusd_val = _parse_item_qtd_valor(lines_ascii, "SUBSIDIO TARIFARIO TUSD")
    _, subsidio_dem_isenta_val = _parse_item_qtd_valor(
        lines_ascii,
        "SUBSIDIO TARIFARIO DEM ISENTA TUSD",
    )
    if subsidio_tusd_val is None:
        subsidio_tusd_val = _parse_last_value_matching(lines_ascii, ("SUBSIDIO TARIFARIO TUSD",))
    if subsidio_tusd_val is None:
        _, subsidio_tusd_val = _parse_qtd_valor_matching(
            lines_ascii,
            ("SUBSIDIO TARIFARIO", "TUSD"),
            ("LIQ", "ISENTA"),
        )
    if subsidio_dem_isenta_val is None:
        subsidio_dem_isenta_val = _parse_last_value_matching(lines_ascii, ("SUBSIDIO TARIFARIO", "ISENTA", "TUSD"))
    if subsidio_dem_isenta_val is None:
        _, subsidio_dem_isenta_val = _parse_qtd_valor_matching(
            lines_ascii,
            ("SUBSIDIO TARIFARIO", "ISENTA"),
            ("LIQ",),
        )

    subsidio_liquido = _parse_signed_value_after_prefix(lines_ascii, "SUBSIDIO TARIFARIO LIQUIDO")
    if subsidio_liquido is None:
        subsidio_liquido = _parse_signed_value_matching(lines_ascii, ("SUBSIDIO TARIFARIO", "LIQ"), ("ISENTA",))

    subsidio_liq_dem_isenta = _parse_signed_value_after_prefix(lines_ascii, "SUBSIDIO TARIFARIO LIQDO DEM ISENTA")
    if subsidio_liq_dem_isenta is None:
        subsidio_liq_dem_isenta = _parse_signed_value_matching(lines_ascii, ("SUBSIDIO TARIFARIO", "LIQ", "ISENTA"))

    bruto = sum(v for v in (subsidio_tusd_val, subsidio_dem_isenta_val) if v is not None)
    liquido = sum(v for v in (subsidio_liquido, subsidio_liq_dem_isenta) if v is not None)
    return (round(bruto, 2) if bruto else None), (round(liquido, 2) if liquido else None)


def _parse_bandeira_valor(lines_ascii: list[str]) -> float | None:
    for line in lines_ascii:
        if "BANDEIRA" not in line:
            continue
        line_up = line.upper()
        # Tenta valor monetário primeiro (pode existir mesmo com "SEM TRF")
        currency_like = re.findall(r"(?<![\d,])-?\d{1,3}(?:\.\d{3})*,\d{2}(?!\d)-?", line)
        if currency_like:
            v = abs(_to_float_mixed(currency_like[-1]) or 0.0)
            if v > 0:
                return v
        # Sem valor numérico + indicador de bandeira verde = R$ 0
        if "SEM TRF" in line_up or "SEM TARIFA" in line_up or "VERDE" in line_up:
            return 0.0
        nums = _num_tokens(line)
        if nums:
            return abs(nums[-1]) or None
    return None


def _parse_repercussao_financeira_valores(lines_ascii: list[str]) -> tuple[float | None, float | None]:
    ponta = None
    fponta = None
    for line in lines_ascii:
        if not line.startswith("CONSUMO REPERCUSSAO"):
            continue
        nums = _num_tokens(line)
        if len(nums) >= 6:
            valor = nums[2]
        elif len(nums) >= 4:
            valor = nums[-1]
        else:
            continue
        if "FORA" in line or "F PONTA" in line:
            fponta = max(fponta or 0.0, valor)
        else:
            ponta = max(ponta or 0.0, valor)
    return (round(ponta, 2) if ponta else None), (round(fponta, 2) if fponta else None)


def _parse_multas_diversas_mt(lines_ascii: list[str]) -> float | None:
    reperc_ponta, reperc_fponta = _parse_repercussao_financeira_valores(lines_ascii)
    relig = _parse_single_value_after_prefix(lines_ascii, "RELIGACAO PROGRAMADA")
    deslig = _parse_single_value_after_prefix(lines_ascii, "DESLIGAMENTO PROGRAMADO")
    lig = _parse_single_value_after_prefix(lines_ascii, "LIGACAO PROGRAMADA")
    taxas_servico = sum(v for v in (relig, deslig, lig) if v is not None)

    if reperc_ponta is not None or reperc_fponta is not None:
        total = sum(v for v in (reperc_ponta, reperc_fponta) if v is not None) + taxas_servico
        return round(total, 2) if total else None

    valores_por_periodo: dict[str, float] = {}
    prefixos_por_periodo = {
        "P": ("ENERGIA ELETRICA ACL-COM ICMS ST P",),
        "F": ("ENERGIA ELETRICA ACL-COM ICMS ST FP", "ENERGIA ELETRICA ACL-COM ICMS ST F"),
    }
    for periodo, prefixos in prefixos_por_periodo.items():
        for prefix in prefixos:
            for line in lines_ascii:
                if not (line == prefix or line.startswith(prefix + " ")):
                    continue
                nums = _num_tokens(line)
                if len(nums) >= 6:
                    val = nums[2]
                elif len(nums) >= 4:
                    val = nums[-1]
                else:
                    continue
                valores_por_periodo[periodo] = max(valores_por_periodo.get(periodo, 0.0), val)
    energia_acl_total = round(sum(valores_por_periodo.values()), 2) if valores_por_periodo else None
    deducao_acl = _parse_single_value_after_prefix(lines_ascii, "DEDUCAO ENERGIA ELETRICA ACL-SEM ICMS")
    if energia_acl_total is not None and deducao_acl is not None:
        valor = max(energia_acl_total - deducao_acl, 0.0) + taxas_servico
        return round(valor, 2) if valor else None
    if taxas_servico:
        return round(taxas_servico, 2)
    return None


def _parse_multas_atraso_mt(lines_ascii: list[str]) -> float | None:
    total = 0.0
    found = False
    for line in lines_ascii:
        if "IMP.RET" in line:
            continue
        if not any(term in line for term in ("MULTA", "JUROS", "MORA", "CORRECAO", "CORRECAO MONETARIA")):
            continue
        nums = _num_tokens(line)
        if not nums:
            continue
        total += abs(nums[-1])
        found = True
    return round(total, 2) if found else None


def _parse_tarifa_subgrupo_mt(text_ascii: str, lines_ascii: list[str]) -> tuple[str, str, str]:
    tarifa = ""
    if "TARIFA HORARIA VERDE" in text_ascii or "HORARIA VERDE" in text_ascii or "HORO-SAZONAL VERDE" in text_ascii:
        tarifa = "HS - Verde"
    elif "TARIFA HORARIA AZUL" in text_ascii or "HORARIA AZUL" in text_ascii or "HORO-SAZONAL AZUL" in text_ascii:
        tarifa = "HS - Azul"

    subgrupo = ""
    for line in lines_ascii:
        if line.startswith("A4") or " A4 " in f" {line} ":
            subgrupo = "A4 [2,3kV a 25kV]"
            break
        if line.startswith("A3A") or " A3A " in f" {line} ":
            subgrupo = "A3a [30kV a 44kV]"
            break
        # Alguns PDFs COPEL MT perdem o "3" no OCR e o cabeçalho "A3a ..."
        # vira "AS ...". Nesses casos, preservamos o roteamento MT correto.
        if line.startswith("AS ") and "BANCOS" in line:
            subgrupo = "A3a [30kV a 44kV]"
            break
        if line.startswith("A2") or " A2 " in f" {line} ":
            subgrupo = "A2 [88kV a 138kV]"
            break

    # fallback: search anywhere in full text
    if not subgrupo:
        if re.search(r"\bA4\b", text_ascii):
            subgrupo = "A4 [2,3kV a 25kV]"
        elif re.search(r"\bA3A\b", text_ascii):
            subgrupo = "A3a [30kV a 44kV]"
        elif re.search(r"\bAS COMERCIAL\b", text_ascii) and "BANCOS" in text_ascii:
            subgrupo = "A3a [30kV a 44kV]"
        elif re.search(r"\bA2\b", text_ascii):
            subgrupo = "A2 [88kV a 138kV]"

    return tarifa, subgrupo, tarifa.upper() if tarifa else ""


def _strip_accents(text: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFKD", text or "") if not unicodedata.combining(ch))


def _parse_nota_fiscal_mt(text_original: str, text_ascii: str) -> tuple[str, str]:
    text_sem_acento = _strip_accents(text_original)
    match = re.search(
        r"NOTA FISCAL\s+No\.\s*(\d+)\s*-\s*S[EÉ]RIE\s*\d+\s*DATA DE EMISS[ÃA]O:\s*(\d{2}/\d{2}/\d{4})",
        text_sem_acento,
        re.IGNORECASE,
    )
    if match:
        return match.group(1), match.group(2)

    match = re.search(
        r"NOTA FISCAL NO\.\s*(\d+)\s*-\s*SERIE\s*\d+\s*DATA DE EMISSAO:\s*(\d{2}/\d{2}/\d{4})",
        text_ascii,
    )
    if match:
        return match.group(1), match.group(2)
    return "", ""


def _parse_endereco_mt(lines_original: list[str], instalacao: str) -> str:
    endereco = ""
    cep = ""
    cidade_estado = ""

    for idx, line in enumerate(lines_original):
        line_norm = _strip_accents(line).upper().strip()
        if line_norm.startswith("ENDERECO:"):
            endereco = line.split(":", 1)[1].strip()
            for prox in lines_original[idx + 1: idx + 4]:
                prox_norm = _strip_accents(prox).upper().strip()
                if prox_norm.startswith("NOTA FISCAL"):
                    break
                if prox_norm.startswith("CEP:"):
                    cep = prox.split(":", 1)[1].strip()
                    continue
                if prox_norm.startswith("CIDADE:"):
                    cidade_estado = prox.strip()
                    break
            break

    partes = [p for p in (endereco, cep, cidade_estado) if p]
    joined = " - ".join(partes).strip()
    if instalacao and joined.endswith(instalacao):
        joined = joined[:-len(instalacao)].strip()
    return joined


def _num_tokens(line: str) -> list[float]:
    return [
        v for v in (_to_float_br(token) for token in _RE_NUMBER.findall(line or "")) if v is not None
    ]


def _parse_energy_line(lines_ascii: list[str], prefix: str) -> tuple[float | None, float | None, float | None]:
    best: tuple[int, float | None, float | None, float | None] | None = None
    for line in lines_ascii:
        if not line.startswith(prefix):
            continue

        m_extrato = re.search(
            rf"{re.escape(prefix)}\s+\d+\s+\d+\s+([\d\.,]+)\s+([\d\.,]+)\s+[\d\.,]+\s+([\d\.,]+)\s*$",
            line,
        )
        if m_extrato:
            cand = (
                2,
                _to_float_br(m_extrato.group(1)),
                _to_float_br(m_extrato.group(2)),
                _to_float_br(m_extrato.group(3)),
            )
            if best is None or cand[0] > best[0]:
                best = cand
            continue

        m_fiscal = re.search(
            rf"{re.escape(prefix)}\s+KWH\s+([\d\.,]+)\s+[\d\.,]+\s+([\d\.,]+)",
            line,
        )
        if m_fiscal:
            qty = _to_float_br(m_fiscal.group(1))
            val = _to_float_br(m_fiscal.group(2))
            cand = (1, qty, qty, val)
            if best is None or cand[0] > best[0]:
                best = cand

    if not best:
        return None, None, None
    return best[1], best[2], best[3]


def _parse_item_qtd_valor(
    lines_ascii: list[str],
    prefix: str,
    unit_pattern: str = r"KWH|MWH|KW|UN",
) -> tuple[float | None, float | None]:
    for line in lines_ascii:
        if not line.startswith(prefix):
            continue
        match = re.search(
            rf"{re.escape(prefix)}\s+(?:{unit_pattern})\s+(-?[\d\.,]+)\s+[\d\.,-]+\s+(-?[\d\.,]+)",
            line,
        )
        if match:
            return _to_float_br(match.group(1)), _to_float_br(match.group(2))
    return None, None


def _parse_demanda_contratada_todos_periodos(text_ascii: str) -> float | None:
    match = re.search(r"MONTANTE EM TODOS OS PERIODOS:?\s*([\d\.,]+)\s*KW", text_ascii)
    return _to_float_br(match.group(1)) if match else None


def _parse_demanda_medidor_registrada(lines_ascii: list[str]) -> tuple[float | None, float | None]:
    ponta = None
    fponta = None

    for somente_detalhada in (True, False):
        for line in lines_ascii:
            if "DEMANDA KW" not in line:
                continue
            detalhada = bool(re.match(r"^\d+.*\bDEMANDA KW\b", line))
            if somente_detalhada and not detalhada:
                continue
            if ponta is None:
                ponta = _parse_line_with_dotted_decimal(line, " PT") or _parse_line_with_dotted_decimal(line, " TP")
            if fponta is None:
                fponta = _parse_line_with_dotted_decimal(line, " FP")
        if ponta is not None or fponta is not None:
            break

    # Layout alternativo: "MEDIDOR DEMANDA ATIVA - KW FORA PONTA ANT ATU FATOR VAL"
    # Valor registrado é o último número inteiro da linha (diferença de leituras)
    if fponta is None:
        for line in lines_ascii:
            if "DEMANDA ATIVA - KW FORA PONTA" in line or "DEMANDA ATIVA - KW HFP" in line:
                m = re.search(r"(\d+)\s*$", line)
                if m:
                    fponta = float(m.group(1))
                    break
    if ponta is None:
        for line in lines_ascii:
            if "DEMANDA ATIVA - KW PONTA" in line and "FORA PONTA" not in line:
                m = re.search(r"(\d+)\s*$", line)
                if m:
                    ponta = float(m.group(1))
                    break

    # Layout HFP/Único: "DEMANDA ATIVA HFP KW VAL tarifa ..." — tarifa monômia Verde
    if fponta is None:
        for line in lines_ascii:
            if re.match(r"DEMANDA ATIVA\s+(?:HFP|UNICO|HFP/UNICO)\s+KW\s+", line):
                m = re.search(r"KW\s+([\d]+(?:[.,]\d+)?)", line)
                if m:
                    fponta = _to_float_mixed(m.group(1))
                    break

    return ponta, fponta


def _parse_demand_mt(lines_ascii: list[str]) -> tuple[float | None, float | None, float | None, float | None]:
    registrada = None
    contratada = None
    faturada = None
    valor_principal = None
    valor_isenta = None

    for line in lines_ascii:
        if not line.startswith("DEMANDA USD "):
            continue
        if "ISENTA ICMS" in line:
            continue

        m_extrato = re.search(
            r"DEMANDA USD\s+\d+\s+\d+\s+([\d\.,]+)\s+([\d\.,]+)\s+([\d\.,]+)\s+[\d\.,]+\s+([\d\.,]+)\s*$",
            line,
        )
        if m_extrato:
            registrada = _to_float_br(m_extrato.group(1))
            contratada = _to_float_br(m_extrato.group(2))
            faturada = _to_float_br(m_extrato.group(3))
            valor_principal = _to_float_br(m_extrato.group(4))
            continue

        m_fiscal = re.search(
            r"DEMANDA USD(?:\s+KW)?\s+([\d\.,]+)\s+[\d\.,]+\s+([\d\.,]+)",
            line,
        )
        if m_fiscal:
            registrada = registrada if registrada is not None else _to_float_br(m_fiscal.group(1))
            faturada = faturada if faturada is not None else _to_float_br(m_fiscal.group(1))
            if valor_principal is None:
                valor_principal = _to_float_br(m_fiscal.group(2))
            continue

        nums = _num_tokens(line)
        if len(nums) >= 3:
            registrada = registrada if registrada is not None else nums[0]
            faturada = faturada if faturada is not None else nums[1]
            if valor_principal is None:
                valor_principal = nums[-1]

    for line in lines_ascii:
        if not line.startswith("DEMANDA USD ISENTA ICMS"):
            continue
        nums = _num_tokens(line)
        if len(nums) >= 2:
            valor_isenta = nums[-1]

    if contratada is None:
        text = "\n".join(lines_ascii)
        m_contr = re.search(r"DEMANDA TODOS OS PERIODOS:\s*([\d\.,]+)\s*KW", text)
        if m_contr:
            contratada = _to_float_br(m_contr.group(1))

    # Formato B3/HFP: "DEMANDA ATIVA HFP KW X ..." — registrada e faturada
    if registrada is None:
        for line in lines_ascii:
            if "DEMANDA ATIVA HFP" in line:
                nums = _num_tokens(line)
                if nums:
                    registrada = nums[0]
                    if faturada is None:
                        faturada = nums[0]
                    break

    # "DEMANDA ATIVA X X" sem HFP — registrada + faturada
    if registrada is None:
        for line in lines_ascii:
            if line.startswith("DEMANDA ATIVA ") and "HFP" not in line and "KW" not in line:
                nums = _num_tokens(line)
                if len(nums) >= 2:
                    registrada = nums[0]
                    if faturada is None:
                        faturada = nums[1]
                    break

    # "DEMANDA FORA PONTA X" — contratada HFP explícita
    if contratada is None:
        for line in lines_ascii:
            if "DEMANDA FORA PONTA" in line and "0 KW" not in line and " 0" not in line.split("DEMANDA FORA PONTA")[-1].split()[0:1]:
                nums = _num_tokens(line)
                if nums and nums[0] > 0:
                    contratada = nums[0]
                    break

    valor_total = (valor_principal or 0.0) + (valor_isenta or 0.0)
    return registrada, contratada, faturada, (round(valor_total, 2) if valor_total else None)


def _parse_demanda_tusd_isenta(lines_ascii: list[str]) -> tuple[float | None, float | None]:
    best: tuple[float, float] | None = None
    for line in lines_ascii:
        if "DEMANDA" not in line or "ISENTA" not in line:
            continue
        if any(term in line for term in ("SUBSIDIO", "LIQ")):
            continue
        nums = _num_tokens(line)
        if len(nums) < 2:
            continue
        qty = nums[0]
        if "DISTRIBUICA" in line and len(nums) >= 3:
            val = nums[2]
        else:
            val = nums[-1]
        candidate = (qty, val)
        if best is None or abs(candidate[1]) > abs(best[1]):
            best = candidate
    return best if best is not None else (None, None)


def _parse_reativo_mt(lines_ascii: list[str], prefix: str) -> tuple[float | None, float | None, float | None]:
    reg, fat, val = _parse_energy_line(lines_ascii, prefix)
    return reg, fat, val


def _parse_federal_total(text_ascii: str) -> float | None:
    match = re.search(r"RETENCAO DE TRIBUTOS FEDERAIS.*?R\$\s*([\d\.,]+)", text_ascii)
    return _to_float_br(match.group(1)) if match else None


def _parse_demanda_ponta_registrada_mt(lines_ascii: list[str]) -> float | None:
    """Parse demand registrada ponta from medidor line."""
    for somente_detalhada in (True, False):
        for line in lines_ascii:
            if "KW" not in line:
                continue
            detalhada = bool(re.match(r"^\d+.*\bDEMANDA KW\b", line))
            if somente_detalhada and not detalhada:
                continue
            valor = _parse_line_with_dotted_decimal(line, " PT") or _parse_line_with_dotted_decimal(line, " TP")
            if valor is not None:
                return valor
    return None


def _parse_excedente_reativo_fp_mt(lines_ascii: list[str]) -> float | None:
    """Parse qty from minimal excedente line: 'ENERGIA REAT EXCED TE F PONTA KWH 387'"""
    prefix = "ENERGIA REAT EXCED TE F PONTA"
    for line in lines_ascii:
        if not line.startswith(prefix):
            continue
        m = re.search(r"KWH\s+([\d\.,]+)", line)
        if m:
            return _to_float_br(m.group(1))
    return None


def _parse_excedente_reativo_resumido_mt(
    lines_ascii: list[str],
    prefixes: tuple[str, ...],
) -> tuple[float | None, float | None, float | None]:
    for line in lines_ascii:
        if not any(line.startswith(prefix) for prefix in prefixes):
            continue
        qty = None
        match = re.search(r"KWH\s+([\d\.,]+)", line)
        if match:
            qty = _to_float_br(match.group(1))
        else:
            nums = _num_tokens(line)
            if nums:
                qty = nums[-1]
        if qty is not None:
            return qty, qty, (0.0 if qty == 0 else None)
    return None, None, None


def _parse_irpj_por_percentual_mt(lines_ascii: list[str]) -> list[tuple[float | None, float]]:
    """Return list of (percentual, valor_signed) for each IMP.RET. IRPJ line.

    In COPEL MT non-mercado-livre, these lines carry CONSUMO (1.20%) and
    DEMANDA (4.80%) retentions instead of a generic IRPJ retention.
    """
    result: list[tuple[float | None, float]] = []
    seen: set[tuple] = set()
    pattern = re.compile(
        r"IMP\.?\s*RET\.?\s*IRPJ\b.*?\(?([\d\.,]+)%\)?\s+(?:UN\s+)?(-?[\d\.,]+-?)",
        re.IGNORECASE,
    )
    pattern2 = re.compile(
        r"IMP\.?\s*RET\.?\s*IRPJ\b.*?\(?([\d\.,]+)%\)?.*?(-?[\d\.,]+)\s*$",
        re.IGNORECASE,
    )
    for raw_line in lines_ascii:
        line = " ".join(raw_line.split())
        if "IMP" not in line or "RET" not in line or "IRPJ" not in line:
            continue
        for pat in (pattern, pattern2):
            m = pat.search(line)
            if m:
                perc = _to_float_br(m.group(1))
                val = -abs(_to_float_br(m.group(2)) or 0.0)
                key = (perc, abs(val))
                if key not in seen:
                    seen.add(key)
                    result.append((perc, val))
                break
    return result


def _deve_ratear_irpj_mt(irpj_linhas: list[tuple[float | None, float]]) -> bool:
    percentuais = {round(float(perc), 2) for perc, _ in irpj_linhas if perc is not None}
    return 1.2 in percentuais and any(perc > 3.0 for perc in percentuais)


def _build_record(pdf_path: Path) -> dict:
    row = {header: ("" if header in TEXT_HEADERS else None) for header in HEADERS}
    row["ARQUIVO"] = str(pdf_path)
    row["fatCarimbo"] = _carimbo_from_path(pdf_path)
    row["ERRO"] = ""
    row["concCod"] = "COPEL"

    try:
        lines_original, lines_ascii, text_original, text_ascii = _extract_pages(pdf_path)
        cliente_livre = _parse_cliente_livre(text_ascii)

        leitura_ant, leitura_atual = _parse_header_dates(lines_ascii)
        referencia, vencimento, valor_fatura = _parse_ref_vcto_valor(text_ascii)
        nota_fiscal, data_emissao = _parse_nota_fiscal_mt(text_original, text_ascii)
        cnpj = _parse_cnpj(text_ascii)
        codigo_cliente = _parse_codigo_cliente(text_ascii)
        instalacao = _parse_instalacao(lines_ascii, text_ascii)
        endereco = _parse_endereco_mt(lines_original, instalacao) or _parse_endereco(lines_original, instalacao)
        codigo_barras = _parse_codigo_barras(text_ascii)
        tarifa, subgrupo, tarifa_detectada = _parse_tarifa_subgrupo_mt(text_ascii, lines_ascii)
        icms_base, icms_aliquota, icms_valor = _parse_tax_line(lines_ascii, text_ascii, "ICMS")
        _, pis_aliquota, pis_valor = _parse_tax_line(lines_ascii, text_ascii, "PIS(?:/PASEP)?")
        _, cofins_aliquota, cofins_valor = _parse_tax_line(lines_ascii, text_ascii, "COFINS")
        ilum = _parse_ilum_publica(lines_ascii)
        ret = _parse_retencoes(lines_ascii, text_ascii)
        debitos_anteriores = _parse_debitos_anteriores(text_ascii)
        valor_nota = _guess_valor_nota_fiscal(text_ascii, valor_fatura, icms_base)
        trib_federal = None

        ponta_te_reg, ponta_te_fat, ponta_te_val = _parse_energy_line(lines_ascii, "ENERGIA ELETRICA TE PONTA")
        ponta_usd_reg, ponta_usd_fat, ponta_usd_val = _parse_energy_line(lines_ascii, "ENERGIA ELETRICA USD PONTA")
        fponta_te_reg, fponta_te_fat, fponta_te_val = _parse_energy_line(lines_ascii, "ENERGIA ELETRICA TE F PONTA")
        fponta_usd_reg, fponta_usd_fat, fponta_usd_val = _parse_energy_line(lines_ascii, "ENERGIA ELETRICA USD F PONTA")

        acl_ponta_qtd, acl_ponta_val = _parse_item_qtd_valor(lines_ascii, "ENERGIA ELET CONSUMO PTA")
        acl_fponta_qtd, acl_fponta_val = _parse_item_qtd_valor(lines_ascii, "ENERGIA ELET CONSUMO F PTA")
        escassez_qtd, escassez_val = _parse_item_qtd_valor(lines_ascii, "ESCASSEZ HIDRICA TP TE")

        reat_p_reg, reat_p_fat, reat_p_val = _parse_reativo_mt(lines_ascii, "ENERGIA REAT EXCED TE PONTA")
        if reat_p_reg is None:
            reat_p_reg, reat_p_fat, reat_p_val = _parse_reativo_mt(lines_ascii, "ENERGIA REAT EXC PONTA")
        if reat_p_reg is None:
            reat_p_reg, reat_p_fat, reat_p_val = _parse_excedente_reativo_resumido_mt(
                lines_ascii,
                ("ENERGIA REAT EXCED TE PONTA", "ENERGIA REAT EXC PONTA"),
            )

        reat_fp_reg, reat_fp_fat, reat_fp_val = _parse_reativo_mt(lines_ascii, "ENERGIA REAT EXCED TE F PONTA")
        if reat_fp_reg is None:
            reat_fp_reg, reat_fp_fat, reat_fp_val = _parse_reativo_mt(lines_ascii, "ENERGIA REAT EXC F PONTA")
        # fallback: minimal line with only qty (e.g. "ENERGIA REAT EXCED TE F PONTA KWH 387")
        if reat_fp_reg is None:
            reat_fp_reg, reat_fp_fat, reat_fp_val = _parse_excedente_reativo_resumido_mt(
                lines_ascii,
                ("ENERGIA REAT EXCED TE F PONTA", "ENERGIA REAT EXC F PONTA"),
            )
        if reat_fp_reg is None:
            reat_fp_reg = _parse_excedente_reativo_fp_mt(lines_ascii)
            if reat_fp_reg is not None:
                reat_fp_fat = reat_fp_reg
                reat_fp_val = None

        dem_reg, dem_contr, dem_fat, dem_val = _parse_demand_mt(lines_ascii)
        dem_dist_qtd, dem_dist_val = _parse_item_qtd_valor(lines_ascii, "DEMANDA DE DISTRIBUICAO TUSD", unit_pattern=r"KW")
        if dem_dist_qtd is None and dem_dist_val is None:
            dem_dist_qtd, dem_dist_val = _parse_qtd_valor_matching(
                lines_ascii,
                ("DEMANDA", "DISTRIBUICAO", "TUSD"),
                ("ULTRAP", "ISENTA"),
            )
        dem_isenta_qtd, dem_isenta_val = _parse_demanda_tusd_isenta(lines_ascii)
        dem_ultra_qtd, dem_ultra_val = _parse_item_qtd_valor(lines_ascii, "DEMANDA ULTRAP.-DISTRIBUICAO TUSD", unit_pattern=r"KW")
        dem_todos_periodos = _parse_demanda_contratada_todos_periodos(text_ascii)
        dem_ponta_medidor, dem_fponta_medidor = _parse_demanda_medidor_registrada(lines_ascii)
        dem_ponta_words, dem_fponta_words = _parse_demanda_medidor_registrada_words(pdf_path)
        desconto_demanda_pct, desconto_consumo_pct = _parse_desconto_fio_percentuais(text_ascii)
        obs_ajuste_cod, obs_ajuste_val = _parse_observacao_ajuste(lines_ascii)
        subsidio_bruto, subsidio_liquido = _parse_subsidio_tarifario(lines_ascii)
        bandeira_val = _parse_bandeira_valor(lines_ascii)
        multas_atraso = _parse_multas_atraso_mt(lines_ascii)
        multas_diversas = _parse_multas_diversas_mt(lines_ascii)

        # ponta registrada from medidor DN KW PT line (period-decimal format)
        dem_ponta_reg_dn = _parse_demanda_ponta_registrada_mt(lines_ascii)
        dem_ponta_registrada = dem_ponta_words or dem_ponta_medidor or dem_ponta_reg_dn
        dem_ponta_faturada: float | None = None

        ponta_reg = acl_ponta_qtd if acl_ponta_qtd is not None else (ponta_te_reg or ponta_usd_reg)
        ponta_fat = acl_ponta_qtd if acl_ponta_qtd is not None else (ponta_te_fat or ponta_usd_fat)
        ponta_val = acl_ponta_val if acl_ponta_val is not None else (round((ponta_te_val or 0.0) + (ponta_usd_val or 0.0), 2) or None)

        fponta_reg = acl_fponta_qtd if acl_fponta_qtd is not None else (fponta_te_reg or fponta_usd_reg)
        fponta_fat = acl_fponta_qtd if acl_fponta_qtd is not None else (fponta_te_fat or fponta_usd_fat)
        fponta_val = acl_fponta_val if acl_fponta_val is not None else (round((fponta_te_val or 0.0) + (fponta_usd_val or 0.0), 2) or None)

        dem_dist_total_qtd = sum(v for v in (dem_dist_qtd, dem_isenta_qtd) if v is not None)
        dem_dist_total_val = sum(v for v in (dem_dist_val, dem_isenta_val) if v is not None)

        if dem_dist_qtd is not None:
            dem_reg = round(dem_dist_total_qtd, 2) if dem_dist_total_qtd else 0.0
            dem_fat = round(dem_dist_total_qtd, 2) if dem_dist_total_qtd else 0.0
        if dem_todos_periodos is not None:
            dem_contr = dem_todos_periodos
        if dem_dist_val is not None:
            dem_val = round(dem_dist_total_val, 2) if dem_dist_total_val else 0.0
        if dem_dist_qtd is None:
            if dem_fponta_words is not None:
                dem_reg = dem_fponta_words
            elif dem_fponta_medidor is not None:
                dem_reg = dem_fponta_medidor
        # Fallback: quando contratada não aparece no PDF, usa registrada
        if dem_contr is None and dem_reg is not None and dem_reg > 0:
            dem_contr = dem_reg

        if dem_dist_qtd is None and dem_contr is not None and dem_reg is not None and not dem_ultra_qtd:
            dem_fat = max(dem_reg, dem_contr)
        elif cliente_livre:
            if dem_reg is not None and dem_contr is not None:
                dem_fat = max(dem_reg, dem_contr)
            elif dem_reg is not None:
                dem_fat = dem_reg

        # Fallback zero-billing: faturas cobradas pela demanda contratada têm a
        # demanda medida (registrada) = 0. O CONSEN espera o valor contratado
        # ("Demanda Todos os Períodos: NN kW") em registrada/faturada.
        if dem_contr is not None and dem_contr > 0:
            if not dem_reg:
                dem_reg = dem_contr
            if not dem_fat:
                dem_fat = dem_contr

        irpj_linhas = _parse_irpj_por_percentual_mt(lines_ascii)
        if _deve_ratear_irpj_mt(irpj_linhas):
            for perc, val in irpj_linhas:
                if perc is not None and perc < 3.0:
                    if ret["CONSUMO"] == (None, 0.0):
                        ret["CONSUMO"] = (perc, round(val, 2))
                else:
                    if ret["DEMANDA"] == (None, 0.0):
                        ret["DEMANDA"] = (perc, round(val, 2))
            ret["IRPJ"] = (None, 0.0)

        row.update(
            {
                "Instalacao": instalacao or "",
                "fatDataEmissao": data_emissao or "",
                "fatDataVcto": vencimento or "",
                "fatValorFatura": valor_fatura,
                "fatDataCadastro": data_emissao or "",
                "fatDataLeituraAnterior": leitura_ant or "",
                "fatDataLeituraAtual": leitura_atual or "",
                "fatIlumPublica": ilum,
                "cadTarifaCod": 1 if tarifa == "HS - Verde" else (2 if tarifa == "HS - Azul" else tarifa or ""),
                "cadSubGrupoCod": subgrupo or "",
                "fatICMS": icms_valor,
                "fatPIS": pis_valor,
                "fatCOFINS": cofins_valor,
                "fatValorNotaFiscal": valor_nota,
                "CNPJ": cnpj or "",
                "ENDERECO": endereco or "",
                "NOTAFISCAL": nota_fiscal or "",
                "CODIGOCLIENTE": codigo_cliente or instalacao or "",
                "fatDataReferencia": referencia or "",
                "fatCodigoBarras": codigo_barras or "",
                "Debitos anteriores": debitos_anteriores,
                "usuCod": "Enzo",
                "fatDesIcmsAliquota": icms_aliquota,
                "fatDescPisAliquota": pis_aliquota,
                "fatDesCofinsAliquota": cofins_aliquota,
                "fatDescPisPercRetImposto": ret["PIS"][0],
                "fatDescPisValRetImposto": ret["PIS"][1],
                "fatDescCofinsPercRetImposto": ret["COFINS"][0],
                "fatDescCofinsValRetImposto": ret["COFINS"][1],
                "fatDescCsllPercRetImposto": ret["CSLL"][0],
                "fatDescCsllValRetImposto": ret["CSLL"][1],
                "fatDescIrpjPercRetImposto": ret["IRPJ"][0],
                "fatDescIrpjValRetImposto": ret["IRPJ"][1],
                "fatDescIrrfPercRetImposto": ret["IRRF"][0],
                "fatDescIrrfValRetImposto": ret["IRRF"][1],
                "fatDescConsumoPercRetImposto": ret["CONSUMO"][0],
                "fatDescConsumoValRetImposto": ret["CONSUMO"][1] or None,
                "fatDescDemandaPercRetImposto": ret["DEMANDA"][0],
                "fatDescDemandaValRetImposto": ret["DEMANDA"][1] or None,
                "fatConPontaRegistrado": ponta_reg,
                "fatConPontaFaturado": ponta_fat,
                "fatConPontaValorReais": ponta_val,
                "fatConFPontaIndRegistrado": fponta_reg,
                "fatConFPontaIndFaturado": fponta_fat,
                "fatConFPontaIndValorReais": fponta_val,
                "fatConPontaExcRegistrado": reat_p_reg,
                "fatConPontaExcFaturado": reat_p_fat,
                "fatConPontaExcValorReais": reat_p_val,
                "fatConFPontaIndExcRegistrado": reat_fp_reg,
                "fatConFPontaIndExcFaturado": reat_fp_fat,
                "fatConFPontaIndExcValorReais": reat_fp_val,
                "fatDemContratadaFPonta": dem_contr,
                "fatDemPontaRegistrada": dem_ponta_registrada,
                "fatDemPontaFaturada": dem_ponta_faturada,
                "fatDemFPontaIndRegistrada": dem_reg,
                "fatDemFPontaIndFaturada": dem_fat,
                "fatDemFPontaIndValorReais": dem_val,
                "fatDemFPontaIndUltra": dem_ultra_qtd,
                "fatDemFPontaIndUltraValorReais": dem_ultra_val,
                "fatDescontoFio": desconto_demanda_pct,
                "fatDescontoFioKWh": desconto_consumo_pct,
                "fatBeneficioTarifarioBrutoValorReais": subsidio_bruto,
                "fatBeneficioLiquidoValorReais": subsidio_liquido,
                "fatValBandeira": bandeira_val if bandeira_val is not None else 0.0,
                "fatEscassezHidrica": escassez_qtd,
                "fatEscassezHidricaValorReais": escassez_val,
                "fatTributoFederalVal": trib_federal,
                "fatMultas": multas_atraso,
                "fatMultasDiversas": multas_diversas,
                "obsCod_1": obs_ajuste_cod,
                "obsValor_1": obs_ajuste_val,
                "obsCod_2": "",
                "obsValor_2": None,
                "TARIFA_DETECTADA": tarifa_detectada,
            }
        )

        missing = [
            field
            for field in ("Instalacao", "fatDataLeituraAnterior", "fatDataLeituraAtual", "fatDataVcto", "cadTarifaCod")
            if not row.get(field)
        ]
        if missing:
            row["ERRO"] = f"campos_criticos_ausentes: {', '.join(missing)}"

    except Exception as exc:
        row["ERRO"] = f"{type(exc).__name__}: {exc}"

    return row


def _default_pasta(mes: str, ano: str) -> Path:
    return DOWNLOAD_DIR / f"{mes}.{ano}" / "MT"


def _output_xlsx(mes: str, ano: str) -> Path:
    return OCR_DIR / f"ocr_copel_MT_{mes}{ano}.xlsx"


def parse_args() -> argparse.Namespace:
    hoje = dt.date.today()
    parser = argparse.ArgumentParser(description="OCR COPEL MT")
    parser.add_argument("--mes", default=f"{hoje.month:02d}")
    parser.add_argument("--ano", default=str(hoje.year))
    parser.add_argument("--pasta", default="")
    parser.add_argument("--saida", default="")
    parser.add_argument("--carimbo", action="append", default=[])
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    mes = str(args.mes).zfill(2)
    ano = str(args.ano)
    pasta = Path(args.pasta) if str(args.pasta).strip() else _default_pasta(mes, ano)
    out = Path(str(args.saida).strip()) if str(args.saida).strip() else _output_xlsx(mes, ano)
    _mkdir_seguro(OCR_DIR)
    _mkdir_seguro(out.parent)

    log.info("=" * 64)
    log.info("OCR COPEL MT")
    log.info("=" * 64)
    log.info(f"Pasta origem : {pasta}")
    log.info(f"Saida xlsx   : {out}")

    if not pasta.exists():
        log.error(f"Pasta nao encontrada: {pasta}")
        return 1

    pdfs = sorted(pasta.glob("*.pdf"))
    if args.carimbo:
        wanted = {str(c).upper().replace("BB_", "") for c in args.carimbo}
        pdfs = [p for p in pdfs if _carimbo_from_path(p) in wanted]

    if not pdfs:
        log.error("Nenhum PDF encontrado para processar.")
        return 1

    registros = []
    for idx, pdf in enumerate(pdfs, start=1):
        log.info(f"[{idx}/{len(pdfs)}] {pdf.name}")
        registros.append(_build_record(pdf))

    df = pd.DataFrame(registros)
    for header in HEADERS:
        if header not in df.columns:
            df[header] = "" if header in TEXT_HEADERS else None
    df = df[HEADERS]

    export_df = df.rename(columns=HEADER_DISPLAY)
    export_df.to_excel(out, index=False)

    erros = int(df["ERRO"].fillna("").astype(str).str.strip().ne("").sum())
    log.info("")
    log.info(f"Linhas geradas : {len(df)}")
    log.info(f"Linhas com erro: {erros}")
    log.info(f"Planilha salva : {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
