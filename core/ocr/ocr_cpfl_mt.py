#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OCR CPFL MT (A4/livre) -> XLSX para digitacao no Consen.

Mantem o mesmo schema-base do BT e preenche os campos extras de MT quando
presentes no layout NF3e da CPFL Paulista / Piratininga.
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
ROOT_DIR = LOCAL_DIR.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))
sys.path.insert(0, str(LOCAL_DIR))

import pdfplumber

from ocr.ocr_neoenergia import (
    MAX_WORKERS,
    OUTPUT_DIR as NEO_OUTPUT_DIR,
    _empty_record,
    _texto_normalizado,
    _to_float_br,
    salvar_excel,
)
from ocr.ocr_cpfl_bt import (
    _digits,
    _extract_cip,
    _extract_datas,
    _extract_emissao,
    _extract_instalacao,
    _extract_mes_ref,
    _extract_multas,
    _extract_notafiscal,
    _extract_retencoes,
    _extract_total,
    _extract_total_distribuidora,
    _extract_tributos,
    _listar_pdfs,
    _resolver_carimbo_master,
)

OUTPUT_DIR = NEO_OUTPUT_DIR.parent / "OCR CPFL"
DEFAULT_PASTA = Path("//10.10.250.21/Energia/CONTASDEENERGIAELETRICA/BB/ENZO")

CPFL_CNPJS = {
    "33050196000188",  # Companhia Paulista de Forca e Luz
    "04172213000151",  # Companhia Piratininga de Forca e Luz
    "02328280000197",  # variante vista em fluxos anteriores
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("ocr_cpfl_mt")


def _is_cpfl_mt(txt: str) -> bool:
    digits = _digits(txt)
    return (
        "COMPANHIA PAULISTA DE FORCA E LUZ" in txt
        or "COMPANHIA PIRATININGA DE FORCA E LUZ" in txt
        or "CPFL" in txt
        or any(cnpj in digits for cnpj in CPFL_CNPJS)
    )


def _detectar_tarifa(texto_norm: str) -> tuple[str, str]:
    tarifa = "HS - Verde"
    texto_up = texto_norm.upper()
    if "TARIFA AZUL" in texto_up:
        tarifa = "HS - Azul"

    subgrupo = "A4"
    m = re.search(r"\bA(?:1|2|3A|3|4)\b", texto_up)
    if m:
        subgrupo = m.group(0)

    return tarifa, subgrupo


def _parse_decimal_tokens(text: str) -> list[float]:
    vals = re.findall(r"-?[\d\.]+,\d+|-?\d+", text)
    out: list[float] = []
    for val in vals:
        try:
            out.append(abs(_to_float_br(val)))
        except Exception:
            continue
    return out


def _parse_money_tokens(text: str) -> list[float]:
    vals = re.findall(r"-?\d{1,3}(?:\.\d{3})*,\d+|-?\d+,\d+", text)
    out: list[float] = []
    for val in vals:
        try:
            out.append(abs(_to_float_br(val)))
        except Exception:
            continue
    return out


def _extrair_linha(texto: str, termo: str) -> str:
    padrao = re.compile(re.escape(termo) + r"[^\n]*", re.I)
    m = padrao.search(texto)
    return m.group(0) if m else ""


def _valor_item_mt(linha: str) -> float:
    trecho = linha.split("kWh", 1)[1] if "kWh" in linha else linha.split("kW", 1)[1] if "kW" in linha else linha
    nums = _parse_money_tokens(trecho)
    if not nums:
        return 0.0
    # Layout NF3e: quantidade, tarifa1, tarifa2, valor, valor_com_icms...
    if len(nums) >= 4:
        return nums[3]
    return nums[-1]


def _quantidade_item_mt(linha: str) -> float:
    m = re.search(r"\bKWH?\s+([\d\.,]+)", linha, re.I)
    if m:
        return abs(_to_float_br(m.group(1)))
    trecho = linha.split("kWh", 1)[1] if "kWh" in linha else linha.split("kW", 1)[1] if "kW" in linha else linha
    nums = _parse_money_tokens(trecho)
    return nums[0] if nums else 0.0


def _extract_consumo_nf3e(txt: str) -> dict[str, float]:
    """NF3e 2023: linhas com código 0601/0605 Energia Atv Fornec/Inj Ponta/Fponta.
    Cada linha: '<codigo> <descr> kWh <qty> <tarifa_s> <tarifa_c> <valor> ...'
    Acumula TUSD + TE (dois itens por período/direção) antes de gravar.
    """
    ponta = fp = inj_pt = inj_fp = 0.0
    for line in txt.splitlines():
        if not re.match(r"^\s*0(?:601|605)\b", line):
            continue
        if "kWh" not in line and "KWH" not in line.upper():
            continue
        ln_up = line.upper()
        is_fornec = bool(re.search(r"\bFORNEC\b", ln_up))
        is_inj    = bool(re.search(r"\bINJ\b|INJET", ln_up))
        if not is_fornec and not is_inj:
            continue
        is_fp = bool(re.search(r"FPONTA|F\s+PONTA|FORA\s*PONTA", ln_up))
        is_pt = not is_fp and bool(re.search(r"\bPONTA\b|\bPTA\b", ln_up))
        parts = re.split(r"kWh", line, maxsplit=1, flags=re.IGNORECASE)
        if len(parts) < 2:
            continue
        nums = _parse_money_tokens(parts[1])
        # Após kWh: qty tarifa [tarifa2] valor — último número é o valor R$
        if len(nums) < 2:
            continue
        val = abs(nums[-1])
        if is_fornec:
            if is_pt:   ponta += val
            else:       fp    += val
        elif is_inj:
            if is_pt:   inj_pt += val
            else:       inj_fp += val
    out: dict[str, float] = {}
    if ponta  > 0: out["fatConPontaValorReais"]          = round(ponta,  2)
    if fp     > 0: out["fatConFPontaIndValorReais"]       = round(fp,     2)
    if inj_pt > 0: out["fatConPontaInjetadoValorReais"]  = round(inj_pt, 2)
    if inj_fp > 0: out["fatConFPontaInjetadoValorReais"] = round(inj_fp, 2)
    return out


def _extract_consumo_mt(txt: str) -> dict[str, float]:
    out: dict[str, float] = {}

    linha_acl_p = _extrair_linha(txt, "Energia ACL - Ponta")
    linha_acl_fp = _extrair_linha(txt, "Energia ACL - Fora de Ponta")
    linha_tusd_p = _extrair_linha(txt, "Tusd Enc Cons Ponta [kWh]")
    if not linha_tusd_p:
        linha_tusd_p = _extrair_linha(txt, "Tusd Enc Cons Ponta")
    linha_tusd_fp = _extrair_linha(txt, "Tusd Enc Cons F Ponta [kWh]")
    if not linha_tusd_fp:
        linha_tusd_fp = _extrair_linha(txt, "Tusd Enc Cons Fora Ponta")

    qtd_acl_p = _quantidade_item_mt(linha_acl_p)
    qtd_acl_fp = _quantidade_item_mt(linha_acl_fp)
    qtd_tusd_p = _quantidade_item_mt(linha_tusd_p)
    qtd_tusd_fp = _quantidade_item_mt(linha_tusd_fp)

    val_acl_p = _valor_item_mt(linha_acl_p)
    val_acl_fp = _valor_item_mt(linha_acl_fp)
    val_tusd_p = _valor_item_mt(linha_tusd_p)
    val_tusd_fp = _valor_item_mt(linha_tusd_fp)

    if qtd_acl_p or qtd_tusd_p:
        qtd_p = qtd_acl_p or qtd_tusd_p
        out["fatConPontaRegistrado"] = qtd_p
        out["fatConPontaFaturado"] = qtd_p
        out["fatConPontaValorReais"] = round(val_acl_p + val_tusd_p, 2)

    if qtd_acl_fp or qtd_tusd_fp:
        qtd_fp = qtd_acl_fp or qtd_tusd_fp
        out["fatConFPontaIndRegistrado"] = qtd_fp
        out["fatConFPontaIndFaturado"] = qtd_fp
        out["fatConFPontaIndValorReais"] = round(val_acl_fp + val_tusd_fp, 2)

    if qtd_tusd_p:
        out["fatConPontaExcRegistrado"] = 0.0
        out["fatConPontaExcFaturado"] = 0.0
        out["fatConFPontaCapValorReais"] = val_tusd_fp

    if qtd_tusd_fp:
        out["fatConFPontaCapRegistrado"] = qtd_tusd_fp
        out["fatConFPontaCapFaturado"] = qtd_tusd_fp
        out["fatConFPontaCapValorReais"] = val_tusd_fp

    return out


def _extract_demanda_contratada(txt: str) -> tuple[float, float]:
    m = re.search(r"DEMANDA\s+KW\s+([\d\.,]+)", txt, re.I)
    if not m:
        return 0.0, 0.0
    # Nas faturas verdes da CPFL, a demanda contratada costuma vir unica.
    return 0.0, abs(_to_float_br(m.group(1)))


def _extract_demanda_registrada(txt: str) -> tuple[float, float]:
    ponta = 0.0
    fponta = 0.0

    for line in txt.splitlines():
        ln = _texto_normalizado(line)
        if "DEMANDA ATIVA - KW PONTA" in ln and "FORA" not in ln:
            nums = _parse_decimal_tokens(line)
            if nums:
                ponta = nums[-1]
        elif "DEMANDA ATIVA - KW FORA PONTA" in ln:
            nums = _parse_decimal_tokens(line)
            if nums:
                fponta = nums[-1]

    return ponta, fponta


def _classificar_linhas_demanda(txt: str) -> tuple[tuple[float, float], tuple[float, float], tuple[float, float]]:
    faturada = (0.0, 0.0)
    excedente = (0.0, 0.0)
    ponta = (0.0, 0.0)

    linhas: list[tuple[str, float, float]] = []
    for line in txt.splitlines():
        ln = _texto_normalizado(line)
        is_usd  = "USO SIST. DISTR." in ln and " KW " in f" {ln} "
        is_tusd = "DEMANDA [KW] - TUSD" in ln or "DEMANDA [KW] - TE" in ln
        if not (is_usd or is_tusd):
            continue
        nums = _parse_money_tokens(line.split("kW", 1)[1] if "kW" in line else line)
        if len(nums) < 4:
            continue
        qtd = nums[0]
        valor = nums[3]
        linhas.append((ln, qtd, valor))

    for ln, qtd, valor in linhas:
        if "PONTA" in ln and "FORA" not in ln:
            if not ponta[0] or qtd > ponta[0]:
                ponta = (qtd, valor)

    linhas_fp = [(ln, qtd, valor) for ln, qtd, valor in linhas if "PONTA" not in ln or "FORA" in ln]
    if linhas_fp:
        linhas_fp.sort(key=lambda item: item[1], reverse=True)
        faturada = (linhas_fp[0][1], linhas_fp[0][2])
        if len(linhas_fp) > 1:
            excedente = (linhas_fp[1][1], linhas_fp[1][2])

    return ponta, faturada, excedente


def _extract_beneficios(txt: str) -> tuple[float, float]:
    bruto = 0.0
    liquido = 0.0

    for line in txt.splitlines():
        ln = _texto_normalizado(line)
        if ("SUBVENCAO TARIFARIA - TUSD" in ln and "COM ICMS" in ln) or ("SUBVENCAO TARIFARIA - TUSD" in ln and "SEM ICMS" in ln):
            nums = _parse_money_tokens(line)
            if nums:
                bruto += nums[0]
        elif "CREDITO SUBVENCAO TARIFARIA - TUSD" in ln:
            nums = _parse_money_tokens(line)
            if nums:
                liquido += nums[0]

    return round(bruto, 2), round(liquido, 2)


def _extract_observacoes_mt(txt: str) -> list[tuple[int, float]]:
    resultado: list[tuple[int, float]] = []
    regras = [
        ("DESC ENERGIA ACL FORA PONTA", 128),
        ("DESC ENERGIA ACL PONTA", 127),
    ]

    for line in txt.splitlines():
        ln = _texto_normalizado(line)
        for termo, codigo in regras:
            if termo not in ln:
                continue
            nums = _parse_money_tokens(line)
            if not nums:
                continue
            valor = -abs(nums[-1])
            resultado.append((codigo, valor))
            break

    return resultado[:5]


def _extract_bandeiras(txt: str) -> tuple[float, float, float]:
    conta_covid = 0.0
    escassez = 0.0
    adicional = 0.0

    for line in txt.splitlines():
        ln = _texto_normalizado(line)
        if "CDE-COVID" in ln:
            conta_covid += _valor_item_mt(line)
        elif "CDE ESCASSEZ HIDRICA" in ln:
            escassez += _valor_item_mt(line)
        elif "ADICIONAL BAND" in ln or "BANDEIRA TARIFARIA" in ln:
            adicional += _valor_item_mt(line)

    return round(conta_covid, 2), round(escassez, 2), round(adicional, 2)


def _extract_multas_mt(txt: str) -> float:
    total = 0.0
    for line in txt.splitlines():
        ln = _texto_normalizado(line)
        if "AO DIA" in ln or "ATRASO NO PAGAMENTO SERA COBRADO" in ln:
            continue
        if not any(
            termo in ln
            for termo in (
                "JUROS DE MORA",
                "MULTA POR ATRASO",
                "ATUALIZACAO MONETARIA",
            )
        ):
            continue
        nums = _parse_money_tokens(line)
        if nums:
            total += nums[-1]
    return round(total, 2)


def processar_pdf(pdf_path: Path, mes_padrao: int, ano_padrao: int) -> dict:
    rec = _empty_record()
    rec["ARQUIVO"] = pdf_path.name
    rec["fatDataCadastro"] = dt.date.today()
    rec["concCod"] = "CPFL"

    try:
        with pdfplumber.open(str(pdf_path)) as pdf:
            partes = [page.extract_text(x_tolerance=1, y_tolerance=1) or "" for page in pdf.pages]
        text = "\n".join(p for p in partes if p.strip())
    except Exception as exc:
        rec["ERRO"] = f"{type(exc).__name__}: {exc}"
        return rec

    if not text.strip():
        rec["ERRO"] = "PDF sem texto extraivel"
        return rec

    txt = _texto_normalizado(text)
    if not _is_cpfl_mt(txt):
        rec["ERRO"] = "Nao identificado como CPFL MT"
        return rec

    tarifa, subgrupo = _detectar_tarifa(txt)
    rec["cadTarifaCod"] = tarifa
    rec["cadSubGrupoCod"] = subgrupo
    rec["TARIFA_DETECTADA"] = f"{tarifa} {subgrupo}".strip()

    rec["Instalacao"] = _extract_instalacao(txt)
    rec["CODIGOCLIENTE"] = rec["Instalacao"]
    rec["NOTAFISCAL"] = _extract_notafiscal(txt)

    m_cnpj = re.search(r"\bCNPJ:\s*([\d\./-]+)", text, re.I)
    rec["CNPJ"] = _digits(m_cnpj.group(1)) if m_cnpj else ""

    ref = _extract_mes_ref(txt)
    rec["fatDataReferencia"] = ref if ref else dt.date(ano_padrao, mes_padrao, 1)
    rec["fatCarimbo"] = _resolver_carimbo_master(
        pdf_path.name,
        rec["Instalacao"],
        rec["fatDataReferencia"],
    )

    rec["fatDataEmissao"] = _extract_emissao(txt)
    leit_ant, leit_atu, vcto = _extract_datas(txt)
    rec["fatDataLeituraAnterior"] = leit_ant
    rec["fatDataLeituraAtual"] = leit_atu
    rec["fatDataVcto"] = vcto

    rec["fatValorFatura"] = _extract_total(txt)
    rec["fatIlumPublica"] = _extract_cip(txt)

    tributos = _extract_tributos(txt)
    rec["fatICMS"] = tributos["_icms_valor"]
    rec["fatPIS"] = tributos["_pis_valor"]
    rec["fatCOFINS"] = tributos["_cofins_valor"]
    rec["fatDesIcmsAliquota"] = tributos["fatDesIcmsAliquota"]
    rec["fatDescPisAliquota"] = tributos["fatDescPisAliquota"]
    rec["fatDesCofinsAliquota"] = tributos["fatDesCofinsAliquota"]

    nf_distrib = _extract_total_distribuidora(txt)
    rec["fatValorNotaFiscal"] = nf_distrib if nf_distrib > 0 else rec["fatValorFatura"]
    rec["fatMultasDiversas"] = _extract_multas_mt(txt)

    rec.update(_extract_retencoes(txt))
    consumo_nf3e = _extract_consumo_nf3e(text)
    rec.update(consumo_nf3e if consumo_nf3e else _extract_consumo_mt(text))

    dem_cont_p, dem_cont_fp = _extract_demanda_contratada(txt)
    dem_reg_p, dem_reg_fp = _extract_demanda_registrada(text)
    dem_p_fat, dem_fp_fat, dem_fp_exc = _classificar_linhas_demanda(text)

    # Fallback: a leitura de demanda registrada pode vir 0 (medidor zerado);
    # nesse caso usa a Demanda Contratada/cobrada ("Demanda kW NN") como
    # registrada/faturada para não deixar os campos zerados no CONSEN.
    if not dem_reg_fp and dem_cont_fp:
        dem_reg_fp = dem_cont_fp
    if not dem_reg_p and dem_cont_p:
        dem_reg_p = dem_cont_p

    rec["fatDemContratadaPonta"] = dem_cont_p
    rec["fatDemContratadaFPonta"] = dem_cont_fp
    rec["fatDemPontaRegistrada"] = dem_reg_p
    rec["fatDemFPontaIndRegistrada"] = dem_reg_fp

    if dem_p_fat[0]:
        rec["fatDemPontaFaturada"] = dem_p_fat[0]
        rec["fatDemPontaValorReais"] = dem_p_fat[1]

    if dem_fp_fat[0]:
        rec["fatDemFPontaIndFaturada"] = dem_fp_fat[0]
        rec["fatDemFPontaIndValorReais"] = dem_fp_fat[1]
    elif dem_cont_fp:
        rec["fatDemFPontaIndFaturada"] = dem_cont_fp

    if dem_fp_exc[0]:
        rec["fatDemFPontaExcFaturada"] = dem_fp_exc[0]
        rec["fatDemFPontaExcRegistrada"] = dem_fp_exc[0]
        rec["fatDemFPontaExcValorReais"] = dem_fp_exc[1]

    beneficio_bruto, beneficio_liquido = _extract_beneficios(text)
    rec["fatBeneficioTarifarioBrutoValorReais"] = beneficio_bruto
    rec["fatBeneficioLiquidoValorReais"] = beneficio_liquido

    conta_covid, escassez, adicional = _extract_bandeiras(text)
    rec["fatContaCovidValorReais"] = conta_covid
    rec["fatEscassezHidricaValorReais"] = escassez
    rec["fatValBandeira"] = round(conta_covid + escassez + adicional, 2)

    for i, (codigo, valor) in enumerate(_extract_observacoes_mt(text), start=1):
        rec[f"obsCod_{i}"] = codigo
        rec[f"obsValor_{i}"] = valor

    rec["ERRO"] = ""
    return rec


def _xlsx_saida(mes: int, ano: int) -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    return OUTPUT_DIR / f"ocr_cpfl_MT_{mes:02d}{ano}.xlsx"


def parse_args() -> argparse.Namespace:
    hoje = dt.date.today()
    parser = argparse.ArgumentParser(description="OCR CPFL MT -> XLSX")
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
        log.warning("Nenhum PDF encontrado.")
        return 0

    log.info("=" * 64)
    log.info("  OCR CPFL MT")
    log.info("=" * 64)
    log.info("  Pasta          : %s", pasta)
    log.info("  PDFs candidatos: %d", len(pdfs))

    registros: list[dict] = []
    ignorados = 0
    sem_bb_estrito = [
        pdf for pdf in pdfs
        if not re.search(r"(?i)\bBB_\d{7}\b", pdf.stem)
    ]
    if sem_bb_estrito:
        log.info(
            "  Modo sequencial: %d PDF(s) sem BB_ estrito exigem reserva atomica no indice",
            len(sem_bb_estrito),
        )
        iterable = (processar_pdf(pdf, int(args.mes), int(args.ano)) for pdf in pdfs)
        for rec in iterable:
            if rec.get("ERRO") == "Nao identificado como CPFL MT":
                ignorados += 1
                continue
            registros.append(rec)
    else:
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futuros = [executor.submit(processar_pdf, pdf, int(args.mes), int(args.ano)) for pdf in pdfs]
            for futuro in as_completed(futuros):
                rec = futuro.result()
                if rec.get("ERRO") == "Nao identificado como CPFL MT":
                    ignorados += 1
                    continue
                registros.append(rec)

    registros.sort(key=lambda r: str(r.get("fatCarimbo", "")))
    if not registros:
        log.warning("Nenhuma fatura CPFL MT extraida.")
        return 0

    destino = Path(str(args.saida).strip()) if str(args.saida).strip() else _xlsx_saida(int(args.mes), int(args.ano))
    try:
        salvar_excel(registros, destino, titulo="OCR_CPFL_MT")
    except Exception as exc:
        log.error("Falha ao salvar XLSX: %s", exc)
        return 1

    ok = sum(1 for r in registros if not r.get("ERRO"))
    erro = len(registros) - ok
    log.info("  XLSX salvo    : %s", destino)
    log.info("  Resumo        : total=%d ok=%d erro=%d ignorados=%d", len(registros), ok, erro, ignorados)
    return 0 if erro == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
