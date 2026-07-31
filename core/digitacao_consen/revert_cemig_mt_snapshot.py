#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Reverte carimbos CEMIG MT ao estado anterior do snapshot.

Lê os arquivos _campos.json salvos antes da correcao errada e restaura
os valores originais no CONSEN via Selenium.

Uso:
    python revert_cemig_mt_snapshot.py --simular
    python revert_cemig_mt_snapshot.py --salvar
"""

from __future__ import annotations

import argparse
import json
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

SNAPSHOT_DIR = Path("//10.10.250.21/Energia/ARQUIVOS ENZO/CEMIG_pipeline_saida/MT/correcoes_por_carimbo")

CARIMBOS = ["2002673", "2007576", "2007577", "2007796"]

# Campos HTML que o script correcao_cemig_mt_carimbo.py pode ter modificado.
# Usamos os IDs exatos como aparecem no snapshot (_campos.json).
CAMPOS_A_REVERTER = [
    "fatDemPontaRegistrada",
    "fatDemPonta",                   # = fatDemPontaFaturada na tela
    "fatDemPontaValorReais",
    "fatDemFPontaIndRegistrada",
    "fatDemFPontaIndutivo",          # = fatDemFPontaIndFaturada na tela
    "fatDemFPontaIndValorReais",
    "fatDemFPontaIndUltra",
    "fatDemFPontaIndUltraValorReais",
    "fatConPontaRegistrado",
    "fatConPonta",                   # = fatConPontaFaturado na tela
    "fatConPontaValorReais",
    "fatConFPontaIndRegistrado",
    "fatConFPontaIndutivo",          # = fatConFPontaIndFaturado na tela
    "fatConFPontaIndValorReais",
    "fatICMS",
    "fatPIS",
    "fatCofins",
    "fatValorNFiscal",
    "fatDescontoFio",
    "fatDescontoFioKWh",
    "fatDescPisPercRetImposto",
    "fatDescPisValRetImposto",
    "fatDescCofinsPercRetImposto",
    "fatDescCofinsValRetImposto",
    "fatDescCsllPercRetImposto",
    "fatDescCsllValRetImposto",
    "fatDescIrpjPercRetImposto",
    "fatDescIrpjValRetImposto",
]

CAMPOS_CRITICOS = ("btnSalvar", "fatDemFPontaIndRegistrada")

LOGIN_URL = fluxo_base.LOGIN_URL
BASE_URL = LOGIN_URL.rsplit("login.php", 1)[0]
EDIT_URL = os.environ.get(
    "CONSEN_EDITA_FATURA_CARIMBO_URL",
    f"{BASE_URL}index.php#bpg/gestao/fatura/editaFaturaCarimbo.php",
)
SAIDA_DIR = SNAPSHOT_DIR
EXECUCAO_CSV = SAIDA_DIR / "revert_execucao.csv"
FECHAR_AO_FINAL = os.environ.get("CONSEN_CORRECAO_FECHAR", "1").strip().lower() not in {"0", "false", "nao", "não"}


def carregar_snapshot(carimbo: str) -> dict[str, str]:
    json_path = SNAPSHOT_DIR / f"BB_{carimbo}_campos.json"
    if not json_path.exists():
        raise FileNotFoundError(f"Snapshot nao encontrado: {json_path}")
    entradas = json.loads(json_path.read_text(encoding="utf-8"))
    por_id = {e["id"]: e["value"] for e in entradas if e.get("id") and e.get("tag") in ("input", "select", "textarea")}
    return por_id


def montar_payload_revert(snapshot: dict[str, str]) -> dict[str, str]:
    payload: dict[str, str] = {}
    for campo in CAMPOS_A_REVERTER:
        if campo in snapshot:
            payload[campo] = snapshot[campo]
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Reverte CEMIG MT ao snapshot original")
    parser.add_argument("--salvar", action="store_true", help="Salva no CONSEN (sem essa flag apenas simula)")
    parser.add_argument("--carimbo", action="append", default=[], help="Processar so este carimbo")
    args = parser.parse_args()

    carimbos = [fluxo_base.normalizar_carimbo(c) for c in args.carimbo] if args.carimbo else CARIMBOS

    # Validar snapshots antes de abrir o browser
    payloads: dict[str, dict[str, str]] = {}
    for carimbo in carimbos:
        try:
            snap = carregar_snapshot(carimbo)
            payload = montar_payload_revert(snap)
            payloads[carimbo] = payload
            log(f"BB_{carimbo}: {len(payload)} campos a reverter")
            for k, v in sorted(payload.items()):
                log(f"  {k} <- {v!r}")
        except FileNotFoundError as e:
            warn(str(e))
            return 1

    if not args.salvar:
        log("Modo simulacao: use --salvar para efetivar.")
        return 0

    driver = None
    try:
        driver, wait = fluxo_base.abrir_driver_logado()
        time.sleep(3.0)

        for carimbo in carimbos:
            payload = payloads[carimbo]
            fluxo_base.registrar_execucao(EXECUCAO_CSV, carimbo, "iniciado")
            fluxo_base.abrir_tela_edicao_carimbo(driver, wait, EDIT_URL)
            time.sleep(1.5)
            fluxo_base.carregar_fatura_por_carimbo(driver, wait, carimbo, CAMPOS_CRITICOS)
            time.sleep(1.0)

            aplicadas, confirmadas, total = fluxo_base.aplicar_correcoes(driver, wait, carimbo, payload)

            if total <= 0:
                warn(f"BB_{carimbo}: sem campos para reverter.")
                fluxo_base.registrar_execucao(EXECUCAO_CSV, carimbo, "sem_campos")
                continue

            if confirmadas < total:
                warn(f"BB_{carimbo}: incompleto ({confirmadas}/{total}). Salvamento bloqueado.")
                fluxo_base.registrar_execucao(EXECUCAO_CSV, carimbo, "bloqueado_incompleto", f"{confirmadas}/{total}")
                continue

            time.sleep(1.0)
            fluxo_base.salvar_auditar_e_avancar(driver, wait, carimbo)
            fluxo_base.registrar_execucao(EXECUCAO_CSV, carimbo, "ok", f"{confirmadas}/{total}")
            log(f"BB_{carimbo}: revertido OK ({confirmadas}/{total})")
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
