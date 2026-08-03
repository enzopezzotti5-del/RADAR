#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Abre cada carimbo da lista do Rodrigo (demanda zerada) no CONSEN e salva.
Não altera nenhum campo — apenas busca e salva.

Uso:
    python salvar_carimbos_demanda_zerada_rodrigo.py --salvar
    python salvar_carimbos_demanda_zerada_rodrigo.py --salvar --retomar-apos 993064
    python salvar_carimbos_demanda_zerada_rodrigo.py          # simula (não salva)
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

CARIMBOS_RODRIGO: list[str] = [
    # 01/2026
    "988491", "990112", "990929", "992009", "992199",
    # 02/2026
    "992843", "992846", "992847", "992848", "992849",
    "993019", "993062", "993063", "993064", "993067",
    "993068", "993069", "993718", "993768", "993769",
    "993770", "993780", "993781", "993966", "994213",
    "994214", "994215", "994217", "994218", "994220",
    "994246", "994247", "994248", "994249", "994250",
    "994251", "994252", "994421", "994573", "994578",
    "994581", "994582", "994583", "994584", "994585",
    "994586", "994595", "995239",
    # 03/2026
    "997063", "997070", "997072", "997104", "997115",
    "997117", "997118", "997119", "997120", "997121",
    "997122", "997123", "997124", "997125", "9972949",
    "9972951", "9972952", "997694", "997695", "997696",
    "997697", "997698", "997699", "997700", "997701",
    "997702", "997703", "997709", "997710", "997711",
    "997712", "997713", "997732", "997733", "998081",
    # 04/2026
    "980176", "980178", "980179", "980195", "980196",
    "980198", "980255", "980262", "980263", "980264",
    "980265", "980266", "980284", "980286", "980299",
    "980301", "980376", "980391", "980476", "980770",
    "980772", "980773", "980774", "980775", "980776",
    "980777", "980778", "980779", "980968", "981516",
    "981518", "981527", "987906", "987936", "988952",
    "999127", "999268", "999454", "999455", "999456",
    "999457", "999458", "999459", "999717", "999718",
    "999993", "999996", "999997", "999998",
    # 05/2026
    "970937", "971092", "971126", "971154", "971155",
    "971156", "971157", "971158", "971159", "971279",
    "971472", "971473", "971474", "971475", "971476",
    "971500", "971501", "971550", "971769", "971770",
    "971771", "971780", "971782", "971784", "971785",
    "971786", "971787", "971788", "971789",
]

SAIDA_CSV = Path(__file__).resolve().parents[2] / "logs" / "salvar_demanda_zerada_rodrigo.csv"


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
    parser = argparse.ArgumentParser(description="Salva carimbos Rodrigo no CONSEN (demanda zerada)")
    parser.add_argument("--salvar",         action="store_true", help="Executa o save (sem isso só simula)")
    parser.add_argument("--retomar-apos",   metavar="CARIMBO",  help="Pula até após este carimbo")
    parser.add_argument("--reprocessar-ok", action="store_true", help="Reprocessa carimbos já marcados OK")
    args = parser.parse_args()

    carimbos = list(CARIMBOS_RODRIGO)
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
