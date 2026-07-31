#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OCR CHESP (MT) -> XLSX para digitacao no Consen.

Implementacao inicial baseada no layout NF3e identificado na pasta ENZO.
O schema de saida reaproveita o mesmo formato do OCR Neoenergia para
facilitar a futura entrada no pipeline de producao.
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


OUTPUT_DIR = NEO_OUTPUT_DIR.parent / "OCR CHESP"
DEFAULT_PASTA = Path("//10.10.250.21/Energia/CONTASDEENERGIAELETRICA/BB/ENZO")

RE_MONEY = re.compile(r"-?\d{1,3}(?:\.\d{3})*,\d{2}")


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("ocr_chesp")


def _is_chesp(text: str) -> bool:
    txt = _texto_normalizado(text)
    return "COMPANHIA HIDROELETRICA SAO PATRICIO" in txt or "CHESP" in txt


def _extract_instalacao_chesp(text: str) -> str:
    text_norm = _texto_normalizado(text)
    patterns = [
        r"UNIDADE CONSUMIDORA\s+(?:ROTA:\s*\d+,\s*SEQUENCIA:\s*\d+\s+)?(\d{6,12})\s+NOTA FISCAL",
        r"UNIDADE CONSUMIDORA\s+(\d{6,12})",
        r"\b(\d{6,12})\s+NOTA FISCAL N",
        r"\b(\d{6,12})\s+NOTA FISCAL\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, text_norm, flags=re.IGNORECASE | re.DOTALL)
        if match:
            return match.group(1).strip()
    return ""


def _extract_codigo_cliente_chesp(text: str) -> str:
    text_norm = _texto_normalizado(text)
    patterns = [
        r"CODIGO DO CLIENTE.*?\n.*?\b(\d{1,10})\s+HTTPS?://",
        r"CPF/CNPJ\s+[\d\./-]+\s+(\d{1,10})\s+HTTPS?://",
        r"CODIGO DO CLIENTE.*?\b(\d{1,10})\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, text_norm, flags=re.IGNORECASE | re.DOTALL)
        if match:
            return match.group(1).strip()
    return ""


def _extract_cnpj_cliente_chesp(text: str) -> str:
    match = re.search(r"CPF/CNPJ\s+([\d\./-]{11,20})", text, flags=re.IGNORECASE)
    if match:
        return re.sub(r"\D", "", match.group(1))
    return ""


def _extract_endereco_chesp(text: str) -> str:
    linhas = [_norm(line) for line in text.splitlines() if _norm(line)]
    for idx, line in enumerate(linhas):
        if "BANCO DO BRASIL" not in _texto_normalizado(line):
            continue
        partes: list[str] = []
        for prox in linhas[idx + 1:idx + 5]:
            prox_norm = _texto_normalizado(prox)
            if any(token in prox_norm for token in ("UNIDADE CONSUMIDORA", "NOTA FISCAL", "CEP ")):
                break
            partes.append(prox)
        if partes:
            return _norm(" ".join(partes))
    return ""


def _extract_notafiscal_chesp(text: str) -> str:
    match = re.search(r"NOTA FISCAL N[Oº°]*\s*(\d+)", text, flags=re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return ""


def _extract_data_emissao_chesp(text: str) -> dt.date | None:
    match = re.search(r"EMISS[ÃA]O:\s*(\d{2}/\d{2}/\d{4})", text, flags=re.IGNORECASE)
    if match:
        return _to_date(match.group(1))
    return None


def _extract_referencia_chesp(text: str, mes_padrao: int, ano_padrao: int) -> dt.date:
    match = re.search(r"(?:^|\n)\s*(\d{2}/\d{4})\s+\d{2}/\d{2}/\d{4}\s+R\$\s*[-\d\.,]+", text)
    if match:
        mm, yyyy = match.group(1).split("/")
        return dt.date(int(yyyy), int(mm), 1)
    return dt.date(ano_padrao, mes_padrao, 1)


def _extract_datas_chesp(text: str) -> tuple[dt.date | None, dt.date | None, dt.date | None]:
    match = re.search(
        r"(\d{2}/\d{2}/\d{4})\s+(\d{2}/\d{2}/\d{4})\s+\d+\s+(\d{2}/\d{2}/\d{4})",
        text,
    )
    leitura_ant = _to_date(match.group(1)) if match else None
    leitura_atu = _to_date(match.group(2)) if match else None

    match_vcto = re.search(
        r"(\d{2}/\d{4})\s+(\d{2}/\d{2}/\d{4})\s+R\$\s*([-\d\.,]+)",
        text,
        flags=re.IGNORECASE,
    )
    vcto = _to_date(match_vcto.group(2)) if match_vcto else None
    return leitura_ant, leitura_atu, vcto


def _extract_total_chesp(text: str) -> float:
    match = re.search(
        r"TOTAL A PAGAR.*?(\d{2}/\d{4})\s+(\d{2}/\d{2}/\d{4})\s+R\$\s*([-\d\.,]+)",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if match:
        return abs(_to_float_br(match.group(3)))

    for line in text.splitlines():
        if "TOTAL A PAGAR" not in _texto_normalizado(line):
            continue
        valores = RE_MONEY.findall(line)
        if valores:
            return abs(_to_float_br(valores[-1]))
    return 0.0


def _extract_valor_nota_fiscal_chesp(text: str) -> float:
    for line in text.splitlines():
        if "VALOR BRUTO DA FATURA" not in _texto_normalizado(line):
            continue
        valores = RE_MONEY.findall(line)
        if valores:
            return abs(_to_float_br(valores[-1]))
    return _extract_total_chesp(text)


def _extract_codigo_barras_chesp(text: str) -> str:
    linhas = [_norm(line) for line in text.splitlines() if _norm(line)]
    for idx, line in enumerate(linhas):
        if "PAGUE COM PIX" not in _texto_normalizado(line):
            continue
        for prox in linhas[idx + 1:idx + 4]:
            digits = re.sub(r"\D", "", prox)
            if len(digits) >= 44:
                return digits

    for line in text.splitlines():
        line_norm = _norm(line)
        if not line_norm:
            continue
        if "CHAVE DE ACESSO" in _texto_normalizado(line_norm):
            continue
        if not re.search(r"^\d{11,}", re.sub(r"\D", "", line_norm)):
            continue
        if "-" not in line_norm:
            continue
        digits = re.sub(r"\D", "", line_norm)
        if len(digits) >= 44:
            return digits
    return _extract_codigo_barras(text)


def _extract_aliquotas_chesp(text: str) -> dict[str, float]:
    out = {
        "fatDescPisAliquota": 0.0,
        "fatDesCofinsAliquota": 0.0,
        "fatDesIcmsAliquota": 0.0,
    }
    mapa = {
        "PIS/PASEP": "fatDescPisAliquota",
        "COFINS": "fatDesCofinsAliquota",
        "ICMS": "fatDesIcmsAliquota",
    }
    for line in text.splitlines():
        line_norm = _texto_normalizado(line)
        for rotulo, campo in mapa.items():
            if not line_norm.startswith(rotulo):
                continue
            numeros = re.findall(r"[-\d\.,]+", line)
            if len(numeros) >= 3:
                out[campo] = abs(_to_float_br(numeros[-2]))
    return out


def _extract_retencoes_chesp(text: str) -> dict[str, float]:
    out = {
        "fatDescPisPercRetImposto": 0.0,
        "fatDescPisValRetImposto": 0.0,
        "fatDescCofinsPercRetImposto": 0.0,
        "fatDescCofinsValRetImposto": 0.0,
        "fatDescCsllPercRetImposto": 0.0,
        "fatDescCsllValRetImposto": 0.0,
        "fatDescIrpjPercRetImposto": 0.0,
        "fatDescIrpjValRetImposto": 0.0,
        "fatDescConsumoPercRetImposto": 0.0,
        "fatDescConsumoValRetImposto": 0.0,
        "fatDescDemandaPercRetImposto": 0.0,
        "fatDescDemandaValRetImposto": 0.0,
    }

    for line in text.splitlines():
        line_norm = _texto_normalizado(line)
        if "RETENCAO " not in line_norm:
            continue

        match = re.search(r"RETENCAO\s+([A-Z/ ]+?)\s+([\d\.,]+)%", line_norm)
        if not match:
            continue

        nome = match.group(1).strip()
        perc_txt = match.group(2).replace(",", ".").strip()
        try:
            perc = abs(float(perc_txt))
        except ValueError:
            perc = 0.0
        negativos = [v for v in re.findall(r"-\d{1,3}(?:\.\d{3})*,\d{2}", line_norm)]
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
        elif "IRPJ CONSUMO" in nome:
            out["fatDescConsumoPercRetImposto"] = perc
            out["fatDescConsumoValRetImposto"] = val
        elif "IRPJ DEMANDA" in nome:
            out["fatDescDemandaPercRetImposto"] = perc
            out["fatDescDemandaValRetImposto"] = val

    total_irpj = out["fatDescConsumoValRetImposto"] + out["fatDescDemandaValRetImposto"]
    if total_irpj:
        out["fatDescIrpjValRetImposto"] = total_irpj
    return out


def _extract_mt_rules_chesp(text: str) -> dict[str, float]:
    out: dict[str, float] = {}
    beneficio = 0.0

    for line in text.splitlines():
        line_norm = _texto_normalizado(line)

        if "EUSD - ENERGIA PONTA" in line_norm:
            match = re.search(r"kWh\s+([-\d\.,]+)\s+[-\d\.,]+\s+([-\d\.,]+)", line, flags=re.IGNORECASE)
            if match:
                qtd = _to_float_qty(match.group(1))
                val = _to_float_br(match.group(2))
                out["fatConPontaRegistrado"] = qtd
                out["fatConPontaFaturado"] = qtd
                out["fatConPontaValorReais"] = round(val, 2)

        elif "EUSD - ENERGIA FORA PONTA" in line_norm:
            match = re.search(r"kWh\s+([-\d\.,]+)\s+[-\d\.,]+\s+([-\d\.,]+)", line, flags=re.IGNORECASE)
            if match:
                qtd = _to_float_qty(match.group(1))
                val = _to_float_br(match.group(2))
                out["fatConFPontaIndRegistrado"] = qtd
                out["fatConFPontaIndFaturado"] = qtd
                out["fatConFPontaIndValorReais"] = round(val, 2)

        elif "EUSD - DEMANDA FORA PONTA" in line_norm:
            match = re.search(r"kW\s+([-\d\.,]+)\s+[-\d\.,]+\s+([-\d\.,]+)", line, flags=re.IGNORECASE)
            if match:
                qtd = _to_float_qty(match.group(1))
                val = _to_float_br(match.group(2))
                out["fatDemFPontaIndRegistrada"] = qtd
                out["fatDemFPontaIndFaturada"] = qtd
                out["fatDemFPontaIndValorReais"] = round(val, 2)

        elif "DEMANDA FORA PONTA-KW" in line_norm:
            valores = re.findall(r"[-\d\.,]+", line)
            if valores:
                out["fatDemContratadaFPonta"] = _to_float_qty(valores[-1])

        elif "ENCARGO ESCASSEZ HIDRICA" in line_norm:
            valores = [abs(_to_float_br(v)) for v in RE_MONEY.findall(line)]
            if valores:
                candidatos = [v for v in valores if v >= 1]
                out["fatEscassezHidricaValorReais"] = (candidatos[0] if candidatos else valores[0])

        elif "DESCONTO TUSD FORA PONTA" in line_norm:
            valores = [abs(_to_float_br(v)) for v in RE_MONEY.findall(line)]
            if len(valores) >= 2:
                out["fatConCreditoTUSDFPontaValorReais"] = valores[1]
                beneficio += valores[1]

        elif "DESCONTO TUSD KWH PONTA LIVRE" in line_norm:
            valores = [abs(_to_float_br(v)) for v in RE_MONEY.findall(line)]
            if len(valores) >= 2:
                out["fatConCreditoTUSDPontaValorReais"] = valores[1]
                beneficio += valores[1]

    if beneficio:
        out["fatBeneficioTarifarioBrutoValorReais"] = round(beneficio, 2)
        out["fatBeneficioLiquidoValorReais"] = round(beneficio, 2)

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

    rec["cadTarifaCod"] = "HS - Verde" if "HORARIA VERDE" in _texto_normalizado(text) else "HS - Azul"
    rec["cadSubGrupoCod"] = "A4 [<13,8kV]"
    rec["TARIFA_DETECTADA"] = "A4_VERDE" if rec["cadTarifaCod"] == "HS - Verde" else "A4_AZUL"

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
    rec.update(_extract_aliquotas_chesp(text))
    rec["Debitos anteriores"] = _extract_debitos_anteriores(text)
    rec.update(_extract_retencoes_chesp(text))
    rec.update(_extract_mt_rules_chesp(text))
    rec["ERRO"] = ""
    return rec


def identificacao_rapida(pdf_path: Path) -> dict:
    resultado = {"sistema": "DESCONHECIDA", "instalacao": "", "mes_ref": "", "grupo": ""}
    try:
        text, _ = _extract_pdf_data(pdf_path)
        if not _is_chesp(text):
            return resultado
        resultado["sistema"] = "CHESP"
        resultado["instalacao"] = _extract_instalacao_chesp(text)
        resultado["mes_ref"] = _extract_referencia_chesp(text, dt.date.today().month, dt.date.today().year).strftime("%m-%Y")
        resultado["grupo"] = "A"
    except Exception as exc:
        log.warning("  identificacao_rapida %s: %s", pdf_path.name, exc)
    return resultado


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
    return OUTPUT_DIR / f"ocr_chesp_MT_{mes:02d}{ano}.xlsx"


def parse_args() -> argparse.Namespace:
    hoje = dt.date.today()
    parser = argparse.ArgumentParser(description="OCR CHESP MT -> XLSX")
    parser.add_argument("--mes", type=int, default=hoje.month, help="Mes de referencia padrao")
    parser.add_argument("--ano", type=int, default=hoje.year, help="Ano de referencia padrao")
    parser.add_argument("--pasta", type=str, default=str(DEFAULT_PASTA), help="Pasta com PDFs")
    parser.add_argument("--saida", type=str, default="", help="XLSX de saida")
    parser.add_argument("--carimbo", action="append", default=[], help="Carimbo(s) BB_XXXXXXX")
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
        log.warning("Nenhum PDF encontrado para o filtro informado.")
        return 0

    log.info("=" * 64)
    log.info("OCR CHESP MT")
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
        log.warning("Nenhuma fatura CHESP extraida.")
        return 0

    destino = Path(str(args.saida).strip()) if str(args.saida).strip() else _xlsx_saida(int(args.mes), int(args.ano))
    try:
        salvar_excel(registros, destino, titulo="OCR_CHESP_MT")
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
