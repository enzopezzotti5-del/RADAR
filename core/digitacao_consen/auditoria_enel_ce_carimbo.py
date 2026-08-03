#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Auditoria ENEL CEARA por carimbo.

Fluxo para cada carimbo informado:
  1. Abre a fatura no Consen via editaFaturaCarimbo.php
  2. Preenche o campo Analise com o texto padrao
  3. Marca o checkbox "Auditoria concluida?"
  4. Clica em Salvar

Uso:
    python auditoria_enel_ce_carimbo.py --carimbo 2008391 --carimbo 2008392 --salvar
    python auditoria_enel_ce_carimbo.py --carimbos-arquivo carimbos_enel_ce.txt --salvar
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import _venv_check  # noqa: E402,F401

try:
    from digitacao_consen import correcao_fluxo_base as fluxo_base
    from digitacao_consen.correcao_fluxo_base import log, warn
    from digitacao_consen.digitacao_consen_enel import (
        _aguardar_sem_spinner,
        clicar_botao_salvar,
    )
except ModuleNotFoundError:
    import correcao_fluxo_base as fluxo_base  # type: ignore
    from correcao_fluxo_base import log, warn  # type: ignore
    from digitacao_consen_enel import (  # type: ignore
        _aguardar_sem_spinner,
        clicar_botao_salvar,
    )

DEFAULT_SAIDA_DIR = "//10.10.250.21/Energia/ARQUIVOS ENZO/ENEL_CE_pipeline_saida/auditoria_por_carimbo"

LOGIN_URL = fluxo_base.LOGIN_URL
BASE_URL = LOGIN_URL.rsplit("login.php", 1)[0]
EDIT_URL = os.environ.get(
    "CONSEN_EDITA_FATURA_CARIMBO_URL",
    f"{BASE_URL}index.php#bpg/gestao/fatura/editaFaturaCarimbo.php",
)

SAIDA_DIR = Path(os.environ.get("CONSEN_AUDITORIA_ENEL_CE_SAIDA", DEFAULT_SAIDA_DIR))
EXECUCAO_CSV = SAIDA_DIR / "auditoria_execucao.csv"
FECHAR_AO_FINAL = os.environ.get("CONSEN_CORRECAO_FECHAR", "1").strip().lower() not in {"0", "false", "nao", "não"}

TEXTO_ANALISE = os.environ.get("CONSEN_TEXTO_ANALISE", "Tarifa proporcional ao reajuste")
CAMPOS_CRITICOS_TELA: tuple[str, ...] = ("btnSalvar", "txtAuditoriaAnalise")


def normalizar_carimbo(carimbo: str) -> str:
    return fluxo_base.normalizar_carimbo(carimbo)


def registrar_execucao(carimbo: str, status: str, detalhe: str = "") -> None:
    fluxo_base.registrar_execucao(EXECUCAO_CSV, carimbo, status, detalhe)


def carregar_status_execucao() -> dict[str, str]:
    return fluxo_base.carregar_status_execucao(EXECUCAO_CSV)


def abrir_driver_logado():
    return fluxo_base.abrir_driver_logado()


def carregar_fatura_por_carimbo(driver, wait, carimbo: str) -> None:
    """Navega direto para editaTabFatura evitando submit do formulário via GET."""
    carimbo_norm = normalizar_carimbo(carimbo)
    log(f"Carregando fatura BB_{carimbo_norm} por URL direta...")
    base = LOGIN_URL.rsplit("login.php", 1)[0]
    destino = f"{base}index.php#bpg/gestao/fatura/editaTabFatura.php?carimbo={carimbo_norm}"

    for tentativa in range(3):
        try:
            driver.get(destino)
        except Exception:
            pass
        try:
            _aguardar_sem_spinner(driver, timeout=12, min_wait=0.5)
        except Exception:
            pass
        url_atual = driver.current_url or ""
        # Fallback SPA: se o hash não foi respeitado, injetar via JS
        if f"carimbo={carimbo_norm}" not in url_atual:
            try:
                driver.execute_script(
                    "window.location.hash = 'bpg/gestao/fatura/editaTabFatura.php?carimbo=' + arguments[0];",
                    carimbo_norm,
                )
                time.sleep(1.5)
                _aguardar_sem_spinner(driver, timeout=10, min_wait=0.3)
            except Exception:
                pass
        try:
            WebDriverWait(driver, 12).until(EC.presence_of_element_located((By.ID, "btnSalvar")))
            WebDriverWait(driver, 8).until(EC.presence_of_element_located((By.ID, "txtAuditoriaAnalise")))
            log(f"BB_{carimbo_norm}: fatura carregada para edicao.")
            return
        except Exception:
            if tentativa < 2:
                log(f"BB_{carimbo_norm}: tentativa {tentativa + 1} falhou, retentando...")
                time.sleep(2)
            else:
                raise TimeoutError(f"BB_{carimbo_norm}: tela de edicao nao carregou apos 3 tentativas.")


def _preencher_texto(driver, elemento, texto: str) -> None:
    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", elemento)
    time.sleep(0.1)
    driver.execute_script(
        """
        arguments[0].focus();
        arguments[0].value = arguments[1];
        arguments[0].dispatchEvent(new Event('input', {bubbles:true}));
        arguments[0].dispatchEvent(new Event('change', {bubbles:true}));
        """,
        elemento,
        texto,
    )


def _marcar_checkbox(driver, elemento) -> None:
    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", elemento)
    time.sleep(0.1)
    already_checked = driver.execute_script("return arguments[0].checked;", elemento)
    if not already_checked:
        driver.execute_script(
            """
            arguments[0].checked = true;
            arguments[0].dispatchEvent(new Event('change', {bubbles:true}));
            arguments[0].dispatchEvent(new Event('click', {bubbles:true}));
            """,
            elemento,
        )
        log("Checkbox 'Auditoria concluida?' marcado.")
    else:
        log("Checkbox 'Auditoria concluida?' ja estava marcado.")


def preencher_auditoria(driver, wait, carimbo: str, texto: str | None = None) -> tuple[bool, bool]:
    carimbo_norm = normalizar_carimbo(carimbo)
    texto_usar = texto if texto is not None else TEXTO_ANALISE

    # Preenche campo Analise
    analise_ok = False
    try:
        el_analise = wait.until(EC.presence_of_element_located((By.ID, "txtAuditoriaAnalise")))
        _preencher_texto(driver, el_analise, texto_usar)
        valor_atual = driver.execute_script("return arguments[0].value;", el_analise) or ""
        analise_ok = texto_usar in valor_atual
        if analise_ok:
            log(f"BB_{carimbo_norm}: campo Analise preenchido.")
        else:
            warn(f"BB_{carimbo_norm}: campo Analise pode nao ter sido confirmado (valor atual: {valor_atual!r}, esperado: {texto_usar!r}).")
    except Exception as exc:
        warn(f"BB_{carimbo_norm}: erro ao preencher Analise: {type(exc).__name__} - {exc}")

    # Marca checkbox Auditoria concluida
    concluida_ok = False
    try:
        seletores_cb = [
            (By.ID, "txtAuditoriaConcluida"),
            (By.NAME, "txtAuditoriaConcluida"),
            (By.CSS_SELECTOR, "input[type='checkbox'][id*='Concluida']"),
            (By.CSS_SELECTOR, "input[type='checkbox'][name*='Concluida']"),
        ]
        el_cb = None
        for by, sel in seletores_cb:
            encontrados = driver.find_elements(by, sel)
            if encontrados:
                el_cb = encontrados[0]
                break
        if el_cb is None:
            raise RuntimeError("Checkbox 'Auditoria concluida?' nao localizado.")
        _marcar_checkbox(driver, el_cb)
        concluida_ok = bool(driver.execute_script("return arguments[0].checked;", el_cb))
        if not concluida_ok:
            warn(f"BB_{carimbo_norm}: checkbox nao ficou marcado apos tentativa.")
    except Exception as exc:
        warn(f"BB_{carimbo_norm}: erro ao marcar checkbox Auditoria concluida: {type(exc).__name__} - {exc}")

    return analise_ok, concluida_ok


def salvar(driver, wait, carimbo: str, texto: str | None = None) -> None:
    carimbo_norm = normalizar_carimbo(carimbo)
    texto_usar = texto if texto is not None else TEXTO_ANALISE
    log(f"BB_{carimbo_norm}: clicando em Salvar...")
    clicar_botao_salvar(driver, wait)
    _aguardar_sem_spinner(driver, timeout=10, min_wait=0.5)

    # Verifica se o save realmente persistiu: recarrega a fatura e confere os campos
    log(f"BB_{carimbo_norm}: verificando persistencia do save...")
    try:
        carregar_fatura_por_carimbo(driver, wait, carimbo_norm)
        el_analise = driver.find_element(By.ID, "txtAuditoriaAnalise")
        valor_salvo = driver.execute_script("return arguments[0].value;", el_analise) or ""
        if texto_usar not in valor_salvo:
            raise RuntimeError(
                f"campo Analise nao persistiu apos salvar "
                f"(esperado: {texto_usar!r}, atual: {valor_salvo!r})"
            )
        seletores_cb = [
            (By.ID, "txtAuditoriaConcluida"),
            (By.NAME, "txtAuditoriaConcluida"),
            (By.CSS_SELECTOR, "input[type='checkbox'][id*='Concluida']"),
        ]
        cb_marcado = False
        for by, sel in seletores_cb:
            els = driver.find_elements(by, sel)
            if els:
                cb_marcado = bool(driver.execute_script("return arguments[0].checked;", els[0]))
                break
        if not cb_marcado:
            raise RuntimeError("checkbox 'Auditoria concluida?' nao ficou marcado apos salvar")
        log(f"BB_{carimbo_norm}: save confirmado (analise e checkbox persistidos).")
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(f"falha ao verificar save: {type(exc).__name__} - {exc}") from exc


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Auditoria ENEL CEARA por carimbo no Consen")
    p.add_argument("--carimbo", action="append", default=[], metavar="CARIMBO",
                   help="Carimbo a processar (pode repetir). Ex: --carimbo 2008391")
    p.add_argument("--carimbos-arquivo", type=str, default="",
                   help="Arquivo TXT com um carimbo por linha.")
    p.add_argument("--salvar", action="store_true",
                   help="Efetiva o salvamento no Consen (sem esta flag so simula).")
    p.add_argument("--retomar-apos", type=str, default="",
                   help="Pula todos os carimbos ate este (inclusive) e retoma a partir do proximo.")
    p.add_argument("--reprocessar-ok", action="store_true",
                   help="Reprocessa carimbos que ja tem status 'ok' no log de execucao.")
    p.add_argument("--limite", type=int, default=0,
                   help="Limita a quantidade de carimbos processados nesta execucao.")
    p.add_argument("--analises-json", type=str, default="",
                   help="JSON file com {carimbo: texto_analise} por carimbo (sobrepoe CONSEN_TEXTO_ANALISE).")
    return p.parse_args()


def carregar_lista_carimbos(args: argparse.Namespace) -> list[str]:
    itens: list[str] = list(args.carimbo or [])
    if args.carimbos_arquivo:
        path = Path(args.carimbos_arquivo)
        if not path.exists():
            print(f"Arquivo nao encontrado: {path}")
            raise SystemExit(2)
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                itens.append(line)
    vistos: set[str] = set()
    saida: list[str] = []
    for item in itens:
        try:
            c = normalizar_carimbo(item)
        except ValueError:
            continue
        if c not in vistos:
            vistos.add(c)
            saida.append(c)
    return saida


def main() -> int:
    import json as _json
    args = parse_args()
    carimbos = carregar_lista_carimbos(args)
    if not carimbos:
        print("Informe ao menos um --carimbo ou --carimbos-arquivo com carimbos validos.")
        return 2

    analises_map: dict[str, str] = {}
    if args.analises_json:
        try:
            analises_map = _json.loads(Path(args.analises_json).read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"Aviso: nao foi possivel carregar --analises-json: {exc}")

    if args.retomar_apos:
        marcador = normalizar_carimbo(args.retomar_apos)
        if marcador in carimbos:
            carimbos = carimbos[carimbos.index(marcador) + 1:]

    if not args.reprocessar_ok:
        status_execucao = carregar_status_execucao()
        carimbos = [c for c in carimbos if status_execucao.get(normalizar_carimbo(c)) != "ok"]

    if args.limite and args.limite > 0:
        carimbos = carimbos[: args.limite]

    if not carimbos:
        print("Nenhum carimbo pendente para processar.")
        return 0

    print(f"Carimbos a processar: {len(carimbos)}")
    if not args.salvar:
        print("[MODO SIMULACAO] Use --salvar para efetivar as alteracoes no Consen.")

    driver = None
    try:
        driver, wait = abrir_driver_logado()
        for carimbo in carimbos:
            registrar_execucao(carimbo, "iniciado")
            texto_carimbo = analises_map.get(carimbo, TEXTO_ANALISE)
            try:
                carregar_fatura_por_carimbo(driver, wait, carimbo)
                analise_ok, concluida_ok = preencher_auditoria(driver, wait, carimbo, texto=texto_carimbo)

                if args.salvar:
                    if not analise_ok:
                        warn(f"BB_{carimbo}: campo Analise nao confirmado. Salvamento bloqueado.")
                        registrar_execucao(carimbo, "bloqueado_analise_falhou")
                    elif not concluida_ok:
                        warn(f"BB_{carimbo}: checkbox Auditoria concluida nao confirmado. Salvamento bloqueado.")
                        registrar_execucao(carimbo, "bloqueado_checkbox_falhou")
                    else:
                        salvar(driver, wait, carimbo, texto=texto_carimbo)
                        registrar_execucao(carimbo, "ok")
                        log(f"BB_{carimbo}: auditoria salva com sucesso.")
                else:
                    detalhe = f"analise={'ok' if analise_ok else 'falhou'} concluida={'ok' if concluida_ok else 'falhou'}"
                    registrar_execucao(carimbo, "simulado", detalhe)
                    log(f"BB_{carimbo}: simulacao concluida ({detalhe}).")

            except Exception as exc:
                warn(f"BB_{carimbo}: erro inesperado: {type(exc).__name__} - {exc}")
                registrar_execucao(carimbo, "erro", str(exc)[:200])

        return 0
    finally:
        if driver and FECHAR_AO_FINAL:
            try:
                driver.quit()
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
