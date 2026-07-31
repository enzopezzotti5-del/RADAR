#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Correcao pontual CELPE: insere fatTributoFederalPerc + fatTributoFederalVal
em faturas ja existentes no Consen, buscando pelo carimbo.

Uso:
    python correcao_celpe_tributo_federal.py --salvar
    python correcao_celpe_tributo_federal.py --salvar --retomar-apos 2010470
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

SAIDA_DIR    = Path("//10.10.250.21/Energia/ARQUIVOS ENZO/NEOENERGIA_pipeline_saida/correcoes_tributo_federal")
EXECUCAO_CSV = SAIDA_DIR / "correcao_execucao.csv"

os.environ.setdefault("CONSEN_PIPELINE_SAIDA", str(SAIDA_DIR))

CAMPOS_CRITICOS: tuple[str, ...] = ("btnSalvar", "instalacao")

# carimbo -> (fatTributoFederalPerc, fatTributoFederalVal)
DADOS: dict[str, tuple[float, float]] = {
    "2008262": (5.85, -190.01), "2008263": (5.85,  -74.08),
    "2009482": (5.85, -229.92), "2009483": (5.85,   -8.68),
    "2010460": (9.45, -152.70), "2010461": (9.45, -143.29),
    "2010463": (9.45, -139.67), "2010466": (9.45, -139.34),
    "2010467": (9.45, -333.18), "2010468": (9.45, -141.90),
    "2010469": (9.45, -345.19), "2010470": (9.45, -139.60),
    "2010472": (9.45, -140.63), "2010475": (9.45, -139.25),
    "2010478": (9.45, -139.93), "2010480": (9.45, -138.86),
    "2010481": (9.45, -327.71), "2010482": (9.45, -139.48),
    "2010483": (9.45, -336.65), "2010484": (9.45, -141.58),
    "2010485": (9.45, -328.82), "2010486": (9.45, -138.77),
    "2010496": (9.45, -400.12), "2010498": (9.45, -117.13),
    "2010505": (9.45, -151.18), "2010507": (9.45, -156.02),
    "2010508": (9.45, -156.84), "2010510": (9.45, -194.97),
    "2010516": (9.45, -423.86), "2010523": (9.45, -195.26),
    "2010527": (9.45, -179.10), "2010537": (9.45, -216.52),
    "2011220": (5.85, -205.37), "2011221": (5.85, -135.60),
}


def _fmt_br(valor: float) -> str:
    """Formata float para o padrao brasileiro (ex: -152,70)."""
    s = f"{valor:.2f}".replace(".", ",")
    return s


def _aplicar_dois_campos(driver, carimbo: str, perc: float, val: float) -> bool:
    """Injeta os dois campos via JS em batch e retorna True se ambos foram encontrados."""
    perc_str = _fmt_br(perc)
    val_str  = _fmt_br(val)

    resultado = driver.execute_script(
        """
        var campos = [
            ['fatTributoFederalPerc', arguments[0]],
            ['fatTributoFederalVal',  arguments[1]],
        ];
        var ok = 0;
        campos.forEach(function(par) {
            var id = par[0], val = par[1];
            var el = document.getElementById(id) || document.getElementsByName(id)[0];
            if (el) {
                el.value = val;
                el.dispatchEvent(new Event('input',  {bubbles:true}));
                el.dispatchEvent(new Event('change', {bubbles:true}));
                el.dispatchEvent(new Event('blur',   {bubbles:true}));
                ok++;
            }
        });
        return ok;
        """,
        perc_str,
        val_str,
    )

    encontrados = int(resultado or 0)
    log(f"[CAMPO] BB_{carimbo}: fatTributoFederalPerc={perc_str} fatTributoFederalVal={val_str} | campos preenchidos={encontrados}/2")
    return encontrados == 2


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Correcao CELPE: insere fatTributoFederal no Consen")
    p.add_argument("--salvar", action="store_true", help="Salva e audita apos corrigir (padrao: modo leitura)")
    p.add_argument("--retomar-apos", type=str, default="", help="Retoma apos este carimbo")
    p.add_argument("--reprocessar-ok", action="store_true", help="Reprocessa mesmo os ja marcados ok")
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

    log(f"Carimbos a corrigir: {len(carimbos)}")

    driver = None
    try:
        driver, wait = fluxo_base.abrir_driver_logado()

        for carimbo in carimbos:
            fluxo_base.registrar_execucao(EXECUCAO_CSV, carimbo, "iniciado")
            perc, val = DADOS[carimbo]

            try:
                fluxo_base.abrir_tela_edicao_carimbo(driver, wait, EDIT_URL)
                fluxo_base.carregar_fatura_por_carimbo(driver, wait, carimbo, CAMPOS_CRITICOS)
            except Exception as e:
                warn(f"BB_{carimbo}: falha ao abrir fatura — {type(e).__name__}: {e}")
                fluxo_base.registrar_execucao(EXECUCAO_CSV, carimbo, "erro_navegacao", str(e))
                continue

            time.sleep(0.3)
            ok = _aplicar_dois_campos(driver, carimbo, perc, val)

            if not ok:
                warn(f"BB_{carimbo}: campos nao encontrados na tela")
                fluxo_base.registrar_execucao(EXECUCAO_CSV, carimbo, "campos_nao_encontrados")
                continue

            if args.salvar:
                try:
                    clicar_botao_salvar(driver, wait)
                    _aguardar_sem_spinner(driver, timeout=6, min_wait=0.3)
                    fluxo_base.registrar_execucao(EXECUCAO_CSV, carimbo, "ok", f"{perc}% {val}")
                    log(f"BB_{carimbo}: salvo OK")
                except Exception as e:
                    warn(f"BB_{carimbo}: erro ao salvar — {type(e).__name__}: {e}")
                    fluxo_base.registrar_execucao(EXECUCAO_CSV, carimbo, "erro_salvar", str(e))
            else:
                log(f"BB_{carimbo}: correcao aplicada (modo leitura, use --salvar para efetivar)")
                fluxo_base.registrar_execucao(EXECUCAO_CSV, carimbo, "preparado_nao_salvo")

    except KeyboardInterrupt:
        log("Interrompido pelo usuario.")
    except Exception as e:
        warn(f"Erro inesperado: {type(e).__name__}: {e}")
        return 1
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass

    return 0


if __name__ == "__main__":
    sys.exit(main())
