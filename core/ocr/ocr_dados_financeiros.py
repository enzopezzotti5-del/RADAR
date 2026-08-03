#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OCR Dados Financeiros
=====================

Extrai os itens financeiros (linhas de cobrança e valores) de faturas de
energia elétrica em PDF, gerando uma planilha com colunas dinâmicas.

Cabeçalhos de saída:
    Carimbo, Concessionária, Linha1, Valor1, Linha2, Valor2, ..., LinhaN, ValorN

Uso:
    python ocr_dados_financeiros.py --pasta "\\\\srv\\DOWNLOAD CEMIG\\04.2026\\BT"
    python ocr_dados_financeiros.py --pasta "..." --pasta "..." --saida resultado.xlsx
    python ocr_dados_financeiros.py --pasta "..." --carimbo BB_2004345

Concessionárias suportadas:
    CEMIG, COPEL, CELESC, ENEL, ELETROPAULO, NEOENERGIA, ENERGISA, CPFL, RGE, EQUATORIAL
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
import unicodedata
from pathlib import Path

import pandas as pd
import pdfplumber


# ── Detecção de concessionária ────────────────────────────────────────────────
# A ordem importa: keywords mais específicas primeiro para evitar falsos positivos.

CONCESSIONARIA_MAP: list[tuple[str, list[str]]] = [
    ("ELETROPAULO", ["ELETROPAULO"]),          # ENEL SP — não tem "ENEL" no texto
    ("CEMIG",       ["CEMIG"]),
    ("COPEL",       ["COPEL"]),
    ("CELESC",      ["CELESC"]),
    ("ENEL",        ["ENEL"]),
    ("NEOENERGIA",  ["NEOENERGIA"]),
    ("ENERGISA",    ["ENERGISA"]),
    ("EQUATORIAL",  ["EQUATORIAL", "232.136.008", "CEAL"]),  # inclui Equatorial Alagoas
    ("CPFL",        ["CPFL"]),
    ("RGE",         ["RGE SUL", "RGE -", "RGE S"]),
    ("LIGHT",       ["LIGHT SERVICOS", "LIGHT S.A", "LIGHT,", "BANCO ITAU", "ITAÚ S.A"]),  # LIGHT paga via Itaú
    ("COELBA",      ["COELBA"]),
    ("COSERN",      ["COSERN"]),
    ("ELEKTRO",     ["ELEKTRO"]),
    ("CHESP",       ["CHESP", "COMPANHIA HIDROELETRICA SAO PATRICIO", "01377555000110"]),
    ("RORAIMA",     ["RORAIMA ENERGIA"]),
    ("CEEE",        ["COMPANHIA ESTADUAL DE DISTRIBUICAO", "CEEE", "08.467.115", "08467115"]),
    ("CEB",         ["CEB DISTRIBUICAO", "CEB-D", "07522669", "07.522.669"]),  # CNPJ CEB DF
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("ocr_dados_financeiros")


# ── Utilitários ───────────────────────────────────────────────────────────────

def _to_ascii_upper(text: str) -> str:
    return "".join(
        ch for ch in unicodedata.normalize("NFKD", text or "") if not unicodedata.combining(ch)
    ).upper()


def _to_float_br(value: str | None) -> float | None:
    if not value:
        return None
    txt = str(value).strip()
    neg = txt.startswith("-")
    txt = txt.replace(".", "").replace(",", ".").strip("-")
    try:
        return -float(txt) if neg else float(txt)
    except ValueError:
        return None


def _detectar_concessionaria(text_ascii: str) -> str:
    for nome, keywords in CONCESSIONARIA_MAP:
        for kw in keywords:
            if kw in text_ascii:
                return nome
    return "DESCONHECIDA"


def _extrair_numero_nota(text_ascii: str) -> str:
    patterns = [
        r"NOTA\s+FISCAL(?:\s+ELETRONICA)?(?:\s*/\s*CONTA\s+DE\s+ENERGIA)?\s*(?:N[O0º°]*\.?\s*)?[:\-]?\s*(\d{4,20})",
        r"\bNF(?:-E)?\s*(?:N[O0º°]*\.?\s*)?[:\-]?\s*(\d{4,20})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text_ascii, re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return ""


def _extrair_serie(text_ascii: str) -> str:
    match = re.search(r"\bSERIE\b\s*[:\-]?\s*([A-Z0-9\-\/]{1,10})", text_ascii, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return ""


def _extrair_numero_nota_serie(text_ascii: str) -> tuple[str, str]:
    match = re.search(
        r"NOTA\s+FISCAL(?:\s+ELETRONICA)?(?:\s*/\s*CONTA\s+DE\s+ENERGIA)?"
        r".{0,120}?(?:N[O0º°]*\.?\s*)?[:\-]?\s*(\d{4,20})"
        r".{0,80}?\bSERIE\b\s*[:\-]?\s*([A-Z0-9\-\/]{1,10})",
        text_ascii,
        re.IGNORECASE | re.DOTALL,
    )
    if match:
        return match.group(1).strip(), match.group(2).strip()
    return _extrair_numero_nota(text_ascii), _extrair_serie(text_ascii)


# ── Parser genérico: linha com unidade explícita (kWh, UN, KWH) ───────────────
#
# Estrutura: <nome> <UNIDADE> <qtd> <preço_unit_3+decimais> <Valor_R$_2decimais> ...
# O preço unitário tem 3+ casas decimais; o Valor R$ (2 casas) vem imediatamente depois.

def _parse_linha_com_unidade(line: str, unit_pattern: re.Pattern) -> tuple[str, str | None]:
    unit_match = unit_pattern.search(line)
    if not unit_match:
        return line.strip(), None

    nome = line[:unit_match.start()].strip()
    rest = line[unit_match.end():]

    # Pula o preço unitário (3+ casas decimais) para chegar no Valor R$
    price_match = re.search(r'-?[\d\.]+,\d{3,}', rest)
    if price_match:
        after_price = rest[price_match.end():]
        val_match = re.search(r'-?[\d\.]+,\d{2}(?!\d)', after_price)
        if val_match:
            return nome, val_match.group()

    # Fallback: primeiro valor com exatamente 2 casas decimais
    val_match = re.search(r'-?[\d\.]+,\d{2}(?!\d)', rest)
    return nome, (val_match.group() if val_match else None)


# ── GRUPO 1: "Itens da Fatura" → kWh → TOTAL ─────────────────────────────────
# Concessionárias: CEMIG, NEOENERGIA, ENERGISA
#
# Linha kWh:  <nome> kWh <qtd> <preço_unit> <Valor_R$> ...
# Linha sem kWh (impostos, cosip): <nome> <Valor_R$>  (valor no final da linha)
# Termina em: TOTAL ou TOTAL:

_G1_KWH = re.compile(r'\bkWh\b', re.IGNORECASE)

# Linhas da coluna fiscal direita (tributos sobre a base de cálculo) — não são itens de fatura
_G1_FISCAL_SKIP = re.compile(
    r'^(PIS|COFINS|ICMS|CSLL|IRPJ|PASEP)\s+[\d\.]',
    re.IGNORECASE,
)


def _extrair_grupo1(pages_text: list[str]) -> list[tuple[str, float | None]]:
    """CEMIG / NEOENERGIA / ENERGISA — seção 'Itens da Fatura'."""
    text  = "\n".join(pages_text)
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    result: list[tuple[str, float | None]] = []
    in_section = False

    for line in lines:
        line_up = _to_ascii_upper(line)

        if not in_section:
            if "ITENS DA FATURA" in line_up:
                in_section = True
            continue

        # TOTAL ou TOTAL: encerram a seção
        if re.match(r"^TOTAL[\s:]", line_up) or line_up == "TOTAL":
            break

        if not re.search(r'\d,\d{2}', line):
            continue
        if any(kw in line_up for kw in ("UNID.", "PRECO UNIT", "VALOR (R$)", "BASE CALC",
                                         "TRIBUTO", "ALIQUOTA")):
            continue
        # Ignora linhas da coluna fiscal direita (ex: "PIS 2.239,71 0,87")
        if _G1_FISCAL_SKIP.match(line):
            continue

        if _G1_KWH.search(line):
            nome, valor_str = _parse_linha_com_unidade(line, _G1_KWH)
        else:
            val_match = re.search(r'(-?[\d\.]+,\d{2})(?!\d)\s*$', line)
            if not val_match:
                continue
            nome      = line[:val_match.start()].strip()
            valor_str = val_match.group(1)

        # Limpa nome de números/ruídos que sobram no final
        nome = re.sub(r'[\d,\.\s]+$', '', nome).strip()

        if nome:
            result.append((nome, _to_float_br(valor_str)))

    return result


# ── GRUPO 2: "Itens de Fatura" → KWH → TOTAL ─────────────────────────────────
# Concessionárias: ENEL, ELETROPAULO (ENEL SP)
#
# Linha kWh: <nome> KWH <qtd> <preço_unit> <Valor_R$> ...
# Tem "Subtotal Faturamento" e "Subtotal Outros" antes do TOTAL — ignorados (sem KWH)
# Termina em: TOTAL

_G2_KWH = re.compile(r'\b(kWh|kW|MW)\b', re.IGNORECASE)


def _extrair_grupo2(pages_text: list[str]) -> list[tuple[str, float | None]]:
    """ENEL / ELETROPAULO — seção 'Itens de Fatura'."""
    text  = "\n".join(pages_text)
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    result: list[tuple[str, float | None]] = []
    in_section = False

    for line in lines:
        line_up = _to_ascii_upper(line)

        if not in_section:
            if "ITENS DE FATURA" in line_up:
                in_section = True
            continue

        if re.match(r"^TOTAL\b", line_up):
            break

        # Subtotais não têm KWH — ignorados automaticamente pelo filtro abaixo
        if not _G2_KWH.search(line):
            continue
        if any(kw in line_up for kw in ("UNID.", "FATURADO (KWH)", "COM TRIBUTOS")):
            continue

        nome, valor_str = _parse_linha_com_unidade(line, _G2_KWH)
        if nome:
            result.append((nome, _to_float_br(valor_str)))

    return result


# ── GRUPO 3: kWh com códigos (0D)/(0E)/(0R)/(0S) → TOTAL ────────────────────
# Concessionária: CELESC
#
# Sem header explícito. Todas as linhas com KWH são itens de fatura.
# Pode ter múltiplos SUBTOTAL antes do TOTAL final — ignoramos subtotais,
# paramos somente no TOTAL.

_G3_KWH = re.compile(r'\bKWH\b', re.IGNORECASE)

_G3_SKIP = re.compile(
    r'^(CONSUMO FATURADO|DIAS FATURADOS|DATA DOCUMENTO|ANTERIOR ATUAL)',
    re.IGNORECASE,
)


def _extrair_grupo3(pages_text: list[str]) -> list[tuple[str, float | None]]:
    """CELESC — linhas KWH com códigos (0D)/(0E)/(0R)/(0S)."""
    text  = "\n".join(pages_text)
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    result: list[tuple[str, float | None]] = []

    for line in lines:
        line_up = _to_ascii_upper(line)

        if re.match(r"^TOTAL\b", line_up):
            break
        if re.match(r"^SUBTOTAL\b", line_up):
            continue
        if not _G3_KWH.search(line):
            continue
        if _G3_SKIP.search(line):
            continue

        nome, valor_str = _parse_linha_com_unidade(line, _G3_KWH)
        if nome and valor_str is not None:
            result.append((nome, _to_float_br(valor_str)))

    return result


# ── GRUPO 4: kWh → Total Distribuidora ───────────────────────────────────────
# Concessionárias: CPFL, RGE
#
# Sem header de seção. Itens: <nome> kWh <qtd> <preço1> <preço2> <Valor_R$> ...
# Termina em "Total Distribuidora" ou "TOTAL"

# CPFL/RGE usam tanto "kWh" standalone quanto "[KWh]" dentro do nome do item
_G4_KWH = re.compile(r'\[?kWh\]?', re.IGNORECASE)

_G4_SKIP = re.compile(
    r'^(SALDO|DECLARA|ENERGIA ATIVA|CONSUMO MEDIO)',
    re.IGNORECASE,
)


def _extrair_grupo4(pages_text: list[str]) -> list[tuple[str, float | None]]:
    """CPFL / RGE — kWh até 'Total Distribuidora'."""
    text  = "\n".join(pages_text)
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    result: list[tuple[str, float | None]] = []

    for line in lines:
        line_up = _to_ascii_upper(line)

        if re.match(r"^TOTAL\b", line_up) or "TOTAL DISTRIBUIDORA" in line_up:
            break
        if not _G4_KWH.search(line):
            continue
        if _G4_SKIP.search(line):
            continue
        if not re.search(r'\d,\d{2}', line):
            continue

        nome, valor_str = _parse_linha_com_unidade(line, _G4_KWH)
        if nome and valor_str is not None:
            result.append((nome, _to_float_br(valor_str)))

    return result


# ── GRUPO 5: (kWh) na descrição → antes de "ITENS FINANCEIROS" ───────────────
# Concessionária: EQUATORIAL
#
# Estrutura: <nome> (kWh) <qtd> <preço_6dec> <preço_6dec> <v1> <v2> <Valor_R$> [COFINS ...]
# O Valor R$ é o ÚLTIMO número com 2 casas decimais antes de qualquer keyword fiscal.
# Seção termina em "ITENS FINANCEIROS".

_G5_UNIT = re.compile(r'\(kWh\)', re.IGNORECASE)
_G5_FISCAL = re.compile(r'\b(COFINS|PIS|ICMS|CSLL|IRPJ)\b')


def _extrair_grupo5(pages_text: list[str]) -> list[tuple[str, float | None]]:
    """
    EQUATORIAL — dois tipos de linha:

    1. Antes de 'ITENS FINANCEIROS': linhas com (kWh)
       ex: 'TUSD Energia Fora Ponta (kWh) 12.441,84 0,194979 ... 2.425,90 COFINS ...'
       Valor = último número 2-decimal antes de keyword fiscal

    2. Após 'ITENS FINANCEIROS': linhas sem unidade (CIP, tributos a reter)
       ex: 'Cip-Ilum Pub Pref Munic 12,38'
            'Tributo a Reter IRPJ -1,26'
       Valor = único número 2-decimal no final da linha
    """
    text  = "\n".join(pages_text)
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    result: list[tuple[str, float | None]] = []
    in_financeiros = False

    for line in lines:
        line_up = _to_ascii_upper(line)

        # Seção 1: itens com (kWh)
        if not in_financeiros:
            if "ITENS FINANCEIROS" in line_up:
                in_financeiros = True
                continue
            if not _G5_UNIT.search(line):
                continue

            unit_match = _G5_UNIT.search(line)
            nome = line[:unit_match.start()].strip()
            rest = line[unit_match.end():]

            fiscal_match = _G5_FISCAL.search(rest)
            if fiscal_match:
                rest = rest[:fiscal_match.start()]

            rest_sem_prices = re.sub(r'-?[\d\.]+,\d{3,}', '', rest)
            vals = re.findall(r'-?[\d\.]+,\d{2}(?!\d)', rest_sem_prices)
            if vals and nome:
                result.append((nome, _to_float_br(vals[-1])))

        # Seção 2: itens financeiros sem unidade (CIP, tributos)
        else:
            # Para quando encontra linha de histórico ou medição
            if any(kw in line_up for kw in ("LEITURA", "MEDIDOR", "CONSUMO", "ANTERIOR",
                                             "PROTOCOLO", "VENCIMENTO", "PERIODO", "GRAFICO")):
                break
            if not re.search(r'\d,\d{2}', line):
                continue
            # Ignora linhas fiscais da coluna direita
            if _G1_FISCAL_SKIP.match(line):
                continue

            # Primeiro valor com 2 casas decimais após o nome
            val_match = re.search(r'(-?[\d\.]+,\d{2})(?!\d)', line)
            if not val_match:
                continue
            nome = line[:val_match.start()].strip()
            nome = re.sub(r'[\d,\.\s]+$', '', nome).strip()
            if nome:
                result.append((nome, _to_float_br(val_match.group(1))))

    return result


# ── GRUPO COPEL ───────────────────────────────────────────────────────────────
# Estrutura: PDF com múltiplas páginas, cada uma terminando em TOTAL.
# Items usam kWh ou UN como unidade.

_COPEL_UNIT = re.compile(r'\b(kWh|UN)\b', re.IGNORECASE)
_COPEL_SKIP = re.compile(
    r'^(HISTORICO DE CONSUMO|CONSUMO FATURADO|\d{7,}\s+CONSUMO kWh|KQH kWh)',
    re.IGNORECASE,
)


def _extrair_copel_pagina(page_text: str) -> list[tuple[str, float | None]]:
    lines  = [l.strip() for l in page_text.splitlines() if l.strip()]
    result: list[tuple[str, float | None]] = []
    for line in lines:
        if re.match(r"^TOTAL\b", _to_ascii_upper(line)):
            break
        if not _COPEL_UNIT.search(line):
            continue
        if _COPEL_SKIP.search(line):
            continue
        nome, valor_str = _parse_linha_com_unidade(line, _COPEL_UNIT)
        if nome and valor_str is not None:
            result.append((nome, _to_float_br(valor_str)))
    return result


def _extrair_copel(pages_text: list[str]) -> list[tuple[str, float | None]]:
    """COPEL — processa página a página, respeitando o TOTAL de cada tabela."""
    result: list[tuple[str, float | None]] = []
    for page in pages_text:
        result.extend(_extrair_copel_pagina(page))
    return result


# ── GRUPO 6: "Itens Financeiros" + "kWh a" → Total a pagar ──────────────────
# Concessionária: RORAIMA ENERGIA
#
# Estrutura: tabela com coluna "Itens Financeiros" e valor no final.
# Linhas de consumo: '<nome> <qtd> kWh a <preço> <preço> <Valor>'
# Linhas de demanda: '<nome> <qtd> kW a <preço> <preço> <Valor>'
# Linhas simples: '<nome> <Valor>'

_G6_UNIT = re.compile(r'\b(kWh|kW)\s+a\b', re.IGNORECASE)


def _extrair_grupo6(pages_text: list[str]) -> list[tuple[str, float | None]]:
    """RORAIMA ENERGIA — seção 'Itens Financeiros' com formato '<nome> qtd kWh a preço valor'."""
    text  = "\n".join(pages_text)
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    result: list[tuple[str, float | None]] = []
    in_section = False

    for line in lines:
        line_up = _to_ascii_upper(line)

        if not in_section:
            if "ITENS FINANCEIROS" in line_up:
                in_section = True
            continue

        if re.match(r"^TOTAL\b", line_up) or "TOTAL A PAGAR" in line_up:
            break

        if not re.search(r'\d,\d{2}', line):
            continue
        if any(kw in line_up for kw in ("GRUPO", "SUBGRUPO", "LIGACAO", "MODALIDADE",
                                         "TAR. SEM", "VALOR (R", "ITENS FINANC")):
            continue

        if _G6_UNIT.search(line):
            # '<nome> <qtd> kWh/kW a <preço_sem_trib> <preço_com_trib> <Valor>'
            unit_match = _G6_UNIT.search(line)
            nome = line[:unit_match.start()].strip()
            # Remove qtd antes da unidade
            nome = re.sub(r'\s+\d[\d\.]*\s*$', '', nome).strip()
            rest = line[unit_match.end():]
            # Pula os dois preços (3+ decimais ou 6+ decimais)
            rest_clean = re.sub(r'\d[\d\.]*,\d{3,}\s*', '', rest).strip()
            val_match = re.search(r'-?[\d\.]+,\d{2}(?!\d)', rest_clean)
            valor_str = val_match.group() if val_match else None
        else:
            # Linha simples: '<nome> <Valor>'
            val_match = re.search(r'(-?[\d\.]+,\d{2})(?!\d)\s*$', line)
            if not val_match:
                continue
            nome      = line[:val_match.start()].strip()
            valor_str = val_match.group(1)

        nome = re.sub(r'[\d,\.\s]+$', '', nome).strip()
        if nome and valor_str:
            result.append((nome, _to_float_br(valor_str)))

    return result


# ── GRUPO 7: kWh compacto + itens "UN 1" → TOTAL A PAGAR ────────────────────
# Concessionária: CEB (Brasília/DF)
#
# Linha kWh: 'CONSUMO KWh 100 0,9686231 96,86 2,56 96,86 12,00 11,62 ...'
# Linhas sem kWh: 'CONTRIBUICAO DE I. PUBLICA 1 1.061,33'
#                 'PIS LEI 10833/03 0,65% -0,62'
# Termina em: 'TOTAL A PAGAR'

_G7_KWH = re.compile(r'\bKWh\b', re.IGNORECASE)


def _extrair_grupo7(pages_text: list[str]) -> list[tuple[str, float | None]]:
    """CEB (Brasília) — layout compacto com CONSUMO KWh e itens simples."""
    text  = "\n".join(pages_text)
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    result: list[tuple[str, float | None]] = []

    for line in lines:
        line_up = _to_ascii_upper(line)

        if "TOTAL A PAGAR" in line_up:
            break

        if not re.search(r'\d,\d{2}', line):
            continue

        # Ignora linhas de leitura/medição/histórico/saldo
        if any(kw in line_up for kw in ("LEITURA", "MEDIDOR", "ANTERIOR", "ATUAL",
                                         "SALDO", "INJETADO", "COMPENSADO", "ABRIR")):
            continue
        # Ignora colunas fiscais laterais "PIS 96,86 0,47 0,45"
        if re.match(r'^(PIS|COFINS|ICMS)\s+[\d\.]', line):
            continue
        # Ignora linhas compactadas sem espaço (duplicatas do layout CEB)
        # ex: "PISLEI10833/030,65%" — palavra sem espaço antes do número
        if re.match(r'^[A-Z]{5,}[\d/]', line.upper()):
            continue

        if _G7_KWH.search(line):
            nome, valor_str = _parse_linha_com_unidade(line, _G7_KWH)
        else:
            # '<nome> [qtd] <valor>'
            val_match = re.search(r'(?:\b\d+\s+)?(-?[\d\.]+,\d{2})(?!\d)\s*$', line)
            if not val_match:
                continue
            nome      = line[:val_match.start()].strip()
            nome      = re.sub(r'\s+\d+\s*$', '', nome).strip()
            valor_str = val_match.group(1)

        nome = re.sub(r'[\d,\.\s%]+$', '', nome).strip()
        if nome and valor_str:
            result.append((nome, _to_float_br(valor_str)))

    return result


# ── GRUPO CHESP: "Itens de fatura" NF3e → TOTAL ───────────────────────────────
# Concessionária: CHESP
#
# Layout próximo de Grupo A MT com mistura de:
# - linhas de energia/demanda com kWh/kW
# - descontos/retenções sem unidade explícita
# - linha final TOTAL encerrando a seção

_CHESP_UNIT = re.compile(r'\b(kWh|kW)\b', re.IGNORECASE)
_CHESP_FISCAL_SKIP = re.compile(r'^(PIS/PASEP|COFINS|ICMS)\b', re.IGNORECASE)


def _extrair_grupo_chesp(pages_text: list[str]) -> list[tuple[str, float | None]]:
    text = "\n".join(pages_text)
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    result: list[tuple[str, float | None]] = []
    in_section = False

    for line in lines:
        line_up = _to_ascii_upper(line)

        if not in_section:
            if "ITENS DE FATURA" in line_up:
                in_section = True
            continue

        if re.match(r"^TOTAL\b", line_up):
            break
        if _CHESP_FISCAL_SKIP.match(line):
            continue
        if any(token in line_up for token in ("BASE CALC.", "ALIQUOTA", "PRECO UNIT", "VALOR (R$)")):
            continue
        if "GRANDEZAS CONTRATADAS" in line_up:
            continue
        if not re.search(r'\d,\d{2}', line):
            continue

        if _CHESP_UNIT.search(line):
            nome, valor_str = _parse_linha_com_unidade(line, _CHESP_UNIT)
        else:
            valores = re.findall(r'-?[\d\.]+,\d{2}(?!\d)', line)
            if not valores:
                continue
            valor_str = next((v for v in valores if v.startswith("-")), valores[0])
            nome = line[:line.find(valor_str)].strip()
            nome = re.sub(r'\s+-?\d+\s*$', '', nome).strip()

        nome = re.sub(r'[\d,\.\s-]+$', '', nome).strip()
        if nome:
            result.append((nome, _to_float_br(valor_str)))

    return result


# ── Registro de extratores ────────────────────────────────────────────────────
# Cada extrator recebe list[str] (uma str por página do PDF).

EXTRATORES: dict[str, callable] = {
    "CEMIG":       _extrair_grupo1,
    "NEOENERGIA":  _extrair_grupo1,
    "ENERGISA":    _extrair_grupo1,
    "ENEL":        _extrair_grupo2,
    "ELETROPAULO": _extrair_grupo2,
    "CELESC":      _extrair_grupo3,
    "CPFL":        _extrair_grupo4,
    "RGE":         _extrair_grupo4,
    "EQUATORIAL":  _extrair_grupo5,
    "LIGHT":       _extrair_grupo2,   # mesmo layout ENEL ("Itens de fatura" + kWh/kW)
    "CEEE":        _extrair_grupo5,   # mesmo layout EQUATORIAL (kWh) + ITENS FINANCEIROS
    "RORAIMA":     _extrair_grupo6,
    "CEB":         _extrair_grupo7,
    "CHESP":       _extrair_grupo_chesp,
    "COPEL":       _extrair_copel,
}


# ── Extração principal ────────────────────────────────────────────────────────

def extrair_dados(pdf_path: Path) -> dict:
    carimbo = pdf_path.stem
    try:
        pages_text: list[str] = []
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                t = page.extract_text(x_tolerance=1, y_tolerance=1) or ""
                pages_text.append(t)
    except Exception as exc:
        log.error("  Erro ao ler %s: %s", pdf_path.name, exc)
        return {
            "carimbo": carimbo,
            "concessionaria": "ERRO",
            "numero_nota": "",
            "serie": "",
            "itens": [],
            "erro": str(exc),
        }

    full_ascii    = _to_ascii_upper("\n".join(pages_text))
    concessionaria = _detectar_concessionaria(full_ascii)
    numero_nota, serie = _extrair_numero_nota_serie(full_ascii)
    extrator      = EXTRATORES.get(concessionaria)

    if extrator is None:
        log.warning("  Sem parser: %-12s  %s", concessionaria, pdf_path.name)
        return {
            "carimbo":       carimbo,
            "concessionaria": concessionaria,
            "numero_nota": numero_nota,
            "serie": serie,
            "itens":         [],
            "erro":          f"Parser não disponível para {concessionaria}",
        }

    itens = extrator(pages_text)
    log.info("  %-32s  %-14s  %d itens", pdf_path.name, concessionaria, len(itens))
    return {
        "carimbo": carimbo,
        "concessionaria": concessionaria,
        "numero_nota": numero_nota,
        "serie": serie,
        "itens": itens,
        "erro": "",
    }


# ── Geração do XLSX ───────────────────────────────────────────────────────────

def gerar_xlsx(dados: list[dict], caminho: Path) -> None:
    rows = []
    for d in dados:
        row: dict = {"Carimbo": d["carimbo"], "Concessionária": d["concessionaria"]}
        row["Numero da Nota"] = d.get("numero_nota", "")
        row["Serie"] = d.get("serie", "")
        for i, (linha, valor) in enumerate(d["itens"], start=1):
            row[f"Linha{i}"] = linha
            row[f"Valor{i}"] = valor
        if d.get("erro"):
            row["ERRO"] = d["erro"]
        rows.append(row)

    df = pd.DataFrame(rows)

    base_cols  = ["Carimbo", "Concessionária"]
    base_cols.extend(["Numero da Nota", "Serie"])
    linha_cols = sorted([c for c in df.columns if re.match(r"^Linha\d+$", c)],
                        key=lambda x: int(re.search(r"\d+", x).group()))
    valor_cols = sorted([c for c in df.columns if re.match(r"^Valor\d+$", c)],
                        key=lambda x: int(re.search(r"\d+", x).group()))
    pares      = [col for lc, vc in zip(linha_cols, valor_cols) for col in (lc, vc)]
    extra_cols = [c for c in df.columns if c not in base_cols + pares]
    ordered    = base_cols + pares + extra_cols
    df         = df[[c for c in ordered if c in df.columns]]

    caminho.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(caminho, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Dados_Financeiros")
        ws = writer.sheets["Dados_Financeiros"]
        for col_cells in ws.columns:
            max_len = max((len(str(c.value or "")) for c in col_cells), default=8)
            ws.column_dimensions[col_cells[0].column_letter].width = min(max_len + 2, 60)

    log.info("XLSX salvo: %s  (%d faturas)", caminho, len(dados))


# ── CLI ───────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="OCR Dados Financeiros — extrai itens e valores das faturas de energia"
    )
    p.add_argument("--pasta", action="append", required=True, dest="pastas", metavar="PASTA")
    p.add_argument("--saida",   default="")
    p.add_argument("--carimbo", action="append", default=[])
    return p.parse_args()


def main() -> int:
    args   = parse_args()
    pastas = [Path(p.strip()) for p in args.pastas]
    saida  = (Path(args.saida.strip()) if args.saida.strip()
              else pastas[0] / "dados_financeiros.xlsx")

    log.info("=" * 60)
    log.info("  OCR Dados Financeiros")
    log.info("=" * 60)
    for pasta in pastas:
        log.info("  Pasta PDFs : %s", pasta)
    log.info("  XLSX saída : %s", saida)

    carimbos_filtro = {c.strip().upper() for c in args.carimbo if c.strip()}
    todos_pdfs: list[Path] = []
    for pasta in pastas:
        if not pasta.exists():
            log.error("Pasta não encontrada: %s", pasta)
            return 1
        pdfs = sorted(pasta.glob("*.pdf"))
        if carimbos_filtro:
            pdfs = [p for p in pdfs if p.stem.upper() in carimbos_filtro]
        todos_pdfs.extend(pdfs)

    if not todos_pdfs:
        log.warning("Nenhum PDF encontrado.")
        return 2

    log.info("  PDFs encontrados: %d", len(todos_pdfs))

    dados:  list[dict] = []
    erros = 0
    for idx, pdf in enumerate(todos_pdfs, start=1):
        log.info("[%d/%d] %s", idx, len(todos_pdfs), pdf.name)
        d = extrair_dados(pdf)
        dados.append(d)
        if d.get("erro"):
            erros += 1

    gerar_xlsx(dados, saida)
    log.info("Concluído: %d PDFs, %d erros.", len(dados), erros)
    return 0 if erros == 0 else 3


if __name__ == "__main__":
    raise SystemExit(main())
