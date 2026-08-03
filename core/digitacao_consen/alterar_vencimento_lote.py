#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Altera a data de vencimento (fatDataVcto) em lote no Consen.

Uso:
    python alterar_vencimento_lote.py --data 20/06/2026 --carimbo 101 21 74 ...
    python alterar_vencimento_lote.py --data 20/06/2026 --arquivo carimbos.txt
    python alterar_vencimento_lote.py --data 20/06/2026 --carimbo 101 21 74 --salvar
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import _venv_check  # noqa: F401

os.environ.setdefault("CONSEN_INVESTIGAR_ZEROS", "0")

try:
    from digitacao_consen import correcao_fluxo_base as fluxo_base
    from digitacao_consen.correcao_fluxo_base import log, warn
except ModuleNotFoundError:
    import correcao_fluxo_base as fluxo_base  # type: ignore
    from correcao_fluxo_base import log, warn  # type: ignore

LOGIN_URL = fluxo_base.LOGIN_URL
BASE_URL = LOGIN_URL.rsplit("login.php", 1)[0]
EDIT_URL = f"{BASE_URL}index.php#bpg/gestao/fatura/editaFaturaCarimbo.php"

DEFAULT_SAIDA = Path("//10.10.250.21/Energia/ARQUIVOS ENZO/correcoes_vencimento")
CAMPOS_CRITICOS: tuple[str, ...] = ("btnSalvar",)
POSSIVEIS_CAMPOS_VENCIMENTO: tuple[str, ...] = ("dataVencimento", "fatDataVcto")


def salvar_sem_auditoria(driver, wait, carimbo: str) -> None:
    carimbo_norm = fluxo_base.normalizar_carimbo(carimbo)
    log(f"BB_{carimbo_norm}: clicando em Salvar (modo rapido, sem auditoria)...")
    fluxo_base.clicar_botao_salvar(driver, wait)
    fluxo_base._aguardar_sem_spinner(driver, timeout=8, min_wait=0.3)
    time.sleep(0.4)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Altera data de vencimento em lote no Consen")
    p.add_argument("--data", required=True, help="Nova data de vencimento (DD/MM/AAAA)")
    p.add_argument("--carimbo", nargs="+", default=[], help="Lista de carimbos")
    p.add_argument("--arquivo", default="", help="Arquivo TXT com carimbos (um por linha)")
    p.add_argument("--salvar", action="store_true", help="Efetiva o salvamento (sem isso é dry-run)")
    p.add_argument("--saida", default=str(DEFAULT_SAIDA), help="Pasta de log de execução")
    return p.parse_args()


def carregar_carimbos(args) -> list[str]:
    itens: list[str] = list(args.carimbo)
    if args.arquivo:
        path = Path(args.arquivo)
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                itens.append(line)
    vistos: set[str] = set()
    saida: list[str] = []
    for item in itens:
        c = fluxo_base.normalizar_carimbo(item)
        if c not in vistos:
            vistos.add(c)
            saida.append(c)
    return saida


def main() -> int:
    args = parse_args()
    carimbos = carregar_carimbos(args)
    if not carimbos:
        print("Nenhum carimbo informado.")
        return 2

    data_nova = args.data.strip()
    saida_dir = Path(args.saida)
    execucao_csv = saida_dir / "vencimento_execucao.csv"

    print(f"Carimbos: {len(carimbos)}")
    print(f"Nova data de vencimento: {data_nova}")
    print(f"Modo: {'SALVAR' if args.salvar else 'DRY-RUN (use --salvar para efetivar)'}")
    print()

    status_anterior = fluxo_base.carregar_status_execucao(execucao_csv)
    ja_ok = [c for c in carimbos if status_anterior.get(c) == "ok"]
    if ja_ok:
        print(f"Pulando {len(ja_ok)} carimbos já marcados como 'ok': {ja_ok}")
        carimbos = [c for c in carimbos if status_anterior.get(c) != "ok"]

    if not carimbos:
        print("Todos os carimbos já processados.")
        return 0

    driver, wait = fluxo_base.abrir_driver_logado()
    time.sleep(2.0)
    erros: list[str] = []

    try:
        for i, carimbo in enumerate(carimbos, 1):
            print(f"\n[{i}/{len(carimbos)}] BB_{carimbo} ...", flush=True)
            try:
                fluxo_base.abrir_tela_edicao_carimbo(driver, wait, EDIT_URL)
                time.sleep(1.0)
                fluxo_base.carregar_fatura_por_carimbo(driver, wait, carimbo, CAMPOS_CRITICOS)
                time.sleep(0.8)

                campo_vencimento = None
                for candidato in POSSIVEIS_CAMPOS_VENCIMENTO:
                    try:
                        fluxo_base.localizar_input_exato(driver, wait, candidato)
                        campo_vencimento = candidato
                        break
                    except Exception:
                        continue

                if not campo_vencimento:
                    raise RuntimeError(
                        "Nenhum campo de vencimento encontrado na tela "
                        f"(testados: {', '.join(POSSIVEIS_CAMPOS_VENCIMENTO)})."
                    )

                correcoes = {campo_vencimento: data_nova}
                qtd, confirmadas, total = fluxo_base.aplicar_correcoes(
                    driver, wait, carimbo, correcoes
                )

                if not args.salvar:
                    fluxo_base.registrar_execucao(execucao_csv, carimbo, "validado_sem_salvar", data_nova)
                    log(f"BB_{carimbo}: dry-run OK (campo preenchido, não salvo)")
                    continue

                if confirmadas < total:
                    warn(f"BB_{carimbo}: campo não confirmado — pulando salvamento")
                    fluxo_base.registrar_execucao(execucao_csv, carimbo, "bloqueado", data_nova)
                    erros.append(carimbo)
                    continue

                time.sleep(0.4)
                salvar_sem_auditoria(driver, wait, carimbo)
                fluxo_base.registrar_execucao(execucao_csv, carimbo, "ok", data_nova)
                log(f"BB_{carimbo}: salvo.")

            except Exception as exc:
                warn(f"BB_{carimbo}: erro — {type(exc).__name__}: {exc}")
                fluxo_base.registrar_execucao(execucao_csv, carimbo, "erro", str(exc)[:120])
                erros.append(carimbo)
                try:
                    driver.get(LOGIN_URL)
                    time.sleep(1.5)
                    from digitacao_consen.digitacao_consen_enel import enviar_login, USUARIO, SENHA
                    enviar_login(driver, wait, USUARIO, SENHA)
                    time.sleep(2.0)
                except Exception:
                    pass

    finally:
        try:
            driver.quit()
        except Exception:
            pass

    print(f"\nFinalizado. Erros: {erros if erros else 'nenhum'}")
    return 1 if erros else 0


if __name__ == "__main__":
    raise SystemExit(main())
