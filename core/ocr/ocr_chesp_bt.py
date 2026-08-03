#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OCR CHESP BT (B3 Convencional / Branca) -> XLSX para digitacao no Consen.

Detecta automaticamente:
  - B3 Convencional: campo de consumo unico (fatConFPontaInd)
  - B3 Horaria Branca: ponta / intermediario / fora ponta separados
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
    _to_float_qty,
    salvar_excel,
)
from ocr.ocr_chesp import (
    _is_chesp,
    _extract_instalacao_chesp,
    _extract_codigo_cliente_chesp,
    _extract_cnpj_cliente_chesp,
    _extract_endereco_chesp,
    _extract_notafiscal_chesp,
    _extract_data_emissao_chesp,
    _extract_referencia_chesp,
    _extract_datas_chesp,
    _extract_total_chesp,
    _extract_valor_nota_fiscal_chesp,
    _extract_codigo_barras_chesp,
)


OUTPUT_DIR = NEO_OUTPUT_DIR.parent / "OCR CHESP"
DEFAULT_PASTA = Path("//10.10.250.21/Energia/CONTASDEENERGIAELETRICA/BB/ENZO")

RE_MONEY = re.compile(r"-?\d{1,3}(?:\.\d{3})*,\d{2}")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("ocr_chesp_bt")


def _extract_classificacao_bt(text: str) -> tuple[str, str]:
    """Retorna (cadSubGrupoCod, cadTarifaCod) para faturas BT CHESP."""
    txt = _texto_normalizado(text)
    m = re.search(r"MODALIDADE\s+TARIFARIA:\s*([A-Z\s]+?)(?:TRIFASICO|MONOFASICO|BIFASICO|$)", txt)
    if m:
        raw = _norm(m.group(1)).upper().strip()
        if "HORARIA BRANCA" in raw or "HORARIA  BRANCA" in raw:
            return "B3 [<2,3kV]", "Branca"
        if "HORARIA VERDE" in raw:
            return "B3 [<2,3kV]", "Horária Verde"
        if "HORARIA AZUL" in raw:
            return "B3 [<2,3kV]", "Horária Azul"
    return "B3 [<2,3kV]", "Convencional"


def _extract_escassez_hidrica(text: str) -> float:
    m = re.search(r"VALOR\s+ESCASSEZ\s+HIDRICA\s*([\d.,]+)", _texto_normalizado(text))
    return abs(_to_float_br(m.group(1))) if m else 0.0


def _extract_consumo_convencional(text: str) -> dict[str, float]:
    """Extrai consumo unico BT Convencional e bandeira amarela."""
    txt = _texto_normalizado(text)
    out: dict[str, float] = {}

    m_con = re.search(
        r"^CONSUMO\s+kWh\s+([\d.,]+)\s+[\d.,]+\s+([\d.,]+)",
        txt,
        flags=re.MULTILINE | re.IGNORECASE,
    )
    if not m_con:
        m_con = re.search(
            r"\bCONSUMO\s+kWh\s+([\d.,]+)\s+[\d.,]+\s+([\d.,]+)",
            txt,
            flags=re.IGNORECASE,
        )

    m_band = re.search(
        r"ADICIONAL\s+BANDEIRA\s+[A-Z]+\s+[\d.,]+\s+[\d.,]+\s+([\d.,]+)",
        txt,
        flags=re.IGNORECASE,
    )
    val_bandeira = abs(_to_float_br(m_band.group(1))) if m_band else 0.0

    if m_con:
        qtd = _to_float_qty(m_con.group(1))
        val = abs(_to_float_br(m_con.group(2)))
        out["fatConFPontaIndRegistrado"] = qtd
        out["fatConFPontaIndFaturado"] = qtd
        out["fatConFPontaIndValorReais"] = round(val + val_bandeira, 2)

    if val_bandeira:
        out["fatValBandeira"] = val_bandeira

    return out


def _extract_consumo_branca(text: str) -> dict[str, float]:
    """Extrai consumo BT Horaria Branca: ponta, intermediario, fora ponta + bandeira."""
    txt = _texto_normalizado(text)
    out: dict[str, float] = {}

    m_pta = re.search(
        r"CONSUMO\s+PONTA\s+BRANCA\s+kWh\s+([\d.,]+)\s+[\d.,]+\s+([\d.,]+)",
        txt,
        flags=re.IGNORECASE,
    )
    m_int = re.search(
        r"CONSUMO\s+INTERMEDIARIO\s+BRANCA\s+kWh\s+([\d.,]+)\s+[\d.,]+\s+([\d.,]+)",
        txt,
        flags=re.IGNORECASE,
    )
    m_fp = re.search(
        r"CONSUMO\s+FORA\s+PONTA\s+BRANCA\s+kWh\s+([\d.,]+)\s+[\d.,]+\s+([\d.,]+)",
        txt,
        flags=re.IGNORECASE,
    )
    m_band = re.search(
        r"ADICIONAL\s+BANDEIRA\s+[A-Z]+\s+[\d.,]+\s+[\d.,]+\s+([\d.,]+)",
        txt,
        flags=re.IGNORECASE,
    )
    val_bandeira = abs(_to_float_br(m_band.group(1))) if m_band else 0.0

    if m_pta:
        qtd = _to_float_qty(m_pta.group(1))
        val = abs(_to_float_br(m_pta.group(2)))
        out["fatConPontaRegistrado"] = qtd
        out["fatConPontaFaturado"] = qtd
        out["fatConPontaValorReais"] = round(val, 2)

    if m_int:
        qtd = _to_float_qty(m_int.group(1))
        val = abs(_to_float_br(m_int.group(2)))
        out["fatConIntermediarioRegistrado"] = qtd
        out["fatConIntermediarioFaturado"] = qtd
        out["fatConIntermediarioValorReais"] = round(val, 2)

    if m_fp:
        qtd = _to_float_qty(m_fp.group(1))
        val = abs(_to_float_br(m_fp.group(2)))
        out["fatConFPontaIndRegistrado"] = qtd
        out["fatConFPontaIndFaturado"] = qtd
        out["fatConFPontaIndValorReais"] = round(val + val_bandeira, 2)

    if val_bandeira:
        out["fatValBandeira"] = val_bandeira

    return out


def _extract_aliquotas_bt(text: str) -> dict[str, float]:
    """Extrai aliquotas PIS, COFINS e ICMS do bloco de tributos lateral."""
    out = {
        "fatDescPisAliquota": 0.0,
        "fatDesCofinsAliquota": 0.0,
        "fatDesIcmsAliquota": 0.0,
    }
    txt = _texto_normalizado(text)

    m_pis = re.search(r"PIS/PASEP\s*([\d.,]+)\s+([\d.,]+)\s+([\d.,]+)", txt)
    if m_pis:
        out["fatDescPisAliquota"] = abs(_to_float_br(m_pis.group(2)))

    m_cof = re.search(r"COFINS\s+([\d.,]+)\s+([\d.,]+)\s+([\d.,]+)", txt)
    if m_cof:
        out["fatDesCofinsAliquota"] = abs(_to_float_br(m_cof.group(2)))

    m_icms = re.search(r"\bICMS\s+([\d.,]+)\s+([\d.,]+)\s+([\d.,]+)", txt)
    if m_icms:
        out["fatDesIcmsAliquota"] = abs(_to_float_br(m_icms.group(2)))

    return out


def _extract_retencoes_bt(text: str) -> dict[str, float]:
    """
    Extrai retencoes BT: PIS 0.65% / COFINS 3% / CSLL 1% / IRPJ 1.2%.
    IRPJ CONSUMO vai direto para fatDescIrpj (nao para fatDescConsumo).
    """
    out = {
        "fatDescPisPercRetImposto": 0.0,
        "fatDescPisValRetImposto": 0.0,
        "fatDescCofinsPercRetImposto": 0.0,
        "fatDescCofinsValRetImposto": 0.0,
        "fatDescCsllPercRetImposto": 0.0,
        "fatDescCsllValRetImposto": 0.0,
        "fatDescIrpjPercRetImposto": 0.0,
        "fatDescIrpjValRetImposto": 0.0,
    }

    for line in text.splitlines():
        line_norm = _texto_normalizado(line)
        if "RETENCAO " not in line_norm:
            continue

        match = re.search(r"RETENCAO\s+([A-Z/ ]+?)\s+([\d\.,]+)%", line_norm)
        if not match:
            continue

        nome = match.group(1).strip()
        perc_txt = match.group(2).replace(",", ".")
        try:
            perc = abs(float(perc_txt))
        except ValueError:
            perc = 0.0

        negativos = re.findall(r"-\d{1,3}(?:\.\d{3})*,\d{2}", line_norm)
        val = -abs(_to_float_br(negativos[0])) if negativos else 0.0

        if nome.startswith("PIS"):
            out["fatDescPisPercRetImposto"] = perc
            out["fatDescPisValRetImposto"] = val
        elif nome.startswith("COFINS"):
            out["fatDescCofinsPercRetImposto"] = perc
            out["fatDescCofinsValRetImposto"] = val
        elif nome.startswith("CSLL"):
            out["fatDescCsllPercRetImposto"] = perc
            out["fatDescCsllValRetImposto"] = val
        elif "IRPJ" in nome:
            out["fatDescIrpjPercRetImposto"] = perc
            out["fatDescIrpjValRetImposto"] = val

    return out


def processar_texto(text: str, *, arquivo: str, carimbo: str, mes_padrao: int, ano_padrao: int) -> dict:
    rec = _empty_record()
    rec["ARQUIVO"] = arquivo
    rec["fatCarimbo"] = carimbo
    rec["fatDataCadastro"] = dt.date.today()
    rec["concCod"] = "CHESP"

    if not text.strip():
        rec["ERRO"] = "PDF sem texto extraivel"
        return rec
    if not _is_chesp(text):
        rec["ERRO"] = "Nao identificado como CHESP"
        return rec

    subgrupo, tarifa = _extract_classificacao_bt(text)
    rec["cadSubGrupoCod"] = subgrupo
    rec["cadTarifaCod"] = tarifa
    rec["TARIFA_DETECTADA"] = f"B3_{tarifa.upper().replace(' ', '_')}"

    rec["Instalacao"] = _extract_instalacao_chesp(text)
    rec["CODIGOCLIENTE"] = _extract_codigo_cliente_chesp(text)
    rec["ENDERECO"] = _extract_endereco_chesp(text)
    rec["NOTAFISCAL"] = _extract_notafiscal_chesp(text)
    rec["CNPJ"] = _extract_cnpj_cliente_chesp(text)
    rec["fatCodigoBarras"] = _extract_codigo_barras_chesp(text)

    rec["fatDataReferencia"] = _extract_referencia_chesp(text, mes_padrao, ano_padrao)
    rec["fatDataEmissao"] = _extract_data_emissao_chesp(text)
    leitura_ant, leitura_atu, vcto = _extract_datas_chesp(text)
    rec["fatDataLeituraAnterior"] = leitura_ant
    rec["fatDataLeituraAtual"] = leitura_atu
    rec["fatDataVcto"] = vcto

    rec["fatValorFatura"] = _extract_total_chesp(text)
    rec["fatValorNotaFiscal"] = _extract_valor_nota_fiscal_chesp(text)
    rec["fatIlumPublica"] = _extract_ilum_publica(text)
    rec["fatICMS"] = abs(_extract_imposto(text, "ICMS"))
    rec["fatPIS"] = abs(_extract_imposto(text, "PIS"))
    rec["fatCOFINS"] = abs(_extract_imposto(text, "COFINS"))
    rec["fatEscassezHidricaValorReais"] = _extract_escassez_hidrica(text)
    rec["Debitos anteriores"] = _extract_debitos_anteriores(text)

    rec.update(_extract_aliquotas_bt(text))
    rec.update(_extract_retencoes_bt(text))

    if tarifa == "Convencional":
        rec.update(_extract_consumo_convencional(text))
    else:
        rec.update(_extract_consumo_branca(text))

    rec["ERRO"] = ""
    return rec


def processar_pdf_direto(pdf_path: Path, mes_padrao: int, ano_padrao: int) -> dict:
    try:
        text, _ = _extract_pdf_data(pdf_path)
    except Exception as exc:
        rec = _empty_record()
        rec["ARQUIVO"] = pdf_path.name
        rec["fatCarimbo"] = _carimbo_do_nome(pdf_path)
        rec["concCod"] = "CHESP"
        rec["ERRO"] = f"{type(exc).__name__}: {exc}"
        return rec

    return processar_texto(
        text,
        arquivo=pdf_path.name,
        carimbo=_carimbo_do_nome(pdf_path),
        mes_padrao=mes_padrao,
        ano_padrao=ano_padrao,
    )


def _listar_pdfs(pasta: Path, carimbos: set[str]) -> list[Path]:
    pdfs = sorted(p for p in pasta.glob("*.pdf") if p.is_file())
    if carimbos:
        pdfs = [p for p in pdfs if p.stem.upper() in carimbos]
    return pdfs


def _xlsx_saida(mes: int, ano: int) -> Path:
    return OUTPUT_DIR / f"ocr_chesp_BT_{mes:02d}{ano}.xlsx"


def parse_args() -> argparse.Namespace:
    hoje = dt.date.today()
    parser = argparse.ArgumentParser(description="OCR CHESP BT -> XLSX")
    parser.add_argument("--mes", type=int, default=hoje.month)
    parser.add_argument("--ano", type=int, default=hoje.year)
    parser.add_argument("--pasta", type=str, default=str(DEFAULT_PASTA))
    parser.add_argument("--saida", type=str, default="")
    parser.add_argument("--carimbo", action="append", default=[])
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    pasta = Path(str(args.pasta).strip())
    carimbos = {str(c).strip().upper() for c in (args.carimbo or []) if str(c).strip()}

    if not pasta.exists():
        log.error("Pasta nao encontrada: %s", pasta)
        return 1

    pdfs = _listar_pdfs(pasta, carimbos)
    if not pdfs:
        log.warning("Nenhum PDF encontrado para o filtro informado.")
        return 0

    log.info("=" * 64)
    log.info("OCR CHESP BT")
    log.info("=" * 64)
    log.info("Pasta : %s", pasta)
    log.info("PDFs candidatos: %d", len(pdfs))

    registros: list[dict] = []
    ignorados = 0
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futuros = [executor.submit(processar_pdf_direto, pdf, int(args.mes), int(args.ano)) for pdf in pdfs]
        for futuro in as_completed(futuros):
            rec = futuro.result()
            if rec.get("ERRO") == "Nao identificado como CHESP":
                ignorados += 1
                continue
            registros.append(rec)

    registros.sort(key=lambda row: str(row.get("fatCarimbo", "")))
    if not registros:
        log.warning("Nenhuma fatura CHESP BT extraida.")
        return 0

    destino = Path(str(args.saida).strip()) if str(args.saida).strip() else _xlsx_saida(int(args.mes), int(args.ano))
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    try:
        salvar_excel(registros, destino, titulo="OCR_CHESP_BT")
    except Exception as exc:
        log.error("Falha ao salvar XLSX: %s", exc)
        return 1

    ok = sum(1 for rec in registros if not rec.get("ERRO"))
    erro = len(registros) - ok
    log.info("XLSX salvo: %s", destino)
    log.info("Resumo: total=%d ok=%d erro=%d ignorados=%d", len(registros), ok, erro, ignorados)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
