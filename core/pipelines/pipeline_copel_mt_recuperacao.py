#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Pipeline COPEL MT para PDFs de recuperacao.

Usa o mesmo pipeline MT existente, mas apontando a pasta de origem para:
    DOWNLOAD COPEL/Faltantes/MM.AAAA/MT
"""

from __future__ import annotations

import argparse
import datetime as dt
import subprocess
import sys
from pathlib import Path


LOCAL_DIR = Path(__file__).resolve().parent.parent
PIPELINE_BASE = LOCAL_DIR / "pipelines" / "pipeline_copel_mt.py"
FALTANTES_DIR = Path("//10.10.250.21/Energia/ARQUIVOS ENZO/DOWNLOAD COPEL/Faltantes")


def _pasta_recuperacao(mes: str, ano: str) -> Path:
    return FALTANTES_DIR / f"{mes}.{ano}" / "MT"


def parse_args() -> argparse.Namespace:
    hoje = dt.date.today()
    parser = argparse.ArgumentParser(description="Pipeline COPEL MT para recuperacao.")
    parser.add_argument("--mes", type=str, default=f"{hoje.month:02d}")
    parser.add_argument("--ano", type=str, default=str(hoje.year))
    parser.add_argument("--so-ocr", action="store_true")
    parser.add_argument("--so-digitacao", action="store_true")
    parser.add_argument("--so-filtro", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    pasta = _pasta_recuperacao(args.mes, args.ano)
    cmd = [
        sys.executable,
        str(PIPELINE_BASE),
        "--mes",
        args.mes,
        "--ano",
        args.ano,
        "--pasta",
        str(pasta),
    ]
    if args.so_ocr:
        cmd.append("--so-ocr")
    if args.so_digitacao:
        cmd.append("--so-digitacao")
    if args.so_filtro:
        cmd.append("--so-filtro")

    proc = subprocess.run(cmd)
    return int(proc.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
