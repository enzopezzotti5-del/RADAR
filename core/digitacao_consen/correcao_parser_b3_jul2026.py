#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Correção de campos específicos afetados pelas correções de parser (jul/2026).
Lê os XLSXs gerados pelo OCR em lote e atualiza apenas os campos corrigidos.

Concessionárias e campos:
  COELBA     → fatValBandeira + fatTributoFederalPerc/Val (consolida; zera individuais)
  ENEL SP    → fatValBandeira + fatTributoFederalPerc/Val (consolida; zera individuais) + fatMultas
  LIGHT      → fatMultas (apenas onde > 0)
  CELESC     → fatValBandeira
  CEEE       → fatValBandeira
  RGE SUL    → fatIlumPublica
  EQ GO      → fatMultas (apenas onde > 0)

Uso:
    python correcao_parser_b3_jul2026.py --conc COELBA --salvar
    python correcao_parser_b3_jul2026.py --conc ENEL_SP --salvar
    python correcao_parser_b3_jul2026.py --conc LIGHT --salvar
    python correcao_parser_b3_jul2026.py --conc CELESC --salvar
    python correcao_parser_b3_jul2026.py --conc CEEE --salvar
    python correcao_parser_b3_jul2026.py --conc RGE_SUL --salvar
    python correcao_parser_b3_jul2026.py --conc EQ_GO --salvar
    python correcao_parser_b3_jul2026.py --conc COELBA  # simula sem salvar
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
CORE = ROOT / "core"
if str(CORE) not in sys.path:
    sys.path.insert(0, str(CORE))

try:
    import _venv_check  # noqa: F401
except ModuleNotFoundError:
    pass

try:
    from digitacao_consen.consen_api import ConsenSession, formatar_correcao
    from digitacao_consen.correcao_fluxo_base import normalizar_carimbo, log, warn
except ModuleNotFoundError:
    from consen_api import ConsenSession, formatar_correcao  # type: ignore
    from correcao_fluxo_base import normalizar_carimbo, log, warn  # type: ignore

LOGS_DIR = ROOT / "logs"
SAIDA_DIR = Path("//10.10.250.21/Energia/ARQUIVOS ENZO/correcoes_parser_b3_jul2026")

# Campos individuais de retenção a zerar quando consolida em TributoFederal
_RETENCOES_INDIVIDUAIS = (
    "fatDescIrpjPercRetImposto", "fatDescIrpjValRetImposto",
    "fatDescPisPercRetImposto",  "fatDescPisValRetImposto",
    "fatDescCofinsPercRetImposto", "fatDescCofinsValRetImposto",
    "fatDescCsllPercRetImposto",   "fatDescCsllValRetImposto",
)

# ── Configuração por concessionária ──────────────────────────────────────────

def _cfg_coelba(row: dict) -> dict[str, str]:
    """COELBA: bandeira + consolidação federal."""
    c = {}
    band = float(row.get("fatValBandeira") or 0)
    if band > 0:
        c["fatValBandeira"] = formatar_correcao("fatValBandeira", band)
    val = float(row.get("fatTributoFederalVal") or 0)
    if val < 0:
        c["fatTributoFederalPerc"] = formatar_correcao("fatTributoFederalPerc", 5.85)
        c["fatTributoFederalVal"]  = formatar_correcao("fatTributoFederalVal", val)
        for campo in _RETENCOES_INDIVIDUAIS:
            c[campo] = formatar_correcao(campo, 0.0)
    return c


def _cfg_enel_sp(row: dict) -> dict[str, str]:
    """ENEL SP: bandeira + consolidação federal + multas."""
    c = {}
    band = float(row.get("fatValBandeira") or 0)
    if band > 0:
        c["fatValBandeira"] = formatar_correcao("fatValBandeira", band)
    val = float(row.get("fatTributoFederalVal") or 0)
    if val < 0:
        c["fatTributoFederalPerc"] = formatar_correcao("fatTributoFederalPerc", 5.85)
        c["fatTributoFederalVal"]  = formatar_correcao("fatTributoFederalVal", val)
        for campo in _RETENCOES_INDIVIDUAIS:
            c[campo] = formatar_correcao(campo, 0.0)
    multas = float(row.get("fatMultas") or 0)
    if multas > 0:
        c["fatMultas"] = formatar_correcao("fatMultas", multas)
    return c


def _cfg_light(row: dict) -> dict[str, str]:
    """LIGHT: apenas multas onde > 0."""
    multas = float(row.get("fatMultas") or 0)
    if multas > 0:
        return {"fatMultas": formatar_correcao("fatMultas", multas)}
    return {}


def _cfg_celesc(row: dict) -> dict[str, str]:
    """CELESC: bandeira."""
    band = float(row.get("fatValBandeira") or 0)
    if band > 0:
        return {"fatValBandeira": formatar_correcao("fatValBandeira", band)}
    return {}


def _cfg_ceee(row: dict) -> dict[str, str]:
    """CEEE: bandeira."""
    band = float(row.get("fatValBandeira") or 0)
    if band > 0:
        return {"fatValBandeira": formatar_correcao("fatValBandeira", band)}
    return {}


def _cfg_rge_sul(row: dict) -> dict[str, str]:
    """RGE SUL: iluminação pública.
    O form CONSEN BT usa id='fatIluminacaoPublica' (não 'fatIlumPublica').
    """
    ilum = float(row.get("fatIlumPublica") or 0)
    if ilum > 0:
        return {"fatIluminacaoPublica": formatar_correcao("fatIlumPublica", ilum)}
    return {}


def _cfg_eq_go(row: dict) -> dict[str, str]:
    """EQ GO: multas onde > 0."""
    multas = float(row.get("fatMultas") or 0)
    if multas > 0:
        return {"fatMultas": formatar_correcao("fatMultas", multas)}
    return {}


CONCS: dict[str, tuple[str, callable]] = {
    "COELBA":   (str(LOGS_DIR / "ocr_correcoes_COELBA.xlsx"),    _cfg_coelba),
    "ENEL_SP":  (str(LOGS_DIR / "ocr_correcoes_ENEL_SP.xlsx"),   _cfg_enel_sp),
    "LIGHT":    (str(LOGS_DIR / "ocr_correcoes_LIGHT.xlsx"),      _cfg_light),
    "CELESC":   (str(LOGS_DIR / "ocr_correcoes_CELESC.xlsx"),     _cfg_celesc),
    "CEEE":     (str(LOGS_DIR / "ocr_correcoes_CEEE.xlsx"),       _cfg_ceee),
    "RGE_SUL":  (str(LOGS_DIR / "ocr_correcoes_RGE_SUL.xlsx"),   _cfg_rge_sul),
    "EQ_GO":    (str(LOGS_DIR / "ocr_correcoes_EQ_GO.xlsx"),      _cfg_eq_go),
}

CAMPOS_CRITICOS = ("btnSalvar", "instalacao")


def rodar(conc: str, salvar: bool, retomar_apos: str = "") -> None:
    xlsx_path, cfg_fn = CONCS[conc]
    df = pd.read_excel(xlsx_path)

    SAIDA_DIR.mkdir(parents=True, exist_ok=True)
    execucao_csv = SAIDA_DIR / f"correcao_{conc.lower()}_execucao.csv"

    if not salvar:
        log("MODO SIMULACAO — use --salvar para efetivar.")

    retomando = bool(retomar_apos)

    with ConsenSession.abrir() as s:
        for _, row in df.iterrows():
            carimbo_raw = row.get("carimbo") or row.get("fatCarimbo") or ""
            carimbo = normalizar_carimbo(str(int(float(carimbo_raw))))

            if retomando:
                if normalizar_carimbo(str(int(float(retomar_apos)))) == carimbo:
                    retomando = False
                continue

            erros = row.get("ERRO")
            if erros and str(erros) not in {"", "nan", "None"}:
                warn(f"BB_{carimbo}: OCR com erro ({erros}) — pulado.")
                s.registrar(execucao_csv, carimbo, "ocr_erro", str(erros)[:100])
                continue

            correcoes = cfg_fn(dict(row))
            if not correcoes:
                log(f"BB_{carimbo}: sem campos a corrigir — pulado.")
                s.registrar(execucao_csv, carimbo, "sem_campo")
                continue

            log(f"=== BB_{carimbo} === ({len(correcoes)} campos)")

            try:
                s.buscar_carimbo(carimbo, CAMPOS_CRITICOS)
                resultado = s.editar_campos(correcoes, tuple(correcoes))

                if not salvar:
                    log(f"  {resultado.resumo()} [simulado]")
                    s.registrar(execucao_csv, carimbo, "simulado",
                                f"{resultado.n_alterados}/{len(correcoes)}")
                    continue

                s.salvar_e_auditar(rapido=True)
                log(f"  {resultado.resumo()} [SALVO]")
                s.registrar(execucao_csv, carimbo, "corrigido",
                             f"{resultado.n_alterados}/{len(correcoes)}")

            except Exception as exc:
                warn(f"  BB_{carimbo}: {type(exc).__name__}: {exc}")
                s.registrar(execucao_csv, carimbo, "erro", str(exc)[:200])


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Correção campos parser B3 jul/2026")
    p.add_argument("--conc", required=True, choices=list(CONCS),
                   help="Concessionária a processar")
    p.add_argument("--salvar", action="store_true",
                   help="Efetivar no CONSEN (sem flag = simulação)")
    p.add_argument("--retomar-apos", default="",
                   help="Pular até depois do carimbo informado")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    rodar(args.conc, args.salvar, args.retomar_apos)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
