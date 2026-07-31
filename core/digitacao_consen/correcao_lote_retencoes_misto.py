#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Correcao de retencoes — lote misto (15 carimbos).

Grupos:
  A) NEOENERGIA (12): apenas fatTributoFederalPerc + fatTributoFederalVal
       998348, 998351, 998354, 998356, 998358 -> 5,85%
       2010237, 2010238, 2010244             -> 9,45%
       2010239, 2010784, 2010975, 2010978    -> 5,85%

  B) ENEL CE (1): apenas fatTributoFederalPerc + fatTributoFederalVal
       2008514 -> 5,85% / -248,01

  C) EDP SP (2): retencoes individuais (PIS/COFINS/CSLL/IRPJ) + zera TribFed
       2010022 -> PIS=-47,56 COF=-219,57 CSLL=-73,20 IRPJ=-87,83
       2010798 -> PIS=-37,79 COF=-174,43 CSLL=-58,14 IRPJ=-69,77

Uso:
    python correcao_lote_retencoes_misto.py --salvar
    python correcao_lote_retencoes_misto.py --salvar --retomar-apos 2010022
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import _venv_check  # noqa: F401

try:
    from digitacao_consen import correcao_fluxo_base as fluxo_base
    from digitacao_consen.correcao_fluxo_base import log, warn
    from digitacao_consen.digitacao_consen_enel import clicar_botao_salvar, _aguardar_sem_spinner
except ModuleNotFoundError:
    import correcao_fluxo_base as fluxo_base  # type: ignore
    from correcao_fluxo_base import log, warn  # type: ignore
    from digitacao_consen_enel import clicar_botao_salvar, _aguardar_sem_spinner  # type: ignore

LOGIN_URL = fluxo_base.LOGIN_URL
BASE_URL  = LOGIN_URL.rsplit("login.php", 1)[0]
EDIT_URL  = os.environ.get(
    "CONSEN_EDITA_FATURA_CARIMBO_URL",
    f"{BASE_URL}index.php#bpg/gestao/fatura/editaFaturaCarimbo.php",
)

SAIDA_DIR    = Path("//10.10.250.21/Energia/ARQUIVOS ENZO/NEOENERGIA_BAHIA_pipeline_saida/correcoes_lote_misto")
EXECUCAO_CSV = SAIDA_DIR / "correcao_execucao.csv"
CAMPOS_CRITICOS: tuple[str, ...] = ("btnSalvar", "instalacao")


# ---------------------------------------------------------------------------
# GRUPO A + B: apenas TributoFederal
# carimbo -> (perc, val)
# ---------------------------------------------------------------------------
TRIB_FEDERAL: dict[str, tuple[float, float]] = {
    # NEOENERGIA 5,85%
    "998348":  (5.85, -275.58),
    "998351":  (5.85, -216.02),
    "998354":  (5.85, -182.57),
    "998356":  (5.85, -358.90),
    "998358":  (5.85, -270.91),
    "2010239": (5.85,  -45.15),
    "2010784": (5.85,   -6.17),
    "2010975": (5.85,  -74.52),
    "2010978": (5.85, -199.87),
    # NEOENERGIA 9,45%
    "2010237": (9.45, -324.50),
    "2010238": (9.45, -272.18),
    "2010244": (9.45, -349.44),
    # ENEL CE 5,85%
    "2008514": (5.85, -248.01),
}

# ---------------------------------------------------------------------------
# GRUPO C: retencoes individuais + zera TributoFederal
# carimbo -> dict com campos
# ---------------------------------------------------------------------------
RETENCOES_INDIVIDUAIS: dict[str, dict] = {
    "2010022": dict(
        pis_perc=0.65,  pis_val=-47.56,
        cof_perc=3.0,   cof_val=-219.57,
        csll_perc=1.0,  csll_val=-73.20,
        irpj_perc=1.2,  irpj_val=-87.83,
    ),
    "2010798": dict(
        pis_perc=0.65,  pis_val=-37.79,
        cof_perc=3.0,   cof_val=-174.43,
        csll_perc=1.0,  csll_val=-58.14,
        irpj_perc=1.2,  irpj_val=-69.77,
    ),
}

TODOS_CARIMBOS = sorted(set(TRIB_FEDERAL) | set(RETENCOES_INDIVIDUAIS))


def _fmt(valor: float) -> str:
    return f"{valor:.2f}".replace(".", ",")


def _aplicar_trib_federal(driver, carimbo: str, perc: float, val: float) -> int:
    resultado = driver.execute_script(
        """
        var pares = [['fatTributoFederalPerc', arguments[0]], ['fatTributoFederalVal', arguments[1]]];
        var ok = 0;
        pares.forEach(function(par) {
            var el = document.getElementById(par[0]) || document.getElementsByName(par[0])[0];
            if (el) {
                el.value = par[1];
                el.dispatchEvent(new Event('input',  {bubbles:true}));
                el.dispatchEvent(new Event('change', {bubbles:true}));
                el.dispatchEvent(new Event('blur',   {bubbles:true}));
                ok++;
            }
        });
        return ok;
        """,
        _fmt(perc), _fmt(val),
    )
    encontrados = int(resultado or 0)
    log(f"[CAMPO] BB_{carimbo}: TribFed={_fmt(perc)}%/{_fmt(val)} | {encontrados}/2")
    return encontrados


def _aplicar_retencoes_individuais(driver, carimbo: str, d: dict) -> int:
    pares = [
        ("fatTributoFederalPerc",    "0"),
        ("fatTributoFederalVal",     "0"),
        ("fatDescPisPercRetImposto",    _fmt(d["pis_perc"])),
        ("fatDescPisValRetImposto",     _fmt(d["pis_val"])),
        ("fatDescCofinsPercRetImposto", _fmt(d["cof_perc"])),
        ("fatDescCofinsValRetImposto",  _fmt(d["cof_val"])),
        ("fatDescCsllPercRetImposto",   _fmt(d["csll_perc"])),
        ("fatDescCsllValRetImposto",    _fmt(d["csll_val"])),
        ("fatDescIrpjPercRetImposto",   _fmt(d["irpj_perc"])),
        ("fatDescIrpjValRetImposto",    _fmt(d["irpj_val"])),
    ]
    resultado = driver.execute_script(
        """
        var pares = arguments[0]; var ok = 0;
        pares.forEach(function(par) {
            var el = document.getElementById(par[0]) || document.getElementsByName(par[0])[0];
            if (el) {
                el.value = par[1];
                el.dispatchEvent(new Event('input',  {bubbles:true}));
                el.dispatchEvent(new Event('change', {bubbles:true}));
                el.dispatchEvent(new Event('blur',   {bubbles:true}));
                ok++;
            }
        });
        return ok;
        """,
        [[id_, val] for id_, val in pares],
    )
    encontrados = int(resultado or 0)
    log(f"[CAMPO] BB_{carimbo}: TribFed=0 PIS={_fmt(d['pis_val'])} COF={_fmt(d['cof_val'])} CSLL={_fmt(d['csll_val'])} IRPJ={_fmt(d['irpj_val'])} | {encontrados}/{len(pares)}")
    return encontrados


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Correcao lote misto de retencoes")
    p.add_argument("--salvar", action="store_true")
    p.add_argument("--retomar-apos", type=str, default="")
    p.add_argument("--reprocessar-ok", action="store_true")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    SAIDA_DIR.mkdir(parents=True, exist_ok=True)

    carimbos = TODOS_CARIMBOS[:]

    if args.retomar_apos:
        marcador = args.retomar_apos.replace("BB_", "").strip()
        if marcador in carimbos:
            carimbos = carimbos[carimbos.index(marcador) + 1:]

    if not args.reprocessar_ok:
        status_ok = fluxo_base.carregar_status_execucao(EXECUCAO_CSV)
        carimbos = [c for c in carimbos if status_ok.get(c) != "ok"]

    if not carimbos:
        log("Nenhum carimbo pendente.")
        return 0

    log(f"Carimbos a processar: {len(carimbos)}")
    for c in carimbos:
        if c in TRIB_FEDERAL:
            p, v = TRIB_FEDERAL[c]
            log(f"  BB_{c}: TribFed {p}% / {_fmt(v)}")
        else:
            d = RETENCOES_INDIVIDUAIS[c]
            log(f"  BB_{c}: EDP SP — PIS={_fmt(d['pis_val'])} COF={_fmt(d['cof_val'])} CSLL={_fmt(d['csll_val'])} IRPJ={_fmt(d['irpj_val'])}")

    driver = None
    try:
        driver, wait = fluxo_base.abrir_driver_logado()

        for carimbo in carimbos:
            log(f"--- BB_{carimbo} ---")
            fluxo_base.registrar_execucao(EXECUCAO_CSV, carimbo, "iniciado")

            try:
                fluxo_base.abrir_tela_edicao_carimbo(driver, wait, EDIT_URL)
                fluxo_base.carregar_fatura_por_carimbo(driver, wait, carimbo, CAMPOS_CRITICOS)
            except Exception as e:
                warn(f"BB_{carimbo}: falha ao abrir — {type(e).__name__}: {e}")
                fluxo_base.registrar_execucao(EXECUCAO_CSV, carimbo, "erro_navegacao", str(e))
                continue

            time.sleep(0.35)

            if carimbo in TRIB_FEDERAL:
                perc, val = TRIB_FEDERAL[carimbo]
                encontrados = _aplicar_trib_federal(driver, carimbo, perc, val)
                detalhe = f"{perc}% {_fmt(val)}"
            else:
                encontrados = _aplicar_retencoes_individuais(driver, carimbo, RETENCOES_INDIVIDUAIS[carimbo])
                detalhe = "retencoes individuais"

            if encontrados == 0:
                warn(f"BB_{carimbo}: campos nao encontrados")
                fluxo_base.registrar_execucao(EXECUCAO_CSV, carimbo, "campos_nao_encontrados")
                continue

            if args.salvar:
                try:
                    clicar_botao_salvar(driver, wait)
                    _aguardar_sem_spinner(driver, timeout=6, min_wait=0.3)
                    fluxo_base.registrar_execucao(EXECUCAO_CSV, carimbo, "ok", detalhe)
                    log(f"BB_{carimbo}: salvo OK")
                except Exception as e:
                    warn(f"BB_{carimbo}: erro ao salvar — {type(e).__name__}: {e}")
                    fluxo_base.registrar_execucao(EXECUCAO_CSV, carimbo, "erro_salvar", str(e))
            else:
                log(f"BB_{carimbo}: preparado (use --salvar)")
                fluxo_base.registrar_execucao(EXECUCAO_CSV, carimbo, "preparado_nao_salvo")

    except KeyboardInterrupt:
        log("Interrompido.")
    except Exception as e:
        warn(f"Erro: {type(e).__name__}: {e}")
        return 1
    finally:
        if driver:
            try: driver.quit()
            except Exception: pass

    return 0


if __name__ == "__main__":
    sys.exit(main())
