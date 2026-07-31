#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OCR Energisa MT (A4) -> XLSX para digitacao no Consen.

Baseado em ocr_energisa_bt.py, com extratores adicionais para:
  - Demanda contratada ponta/fora ponta
  - Demanda registrada e faturada ponta/fora ponta
  - Consumo ponta e fora ponta separados
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
    _carimbo_do_nome,
    _empty_record,
    _extract_codigo_barras,
    _extract_debitos_anteriores,
    _extract_ilum_publica,
    _extract_pdf_data,
    _extract_tributo_federal,
    _norm,
    _texto_normalizado,
    _to_date,
    _to_float_br,
    salvar_excel,
)
from ocr.ocr_energisa_bt import (
    OUTPUT_DIR as BT_OUTPUT_DIR,
    DEFAULT_PASTA,
    _arquivo_original_por_carimbo,
    _digits,
    _extract_bandeira_energisa,
    _extract_codigo_cliente_energisa,
    _extract_data_emissao,
    _extract_datas,
    _extract_endereco_energisa,
    _extract_gdi_energisa,
    _extract_instalacao_energisa,
    _extract_multas_diversas_energisa,
    _extract_notafiscal_energisa,
    _extract_referencia,
    _extract_retencoes_energisa,
    _extract_total_energisa,
    _extract_vencimento_energisa,
    _is_energisa,
    _listar_pdfs,
    _normalizar_codigo_energisa,
    _ucs_do_nome,
)

OUTPUT_DIR = BT_OUTPUT_DIR.parent / "OCR ENERGISA"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("ocr_energisa_mt")


def _detectar_subgrupo(text: str) -> str:
    txt = _texto_normalizado(text)
    for sg in ("A4", "A3A", "A3", "A2", "A1"):
        if sg in txt:
            return sg
    if "MEDIA TENSAO" in txt or "GRUPO A" in txt:
        return "A4"
    return "A4"



def _floats_da_linha(line: str) -> list[float]:
    nums = re.findall(r"-?[\d\.,]+", line)
    result = []
    for n in nums:
        try:
            result.append(_to_float_br(n))
        except Exception:
            pass
    return result


def _linha_kw(ln_norm: str, prefixo: str) -> bool:
    s = ln_norm.strip()
    return s == prefixo or s.startswith(prefixo + " ") or s.startswith(prefixo + "\t")


def _extract_demanda_contratada(text: str) -> tuple[float, float]:
    """Retorna (ponta, fora_ponta) da demanda contratada."""
    txt = _texto_normalizado(text)

    ponta = 0.0
    fp = 0.0

    # "Demanda ponta - kW 135" ou embutido em linha maior
    m_p = re.search(r"DEMANDA\s+PONTA\s*[-–\-]\s*KW\s+([\d\.,]+)", txt)
    m_fp = re.search(r"DEMANDA\s+FORA\s+PONTA\s*[-–\-]\s*KW\s+([\d\.,]+)", txt)
    if m_p:
        ponta = _to_float_br(m_p.group(1))
    if m_fp:
        fp = _to_float_br(m_fp.group(1))

    if ponta or fp:
        return ponta, fp

    # Tabela: "KW Ponta ... <registrado> <contratado>"
    for line in text.splitlines():
        ln = _texto_normalizado(line)
        floats = _floats_da_linha(line)
        if _linha_kw(ln, "KW PONTA") and len(floats) >= 2 and not ponta:
            ponta = floats[-1]
        elif _linha_kw(ln, "KW FPONTA") and len(floats) >= 2 and not fp:
            fp = floats[-1]

    return ponta, fp


def _extract_demanda_registrada(text: str) -> tuple[float, float]:
    """Retorna (ponta, fora_ponta) da demanda registrada (medida)."""
    ponta = 0.0
    fp = 0.0

    for line in text.splitlines():
        ln = _texto_normalizado(line)
        floats = _floats_da_linha(line)
        if _linha_kw(ln, "KW PONTA") and len(floats) >= 2 and not ponta:
            # penúltimo = registrado, último = contratado
            ponta = floats[-2] if len(floats) >= 2 else floats[-1]
        elif _linha_kw(ln, "KW FPONTA") and len(floats) >= 2 and not fp:
            fp = floats[-2] if len(floats) >= 2 else floats[-1]

    return ponta, fp


def _extract_demanda_faturada(text: str) -> tuple[float, float]:
    """Retorna (ponta, fora_ponta) da demanda faturada a partir dos itens da fatura."""
    ponta = 0.0
    fp = 0.0
    txt = _texto_normalizado(text)

    # "Demanda de Potência Medida - Ponta KW <qtd> ..."
    # "Demanda de Potência Medida - Fora Ponta KW <qtd> ..."
    m_p = re.search(
        r"DEMANDA\s+DE\s+POT[EÊ]NCIA\s+MEDIDA\s*[-–]\s*PONTA\s+KW\s+([\d\.,]+)",
        txt,
    )
    m_fp = re.search(
        r"DEMANDA\s+DE\s+POT[EÊ]NCIA\s+MEDIDA\s*[-–]\s*FORA\s+PONTA\s+KW\s+([\d\.,]+)",
        txt,
    )
    if m_p:
        ponta = _to_float_br(m_p.group(1))
    if m_fp:
        fp = _to_float_br(m_fp.group(1))

    if ponta or fp:
        return ponta, fp

    # Fallback: use registrada as faturada
    return _extract_demanda_registrada(text)


def _extract_consumo_mt(text: str) -> dict[str, float]:
    """Extrai consumo ponta e fora ponta para MT."""
    out: dict[str, float] = {}
    txt = _texto_normalizado(text)

    # --- Formato A: "Consumo em kWh - Ponta KWH <qtd> <tarifa> <valor>" ---
    # Usado por Energisa SE, GO e outros layouts com KWH explícito após PONTA.
    m_p_a = re.search(
        r"CONSUMO\s+EM\s+KWH\s*[-–]\s*PONTA\s+KWH\s+([\d\.,]+)\s+[\d\.,]+\s+([\d\.,]+)",
        txt,
    )
    # --- Formato B: "Consumo em kWh - Ponta <tarifa_6dec> <valor_2dec>" ---
    # CERON/Energisa Rondônia: tarifa vem antes do valor, sem campo KWH/qtd na linha.
    # A tarifa tem exatamente 6 casas decimais (ex: 2,715320), o valor tem 2 (ex: 2.359,00).
    # O regex \d+,\d{6} distingue a tarifa (6 dec) de qualquer quantidade kWh (2 dec).
    m_p_b = re.search(
        r"CONSUMO\s+EM\s+KWH\s*[-–]\s*PONTA\s+\d+[,\.]\d{6}\s+([\d\.]+,\d{2})",
        txt,
    )
    # --- Formato C: "Consumo em kWh - Ponta <qty_2dec> <tarifa_6dec> <valor_2dec>" ---
    # DANF3E MS/SE: quantidade (2 dec) precede tarifa (6 dec), sem "KWH" após "Ponta".
    m_p_c = None
    if not m_p_a and not m_p_b:
        m_p_c = re.search(
            r"CONSUMO\s+EM\s+KWH\s*[-–]\s*PONTA\s+([\d\.]+,\d{2})\s+\d+[,\.]\d{6}\s+([\d\.]+,\d{2})",
            txt,
        )

    if m_p_a:
        qtd = _to_float_br(m_p_a.group(1))
        val = _to_float_br(m_p_a.group(2))
        out["fatConPontaRegistrado"] = qtd
        out["fatConPontaFaturado"] = qtd
        out["fatConPontaValorReais"] = val
    elif m_p_b:
        val = _to_float_br(m_p_b.group(1))
        out["fatConPontaValorReais"] = val
    elif m_p_c:
        qtd = _to_float_br(m_p_c.group(1))
        val = _to_float_br(m_p_c.group(2))
        out["fatConPontaRegistrado"] = qtd
        out["fatConPontaFaturado"] = qtd
        out["fatConPontaValorReais"] = val

    # --- Formato A: "Consumo em kWh - Fora Ponta KWH <qtd> <tarifa> <valor>" ---
    m_fp_a = re.search(
        r"CONSUMO\s+EM\s+KWH\s*[-–]\s*FORA\s+PONTA\s+KWH\s+([\d\.,]+)\s+[\d\.,]+\s+([\d\.,]+)",
        txt,
    )
    # --- Formato B: "Consumo em kWh - Fora Ponta <qtd_2dec> <tarifa_6dec> <valor_2dec>" ---
    # CERON: quantidade kWh (2 dec) → tarifa (6 dec) → valor R$ (2 dec).
    # DANF3E: tarifa pode usar "." como separador decimal (ex: 0.520980).
    m_fp_b = re.search(
        r"CONSUMO\s+EM\s+KWH\s*[-–]\s*FORA\s+PONTA\s+([\d\.]+,\d{2})\s+\d+[,\.]\d{6}\s+([\d\.]+,\d{2})",
        txt,
    )

    if m_fp_a:
        qtd = _to_float_br(m_fp_a.group(1))
        val = _to_float_br(m_fp_a.group(2))
        out["fatConFPontaIndRegistrado"] = qtd
        out["fatConFPontaIndFaturado"] = qtd
        out["fatConFPontaIndValorReais"] = val
    elif m_fp_b:
        qtd = _to_float_br(m_fp_b.group(1))
        val = _to_float_br(m_fp_b.group(2))
        out["fatConFPontaIndRegistrado"] = qtd
        out["fatConFPontaIndFaturado"] = qtd
        out["fatConFPontaIndValorReais"] = val

    # Fallback para formato Energisa SE/GO: "TUSD EM KWH - PONTA/FORA PONTA"
    # Energisa SE não tem linha "CONSUMO EM KWH" — o consumo vem como TUSD EM KWH
    if not out.get("fatConPontaRegistrado"):
        m_tusd_p = re.search(
            r"TUSD\s+EM\s+KWH\s*[-–]\s*PONTA\s+KWH\s+([\d\.,]+)\s+[\d\.,]+\s+([\d\.,]+)",
            txt,
        )
        if m_tusd_p:
            qtd = _to_float_br(m_tusd_p.group(1))
            val = _to_float_br(m_tusd_p.group(2))
            out["fatConPontaRegistrado"] = qtd
            out["fatConPontaFaturado"] = qtd
            out["fatConPontaValorReais"] = val

    if not out.get("fatConFPontaIndRegistrado"):
        m_tusd_fp = re.search(
            r"TUSD\s+EM\s+KWH\s*[-–]\s*FORA\s+PONTA\s+KWH\s+([\d\.,]+)\s+[\d\.,]+\s+([\d\.,]+)",
            txt,
        )
        if m_tusd_fp:
            qtd = _to_float_br(m_tusd_fp.group(1))
            val = _to_float_br(m_tusd_fp.group(2))
            out["fatConFPontaIndRegistrado"] = qtd
            out["fatConFPontaIndFaturado"] = qtd
            out["fatConFPontaIndValorReais"] = val

    # Fallback para o quadro "FATURAMENTO PELA MEDIA/MINIMO", em que o valor
    # em reais aparece no inicio da linha e o consumo registrado/faturado no fim.
    if not out:
        for line in text.splitlines():
            ln = _texto_normalizado(line)
            floats = _floats_da_linha(line)
            if "KWH PONTA" in ln and len(floats) >= 4:
                out["fatConPontaValorReais"] = abs(floats[0])
                out["fatConPontaRegistrado"] = abs(floats[2])
                out["fatConPontaFaturado"] = abs(floats[-1])
            elif "KWH FPONTA" in ln and len(floats) >= 4:
                out["fatConFPontaIndValorReais"] = abs(floats[0])
                out["fatConFPontaIndRegistrado"] = abs(floats[2])
                out["fatConFPontaIndFaturado"] = abs(floats[-1])

        if out:
            return out

    # Fallback: "Consumo em kWh KWH <qtd>" (sem separação ponta/fp — trata como fp)
    if not out:
        for line in text.splitlines():
            ln = _texto_normalizado(line)
            if "CONSUMO EM KWH" not in ln:
                continue
            m = re.search(r"KWH\s+([\d\.,]+)\s+[\d\.,]+\s+([\d\.,]+)", line, flags=re.IGNORECASE)
            if m:
                qtd = abs(_to_float_br(m.group(1)))
                val = abs(_to_float_br(m.group(2)))
                out["fatConFPontaIndRegistrado"] = qtd
                out["fatConFPontaIndFaturado"] = qtd
                out["fatConFPontaIndValorReais"] = val
                break

    return out


def _extract_texto_bruto_mt(pdf_path: Path) -> str:
    partes: list[str] = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        for page in pdf.pages:
            partes.append(page.extract_text(x_tolerance=1, y_tolerance=1) or "")
    return "\n".join(partes)


def _resolver_pdf_base_mt(pdf_path: Path, original_pdf: Path | None) -> Path:
    if original_pdf:
        try:
            if Path(original_pdf).exists():
                return Path(original_pdf)
        except Exception:
            pass
    return pdf_path


def _extract_item_valor_mt(line: str) -> tuple[float, float] | None:
    line_norm = _texto_normalizado(line)
    if not line_norm or any(
        bloqueio in line_norm
        for bloqueio in (
            "CONTRIBUICAO ILUM PUBLICA",
            "CONTRIB DE ILUM PUB",
            "IMPOSTO RENDA",
            "CONT. SOCIAL",
        )
    ):
        return None

    match = re.search(
        r"\b(KWH|KW)\b\s+([-\d\.,]+)\s+[-\d\.,]+\s+([-\d\.,]+)",
        line,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    qtd = abs(_to_float_br(match.group(2)))
    valor = abs(_to_float_br(match.group(3)))
    if not qtd and not valor:
        return None
    return qtd, valor


_DEMANDA_ULTRAP = (
    "ULTRAPASSAGEM", "ULTRAP",
)
_DEMANDA_NAO_CONSUMIDA = (
    "N CONSUMIDA", "N. CONSUMIDA", "NAO CONSUMIDA", "NAO CONS", "N CONS",
)


def _extract_demanda_valores_mt(text: str) -> dict[str, float]:
    out: dict[str, float] = {}
    for line in text.splitlines():
        line_norm = _texto_normalizado(line)
        if not re.search(r"\bKW\b", line_norm):
            continue
        if "KWH" in line_norm:
            continue
        if "TUSD EM KW" not in line_norm and "DEMANDA" not in line_norm:
            continue

        item = _extract_item_valor_mt(line)
        if not item:
            continue
        qtd, valor = item

        is_fp = (
            "FORA PONTA" in line_norm
            or "F. PONTA" in line_norm
            or "F PONTA" in line_norm
            or "FPONTA" in line_norm
        )
        is_ultrap = any(u in line_norm for u in _DEMANDA_ULTRAP)
        is_nao_consumida = any(n in line_norm for n in _DEMANDA_NAO_CONSUMIDA)

        if is_ultrap:
            # Ultrapassagem: demanda acima do contratado (penalidade)
            if is_fp:
                if not out.get("fatDemFPontaIndUltra"):
                    out["fatDemFPontaIndUltra"] = qtd
                    out["fatDemFPontaIndUltraValorReais"] = valor
            else:
                if not out.get("fatDemPontaUltra"):
                    out["fatDemPontaUltra"] = qtd
                    out["fatDemPontaUltraValorReais"] = valor
        elif is_fp:
            if not out.get("fatDemFPontaIndFaturada"):
                out["fatDemFPontaIndFaturada"] = qtd
                out["fatDemFPontaIndValorReais"] = valor
        elif "PONTA" in line_norm:
            if not out.get("fatDemPontaFaturada"):
                out["fatDemPontaFaturada"] = qtd
                out["fatDemPontaValorReais"] = valor

    return out


def _extract_reativa_excedente_fp_mt(text: str) -> dict[str, float]:
    out: dict[str, float] = {}
    for line in text.splitlines():
        line_norm = _texto_normalizado(line)
        if not line_norm:
            continue

        is_reativa_fp = (
            ("REAT" in line_norm and "EXCED" in line_norm and "FPONTA" in line_norm)
            or ("ERE FPONTA" in line_norm)
            or ("UFER" in line_norm and "FPONTA" in line_norm)
        )
        if not is_reativa_fp:
            continue

        numeros = [abs(_to_float_br(n)) for n in re.findall(r"-?[\d\.,]+", line)]
        if len(numeros) < 3:
            continue

        qtd = numeros[0]
        valor = numeros[2]
        if not qtd:
            continue

        out["fatConFPontaIndExcRegistrado"] = qtd
        out["fatConFPontaIndExcFaturado"] = qtd
        if valor:
            out["fatConFPontaIndExcValorReais"] = valor
        break

    return out


def _extract_impostos_aliquotas_mt(text: str) -> dict[str, float]:
    out = {
        "fatICMS": 0.0,
        "fatPIS": 0.0,
        "fatCOFINS": 0.0,
        "fatDescPisAliquota": 0.0,
        "fatDesCofinsAliquota": 0.0,
        "fatDesIcmsAliquota": 0.0,
    }
    mapa = {
        "PIS/PASEP": ("fatPIS", "fatDescPisAliquota"),
        "PIS": ("fatPIS", "fatDescPisAliquota"),
        "COFINS": ("fatCOFINS", "fatDesCofinsAliquota"),
        "ICMS": ("fatICMS", "fatDesIcmsAliquota"),
    }

    for line in text.splitlines():
        line_norm = _texto_normalizado(line)
        if not line_norm:
            continue
        if "( - )" in line_norm:
            continue
        if any(bloqueio in line_norm for bloqueio in ("IMP.RET.", "COBRANCA", "COBR.", "ESCASSEZ HIDRICA")):
            continue

        for label, (campo_valor, campo_aliq) in mapa.items():
            if not re.match(rf"^{re.escape(label)}\b", line_norm):
                continue

            numeros = [abs(_to_float_br(n)) for n in re.findall(r"-?[\d\.,]+", line)]
            if not numeros:
                continue

            if len(numeros) >= 2:
                if not out[campo_aliq]:
                    out[campo_aliq] = numeros[-2]
                if not out[campo_valor]:
                    out[campo_valor] = numeros[-1]
            elif not out[campo_aliq]:
                out[campo_aliq] = numeros[-1]

    text_norm = _texto_normalizado(text)
    fallbacks = {
        "fatPIS": (r"PIS(?:/PASEP)?\s+[\d\.,]+\s+([\d\.,]+)\s+([\d\.,]+)", "fatDescPisAliquota"),
        "fatCOFINS": (r"COFINS\s+[\d\.,]+\s+([\d\.,]+)\s+([\d\.,]+)", "fatDesCofinsAliquota"),
        "fatICMS": (r"ICMS\s+[\d\.,]+\s+([\d\.,]+)\s+([\d\.,]+)", "fatDesIcmsAliquota"),
    }
    for campo_valor, (pattern, campo_aliq) in fallbacks.items():
        if out[campo_valor] and out[campo_aliq]:
            continue
        match = re.search(pattern, text_norm, flags=re.IGNORECASE)
        if not match:
            continue
        if not out[campo_aliq]:
            out[campo_aliq] = abs(_to_float_br(match.group(1)))
        if not out[campo_valor]:
            out[campo_valor] = abs(_to_float_br(match.group(2)))

    return out


def _extract_valor_nota_fiscal_mt(text: str, valor_fatura: float) -> float:
    for line in text.splitlines():
        line_norm = _texto_normalizado(line)
        if not line_norm.startswith("TOTAL:"):
            continue
        numeros = [abs(_to_float_br(n)) for n in re.findall(r"-?[\d\.,]+", line)]
        if len(numeros) >= 3 and numeros[2] > 0:
            return numeros[2]

    candidatos: list[float] = []
    for line in text.splitlines():
        item = _extract_item_valor_mt(line)
        if not item:
            continue
        _, valor = item
        if valor > 0:
            candidatos.append(valor)

    if candidatos:
        return max(candidatos)
    return valor_fatura


def _extract_observacoes_debito_tusd_mt(text: str) -> list[tuple[str, float]]:
    totais = {"289": 0.0, "288": 0.0}
    for line in text.splitlines():
        line_norm = _texto_normalizado(line)
        if "DEBITO TUSD" not in line_norm and "DÉBITO TUSD" not in line:
            continue
        m_valor = re.search(r"\d{2}/\d{4}\s+(-?[\d\.,]+)", line)
        if not m_valor:
            continue
        valor = abs(_to_float_br(m_valor.group(1)))
        if not valor:
            continue
        if " KWH " in f" {line_norm} ":
            totais["288"] += valor
        elif " KW" in f" {line_norm} ":
            totais["289"] += valor

    return [(cod, round(val, 2)) for cod, val in totais.items() if val]


def _aplicar_observacoes(rec: dict, observacoes: list[tuple[str, float]]) -> None:
    slot = 1
    for cod, valor in observacoes:
        if slot > 5:
            break
        rec[f"obsCod_{slot}"] = str(cod)
        rec[f"obsValor_{slot}"] = round(valor, 2)
        slot += 1


def processar_pdf_mt(pdf_path: Path, mes_padrao: int, ano_padrao: int) -> dict:
    rec = _empty_record()
    rec["ARQUIVO"] = pdf_path.name
    rec["fatCarimbo"] = _carimbo_do_nome(pdf_path)
    rec["fatDataCadastro"] = dt.date.today()
    rec["concCod"] = "ENERGISA"

    try:
        text, first_page_words = _extract_pdf_data(pdf_path)
    except Exception as exc:
        rec["ERRO"] = f"{type(exc).__name__}: {exc}"
        return rec

    if not text.strip():
        rec["ERRO"] = "PDF sem texto extraivel"
        return rec
    if not _is_energisa(text):
        rec["ERRO"] = "Nao identificado como Energisa"
        return rec
    uc_antiga_nome, uc_nova_nome = _ucs_do_nome(pdf_path)
    original_pdf = None
    if not (uc_antiga_nome or uc_nova_nome) and str(pdf_path.stem).upper().startswith("BB_"):
        carimbo_norm = f"BB_{rec['fatCarimbo']}" if str(rec["fatCarimbo"]).isdigit() else str(rec["fatCarimbo"])
        original_pdf = _arquivo_original_por_carimbo(carimbo_norm)
        uc_antiga_nome, uc_nova_nome = _ucs_do_nome(original_pdf)
    pdf_base_mt = _resolver_pdf_base_mt(pdf_path, original_pdf)
    raw_text_mt = _extract_texto_bruto_mt(pdf_base_mt)

    subgrupo = _detectar_subgrupo(text)
    rec["cadTarifaCod"] = "HS - Verde"
    rec["cadSubGrupoCod"] = subgrupo
    rec["TARIFA_DETECTADA"] = subgrupo

    instalacao = _extract_instalacao_energisa(text, pdf_base_mt)
    if not instalacao:
        from ocr.ocr_neoenergia import _extract_instalacao
        instalacao = _normalizar_codigo_energisa(_extract_instalacao(text, first_page_words))
    rec["Instalacao"] = instalacao
    rec["CODIGOCLIENTE"] = _extract_codigo_cliente_energisa(text, instalacao)
    rec["ENDERECO"] = _extract_endereco_energisa(text)
    rec["NOTAFISCAL"] = _extract_notafiscal_energisa(text)
    rec["CNPJ"] = ""

    rec["fatDataEmissao"] = _extract_data_emissao(text)
    leitura_ant, leitura_atu, vencimento = _extract_datas(text)
    rec["fatDataLeituraAnterior"] = leitura_ant
    rec["fatDataLeituraAtual"] = leitura_atu
    rec["fatDataVcto"] = vencimento
    vencimento_explicit = _extract_vencimento_energisa(text)
    if vencimento_explicit:
        rec["fatDataVcto"] = vencimento_explicit
    rec["fatDataReferencia"] = _extract_referencia(text, mes_padrao, ano_padrao)

    rec["fatValorFatura"] = _extract_total_energisa(raw_text_mt or text)
    rec["fatIlumPublica"] = _extract_ilum_publica(raw_text_mt or text)
    rec.update(_extract_impostos_aliquotas_mt(raw_text_mt or text))
    rec["fatTributoFederalPerc"], rec["fatTributoFederalVal"] = _extract_tributo_federal(text)
    rec["Debitos anteriores"] = _extract_debitos_anteriores(text)
    rec.update(_extract_retencoes_energisa(text))
    # fatMultas = juros de mora / multa por atraso / atualização monetária (schema não tem fatMultasPorAtraso)
    rec["fatMultas"] = _extract_multas_diversas_energisa(text)
    rec["fatDescontoFio"] = 0.0
    rec["fatDescontoFioKWh"] = 0.0

    # Demanda
    dem_cont_p, dem_cont_fp = _extract_demanda_contratada(raw_text_mt or text)
    dem_reg_p, dem_reg_fp = _extract_demanda_registrada(raw_text_mt or text)
    dem_fat_p, dem_fat_fp = _extract_demanda_faturada(raw_text_mt or text)
    dem_valores = _extract_demanda_valores_mt(raw_text_mt or text)

    rec["fatDemContratadaPonta"]      = dem_cont_p
    rec["fatDemContratadaFPonta"]     = dem_cont_fp
    rec["fatDemPontaRegistrada"]      = dem_reg_p
    rec["fatDemFPontaIndRegistrada"]  = dem_reg_fp
    rec["fatDemPontaFaturada"]        = dem_valores.get("fatDemPontaFaturada", dem_fat_p)
    rec["fatDemFPontaIndFaturada"]    = dem_valores.get("fatDemFPontaIndFaturada", dem_fat_fp)
    rec["fatDemPontaValorReais"]      = dem_valores.get("fatDemPontaValorReais", 0.0)
    rec["fatDemFPontaIndValorReais"]  = dem_valores.get("fatDemFPontaIndValorReais", 0.0)
    rec["fatDemFPontaIndUltra"]           = dem_valores.get("fatDemFPontaIndUltra", 0.0)
    rec["fatDemFPontaIndUltraValorReais"] = dem_valores.get("fatDemFPontaIndUltraValorReais", 0.0)
    rec["fatDemPontaUltra"]               = dem_valores.get("fatDemPontaUltra", 0.0)
    rec["fatDemPontaUltraValorReais"]     = dem_valores.get("fatDemPontaUltraValorReais", 0.0)

    # Fallback DANF3E (layout de demanda única): quando registrada/faturada saem 0
    # mas a contratada fora-ponta foi extraída de "Demanda fora ponta - kW NN",
    # esse é o único valor de demanda da fatura — propaga para registrada e faturada
    # para não deixar os campos zerados no CONSEN.
    if (dem_cont_fp or 0) > 0:
        if not (rec.get("fatDemFPontaIndRegistrada") or 0):
            rec["fatDemFPontaIndRegistrada"] = dem_cont_fp
        if not (rec.get("fatDemFPontaIndFaturada") or 0):
            rec["fatDemFPontaIndFaturada"] = dem_cont_fp
    if (dem_cont_p or 0) > 0:
        if not (rec.get("fatDemPontaRegistrada") or 0):
            rec["fatDemPontaRegistrada"] = dem_cont_p
        if not (rec.get("fatDemPontaFaturada") or 0):
            rec["fatDemPontaFaturada"] = dem_cont_p

    # Consumo ponta/fora ponta
    rec.update(_extract_consumo_mt(raw_text_mt or text))
    rec.update(_extract_reativa_excedente_fp_mt(raw_text_mt or text))

    # Energia injetada (GDI). _extract_gdi_energisa retorna dict com kWh e R$.
    gdi = _extract_gdi_energisa(raw_text_mt or text)
    injetado_val = gdi.get("fatConFPontaInjetadoValorReais", 0.0) or 0.0
    rec["fatConFPontaInjetadoRegistrado"] = gdi.get("fatConFPontaInjetadoRegistrado", 0.0)
    rec["fatConFPontaInjetadoFaturado"]   = gdi.get("fatConFPontaInjetadoFaturado", 0.0)
    rec["fatConFPontaInjetadoUsina"]      = gdi.get("fatConFPontaInjetadoUsina", 0.0)
    if gdi.get("fatConFPontaInjetadoValorReais"):
        rec["fatConFPontaInjetadoValorReais"] = gdi["fatConFPontaInjetadoValorReais"]

    # Bandeira tarifária
    bandeira_val = _extract_bandeira_energisa(raw_text_mt or text)
    rec["fatValBandeira"] = bandeira_val

    rec["fatValorNotaFiscal"] = rec["fatValorFatura"]

    _aplicar_observacoes(rec, _extract_observacoes_debito_tusd_mt(raw_text_mt or text))

    codigo_barras = _extract_codigo_barras(text)
    if len(_digits(codigo_barras)) >= 44:
        rec["fatCodigoBarras"] = _digits(codigo_barras)
    else:
        rec["fatCodigoBarras"] = ""

    rec["ERRO"] = ""
    return rec


def _xlsx_saida(mes: int, ano: int) -> Path:
    return OUTPUT_DIR / f"ocr_energisa_MT_{mes:02d}{ano}.xlsx"


def parse_args() -> argparse.Namespace:
    hoje = dt.date.today()
    parser = argparse.ArgumentParser(description="OCR Energisa MT (A4) -> XLSX")
    parser.add_argument("--mes", type=int, default=hoje.month)
    parser.add_argument("--ano", type=int, default=hoje.year)
    parser.add_argument("--pasta", type=str, default=str(DEFAULT_PASTA))
    parser.add_argument("--saida", type=str, default="")
    parser.add_argument("--carimbo", action="append", default=[])
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    pasta = Path(str(args.pasta).strip())
    carimbos = {str(c).strip().upper() for c in args.carimbo or [] if str(c).strip()}

    if not pasta.exists():
        log.error("Pasta nao encontrada: %s", pasta)
        return 1

    pdfs = _listar_pdfs(pasta, carimbos)
    if not pdfs:
        log.warning("Nenhum PDF encontrado.")
        return 0

    log.info("=" * 64)
    log.info("OCR ENERGISA MT (A4)")
    log.info("=" * 64)
    log.info("Pasta : %s", pasta)
    log.info("PDFs candidatos: %d", len(pdfs))

    registros: list[dict] = []
    ignorados = 0
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futuros = [executor.submit(processar_pdf_mt, pdf, int(args.mes), int(args.ano)) for pdf in pdfs]
        for futuro in as_completed(futuros):
            rec = futuro.result()
            if rec.get("ERRO") == "Nao identificado como Energisa":
                ignorados += 1
                continue
            registros.append(rec)

    registros.sort(key=lambda r: str(r.get("fatCarimbo", "")))
    if not registros:
        log.warning("Nenhuma fatura Energisa MT extraida.")
        return 0

    destino = Path(str(args.saida).strip()) if str(args.saida).strip() else _xlsx_saida(int(args.mes), int(args.ano))
    try:
        salvar_excel(registros, destino, titulo="OCR_ENERGISA_MT")
    except Exception as exc:
        log.error("Falha ao salvar XLSX: %s", exc)
        return 1

    ok = sum(1 for r in registros if not r.get("ERRO"))
    erro = len(registros) - ok
    log.info("XLSX salvo: %s", destino)
    log.info("Resumo: total=%d ok=%d erro=%d ignorados=%d", len(registros), ok, erro, ignorados)
    return 0 if erro == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
