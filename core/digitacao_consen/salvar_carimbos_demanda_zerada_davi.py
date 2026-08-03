#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Abre cada carimbo da lista do Davi (demanda zerada) no CONSEN e salva.
Não altera nenhum campo — apenas busca e salva.

Uso:
    python salvar_carimbos_demanda_zerada_davi.py --salvar
    python salvar_carimbos_demanda_zerada_davi.py --salvar --retomar-apos 994560
    python salvar_carimbos_demanda_zerada_davi.py          # simula (não salva)
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import _venv_check  # noqa: F401

try:
    from digitacao_consen.consen_api import ConsenSession
    from digitacao_consen.correcao_fluxo_base import log, warn
except ModuleNotFoundError:
    from consen_api import ConsenSession  # type: ignore
    from correcao_fluxo_base import log, warn  # type: ignore

CAMPOS_CRITICOS: tuple[str, ...] = ("btnSalvar", "fatDemFPontaIndRegistrada")

CARIMBOS_DAVI: list[str] = [
    # 01/2026
    "990111", "990717", "990718", "990719", "990720", "990721",
    "990722", "990723", "990724", "990725", "990726", "990727",
    "990730", "990731", "990732", "991187",
    # 02/2026
    "992815", "994196", "994197", "994222", "994223", "994557",
    "994558", "994560", "994562", "994563", "994564", "994566",
    "994567", "994568", "994569", "994570", "994571",
    # 03/2026
    "996722", "996736", "996739", "996740", "996761", "996762",
    "996799", "996877", "996882", "997076", "997077", "997078",
    "997304", "997690", "997692", "997693", "997714", "997715",
    "997716", "997740", "997957", "998422",
    # 04/2026
    "999245", "999246", "999247", "999248", "999249", "999250",
    "999433", "999434", "999435", "999436", "999437", "999492",
]

SAIDA_CSV = Path(__file__).resolve().parents[2] / "logs" / "salvar_demanda_zerada_davi.csv"


def _registrar(csv_path: Path, carimbo: str, status: str, detalhe: str = "") -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    escrever_header = not csv_path.exists()
    with csv_path.open("a", newline="", encoding="utf-8-sig") as f:
        if escrever_header:
            f.write("carimbo,status,detalhe\n")
        f.write(f"{carimbo},{status},{detalhe}\n")


def _carimbos_ja_ok(csv_path: Path) -> set[str]:
    if not csv_path.exists():
        return set()
    ok: set[str] = set()
    with csv_path.open(encoding="utf-8-sig") as f:
        for linha in f:
            partes = linha.strip().split(",", 2)
            if len(partes) >= 2 and partes[1] in {"OK", "PULADO"}:
                ok.add(partes[0].strip())
    return ok


def main() -> int:
    parser = argparse.ArgumentParser(description="Salva carimbos Davi no CONSEN (demanda zerada)")
    parser.add_argument("--salvar",        action="store_true", help="Executa o save (sem isso só simula)")
    parser.add_argument("--retomar-apos",  metavar="CARIMBO",  help="Pula até após este carimbo")
    parser.add_argument("--reprocessar-ok", action="store_true", help="Reprocessa carimbos já marcados OK")
    args = parser.parse_args()

    carimbos = list(CARIMBOS_DAVI)
    total = len(carimbos)

    if args.retomar_apos:
        alvo = str(args.retomar_apos).strip().replace("BB_", "")
        try:
            idx = carimbos.index(alvo)
            carimbos = carimbos[idx + 1:]
            log(f"Retomando a partir do carimbo seguinte a {alvo} ({len(carimbos)} restantes)")
        except ValueError:
            warn(f"Carimbo {alvo} não encontrado na lista — ignorando --retomar-apos")

    ja_ok = set() if args.reprocessar_ok else _carimbos_ja_ok(SAIDA_CSV)

    pendentes = [c for c in carimbos if c not in ja_ok]
    pulados   = total - len(pendentes) - (len(carimbos) - len(pendentes))

    log(f"Total na lista : {total}")
    log(f"Já OK (CSV)    : {len(ja_ok)}")
    log(f"A processar    : {len(pendentes)}")

    if not args.salvar:
        log("\n[SIMULAÇÃO] Passe --salvar para executar.")
        for c in pendentes:
            log(f"  -> {c}")
        return 0

    ok_count = erro_count = 0
    with ConsenSession.abrir() as s:
        for i, carimbo in enumerate(pendentes, 1):
            log(f"\n[{i}/{len(pendentes)}] Carimbo {carimbo}")
            try:
                s.buscar_carimbo(carimbo, CAMPOS_CRITICOS)
                s.salvar_e_auditar()
                log("  OK")
                _registrar(SAIDA_CSV, carimbo, "OK")
                ok_count += 1
            except Exception as exc:
                msg = str(exc)[:120]
                warn(f"  ERRO: {msg}")
                _registrar(SAIDA_CSV, carimbo, "ERRO", msg.replace(",", ";"))
                erro_count += 1
                time.sleep(2)

    log(f"\n{'='*60}")
    log(f"  OK: {ok_count}  |  ERRO: {erro_count}")
    log(f"  Log: {SAIDA_CSV}")
    log(f"{'='*60}")
    return 0 if erro_count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
