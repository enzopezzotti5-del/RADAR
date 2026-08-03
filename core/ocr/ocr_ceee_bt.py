#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OCR CEEE BT (Companhia Estadual de Distribuicao de Energia Eletrica - RS)
-> XLSX para digitacao no Consen.

Baseado em ocr_energisa_bt.py. Reutiliza helpers de ocr_neoenergia.
Suporte a faturas BT comerciais com TE + TUSD em linhas separadas.

Identificacao: "COMPANHIA ESTADUAL DE DISTRIBUICAO DE ENERGIA ELETRICA"
               CNPJ: 08.467.115/0001-00
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

LOCAL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LOCAL_DIR))

import pdfplumber

from ocr.ocr_neoenergia import (
    MAX_WORKERS,
    OUTPUT_DIR as NEO_OUTPUT_DIR,
    _carimbo_do_nome,
    _empty_record,
    _extract_codigo_barras,
    _extract_debitos_anteriores,
    _extract_ilum_publica,
    _extract_imposto,
    _extract_pdf_data,
    _norm,
    _texto_normalizado,
    _to_date,
    _to_float_br,
    salvar_excel,
)


OUTPUT_DIR   = NEO_OUTPUT_DIR.parent / "OCR CEEE"
DEFAULT_PASTA = Path("//10.10.250.21/Energia/CONTASDEENERGIAELETRICA/BB/ENZO")

CNPJ_CEEE = "08467115000100"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("ocr_ceee_bt")


def _digits(text: str) -> str:
    return re.sub(r"\D", "", str(text or ""))


def _first_page_text(pdf_path: Path) -> str:
    with pdfplumber.open(str(pdf_path)) as pdf:
        if not pdf.pages:
            return ""
        return pdf.pages[0].extract_text(x_tolerance=1, y_tolerance=1) or ""


def _is_ceee(text: str) -> bool:
    txt = _texto_normalizado(text)
    return (
        "COMPANHIA ESTADUAL DE DISTRIBUICAO DE ENERGIA ELETRICA" in txt
        or "COMPANHIA ESTADUAL DE DISTRIBUICAO ELETRICA" in txt
        or CNPJ_CEEE in _digits(txt)
    )


def _extract_instalacao_ceee(text: str) -> str:
    txt = _texto_normalizado(text)
    # Formato novo: UC no padrão d.ddd.ddd.ddd-dd ou ddd.ddd.ddd-dd
    m = re.search(r"(\d{1,3}\.\d{3}\.\d{3}(?:\.\d{3})?-\d{2})", txt)
    if m:
        return m.group(1)
    # Formato antigo: "INSTALACAO: XXXX"
    m = re.search(r"INSTALACAO:\s*(\d+)", txt)
    if m:
        return m.group(1)
    m = re.search(r"INSTALACAO\s+(\d+)", txt)
    if m:
        return m.group(1)
    return ""


def _extract_referencia_ceee(text: str, mes_padrao: int, ano_padrao: int) -> dt.date:
    """
    Linha corpo : '03/2026 20/05/2026 R$ 5.055,90'
    Fallback boleto: '2.040.417.010-20 06/2026 DATA DOCUMENTO'
    """
    txt = _texto_normalizado(text)
    m = re.search(r"(\d{2})/(\d{4})\s+\d{2}/\d{2}/\d{4}\s+R\$", txt)
    if m:
        try:
            return dt.date(int(m.group(2)), int(m.group(1)), 1)
        except ValueError:
            pass
    # Fallback: UC seguida de MM/YYYY no boleto
    m = re.search(r"\d{1,3}\.\d{3}\.\d{3}(?:\.\d{3})?-\d{2}\s+(\d{2})/(\d{4})", txt)
    if m:
        try:
            return dt.date(int(m.group(2)), int(m.group(1)), 1)
        except ValueError:
            pass
    return dt.date(ano_padrao, mes_padrao, 1)


def _extract_datas_ceee(text: str) -> tuple[dt.date | None, dt.date | None, dt.date | None]:
    """
    Leituras (novo): 'Leitura Anterior  Leitura Atual  N° de Dias  Próxima Leitura'
                     '24/04/2026  25/05/2026  31  24/06/2026'
    Leituras (antigo): 'LEITURAS 20/02/2026 20/03/2026 28 20/04/2026'
    Resumo  : '03/2026 20/05/2026 R$ 5.055,90'
    """
    txt = _texto_normalizado(text)

    # Leituras anterior e atual
    m_leit = re.search(
        r"LEITURA\s+ANTERIOR[^\d]*(\d{2}/\d{2}/\d{4})\s+(\d{2}/\d{2}/\d{4})\s+\d+",
        txt,
    )
    if not m_leit:
        m_leit = re.search(
            r"LEITURAS\s+(\d{2}/\d{2}/\d{4})\s+(\d{2}/\d{2}/\d{4})\s+\d+",
            txt,
        )
    ant = _to_date(m_leit.group(1)) if m_leit else None
    atu = _to_date(m_leit.group(2)) if m_leit else None

    # Vencimento na linha de resumo ou no label VENCIMENTO do boleto
    m_vcto = re.search(r"\d{2}/\d{4}\s+(\d{2}/\d{2}/\d{4})\s+R\$", txt)
    if not m_vcto:
        m_vcto2 = re.search(r"VENCIMENTO\s+(\d{2}[./]\d{2}[./]\d{4})", txt)
        vcto = _to_date(m_vcto2.group(1).replace(".", "/")) if m_vcto2 else None
    else:
        vcto = _to_date(m_vcto.group(1))

    return ant, atu, vcto


def _extract_data_emissao_ceee(text: str) -> dt.date | None:
    txt = _texto_normalizado(text)
    padroes = [
        r"DATA DE EMISSAO[^\d]{0,80}(\d{2}[./]\d{2}[./]\d{4})",
        r"DATA DOCUMENTO[^\d]{0,120}(\d{2}[./]\d{2}[./]\d{4})",
    ]
    for padrao in padroes:
        m = re.search(padrao, txt)
        if m:
            return _to_date(m.group(1).replace(".", "/"))
    return None


def _extract_notafiscal_ceee(text: str) -> str:
    txt = _texto_normalizado(text)
    m = re.search(r"NOTA FISCAL N[O°]*\s*([\d.]+)", txt)
    return _digits(m.group(1)) if m else ""


def _extract_total_ceee(text: str) -> float:
    """
    Linha corpo : '03/2026 20/05/2026 R$ 5.055,90'
    Fallback boleto: 'VALOR DOCUMENTO  RCO 100 R$ 2.402,95'
    """
    txt = _texto_normalizado(text)
    m = re.search(r"\d{2}/\d{4}\s+\d{2}/\d{2}/\d{4}\s+R\$\s*([\d\.,]+)", txt)
    if m:
        return abs(_to_float_br(m.group(1)))
    m = re.search(r"VALOR DOCUMENTO\s+RCO\s+\d+\s+R\$\s*([\d\.,]+)", txt)
    if m:
        return abs(_to_float_br(m.group(1)))
    return 0.0


def _extract_consumo_ceee(text: str) -> dict[str, float]:
    """
    Tres casos possíveis na CEEE BT:

    1. Consumo normal — TE + TUSD em linhas separadas:
         CONSUMO TE (KWH)   5.160,00  0,431849  2.228,34
         CONSUMO TU(SD)? (KWH)  5.160,00  0,601965  3.106,14
       -> fatConFPontaIndRegistrado = kWh
       -> fatConFPontaIndValorReais = TE + TUSD (total)
       -> fatConFPontaCapValorReais = TUSD isolado (auxiliar)

    2. Custo de disponibilidade (consumo zero ou abaixo do minimo) B3 = 100 kWh:
         CUSTO DE DISPONIBILIDADE  100  0,431849  43,18
       -> 100 / 100 / valor em R$ (sem TUSD separado)

    3. Fallback: nem TE nem disponibilidade encontrados -> zeros.
    """
    out: dict[str, float] = {}
    txt = _texto_normalizado(text)

    def _extract_line_numbers(label: str) -> tuple[float, float] | None:
        for raw_line in text.splitlines():
            line_norm = _texto_normalizado(raw_line)
            if label not in line_norm:
                continue
            line_clean = re.sub(r"(?<=\d)[A-Z](?=\d)", "", line_norm)
            number_tokens = re.findall(r"\d[\d\.]*,\d+|\d[\d\.]*", line_clean)
            if len(number_tokens) < 3:
                continue
            qtd = abs(_to_float_br(number_tokens[0]))
            money_candidates: list[float] = []
            for token in number_tokens[1:]:
                if "," not in token:
                    continue
                parsed = abs(_to_float_br(token))
                if parsed > 0:
                    money_candidates.append(parsed)
            if not money_candidates:
                continue
            repeated = [value for value in money_candidates if money_candidates.count(value) > 1]
            val = repeated[0] if repeated else money_candidates[0]
            if qtd > 0 and val > 0:
                return qtd, val
        return None

    # Caso 1 — TE
    m_te = re.search(
        r"CONSUMO\s+TE\s*\(?KWH\)?\s+([\d\.,]+)\s+([\d\.,]+)\s+([\d\.,]+)",
        txt,
    )
    val_te = 0.0
    qtd = 0.0
    if m_te:
        qtd    = abs(_to_float_br(m_te.group(1)))
        val_te = abs(_to_float_br(m_te.group(3)))
    else:
        parsed_te = _extract_line_numbers("CONSUMO TE")
        if parsed_te:
            qtd, val_te = parsed_te
    if qtd > 0 and val_te > 0:
        out["fatConFPontaIndRegistrado"] = qtd
        out["fatConFPontaIndFaturado"]   = qtd
        out["fatConFPontaIndValorReais"] = val_te

    # Caso 1 — TUSD
    m_tusd = re.search(
        r"CONSUMO\s+TU(?:SD)?\s*\(?KWH\)?\s+([\d\.,]+)\s+([\d\.,]+)\s+([\d\.,]+)",
        txt,
    )
    val_tusd = 0.0
    if m_tusd:
        qtd_tusd = abs(_to_float_br(m_tusd.group(1)))
        val_tusd = abs(_to_float_br(m_tusd.group(3)))
    else:
        parsed_tusd = _extract_line_numbers("CONSUMO TUSD")
        qtd_tusd = parsed_tusd[0] if parsed_tusd else 0.0
        val_tusd = parsed_tusd[1] if parsed_tusd else 0.0
    if val_tusd > 0 and qtd_tusd > 0:
        out["fatConFPontaCapValorReais"] = val_tusd
        out["fatConFPontaCapRegistrado"] = qtd_tusd
        out["fatConFPontaCapFaturado"]   = qtd_tusd
        if "fatConFPontaIndRegistrado" not in out:
            out["fatConFPontaIndRegistrado"] = qtd_tusd
            out["fatConFPontaIndFaturado"]   = qtd_tusd
            out["fatConFPontaIndValorReais"] = val_tusd

    # Caso 2 — Custo de disponibilidade (sem TE/TUSD)
    if "fatConFPontaIndRegistrado" not in out:
        m_disp = re.search(
            r"CUSTO\s+(?:DE\s+)?DISPONIBILIDADE\s+([\d\.,]+)\s+([\d\.,]+)\s+([\d\.,]+)",
            txt,
        )
        if not m_disp:
            # fallback: pode estar em multiplas colunas — pega linha com DISPONIBILIDADE e 3 numeros
            m_disp = re.search(
                r"DISPONIBILIDADE[^\n]*?([\d\.,]+)\s+([\d\.,]+)\s+([\d\.,]+)",
                txt,
            )
        if m_disp:
            val_disp = abs(_to_float_br(m_disp.group(3)))
            out["fatConFPontaIndRegistrado"] = 100.0
            out["fatConFPontaIndFaturado"]   = 100.0
            out["fatConFPontaIndValorReais"] = val_disp

    return out


def _extract_aliquotas_ceee(text: str) -> dict[str, float]:
    """
    Bloco de tributos (coluna direita):
      ICMS  5.334,47  17,0000  906,86
      PIS   4.427,62   0,7453   32,99
      COFINS 4.427,62  3,4583  153,12
    Padrao: LABEL base aliquota valor

    Tambem deriva:
      fatValorNotaFiscal = base PIS/COFINS (convenção adotada nos outros fluxos)
      Os campos _base nao existem em HEADERS mas sao usados internamente.
    """
    out: dict[str, float] = {
        "fatDescPisAliquota":    0.0,
        "fatDesCofinsAliquota":  0.0,
        "fatDesIcmsAliquota":    0.0,
        "_base_icms":            0.0,  # uso interno — nao gravado no xlsx
        "_base_pis_cofins":      0.0,  # uso interno — nao gravado no xlsx
    }
    txt = _texto_normalizado(text)

    m_icms = re.search(r"\bICMS\s+([\d\.,]+)\s+([\d\.,]+)\s+([\d\.,]+)", txt)
    if m_icms:
        out["fatDesIcmsAliquota"] = abs(_to_float_br(m_icms.group(2)))
        out["_base_icms"]         = abs(_to_float_br(m_icms.group(1)))

    m_pis = re.search(r"\bPIS\s+([\d\.,]+)\s+([\d\.,]+)\s+([\d\.,]+)", txt)
    if m_pis:
        out["fatDescPisAliquota"] = abs(_to_float_br(m_pis.group(2)))
        out["_base_pis_cofins"]   = abs(_to_float_br(m_pis.group(1)))

    m_cof = re.search(r"\bCOFINS\s+([\d\.,]+)\s+([\d\.,]+)\s+([\d\.,]+)", txt)
    if m_cof:
        out["fatDesCofinsAliquota"] = abs(_to_float_br(m_cof.group(2)))
        # PIS e COFINS tem mesma base; usa COFINS como fallback se PIS nao capturou
        if out["_base_pis_cofins"] == 0.0:
            out["_base_pis_cofins"] = abs(_to_float_br(m_cof.group(1)))

    return out


def _extract_ilum_publica_ceee(text: str) -> float:
    """CEEE: 'CIP - IP Pref Munic  33,46' ou 'Cip-Ilum Pub Pref Munic  8,06'."""
    txt = _texto_normalizado(text)
    m = re.search(r"CIP\s*[-–]\s*IP\s+PREF\s+MUNIC\s+([\d\.,]+)", txt)
    if m:
        return abs(_to_float_br(m.group(1)))
    m = re.search(r"CIP[-–]ILUM\s+PUB\s+PREF\s+MUNIC\s+([\d\.,]+)", txt)
    if m:
        return abs(_to_float_br(m.group(1)))
    return _extract_ilum_publica(text)


def _extract_retencoes_ceee(text: str) -> dict[str, float]:
    """
    Formato CEEE antigo: 'Tributo A Reter Csll   53,34-'  (sinal após valor)
    Formato CEEE novo  : 'Tributo a Reter CSLL  -47,55'   (sinal antes do valor)
    """
    out = {
        "fatDescCsllPercRetImposto":   0.0,
        "fatDescCsllValRetImposto":    0.0,
        "fatDescIrpjPercRetImposto":   0.0,
        "fatDescIrpjValRetImposto":    0.0,
        "fatDescCofinsPercRetImposto": 0.0,
        "fatDescCofinsValRetImposto":  0.0,
        "fatDescPisPercRetImposto":    0.0,
        "fatDescPisValRetImposto":     0.0,
    }
    mapa = {
        "CSLL":   ("fatDescCsllPercRetImposto",   "fatDescCsllValRetImposto",   1.0),
        "IRPJ":   ("fatDescIrpjPercRetImposto",   "fatDescIrpjValRetImposto",   1.2),
        "COFINS": ("fatDescCofinsPercRetImposto",  "fatDescCofinsValRetImposto", 3.0),
        "PIS":    ("fatDescPisPercRetImposto",     "fatDescPisValRetImposto",    0.65),
    }
    txt = _texto_normalizado(text)
    for label, (campo_perc, campo_val, perc) in mapa.items():
        m = re.search(
            rf"TRIBUTO A RETER\s+{label}\s+-?([\d\.,]+)-?",
            txt,
        )
        if m:
            out[campo_perc] = perc
            out[campo_val]  = -abs(_to_float_br(m.group(1)))
    return out


def _extract_multas_ceee(text: str) -> float:
    """
    Soma Multa + Correcao Monetaria + Juros → fatMultas (multas por atraso).
    Formato CEEE: '<LABEL>  <valor>'
    """
    txt = _texto_normalizado(text)
    total = 0.0
    padroes = [
        r"MULTA\s+([\d\.,]+)",
        r"MULTA POR ATRASO[^\d]*([\d\.,]+)",
        r"MULTA MORATORIA[^\d]*([\d\.,]+)",
        r"CORR(?:ECAO|\.)\s+MONETARIA[^\d]*([\d\.,]+)",
        r"CORRECAO MONETARIA[^\d]*([\d\.,]+)",
        r"JUROS(?:\s+(?:DE\s+)?MORA)?[^\d]*([\d\.,]+)",
        r"JUROS POR ATRASO[^\d]*([\d\.,]+)",
    ]
    vistos = set()
    for pat in padroes:
        m = re.search(pat, txt)
        if m and m.start() not in vistos:
            vistos.add(m.start())
            total += abs(_to_float_br(m.group(1)))
    return round(total, 2)


def _extract_bandeira_ceee(text: str) -> float:
    """Adicional de Bandeira CEEE: 'ADICIONAL BANDEIRA {cor?} XX,XX ...'

    pdfplumber extrai a página como uma única linha longa, então 'nums[-1]'
    pegaria o total do boleto ao final. Usa regex ancorado logo após a keyword
    para capturar somente o primeiro valor monetário do item.
    """
    txt = _texto_normalizado(text)
    total = 0.0
    for m in re.finditer(r"ADICIONAL\s+BANDEIRA|ADIC(?:IONAL)?\.?\s+BAND\.", txt):
        if "CRED" in txt[max(0, m.start() - 10):m.start()] or "DEVOL" in txt[max(0, m.start() - 10):m.start()]:
            continue
        snippet = txt[m.end():m.end() + 120]
        nums = re.findall(r"[\d.]+,\d{2}", snippet)
        if nums:
            v = abs(_to_float_br(nums[0]))
            if v >= 0.01:
                total += v
    return round(total, 2)


def _extract_ajuste_consumo_ceee(text: str) -> float:
    """'Ajuste de consu. Anterior N de M' → valor R$ para obs 259 (acerto de faturamento)."""
    txt = _texto_normalizado(text)
    total = 0.0
    for line in txt.splitlines():
        if not re.search(r"AJUSTE\s+DE\s+CONS", line):
            continue
        nums = re.findall(r"-?\d[\d.]*,\d{2}", line)
        if nums:
            v = abs(_to_float_br(nums[-1]))
            if v >= 0.01:
                total += v
    return round(total, 2)


def _extract_parcelamento_ceee(text: str) -> float:
    """
    Parcelas / Parc. → observacao cod 201 no Consen.
    Formato CEEE: 'PARCELA <N>/<T>  <valor>' ou 'PARC.  <valor>'
    """
    txt = _texto_normalizado(text)
    m = re.search(r"PARC(?:ELA)?\.?\s+[\d/]*\s*([\d\.,]+)", txt)
    return abs(_to_float_br(m.group(1))) if m else 0.0


def _extract_endereco_ceee(text: str) -> str:
    """Endereço do cliente fica apos a linha de leituras."""
    txt = _texto_normalizado(text)
    linhas = [l.strip() for l in txt.splitlines() if l.strip()]
    for i, line in enumerate(linhas):
        if re.search(r"LEITURAS\s+\d{2}/\d{2}/\d{4}", line):
            partes = []
            for l in linhas[i + 1 : i + 6]:
                if any(k in l for k in ("NOTA FISCAL", "DATA DE EMISSAO", "CONSULTE", "CNPJ")):
                    break
                if re.fullmatch(r"[\d./-]{6,}", l):
                    continue
                partes.append(l)
            return _norm(" ".join(partes))
    return ""


def identificacao_rapida(pdf_path: Path) -> dict:
    resultado = {"sistema": "DESCONHECIDA", "instalacao": "", "mes_ref": "", "grupo": "B"}
    try:
        text = _first_page_text(pdf_path)
        if not text or not _is_ceee(text):
            return resultado
        resultado["sistema"]    = "CEEE"
        resultado["instalacao"] = _extract_instalacao_ceee(text)
        ref = _extract_referencia_ceee(text, dt.date.today().month, dt.date.today().year)
        resultado["mes_ref"]    = ref.strftime("%m-%Y")
        resultado["grupo"]      = "B"  # sempre BT neste OCR
    except Exception as exc:
        log.warning("  identificacao_rapida %s: %s", pdf_path.name, exc)
    return resultado


def processar_pdf_direto(pdf_path: Path, mes_padrao: int, ano_padrao: int) -> dict:
    rec = _empty_record()
    rec["ARQUIVO"]        = pdf_path.name
    # CEEE nao usa prefixo BB_ — usa o stem do arquivo como carimbo provisorio.
    # _carimbo_do_nome trunca nomes com 7 digitos; usamos o stem diretamente.
    stem = pdf_path.stem
    m_bb = re.search(r"[Bb][Bb]_(\d+)", stem)
    rec["fatCarimbo"] = m_bb.group(0) if m_bb else stem
    rec["fatDataCadastro"] = dt.date.today()
    rec["concCod"]        = "CEEE"

    try:
        text, _ = _extract_pdf_data(pdf_path)
    except Exception as exc:
        rec["ERRO"] = f"{type(exc).__name__}: {exc}"
        return rec

    if not text.strip():
        rec["ERRO"] = "PDF sem texto extraivel"
        return rec
    if not _is_ceee(text):
        rec["ERRO"] = "Nao identificado como CEEE"
        return rec

    rec["cadTarifaCod"]   = "Convencional"
    rec["cadSubGrupoCod"] = "B3 [<2,3kV]"
    rec["TARIFA_DETECTADA"] = "B3"

    rec["Instalacao"]     = _extract_instalacao_ceee(text)
    rec["CODIGOCLIENTE"]  = rec["Instalacao"]
    rec["ENDERECO"]       = _extract_endereco_ceee(text)
    rec["NOTAFISCAL"]     = _extract_notafiscal_ceee(text)
    rec["CNPJ"]           = ""

    rec["fatDataEmissao"]       = _extract_data_emissao_ceee(text)
    leitura_ant, leitura_atu, vencimento = _extract_datas_ceee(text)
    rec["fatDataLeituraAnterior"] = leitura_ant
    rec["fatDataLeituraAtual"]    = leitura_atu
    rec["fatDataVcto"]            = vencimento
    rec["fatDataReferencia"]      = _extract_referencia_ceee(text, mes_padrao, ano_padrao)

    rec["fatValorFatura"]  = _extract_total_ceee(text)
    rec["fatIlumPublica"]  = _extract_ilum_publica_ceee(text)
    rec["fatICMS"]         = abs(_extract_imposto(text, "ICMS"))
    rec["fatPIS"]          = abs(_extract_imposto(text, "PIS"))
    rec["fatCOFINS"]       = abs(_extract_imposto(text, "COFINS"))

    aliquotas = _extract_aliquotas_ceee(text)
    base_pis_cofins = aliquotas.pop("_base_pis_cofins", 0.0)
    aliquotas.pop("_base_icms", None)
    rec.update(aliquotas)

    # fatValorNotaFiscal = base PIS/COFINS (convenção dos outros fluxos neo/energisa)
    rec["fatValorNotaFiscal"] = base_pis_cofins if base_pis_cofins > 0 else _extract_total_ceee(text)

    rec["Debitos anteriores"] = _extract_debitos_anteriores(text)
    rec.update(_extract_retencoes_ceee(text))

    # Multa + Correcao Monetaria + Juros → fatMultas (multas por atraso)
    multas = _extract_multas_ceee(text)
    if multas > 0:
        rec["fatMultas"] = multas

    rec["fatValBandeira"] = _extract_bandeira_ceee(text)

    # Parcelamento → observacao cod 201
    parc = _extract_parcelamento_ceee(text)
    if parc > 0:
        rec["obsCod_1"]   = "201"
        rec["obsValor_1"] = parc

    # Ajuste de consumo anterior → obs 259 (acerto de faturamento)
    ajuste = _extract_ajuste_consumo_ceee(text)
    if ajuste > 0:
        slot = 2 if parc > 0 else 1
        rec[f"obsCod_{slot}"]   = "259"
        rec[f"obsValor_{slot}"] = ajuste

    consumo = _extract_consumo_ceee(text)
    # Regra de soma: fatConFPontaIndValorReais = TE + TUSD (consumo total R$ para digitação)
    val_te   = consumo.get("fatConFPontaIndValorReais", 0.0)
    val_tusd = consumo.get("fatConFPontaCapValorReais", 0.0)
    if val_te > 0 and val_tusd > 0:
        consumo["fatConFPontaIndValorReais"] = round(val_te + val_tusd, 2)
    rec.update(consumo)

    codigo_barras = _extract_codigo_barras(text)
    rec["fatCodigoBarras"] = _digits(codigo_barras) if len(_digits(codigo_barras)) >= 44 else ""

    rec["ERRO"] = ""
    return rec


def _listar_pdfs(pasta: Path, carimbos: set[str]) -> list[Path]:
    pdfs = sorted(p for p in pasta.glob("*.pdf") if p.is_file())
    if carimbos:
        norm = {str(c).strip().upper() for c in carimbos}
        pdfs = [p for p in pdfs if p.stem.upper() in norm]
    return pdfs


def _xlsx_saida(mes: int, ano: int) -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    return OUTPUT_DIR / f"ocr_ceee_BT_{mes:02d}{ano}.xlsx"


def parse_args() -> argparse.Namespace:
    hoje = dt.date.today()
    parser = argparse.ArgumentParser(description="OCR CEEE BT -> XLSX")
    parser.add_argument("--mes",    type=int, default=hoje.month, help="Mes de referencia padrao")
    parser.add_argument("--ano",    type=int, default=hoje.year,  help="Ano de referencia padrao")
    parser.add_argument("--pasta",  type=str, default=str(DEFAULT_PASTA), help="Pasta com PDFs")
    parser.add_argument("--saida",  type=str, default="", help="XLSX de saida (opcional)")
    parser.add_argument("--carimbo", action="append", default=[], help="Filtrar por carimbo BB_XXXXXXX")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    pasta    = Path(str(args.pasta).strip())
    carimbos = {str(c).strip().upper() for c in (args.carimbo or []) if str(c).strip()}

    if not pasta.exists():
        log.error("Pasta nao encontrada: %s", pasta)
        return 1

    pdfs = _listar_pdfs(pasta, carimbos)
    if not pdfs:
        log.warning("Nenhum PDF encontrado para o filtro informado.")
        return 0

    log.info("=" * 64)
    log.info("  OCR CEEE BT")
    log.info("=" * 64)
    log.info("  Pasta : %s", pasta)
    log.info("  PDFs candidatos: %d", len(pdfs))

    registros: list[dict] = []
    ignorados = 0
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futuros = [
            executor.submit(processar_pdf_direto, pdf, int(args.mes), int(args.ano))
            for pdf in pdfs
        ]
        for futuro in as_completed(futuros):
            rec = futuro.result()
            if rec.get("ERRO") == "Nao identificado como CEEE":
                ignorados += 1
                continue
            registros.append(rec)

    registros.sort(key=lambda r: str(r.get("fatCarimbo", "")))
    if not registros:
        log.warning("Nenhuma fatura CEEE BT extraida.")
        return 0

    destino = (
        Path(str(args.saida).strip())
        if str(args.saida).strip()
        else _xlsx_saida(int(args.mes), int(args.ano))
    )
    try:
        salvar_excel(registros, destino, titulo="OCR_CEEE_BT")
    except Exception as exc:
        log.error("Falha ao salvar XLSX: %s", exc)
        return 1

    ok    = sum(1 for r in registros if not r.get("ERRO"))
    erro  = len(registros) - ok
    log.info("  XLSX salvo: %s", destino)
    log.info("  Resumo: total=%d ok=%d erro=%d ignorados=%d", len(registros), ok, erro, ignorados)
    return 0 if erro == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
