#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Correcao de demanda zerada — le os tres xlsx de Erros de Digitacao e corrige
fatDemFPontaIndRegistrada, fatDemContratadaFPonta e fatDemFPontaIndFaturada
em uma unica sessao CONSEN por carimbo.

Uso:
    python correcao_demanda_zerada_xlsx.py --simular
    python correcao_demanda_zerada_xlsx.py --salvar
    python correcao_demanda_zerada_xlsx.py --salvar --retomar-apos 2012580
    python correcao_demanda_zerada_xlsx.py --salvar --reprocessar-ok
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import _venv_check  # noqa: F401

try:
    from digitacao_consen import correcao_fluxo_base as fluxo_base
    from digitacao_consen.correcao_fluxo_base import log, warn
except ModuleNotFoundError:
    import correcao_fluxo_base as fluxo_base  # type: ignore
    from correcao_fluxo_base import log, warn  # type: ignore

LOGIN_URL = fluxo_base.LOGIN_URL
BASE_URL  = LOGIN_URL.rsplit("login.php", 1)[0]
EDIT_URL  = os.environ.get(
    "CONSEN_EDITA_FATURA_CARIMBO_URL",
    f"{BASE_URL}index.php#bpg/gestao/fatura/editaFaturaCarimbo.php",
)

ERROS_DIR    = Path(r"\\10.10.250.21\Energia\ARQUIVOS ENZO\Erros de Digitacao")
SAIDA_DIR    = Path(r"\\10.10.250.21\Energia\ARQUIVOS ENZO\correcoes_demanda_zerada")
EXECUCAO_CSV = SAIDA_DIR / "correcao_execucao.csv"
FECHAR       = os.environ.get("CONSEN_CORRECAO_FECHAR", "1").strip() not in {"0", "false"}

CAMPOS_CRITICOS = ("btnSalvar", "fatDemFPontaIndRegistrada")


def _fmt(v) -> str:
    try:
        return f"{float(str(v).replace(',', '.')):.2f}".replace(".", ",")
    except (ValueError, TypeError):
        return "0,00"


def _norm_carimbo(v: str) -> str:
    return str(v).strip().replace("BB_", "").replace("bb_", "")


def carregar_payloads(apenas_robo: bool = True) -> dict[str, dict[str, str]]:
    """Lê os três xlsx e consolida {carimbo: {campo_consen: valor_fmt}}.

    apenas_robo=True filtra somente linhas com Digitador='Robo Digitador'.
    """
    payloads: dict[str, dict[str, str]] = {}
    digitadores: dict[str, str] = {}

    def _aceitar(row) -> bool:
        if not apenas_robo:
            return True
        return "robo" in str(row.get("Digitador", "")).lower()

    # --- Demanda Registrada ---
    arq_reg = ERROS_DIR / "Demana Registrada Zerada(2).xlsx"
    if arq_reg.exists():
        df = pd.read_excel(arq_reg, dtype=str)
        col_valor = df.columns[-1]
        aceitos = 0
        for _, row in df.iterrows():
            if not _aceitar(row):
                continue
            car = _norm_carimbo(row["Carimbo"])
            payloads.setdefault(car, {})["fatDemFPontaIndRegistrada"] = _fmt(row[col_valor])
            digitadores[car] = str(row.get("Digitador", ""))
            aceitos += 1
        log(f"[REG]  {aceitos}/{len(df)} linhas carregadas de {arq_reg.name}")
    else:
        warn(f"Nao encontrado: {arq_reg}")

    # --- Demanda Contratada ---
    arq_cont = ERROS_DIR / "Demanda Contratada Zerada.xlsx"
    if arq_cont.exists():
        df = pd.read_excel(arq_cont, dtype=str)
        col_valor = df.columns[-1]
        aceitos = 0
        for _, row in df.iterrows():
            if not _aceitar(row):
                continue
            car = _norm_carimbo(row["Carimbo"])
            payloads.setdefault(car, {})["fatDemContratadaFPonta"] = _fmt(row[col_valor])
            digitadores.setdefault(car, str(row.get("Digitador", "")))
            aceitos += 1
        log(f"[CONT] {aceitos}/{len(df)} linhas carregadas de {arq_cont.name}")
    else:
        warn(f"Nao encontrado: {arq_cont}")

    # --- Demanda Faturada ---
    arq_fat = ERROS_DIR / "Demanda Faturada Zerada.xlsx"
    if arq_fat.exists():
        df = pd.read_excel(arq_fat, dtype=str)
        col_valor = df.columns[-1]
        aceitos = 0
        for _, row in df.iterrows():
            if not _aceitar(row):
                continue
            car = _norm_carimbo(row["Carimbo"])
            payloads.setdefault(car, {})["fatDemFPontaIndFaturada"] = _fmt(row[col_valor])
            digitadores.setdefault(car, str(row.get("Digitador", "")))
            aceitos += 1
        log(f"[FAT]  {aceitos}/{len(df)} linhas carregadas de {arq_fat.name}")
    else:
        warn(f"Nao encontrado: {arq_fat}")

    return payloads


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Correcao demanda zerada — Registrada + Contratada + Faturada")
    p.add_argument("--salvar",         action="store_true", help="Efetiva no CONSEN (padrao: simula)")
    p.add_argument("--retomar-apos",   type=str, default="", help="Pula ate e inclusive este carimbo")
    p.add_argument("--reprocessar-ok", action="store_true", help="Reprocessa mesmo os ja marcados ok")
    p.add_argument("--todos",          action="store_true", help="Inclui Davi alem do Robo (padrao: so Robo)")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    SAIDA_DIR.mkdir(parents=True, exist_ok=True)

    payloads = carregar_payloads(apenas_robo=not args.todos)
    if not payloads:
        warn("Nenhum dado carregado.")
        return 1

    if not args.reprocessar_ok:
        status_ok = fluxo_base.carregar_status_execucao(EXECUCAO_CSV)
        payloads = {c: p for c, p in payloads.items() if status_ok.get(c) != "ok"}

    carimbos = sorted(payloads)

    if args.retomar_apos:
        marcador = _norm_carimbo(args.retomar_apos)
        idx = next((i for i, c in enumerate(carimbos) if c == marcador), None)
        if idx is not None:
            carimbos = carimbos[idx + 1:]

    if not carimbos:
        log("Nenhum carimbo pendente.")
        return 0

    # Resumo por tipo
    n_reg  = sum(1 for c in carimbos if "fatDemFPontaIndRegistrada" in payloads[c])
    n_cont = sum(1 for c in carimbos if "fatDemContratadaFPonta"    in payloads[c])
    n_fat  = sum(1 for c in carimbos if "fatDemFPontaIndFaturada"   in payloads[c])
    log(f"Carimbos a corrigir: {len(carimbos)}  "
        f"(Registrada={n_reg}  Contratada={n_cont}  Faturada={n_fat})")

    if not args.salvar:
        log("MODO SIMULACAO — use --salvar para efetivar.")
        for car in carimbos[:10]:
            log(f"  BB_{car}: {payloads[car]}")
        if len(carimbos) > 10:
            log(f"  ... +{len(carimbos)-10} carimbos")
        return 0

    driver = None
    try:
        driver, wait = fluxo_base.abrir_driver_logado()

        for carimbo in carimbos:
            payload = payloads[carimbo]
            log(f"--- BB_{carimbo}  {payload} ---")
            fluxo_base.registrar_execucao(EXECUCAO_CSV, carimbo, "iniciado")

            try:
                fluxo_base.abrir_tela_edicao_carimbo(driver, wait, EDIT_URL)
                fluxo_base.carregar_fatura_por_carimbo(driver, wait, carimbo, CAMPOS_CRITICOS)
            except Exception as e:
                warn(f"BB_{carimbo}: falha ao abrir — {e}")
                fluxo_base.registrar_execucao(EXECUCAO_CSV, carimbo, "erro_navegacao", str(e))
                continue

            time.sleep(0.4)
            aplicadas, confirmadas, total = fluxo_base.aplicar_correcoes(
                driver, wait, carimbo, payload
            )

            if confirmadas < total:
                warn(f"BB_{carimbo}: incompleto ({confirmadas}/{total})")
                fluxo_base.registrar_execucao(EXECUCAO_CSV, carimbo, "incompleto",
                                              f"{confirmadas}/{total}")
                continue

            time.sleep(0.8)
            fluxo_base.salvar_auditar_e_avancar(driver, wait, carimbo)
            fluxo_base.registrar_execucao(EXECUCAO_CSV, carimbo, "ok", f"{confirmadas}/{total}")
            log(f"BB_{carimbo}: salvo ({confirmadas}/{total})")
            time.sleep(0.5)

    except KeyboardInterrupt:
        log("Interrompido.")
    finally:
        if driver and FECHAR:
            try:
                driver.quit()
            except Exception:
                pass

    log("Concluido.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
