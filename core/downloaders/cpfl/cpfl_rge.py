#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Entrada BT do downloader CPFL / RGE.

Mantem o caminho historico usado pelo Radar para Baixa Tensao (BT) e delega a
implementacao compartilhada para `cpfl_rge_base.py`.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
CORE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(CORE_ROOT))
import _venv_check  # noqa

from core.downloaders.cpfl.cpfl_rge_base import (  # noqa: E402
    _CONTAS,
    SENHA_PADRAO,
    USUARIO_PADRAO,
    executar,
)
from core.downloaders.cpfl.cpfl_guard import (  # noqa: E402
    resolver_max_ucs_por_titular,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fluxo BT CPFL/RGE.")
    parser.add_argument(
        "--conta",
        choices=list(_CONTAS),
        default="",
        help="Atalho de credenciais: denise (padrao CPFL), rge/bb (RGE/bbenergia)",
    )
    parser.add_argument("--usuario", default="")
    parser.add_argument("--senha", default="")
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--manter-aberto", action="store_true")
    parser.add_argument("--parar-na-segunda-via", action="store_true")
    parser.add_argument("--indice-uc-ativa", type=int, default=0)
    parser.add_argument("--perfil", choices=["bt"], default="bt")
    parser.add_argument("--lote", action="store_true")
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--limite-titulares", type=int, default=0)
    parser.add_argument(
        "--offset-titulares",
        type=int,
        default=0,
        help="Pula os primeiros N titulares (para execucao paralela)",
    )
    parser.add_argument("--limite-ucs", type=int, default=0)
    parser.add_argument(
        "--max-ucs-por-titular",
        type=int,
        default=0,
        help="Guarda de expansao; CLI > CPFL_MAX_UCS_PER_TITULAR > 368",
    )
    parser.add_argument(
        "--forcar-download",
        action="store_true",
        help="Ignora pre-filtro master/local e forca download mesmo se ja baixado",
    )
    parser.add_argument(
        "--worker-id",
        type=int,
        default=0,
        help="ID do worker (0=padrao). Cada worker usa pasta temp isolada.",
    )
    parser.add_argument(
        "--fluxo-servico",
        choices=["segunda-via", "pagar-conta"],
        default="segunda-via",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    if args.conta:
        usuario, senha = _CONTAS[args.conta]
    else:
        usuario = args.usuario or USUARIO_PADRAO
        senha = args.senha or SENHA_PADRAO
    raise SystemExit(
        executar(
            usuario,
            senha,
            args.headless,
            args.manter_aberto,
            args.parar_na_segunda_via,
            args.indice_uc_ativa,
            args.fluxo_servico,
            "bt",
            args.lote,
            args.limite_titulares,
            args.limite_ucs,
            offset_titulares=args.offset_titulares,
            worker_id=args.worker_id,
            forcar_download=args.forcar_download,
            max_ucs_por_titular=resolver_max_ucs_por_titular(args.max_ucs_por_titular),
            preflight=args.preflight,
        )
    )
