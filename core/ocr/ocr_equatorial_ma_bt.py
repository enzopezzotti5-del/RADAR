#!/usr/bin/env python3
from __future__ import annotations

import datetime as dt
import re
import sys
import unicodedata
from pathlib import Path

import pdfplumber

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from core.ocr.ocr_bt_cemig_adapter import main_bt_generico


def _br2f(value: str) -> float:
    txt = str(value or "").strip()
    if not txt:
        return 0.0
    txt = txt.replace(".", "").replace(",", ".")
    try:
        return abs(float(txt))
    except ValueError:
        return 0.0


def _to_date(value: str) -> dt.date | None:
    txt = str(value or "").strip().replace(".", "/")
    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            return dt.datetime.strptime(txt, fmt).date()
        except ValueError:
            continue
    return None


def _texto_pdf(pdf_path: Path, max_paginas: int = 2) -> str:
    partes: list[str] = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        for page in pdf.pages[:max_paginas]:
            txt = (
                page.extract_text(x_tolerance=1, y_tolerance=1)
                or page.extract_text(layout=True)
                or page.extract_text()
                or ""
            )
            if txt:
                partes.append(txt)
    return "\n".join(partes)


def _extract_instalacao(txt: str, fallback: str) -> str:
    patterns = (
        r"\b(\d\.\d{3}\.\d{3}\.\d{3}-\d{2})\b",
        r"\b(\d{3}\.\d{3}\.\d{3}-\d{2})\b",
    )
    for pattern in patterns:
        m = re.search(pattern, txt)
        if m:
            return m.group(1).strip()
    return fallback


def _extract_nf(txt: str) -> str:
    m = re.search(r"NOTA\s+FISCAL\s+N[º°O]?\s*(\d+)", txt, re.I)
    return m.group(1).strip() if m else ""


def _extract_emissao(txt: str) -> dt.date | None:
    m = re.search(r"DATA\s+DE\s+EMISS[ÃA]O:\s*(\d{2}/\d{2}/\d{4})", txt, re.I)
    return _to_date(m.group(1)) if m else None


_DATE_RE = re.compile(r"\b(\d{1,2}/\d{1,2}/\d{4})\b")
_LEITURA_HINTS = ("BANCO DO BRASIL", "LEITURA", "DATA LEITURA", "LEIT.", "PROXIMA LEITURA")


def _normalizar_texto_ocr(txt: str) -> str:
    txt = (txt or "").replace("\r", "\n")
    txt = re.sub(r"[\u00a0\u2007\u202f]", " ", txt)
    txt = re.sub(r"[ \t]+", " ", txt)
    txt = re.sub(r"\n{3,}", "\n\n", txt)
    return txt.strip()


def _sem_acentos(txt: str) -> str:
    return "".join(
        ch for ch in unicodedata.normalize("NFKD", txt)
        if not unicodedata.combining(ch)
    )


def _candidate_leituras_from_line(line: str) -> tuple[dt.date | None, dt.date | None]:
    dates = [_to_date(d) for d in _DATE_RE.findall(line)]
    dates = [d for d in dates if d is not None]
    if len(dates) < 2:
        return None, None

    # Prioriza pares com distância plausível para leitura mensal.
    candidates: list[tuple[int, dt.date, dt.date]] = []
    for idx in range(len(dates) - 1):
        d1, d2 = dates[idx], dates[idx + 1]
        gap = abs((d2 - d1).days)
        if 5 <= gap <= 60:
            candidates.append((gap, d1, d2))

    if candidates:
        candidates.sort(key=lambda item: (abs(item[0] - 30), item[0]))
        d1, d2 = candidates[0][1], candidates[0][2]
        return (d1, d2) if d1 <= d2 else (d2, d1)

    d1, d2 = dates[0], dates[1]
    return (d1, d2) if d1 <= d2 else (d2, d1)


def _extract_ref_vcto_valor(txt: str) -> tuple[dt.date | None, dt.date | None, float]:
    m = re.search(r"\b(0[1-9]|1[0-2])/(20\d{2})\s+(\d{2}/\d{2}/\d{4})\s+R\$\s*([\d.,]+)", txt)
    if not m:
        return None, None, 0.0
    mes = int(m.group(1))
    ano = int(m.group(2))
    try:
        ref = dt.date(ano, mes, 1)
    except ValueError:
        ref = None
    return ref, _to_date(m.group(3)), _br2f(m.group(4))


def _extract_leituras(txt: str) -> tuple[dt.date | None, dt.date | None]:
    norm = _normalizar_texto_ocr(txt)
    if not norm:
        return None, None
    norm_sem_acentos = _sem_acentos(norm)

    patterns = (
        r"BANCO\s+DO\s+BRASIL(?:\s+S\.?\s*A\.?)?\s+(\d{1,2}/\d{1,2}/\d{4})\s+(\d{1,2}/\d{1,2}/\d{4})\s+\d{1,3}\s+(\d{1,2}/\d{1,2}/\d{4})",
        r"BANCO\s+DO\s+BRASIL(?:\s+S\.?\s*A\.?)?.{0,80}?(\d{1,2}/\d{1,2}/\d{4})\s+(\d{1,2}/\d{1,2}/\d{4})",
        r"(?:LEITURA|DATA\s+LEITURA|PROXIMA\s+LEITURA).{0,80}?(\d{1,2}/\d{1,2}/\d{4}).{1,40}?(\d{1,2}/\d{1,2}/\d{4})",
    )
    for pattern in patterns:
        m = re.search(pattern, norm_sem_acentos, re.I | re.S)
        if m:
            d1 = _to_date(m.group(1))
            d2 = _to_date(m.group(2))
            if d1 and d2:
                return (d1, d2) if d1 <= d2 else (d2, d1)

    linhas = [re.sub(r"\s+", " ", ln).strip() for ln in norm.splitlines() if ln.strip()]
    linhas_sem_acentos = [re.sub(r"\s+", " ", ln).strip() for ln in norm_sem_acentos.splitlines() if ln.strip()]
    # Primeiro tenta linhas com pistas semânticas.
    for linha, linha_sem_acentos in zip(linhas, linhas_sem_acentos):
        upper = linha_sem_acentos.upper()
        if any(hint in upper for hint in _LEITURA_HINTS):
            d1, d2 = _candidate_leituras_from_line(linha)
            if d1 and d2:
                return d1, d2

    # Fallback: qualquer linha com duas datas plausíveis.
    for linha in linhas:
        d1, d2 = _candidate_leituras_from_line(linha)
        if d1 and d2:
            return d1, d2

    return None, None


def _extract_consumo(txt: str) -> float:
    m = re.search(
        r"Consumo\s+ATIVO\s+TOTAL\s+[\d.,]+\s+[\d.,]+\s+[\d.,]+\s+([\d.,]+)\s+kWh",
        txt,
        re.I,
    )
    return _br2f(m.group(1)) if m else 0.0


def _extract_valor_consumo_convencional(txt: str) -> float:
    """Extrai o valor em R$ da linha energética principal.

    Layout Equatorial MA BT convencional:
      Consumo (kWh) 5.550 1,153023 0,843180 247,80 1.471,84 6.399,28 PIS ...

    O último valor antes do bloco de tributos é o valor total de consumo que o
    CONSEN espera junto ao kWh faturado. Não converte ausência em zero: retorna
    0.0 apenas quando a linha não existe ou não casa com esse layout.
    """
    for linha in txt.splitlines():
        if not re.search(r"\bConsumo\s*\(kWh\)", linha, re.I):
            continue
        if re.search(r"Compensad|Injetad", linha, re.I):
            continue
        trecho = re.split(r"\bPIS\b|\bCOFINS\b|\bICMS\b", linha, maxsplit=1, flags=re.I)[0]
        nums = re.findall(r"-?\d{1,3}(?:\.\d{3})*,\d+|-?\d{1,3}(?:\.\d{3})+|-?\d+(?:,\d+)?", trecho)
        if len(nums) >= 6:
            return _br2f(nums[-1])
    return 0.0


def _extract_consumos_tarifa_branca(txt: str) -> dict[str, float]:
    """Extrai consumo BT multiposto (Tarifa Branca) por posto.

    Layout observado Equatorial MA:
      Consumo Ponta (kWh) 370 ... 964,68 PIS ...
      Consumo Fora Ponta (kWh) 4.392 ... 4.171,35 COFINS ...
      Consumo Intermediário (kWh) 295 ... 491,54 ICMS ...

    Não usa linhas compensadas/injetadas e não transforma ausência em consumo:
    campos não encontrados permanecem 0.0.
    """
    out = {
        "fatConPontaRegistrado": 0.0,
        "fatConPontaFaturado": 0.0,
        "fatConPontaValorReais": 0.0,
        "fatConFPontaIndRegistrado": 0.0,
        "fatConFPontaIndFaturado": 0.0,
        "fatConFPontaIndValorReais": 0.0,
        "fatConIntermediarioRegistrado": 0.0,
        "fatConIntermediarioFaturado": 0.0,
        "fatConIntermediarioValorReais": 0.0,
    }
    postos = [
        (r"\bConsumo\s+Ponta\s*\(kWh\)", "fatConPontaRegistrado", "fatConPontaFaturado", "fatConPontaValorReais"),
        (r"\bConsumo\s+Fora\s+Ponta\s*\(kWh\)", "fatConFPontaIndRegistrado", "fatConFPontaIndFaturado", "fatConFPontaIndValorReais"),
        (r"\bConsumo\s+Intermedi[aá]rio\s*\(kWh\)", "fatConIntermediarioRegistrado", "fatConIntermediarioFaturado", "fatConIntermediarioValorReais"),
    ]
    for linha in txt.splitlines():
        if re.search(r"Compensad|Injetad", linha, re.I):
            continue
        trecho = re.split(r"\bPIS\b|\bCOFINS\b|\bICMS\b", linha, maxsplit=1, flags=re.I)[0]
        nums = re.findall(r"-?\d{1,3}(?:\.\d{3})*,\d+|-?\d{1,3}(?:\.\d{3})+|-?\d+(?:,\d+)?", trecho)
        if len(nums) < 6:
            continue
        quantidade = _br2f(nums[0])
        valor = _br2f(nums[-1])
        for pattern, campo_reg, campo_fat, campo_valor in postos:
            if re.search(pattern, linha, re.I):
                out[campo_reg] = quantidade
                out[campo_fat] = quantidade
                out[campo_valor] = valor
                break
    return out


def _extract_imposto(txt: str, nome: str) -> tuple[float, float, float]:
    m = re.search(
        rf"\b{nome}\b\s+([\d.,]+)\s+([\d.,]+)\s+([\d.,]+)",
        txt,
        re.I,
    )
    if not m:
        return 0.0, 0.0, 0.0
    return _br2f(m.group(1)), _br2f(m.group(2)), _br2f(m.group(3))


def _extract_codigo_barras(txt: str) -> str:
    m = re.search(r"(\d{44})", re.sub(r"\s+", "", txt))
    return m.group(1) if m else ""


def _extract_cnpj(txt: str) -> str:
    m = re.search(r"\b(\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2})\b", txt)
    if not m:
        return ""
    return re.sub(r"\D", "", m.group(1))


def _parser_equatorial_ma_bt(pdf_path: Path) -> dict:
    txt = _texto_pdf(pdf_path)
    fallback_uc = pdf_path.stem if pdf_path.stem.upper().startswith("BB_") else pdf_path.stem.split(" - ")[0].strip()

    instalacao = _extract_instalacao(txt, fallback_uc)
    emissao = _extract_emissao(txt)
    data_ref, vencimento, valor_fatura = _extract_ref_vcto_valor(txt)
    leitura_ant, leitura_atual = _extract_leituras(txt)
    consumo = _extract_consumo(txt)
    valor_consumo = _extract_valor_consumo_convencional(txt)
    consumos_branca = _extract_consumos_tarifa_branca(txt)
    icms_base, icms_aliq, icms_val = _extract_imposto(txt, "ICMS")
    _, pis_aliq, pis_val = _extract_imposto(txt, "PIS")
    _, cof_aliq, cof_val = _extract_imposto(txt, "COFINS")

    erro = ""
    if (
        valor_fatura == 0
        and consumo == 0
        and icms_val == 0
        and pis_val == 0
        and cof_val == 0
    ):
        erro = "FATURA_ZERADA"

    def _fmt_date(value: dt.date | None) -> str:
        return value.strftime("%d/%m/%Y") if value else ""

    tarifa_branca = any(consumos_branca.values())
    return {
        "fatCarimbo": pdf_path.stem if pdf_path.stem.upper().startswith("BB_") else "",
        "concCod": "EQUATORIAL MA",
        "Instalacao": instalacao,
        "CODIGOCLIENTE": instalacao,
        "NOTAFISCAL": _extract_nf(txt),
        "CNPJ": _extract_cnpj(txt),
        "fatDataEmissao": _fmt_date(emissao),
        "fatDataVcto": _fmt_date(vencimento),
        "fatDataLeituraAnterior": _fmt_date(leitura_ant),
        "fatDataLeituraAtual": _fmt_date(leitura_atual),
        "fatDataReferencia": data_ref.strftime("01/%m/%Y") if data_ref else "",
        "fatValorFatura": valor_fatura,
        "fatValorNotaFiscal": valor_fatura,
        "fatConPontaRegistrado": consumos_branca["fatConPontaRegistrado"],
        "fatConPontaFaturado": consumos_branca["fatConPontaFaturado"],
        "fatConPontaValorReais": consumos_branca["fatConPontaValorReais"],
        "fatConFPontaIndRegistrado": consumos_branca["fatConFPontaIndRegistrado"] if tarifa_branca else consumo,
        "fatConFPontaIndFaturado": consumos_branca["fatConFPontaIndFaturado"] if tarifa_branca else consumo,
        "fatConFPontaIndValorReais": consumos_branca["fatConFPontaIndValorReais"] if tarifa_branca else valor_consumo,
        "fatConIntermediarioRegistrado": consumos_branca["fatConIntermediarioRegistrado"],
        "fatConIntermediarioFaturado": consumos_branca["fatConIntermediarioFaturado"],
        "fatConIntermediarioValorReais": consumos_branca["fatConIntermediarioValorReais"],
        "fatICMSBase": icms_base,
        "fatDesIcmsAliquota": icms_aliq,
        "fatICMS": icms_val,
        "fatDescPisAliquota": pis_aliq,
        "fatPIS": pis_val,
        "fatDesCofinsAliquota": cof_aliq,
        "fatCOFINS": cof_val,
        "fatCodigoBarras": _extract_codigo_barras(txt),
        "cadTarifaCod": "Branca" if tarifa_branca else "Convencional",
        "cadSubGrupoCod": "B3 [<2,3kV]",
        "TARIFA_DETECTADA": "B3_BRANCA" if tarifa_branca else "Convencional",
        "ERRO": erro,
    }


if __name__ == "__main__":
    raise SystemExit(
        main_bt_generico(
            sistema="EQUATORIAL MA",
            default_pasta="",
            default_saida_stem="ocr_equatorial_ma_bt",
            description="OCR Equatorial MA BT -> XLSX no schema CEMIG",
            skip_sistema_check=True,
            parser_func=_parser_equatorial_ma_bt,
        )
    )
