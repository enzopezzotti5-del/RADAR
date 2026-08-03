#!/usr/bin/env python3
"""
Correcao de Tarifa Branca — Consumo Intermediario zerado no CONSEN.

Carimbos afetados: Neoenergia (CELPE/COSERN/COELBA) e Energisa Rondonia,
digitados pelo Robo Digitador com fatConIntermediarioRegistrado = 0 por bug
dos parsers ocr_neoenergia.py e ocr_energisa_bt.py (corrigidos em 01/07/2026).

Para Neoenergia (CELPE): o parser antigo colocava todo consumo em fora_ponta.
Corrige ponta + intermediario + fora_ponta simultaneamente.
Para Energisa: corrige apenas intermediario (unico campo zerado).

Uso:
    python core/digitacao_consen/correcao_tarifa_branca_interm_carimbo.py
    python core/digitacao_consen/correcao_tarifa_branca_interm_carimbo.py --salvar
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "core"))

try:
    from digitacao_consen.consen_api import ConsenSession
except ImportError:
    from consen_api import ConsenSession  # type: ignore

DATA_JSON = ROOT / "_tmp_branca_interm_correcao.json"
CSV_SAIDA = ROOT / "_resultado_correcao_branca_interm.csv"

# Campos a corrigir por parser
CAMPOS_NEO = [
    "fatConPontaRegistrado", "fatConPontaFaturado", "fatConPontaValorReais",
    "fatConIntermediarioRegistrado", "fatConIntermediarioFaturado", "fatConIntermediarioValorReais",
    "fatConFPontaIndRegistrado", "fatConFPontaIndValorReais",
    # fatConFPontaIndFaturado omitido: campo nao existe no CONSEN HTML (F.Ponta Faturado ja estava correto)
]
CAMPOS_ENERG = [
    "fatConIntermediarioRegistrado", "fatConIntermediarioFaturado", "fatConIntermediarioValorReais",
]

# Campos Faturado: o CONSEN usa id sem sufixo (fatConPonta, fatConIntermediario).
# fatConFPontaInd nao existe no HTML — F.Ponta Faturado ja estava correto no CONSEN.
FATURADO_HTML_ID = {
    "fatConPontaFaturado":         "fatConPonta",
    "fatConIntermediarioFaturado": "fatConIntermediario",
}


def _fmt(v: float) -> str:
    return f"{v:.2f}".replace(".", ",")


def carregar_registros() -> list[dict]:
    if not DATA_JSON.exists():
        raise FileNotFoundError(
            f"JSON de correcao nao encontrado: {DATA_JSON}\n"
            "Execute primeiro o script de extracao dos valores do parser."
        )
    with open(DATA_JSON, encoding="utf-8") as f:
        todos = json.load(f)
    # Filtrar apenas os que tem intermediario > 0
    return [r for r in todos if r.get("fatConIntermediarioRegistrado", 0) > 0]


def corrigir(salvar: bool) -> None:
    registros = carregar_registros()
    print(f"Carimbos a corrigir: {len(registros)}")
    if not salvar:
        print("MODO DRY-RUN — use --salvar para efetivar\n")

    resultados = []

    with ConsenSession.abrir() as session:
        for reg in registros:
            carimbo = reg["carimbo"]
            parser  = reg["parser"]
            campos  = CAMPOS_NEO if parser == "NEOENERGIA" else CAMPOS_ENERG

            print(f"\n{'='*55}")
            print(f"{carimbo}  [{parser}]")

            # Monta dict de campos: usa HTML ID para Faturado (localizador por name nao funciona)
            valores = {}
            for c in campos:
                if reg.get(c, 0) == 0:
                    continue
                chave = FATURADO_HTML_ID.get(c, c)
                valores[chave] = _fmt(reg[c])

            if not valores:
                print("  Nenhum campo a corrigir — pulando")
                resultados.append({"carimbo": carimbo, "status": "PULADO", "detalhe": "sem valores"})
                continue

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
                "carimbo": carimbo,
                "parser": parser,
                "status": status,
                "detalhe": detalhe,
                "fatConIntermediarioRegistrado": reg.get("fatConIntermediarioRegistrado"),
            })

    # Salvar CSV de resultado
    with open(CSV_SAIDA, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["carimbo","parser","status","detalhe","fatConIntermediarioRegistrado"])
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
