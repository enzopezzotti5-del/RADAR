#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Altera o vencimento da fatura mais recente de cada instalacao no Consen.

Fluxo:
1. Abre a consulta por instalacao.
2. Carrega a tabela da instalacao.
3. Seleciona a linha mais recente visivel na pagina atual (prioridade: maior
   data de referencia; em empate, a primeira linha).
4. Abre a edicao da fatura, captura o carimbo e altera fatDataVcto.
"""
from __future__ import annotations

import argparse
import csv
import sys
import time
from pathlib import Path

from selenium.webdriver.common.by import By

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import _venv_check  # noqa: F401

from core.digitacao_consen import correcao_fluxo_base as fluxo_base
from core.digitacao_consen.correcao_fluxo_base import log, warn
from core.digitacao_consen.digitacao_consen_enel import (
    localizar_tabela_faturas,
    obter_ultima_data_referencia_tabela,
    parse_data_ddmmyyyy,
)
from scripts.corrigir.refaturar_copel import (
    buscar_instalacao,
    capturar_dados_fatura_atual,
    login_consen,
)
from core.digitacao_consen.consen_credentials import resolver_credenciais_consen


DEFAULT_SAIDA = Path("//10.10.250.21/Energia/ARQUIVOS ENZO/correcoes_vencimento")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Altera o vencimento da ultima referencia por instalacao no Consen")
    p.add_argument("--data", required=True, help="Nova data de vencimento (DD/MM/AAAA)")
    p.add_argument("--instalacao", nargs="+", default=[], help="Lista de instalacoes")
    p.add_argument("--arquivo", default="", help="Arquivo TXT com instalacoes (uma por linha)")
    p.add_argument("--salvar", action="store_true", help="Efetiva o salvamento (sem isso e dry-run)")
    p.add_argument("--saida", default=str(DEFAULT_SAIDA), help="Pasta de log de execucao")
    p.add_argument("--headless", action="store_true", help="Executa o Chrome em modo headless")
    return p.parse_args()


def carregar_instalacoes(args: argparse.Namespace) -> list[str]:
    itens: list[str] = list(args.instalacao)
    if args.arquivo:
        path = Path(args.arquivo)
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                itens.append(line)
    vistos: set[str] = set()
    saida: list[str] = []
    for item in itens:
        inst = "".join(ch for ch in str(item).strip() if ch.isdigit())
        if inst and inst not in vistos:
            vistos.add(inst)
            saida.append(inst)
    return saida


def _selecionar_linha_mais_recente_visivel(driver, wait) -> str:
    tabela = localizar_tabela_faturas(driver, wait)
    linhas = tabela.find_elements(By.CSS_SELECTOR, "tbody tr")
    if not linhas:
        raise RuntimeError("Tabela da instalacao sem linhas.")

    alvo_data = obter_ultima_data_referencia_tabela(driver, wait)
    linha_alvo = None
    if alvo_data is not None:
        alvo_fmt = alvo_data.strftime("%d/%m/%Y")
        for linha in linhas:
            colunas = linha.find_elements(By.TAG_NAME, "td")
            if not colunas:
                continue
            if (colunas[0].text or "").strip() == alvo_fmt:
                linha_alvo = linha
                break

    if linha_alvo is None:
        linha_alvo = linhas[0]

    texto_linha = (linha_alvo.text or "").strip()
    imgs = linha_alvo.find_elements(By.TAG_NAME, "img")
    for img in imgs:
        src = img.get_attribute("src") or ""
        if "edit2_icon.png" not in src:
            continue
        try:
            img.click()
        except Exception:
            driver.execute_script("arguments[0].click();", img)
        fluxo_base._aguardar_sem_spinner(driver, timeout=10, min_wait=0.3)  # type: ignore[attr-defined]
        time.sleep(0.8)
        return texto_linha

    raise RuntimeError("Linha mais recente encontrada, mas sem icone de edicao.")


def registrar_resultado(csv_path: Path, row: dict[str, str]) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    existe = csv_path.exists()
    campos = ["timestamp", "instalacao", "referencia_tabela", "carimbo", "status", "detalhe"]
    with csv_path.open("a", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=campos, delimiter=";")
        if not existe:
            writer.writeheader()
        writer.writerow(row)


def _referencia_da_linha(texto_linha: str) -> str:
    for parte in (texto_linha or "").split():
        try:
            return parse_data_ddmmyyyy(parte).strftime("%d/%m/%Y")
        except Exception:
            continue
    return ""


def main() -> int:
    args = parse_args()
    instalacoes = carregar_instalacoes(args)
    if not instalacoes:
        print("Nenhuma instalacao informada.")
        return 2

    saida_dir = Path(args.saida)
    execucao_csv = saida_dir / "vencimento_por_instalacao_execucao.csv"

    print(f"Instalacoes: {len(instalacoes)}")
    print(f"Nova data de vencimento: {args.data.strip()}")
    print(f"Modo: {'SALVAR' if args.salvar else 'DRY-RUN (use --salvar para efetivar)'}")

    usuario, senha = resolver_credenciais_consen()
    driver, wait = login_consen(usuario, senha, headless=bool(args.headless))
    erros: list[str] = []

    try:
        for i, instalacao in enumerate(instalacoes, 1):
            print(f"\n[{i}/{len(instalacoes)}] Instalacao {instalacao} ...", flush=True)
            try:
                buscar_instalacao(driver, wait, instalacao)
                time.sleep(0.8)
                texto_linha = _selecionar_linha_mais_recente_visivel(driver, wait)
                referencia = _referencia_da_linha(texto_linha)
                carimbo, data_cadastro, _valor = capturar_dados_fatura_atual(driver)
                if not carimbo:
                    raise RuntimeError("Carimbo nao identificado na tela de edicao.")

                correcoes = {"fatDataVcto": args.data.strip()}
                _aplicadas, confirmadas, total = fluxo_base.aplicar_correcoes(driver, wait, carimbo, correcoes)
                if not args.salvar:
                    registrar_resultado(
                        execucao_csv,
                        {
                            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                            "instalacao": instalacao,
                            "referencia_tabela": referencia,
                            "carimbo": f"BB_{carimbo}",
                            "status": "validado_sem_salvar",
                            "detalhe": data_cadastro,
                        },
                    )
                    log(f"Instalacao {instalacao}: BB_{carimbo} | ref={referencia} | dry-run OK")
                    continue

                if confirmadas < total:
                    registrar_resultado(
                        execucao_csv,
                        {
                            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                            "instalacao": instalacao,
                            "referencia_tabela": referencia,
                            "carimbo": f"BB_{carimbo}",
                            "status": "bloqueado",
                            "detalhe": "campo_nao_confirmado",
                        },
                    )
                    erros.append(instalacao)
                    warn(f"Instalacao {instalacao}: BB_{carimbo} sem confirmacao do campo.")
                    continue

                fluxo_base.salvar_auditar_e_avancar(driver, wait, carimbo)
                registrar_resultado(
                    execucao_csv,
                    {
                        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                        "instalacao": instalacao,
                        "referencia_tabela": referencia,
                        "carimbo": f"BB_{carimbo}",
                        "status": "ok",
                        "detalhe": data_cadastro,
                    },
                )
                log(f"Instalacao {instalacao}: BB_{carimbo} salvo | ref={referencia}")

            except Exception as exc:
                registrar_resultado(
                    execucao_csv,
                    {
                        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                        "instalacao": instalacao,
                        "referencia_tabela": "",
                        "carimbo": "",
                        "status": "erro",
                        "detalhe": f"{type(exc).__name__}: {exc}"[:160],
                    },
                )
                erros.append(instalacao)
                warn(f"Instalacao {instalacao}: erro - {type(exc).__name__}: {exc}")
                try:
                    driver.quit()
                except Exception:
                    pass
                driver, wait = login_consen(usuario, senha, headless=bool(args.headless))

    finally:
        try:
            driver.quit()
        except Exception:
            pass

    print(f"\nFinalizado. Erros: {erros if erros else 'nenhum'}")
    return 1 if erros else 0


if __name__ == "__main__":
    raise SystemExit(main())
