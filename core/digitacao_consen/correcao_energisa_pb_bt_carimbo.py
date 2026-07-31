#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Correcao Energisa PB BT por carimbo.

Zera fatMultasDiversas (valor de multas/mora digitado incorretamente)
e aplica demais correcoes campos a campos conforme necessario.

Uso:
    python correcao_energisa_pb_bt_carimbo.py --carimbo BB_2013192 [--salvar]
    python correcao_energisa_pb_bt_carimbo.py --carimbo BB_2013192 --carimbo BB_2013193 --salvar
    python correcao_energisa_pb_bt_carimbo.py --carimbos-arquivo lista.txt --salvar
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    from digitacao_consen.consen_api import (
        ConsenSession,
        carregar_lista_carimbos_args,
    )
    from digitacao_consen.correcao_fluxo_base import log, warn
except ModuleNotFoundError:
    from consen_api import ConsenSession, carregar_lista_carimbos_args  # type: ignore
    from correcao_fluxo_base import log, warn  # type: ignore

# ── Configuracao ──────────────────────────────────────────────────────────────

SAIDA_DIR = Path(
    "//10.10.250.21/Energia/ARQUIVOS ENZO"
    "/ALTERACOES APOS AUDITORIA/correcoes_energisa_pb_bt"
)
EXECUCAO_CSV = SAIDA_DIR / "correcao_execucao.csv"

CAMPOS_CRITICOS: tuple[str, ...] = ("fatMultasDiversas",)

ORDEM_CAMPOS: tuple[str, ...] = ("fatMultasDiversas",)


def _build_correcoes() -> dict[str, str]:
    return {
        "fatMultasDiversas": "0",
    }


# ── Fluxo ─────────────────────────────────────────────────────────────────────

def rodar(carimbos: list[str], salvar: bool) -> dict[str, str]:
    SAIDA_DIR.mkdir(parents=True, exist_ok=True)

    if not salvar:
        log("MODO SIMULACAO: use --salvar para efetivar as correcoes.")

    resultados: dict[str, str] = {}

    with ConsenSession.abrir() as s:
        for carimbo in carimbos:
            log(f"=== BB_{carimbo} ===")
            correcoes = _build_correcoes()

            try:
                s.buscar_carimbo(carimbo, CAMPOS_CRITICOS)
                s.snapshot(SAIDA_DIR, carimbo)
                resultado = s.editar_campos(correcoes, ORDEM_CAMPOS)

                if not salvar:
                    log(f"  {resultado.resumo()} [simulado]")
                    s.registrar(EXECUCAO_CSV, carimbo, "simulado",
                                f"{resultado.n_alterados}/{len(correcoes)} campos")
                    resultados[carimbo] = "simulado"
                    continue

                s.salvar_e_auditar()
                log(f"  {resultado.resumo()} [SALVO]")
                s.registrar(EXECUCAO_CSV, carimbo, "corrigido",
                             f"{resultado.n_alterados}/{len(correcoes)} campos")
                resultados[carimbo] = "corrigido"

            except Exception as exc:
                warn(f"  BB_{carimbo}: {type(exc).__name__}: {exc}")
                s.registrar(EXECUCAO_CSV, carimbo, "erro", str(exc)[:200])
                resultados[carimbo] = "erro"

    return resultados


# ── CLI ───────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Correcao Energisa PB BT por carimbo no CONSEN")
    p.add_argument("--carimbo", action="append", default=[],
                   help="BB_XXXXXXX ou so o numero. Repetivel.")
    p.add_argument("--carimbos-arquivo", type=str, default="",
                   help="TXT com um carimbo por linha")
    p.add_argument("--salvar", action="store_true",
                   help="Efetivar a correcao (sem esta flag apenas simula)")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    carimbos = carregar_lista_carimbos_args(args)

    if not carimbos:
        print("Informe ao menos um carimbo com --carimbo BB_XXXXXXX ou --carimbos-arquivo arquivo.txt")
        return 1

    resultados = rodar(carimbos, salvar=args.salvar)

    log("=== RESUMO ===")
    for c, r in resultados.items():
        log(f"  BB_{c}: {r}")

    return 1 if any(r == "erro" for r in resultados.values()) else 0


if __name__ == "__main__":
    raise SystemExit(main())
