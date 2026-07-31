#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Correção de campos específicos afetados pelas correções de parser — campanha MENOR (jul/2026).
Lê os XLSXs gerados pelo OCR em lote (ocr_lote_correcoes_parser_b3_menor.py) e atualiza
apenas os campos corrigidos no CONSEN.

Concessionárias e campos:
  CELESC  → fatValBandeira + fatValBandeira2 + fatDescPisAliquota + fatDesCofinsAliquota
  COPEL   → fatValBandeira + fatValBandeira2
  CPFL    → fatValBandeira + obs 109 (devolução Fat. Maior, quando presente)
  EDP SP  → 4 pares de retenção (PIS/COFINS/CSLL/IRPJ perc+val)
  RGE     → fatIluminacaoPublica
  LIGHT   → fatMultasDiversas (apenas onde > 0)
  EQ GO   → fatMultasDiversas (apenas onde > 0)

Uso:
    python correcao_parser_b3_menor_jul2026.py --conc CELESC --salvar
    python correcao_parser_b3_menor_jul2026.py --conc COPEL --salvar
    python correcao_parser_b3_menor_jul2026.py --conc CPFL --salvar
    python correcao_parser_b3_menor_jul2026.py --conc EDP_SP --salvar
    python correcao_parser_b3_menor_jul2026.py --conc RGE --salvar
    python correcao_parser_b3_menor_jul2026.py --conc LIGHT --salvar
    python correcao_parser_b3_menor_jul2026.py --conc EQ_GO --salvar
    python correcao_parser_b3_menor_jul2026.py --conc CELESC  # simulação
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
SAIDA_DIR = Path("//10.10.250.21/Energia/ARQUIVOS ENZO/correcoes_parser_b3_menor_jul2026")

CAMPOS_CRITICOS = ("btnSalvar", "instalacao")

_RETENCOES = (
    "fatDescIrpjPercRetImposto", "fatDescIrpjValRetImposto",
    "fatDescPisPercRetImposto",  "fatDescPisValRetImposto",
    "fatDescCofinsPercRetImposto", "fatDescCofinsValRetImposto",
    "fatDescCsllPercRetImposto",   "fatDescCsllValRetImposto",
)


def _f(v) -> float:
    """Converte valor de célula Excel para float, retornando 0.0 para NaN/None/vazio."""
    try:
        r = float(v)
        return 0.0 if r != r else r  # nan != nan
    except (TypeError, ValueError):
        return 0.0


# ── Configuração por concessionária ──────────────────────────────────────────

def _cfg_celesc(row: dict) -> dict[str, str]:
    """CELESC: bandeira + bandeira2 (GD crédito, negativo) + alíquotas PIS/COFINS."""
    c = {}
    band = _f(row.get("fatValBandeira"))
    if band > 0:
        c["fatValBandeira"] = formatar_correcao("fatValBandeira", band)
    band2 = _f(row.get("fatValBandeira2"))
    # XLSX gerado com OCR que retornava b2 positivo; CONSEN espera negativo
    if band2 > 0:
        band2 = -band2
    if band2 != 0:
        c["fatValBandeira2"] = formatar_correcao("fatValBandeira2", band2)
    pis = _f(row.get("fatDescPisAliquota"))
    if pis > 0:
        c["fatDescPisAliquota"] = formatar_correcao("fatDescPisAliquota", pis)
    # OCR CELESC usa 'fatDescCofinsAliquota'; CONSEN usa 'fatDesCofinsAliquota'
    cofins = _f(row.get("fatDescCofinsAliquota"))
    if cofins > 0:
        c["fatDesCofinsAliquota"] = formatar_correcao("fatDesCofinsAliquota", cofins)
    return c


def _cfg_copel(row: dict) -> dict[str, str]:
    """COPEL: bandeira + bandeira2 (GD crédito, negativo)."""
    c = {}
    band = _f(row.get("fatValBandeira"))
    if band > 0:
        c["fatValBandeira"] = formatar_correcao("fatValBandeira", band)
    band2 = _f(row.get("fatValBandeira2"))
    if band2 != 0:
        c["fatValBandeira2"] = formatar_correcao("fatValBandeira2", band2)
    return c


def _cfg_cpfl(row: dict) -> dict[str, str]:
    """CPFL: bandeira bruta + bandeira2 (crédito GD, negativo) + obs (devolução → cod 109)."""
    c = {}
    band = _f(row.get("fatValBandeira"))
    if band > 0:
        c["fatValBandeira"] = formatar_correcao("fatValBandeira", band)
    band2 = _f(row.get("fatValBandeira2"))
    if band2 != 0:
        c["fatValBandeira2"] = formatar_correcao("fatValBandeira2", band2)
    for i in range(1, 6):
        cod = str(row.get(f"obsCod_{i}") or "").strip()
        val = _f(row.get(f"obsValor_{i}"))
        if not cod or cod in ("0", "nan", "None"):
            break
        c[f"obsCod_{i}"]   = formatar_correcao(f"obsCod_{i}",   int(float(cod)))
        c[f"obsValor_{i}"] = formatar_correcao(f"obsValor_{i}", val)
    return c


def _cfg_edp_sp(row: dict) -> dict[str, str]:
    """EDP SP: 4 pares de retenção federal."""
    c = {}
    for campo in _RETENCOES:
        v = _f(row.get(campo))
        if v != 0:
            c[campo] = formatar_correcao(campo, v)
    return c


def _cfg_rge(row: dict) -> dict[str, str]:
    """RGE: iluminação pública (id CONSEN = fatIluminacaoPublica)."""
    ilum = _f(row.get("fatIlumPublica"))
    if ilum > 0:
        return {"fatIluminacaoPublica": formatar_correcao("fatIlumPublica", ilum)}
    return {}


def _cfg_light(row: dict) -> dict[str, str]:
    """LIGHT: multas onde > 0."""
    multas = _f(row.get("fatMultasDiversas"))
    if multas > 0:
        return {"fatMultasDiversas": formatar_correcao("fatMultasDiversas", multas)}
    return {}


def _cfg_eq_go(row: dict) -> dict[str, str]:
    """EQ GO: multas onde > 0."""
    multas = _f(row.get("fatMultasDiversas"))
    if multas > 0:
        return {"fatMultasDiversas": formatar_correcao("fatMultasDiversas", multas)}
    return {}


CONCS: dict[str, tuple[str, Any]] = {
    "CELESC": (str(LOGS_DIR / "ocr_correcoes_menor_CELESC.xlsx"),  _cfg_celesc),
    "COPEL":  (str(LOGS_DIR / "ocr_correcoes_menor_COPEL.xlsx"),   _cfg_copel),
    "CPFL":   (str(LOGS_DIR / "ocr_correcoes_menor_CPFL.xlsx"),    _cfg_cpfl),
    "EDP_SP": (str(LOGS_DIR / "ocr_correcoes_menor_EDP_SP.xlsx"),  _cfg_edp_sp),
    "RGE":    (str(LOGS_DIR / "ocr_correcoes_menor_RGE.xlsx"),     _cfg_rge),
    "LIGHT":  (str(LOGS_DIR / "ocr_correcoes_menor_LIGHT.xlsx"),   _cfg_light),
    "EQ_GO":  (str(LOGS_DIR / "ocr_correcoes_menor_EQ_GO.xlsx"),   _cfg_eq_go),
}


def rodar(conc: str, salvar: bool, retomar_apos: str = "") -> None:
    xlsx_path, cfg_fn = CONCS[conc]
    df = pd.read_excel(xlsx_path)

    SAIDA_DIR.mkdir(parents=True, exist_ok=True)
    execucao_csv = SAIDA_DIR / f"correcao_menor_{conc.lower()}_execucao.csv"

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
    p = argparse.ArgumentParser(description="Correção campos parser B3 MENOR jul/2026")
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
