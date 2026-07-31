#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Teste_Celesc.py
===============

Fluxo alternativo de validacao da CELESC:
1. Login
2. Seleciona Grupo A
3. Lista todos os CNPJs
4. Entra em cada CNPJ e lista as UCs
5. Encerra sem baixar faturas

Objetivo: validar o acesso e a navegacao basica do portal sem consumir
carimbos nem rodar o fluxo de download.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import celesc_grupo_a as C


SAIDA_DIR = Path(__file__).resolve().parent / "saida"


def executar_teste(
    usuario: str,
    senha: str,
    headless: bool,
    limite_cnpjs: int | None = None,
    limite_ucs: int | None = None,
) -> int:
    driver = None
    try:
        C.log.info("=" * 72)
        C.log.info("TESTE CELESC - LOGIN + GRUPO A + LISTA DE UCs")
        C.log.info("=" * 72)

        driver = C.build_driver(headless=headless)
        C.fazer_login(driver, usuario, senha)
        C.fechar_modal_boas_vindas(driver)
        C.selecionar_grupo_a(driver)
        C.aguardar_lista_cnpjs(driver)
        C._pausa_humana(1.0, 2.0)

        html_path = C.salvar_html(driver)
        cartoes = C._coletar_cartoes_cnpj_bs4(driver.page_source)
        if not cartoes:
            C.log.warning("Nenhum CNPJ encontrado. HTML salvo em: %s", html_path)
            return 2

        csv_cnpjs = C.salvar_cnpjs_csv(cartoes)
        C.log.info("CNPJs encontrados no teste: %s", len(cartoes))

        linhas_ucs, _ = C.listar_ucs_por_cnpj(
            driver,
            master=None,
            indice_local=None,  # nao e usado quando baixar_faturas=False
            cartoes_cnpj=cartoes,
            limite_cnpjs=limite_cnpjs,
            limite_ucs=limite_ucs,
            baixar_faturas=False,
            limite_faturas=None,
        )
        csv_ucs = C.salvar_ucs_csv(linhas_ucs)

        C.log.info("-" * 72)
        C.log.info("TESTE CELESC CONCLUIDO")
        C.log.info("HTML  : %s", html_path)
        C.log.info("CNPJs : %s", csv_cnpjs)
        C.log.info("UCs   : %s", csv_ucs)
        C.log.info("Total de linhas de UCs: %s", len(linhas_ucs))
        C.log.info("-" * 72)
        return 0
    finally:
        C._encerrar_driver(driver)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Teste alternativo CELESC: login + Grupo A + listagem de UCs.")
    parser.add_argument("--usuario", default=C.USUARIO_PADRAO)
    parser.add_argument("--senha", default=C.SENHA_PADRAO)
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--limite-cnpjs", type=int, default=None)
    parser.add_argument("--limite-ucs", type=int, default=None)
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    raise SystemExit(
        executar_teste(
            usuario=args.usuario,
            senha=args.senha,
            headless=args.headless,
            limite_cnpjs=args.limite_cnpjs,
            limite_ucs=args.limite_ucs,
        )
    )
