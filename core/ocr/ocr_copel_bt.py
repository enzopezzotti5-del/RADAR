#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
OCR COPEL BT
============

Extrai campos das faturas COPEL BT para alimentar o fluxo de digitacao do
Consen, alinhando o contrato de colunas com os demais OCRs do projeto.
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

try:
    import pdfplumber
except ImportError as exc:  # pragma: no cover
    raise SystemExit("pdfplumber nao instalado. Execute: pip install pdfplumber") from exc


ROOT_DIR = Path("//10.10.250.21/Energia/ARQUIVOS ENZO")
DOWNLOAD_DIR = ROOT_DIR / "DOWNLOAD COPEL"
OCR_DIR = ROOT_DIR / "OCR COPEL"
LOG_DIR = Path(__file__).resolve().parent.parent / "logs"

HEADERS = [
    "Instalacao", "fatDataEmissao", "fatDataVcto", "fatValorFatura", "concCod",
    "fatDataCadastro", "fatDataLeituraAnterior", "fatDataLeituraAtual", "fatIlumPublica",
    "cadTarifaCod", "cadSubGrupoCod", "fatDemContratadaPonta", "fatDemContratadaFPonta",
    "fatDemPontaRegistrada", "fatDemFPontaIndRegistrada", "fatDemFPontaCapRegistrada",
    "fatDemPontaExcFaturada", "fatDemFPontaExcFaturada", "fatDemPontaExcRegistrada",
    "fatDemFPontaExcRegistrada", "fatDemPontaFaturada", "fatDemFPontaIndFaturada",
    "fatDemPontaUltra", "fatDemFPontaIndUltra", "fatConPontaRegistrado",
    "fatConFPontaIndRegistrado", "fatConFPontaCapRegistrado", "fatConIntermediarioRegistrado",
    "fatConPontaFaturado", "fatConFPontaIndFaturado", "fatConFPontaCapFaturado",
    "fatConIntermediarioFaturado", "fatConPontaExcRegistrado", "fatConFPontaIndExcRegistrado",
    "fatConFPontaCapExcRegistrado", "fatConPontaExcFaturado", "fatConFPontaIndExcFaturado",
    "fatConFPontaCapExcFaturado", "fatICMS", "fatPIS", "fatCOFINS", "fatValorNotaFiscal",
    "obsCod_1", "obsValor_1", "obsCod_2", "obsValor_2", "obsCod_3", "obsValor_3",
    "obsCod_4", "obsValor_4", "obsCod_5", "obsValor_5", "CNPJ", "ENDERECO",
    "NOTAFISCAL", "CODIGOCLIENTE", "fatDataReferencia", "fatConPontaInjetadoRegistrado",
    "fatConPontaInjetadoFaturado", "fatConFPontaInjetadoRegistrado",
    "fatConFPontaInjetadoFaturado", "fatCodigoBarras", "Debitos anteriores",
    "fatCarimbo", "usuCod", "fatDemPontaGeracaoRegistrada", "fatDemPontaGeracao",
    "fatDemPontaGeracaoValorReais", "fatDemFPontaGeracaoRegistrada", "fatDemFPontaGeracao",
    "fatDemFPontaGeracaoValorReais", "fatDemContratadaGeracaoPonta",
    "fatDemContratadaGeracaoFPonta", "fatDemPontaValorReais", "fatDemFPontaIndValorReais",
    "fatDemPontaUltraValorReais", "fatDemFPontaIndUltraValorReais", "fatDemPontaExcValorReais",
    "fatDemFPontaExcValorReais", "fatConPontaValorReais", "fatConFPontaIndValorReais",
    "fatConFPontaCapValorReais", "fatConIntermediarioValorReais", "fatConPontaExcValorReais",
    "fatConFPontaIndExcValorReais", "fatConFPontaCapExcValorReais",
    "fatConPontaInjetadoValorReais", "fatConFPontaInjetadoValorReais",
    "fatConPontaInjetadoUsina", "fatConPontaInjetadoUsinaSaldoAcumulado",
    "fatConFPontaInjetadoUsina", "fatConFPontaInjetadoUsinaSaldoAcumulado",
    "fatDemandasDevolucaoPtaValorReais", "fatDemandasDevolucaoFPtaValorReais",
    "fatConIntermedInjetadoRegistrado", "fatConIntermedInjetadoFaturado",
    "fatConIntermedInjetadoValorReais", "fatDescontoFio", "fatDescPisAliquota",
    "fatDescPisPercRetImposto", "fatDescPisValRetImposto", "fatDesCofinsAliquota",
    "fatDescCofinsPercRetImposto", "fatDescCofinsValRetImposto", "fatDesIcmsAliquota",
    "fatDescCsllPercRetImposto", "fatDescCsllValRetImposto", "fatDescIrpjPercRetImposto",
    "fatDescIrpjValRetImposto", "fatDescIrrfPercRetImposto", "fatDescIrrfValRetImposto",
    "fatDescConsumoPercRetImposto", "fatDescConsumoValRetImposto",
    "fatDescDemandaPercRetImposto", "fatDescDemandaValRetImposto", "fatValBandeira",
    "fatValBandeira2", "fatDIC", "fatFIC", "fatMultas", "fatTributoFederalPerc",
    "fatTributoFederalVal", "fatMultasDiversas", "fatDescontoFioKWh",
    "fatConCreditoTUSDPontaValorReais", "fatConCreditoTUSDFPontaValorReais",
    "fatBeneficioTarifarioBrutoValorReais", "fatBeneficioLiquidoValorReais",
    "fatContaCovidValorReais", "fatEscassezHidricaValorReais", "fatContaCovid",
    "fatEscassezHidrica", "TARIFA_DETECTADA", "ARQUIVO", "ERRO",
]

DATE_HEADERS = {
    "fatDataEmissao",
    "fatDataVcto",
    "fatDataCadastro",
    "fatDataLeituraAnterior",
    "fatDataLeituraAtual",
    "fatDataReferencia",
}

TEXT_HEADERS = {
    "Instalacao", "concCod", "cadTarifaCod", "cadSubGrupoCod",
    "obsCod_1", "obsCod_2", "obsCod_3", "obsCod_4", "obsCod_5",
    "CNPJ", "ENDERECO", "NOTAFISCAL", "CODIGOCLIENTE", "fatCodigoBarras",
    "fatCarimbo", "usuCod", "fatConPontaInjetadoUsina",
    "fatConPontaInjetadoUsinaSaldoAcumulado", "fatConFPontaInjetadoUsina",
    "fatConFPontaInjetadoUsinaSaldoAcumulado", "fatContaCovid", "fatEscassezHidrica",
    "TARIFA_DETECTADA", "ARQUIVO", "ERRO",
}

NUMERIC_HEADERS = set(HEADERS) - DATE_HEADERS - TEXT_HEADERS
HEADER_DISPLAY = {
    "Instalacao": "Instalação",
    "Debitos anteriores": "Débitos anteriores",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("ocr_copel_bt")


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


def _parse_header_dates(lines_ascii: list[str]) -> tuple[str, str]:
    for line in lines_ascii:
        match = re.search(r"(\d{2}/\d{2}/\d{4})\s+(\d{2}/\d{2}/\d{4})\s+\d+\s+(\d{2}/\d{2}/\d{4})", line)
        if match:
            return match.group(1), match.group(2)
    return "", ""


def _parse_ref_vcto_valor(text_ascii: str) -> tuple[str, str, float | None]:
    match = re.search(r"(\d{2}/\d{4})\s+(\d{2}/\d{2}/\d{4})\s+R\$\s*([\d\.,]+)", text_ascii)
    if match:
        return match.group(1), match.group(2), _to_float_br(match.group(3))

    match_sem_valor = re.search(r"(\d{2}/\d{4})\s+(\d{2}/\d{2}/\d{4})\s+(?:R\$\s*)?\*{3,}", text_ascii)
    referencia = match_sem_valor.group(1) if match_sem_valor else ""
    vencimento = match_sem_valor.group(2) if match_sem_valor else ""

    fallback_patterns = [
        r"\bTOTAL\s+([\d\.,]+)\s+[\d\.,]+\s+[\d\.,]+",
        r"O VALOR DE R\$\s*([\d\.,]+)",
        r"RETENCAO DE TRIBUTOS FEDERAIS - LEI 10\.833/2003 R\$\s*([\d\.,]+)",
    ]
    for pattern in fallback_patterns:
        alt = re.search(pattern, text_ascii)
        if alt:
            return referencia, vencimento, _to_float_br(alt.group(1))

    return referencia, vencimento, None


def _parse_nota_fiscal(text_ascii: str) -> tuple[str, str]:
    patterns = [
        r"NOTA FISCAL NO\.\s*(\d+)\s*-\s*SERIE\s*\d+\s*/\s*DATA DE EMISSAO:\s*(\d{2}/\d{2}/\d{4})",
        r"NOTA FISCAL NO\.\s*(\d+)\s*-\s*SERIE\s*\d+\s*DATA DE EMISSAO:\s*(\d{2}/\d{2}/\d{4})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text_ascii)
        if match:
            return match.group(1), match.group(2)
    return "", ""


def _parse_cnpj(text_ascii: str) -> str:
    match = re.search(r"CNPJ:\s*([\d\./-]+)", text_ascii)
    return re.sub(r"\D+", "", match.group(1)) if match else ""


def _parse_codigo_cliente(text_ascii: str) -> str:
    patterns = [
        r"FAT-\d+-[\d\.]+\s+(\d{7,12})\s+\d{7,10}\s+R\$",
        r"UC[:\s]+(\d{7,12})",
        r"CODIGO DO CLIENTE[:\s]+(\d{7,12})",
        r"ENDERE\w*:\s+.*?(\d{7,12})\s+CEP:",
        r"NOME:\s+BANCO DO BRASIL.*?ENDERE.{0,80}?(\d{7,12})\s+CEP:",
    ]
    for pattern in patterns:
        match = re.search(pattern, text_ascii, re.S)
        if match:
            return match.group(1)
    return ""


def _normalizar_instalacao(raw: str) -> str:
    """
    Normaliza a instalacao COPEL preservando o padrao ANEEL/OCM.

    - UC antiga: mantém sem zeros à esquerda artificiais.
    - UC nova ANEEL/OCM: completa com zeros à esquerda até 15 dígitos.
    """
    digits = re.sub(r"\D", "", raw or "")
    if not digits:
        return ""
    if 11 <= len(digits) <= 15:
        return digits.zfill(15)
    return digits.lstrip("0") or digits


def _parse_instalacao(lines_ascii: list[str], text_ascii: str) -> str:
    # Formato antigo: número standalone 6-10 dígitos após "NOME:"
    for idx, line in enumerate(lines_ascii):
        if line.startswith("NOME:"):
            for prox in lines_ascii[idx + 1: idx + 5]:
                standalone = re.fullmatch(r"\d{6,15}", prox.strip())
                if standalone:
                    return _normalizar_instalacao(standalone.group(0))

    for idx, line in enumerate(lines_ascii):
        standalone = re.fullmatch(r"\d{6,15}", line.strip())
        if not standalone:
            continue
        prev = lines_ascii[idx - 1] if idx > 0 else ""
        nxt = lines_ascii[idx + 1] if idx + 1 < len(lines_ascii) else ""
        if prev.startswith("NOME:") or nxt.startswith("ENDERECO:") or nxt.startswith("- "):
            return _normalizar_instalacao(standalone.group(0))

    # Novo formato: número ANEEL de 15 dígitos ao final da linha ENDERECO
    for line in lines_ascii:
        if line.startswith("ENDERECO:"):
            # Prefere o número mais longo (ANEEL 15-dig); fallback para 6-10
            nums = re.findall(r"(\d{6,15})", line)
            if nums:
                return _normalizar_instalacao(nums[-1])
    match = re.search(r"FAT-\d+-[\d\.]+\s+\d{6,12}\s+(\d{6,15})\s+R\$", text_ascii)
    if match:
        return _normalizar_instalacao(match.group(1))
    match = re.search(r"ENDERE\w*:\s+.*?(\d{6,15})\s+CEP:", text_ascii, re.S)
    if match:
        return _normalizar_instalacao(match.group(1))
    match = re.search(r"NOME:\s+BANCO DO BRASIL.*?ENDERE.{0,80}?(\d{6,15})\s+CEP:", text_ascii, re.S)
    if match:
        return _normalizar_instalacao(match.group(1))
    match = re.search(r"\bINSTALACAO\b.*?(\d{6,15})", text_ascii)
    if match:
        return _normalizar_instalacao(match.group(1))
    return ""


def _parse_endereco(lines_original: list[str], instalacao: str) -> str:
    for idx, line in enumerate(lines_original):
        line_ascii = _to_ascii_upper(line)
        if line_ascii.startswith("ENDERECO:"):
            parts = [line.split(":", 1)[1].strip()]
            j = idx + 1
            while j < len(lines_original):
                nxt = lines_original[j]
                nxt_ascii = _to_ascii_upper(nxt)
                if nxt_ascii.startswith("CEP:") or nxt_ascii.startswith("CIDADE:") or nxt_ascii.startswith("CNPJ:") or nxt_ascii.startswith("I.E.:"):
                    break
                parts.append(nxt.strip())
                j += 1
            endereco = " ".join(p for p in parts if p).strip()
            # Limpa o número da instalação que a COPEL costuma grudar no final do CEP
            if instalacao and endereco.endswith(instalacao):
                endereco = endereco[:-len(instalacao)].strip()
            return endereco
    return ""


def _parse_codigo_barras(text_ascii: str) -> str:
    patterns = [
        r"(\d{5}\.\d{5}\s+\d{5}\.\d{6}\s+\d{5}\.\d{6}\s+\d\s+\d{14})",
        r"(\d{5}\.?\d{5}\s+\d{5}\.?\d{6}\s+\d{5}\.?\d{6}\s+\d\s+\d{14})",
        r"((?:237|001)\d{2}\.?\d{5}\s*\d{5}\.?\d{6}\s*\d{5}\.?\d{6}\s*\d\s*\d{14})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text_ascii)
        if match:
            return re.sub(r"\s+", " ", match.group(1)).strip()
    return ""


def _parse_tarifa_subgrupo(text_ascii: str, lines_ascii: list[str]) -> tuple[str, str, str]:
    tarifa = ""
    subgrupo = ""
    if "CONVENCIONAL" in text_ascii:
        tarifa = "Convencional"
    elif "HORO-SAZONAL VERDE" in text_ascii or "HS VERDE" in text_ascii:
        tarifa = "HS - Verde"
    elif "HORO-SAZONAL AZUL" in text_ascii or "HS AZUL" in text_ascii:
        tarifa = "HS - Azul"

    for line in lines_ascii:
        if line.startswith("B3") or " B3 " in f" {line} ":
            subgrupo = "B3 [<2,3kV]"
            break
        if line.startswith("A4") or " A4 " in f" {line} ":
            subgrupo = "A4 [2,3 a 25 kV]"
            break

    if not subgrupo and re.search(r"\bB3\b", text_ascii):
        subgrupo = "B3 [<2,3kV]"
    if not tarifa and subgrupo.startswith("B3"):
        tarifa = "Convencional"

    return tarifa, subgrupo, tarifa.upper() if tarifa else ""


def _parse_tax_line(
    lines_ascii: list[str],
    text_ascii: str,
    nome: str,
) -> tuple[float | None, float | None, float | None]:
    patterns = [
        re.compile(rf"\b{nome}\b\s+(-?[\d\.,]+)\s+(-?[\d\.,]+)%?\s+(-?[\d\.,]+-?)", re.IGNORECASE),
        re.compile(rf"\b{nome}\b\s+(-?[\d\.,]+)%?\s+(-?[\d\.,]+-?)", re.IGNORECASE),
        re.compile(rf"\b{nome}\b.*?(-?[\d\.,]+)%", re.IGNORECASE),
    ]
    candidatos: list[tuple[float | None, float | None, float | None]] = []

    chunks = list(lines_ascii)
    for chunk in chunks:
        if "IMP.RET." in chunk or "EST.IMP.RET." in chunk or "ENERGIA INJ." in chunk:
            continue
        for idx, pattern in enumerate(patterns):
            for match in pattern.finditer(chunk):
                if idx == 0:
                    base = _to_float_br(match.group(1))
                    aliquota = _to_float_br(match.group(2))
                    valor = _to_float_br(match.group(3))
                    if valor is not None and valor <= 0:
                        continue
                    candidatos.append((base, aliquota, valor))
                elif idx == 1:
                    aliquota = _to_float_br(match.group(1))
                    valor = _to_float_br(match.group(2))
                    if valor is not None and valor <= 0:
                        continue
                    candidatos.append((None, aliquota, valor))
                else:
                    candidatos.append((None, _to_float_br(match.group(1)), None))

    if not candidatos:
        for idx, pattern in enumerate(patterns):
            for match in pattern.finditer(text_ascii):
                if idx == 0:
                    base = _to_float_br(match.group(1))
                    aliquota = _to_float_br(match.group(2))
                    valor = _to_float_br(match.group(3))
                    if valor is not None and valor > 0:
                        candidatos.append((base, aliquota, valor))
                elif idx == 1:
                    aliquota = _to_float_br(match.group(1))
                    valor = _to_float_br(match.group(2))
                    if valor is not None and valor > 0:
                        candidatos.append((None, aliquota, valor))
                else:
                    candidatos.append((None, _to_float_br(match.group(1)), None))

    if not candidatos:
        return None, None, None

    unicos: list[tuple[float | None, float | None, float | None]] = []
    vistos: set[tuple[float | None, float | None, float | None]] = set()
    for candidato in candidatos:
        chave = tuple(None if v is None else round(v, 4) for v in candidato)
        if chave in vistos:
            continue
        vistos.add(chave)
        unicos.append(candidato)

    completos = [c for c in unicos if c[2] is not None]
    base = completos or [c for c in unicos if c[1] is not None] or unicos
    return max(
        base,
        key=lambda item: (
            item[0] is not None,
            item[2] is not None,
            abs(item[2] or 0.0),
            abs(item[1] or 0.0),
            abs(item[0] or 0.0),
        ),
    )


def _parse_ilum_publica(lines_ascii: list[str]) -> float | None:
    for line in lines_ascii:
        if "CONT ILUMIN PUBLICA MUNICIPIO" not in line:
            continue
        match = re.search(r"CONT ILUMIN PUBLICA MUNICIPIO\s+(-?[\d\.,]+)(?:\s|$)", line)
        if match:
            return _to_float_br(match.group(1))
        match = re.search(r"CONT ILUMIN PUBLICA MUNICIPIO\s+\w+\s+([\d\.,]+)\s+([\d\.,]+)", line)
        if match:
            return _to_float_br(match.group(2))
        tail = line.split("CONT ILUMIN PUBLICA MUNICIPIO", 1)[1]
        nums = re.findall(r"(-?[\d\.,]+)", tail)
        if len(nums) >= 2:
            return _to_float_br(nums[1])
        if nums:
            return _to_float_br(nums[-1])
    return None


def _parse_component_sum(lines_ascii: list[str], prefixes: tuple[str, ...]) -> tuple[float, float, float]:
    qty_seen: set[tuple[str, float]] = set()
    value_seen: set[tuple[str, str, float]] = set()
    total_qty = 0.0
    total_val = 0.0

    for line in lines_ascii:
        if not any(line.startswith(prefix) for prefix in prefixes):
            continue
        match = re.search(
            r"(TE|TUSD)?\s*(\d{2}/\d{4})\s+.*?KWH\s+(-?[\d\.,]+)\s+[\d\.,]+\s+(-?[\d\.,]+)",
            line,
        )
        if not match:
            continue

        tipo = (match.group(1) or "GEN").upper()
        mes = match.group(2)
        qty = abs(_to_float_br(match.group(3)) or 0.0)
        val = abs(_to_float_br(match.group(4)) or 0.0)

        if qty:
            qty_key = (mes, qty)
            if qty_key not in qty_seen:
                qty_seen.add(qty_key)
                total_qty += qty

        if val:
            val_key = (mes, tipo, val)
            if val_key not in value_seen:
                value_seen.add(val_key)
                total_val += val

    return total_qty, total_qty, total_val


def _parse_consumo(lines_ascii: list[str], icms_base: float | None) -> tuple[float | None, float | None, float | None]:
    consumo_seen: set[tuple[float, float]] = set()
    uso_sistema_seen: set[tuple[float, float]] = set()
    consumo_total = 0.0
    uso_sistema_total = 0.0

    for line in lines_ascii:
        if "ENERGIA ELET CONSUMO" in line:
            match = re.search(
                r"ENERGIA ELET CONSUMO\s+KWH\s+([\d\.,]+)\s+[\d\.,]+\s+([\d\.,]+)",
                line,
            )
            if not match:
                continue
            qty = _to_float_br(match.group(1)) or 0.0
            val = _to_float_br(match.group(2)) or 0.0
            key = (qty, val)
            if key in consumo_seen:
                continue
            consumo_seen.add(key)
            consumo_total += val
            continue

        if "ENERGIA ELET USO SISTEMA" not in line:
            continue
        match = re.search(
            r"ENERGIA ELET USO SISTEMA\s+KWH\s+([\d\.,]+)\s+[\d\.,]+\s+([\d\.,]+)",
            line,
        )
        if not match:
            continue
        qty = _to_float_br(match.group(1)) or 0.0
        val = _to_float_br(match.group(2)) or 0.0
        key = (qty, val)
        if key in uso_sistema_seen:
            continue
        uso_sistema_seen.add(key)
        uso_sistema_total += val

    valor_total = consumo_total + uso_sistema_total

    for line in lines_ascii:
        match = re.search(r"\bCONSUMO\s+KWH\s+TP\s+\d+\s+\d+\s+\d+\s+(\d+)\b", line)
        if match:
            qtd = _to_float_br(match.group(1))
            return qtd, qtd, (valor_total or icms_base)
    reg, fat, _ = _parse_component_sum(lines_ascii, ("CONSUMO", "ENERGIA ATIVA", "ENERG ELETRICA"))
    return (reg or None, fat or None, (valor_total or icms_base))


def _parse_injetado(lines_ascii: list[str]) -> tuple[float, float, float]:
    return _parse_component_sum(lines_ascii, ("ENERGIA INJ.", "ENERGIA INJETADA"))


def _parse_scee_saldos(text_ascii: str) -> tuple[float, float]:
    ponta = 0.0
    fponta = 0.0
    match_p = re.search(r"SALDO\s+ACUMULADO\s+PONTA\s+(-?[\d\.,]+)", text_ascii)
    if match_p:
        ponta = _to_float_br(match_p.group(1)) or 0.0
    match_fp = re.search(r"SALDO\s+ACUMULADO\s+F\s*PONTA\s+(-?[\d\.,]+)", text_ascii)
    if match_fp:
        fponta = _to_float_br(match_fp.group(1)) or 0.0
    return ponta, fponta


def _parse_retencoes(lines_ascii: list[str], text_ascii: str) -> dict[str, tuple[float | None, float]]:
    result = {
        "PIS": (None, 0.0),
        "COFINS": (None, 0.0),
        "CSLL": (None, 0.0),
        "IRPJ": (None, 0.0),
        "IRRF": (None, 0.0),
        "CONSUMO": (None, 0.0),
        "DEMANDA": (None, 0.0),
    }
    seen: set[tuple[str, str, float | None, float]] = set()
    default_perc = {
        "PIS": 0.65,
        "COFINS": 3.0,
        "CSLL": 1.0,
    }

    def _accumulate(kind: str, tax: str, perc: float | None, value: float) -> None:
        tax = "PIS" if tax == "PIS/PASEP" else tax
        if perc is None:
            perc = default_perc.get(tax)
        key = (kind, tax, perc, abs(value))
        if key in seen:
            return
        seen.add(key)
        prev_perc, prev_val = result[tax]
        result[tax] = (perc if perc is not None else prev_perc, prev_val + value)

    token = r"(EST\.?\s*)?IMP\.?\s*RET\.?"
    tax = r"(PIS|COFINS|CSLL|IRPJ|IRRF|CONSUMO|DEMANDA)"
    line_patterns = [
        re.compile(
            rf"{token}\s*{tax}\b.*?\(?([\d\.,]+)%\)?\s+(?:UN\s+)?([\d\.,-]+)",
            re.IGNORECASE,
        ),
        re.compile(
            rf"{token}\s*{tax}\b.*?\(?([\d\.,]+)%\)?.*?(-?[\d\.,]+-?)\s*$",
            re.IGNORECASE,
        ),
    ]

    for raw_line in lines_ascii:
        line = " ".join(raw_line.split())
        matched = False
        if "IMP" in line and "RET" in line:
            for pattern in line_patterns:
                match = pattern.search(line)
                if not match:
                    continue
                kind = "EST" if match.group(1) else "IMP"
                tax = match.group(2).upper()
                perc = _to_float_br(match.group(3))
                raw_val = _to_float_br(match.group(4)) or 0.0
                signed_val = abs(raw_val) if kind == "EST" else -abs(raw_val)
                _accumulate(kind, tax, perc, signed_val)
                matched = True
                break
        if matched:
            continue

        match_imposto_retido = re.search(
            r"IMPOSTO\s+RETIDO\s*-\s*(PIS/PASEP|PIS|COFINS|CSLL|IRPJ|IRRF)\s+(-?[\d\.,]+-?)",
            line,
            re.IGNORECASE,
        )
        if match_imposto_retido:
            tax = match_imposto_retido.group(1).upper()
            raw_val = _to_float_br(match_imposto_retido.group(2)) or 0.0
            _accumulate("IMP", tax, None, -abs(raw_val))

    if not seen:
        compact = text_ascii.replace("\n", " ")
        pattern = re.compile(
            rf"{token}\s*{tax}\b.*?\(?([\d\.,]+)%\)?.*?(-?[\d\.,]+-?)",
            re.IGNORECASE,
        )
        for match in pattern.finditer(compact):
            kind = "EST" if match.group(1) else "IMP"
            tax = match.group(2).upper()
            perc = _to_float_br(match.group(3))
            raw_val = _to_float_br(match.group(4)) or 0.0
            signed_val = abs(raw_val) if kind == "EST" else -abs(raw_val)
            _accumulate(kind, tax, perc, signed_val)

        if not seen:
            pattern_imposto_retido = re.compile(
                r"IMPOSTO\s+RETIDO\s*-\s*(PIS/PASEP|PIS|COFINS|CSLL|IRPJ|IRRF)\s+(-?[\d\.,]+-?)",
                re.IGNORECASE,
            )
            for match in pattern_imposto_retido.finditer(compact):
                tax = match.group(1).upper()
                raw_val = _to_float_br(match.group(2)) or 0.0
                _accumulate("IMP", tax, None, -abs(raw_val))

    result = {
        nome: (perc, round(valor, 2))
        for nome, (perc, valor) in result.items()
    }
    return result


def _parse_observacoes_copel(lines_ascii: list[str]) -> list[tuple[str, float]]:
    """Extrai pares (código_obs, valor_R$) das linhas de observação COPEL.
    Padrões reconhecidos:
      'IMP.RET. PIS ...'       → código derivado do tipo
      'DEVOLUCAO ...'
      'CREDITO ...'
      Linhas com código numérico explícito: 'OBS 12: ...' ou similar.
    Retorna lista de até 5 pares, excluindo retenções (tratadas à parte).
    """
    # Prefixos que NÃO são obs (já tratados como tributos ou retenções)
    EXCLUIR = (
        "ENERGIA ELET", "CONT ILUMIN", "IMP.RET.", "EST.IMP.RET.",
        "ICMS", "PIS", "COFINS", "NOTA FISCAL", "ADICIONAL BAND",
        "ENCARGO BAND",
    )
    pares: list[tuple[str, float]] = []
    # Linhas que indicam ajuste/observação COPEL: devolução, crédito, desconto, multa
    OBS_PATTERNS = [
        r"DEVOLUCAO\s+([\d\.]+,\d+)",
        r"CREDITO\s+(?:DE\s+)?[\w\s]+\s+([\d\.]+,\d+)",
        r"DESCONTO\s+[\w\s]+\s+([\d\.]+,\d+)",
        r"MULTA\s+[\w\s]+\s+([\d\.]+,\d+)",
        r"JUROS\s+[\w\s]+\s+([\d\.]+,\d+)",
        r"OUTROS?\s+DEBITOS?\s+([\d\.]+,\d+)",
        r"OUTROS?\s+CREDITOS?\s+([\d\.]+,\d+)",
    ]
    OBS_LABELS = {
        "DEVOLUCAO": "7",
        "CREDITO": "6",
        "DESCONTO": "6",
        "MULTA": "8",
        "JUROS": "8",
        "OUTROS DEBITOS": "1",
        "OUTROS CREDITOS": "6",
    }
    for line in lines_ascii:
        if any(line.startswith(exc) for exc in EXCLUIR):
            continue
        for pat in OBS_PATTERNS:
            m = re.search(pat, line, re.I)
            if m:
                v = _to_float_br(m.group(1))
                if v and abs(v) > 0:
                    # Determina código obs pelo prefixo do label
                    cod = "1"
                    for lbl, c in OBS_LABELS.items():
                        if lbl in line:
                            cod = c
                            break
                    pares.append((cod, v))
                    break
        if len(pares) >= 5:
            break
    return pares


def _parse_bandeira(lines_ascii: list[str], text_ascii: str) -> tuple[float, float]:
    """Extrai fatValBandeira (consumo) e fatValBandeira2 (crédito injeção GD, negativo).

    Formatos suportados:
      Legado:  'ADICIONAL BANDEIRA AMARELA XX/XXXX KWH xxx x,xxxxx xx,xx' → nums[-1]
      GD cons: 'ENERGIA CONS. B.AMARELA kWh qty rate_TE R$_TE col4 col5 rate_TUSD' → nums[2]
      GD inj:  'ENERGIA INJ. BAND. AMARELA TE kWh -qty rate_TE -R$_TE ...' → -nums[2] → b2
    """
    b1 = 0.0
    b2 = 0.0
    inj_credit = 0.0
    count_legado = 0

    PATS_LEGADO = [
        "ADICIONAL BANDEIRA",
        "ADICIONAL BAND",
        "ENCARGO BANDEIRA",
        "ADICIONAL DE BANDEIRA",
    ]

    for line in lines_ascii:
        nums = re.findall(r"[\d\.]+,\d+", line)
        if not nums:
            continue

        if any(p in line for p in PATS_LEGADO):
            # Formato legado: valor R$ é o último número da linha
            v = abs(_to_float_br(nums[-1]) or 0.0)
            if v > 2000 and len(nums) >= 2:
                v = abs(_to_float_br(nums[-2]) or 0.0)
            if v > 0:
                if count_legado == 0:
                    b1 = v
                else:
                    b2 += v
                count_legado += 1

        elif "ENERGIA CONS. B." in line:
            # COPEL GD: ENERGIA CONS. B.AMARELA kWh qty rate_TE R$_TE ICMS R$_TUSD rate_TUSD
            # Total bandeira = R$_TE (idx 2) + R$_TUSD (idx 4)
            if len(nums) >= 3:
                v = abs(_to_float_br(nums[2]) or 0.0)
                if len(nums) >= 5:
                    v5 = abs(_to_float_br(nums[4]) or 0.0)
                    if 0.01 < v5 < v * 3:
                        v = round(v + v5, 2)
                if 0.5 < v < 10_000:
                    b1 = v

        elif "ENERGIA INJ. BAND." in line:
            # COPEL GD: crédito de bandeira por injeção → valor negativo em fatValBandeira2
            # Total crédito = R$_TE (idx 2) + R$_TUSD (idx 4)
            if len(nums) >= 3:
                v = abs(_to_float_br(nums[2]) or 0.0)
                if len(nums) >= 5:
                    v5 = abs(_to_float_br(nums[4]) or 0.0)
                    if 0.01 < v5 < v * 3:
                        v = round(v + v5, 2)
                if 0.5 < v < 10_000:
                    inj_credit += v

    if inj_credit > 0:
        b2 = -inj_credit

    return round(b1, 2), round(b2, 2)


def _parse_debitos_anteriores(text_ascii: str) -> float | None:
    match = re.search(r"DEBITOS ANTERIORES\s+R\$\s*([\d\.,-]+)", text_ascii)
    return _to_float_br(match.group(1)) if match else None


def _guess_valor_nota_fiscal(text_ascii: str, valor_fatura: float | None, icms_base: float | None) -> float | None:
    patterns = [
        r"VALOR DA NOTA FISCAL\s+R\$\s*([\d\.,]+)",
        r"VALOR N\. FISCAL\s+([\d\.,]+)",
        r"BASE DE C[AÁ]LCULO DO ICMS\s+([\d\.,]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text_ascii)
        if match:
            return _to_float_br(match.group(1))
    return icms_base if icms_base else valor_fatura


def _build_record(pdf_path: Path) -> dict:
    row = {header: ("" if header in TEXT_HEADERS else None) for header in HEADERS}
    row["ARQUIVO"] = str(pdf_path)
    row["fatCarimbo"] = _carimbo_from_path(pdf_path)
    row["ERRO"] = ""
    row["concCod"] = "COPEL"

    try:
        lines_original, lines_ascii, text_original, text_ascii = _extract_pages(pdf_path)

        leitura_ant, leitura_atual = _parse_header_dates(lines_ascii)
        referencia, vencimento, valor_fatura = _parse_ref_vcto_valor(text_ascii)
        nota_fiscal, data_emissao = _parse_nota_fiscal(text_ascii)
        cnpj = _parse_cnpj(text_ascii)
        codigo_cliente = _parse_codigo_cliente(text_ascii)
        instalacao = _parse_instalacao(lines_ascii, text_ascii)
        endereco = _parse_endereco(lines_original, instalacao)
        codigo_barras = _parse_codigo_barras(text_ascii)
        tarifa, subgrupo, tarifa_detectada = _parse_tarifa_subgrupo(text_ascii, lines_ascii)
        icms_base, icms_aliquota, icms_valor = _parse_tax_line(lines_ascii, text_ascii, "ICMS")
        _, pis_aliquota, pis_valor = _parse_tax_line(lines_ascii, text_ascii, "PIS(?:/PASEP)?")
        _, cofins_aliquota, cofins_valor = _parse_tax_line(lines_ascii, text_ascii, "COFINS")
        ilum = _parse_ilum_publica(lines_ascii)
        consumo_reg, consumo_fat, consumo_val = _parse_consumo(lines_ascii, icms_base)
        injetado_reg, injetado_fat, injetado_val = _parse_injetado(lines_ascii)
        saldo_ponta, saldo_fponta = _parse_scee_saldos(text_ascii)
        ret = _parse_retencoes(lines_ascii, text_ascii)
        debitos_anteriores = _parse_debitos_anteriores(text_ascii)
        valor_nota = _guess_valor_nota_fiscal(text_ascii, valor_fatura, icms_base)
        bandeira1, bandeira2 = _parse_bandeira(lines_ascii, text_ascii)
        obs_pares = _parse_observacoes_copel(lines_ascii)

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
                "cadTarifaCod": tarifa or "",
                "cadSubGrupoCod": subgrupo or "",
                "fatICMS": icms_valor,
                "fatPIS": pis_valor,
                "fatCOFINS": cofins_valor,
                "fatValorNotaFiscal": valor_nota,
                "CNPJ": cnpj or "",
                "ENDERECO": endereco or "",
                "NOTAFISCAL": nota_fiscal or "",
                "CODIGOCLIENTE": codigo_cliente or "",
                "fatDataReferencia": referencia or "",
                "fatCodigoBarras": codigo_barras or "",
                "Debitos anteriores": debitos_anteriores,
                "usuCod": "Enzo",
                "fatDesIcmsAliquota": icms_aliquota,
                "fatDescPisAliquota": pis_aliquota,
                "fatDesCofinsAliquota": cofins_aliquota,
                # COPEL B3: percentuais PIS=0,65% e COFINS=3,00% são fixos por lei;
                # mesmo que o PDF não mostre a linha explicitamente, cadastramos o percentual.
                "fatDescPisPercRetImposto": ret["PIS"][0] if ret["PIS"][0] is not None else 0.65,
                "fatDescPisValRetImposto": ret["PIS"][1],
                "fatDescCofinsPercRetImposto": ret["COFINS"][0] if ret["COFINS"][0] is not None else 3.0,
                "fatDescCofinsValRetImposto": ret["COFINS"][1],
                "fatDescCsllPercRetImposto": ret["CSLL"][0],
                "fatDescCsllValRetImposto": ret["CSLL"][1],
                "fatDescIrpjPercRetImposto": ret["IRPJ"][0],
                "fatDescIrpjValRetImposto": ret["IRPJ"][1],
                "fatDescIrrfPercRetImposto": ret["IRRF"][0],
                "fatDescIrrfValRetImposto": ret["IRRF"][1],
                "fatDescConsumoPercRetImposto": ret["CONSUMO"][0],
                "fatDescConsumoValRetImposto": ret["CONSUMO"][1],
                "fatDescDemandaPercRetImposto": ret["DEMANDA"][0],
                "fatDescDemandaValRetImposto": ret["DEMANDA"][1],
                "fatConFPontaIndRegistrado": consumo_reg,
                "fatConFPontaIndFaturado": consumo_fat,
                "fatConFPontaIndValorReais": consumo_val,
                "fatConFPontaInjetadoRegistrado": injetado_reg,
                "fatConFPontaInjetadoFaturado": injetado_fat,
                "fatConFPontaInjetadoValorReais": injetado_val,
                "fatValBandeira": bandeira1 if bandeira1 > 0 else None,
                "fatValBandeira2": bandeira2 if bandeira2 != 0 else None,
                **{f"obsCod_{i+1}": cod for i, (cod, _) in enumerate(obs_pares)},
                **{f"obsValor_{i+1}": val for i, (_, val) in enumerate(obs_pares)},
                "fatConPontaInjetadoUsina": 0.0,
                "fatConPontaInjetadoUsinaSaldoAcumulado": saldo_ponta,
                "fatConFPontaInjetadoUsina": injetado_reg,
                "fatConFPontaInjetadoUsinaSaldoAcumulado": saldo_fponta,
                "TARIFA_DETECTADA": tarifa_detectada,
            }
        )

        missing = [field for field in ("Instalacao", "fatDataLeituraAnterior", "fatDataLeituraAtual", "fatDataVcto") if not row.get(field)]
        if missing:
            row["ERRO"] = f"campos_criticos_ausentes: {', '.join(missing)}"

    except Exception as exc:
        row["ERRO"] = f"{type(exc).__name__}: {exc}"

    return row


def _default_pasta(mes: str, ano: str) -> Path:
    return DOWNLOAD_DIR / f"{mes}.{ano}" / "BT"


def _output_xlsx(mes: str, ano: str) -> Path:
    return OCR_DIR / f"ocr_copel_BT_{mes}{ano}.xlsx"


def parse_args() -> argparse.Namespace:
    hoje = dt.date.today()
    parser = argparse.ArgumentParser(description="OCR COPEL BT")
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
    _mkdir_seguro(LOG_DIR)
    _mkdir_seguro(OCR_DIR)
    _mkdir_seguro(out.parent)

    log.info("=" * 64)
    log.info("OCR COPEL BT")
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
