#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from core.ocr.ocr_bt_cemig_adapter import main_enel_bt


if __name__ == "__main__":
    raise SystemExit(
        main_enel_bt(
            sistema="ENEL CE",
            default_pasta="",
            default_saida_stem="ocr_enel_ce_bt",
            description="OCR Enel CE BT -> XLSX no schema CEMIG",
        )
    )
