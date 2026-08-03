#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Pipeline EDP ES BT: OCR -> Digitacao -> Filtro."""
from __future__ import annotations

import sys
from pathlib import Path

# Importa tudo do pipeline EDP SP e sobrescreve apenas as constantes
sys.path.insert(0, str(Path(__file__).resolve().parent))
import pipeline_edp_sp_bt as _base  # noqa: E402

_base.OCR_SCRIPT     = Path(_base.LOCAL_DIR) / "ocr" / "ocr_edp_es_bt.py"
_base.OCR_SAIDA_DIR  = Path(_base.SERVIDOR) / "ARQUIVOS ENZO" / "OCR EDP SP"
_base.PIPELINE_SAIDA = Path(_base.SERVIDOR) / "ARQUIVOS ENZO" / "EDP_ES_pipeline_saida"
_base.PIPELINE_NOME  = "EDP ES BT"

_base._xlsx_saida   = lambda mes, ano: _base.OCR_SAIDA_DIR / f"ocr_edp_es_bt_{mes}{ano}.xlsx"
_base._xlsx_resgate = lambda slug:     _base.OCR_SAIDA_DIR / "_resgates" / f"ocr_edp_es_bt_{slug}.xlsx"


if __name__ == "__main__":
    raise SystemExit(_base.main())
