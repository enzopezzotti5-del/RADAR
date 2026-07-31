#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pipeline_celesc_mt.py
---------------------
Wrapper legado do pipeline CELESC MT.

Mantem a interface antiga, mas delega a execucao para o pipeline unificado:
    pipelines/pipeline_celesc.py --so-mt
"""

from __future__ import annotations

import argparse
import datetime as dt
import subprocess
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from pipelines import pipeline_celesc as base

STAGING_SCRIPT = Path(__file__).resolve().parent / "staging_celesc_mt.py"
DOWNLOAD_CELESC = Path("//10.10.250.21/Energia/ARQUIVOS ENZO/DOWNLOAD CELESC")
PYTHON_EXE = str(Path(sys.executable))


def parse_args() -> argparse.Namespace:
    hoje = dt.date.today()
    parser = argparse.ArgumentParser(description="Pipeline CELESC MT normalizado no fluxo unificado")
    parser.add_argument("--mes", type=str, default=f"{hoje.month:02d}")
    parser.add_argument("--ano", type=str, default=str(hoje.year))
    parser.add_argument("--so-ocr", action="store_true", help="Executa apenas o OCR")
    parser.add_argument("--so-digitacao", action="store_true", help="Executa apenas a digitacao")
    parser.add_argument("--so-filtro", action="store_true", help="Executa apenas o filtro/mover")
    parser.add_argument(
        "--pasta",
        type=str,
        default="",
        help="Pasta de PDFs (override do padrao MM.YYYY/MT)",
    )
    parser.add_argument(
        "--carimbo",
        action="append",
        default=[],
        help="Restringe OCR a este carimbo. Ex: --carimbo BB_2003260",
    )
    return parser.parse_args()


def _montar_argv_base(args: argparse.Namespace, pasta_pipeline: str = "") -> list[str]:
    argv = [
        sys.argv[0],
        "--mes",
        str(args.mes),
        "--ano",
        str(args.ano),
        "--so-mt",
    ]
    if args.so_ocr:
        argv.append("--so-ocr")
    if args.so_digitacao:
        argv.append("--so-digitacao")
    if args.so_filtro:
        argv.append("--so-filtro")
    pasta_alvo = str(pasta_pipeline).strip() or str(args.pasta).strip()
    if pasta_alvo:
        argv.extend(["--pasta", pasta_alvo])
    for carimbo in args.carimbo:
        carimbo_limpo = str(carimbo).strip()
        if carimbo_limpo:
            argv.extend(["--carimbo", carimbo_limpo])
    return argv


def _rodar_staging(pasta: str, mes: str, ano: str) -> int:
    """Carimbagem e cópia dos PDFs originais para DOWNLOAD CELESC antes do pipeline."""
    cmd = [
        PYTHON_EXE, str(STAGING_SCRIPT),
        "--pasta", pasta,
        "--mes", mes,
        "--ano", ano,
    ]
    print(f"[STAGING CELESC MT] {pasta}")
    res = subprocess.run(cmd)
    return res.returncode


def main() -> int:
    args = parse_args()

    # Se --pasta foi dado, roda staging primeiro (carimbagem + cópia para DOWNLOAD CELESC)
    pasta_str = str(args.pasta).strip()
    if pasta_str and STAGING_SCRIPT.exists():
        rc = _rodar_staging(pasta_str, args.mes, args.ano)
        if rc != 0:
            print(f"[WARN] Staging CELESC MT retornou código {rc} — prosseguindo mesmo assim")

    argv_original = sys.argv[:]
    try:
        sys.argv = _montar_argv_base(args)
        return base.main()
    finally:
        sys.argv = argv_original


if __name__ == "__main__":
    raise SystemExit(main())
