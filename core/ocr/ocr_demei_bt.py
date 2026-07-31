#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OCR DEMEI BT (Depto. Municipal de Energia de Ijui - RS) -> XLSX para digitacao.
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
    _empty_record,
    _extract_codigo_barras,
    _norm,
    _texto_normalizado,
    _to_date,
    _to_float_br,
    salvar_excel,
)


OUTPUT_DIR = NEO_OUTPUT_DIR.parent / "OCR DEMEI"
DEFAULT_PASTA = Path("//10.10.250.21/Energia/CONTASDEENERGIAELETRICA/BB/ENZO")
CNPJ_DEMEI = "95289500000100"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("ocr_demei_bt")


def _digits(text: str) -> str:
    return re.sub(r"\D", "", str(text or ""))


def _carimbo_do_arquivo(pdf_path: Path) -> str:
    stem = pdf_path.stem.strip()
    m_bb = re.search(r"[Bb][Bb]_(\d+)", stem)
    return m_bb.group(0) if m_bb else stem


def _uc_do_nome(pdf_path: Path) -> str:
    return pdf_path.stem.split(" - ", 1)[0].strip()


def _first_page_text(pdf_path: Path) -> str:
    with pdfplumber.open(str(pdf_path)) as pdf:
        if not pdf.pages:
            return ""
        return pdf.pages[0].extract_text(x_tolerance=1, y_tolerance=1) or ""


def _is_demei(text: str) -> bool:
    txt = _texto_normalizado(text)
    return "DEPTO MUNICIPAL DE ENERGIA DE IJUI" in txt or CNPJ_DEMEI in _digits(txt)


def _extract_instalacao(text: str, pdf_path: Path) -> str:
    txt = _texto_normalizado(text)
    for pat in (
        r"UNIDADE\s+CONSUMIDORA\s+([0-9.\-]+)",
        r"COMPETENCIA\s+CONTA\s+L\.E\.\s+G\.F\.\s+N[O0]\s+FATURA\s+VENCIMENTO\s+TOTAL\s+A\s+PAGAR\s+[\d/]+\s+([0-9.\-]+)",
    ):
        m = re.search(pat, txt)
        if m:
            return m.group(1).strip()
    return _uc_do_nome(pdf_path)


def _extract_referencia(text: str, mes_padrao: int, ano_padrao: int) -> dt.date:
    txt = _texto_normalizado(text)
    m = re.search(r"\b(0[1-9]|1[0-2])/(20\d{4}|\d{4})\s+\d{2}/\d{2}/\d{4}\s+R\$\s*[\d.,]+", txt)
    if m:
        return dt.date(int(m.group(2)), int(m.group(1)), 1)
    return dt.date(ano_padrao, mes_padrao, 1)


def _extract_vencimento(text: str) -> dt.date | None:
    txt = _texto_normalizado(text)
    m = re.search(r"\b(?:0[1-9]|1[0-2])/\d{4}\s+(\d{2}/\d{2}/\d{4})\s+R\$\s*[\d.,]+", txt)
    return _to_date(m.group(1)) if m else None


def _extract_emissao(text: str) -> dt.date | None:
    txt = _texto_normalizado(text)
    m = re.search(r"EMISSAO:\s*(\d{2}/\d{2}/\d{4})", txt)
    return _to_date(m.group(1)) if m else None


def _extract_leituras(text: str) -> tuple[dt.date | None, dt.date | None]:
    txt = _texto_normalizado(text)
    m = re.search(
        r"DATAS\s+DE\s+LEITURAS\s+(\d{2}/\d{2}/\d{4})\s+(\d{2}/\d{2}/\d{4})\s+\d{1,3}\s+\d{2}/\d{2}/\d{4}",
        txt,
    )
    if m:
        return _to_date(m.group(1)), _to_date(m.group(2))
    return None, None


def _extract_total(text: str) -> float:
    txt = _texto_normalizado(text)
    m = re.search(r"\b(?:0[1-9]|1[0-2])/\d{4}\s+\d{2}/\d{2}/\d{4}\s+R\$\s*([\d.,]+)", txt)
    return abs(_to_float_br(m.group(1))) if m else 0.0


def _extract_nota_fiscal(text: str) -> str:
    txt = _texto_normalizado(text)
    m = re.search(r"NOTA\s+FISCAL\s+N[O0]\s+(\d+)", txt)
    return m.group(1) if m else ""


def _extract_ilum_publica(text: str) -> float:
    txt = _texto_normalizado(text)
    m = re.search(r"C\.I\.P\.\s*-\s*CONT\.\s+ILUM\.\s+PUBLICA\s+MUNICIPAL\s+\d+\s+([\d.,]+)", txt)
    return abs(_to_float_br(m.group(1))) if m else 0.0


def _extract_consumo(text: str) -> dict[str, float]:
    txt = _texto_normalizado(text)
    out: dict[str, float] = {}

    m_cons = re.search(r"CONSUMO\s+kWh\s+([\d.,]+)\s+[\d.,]+\s+([\d.,]+)", txt, re.I)
    qtd = abs(_to_float_br(m_cons.group(1))) if m_cons else 0.0
    val_energia = abs(_to_float_br(m_cons.group(2))) if m_cons else 0.0

    m_band = re.search(r"ACRESCIMO\s+BANDEIRA\s+[A-Z]+\s+\d+\s+[\d.,]+\s+([\d.,]+)", txt, re.I)
    val_bandeira = abs(_to_float_br(m_band.group(1))) if m_band else 0.0

    if qtd > 0:
        out["fatConFPontaIndRegistrado"] = qtd
        out["fatConFPontaIndFaturado"] = qtd
        out["fatConFPontaIndValorReais"] = round(val_energia + val_bandeira, 2)
    if val_bandeira > 0:
        out["fatValBandeira"] = val_bandeira
    return out


def _extract_tributos(text: str) -> dict[str, float]:
    txt = _texto_normalizado(text)
    out = {
        "fatICMS": 0.0,
        "fatPIS": 0.0,
        "fatCOFINS": 0.0,
        "fatDesIcmsAliquota": 0.0,
        "fatDescPisAliquota": 0.0,
        "fatDesCofinsAliquota": 0.0,
        "fatValorNotaFiscal": 0.0,
    }

    m_icms = re.search(r"\bICMS\s+([\d.,]+)\s+([\d.,]+)\s+([\d.,]+)", txt)
    if m_icms:
        out["fatValorNotaFiscal"] = abs(_to_float_br(m_icms.group(1)))
        out["fatDesIcmsAliquota"] = abs(_to_float_br(m_icms.group(2)))
        out["fatICMS"] = abs(_to_float_br(m_icms.group(3)))

    m_pis = re.search(r"PIS/PASEP\s*([\d.,]+)\s+([\d.,]+)\s+([\d.,]+)", txt)
    if m_pis:
        out["fatDescPisAliquota"] = abs(_to_float_br(m_pis.group(2)))
        out["fatPIS"] = abs(_to_float_br(m_pis.group(3)))

    m_cof = re.search(r"COFINS\s+([\d.,]+)\s+([\d.,]+)\s+([\d.,]+)", txt)
    if m_cof:
        out["fatDesCofinsAliquota"] = abs(_to_float_br(m_cof.group(2)))
        out["fatCOFINS"] = abs(_to_float_br(m_cof.group(3)))

    return out


def identificacao_rapida(pdf_path: Path) -> dict:
    resultado = {"sistema": "DESCONHECIDA", "instalacao": "", "mes_ref": "", "grupo": "B"}
    try:
        text = _first_page_text(pdf_path)
        if not text or not _is_demei(text):
            return resultado
        resultado["sistema"] = "DEMEI"
        resultado["instalacao"] = _extract_instalacao(text, pdf_path)
        ref = _extract_referencia(text, dt.date.today().month, dt.date.today().year)
        resultado["mes_ref"] = ref.strftime("%m-%Y")
        resultado["grupo"] = "B"
    except Exception as exc:
        log.warning("  identificacao_rapida %s: %s", pdf_path.name, exc)
    return resultado


def processar_pdf(pdf_path: Path, mes_padrao: int, ano_padrao: int) -> dict:
    return processar_pdf_direto(pdf_path, mes_padrao, ano_padrao)


def processar_pdf_direto(pdf_path: Path, mes_padrao: int, ano_padrao: int) -> dict:
    rec = _empty_record()
    rec["ARQUIVO"] = pdf_path.name
    rec["fatCarimbo"] = _carimbo_do_arquivo(pdf_path)
    rec["fatDataCadastro"] = dt.date.today()
    rec["concCod"] = "DEMEI"

    try:
        with pdfplumber.open(str(pdf_path)) as pdf:
            text = "\n".join((page.extract_text(x_tolerance=1, y_tolerance=1) or "") for page in pdf.pages[:3])
    except Exception as exc:
        rec["ERRO"] = f"{type(exc).__name__}: {exc}"
        return rec

    if not text.strip():
        rec["ERRO"] = "PDF sem texto extraivel"
        return rec
    if not _is_demei(text):
        rec["ERRO"] = "Nao identificado como DEMEI"
        return rec

    rec["cadTarifaCod"] = "Convencional"
    rec["cadSubGrupoCod"] = "B3 [<2,3kV]"
    rec["TARIFA_DETECTADA"] = "B3"
    rec["Instalacao"] = _extract_instalacao(text, pdf_path)
    rec["CODIGOCLIENTE"] = rec["Instalacao"]
    rec["NOTAFISCAL"] = _extract_nota_fiscal(text)
    rec["CNPJ"] = CNPJ_DEMEI

    rec["fatDataReferencia"] = _extract_referencia(text, mes_padrao, ano_padrao)
    rec["fatDataVcto"] = _extract_vencimento(text)
    rec["fatDataEmissao"] = _extract_emissao(text)
    leitura_ant, leitura_atu = _extract_leituras(text)
    rec["fatDataLeituraAnterior"] = leitura_ant
    rec["fatDataLeituraAtual"] = leitura_atu

    rec["fatValorFatura"] = _extract_total(text)
    rec["fatIlumPublica"] = _extract_ilum_publica(text)
    rec.update(_extract_consumo(text))
    rec.update(_extract_tributos(text))

    codigo_barras = _extract_codigo_barras(text)
    rec["fatCodigoBarras"] = _digits(codigo_barras) if len(_digits(codigo_barras)) >= 44 else ""
    rec["ENDERECO"] = _norm("")
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
    return OUTPUT_DIR / f"ocr_demei_BT_{mes:02d}{ano}.xlsx"


def parse_args() -> argparse.Namespace:
    hoje = dt.date.today()
    parser = argparse.ArgumentParser(description="OCR DEMEI BT -> XLSX")
    parser.add_argument("--mes", type=int, default=hoje.month)
    parser.add_argument("--ano", type=int, default=hoje.year)
    parser.add_argument("--pasta", type=str, default=str(DEFAULT_PASTA))
    parser.add_argument("--saida", type=str, default="")
    parser.add_argument("--carimbo", action="append", default=[])
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    pasta = Path(str(args.pasta).strip())
    if not pasta.exists():
        log.error("Pasta nao encontrada: %s", pasta)
        return 1

    carimbos = {str(c).strip().upper() for c in (args.carimbo or []) if str(c).strip()}
    destino = Path(str(args.saida).strip()) if str(args.saida).strip() else _xlsx_saida(int(args.mes), int(args.ano))
    destino.parent.mkdir(parents=True, exist_ok=True)

    pdfs = _listar_pdfs(pasta, carimbos)
    if not pdfs:
        log.warning("Nenhum PDF encontrado em %s", pasta)
        return 0

    registros: list[dict] = []
    ignorados = 0
    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(pdfs) or 1)) as executor:
        futures = {
            executor.submit(processar_pdf_direto, pdf, int(args.mes), int(args.ano)): pdf
            for pdf in pdfs
        }
        for future in as_completed(futures):
            pdf = futures[future]
            rec = future.result()
            if rec.get("ERRO") == "Nao identificado como DEMEI":
                ignorados += 1
                continue
            registros.append(rec)
            log.info("  OK  %s", pdf.name)

    if not registros:
        log.warning("Nenhuma fatura DEMEI extraida. Ignorados=%d", ignorados)
        return 0

    registros.sort(key=lambda r: str(r.get("fatCarimbo", "")))
    salvar_excel(registros, destino, titulo="OCR_DEMEI_BT")
    log.info("  OK=%d | Ignorados=%d", len(registros), ignorados)
    log.info("Saida: %s", destino)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
