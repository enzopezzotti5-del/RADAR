#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Correcao de retencoes: zera fatTributoFederalPerc/Val e preenche os
campos individuais (PIS/COFINS/CSLL/IRPJ perc e val) com os valores
corretos extraidos dos PDFs originais.

Concessionarias: CEMIG BT, CEEE, COPEL BT

Uso:
    python correcao_retencoes_tributo_federal.py --salvar
    python correcao_retencoes_tributo_federal.py --salvar --conc CEMIG
    python correcao_retencoes_tributo_federal.py --salvar --conc CEEE
    python correcao_retencoes_tributo_federal.py --salvar --conc COPEL
    python correcao_retencoes_tributo_federal.py --salvar --retomar-apos 2008044
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

SAIDA_DIR    = Path("//10.10.250.21/Energia/ARQUIVOS ENZO/CEMIG_pipeline_saida/correcoes_retencao_trib_fed")
EXECUCAO_CSV = SAIDA_DIR / "correcao_execucao.csv"
CAMPOS_CRITICOS: tuple[str, ...] = ("btnSalvar", "instalacao")


# ---------------------------------------------------------------------------
# Dados extraidos via OCR dos PDFs originais.
#
# Campos por carimbo:
#   pis_perc  / pis_val   — retenção PIS (%)  e valor R$ (negativo)
#   cof_perc  / cof_val   — retenção COFINS
#   csll_perc / csll_val  — retenção CSLL
#   irpj_perc / irpj_val  — retenção IRPJ (-1 = convenção CEMIG BT "nao se aplica individualmente")
#
# CEMIG 2008383: sem retencoes individuais (fatura de Custo de Disponibilidade).
#   Apenas zera TributoFederal.
# ---------------------------------------------------------------------------

DADOS: dict[str, dict] = {
    # ── CEMIG BT ──────────────────────────────────────────────────────────
    "2007942": dict(pis_perc=0.65, pis_val=-0.73, cof_perc=3.0, cof_val=-3.39, csll_perc=1.0, csll_val=-1.13, irpj_perc=-1, irpj_val=-1.35),
    "2007943": dict(pis_perc=0.65, pis_val=-0.73, cof_perc=3.0, cof_val=-3.39, csll_perc=1.0, csll_val=-1.13, irpj_perc=-1, irpj_val=-1.35),
    "2007944": dict(pis_perc=0.65, pis_val=-0.73, cof_perc=3.0, cof_val=-3.39, csll_perc=1.0, csll_val=-1.13, irpj_perc=-1, irpj_val=-1.35),
    "2007945": dict(pis_perc=0.65, pis_val=-0.73, cof_perc=3.0, cof_val=-3.39, csll_perc=1.0, csll_val=-1.13, irpj_perc=-1, irpj_val=-1.35),
    "2007946": dict(pis_perc=0.65, pis_val=-0.73, cof_perc=3.0, cof_val=-3.39, csll_perc=1.0, csll_val=-1.13, irpj_perc=-1, irpj_val=-1.35),
    "2007947": dict(pis_perc=0.65, pis_val=-0.73, cof_perc=3.0, cof_val=-3.39, csll_perc=1.0, csll_val=-1.13, irpj_perc=-1, irpj_val=-1.35),
    "2007948": dict(pis_perc=0.65, pis_val=-0.73, cof_perc=3.0, cof_val=-3.39, csll_perc=1.0, csll_val=-1.13, irpj_perc=-1, irpj_val=-1.35),
    "2008060": dict(pis_perc=0.65, pis_val=-0.36, cof_perc=3.0, cof_val=-1.69, csll_perc=1.0, csll_val=-0.56, irpj_perc=-1, irpj_val=-0.67),
    "2008383": dict(),  # Custo de Disponibilidade — sem retencoes individuais; apenas zera TribFed

    # ── CEEE ──────────────────────────────────────────────────────────────
    "2008044": dict(pis_perc=0.65, pis_val=-10.98, cof_perc=3.0, cof_val=-50.67, csll_perc=1.0, csll_val=-16.89, irpj_perc=1.2, irpj_val=-20.27),
    "2008045": dict(pis_perc=0.65, pis_val=-0.73,  cof_perc=3.0, cof_val=-3.39,  csll_perc=1.0, csll_val=-1.13,  irpj_perc=1.2, irpj_val=-1.35),
    "2008046": dict(pis_perc=0.65, pis_val=-0.36,  cof_perc=3.0, cof_val=-1.69,  csll_perc=1.0, csll_val=-0.56,  irpj_perc=1.2, irpj_val=-0.67),
    "2008047": dict(pis_perc=0.65, pis_val=-0.73,  cof_perc=3.0, cof_val=-3.39,  csll_perc=1.0, csll_val=-1.13,  irpj_perc=1.2, irpj_val=-1.35),
    "2008048": dict(pis_perc=0.65, pis_val=-0.73,  cof_perc=3.0, cof_val=-3.39,  csll_perc=1.0, csll_val=-1.13,  irpj_perc=1.2, irpj_val=-1.35),
    "2008049": dict(pis_perc=0.65, pis_val=-0.73,  cof_perc=3.0, cof_val=-3.39,  csll_perc=1.0, csll_val=-1.13,  irpj_perc=1.2, irpj_val=-1.35),
    "2008050": dict(pis_perc=0.65, pis_val=-0.73,  cof_perc=3.0, cof_val=-3.39,  csll_perc=1.0, csll_val=-1.13,  irpj_perc=1.2, irpj_val=-1.35),
    "2008051": dict(pis_perc=0.65, pis_val=-0.73,  cof_perc=3.0, cof_val=-3.39,  csll_perc=1.0, csll_val=-1.13,  irpj_perc=1.2, irpj_val=-1.35),
    "2008053": dict(pis_perc=0.65, pis_val=-19.03, cof_perc=3.0, cof_val=-87.84, csll_perc=1.0, csll_val=-29.28, irpj_perc=1.2, irpj_val=-35.13),
    "2008054": dict(pis_perc=0.65, pis_val=-0.73,  cof_perc=3.0, cof_val=-3.39,  csll_perc=1.0, csll_val=-1.13,  irpj_perc=1.2, irpj_val=-1.35),
    "2008055": dict(pis_perc=0.65, pis_val=-0.73,  cof_perc=3.0, cof_val=-3.39,  csll_perc=1.0, csll_val=-1.13,  irpj_perc=1.2, irpj_val=-1.35),
    "2008059": dict(pis_perc=0.65, pis_val=-21.34, cof_perc=3.0, cof_val=-98.50, csll_perc=1.0, csll_val=-32.83, irpj_perc=1.2, irpj_val=-39.40),

    # ── COPEL BT ──────────────────────────────────────────────────────────
    "2008056": dict(pis_perc=0.65, pis_val=-0.73,   cof_perc=3.0, cof_val=-3.39,   csll_perc=1.0, csll_val=-1.13,   irpj_perc=1.2, irpj_val=-1.35),
    "2008058": dict(pis_perc=0.65, pis_val=-81.66,  cof_perc=3.0, cof_val=-376.92, csll_perc=1.0, csll_val=-125.64, irpj_perc=1.2, irpj_val=-150.77),
}

CONC_MAP: dict[str, str] = {
    **{c: "CEMIG" for c in ["2007942","2007943","2007944","2007945","2007946","2007947","2007948","2008060","2008383"]},
    **{c: "CEEE"  for c in ["2008044","2008045","2008046","2008047","2008048","2008049","2008050","2008051","2008053","2008054","2008055","2008059"]},
    **{c: "COPEL" for c in ["2008056","2008058"]},
}


def _fmt(valor: float) -> str:
    return f"{valor:.2f}".replace(".", ",")


def _aplicar_correcao(driver, carimbo: str, d: dict) -> int:
    """
    Injeta os campos de retencao corrigidos e zera TributoFederal via JS.
    Retorna numero de campos encontrados na tela.
    """
    pares: list[tuple[str, str]] = [
        ("fatTributoFederalPerc", "0"),
        ("fatTributoFederalVal",  "0"),
    ]

    if d:  # dict vazio = sem retencoes individuais (2008383)
        pares += [
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
        var pares = arguments[0];
        var ok = 0;
        pares.forEach(function(par) {
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
        [[id_, val] for id_, val in pares],
    )

    encontrados = int(resultado or 0)
    esperados = len(pares)
    campos_str = ", ".join(f"{id_}={val}" for id_, val in pares)
    log(f"[CAMPO] BB_{carimbo}: {campos_str} | {encontrados}/{esperados} encontrados")
    return encontrados


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Correcao: preenche retencoes individuais e zera TributoFederal"
    )
    p.add_argument("--salvar", action="store_true",
                   help="Salva cada fatura (padrao: modo leitura)")
    p.add_argument("--retomar-apos", type=str, default="",
                   help="Pula carimbos ate e inclusive este")
    p.add_argument("--reprocessar-ok", action="store_true",
                   help="Reprocessa mesmo os ja marcados ok")
    p.add_argument("--conc", type=str, default="",
                   choices=["CEMIG", "CEEE", "COPEL", ""],
                   help="Filtra por concessionaria (padrao: todas)")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    SAIDA_DIR.mkdir(parents=True, exist_ok=True)

    carimbos = sorted(DADOS.keys())

    if args.conc:
        carimbos = [c for c in carimbos if CONC_MAP.get(c) == args.conc]

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
    for c in carimbos:
        conc = CONC_MAP.get(c, "?")
        d = DADOS[c]
        if d:
            log(f"  {conc} BB_{c}: PIS={d['pis_val']} COF={d['cof_val']} CSLL={d['csll_val']} IRPJ={d['irpj_val']}")
        else:
            log(f"  {conc} BB_{c}: sem retencoes individuais — apenas zera TribFed")

    driver = None
    try:
        driver, wait = fluxo_base.abrir_driver_logado()

        for carimbo in carimbos:
            conc = CONC_MAP.get(carimbo, "?")
            log(f"--- {conc} BB_{carimbo} ---")
            fluxo_base.registrar_execucao(EXECUCAO_CSV, carimbo, "iniciado", conc)

            try:
                fluxo_base.abrir_tela_edicao_carimbo(driver, wait, EDIT_URL)
                fluxo_base.carregar_fatura_por_carimbo(driver, wait, carimbo, CAMPOS_CRITICOS)
            except Exception as e:
                warn(f"BB_{carimbo}: falha ao abrir — {type(e).__name__}: {e}")
                fluxo_base.registrar_execucao(EXECUCAO_CSV, carimbo, "erro_navegacao", str(e))
                continue

            time.sleep(0.4)
            encontrados = _aplicar_correcao(driver, carimbo, DADOS[carimbo])

            if encontrados == 0:
                warn(f"BB_{carimbo}: nenhum campo encontrado na tela")
                fluxo_base.registrar_execucao(EXECUCAO_CSV, carimbo, "campos_nao_encontrados", conc)
                continue

            if args.salvar:
                try:
                    clicar_botao_salvar(driver, wait)
                    _aguardar_sem_spinner(driver, timeout=6, min_wait=0.3)
                    fluxo_base.registrar_execucao(EXECUCAO_CSV, carimbo, "ok", f"{conc} corrigido")
                    log(f"BB_{carimbo}: salvo OK")
                except Exception as e:
                    warn(f"BB_{carimbo}: erro ao salvar — {type(e).__name__}: {e}")
                    fluxo_base.registrar_execucao(EXECUCAO_CSV, carimbo, "erro_salvar", str(e))
            else:
                log(f"BB_{carimbo}: preparado sem salvar (use --salvar para efetivar)")
                fluxo_base.registrar_execucao(EXECUCAO_CSV, carimbo, "preparado_nao_salvo", conc)

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
