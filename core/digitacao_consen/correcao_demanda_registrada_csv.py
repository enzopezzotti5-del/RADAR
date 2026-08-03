#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Correcao de demanda registrada por carimbo — script universal.

Le um CSV com colunas: carimbo, fp_kw, pta_kw
Aplica SOMENTE fatDemFPontaIndRegistrada (fp_kw) e fatDemPontaRegistrada (pta_kw).
Nao toca nenhum outro campo.

Uso:
    python correcao_demanda_registrada_csv.py --csv demanda.csv --simular
    python correcao_demanda_registrada_csv.py --csv demanda.csv --salvar
    python correcao_demanda_registrada_csv.py --carimbo BB_2007867 --fp-kw 49 --pta-kw 9 --salvar
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import _venv_check  # noqa: E402,F401

try:
    from digitacao_consen import correcao_fluxo_base as fluxo_base
    from digitacao_consen.correcao_fluxo_base import log, warn
except ModuleNotFoundError:
    import correcao_fluxo_base as fluxo_base  # type: ignore
    from correcao_fluxo_base import log, warn  # type: ignore

LOGIN_URL = fluxo_base.LOGIN_URL
BASE_URL = LOGIN_URL.rsplit("login.php", 1)[0]
EDIT_URL = os.environ.get(
    "CONSEN_EDITA_FATURA_CARIMBO_URL",
    f"{BASE_URL}index.php#bpg/gestao/fatura/editaFaturaCarimbo.php",
)

DEFAULT_SAIDA_DIR = Path("//10.10.250.21/Energia/ARQUIVOS ENZO/correcoes_demanda_registrada")
SAIDA_DIR = Path(os.environ.get("CONSEN_CORRECAO_SAIDA", str(DEFAULT_SAIDA_DIR)))
EXECUCAO_CSV = SAIDA_DIR / "correcao_execucao.csv"
FECHAR_AO_FINAL = os.environ.get("CONSEN_CORRECAO_FECHAR", "1").strip().lower() not in {"0", "false", "nao", "não"}
CAMPOS_CRITICOS = ("btnSalvar", "fatDemFPontaIndRegistrada")


def _fmt(valor: float) -> str:
    """Formata float para string BR com 2 casas decimais."""
    return f"{valor:.2f}".replace(".", ",")


def montar_payload(fp_kw: float, pta_kw: float) -> dict[str, str]:
    payload: dict[str, str] = {}
    payload["fatDemFPontaIndRegistrada"] = _fmt(fp_kw)
    if pta_kw and pta_kw != 0.0:
        payload["fatDemPontaRegistrada"] = _fmt(pta_kw)
    return payload


def carregar_csv(path: Path) -> dict[str, dict[str, str]]:
    """Lê CSV com colunas carimbo, fp_kw, pta_kw. Retorna {carimbo_norm: payload}."""
    result: dict[str, dict[str, str]] = {}
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f, delimiter=";")
        for row in reader:
            carimbo_raw = str(row.get("carimbo") or "").strip()
            if not carimbo_raw:
                continue
            carimbo = fluxo_base.normalizar_carimbo(carimbo_raw)
            fp_kw = float(str(row.get("fp_kw") or "0").replace(",", "."))
            pta_kw = float(str(row.get("pta_kw") or "0").replace(",", "."))
            result[carimbo] = montar_payload(fp_kw, pta_kw)
    return result


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Correcao de demanda registrada por carimbo")
    p.add_argument("--csv", type=str, default="", help="CSV com colunas: carimbo;fp_kw;pta_kw")
    p.add_argument("--carimbo", type=str, default="", help="Carimbo avulso (ex: BB_2007867)")
    p.add_argument("--fp-kw", type=float, default=0.0, help="kW FP registrado")
    p.add_argument("--pta-kw", type=float, default=0.0, help="kW Ponta registrado (0 se nao houver)")
    p.add_argument("--salvar", action="store_true", help="Salva no CONSEN (sem essa flag apenas simula)")
    p.add_argument("--reprocessar-ok", action="store_true", help="Reprocessa mesmo ja com status ok")
    return p.parse_args()


def main() -> int:
    args = parse_args()

    payloads: dict[str, dict[str, str]] = {}

    if args.csv:
        payloads = carregar_csv(Path(args.csv))
    elif args.carimbo:
        carimbo = fluxo_base.normalizar_carimbo(args.carimbo)
        payloads[carimbo] = montar_payload(args.fp_kw, args.pta_kw)
    else:
        print("Informe --csv ou --carimbo.")
        return 2

    if not payloads:
        print("Nenhum carimbo encontrado.")
        return 0

    if not args.reprocessar_ok:
        status_ex = fluxo_base.carregar_status_execucao(EXECUCAO_CSV)
        payloads = {c: p for c, p in payloads.items() if status_ex.get(c) != "ok"}

    log(f"Carimbos a corrigir: {len(payloads)}")
    for carimbo, payload in sorted(payloads.items()):
        log(f"  BB_{carimbo}: {payload}")

    if not args.salvar:
        log("Modo simulacao: use --salvar para efetivar.")
        return 0

    driver = None
    try:
        driver, wait = fluxo_base.abrir_driver_logado()
        time.sleep(3.0)

        for carimbo in sorted(payloads):
            payload = payloads[carimbo]
            fluxo_base.registrar_execucao(EXECUCAO_CSV, carimbo, "iniciado")
            fluxo_base.abrir_tela_edicao_carimbo(driver, wait, EDIT_URL)
            time.sleep(1.5)
            fluxo_base.carregar_fatura_por_carimbo(driver, wait, carimbo, CAMPOS_CRITICOS)
            time.sleep(1.0)

            fluxo_base.salvar_snapshot(driver, SAIDA_DIR, carimbo)

            aplicadas, confirmadas, total = fluxo_base.aplicar_correcoes(driver, wait, carimbo, payload)

            if confirmadas < total:
                warn(f"BB_{carimbo}: incompleto ({confirmadas}/{total}). Bloqueado.")
                fluxo_base.registrar_execucao(EXECUCAO_CSV, carimbo, "bloqueado", f"{confirmadas}/{total}")
                continue

            time.sleep(1.0)
            fluxo_base.salvar_auditar_e_avancar(driver, wait, carimbo)
            fluxo_base.registrar_execucao(EXECUCAO_CSV, carimbo, "ok", f"{confirmadas}/{total}")
            log(f"BB_{carimbo}: corrigido OK ({confirmadas}/{total})")
            time.sleep(1.0)

        return 0
    finally:
        if driver and FECHAR_AO_FINAL:
            try:
                driver.quit()
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
