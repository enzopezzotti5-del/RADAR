#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import re
import sys
from pathlib import Path
from typing import Callable

ROOT_DIR = Path(__file__).resolve().parents[2]
CORE_DIR = ROOT_DIR / "core"
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))
if str(CORE_DIR) not in sys.path:
    sys.path.insert(0, str(CORE_DIR))

import pdfplumber

from core.ocr import ocr_bt_generico, ocr_enel
from core.ocr.OCR_Cemig import HEADERS_REF, log, salvar_excel
from core.pipelines.pipeline_producao_enzo import _identificar_pdf


def _carimbo(pdf_path: Path) -> str:
    stem = pdf_path.stem.strip().upper()
    return stem if stem.startswith("BB_") else pdf_path.stem


def _valor_cliente_valido(valor: object) -> str:
    txt = str(valor or "").strip()
    if not txt:
        return ""
    if txt.upper().startswith("BB_"):
        return ""
    return txt


def _listar_pdfs(pasta: Path, carimbos: set[str]) -> list[Path]:
    pdfs = sorted(p for p in pasta.glob("*.pdf") if p.is_file())
    if not carimbos:
        return pdfs
    # Aceita "2013894" e "BB_2013894" indistintamente
    c_norm: set[str] = set()
    for c in carimbos:
        c = c.strip().upper()
        c_norm.add(c)
        c_norm.add(f"BB_{c}" if not c.startswith("BB_") else c[3:])
    return [p for p in pdfs if _carimbo(p) in c_norm]


def _texto_pdf(pdf_path: Path, max_paginas: int = 2) -> str:
    partes: list[str] = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        for page in pdf.pages[:max_paginas]:
            partes.append(page.extract_text() or "")
    return "\n".join(partes)


def _extrair_cnpj(pdf_path: Path) -> str:
    texto = _texto_pdf(pdf_path)
    m = re.search(r"\b(\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2})\b", texto)
    if not m:
        return ""
    return re.sub(r"\D", "", m.group(1))


def _normalizar_obs(dest: dict, src: dict) -> None:
    obs = src.get("_obs_list")
    if not isinstance(obs, list):
        return
    for idx, item in enumerate(obs[:5], start=1):
        cod = ""
        valor = 0
        if isinstance(item, tuple) and len(item) >= 2:
            cod = str(item[0] or "")
            valor = item[1] or 0
        else:
            cod = str(item or "")
        dest[f"obsCod_{idx}"] = cod
        dest[f"obsValor_{idx}"] = valor


def normalizar_para_cemig_schema(
    src: dict,
    *,
    sistema: str,
    pdf_path: Path,
    conc_cod: str = "",
    tarifa_padrao: str = "Convencional",
    subgrupo_padrao: str = "B3 [<2,3kV]",
) -> dict:
    dest = {h: "" for h in HEADERS_REF}
    for header in HEADERS_REF:
        if header in src:
            dest[header] = src.get(header, "")

    dest["ARQUIVO"] = pdf_path.name
    dest["fatCarimbo"] = src.get("fatCarimbo") or _carimbo(pdf_path)
    instalacao = _valor_cliente_valido(src.get("Instalacao"))
    codigo_cliente = _valor_cliente_valido(src.get("CODIGOCLIENTE"))
    dest["Instalacao"] = instalacao or codigo_cliente
    dest["CODIGOCLIENTE"] = codigo_cliente or dest["Instalacao"]
    dest["fatDataCadastro"] = src.get("fatDataCadastro") or dt.date.today().strftime("%d/%m/%Y")
    dest["concCod"] = src.get("concCod") or conc_cod or sistema
    dest["cadTarifaCod"] = src.get("cadTarifaCod") or tarifa_padrao
    dest["cadSubGrupoCod"] = src.get("cadSubGrupoCod") or subgrupo_padrao
    dest["fatValorNotaFiscal"] = src.get("fatValorNotaFiscal") or src.get("fatValorFatura") or 0
    dest["CNPJ"] = src.get("CNPJ") or _extrair_cnpj(pdf_path)
    dest["TARIFA_DETECTADA"] = src.get("TARIFA_DETECTADA") or tarifa_padrao
    dest["ERRO"] = src.get("ERRO", "")
    _normalizar_obs(dest, src)
    return dest


def _parser_generico(pdf_path: Path) -> dict:
    return ocr_bt_generico.processar_pdf(str(pdf_path), str(pdf_path))


def _parser_enel_bt(pdf_path: Path) -> dict:
    return ocr_enel.processar_pdf(str(pdf_path), "bt")


def _processar_lote(
    *,
    pasta: Path,
    sistema: str,
    parser_func: Callable[[Path], dict],
    saida: Path,
    mes: int,
    ano: int,
    carimbos: set[str],
    conc_cod: str = "",
    tarifa_padrao: str = "Convencional",
    subgrupo_padrao: str = "B3 [<2,3kV]",
    skip_sistema_check: bool = False,
) -> int:
    if not pasta.exists():
        log.error("Pasta nao encontrada: %s", pasta)
        return 1

    pdfs = _listar_pdfs(pasta, carimbos)
    if not pdfs:
        log.warning("Nenhum PDF encontrado em %s", pasta)
        return 0

    registros: list[dict] = []
    ignorados = 0
    total = len(pdfs)
    for idx, pdf in enumerate(pdfs, start=1):
        log.info("  [%d/%d] Processando %s", idx, total, pdf.name)
        if not skip_sistema_check:
            info = _identificar_pdf(pdf)
            if str(info.get("sistema") or "").strip().upper() != sistema.upper():
                ignorados += 1
                log.info("  [%d/%d] Ignorado por sistema divergente: %s", idx, total, pdf.name)
                continue
        rec = parser_func(pdf)
        if not rec.get("fatDataReferencia"):
            rec["fatDataReferencia"] = f"01/{mes:02d}/{ano}"
        registros.append(
            normalizar_para_cemig_schema(
                rec,
                sistema=sistema,
                pdf_path=pdf,
                conc_cod=conc_cod,
                tarifa_padrao=tarifa_padrao,
                subgrupo_padrao=subgrupo_padrao,
            )
        )
        log.info("  [%d/%d] OK %s", idx, total, pdf.name)

    if not registros:
        log.warning("Nenhuma fatura %s extraida. Ignorados=%d", sistema, ignorados)
        return 0

    registros.sort(key=lambda r: str(r.get("fatCarimbo", "")))
    salvar_excel(registros, saida)
    log.info("  Sistema=%s | OK=%d | Ignorados=%d", sistema, len(registros), ignorados)
    return 0


def _parse_args(default_pasta: str, description: str) -> argparse.Namespace:
    hoje = dt.date.today()
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--mes", type=int, default=hoje.month)
    parser.add_argument("--ano", type=int, default=hoje.year)
    parser.add_argument("--pasta", type=str, default=default_pasta)
    parser.add_argument("--saida", type=str, default="")
    parser.add_argument("--carimbo", action="append", default=[])
    return parser.parse_args()


def main_bt_generico(
    *,
    sistema: str,
    default_pasta: str,
    default_saida_stem: str,
    description: str,
    conc_cod: str = "",
    tarifa_padrao: str = "Convencional",
    subgrupo_padrao: str = "B3 [<2,3kV]",
    parser_func: Callable[[Path], dict] | None = None,
    skip_sistema_check: bool = False,
) -> int:
    args = _parse_args(default_pasta, description)
    pasta = Path(str(args.pasta).strip())
    saida = Path(str(args.saida).strip()) if str(args.saida).strip() else pasta / f"{default_saida_stem}_{int(args.mes):02d}{int(args.ano)}.xlsx"
    carimbos = {str(c).strip().upper() for c in (args.carimbo or []) if str(c).strip()}
    return _processar_lote(
        pasta=pasta,
        sistema=sistema,
        parser_func=parser_func or _parser_generico,
        saida=saida,
        mes=int(args.mes),
        ano=int(args.ano),
        carimbos=carimbos,
        conc_cod=conc_cod,
        tarifa_padrao=tarifa_padrao,
        subgrupo_padrao=subgrupo_padrao,
        skip_sistema_check=skip_sistema_check,
    )


def main_enel_bt(
    *,
    sistema: str,
    default_pasta: str,
    default_saida_stem: str,
    description: str,
    conc_cod: str = "",
) -> int:
    args = _parse_args(default_pasta, description)
    pasta = Path(str(args.pasta).strip())
    saida = Path(str(args.saida).strip()) if str(args.saida).strip() else pasta / f"{default_saida_stem}_{int(args.mes):02d}{int(args.ano)}.xlsx"
    carimbos = {str(c).strip().upper() for c in (args.carimbo or []) if str(c).strip()}
    return _processar_lote(
        pasta=pasta,
        sistema=sistema,
        parser_func=_parser_enel_bt,
        saida=saida,
        mes=int(args.mes),
        ano=int(args.ano),
        carimbos=carimbos,
        conc_cod=conc_cod,
    )
