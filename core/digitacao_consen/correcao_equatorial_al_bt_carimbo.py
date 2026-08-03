#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Digitação/Correção Equatorial AL BT por carimbo.

Fluxo:
1. Processa o PDF com OCR Equatorial AL.
2. Abre o Consen pelo carimbo.
3. Preenche consumo, injetado GD, tributos, retenções, bandeira e CIP.
4. Salva (requer --salvar).
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import _venv_check  # noqa: E402,F401

try:
    from digitacao_consen import correcao_fluxo_base as fluxo_base
    from digitacao_consen.correcao_fluxo_base import (
        CorrecaoFluxoConfig,
        formatar_valor_para_campo,
        log,
        warn,
    )
    from ocr.ocr_equatorial_al_bt import processar_pdf_al
except ModuleNotFoundError:
    import correcao_fluxo_base as fluxo_base  # type: ignore
    from correcao_fluxo_base import (  # type: ignore
        CorrecaoFluxoConfig,
        formatar_valor_para_campo,
        log,
        warn,
    )
    from ocr_equatorial_al_bt import processar_pdf_al  # type: ignore

DEFAULT_SAIDA_DIR = "//10.10.250.21/Energia/ARQUIVOS ENZO/EQUATORIAL_AL_producao_saida/digitacao_bt"

LOGIN_URL = fluxo_base.LOGIN_URL
BASE_URL = LOGIN_URL.rsplit("login.php", 1)[0]
EDIT_URL = os.environ.get(
    "CONSEN_EDITA_FATURA_CARIMBO_URL",
    f"{BASE_URL}index.php#bpg/gestao/fatura/editaFaturaCarimbo.php",
)

SAIDA_DIR = Path(os.environ.get("CONSEN_CORRECAO_SAIDA", DEFAULT_SAIDA_DIR))
FECHAR_AO_FINAL = os.environ.get("CONSEN_CORRECAO_FECHAR", "1").strip().lower() not in {"0", "false", "nao", "não"}
EXECUCAO_CSV = SAIDA_DIR / "digitacao_execucao.csv"

CAMPOS_CRITICOS_TELA: tuple[str, ...] = ("btnSalvar", "fatConFPontaIndRegistrado")

# Campos do OCR → tela Consen (IDs HTML padrão BT)
CAMPOS_OCR: tuple[str, ...] = (
    "fatConFPontaIndRegistrado",
    "fatConFPontaIndFaturado",
    "fatConFPontaIndValorReais",
    "fatConFPontaInjetadoRegistrado",
    "fatConFPontaInjetadoFaturado",
    "fatConFPontaInjetadoValorReais",
    "fatICMS",
    "fatICMSBase",
    "fatDesIcmsAliquota",
    "fatPIS",
    "fatDescPisAliquota",
    "fatCOFINS",
    "fatDesCofinsAliquota",
    "fatDescIrpjValRetImposto",
    "fatDescIrpjPercRetImposto",
    "fatDescCsllValRetImposto",
    "fatDescCsllPercRetImposto",
    "fatDescConsumoValRetImposto",
    "fatDescConsumoPercRetImposto",
    "fatValBandeira",
    "fatIlumPublica",
)

CONFIG = CorrecaoFluxoConfig(
    saida_dir=SAIDA_DIR,
    execucao_csv=EXECUCAO_CSV,
    edit_url=EDIT_URL,
    ordem_campos=CAMPOS_OCR,
    fechar_ao_final=FECHAR_AO_FINAL,
)


def normalizar_carimbo(c: str) -> str:
    return fluxo_base.normalizar_carimbo(c)


def valor_vazio(v: Any) -> bool:
    return fluxo_base.valor_vazio(v)


def _correcoes_do_ocr(ocr: dict) -> dict[str, Any]:
    correcoes: dict[str, Any] = {}
    for campo in CAMPOS_OCR:
        val = ocr.get(campo)
        if valor_vazio(val):
            continue
        try:
            if float(val) == 0.0:
                continue
        except (TypeError, ValueError):
            pass
        correcoes[campo] = formatar_valor_para_campo(campo, val, "text")
    return correcoes


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Digitação Equatorial AL BT por carimbo")
    p.add_argument("--pdf", required=True, help="Caminho do PDF da fatura")
    p.add_argument("--carimbo", type=str, default="", help="Carimbo (inferido do nome do PDF se omitido)")
    p.add_argument("--salvar", action="store_true", help="Salva após preencher")
    p.add_argument("--sem-snapshot", action="store_true", help="Não salva HTML/JSON da tela")
    p.add_argument("--preparar-apenas", action="store_true", help="Só mostra campos sem abrir o navegador")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    pdf = Path(args.pdf)
    if not pdf.exists():
        print(f"PDF nao encontrado: {pdf}")
        return 2

    carimbo = normalizar_carimbo(args.carimbo or pdf.stem)
    log(f"Processando OCR: {pdf.name}")
    ocr = processar_pdf_al(pdf)

    correcoes = _correcoes_do_ocr(ocr)
    if not correcoes:
        log("OCR nao retornou campos para digitar.")
        return 1

    log(f"Campos prontos ({len(correcoes)}): {', '.join(correcoes)}")

    if args.preparar_apenas:
        for k, v in correcoes.items():
            print(f"  {k}: {v}")
        return 0

    SAIDA_DIR.mkdir(parents=True, exist_ok=True)
    driver = None
    try:
        driver, wait = fluxo_base.abrir_driver_logado()
        time.sleep(3.0)

        fluxo_base.abrir_tela_edicao_carimbo(driver, wait, CONFIG.edit_url)
        time.sleep(1.5)
        fluxo_base.carregar_fatura_por_carimbo(driver, wait, carimbo, CAMPOS_CRITICOS_TELA)
        time.sleep(1.0)

        if not args.sem_snapshot:
            fluxo_base.salvar_snapshot(driver, SAIDA_DIR, carimbo)

        qtd, confirmadas, total = fluxo_base.aplicar_correcoes(
            driver, wait, carimbo, correcoes, CONFIG.ordem_campos
        )

        if args.salvar:
            if confirmadas < total:
                warn(f"BB_{carimbo}: {confirmadas}/{total} confirmados — salvamento bloqueado.")
                fluxo_base.registrar_execucao(EXECUCAO_CSV, carimbo, "bloqueado_incompleto", f"{confirmadas}/{total}")
            else:
                time.sleep(1.0)
                fluxo_base.salvar_auditar_e_avancar(driver, wait, carimbo)
                fluxo_base.registrar_execucao(EXECUCAO_CSV, carimbo, "ok", f"{confirmadas}/{total}")
                log(f"BB_{carimbo}: salvo com sucesso.")
        else:
            fluxo_base.registrar_execucao(EXECUCAO_CSV, carimbo, "validado_sem_salvar", f"{confirmadas}/{total}")
            log(f"BB_{carimbo}: modo seguro — use --salvar para efetivar. ({confirmadas}/{total} campos prontos)")

        return 0
    finally:
        if driver and CONFIG.fechar_ao_final:
            try:
                driver.quit()
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
