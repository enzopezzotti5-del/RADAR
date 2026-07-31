#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from core.ocr.ocr_bt_cemig_adapter import main_bt_generico
from core.ocr.ocr_equatorial_pi_bt_parser import processar_pdf as _parser_pi_bt


def _parser(pdf_path: Path) -> dict:
    return _parser_pi_bt(str(pdf_path))


if __name__ == "__main__":
    raise SystemExit(
        main_bt_generico(
            sistema="EQUATORIAL PI",
            default_pasta="",
            default_saida_stem="ocr_equatorial_pi_bt",
            description="OCR Equatorial PI BT -> XLSX no schema CEMIG",
            conc_cod="EQUATORIAL PI",
            tarifa_padrao="Convencional",
            subgrupo_padrao="B3 [<2,3kV]",
            parser_func=_parser,
        )
    )
