#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Correcao COELBA: insere fatTributoFederalPerc + fatTributoFederalVal
em 86 faturas ja existentes no Consen, buscando pelo carimbo.

Regras:
  - 83 carimbos: aliquota 5,85%
  - 3 carimbos (2010687/2010688/2010693): aliquota 9,45%
  - 2 carimbos (2010651/2011482): sem retencao — ignorados
  - Nao preencher retencoes individuais (PIS/COFINS/CSLL/IRPJ)

Uso:
    python correcao_coelba_tributo_federal.py --salvar
    python correcao_coelba_tributo_federal.py --salvar --retomar-apos 2006528
    python correcao_coelba_tributo_federal.py --reprocessar-ok
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

SAIDA_DIR    = Path("//10.10.250.21/Energia/ARQUIVOS ENZO/NEOENERGIA_BAHIA_pipeline_saida/correcoes_tributo_federal_coelba")
EXECUCAO_CSV = SAIDA_DIR / "correcao_execucao.csv"
CAMPOS_CRITICOS: tuple[str, ...] = ("btnSalvar", "instalacao")

# carimbo -> (fatTributoFederalPerc, fatTributoFederalVal)
# 2010651 e 2011482 ausentes: sem retencao, nao processados.
DADOS: dict[str, tuple[float, float]] = {
    "2005176": (5.85, -542.79),
    "2005185": (5.85, -336.11),
    "2005187": (5.85, -238.18),
    "2005246": (5.85, -241.93),
    "2005249": (5.85, -978.53),
    "2005252": (5.85, -415.49),
    "2005253": (5.85, -428.24),
    "2005771": (5.85, -258.43),
    "2005772": (5.85, -215.73),
    "2005773": (5.85, -310.70),
    "2005777": (5.85, -437.91),
    "2005932": (5.85, -241.53),
    "2005940": (5.85, -313.60),
    "2005956": (5.85, -124.45),
    "2005957": (5.85, -228.67),
    "2005965": (5.85, -308.92),
    "2005994": (5.85,  -30.69),
    "2005995": (5.85, -427.63),
    "2005996": (5.85, -363.97),
    "2006002": (5.85, -208.69),
    "2006006": (5.85, -242.65),
    "2006013": (5.85, -158.08),
    "2006015": (5.85, -280.45),
    "2006020": (5.85, -319.74),
    "2006024": (5.85, -199.23),
    "2006027": (5.85, -284.28),
    "2006031": (5.85, -274.85),
    "2006033": (5.85, -229.25),
    "2006034": (5.85,   -6.59),
    "2006035": (5.85, -293.03),
    "2006036": (5.85, -381.06),
    "2006038": (5.85, -275.77),
    "2006041": (5.85, -247.95),
    "2006043": (5.85, -294.43),
    "2006045": (5.85, -181.82),
    "2006059": (5.85, -283.55),
    "2006062": (5.85, -196.53),
    "2006064": (5.85, -299.39),
    "2006065": (5.85, -547.87),
    "2006066": (5.85, -350.29),
    "2006076": (5.85, -263.86),
    "2006528": (5.85, -368.03),
    "2006529": (5.85, -234.47),
    "2006531": (5.85, -208.01),
    "2006540": (5.85, -282.30),
    "2006541": (5.85, -230.45),
    "2006542": (5.85, -347.72),
    "2006543": (5.85, -281.05),
    "2006545": (5.85, -272.37),
    "2006552": (5.85, -265.62),
    "2006553": (5.85, -325.01),
    "2006555": (5.85, -413.92),
    "2006557": (5.85, -203.17),
    "2006559": (5.85, -481.28),
    "2006606": (5.85,-1119.54),
    "2007586": (5.85, -389.95),
    "2007594": (5.85, -255.46),
    "2007599": (5.85, -257.42),
    "2007601": (5.85, -126.50),
    "2007602": (5.85, -295.38),
    "2007604": (5.85, -450.93),
    "2007605": (5.85, -276.29),
    "2007606": (5.85, -173.69),
    "2007607": (5.85, -284.73),
    "2007610": (5.85, -398.79),
    "2007612": (5.85, -334.92),
    "2007616": (5.85, -235.84),
    "2007617": (5.85, -379.18),
    "2007618": (5.85, -435.89),
    "2007620": (5.85, -198.60),
    "2007621": (5.85, -293.49),
    "2007622": (5.85, -271.37),
    "2007623": (5.85, -460.69),
    "2007624": (5.85, -504.01),
    "2007625": (5.85, -320.66),
    "2007626": (5.85, -493.70),
    "2007629": (5.85, -422.61),
    "2007630": (5.85, -262.41),
    "2010640": (5.85, -368.20),
    "2010641": (5.85, -364.25),
    "2010642": (5.85, -363.30),
    "2010650": (5.85, -349.18),
    "2010667": (5.85, -178.72),
    "2010687": (9.45, -357.01),  # aliquota 9,45%
    "2010688": (9.45, -241.48),  # aliquota 9,45%
    "2010693": (9.45, -341.60),  # aliquota 9,45%
    "2010651": (0.0,    0.0),   # sem retencao — zera TribFed
    "2011482": (0.0,    0.0),   # sem retencao — zera TribFed
}


def _fmt(valor: float) -> str:
    return f"{valor:.2f}".replace(".", ",")


def _aplicar_tributo_federal(driver, carimbo: str, perc: float, val: float) -> int:
    resultado = driver.execute_script(
        """
        var campos = [
            ['fatTributoFederalPerc', arguments[0]],
            ['fatTributoFederalVal',  arguments[1]],
        ];
        var ok = 0;
        campos.forEach(function(par) {
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
        _fmt(perc),
        _fmt(val),
    )
    encontrados = int(resultado or 0)
    log(f"[CAMPO] BB_{carimbo}: fatTributoFederalPerc={_fmt(perc)} fatTributoFederalVal={_fmt(val)} | {encontrados}/2 encontrados")
    return encontrados


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Correcao COELBA: insere fatTributoFederal no Consen")
    p.add_argument("--salvar", action="store_true")
    p.add_argument("--retomar-apos", type=str, default="")
    p.add_argument("--reprocessar-ok", action="store_true")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    SAIDA_DIR.mkdir(parents=True, exist_ok=True)

    carimbos = sorted(DADOS.keys())

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

    log(f"COELBA — carimbos a processar: {len(carimbos)}")
    log("  2010651 e 2011482: sem retencao — TribFed sera zerado")

    driver = None
    try:
        driver, wait = fluxo_base.abrir_driver_logado()

        for carimbo in carimbos:
            perc, val = DADOS[carimbo]
            log(f"--- BB_{carimbo}  {perc}% / {_fmt(val)} ---")
            fluxo_base.registrar_execucao(EXECUCAO_CSV, carimbo, "iniciado")

            try:
                fluxo_base.abrir_tela_edicao_carimbo(driver, wait, EDIT_URL)
                fluxo_base.carregar_fatura_por_carimbo(driver, wait, carimbo, CAMPOS_CRITICOS)
            except Exception as e:
                warn(f"BB_{carimbo}: falha ao abrir — {type(e).__name__}: {e}")
                fluxo_base.registrar_execucao(EXECUCAO_CSV, carimbo, "erro_navegacao", str(e))
                continue

            time.sleep(0.3)
            encontrados = _aplicar_tributo_federal(driver, carimbo, perc, val)

            if encontrados == 0:
                warn(f"BB_{carimbo}: campos nao encontrados na tela")
                fluxo_base.registrar_execucao(EXECUCAO_CSV, carimbo, "campos_nao_encontrados")
                continue

            if args.salvar:
                try:
                    clicar_botao_salvar(driver, wait)
                    _aguardar_sem_spinner(driver, timeout=6, min_wait=0.3)
                    fluxo_base.registrar_execucao(EXECUCAO_CSV, carimbo, "ok", f"{perc}% {_fmt(val)}")
                    log(f"BB_{carimbo}: salvo OK")
                except Exception as e:
                    warn(f"BB_{carimbo}: erro ao salvar — {type(e).__name__}: {e}")
                    fluxo_base.registrar_execucao(EXECUCAO_CSV, carimbo, "erro_salvar", str(e))
            else:
                log(f"BB_{carimbo}: preparado (use --salvar para efetivar)")
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
