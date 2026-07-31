#!/usr/bin/env python3
"""
Correcao CEMIG MT - fatDescontoFioKWh 45,01 -> 45,14 (Res ANEEL 3.589/2026).

Aplica valor fixo 45,14 em todos os carimbos da lista.
Nao requer PDFs - correcao direta no Consen via editaFaturaCarimbo.php.

Uso:
    python correcao_cemig_mt_desconto_fio_kwh.py             # modo seguro (sem salvar)
    python correcao_cemig_mt_desconto_fio_kwh.py --salvar    # efetiva as correcoes
    python correcao_cemig_mt_desconto_fio_kwh.py --retomar-apos 2012056
    python correcao_cemig_mt_desconto_fio_kwh.py --limite 10
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    from digitacao_consen import correcao_fluxo_base as fluxo_base
    from digitacao_consen.correcao_fluxo_base import CorrecaoFluxoConfig, log, warn
except ModuleNotFoundError:
    import correcao_fluxo_base as fluxo_base  # type: ignore
    from correcao_fluxo_base import CorrecaoFluxoConfig, log, warn  # type: ignore

LOGIN_URL = fluxo_base.LOGIN_URL
BASE_URL  = LOGIN_URL.rsplit("login.php", 1)[0]
EDIT_URL  = f"{BASE_URL}index.php#bpg/gestao/fatura/editaFaturaCarimbo.php"

SAIDA_DIR   = Path("//10.10.250.21/Energia/ARQUIVOS ENZO/CEMIG_pipeline_saida/MT/correcao_desconto_fio_kwh")
EXECUCAO_CSV = SAIDA_DIR / "correcao_execucao.csv"

CARIMBOS_TXT = Path(__file__).with_name("cemig_mt_carimbos_desconto_fio_kwh.txt")

CAMPOS_CRITICOS: tuple[str, ...] = ("btnSalvar", "fatDescontoFioKWh")

CORRECAO_FIXA: dict[str, str] = {
    "fatDescontoFioKWh": "45,14",
}

ORDEM_CAMPOS: tuple[str, ...] = ("fatDescontoFioKWh",)

CONFIG = CorrecaoFluxoConfig(
    saida_dir=SAIDA_DIR,
    execucao_csv=EXECUCAO_CSV,
    edit_url=EDIT_URL,
    ordem_campos=ORDEM_CAMPOS,
    fechar_ao_final=True,
)


def _carregar_lista(txt: Path) -> list[str]:
    carimbos = []
    for line in txt.read_text(encoding="utf-8-sig").splitlines():
        c = line.strip().lstrip("bBbB_")
        c = c.lstrip("Bb_").strip()
        # normaliza: aceita "BB_2012390", "2012390", etc.
        c = fluxo_base.normalizar_carimbo(line.strip())
        if c:
            carimbos.append(c)
    return carimbos


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Correcao CEMIG MT - fatDescontoFioKWh 45,14")
    p.add_argument("--salvar",        action="store_true", help="Efetivar salvamento no Consen")
    p.add_argument("--retomar-apos",  type=str, default="", help="Retomar apos este carimbo")
    p.add_argument("--reprocessar-ok",action="store_true", help="Reprocessar carimbos ja concluidos")
    p.add_argument("--limite",        type=int, default=0,  help="Limitar quantidade de carimbos")
    p.add_argument("--sem-snapshot",  action="store_true",  help="Nao salvar HTML snapshot")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    SAIDA_DIR.mkdir(parents=True, exist_ok=True)

    carimbos = _carregar_lista(CARIMBOS_TXT)
    if not carimbos:
        print(f"Nenhum carimbo encontrado em {CARIMBOS_TXT}")
        return 2

    if args.retomar_apos:
        marcador = fluxo_base.normalizar_carimbo(args.retomar_apos)
        if marcador in carimbos:
            carimbos = carimbos[carimbos.index(marcador) + 1:]

    if not args.reprocessar_ok:
        status_exec = fluxo_base.carregar_status_execucao(EXECUCAO_CSV)
        carimbos = [c for c in carimbos if status_exec.get(c) != "ok"]

    if args.limite > 0:
        carimbos = carimbos[:args.limite]

    if not carimbos:
        log("Nenhum carimbo pendente.")
        return 0

    log(f"Carimbos a processar: {len(carimbos)}")
    log(f"Correcao fixa: {CORRECAO_FIXA}")
    log(f"Modo: {'SALVAR' if args.salvar else 'SEGURO (sem salvar)'}")

    driver = None
    try:
        driver, wait = fluxo_base.abrir_driver_logado()
        fluxo_base.abrir_tela_edicao_carimbo(driver, wait, CONFIG.edit_url)

        for carimbo in carimbos:
            fluxo_base.registrar_execucao(EXECUCAO_CSV, carimbo, "iniciado")
            try:
                fluxo_base.carregar_fatura_por_carimbo(driver, wait, carimbo, CAMPOS_CRITICOS)
            except Exception as exc:
                warn(f"BB_{carimbo}: falha ao carregar fatura - {exc}")
                fluxo_base.registrar_execucao(EXECUCAO_CSV, carimbo, "erro_carregar", str(exc))
                continue

            if not args.sem_snapshot:
                fluxo_base.salvar_snapshot(driver, SAIDA_DIR, carimbo)

            qtd, confirmadas, total = fluxo_base.aplicar_correcoes(
                driver, wait, carimbo, CORRECAO_FIXA, ORDEM_CAMPOS
            )
            log(f"BB_{carimbo}: aplicados={qtd} confirmados={confirmadas}/{total}")

            if args.salvar:
                if confirmadas < total:
                    warn(f"BB_{carimbo}: {confirmadas}/{total} confirmados - bloqueado")
                    fluxo_base.registrar_execucao(EXECUCAO_CSV, carimbo, "bloqueado_incompleto", f"{confirmadas}/{total}")
                else:
                    fluxo_base.salvar_auditar_e_avancar(driver, wait, carimbo)
                    fluxo_base.registrar_execucao(EXECUCAO_CSV, carimbo, "ok", f"{confirmadas}/{total}")
                    log(f"BB_{carimbo}: salvo.")
            else:
                fluxo_base.registrar_execucao(EXECUCAO_CSV, carimbo, "validado_sem_salvar", f"{confirmadas}/{total}")

        return 0
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
