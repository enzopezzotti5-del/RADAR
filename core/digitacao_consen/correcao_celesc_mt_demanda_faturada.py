#!/usr/bin/env python3
"""
Correcao de Demanda Faturada CELESC MT — valores arredondados no CONSEN.

CELESC imprime demanda faturada arredondada no PDF (ex: 52 kW).
O correto e o valor registrado exato quando Reg > Contratada.
Parser ocr_celesc_mt.py corrigido em 01/07/2026 para derivar max(Reg, Cont).

6 carimbos afetados: CELESC MT 03/2026, digitados pelo Robo Digitador.

Uso:
    python core/digitacao_consen/correcao_celesc_mt_demanda_faturada.py
    python core/digitacao_consen/correcao_celesc_mt_demanda_faturada.py --salvar
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "core"))

try:
    from digitacao_consen.consen_api import ConsenSession
except ImportError:
    from consen_api import ConsenSession  # type: ignore

CSV_SAIDA = ROOT / "_resultado_correcao_celesc_mt_demanda.csv"

# Carimbo → (fatDemFPontaIndutivo, fatDemPontaFaturada)
# Ponta=None quando a fatura nao tem demanda ponta separada (A4/A3a)
CORRECOES: dict[str, dict[str, float]] = {
    "2005003": {"fatDemFPontaIndutivo": 51.77},
    "2005006": {"fatDemFPontaIndutivo": 55.46},
    "2005035": {"fatDemFPontaIndutivo": 71.20},
    "2005047": {"fatDemFPontaIndutivo": 32.06},
    "2005072": {"fatDemFPontaIndutivo": 63.24},
    "2005079": {"fatDemFPontaIndutivo": 184.46},
}


def _fmt(v: float) -> str:
    return f"{v:.2f}".replace(".", ",")


def corrigir(salvar: bool) -> None:
    print(f"Carimbos a corrigir: {len(CORRECOES)}")
    if not salvar:
        print("MODO DRY-RUN — use --salvar para efetivar\n")

    resultados = []

    with ConsenSession.abrir() as session:
        for carimbo, campos in CORRECOES.items():
            print(f"\n{'='*55}")
            print(f"BB_{carimbo}  [CELESC MT]")

            valores = {campo: _fmt(val) for campo, val in campos.items()}
            for c, v in valores.items():
                print(f"  {c}: {v}")

            status = "DRY_RUN"
            detalhe = ""
            try:
                if salvar:
                    session.buscar_carimbo(carimbo)
                    resultado = session.editar_campos(valores)
                    session.salvar()
                    alterados = [c.campo for c in resultado.alterados]
                    status = "OK"
                    detalhe = f"alterados: {','.join(alterados)}"
                    print(f"  -> SALVO: {detalhe}")
            except Exception as e:
                status = "ERRO"
                detalhe = str(e)
                print(f"  -> ERRO: {e}")

            resultados.append({
                "carimbo": f"BB_{carimbo}",
                "status": status,
                "detalhe": detalhe,
                "fatDemFPontaIndutivo": campos.get("fatDemFPontaIndutivo"),
            })

    with open(CSV_SAIDA, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["carimbo", "status", "detalhe", "fatDemFPontaIndutivo"])
        writer.writeheader()
        writer.writerows(resultados)

    ok  = sum(1 for r in resultados if r["status"] == "OK")
    err = sum(1 for r in resultados if r["status"] == "ERRO")
    dry = sum(1 for r in resultados if r["status"] == "DRY_RUN")
    print(f"\n{'='*55}")
    print(f"OK={ok}  ERRO={err}  DRY_RUN={dry}")
    print(f"CSV: {CSV_SAIDA}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--salvar", action="store_true", help="Efetivar correcoes no CONSEN")
    args = parser.parse_args()
    corrigir(salvar=args.salvar)


if __name__ == "__main__":
    main()
