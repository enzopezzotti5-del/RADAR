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
_MESES = _gen.MESES


def _extract_mes_ref_al(txt: str):
    """Equatorial AL: ignora '04/2026' dentro de 'oUC 04/2026 oPT'; prioriza linha do boleto."""
    import datetime as dt

    # "05/2026 25/06/2026 R$ ..." — linha de referência/vencimento/valor
    m = re.search(r"\b(0[1-9]|1[0-2])/(20\d{2})\s+\d{2}/\d{2}/\d{4}\s+R\$", txt)
    if m:
        return dt.date(int(m.group(2)), int(m.group(1)), 1)
    return _gen._extract_mes_ref(txt)


def _extract_vencimento_al(txt: str) -> str:
    """Vencimento: segunda data na linha 'MM/AAAA DD/MM/AAAA R$ ...'."""
    m = re.search(r"\b(?:0[1-9]|1[0-2])/20\d{2}\s+(\d{2}/\d{2}/20\d{2})\s+R\$", txt)
    return m.group(1) if m else ""


def _extract_total_fatura_al(txt: str) -> float:
    """Valor total da fatura na linha 'MM/AAAA DD/MM/AAAA R$ X.XXX,XX'."""
    m = re.search(r"\b(?:0[1-9]|1[0-2])/20\d{2}\s+\d{2}/\d{2}/20\d{2}\s+R\$\s*([\d.]+,\d{2})", txt)
    if m:
        return _br2f(m.group(1))
    return 0.0


def _extract_consumo_registrado_al(txt: str) -> float:
    """Consumo lido no medidor: linha 'Consumo ATIVO TOTAL ... N kWh'."""
    m = re.search(
        r"Consumo\s+ATIVO\s+TOTAL\s+[\d.,]+\s+[\d.,]+\s+[\d.,]+\s+([\d.,]+)\s+kWh",
        txt, re.I,
    )
    if m:
        v = _br2f(m.group(1))
        if 1 <= v <= 999_999:
            return v
    return _gen._extract_consumo(txt)


def _extract_consumo_faturado_al(txt: str) -> float:
    """Consumo faturado: 'Consumo (kWh)' (GD) ou 'Custo de disponibilidade (kWh)' (sem GD)."""
    for line in txt.splitlines():
        if re.search(r"^Consumo\s+\(kWh\)", line.strip(), re.I):
            nums = re.findall(r"[\d.,]+", line)
            if nums:
                v = _br2f(nums[0])
                if 1 <= v <= 999_999:
                    return v
    # custo de disponibilidade: 'Custo de disponibilidade (kWh) 30 ...'
    for line in txt.splitlines():
        if re.search(r"Custo\s+de\s+disponibilidade\s+\(kWh\)", line, re.I):
            nums = re.findall(r"[\d.,]+", line)
            if nums:
                v = _br2f(nums[0])
                if 1 <= v <= 999_999:
                    return v
    return 0.0


def _extract_consumo_valor_al(txt: str) -> float:
    """Valor R$ do consumo/disponibilidade: último número antes de 'PIS' na linha."""
    for line in txt.splitlines():
        if re.search(r"(?:Consumo\s+\(kWh\)|Custo\s+de\s+disponibilidade\s+\(kWh\))", line, re.I):
            clean = re.split(r"\s+PIS\b", line, flags=re.I)[0]
            nums = re.findall(r"[\d.,]+", clean)
            if nums:
                v = _br2f(nums[-1])
                if v > 0:
                    return v
    return 0.0


def _extract_multas_al(txt: str) -> float:
    """Multa + Correção Monetária + Juros (débitos de fatura anterior)."""
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


def _extract_injetado_al(txt: str) -> tuple[float, float]:
    """(kWh_compensado, valor_R$) da linha 'Consumo Compensado (kWh) N ... TOTAL'.

    Tributos (COFINS/PIS/ICMS) são impressos na mesma linha pelo pdfplumber;
    truncamos antes deles para não capturar o valor do tributo como total.
    """
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


def _extract_bandeira_al(txt: str) -> float:
    """Valor R$ do adicional de bandeira tarifária."""
    m = re.search(r"Adicional\s+Bandeira\s+[\d.,]+\s+[\d.,]+\s+([\d.,]+)", txt, re.I)
    if m:
        v = _br2f(m.group(1))
        if v > 0:
            return v
    return 0.0


def _extract_cip_al(txt: str) -> float:
    """CIP / Iluminação Pública Municipal — primeiro número após a label."""
    for line in txt.splitlines():
        if re.search(r"Cip[-\s]Ilum\s+Pub", line, re.I):
            nums = re.findall(r"[\d.,]+", line)
            if nums:
                v = _br2f(nums[0])
                if v > 0:
                    return v
    return 0.0


def _aplicar_injetado_al(base: dict, txt: str) -> None:
    kwh_inj, val_inj = _extract_injetado_al(txt)
    base["fatConFPontaInjetadoRegistrado"] = kwh_inj
    base["fatConFPontaInjetadoFaturado"] = kwh_inj
    base["fatConFPontaInjetadoValorReais"] = val_inj
    base["fatConFPontaInjetadoUsina"] = kwh_inj


def _extract_retencoes_al(txt: str) -> dict:
    """
    Equatorial AL: 'Tributo a Reter IRPJ/CSLL/PIS/COFINS  XX,XX'
      Cada tributo vai para seu próprio campo.
    """
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


def _extract_uc_al(txt: str) -> str:
    """UC da linha 'NNN.NNN.NNN-NN https://...' (aparece antes da chave NF-e)."""
    m = re.search(r"^(\d{3}\.\d{3}\.\d{3}-\d{2})\s+https?://", txt, re.MULTILINE)
    if m:
        return m.group(1)
    # fallback: linha do boleto 'EQUATORIAL ALAGOAS DISTRIB ... NNN.NNN.NNN-NN'
    m = re.search(r"EQUATORIAL[^\n]+DISTRIB[^\n]+\s(\d{3}\.\d{3}\.\d{3}-\d{2})\b", txt, re.I)
    return m.group(1) if m else ""


def processar_pdf_al(pdf_path: Path) -> dict:
    """Parser Equatorial AL BT: base genérica + correções específicas."""
    with pdfplumber.open(str(pdf_path)) as pdf:
        txt = "\n".join(p.extract_text() or "" for p in pdf.pages)

    base = _gen.processar_pdf(str(pdf_path), str(pdf_path))

    uc = _extract_uc_al(txt)
    if uc:
        base["Instalacao"] = uc
        base["CODIGOCLIENTE"] = uc

    mes_ref = _extract_mes_ref_al(txt)
    if mes_ref:
        base["fatDataReferencia"] = mes_ref.strftime("01/%m/%Y")

    valor_fatura = _extract_total_fatura_al(txt)
    if valor_fatura > 0:
        base["fatValorFatura"] = valor_fatura

    base["fatConFPontaIndRegistrado"] = _extract_consumo_registrado_al(txt)
    base["fatConFPontaIndFaturado"] = _extract_consumo_faturado_al(txt)
    val_consumo = _extract_consumo_valor_al(txt)
    if val_consumo:
        base["fatConFPontaIndValorReais"] = val_consumo

    _aplicar_injetado_al(base, txt)

    base["fatValBandeira"] = _extract_bandeira_al(txt)
    base["fatIlumPublica"] = _extract_cip_al(txt)
    base.update(_extract_retencoes_al(txt))

    multas = _extract_multas_al(txt)
    if multas:
        base["fatMultas"] = multas

    vcto = _extract_vencimento_al(txt)
    if vcto:
        base["fatDataVcto"] = vcto

    # Nesta família BT, a base do ICMS representa melhor o valor fiscal da nota;
    # sem ela, usamos o total da fatura como fallback.
    base["fatValorNotaFiscal"] = float(base.get("fatICMSBase") or 0) or float(base.get("fatValorFatura") or 0)

    # Alíquotas de retenção: val_retido / base_ICMS * 100
    icms_base = float(base.get("fatICMSBase") or 0)
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
            sistema="EQUATORIAL AL",
            default_pasta="",
            default_saida_stem="ocr_equatorial_al_bt",
            description="OCR Equatorial AL BT -> XLSX no schema CEMIG",
            parser_func=processar_pdf_al,
        )
    )
