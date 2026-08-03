#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from core.ocr.ocr_bt_cemig_adapter import main_bt_generico
from core.ocr import ocr_edp_bt


if __name__ == "__main__":
    raise SystemExit(
        main_bt_generico(
            sistema="EDP ES",
            default_pasta="",
            default_saida_stem="ocr_edp_es_bt",
            description="OCR EDP ES BT -> XLSX no schema CEMIG",
            conc_cod="EDP ES",
            parser_func=lambda p: ocr_edp_bt.processar_pdf(str(p)),
        )
    )
