#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Parser específico para faturas BT Equatorial Piauí (CEPISA).

Layout:
  • Cabeçalho: UC, subgrupo, leituras, emissão, ref/vencimento/total
  • Tabela de itens: Consumo (kWh) / Consumo Compensado (kWh) / Energia Inj. oUC
    – fiscal inline: "... valor TOTAL  PIS base aliq val" na mesma linha
    – ICMS aparece em linha própria acima do Consumo
  • Itens financeiros: CIP, Multa, Correção, Juros, Tributo a Reter
  • Boleto: UC, referência, linha digitável
  • Mensagens: Saldo Acumulado Geral Fora Ponta
"""
from __future__ import annotations

import re
import unicodedata
from pathlib import Path

import pdfplumber


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _norm(text: str) -> str:
    return unicodedata.normalize("NFKD", text or "").encode("ASCII", "ignore").decode("ASCII").upper()


def _br2f(s: str) -> float:
    s = str(s or "").strip().lstrip("-").replace(" ", "")
    if not s:
        return 0.0
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    elif s.count(".") == 1:
        before, after = s.split(".")
        if len(after) == 3 and len(before) <= 3:
            s = before + after
    try:
        return float(s)
    except ValueError:
        return 0.0


# Valores monetários BR com exatamente 2 casas decimais (exclui tarifas unitárias 3-8 dp)
_RE_MONEY = re.compile(r"-?[\d.]*\d,\d{2}(?!\d)")

_RE_BB = re.compile(r"^BB_(\d+)\.pdf$", re.IGNORECASE)


def _carimbo(pdf_path: Path) -> str:
    m = _RE_BB.match(pdf_path.name)
    return m.group(1) if m else pdf_path.stem


def _texto(pdf_path: Path) -> str:
    partes: list[str] = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        for page in pdf.pages:
            partes.append(page.extract_text() or "")
    return "\n".join(partes)


# ---------------------------------------------------------------------------
# Extração de campos
# ---------------------------------------------------------------------------

def _uc(txt: str) -> str:
    # Boleto: "UNIDADE CONSUMIDORA  X.XXX.XXX.XXX-XX  MM/YYYY"
    m = re.search(
        r"UNIDADE\s+CONSUMIDORA\s+([\d]{1,3}\.[\d]{3}\.[\d]{3}(?:\.[\d]{3})?-\d{2})\s",
        txt, re.IGNORECASE,
    )
    if m:
        return m.group(1)
    # Standalone antes do padrão ref/venc
    m2 = re.search(
        r"([\d]{1,3}\.[\d]{3}\.[\d]{3}(?:\.[\d]{3})?-\d{2})\s*\n\s*\d{2}/\d{4}\s+\d{2}",
        txt,
    )
    if m2:
        return m2.group(1)
    # Qualquer UC no texto (evita CNPJ: 14 dígitos)
    for m3 in re.finditer(r"([\d]{1,3}\.[\d]{3}\.[\d]{3}(?:\.[\d]{3})?-\d{2})", txt):
        digits = re.sub(r"\D", "", m3.group(1))
        if len(digits) <= 13:
            return m3.group(1)
    return ""


def _ref_vcto_total(txt: str) -> tuple[str, str, float]:
    """Linha 'MM/YYYY  DD/MM/YYYY  R$  X.XXX,XX' → (ref, vcto, total)."""
    m = re.search(
        r"\b(0[1-9]|1[0-2])/(20\d{2})\s+(\d{2}[./]\d{2}[./]20\d{2})\s+R\$\s*([\d.,]+)",
        txt,
    )
    if m:
        ref = f"01/{m.group(1)}/{m.group(2)}"
        vcto = m.group(3).replace(".", "/")
        total = _br2f(m.group(4))
        return ref, vcto, total
    return "", "", 0.0


def _emissao(txt: str) -> str:
    m = re.search(r"DATA\s+DE\s+EMISS[AÃ]O\s*:\s*(\d{2}/\d{2}/\d{4})", txt, re.IGNORECASE)
    if m:
        return m.group(1)
    # Fallback boleto: DATA DOCUMENTO + linha seguinte
    linhas = txt.splitlines()
    for i, ln in enumerate(linhas):
        if re.search(r"DATA\s+DOCUMENTO", ln, re.IGNORECASE) and i + 1 < len(linhas):
            dm = re.match(r"(\d{2}[./]\d{2}[./]20\d{2})", linhas[i + 1].strip())
            if dm:
                return dm.group(1).replace(".", "/")
    return ""


def _datas_leitura(txt: str) -> tuple[str, str]:
    """Linha 'DD/MM/YYYY DD/MM/YYYY N DD/MM/YYYY' → (anterior, atual)."""
    m = re.search(
        r"(\d{2}/\d{2}/20\d{2})\s+(\d{2}/\d{2}/20\d{2})\s+\d{1,3}\s+\d{2}/\d{2}/20\d{2}",
        txt,
    )
    if m:
        return m.group(1), m.group(2)
    return "", ""


def _subgrupo(txt: str) -> str:
    """Extrai subgrupo do cabeçalho (B1, B2, B3 etc.)."""
    m = re.search(r"SUBGRUPO\s*:\s*(B\d[^\s,]*)", txt, re.IGNORECASE)
    if m:
        sg = m.group(1).strip().upper()
        # Mapeia para formato Consen
        _MAP = {
            "B1": "B1 [AT 1kV]",
            "B2": "B2 [>1kV a 2,3kV]",
            "B3": "B3 [<2,3kV]",
            "B4": "B4 [<2,3kV]",
        }
        return _MAP.get(sg, f"{sg} [<2,3kV]")
    return "B3 [<2,3kV]"


def _nota_fiscal(txt: str) -> str:
    m = re.search(r"NOTA\s+FISCAL\s+N[Oº°]*\s*(\d{6,20})", _norm(txt))
    return m.group(1) if m else ""


def _consumo_ativo_total(txt: str) -> float:
    """'Consumo ATIVO TOTAL ... N kWh' → N como float."""
    m = re.search(
        r"Consumo\s+ATIVO\s+TOTAL\s+[\d.]+\s+[\d.]+\s+[\d.,]+\s+([\d.]+)\s*kWh",
        txt, re.IGNORECASE,
    )
    if m:
        return _br2f(m.group(1))
    return 0.0


def _consumo_items(txt: str) -> dict:
    """
    Extrai consumo, compensado e energia injetada das linhas da tabela.
    Retorna dict com:
      consumo_kwh, consumo_val,
      compensado_kwh, compensado_val,
      energia_inj_kwh (soma de todos Energia Inj.)
    """
    result = {
        "consumo_kwh": 0.0, "consumo_val": 0.0,
        "compensado_kwh": 0.0, "compensado_val": 0.0,
        "energia_inj_kwh": 0.0,
    }

    for ln in txt.splitlines():
        ln_s = ln.strip()
        # Remove dados fiscais inline (após PIS/COFINS/ICMS keyword) para não confundir
        ln_limpa = re.sub(r"\s+(?:PIS|COFINS|ICMS)\s+[\d.,]+.*$", "", ln_s, flags=re.I)
        monies = _RE_MONEY.findall(ln_limpa)

        # Consumo (kWh)  – linha direta, sem "Compensado"
        if re.match(r"Consumo\s*\(kWh\)\s+", ln_s, re.I) and "Compensado" not in ln_s:
            # kWh: primeiro inteiro ou decimal antes das tarifas
            m_kwh = re.match(r"Consumo\s*\(kWh\)\s+([\d.,]+)", ln_s, re.I)
            if m_kwh:
                result["consumo_kwh"] = _br2f(m_kwh.group(1))
            if monies:
                result["consumo_val"] = abs(_br2f(monies[-1]))

        # Consumo Compensado (kWh)
        elif re.match(r"Consumo\s+Compensado\s*\(kWh\)\s+", ln_s, re.I):
            m_kwh = re.match(r"Consumo\s+Compensado\s*\(kWh\)\s+([\d.,]+)", ln_s, re.I)
            if m_kwh:
                result["compensado_kwh"] = _br2f(m_kwh.group(1))
            if monies:
                result["compensado_val"] = abs(_br2f(monies[-1]))

        # Energia Inj. oUC ... (kWh)  – créditos de meses anteriores
        elif re.search(r"Energia\s+Inj\.?\s+oUC", ln_s, re.I):
            m_kwh = re.search(r"\)\s+([\d.,]+)", ln_s)
            if m_kwh:
                result["energia_inj_kwh"] += _br2f(m_kwh.group(1))

    return result


def _fiscal(txt: str) -> dict:
    """
    ICMS → linha própria: "ICMS base aliq valor"
    PIS / COFINS → inline no final de Consumo / Compensado:
      "... valor  PIS base aliq valor" ou em linha própria
    """
    out = {
        "fatICMS": 0.0, "fatDesIcmsAliquota": 0.0,
        "fatPIS": 0.0,  "fatDescPisAliquota": 0.0,
        "fatCOFINS": 0.0, "fatDesCofinsAliquota": 0.0,
    }
    # ICMS: "ICMS  136,23  22,5000  30,65"
    m = re.search(r"\bICMS\b\s+([\d.,]+)\s+([\d.,]+)\s+([\d.,]+)", txt, re.IGNORECASE)
    if m:
        out["fatICMS"] = abs(_br2f(m.group(3)))
        out["fatDesIcmsAliquota"] = abs(_br2f(m.group(2)))

    # PIS: "PIS  base  aliq  valor"
    m = re.search(r"\bPIS\b\s+([\d.,]+)\s+([\d.,]+)\s+([\d.,]+)", txt, re.IGNORECASE)
    if m:
        out["fatPIS"] = abs(_br2f(m.group(3)))
        out["fatDescPisAliquota"] = abs(_br2f(m.group(2)))

    # COFINS: "COFINS  base  aliq  valor"
    m = re.search(r"\bCOFINS\b\s+([\d.,]+)\s+([\d.,]+)\s+([\d.,]+)", txt, re.IGNORECASE)
    if m:
        out["fatCOFINS"] = abs(_br2f(m.group(3)))
        out["fatDesCofinsAliquota"] = abs(_br2f(m.group(2)))

    return out


def _financeiros(txt: str) -> dict:
    """Extrai CIP, Multa, Correção, Juros, Bandeira e Retenções."""
    out = {
        "fatIlumPublica": 0.0,
        "fatMultas": 0.0,
        "fatValBandeira": 0.0,
        "fatDescIrpjValRetImposto": 0.0,
        "fatDescCsllValRetImposto": 0.0,
        "fatDescPisValRetImposto": 0.0,
        "fatDescCofinsValRetImposto": 0.0,
    }

    _multa = 0.0
    _corr = 0.0
    _juros = 0.0

    for ln in txt.splitlines():
        ln_s = ln.strip()
        # Remove sidebar histórico de consumo (ex.: "214,13 JUN/25 1481")
        ln_clean = re.sub(r"\s+[A-Z]{3}/\d{2}\s+\d{3,5}.*$", "", ln_s).rstrip()
        monies = _RE_MONEY.findall(ln_clean)
        if not monies:
            continue

        up = _norm(ln_clean)
        last = abs(_br2f(monies[-1]))

        if "CIP" in up or "ILUM" in up:
            if last > 0:
                out["fatIlumPublica"] = last

        elif up.startswith("MULTA") and "ATRASO" not in up:
            _multa = last

        elif "CORR" in up and "MONET" in up:
            _corr = last

        elif up.startswith("JUROS"):
            _juros = last

        elif "ADICIONAL" in up and "BANDEIRA" in up:
            out["fatValBandeira"] = last

        elif "TRIBUTO" in up and "RETER" in up:
            for cod, campo in [
                ("IRPJ",   "fatDescIrpjValRetImposto"),
                ("CSLL",   "fatDescCsllValRetImposto"),
                ("PIS",    "fatDescPisValRetImposto"),
                ("COFINS", "fatDescCofinsValRetImposto"),
            ]:
                if cod in up:
                    # Extrai valor do final da linha (pode ter sidebar de histórico)
                    mv = _RE_MONEY.findall(ln_s)
                    if mv:
                        out[campo] = -abs(_br2f(mv[0]))
                    break

    out["fatMultas"] = round(_multa + _corr + _juros, 2)
    return out


def _saldo_acumulado(txt: str) -> tuple[float, float]:
    """Retorna (saldo_fp, saldo_ponta) dos créditos de GD."""
    fp = 0.0
    m = re.search(
        r"Saldo\s+Acumulado\s+Geral\s+Fora\s+Ponta\s*:\s*([\d.]+,\d{2})",
        txt, re.IGNORECASE,
    )
    if m:
        fp = _br2f(m.group(1))

    pt = 0.0
    m2 = re.search(
        r"Saldo\s+Acumulado\s+Geral\s+Ponta\s*:\s*([\d.]+,\d{2})",
        txt, re.IGNORECASE,
    )
    if m2:
        pt = _br2f(m2.group(1))

    return fp, pt


def _barcode(txt: str) -> str:
    """44-dígitos contíguos (chave NF-e / código de barras)."""
    m = re.search(r"(?<!\d)(\d{44})(?!\d)", txt)
    if m:
        return m.group(1)
    # Linha digitável: "XXXXX.XXXXX XXXXX.XXXXXX XXXXX.XXXXXX X XXXXXXXXXXXXXX"
    for ln in txt.splitlines():
        ln_s = ln.strip()
        digits = re.sub(r"\D", "", ln_s)
        if len(digits) in (47, 48) and len(re.sub(r"[\d.\- ]", "", ln_s)) <= 4:
            return digits
    return ""


# ---------------------------------------------------------------------------
# Função principal (chamada pelo ocr_bt_cemig_adapter)
# ---------------------------------------------------------------------------

def processar_pdf(path: str, _src: str | None = None) -> dict:
    """
    Processa PDF BT Equatorial PI e retorna dict no schema Consen.
    """
    pdf_path = Path(path)
    txt = _texto(pdf_path)

    uc = _uc(txt)
    ref, vcto, total = _ref_vcto_total(txt)
    emissao = _emissao(txt)
    ant, atu = _datas_leitura(txt)
    subgrupo = _subgrupo(txt)
    nf = _nota_fiscal(txt)
    carimbo = _carimbo(pdf_path)

    consumo = _consumo_items(txt)
    fiscal = _fiscal(txt)
    fin = _financeiros(txt)
    saldo_fp, saldo_pt = _saldo_acumulado(txt)
    code = _barcode(txt)

    # Consumo registrado total = kWh no medidor antes do crédito GD
    total_kwh = _consumo_ativo_total(txt)
    if total_kwh == 0:
        total_kwh = consumo["consumo_kwh"] + consumo["compensado_kwh"]

    # Valor bruto = consumo pago + equivalente GD (ambos positivos)
    total_val = round(consumo["consumo_val"] + consumo["compensado_val"], 2)

    comp_kwh = consumo["compensado_kwh"]
    comp_val = consumo["compensado_val"]

    return {
        # ── Identificação ─────────────────────────────────────────────────
        "fatCarimbo":                  carimbo,
        "Instalacao":                  uc,
        "CODIGOCLIENTE":               uc,
        "NOTAFISCAL":                  nf,
        "CNPJ":                        "00000000000191",
        "concCod":                     "EQUATORIAL PI",
        "cadTarifaCod":                "Convencional",
        "cadSubGrupoCod":              subgrupo,
        # ── Datas ─────────────────────────────────────────────────────────
        "fatDataEmissao":              emissao,
        "fatDataVcto":                 vcto,
        "fatDataReferencia":           ref,
        "fatDataLeituraAnterior":      ant,
        "fatDataLeituraAtual":         atu,
        # ── Valores ───────────────────────────────────────────────────────
        "fatValorFatura":              total,
        "fatValorNotaFiscal":          total,
        # ── Consumo F.Ponta Indutivo (total antes do crédito GD) ──────────
        "fatConFPontaIndRegistrado":   total_kwh,
        "fatConFPontaIndFaturado":     total_kwh,
        "fatConFPontaIndValorReais":   total_val,
        # ── Consumo F.Ponta Injetado (crédito GD aplicado este mês) ───────
        "fatConFPontaInjetadoRegistrado": comp_kwh,
        "fatConFPontaInjetadoFaturado":   comp_kwh,
        "fatConFPontaInjetadoValorReais": comp_val,
        # ── Usina (planta GD) ─────────────────────────────────────────────
        "fatConFPontaInjetadoUsina":               comp_kwh,
        "fatConFPontaInjetadoUsinaSaldoAcumulado":  saldo_fp,
        # ── Fiscal ────────────────────────────────────────────────────────
        **fiscal,
        # ── Financeiros ───────────────────────────────────────────────────
        "fatIlumPublica":              fin["fatIlumPublica"],
        "fatMultas":                   fin["fatMultas"],
        "fatValBandeira":              fin["fatValBandeira"],
        # ── Retenções ─────────────────────────────────────────────────────
        "fatDescIrpjPercRetImposto":   1.20,
        "fatDescIrpjValRetImposto":    fin["fatDescIrpjValRetImposto"],
        "fatDescPisPercRetImposto":    0.65,
        "fatDescPisValRetImposto":     fin["fatDescPisValRetImposto"],
        "fatDescCofinsPercRetImposto": 3.00,
        "fatDescCofinsValRetImposto":  fin["fatDescCofinsValRetImposto"],
        "fatDescCsllPercRetImposto":   1.00,
        "fatDescCsllValRetImposto":    fin["fatDescCsllValRetImposto"],
        # ── Código de barras ──────────────────────────────────────────────
        "fatCodigoBarras":             code,
    }
