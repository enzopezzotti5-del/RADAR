#!/usr/bin/env python3
from __future__ import annotations

import re
import sys
from pathlib import Path

import pdfplumber

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from core.ocr.ocr_bt_cemig_adapter import main_bt_generico
from core.ocr import ocr_bt_generico as _gen

_br2f = _gen._br2f

# CNPJ Equatorial Amapá Distribuição de Energia S.A.
_CNPJ_AP = "05965546000109"


def _extract_uc_ap(txt: str) -> str:
    """UC: linha 'NNN.NNN.NNN-NN https://...' (antes da chave NF-e)."""
    m = re.search(r"^(\d{3}\.\d{3}\.\d{3}-\d{2})\s+https?://", txt, re.MULTILINE)
    if m:
        return m.group(1)
    # fallback: linha do boleto 'EQUATORIAL AMAPA DISTRIB ... NNN.NNN.NNN-NN'
    m = re.search(r"EQUATORIAL[^\n]+DISTRIB[^\n]+\s(\d{3}\.\d{3}\.\d{3}-\d{2})\b", txt, re.I)
    return m.group(1) if m else ""


def _extract_mes_ref_ap(txt: str):
    """Prioriza linha 'MM/AAAA DD/MM/AAAA R$' para nao confundir com historico."""
    import datetime as dt
    m = re.search(r"\b(0[1-9]|1[0-2])/(20\d{2})\s+\d{2}[/.]?\d{2}[/.]?\d{4}\s+R\$", txt)
    if m:
        return dt.date(int(m.group(2)), int(m.group(1)), 1)
    return _gen._extract_mes_ref(txt)


def _extract_vencimento_ap(txt: str) -> str:
    """Vencimento: segunda data na linha 'MM/AAAA DD/MM/AAAA R$ ...'."""
    m = re.search(r"\b(?:0[1-9]|1[0-2])/20\d{2}\s+(\d{2}[/.]\d{2}[/.]\d{4})\s+R\$", txt)
    if m:
        d = _gen._to_date(m.group(1))
        if d:
            return d.strftime("%d/%m/%Y")
    vcto = _gen._extract_vencimento(txt)
    return vcto.strftime("%d/%m/%Y") if vcto else ""


def _extract_total_fatura_ap(txt: str) -> float:
    """Valor total na linha 'MM/AAAA DD/MM/AAAA R$ X.XXX,XX'."""
    m = re.search(
        r"\b(?:0[1-9]|1[0-2])/20\d{2}\s+\d{2}[/.]\d{2}[/.]\d{4}\s+R\$\s*([\d.]+,\d{2})", txt
    )
    if m:
        return _br2f(m.group(1))
    return 0.0


def _extract_consumo_registrado_ap(txt: str) -> float:
    """Consumo lido no medidor: 'Consumo ATIVO TOTAL ... N kWh'."""
    m = re.search(
        r"Consumo\s+ATIVO\s+TOTAL\s+[\d.,]+\s+[\d.,]+\s+[\d.,]+\s+([\d.,]+)\s+kWh",
        txt, re.I,
    )
    if m:
        v = _br2f(m.group(1))
        if 1 <= v <= 999_999:
            return v
    return _gen._extract_consumo(txt)


def _extract_consumo_faturado_ap(txt: str) -> float:
    """Consumo faturado (linha Consumo kWh) ou custo de disponibilidade."""
    for line in txt.splitlines():
        if re.search(r"^Consumo\s+\(kWh\)", line.strip(), re.I):
            nums = re.findall(r"[\d.,]+", line)
            if nums:
                v = _br2f(nums[0])
                if 1 <= v <= 999_999:
                    return v
    for line in txt.splitlines():
        if re.search(r"Custo\s+de\s+disponibilidade\s+\(kWh\)", line, re.I):
            nums = re.findall(r"[\d.,]+", line)
            if nums:
                v = _br2f(nums[0])
                if 1 <= v <= 999_999:
                    return v
    return 0.0


def _extract_consumo_valor_ap(txt: str) -> float:
    """Valor R$ da linha de consumo/disponibilidade (parte antes do bloco PIS)."""
    for line in txt.splitlines():
        if re.search(r"(?:Consumo\s+\(kWh\)|Custo\s+de\s+disponibilidade\s+\(kWh\))", line, re.I):
            clean = re.split(r"\s+PIS\b", line, flags=re.I)[0]
            nums = re.findall(r"[\d.,]+", clean)
            if nums:
                v = _br2f(nums[-1])
                if v > 0:
                    return v
    return 0.0


def _extract_bandeira_ap(txt: str) -> float:
    """Valor R$ da bandeira — trunca antes de COFINS/PIS (mesclados na mesma linha pelo PDF)."""
    for line in txt.splitlines():
        if re.search(r"Adicional\s+Bandeira", line, re.I):
            clean = re.split(r"\s+(?:COFINS|PIS|ICMS)\b", line, flags=re.I)[0]
            nums = re.findall(r"[\d.]+,\d{2}", clean)
            if not nums:
                nums = re.findall(r"\d+,\d{2}", clean)
            if nums:
                v = _br2f(nums[-1])
                if v > 0:
                    return v
    return 0.0


def _extract_barcode_ap(txt: str) -> str:
    """Boleto BB: XXXXX.XXXXX XXXXX.XXXXXX XXXXX.XXXXXX X XXXXXXXXXXXXXX.

    Extrai o boleto antes do fallback generico para nao capturar a chave NF-e (44 digitos).
    """
    m = re.search(
        r"(\d{5}\.\d{5})\s+(\d{5}\.\d{6})\s+(\d{5}\.\d{6})\s+(\d)\s+(\d{14})",
        txt,
    )
    if m:
        return re.sub(r"\D", "", "".join(m.groups()))
    return _gen._extract_barcode(txt)


def _extract_injetado_ap(txt: str) -> tuple[float, float]:
    """(kWh_compensado, valor_R$) de 'Consumo Compensado (kWh)'."""
    for line in txt.splitlines():
        if re.search(r"Consumo\s+Compensado\s+\(kWh\)", line, re.I):
            clean = re.split(r"\s+(?:COFINS|PIS|ICMS)\b", line, flags=re.I)[0]
            nums = re.findall(r"[\d.,]+", clean)
            if len(nums) >= 2:
                kwh = _br2f(nums[0])
                val = _br2f(nums[-1])
                if kwh > 0 and val > 0:
                    return kwh, val
    return 0.0, 0.0


def _extract_retencoes_ap(txt: str) -> dict:
    """'Tributo a Reter IRPJ/CSLL/PIS/COFINS  -XX,XX' — valores individuais."""
    result: dict = {}
    for campo_val, cod in [
        ("fatDescIrpjValRetImposto",   "IRPJ"),
        ("fatDescCsllValRetImposto",   "CSLL"),
        ("fatDescPisValRetImposto",    "PIS"),
        ("fatDescCofinsValRetImposto", "COFINS"),
    ]:
        m = re.search(r"Tributo\s+a\s+Reter\s+" + cod + r"\s+-?\s*([\d.,]+)", txt, re.I)
        if m:
            v = _br2f(m.group(1))
            if v > 0:
                result[campo_val] = -v
    return result


def _extract_multas_ap(txt: str) -> float:
    """Multa + Correcao Monetaria + Juros de atraso."""
    total = 0.0
    for pattern in (
        r"^Multa\s+([\d.,]+)",
        r"^Corre[cç][aã]o\s+Monet[aá]ria\s+([\d.,]+)",
        r"^Juros\s+([\d.,]+)",
    ):
        m = re.search(pattern, txt, re.I | re.MULTILINE)
        if m:
            total += _br2f(m.group(1))
    return total


def processar_pdf_ap(pdf_path: Path) -> dict:
    """Parser Equatorial AP BT: base generica + correcoes especificas Amapa."""
    with pdfplumber.open(str(pdf_path)) as pdf:
        txt = "\n".join(p.extract_text() or "" for p in pdf.pages)

    base = _gen.processar_pdf(str(pdf_path), str(pdf_path))

    # UC
    uc = _extract_uc_ap(txt)
    if uc:
        base["Instalacao"] = uc
        base["CODIGOCLIENTE"] = uc

    # CNPJ da distribuidora (mascarado no PDF)
    base["CNPJ"] = _CNPJ_AP

    # Mes de referencia
    mes_ref = _extract_mes_ref_ap(txt)
    if mes_ref:
        base["fatDataReferencia"] = mes_ref.strftime("01/%m/%Y")

    # Vencimento
    vcto = _extract_vencimento_ap(txt)
    if vcto:
        base["fatDataVcto"] = vcto

    # Valor total da fatura
    valor_fatura = _extract_total_fatura_ap(txt)
    if valor_fatura > 0:
        base["fatValorFatura"] = valor_fatura

    # Consumo
    base["fatConFPontaIndRegistrado"] = _extract_consumo_registrado_ap(txt)
    base["fatConFPontaIndFaturado"] = _extract_consumo_faturado_ap(txt)
    val_consumo = _extract_consumo_valor_ap(txt)
    if val_consumo:
        base["fatConFPontaIndValorReais"] = val_consumo

    # GD / Injetado
    kwh_inj, val_inj = _extract_injetado_ap(txt)
    base["fatConFPontaInjetadoRegistrado"] = kwh_inj
    base["fatConFPontaInjetadoFaturado"] = kwh_inj
    base["fatConFPontaInjetadoValorReais"] = val_inj

    # Bandeira (trunca antes de COFINS/PIS que aparecem na mesma linha)
    base["fatValBandeira"] = _extract_bandeira_ap(txt)

    # Codigo de barras (boleto, nao chave NF-e)
    barcode = _extract_barcode_ap(txt)
    if barcode:
        base["fatCodigoBarras"] = barcode

    # Retencoes (valores individuais)
    base.update(_extract_retencoes_ap(txt))

    # Multas
    multas = _extract_multas_ap(txt)
    if multas:
        base["fatMultas"] = multas

    # Valor da Nota Fiscal = base ICMS (ou valor fatura se ICMS zerado)
    icms_base = float(base.get("fatICMSBase") or 0)
    val_fat = float(base.get("fatValorFatura") or 0)
    base["fatValorNotaFiscal"] = icms_base if icms_base > 0 else val_fat

    # Aliquotas de retencao calculadas a partir dos valores vs base ICMS
    if icms_base > 0:
        for campo_val, campo_perc in [
            ("fatDescIrpjValRetImposto",   "fatDescIrpjPercRetImposto"),
            ("fatDescCsllValRetImposto",   "fatDescCsllPercRetImposto"),
            ("fatDescPisValRetImposto",    "fatDescPisPercRetImposto"),
            ("fatDescCofinsValRetImposto", "fatDescCofinsPercRetImposto"),
        ]:
            val = float(base.get(campo_val) or 0)
            if val != 0:
                base[campo_perc] = round(abs(val) / icms_base * 100, 4)

    return base


if __name__ == "__main__":
    raise SystemExit(
        main_bt_generico(
            sistema="EQUATORIAL AP",
            default_pasta="",
            default_saida_stem="ocr_equatorial_ap_bt",
            description="OCR Equatorial AP BT -> XLSX no schema CEMIG",
            parser_func=processar_pdf_ap,
            skip_sistema_check=True,
        )
    )
