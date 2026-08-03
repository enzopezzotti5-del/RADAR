#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Adaptador CELESC - MT
=====================

Este modulo expoe a operacao de media tensao com um nome explicito (`celesc_mt`)
reaproveitando a implementacao consolidada em `celesc_grupo_a`.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


CORE_ROOT = Path(__file__).resolve().parents[2]
ROOT_LOCAL = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT_LOCAL))
sys.path.insert(0, str(CORE_ROOT))

from downloaders.celesc import celesc_grupo_a as mt


class CelescMTDownloader:
    """Fachada enxuta para execucao do fluxo CELESC de media tensao."""

    tensao = mt.TENSAO_GRUPO_A

    def executar(
        self,
        usuario: str,
        senha: str,
        headless: bool,
        limite_cnpjs: int | None = None,
        limite_ucs: int | None = None,
        baixar_faturas: bool = False,
        limite_faturas: int | None = None,
        cnpjs_alvo: set[str] | None = None,
        ucs_alvo: set[str] | None = None,
        meses_ref_alvo: set[str] | None = None,
        ignorar_ja_baixado: bool = False,
    ) -> int:
        return mt.executar(
            usuario,
            senha,
            headless,
            limite_cnpjs=limite_cnpjs,
            limite_ucs=limite_ucs,
            baixar_faturas=baixar_faturas,
            limite_faturas=limite_faturas,
            cnpjs_alvo=cnpjs_alvo,
            ucs_alvo=ucs_alvo,
            meses_ref_alvo=meses_ref_alvo,
            ignorar_ja_baixado=ignorar_ja_baixado,
        )


def executar(
    usuario: str,
    senha: str,
    headless: bool,
    limite_cnpjs: int | None = None,
    limite_ucs: int | None = None,
    baixar_faturas: bool = False,
    limite_faturas: int | None = None,
    cnpjs_alvo: set[str] | None = None,
    ucs_alvo: set[str] | None = None,
    meses_ref_alvo: set[str] | None = None,
    ignorar_ja_baixado: bool = False,
) -> int:
    return CelescMTDownloader().executar(
        usuario,
        senha,
        headless,
        limite_cnpjs=limite_cnpjs,
        limite_ucs=limite_ucs,
        baixar_faturas=baixar_faturas,
        limite_faturas=limite_faturas,
        cnpjs_alvo=cnpjs_alvo,
        ucs_alvo=ucs_alvo,
        meses_ref_alvo=meses_ref_alvo,
        ignorar_ja_baixado=ignorar_ja_baixado,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fluxo inicial CELESC - MT")
    parser.add_argument("--usuario", default=mt.USUARIO_PADRAO)
    parser.add_argument("--senha", default=mt.SENHA_PADRAO)
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--limite-cnpjs", type=int, default=None)
    parser.add_argument("--limite-ucs", type=int, default=None)
    parser.add_argument("--baixar-faturas-2026", action="store_true")
    parser.add_argument("--limite-faturas", type=int, default=None)
    parser.add_argument("--cnpjs-alvo", default="", help="Lista de CNPJs separadas por virgula.")
    parser.add_argument("--ucs-alvo", default="", help="Lista de UCs separadas por virgula.")
    parser.add_argument("--meses-ref", default="", help="Lista de referencias MM-AAAA separadas por virgula.")
    parser.add_argument(
        "--ignorar-ja-baixado",
        action="store_true",
        help="Baixa novamente mesmo que a UC/ref ja exista no master/indice local.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    cnpjs_alvo = {item.strip() for item in str(args.cnpjs_alvo or "").split(",") if item.strip()} or None
    ucs_alvo = {item.strip() for item in str(args.ucs_alvo or "").split(",") if item.strip()} or None
    meses_ref_alvo = {item.strip() for item in str(args.meses_ref or "").split(",") if item.strip()} or None
    raise SystemExit(
        executar(
            args.usuario,
            args.senha,
            args.headless,
            limite_cnpjs=args.limite_cnpjs,
            limite_ucs=args.limite_ucs,
            baixar_faturas=args.baixar_faturas_2026,
            limite_faturas=args.limite_faturas,
            cnpjs_alvo=cnpjs_alvo,
            ucs_alvo=ucs_alvo,
            meses_ref_alvo=meses_ref_alvo,
            ignorar_ja_baixado=args.ignorar_ja_baixado,
        )
    )
