#!/usr/bin/env python3
from __future__ import annotations

import datetime as _dt
import logging
import re
import sys
import unicodedata
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import pdfplumber

from core.ocr import ocr_enel
from core.ocr.ocr_bt_cemig_adapter import _parser_generico, main_bt_generico


log = logging.getLogger(__name__)


def _br2f(s: str) -> float:
    try:
        return float(str(s).strip().replace(".", "").replace(",", "."))
    except (ValueError, AttributeError):
        return 0.0


def _texto(pdf_path: Path) -> str:
    with pdfplumber.open(str(pdf_path)) as pdf:
        return "\n".join(p.extract_text() or "" for p in pdf.pages[:2])


def _extract_consumo_registrado_light(txt: str) -> float:
    """Consumo lido no medidor na tabela 'CAMPO LIDO PELO MEDIDOR'."""
    patterns = [
        r"CAMPO\s+LIDO\s+PELO\s+MEDIDOR[\s\S]{0,200}?VALOR\s+DO\s+CONSUMO\s+REGISTRADO\s+([-\d\.,]+)",
        r"VALOR\s+DO\s+CONSUMO\s+REGISTRADO\s+([-\d\.,]+)",
    ]
    for pattern in patterns:
        m = re.search(pattern, txt, re.IGNORECASE)
        if m:
            valor = _br2f(m.group(1))
            if valor > 0:
                return valor

    for line in txt.splitlines():
        line_norm = unicodedata.normalize("NFKD", line).encode("ascii", "ignore").decode("ascii").upper()
        if "LIDO PELO MEDIDOR" not in line_norm and "CONSUMO REGISTRADO" not in line_norm:
            continue
        nums = re.findall(r"[-\d\.,]+", line)
        if not nums:
            continue
        valor = _br2f(nums[-1])
        if valor > 0:
            return valor
    return 0.0


def _extract_consumo_faturado_light(txt: str) -> float:
    """Consumo faturado em 'ITENS DA FATURA' / linha de consumo."""
    linhas = txt.splitlines()
    in_itens = False
    for line in linhas:
        line_norm = unicodedata.normalize("NFKD", line).encode("ascii", "ignore").decode("ascii").upper()
        if "ITENS DA FATURA" in line_norm:
            in_itens = True
            continue
        if in_itens and any(marcador in line_norm for marcador in ("TABELA DE TRIBUTOS", "CAMPO LIDO PELO MEDIDOR", "TOTAL", "VENCIMENTO")):
            break
        if "CONSUMO" not in line_norm:
            continue
        if "KWH" not in line_norm:
            continue
        m = re.search(r"\bKWH\b\s+([-\d\.,]+)", line, re.IGNORECASE)
        if m:
            valor = _br2f(m.group(1))
            if valor > 0:
                return valor

    for line in linhas:
        line_norm = unicodedata.normalize("NFKD", line).encode("ascii", "ignore").decode("ascii").upper()
        if not line_norm.startswith("CONSUMO"):
            continue
        if "KWH" not in line_norm:
            continue
        m = re.search(r"\bKWH\b\s+([-\d\.,]+)", line, re.IGNORECASE)
        if m:
            valor = _br2f(m.group(1))
            if valor > 0:
                return valor
    return 0.0


def _parser_light_bt(pdf_path: Path) -> dict:
    # A LIGHT compartilha o mesmo bloco de itens/obs da família ENEL em boa parte
    # dos PDFs BT; usar esse parser como base evita perder DIC/FIC, multas e GD.
    rec = ocr_enel.processar_pdf(str(pdf_path), "bt")
    if rec.get("ERRO"):
        rec = _parser_generico(pdf_path)
    txt = _texto(pdf_path)

    # UC LIGHT: número completo aparece no início da linha de autenticação
    # Ex: "549.073.059-02 24/06/2026 ****21.749,45 MAI/2026 Autenticação Mecânica"
    # O ocr_bt_generico captura fragmento truncado; aqui pegamos o valor correto.
    m_uc = re.search(r"^([\d][\d.]*-\d{2})\s+\d{2}/\d{2}/\d{4}", txt, re.MULTILINE)
    if not m_uc:
        # Alguns PDFs LIGHT trazem a UC sozinha em uma linha isolada.
        m_uc = re.search(r"(?m)^(\d{1,4}(?:\.\d{3}){1,3}-\d{2})\s*$", txt)
    if m_uc:
        uc = m_uc.group(1)
        rec["Instalacao"] = uc
        rec["CODIGOCLIENTE"] = uc

    # IlumPublica: "Contrib Ilum Pública Municipal 555,26"
    m = re.search(r"[Cc]ontrib\.?\s+[Ii]lum[^\d]+([\d.]+,\d{2})", txt)
    if m:
        rec["fatIlumPublica"] = _br2f(m.group(1))
    m_cosip = re.search(r"\bComplemento\s+COSIP\b[^\n]*?([\d.]+,\d{2})", txt, re.MULTILINE | re.IGNORECASE)
    if m_cosip:
        rec["fatIlumPublica"] = round(float(rec.get("fatIlumPublica") or 0.0) + _br2f(m_cosip.group(1)), 2)

    # "MAI/2026 24/06/2026 R$ 1.234,56" — captura vencimento e valor
    m_total = re.search(r"\b[A-Z]{3}/\d{4}\s+(\d{2}/\d{2}/\d{4})\s+R\$\s*([\d.]+,\d{2})", txt)
    if m_total:
        rec["fatValorFatura"] = _br2f(m_total.group(2))
        try:
            rec["fatDataVcto"] = _dt.datetime.strptime(m_total.group(1), "%d/%m/%Y").date()
        except ValueError:
            pass

    # Fallback vencimento: linha "VENCIMENTO DD/MM/AAAA" ou "DATA DE VENCIMENTO DD/MM/AAAA"
    if not rec.get("fatDataVcto"):
        m_vcto = re.search(r"VENCIMENTO\s*[:\-]?\s*(\d{2}/\d{2}/\d{4})", txt, re.IGNORECASE)
        if m_vcto:
            try:
                rec["fatDataVcto"] = _dt.datetime.strptime(m_vcto.group(1), "%d/%m/%Y").date()
            except ValueError:
                pass

    # Data de emissao costuma aparecer junto ao bloco da nota fiscal.
    if not rec.get("fatDataEmissao"):
        m_emissao = re.search(
            r"DATA\s+DE\s+EMISS[ÃA]O\s*[:\-]?\s*(\d{2}/\d{2}/\d{4})",
            txt,
            re.IGNORECASE,
        )
        if m_emissao:
            try:
                rec["fatDataEmissao"] = _dt.datetime.strptime(m_emissao.group(1), "%d/%m/%Y").date()
            except ValueError:
                pass

    # Light tem dois formatos de PDF dependendo do porte da UC.
    # Formato A (grande UC): "PIS/PASEP100.754,371,23% 1.239,28" ou "PIS/PASEP 2.178,34 1,23% 26,79"
    #   — pdfplumber pode ou não inserir espaço entre base e alíquota
    # Formato B (pequena UC): texto de tabela com "ICMS 1.584,72 19% 301,09"; PIS/COFINS saem
    #   em texto vertical ilegível, sem extração confiável.
    m_base = re.search(
        r"\bPIS(?:/PASEP)?\s*([\d.]+,\d{2})\s*([\d.,]+)\s*%\s+([\d.]+,\d{2})",
        txt, re.IGNORECASE,
    )
    if m_base:
        rec["fatValorNotaFiscal"] = _br2f(m_base.group(1))
        rec["fatDescPisAliquota"] = _br2f(m_base.group(2))
        rec["fatPIS"]             = _br2f(m_base.group(3))

    m_cof = re.search(
        r"\bCOFINS\s*([\d.]+,\d{2})\s*([\d.,]+)\s*%\s+([\d.]+,\d{2})",
        txt, re.IGNORECASE,
    )
    if m_cof:
        rec["fatDesCofinsAliquota"] = _br2f(m_cof.group(2))
        rec["fatCOFINS"]            = _br2f(m_cof.group(3))

    # Formato A: ICMS embutido na linha de Energia como "XX,000 XXXXX,XX ... PIS/PASEP"
    m_icms = re.search(
        r"(\d{1,2},\d{3})\s+([\d.]+,\d{2})\s+[\d.,]+\s+PIS/PASEP",
        txt, re.IGNORECASE,
    )
    if m_icms:
        rec["fatDesIcmsAliquota"] = _br2f(m_icms.group(1))
        rec["fatICMS"]            = _br2f(m_icms.group(2))

    # Formato B: "ICMS BASE XX% VALOR" em linha própria
    if not rec.get("fatDesIcmsAliquota"):
        m_icms_b = re.search(
            r"\bICMS\s+([\d.]+,\d{2})\s+([\d.,]+)\s*%\s+([\d.]+,\d{2})",
            txt, re.IGNORECASE,
        )
        if m_icms_b:
            rec["fatDesIcmsAliquota"] = _br2f(m_icms_b.group(2))
            rec["fatICMS"]            = _br2f(m_icms_b.group(3))

    consumo_registrado_kwh = _extract_consumo_registrado_light(txt)
    consumo_faturado_kwh = _extract_consumo_faturado_light(txt)
    consumo_disponibilidade_raw = consumo_faturado_kwh - consumo_registrado_kwh
    consumo_disponibilidade_kwh = max(consumo_disponibilidade_raw, 0.0)
    if consumo_faturado_kwh and consumo_disponibilidade_raw < 0:
        log.warning(
            "[LIGHT B3] consumo faturado menor que o registrado: registrado=%s faturado=%s",
            consumo_registrado_kwh,
            consumo_faturado_kwh,
        )

    consumo_kwh = 0.0
    consumo_val = 0.0
    m_consumo = re.search(
        r"^Energia\s+El[ée]trica\s+kWh\s+kWh\s+([\d.]+)\s+[\d.,]+\s+([\d.]+,\d{2})",
        txt,
        re.MULTILINE | re.IGNORECASE,
    )
    if m_consumo:
        consumo_kwh += _br2f(m_consumo.group(1))
        consumo_val += _br2f(m_consumo.group(2))

    # Em LIGHT BT com GD, a energia consumida pode vir separada em
    # "Energia Elétrica" e "Energia Fornecida GD". Para digitação/auditoria,
    # consolidamos essas parcelas no consumo principal.
    fornecida_kwh = 0.0
    for m_forn in re.finditer(
        r"^Energia\s+Fornecida\s+GD.*?kWh\s+([\d.]+)\s+[\d.,]+\s+([\d.]+,\d{2})",
        txt,
        re.MULTILINE | re.IGNORECASE,
    ):
        fornecida_kwh = max(fornecida_kwh, _br2f(m_forn.group(1)))
        consumo_val += _br2f(m_forn.group(2))
    consumo_kwh += fornecida_kwh

    m_medidor = re.search(
        r"Energia\s+Ativa-Kwh\s+\S+\s+[\d.]+\s+[\d.]+\s+[\d.,]+\s+([\d.]+)",
        txt,
        re.IGNORECASE,
    )
    if m_medidor:
        consumo_kwh = max(consumo_kwh, _br2f(m_medidor.group(1)))

    if consumo_registrado_kwh > 0:
        rec["fatConFPontaIndRegistrado"] = consumo_registrado_kwh
    if consumo_faturado_kwh > 0:
        rec["fatConFPontaIndFaturado"] = consumo_faturado_kwh
    elif consumo_kwh > 0:
        rec["fatConFPontaIndFaturado"] = consumo_kwh
    if consumo_kwh > 0 and not rec.get("fatConFPontaIndRegistrado"):
        rec["fatConFPontaIndRegistrado"] = consumo_kwh
    if consumo_val > 0:
        rec["fatConFPontaIndValorReais"] = round(consumo_val, 2)

    # Consumo mínimo: Light pode faturar 100 kWh mesmo sem linha explícita
    if not rec.get("fatConFPontaIndRegistrado"):
        rec["fatConFPontaIndRegistrado"] = 100.0
        rec["fatConFPontaIndFaturado"]   = 100.0

    inj_kwh = 0.0
    inj_val = 0.0
    for m_inj in re.finditer(
        r"^Energia\s+Injetada\s+GD.*?kWh\s+([\d.]+)\s+[\d.,]+\s+(-?[\d.]+,\d{2})",
        txt,
        re.MULTILINE | re.IGNORECASE,
    ):
        inj_kwh = max(inj_kwh, _br2f(m_inj.group(1)))
        inj_val += abs(_br2f(m_inj.group(2)))
    if inj_kwh > 0:
        rec["fatConFPontaInjetadoRegistrado"] = inj_kwh
        rec["fatConFPontaInjetadoFaturado"] = inj_kwh
        rec["fatConFPontaInjetadoUsina"] = inj_kwh
    if inj_val > 0:
        rec["fatConFPontaInjetadoValorReais"] = round(inj_val, 2)

    m_saldo = re.search(r"Saldo\s+de\s+Cr[eé]ditos:\s*([\d.]+,\d{2})", txt, re.IGNORECASE)
    if m_saldo:
        rec["fatConFPontaInjetadoUsinaSaldoAcumulado"] = _br2f(m_saldo.group(1))

    m_band = re.search(r"^Bandeira\s+(?:Amarela|Vermelha|Verde)[^\d]*([\d.]+,\d{2})", txt, re.MULTILINE | re.IGNORECASE)
    if m_band:
        rec["fatValBandeira"] = _br2f(m_band.group(1))

    m_dic = re.search(r"Compensa[cç][aã]o\s+DIC(?:\s+Mensal)?[^\n]*?(-?[\d.]+,\d{2})", txt, re.IGNORECASE)
    if m_dic:
        rec["fatDIC"] = abs(_br2f(m_dic.group(1)))
    m_fic = re.search(r"Compensa[cç][aã]o\s+FIC(?:\s+Mensal)?[^\n]*?(-?[\d.]+,\d{2})", txt, re.IGNORECASE)
    if m_fic:
        rec["fatFIC"] = abs(_br2f(m_fic.group(1)))

    # Retenções LIGHT: "Imposto Retido {COD} - Energia -XX,XX"
    # Alíquota não consta no PDF — aplica breakdown fixo 5,85% BT
    _ALIQ_LIGHT: dict[str, float] = {"IRPJ": 1.20, "PIS": 0.65, "COFINS": 3.00, "CSLL": 1.00}
    for cod, campo_val, campo_perc in [
        ("IRPJ",   "fatDescIrpjValRetImposto",   "fatDescIrpjPercRetImposto"),
        ("PIS",    "fatDescPisValRetImposto",    "fatDescPisPercRetImposto"),
        ("COFINS", "fatDescCofinsValRetImposto", "fatDescCofinsPercRetImposto"),
        ("CSLL",   "fatDescCsllValRetImposto",   "fatDescCsllPercRetImposto"),
    ]:
        m = re.search(
            r"Imposto\s+Retido\s+" + cod + r"\s*-?\s*Energia\s+-\s*([\d.,]+)",
            txt, re.IGNORECASE,
        )
        if m:
            val = _br2f(m.group(1))
            if val > 0:
                rec[campo_val]  = -val
                rec[campo_perc] = _ALIQ_LIGHT[cod]

    # Sobrescreve encargos por atraso com os itens explícitos da fatura LIGHT.
    # Formato: "Multa 2% ... sobre R$ {base} {valor_multa} [outros campos]"
    #          "Juros mora ...  sobre R${base} {valor_juros} [outros campos]"
    # Ancora em "sobre R$ {base}" para capturar o valor IMEDIATAMENTE APÓS a base,
    # ignorando outros valores que possam aparecer depois (COFINS, kWh, etc.).
    multa_total = 0.0
    for m in re.finditer(r"\bMulta\b[^\n]*?sobre\s+R\$\s*[\d.]+,\d+\s+([\d.]+,\d{2})", txt, re.MULTILINE | re.IGNORECASE):
        multa_total += _br2f(m.group(1))

    juros_total = 0.0
    for m in re.finditer(r"\bJuros\b[^\n]*?sobre\s+R\$[\d.]+,\d+\s+([\d.]+,\d{2})", txt, re.MULTILINE | re.IGNORECASE):
        juros_total += _br2f(m.group(1))

    ipca_total = 0.0
    for m in re.finditer(r"\bD[ÉE]BITO\s+VAR\s+IPCA\b[^\n]*?sobre\s+R\$\s*[\d.]+,\d+\s+([\d.]+,\d{2})", txt, re.MULTILINE | re.IGNORECASE):
        ipca_total += _br2f(m.group(1))

    rec["fatMultas"] = round(multa_total + juros_total + ipca_total, 2)
    rec["fatMultasDiversas"] = 0.0

    bandeira_original = float(rec.get("fatValBandeira") or 0.0)
    parcela_disponibilidade_bandeira = 0.0
    bandeira_corrigida = bandeira_original
    if bandeira_original > 0 and consumo_faturado_kwh > 0 and consumo_disponibilidade_kwh > 0:
        parcela_disponibilidade_bandeira = round(
            bandeira_original * (consumo_disponibilidade_kwh / consumo_faturado_kwh),
            2,
        )
        bandeira_corrigida = round(max(bandeira_original - parcela_disponibilidade_bandeira, 0.0), 2)
    log.info(
        "[LIGHT B3] consumo_registrado=%s consumo_faturado=%s consumo_disponibilidade=%s bandeira_original=%s parcela_disponibilidade_bandeira=%s bandeira_corrigida=%s",
        round(consumo_registrado_kwh, 2),
        round(consumo_faturado_kwh, 2),
        round(consumo_disponibilidade_kwh, 2),
        round(bandeira_original, 2),
        round(parcela_disponibilidade_bandeira, 2),
        round(bandeira_corrigida, 2),
    )

    return rec


if __name__ == "__main__":
    raise SystemExit(
        main_bt_generico(
            sistema="LIGHT RJ",
            default_pasta="",
            default_saida_stem="ocr_light_rj_bt",
            description="OCR Light RJ BT -> XLSX no schema CEMIG",
            parser_func=_parser_light_bt,
        )
    )
