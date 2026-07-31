#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pipeline_equatorial_go_mt.py — Lote MT de Equatorial Goiás via watcher.

Wrapper chamado pelo watcher.py quando detecta PDFs em EQUATORIAL/GOIAS/MT/.
Delega integralmente para pipeline_equatorial_go.py, que aceita --pasta,
--mes e --ano e processa tanto BT quanto MT (BT estará vazio nesse contexto).

Uso (pelo watcher):
    python pipeline_equatorial_go_mt.py --pasta <pasta_mt> --mes 07 --ano 2026
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pipeline_equatorial_go import main

if __name__ == "__main__":
    # Quando chamado pelo watcher para processar apenas MT (PDFs flat na pasta raiz),
    # restringe carimbo/OCR a tensão MT e ativa o fallback de subpasta automático.
    if "--tipo-tensao" not in sys.argv:
        sys.argv.extend(["--tipo-tensao", "mt"])
    sys.exit(main())
