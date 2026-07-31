#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import csv
import json
import math
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

try:
    from digitacao_consen.digitacao_consen_enel import (
        HEADLESS,
        LOGIN_URL,
        SENHA,
        USUARIO,
        _aguardar_sem_spinner,
        clicar_botao,
        clicar_botao_proxima_fatura,
        clicar_botao_salvar,
        clicar_link_auditoria,
        enviar_login,
        formatar_valor_para_campo,
        iniciar_driver,
        log,
        preencher_elemento_html,
        preencher_input_texto,
        warn,
    )
except ModuleNotFoundError:
    from digitacao_consen_enel import (  # type: ignore
        HEADLESS,
        LOGIN_URL,
        SENHA,
        USUARIO,
        _aguardar_sem_spinner,
        clicar_botao,
        clicar_botao_proxima_fatura,
        clicar_botao_salvar,
        clicar_link_auditoria,
        enviar_login,
        formatar_valor_para_campo,
        iniciar_driver,
        log,
        preencher_elemento_html,
        preencher_input_texto,
        warn,
    )


@dataclass(frozen=True)
class CorrecaoFluxoConfig:
    saida_dir: Path
    execucao_csv: Path
    edit_url: str
    ordem_campos: tuple[str, ...] = ()
    fechar_ao_final: bool = True


def normalizar_carimbo(carimbo: str) -> str:
    txt = str(carimbo or "").strip().upper()
    txt = txt.replace("BB_", "").replace(".PDF", "")
    txt = re.sub(r"\D", "", txt)
    if not txt:
        raise ValueError("Carimbo vazio.")
    if len(txt) > 7:
        txt = txt[:7]
    return txt


def valor_vazio(valor: Any) -> bool:
    if valor is None:
        return True
    if isinstance(valor, float) and math.isnan(valor):
        return True
    txt = str(valor).strip()
    return txt == "" or txt.lower() in {"nan", "none", "null"}


def registrar_execucao(execucao_csv: Path, carimbo: str, status: str, detalhe: str = "") -> None:
    execucao_csv.parent.mkdir(parents=True, exist_ok=True)
    existe = execucao_csv.exists()
    with execucao_csv.open("a", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f, delimiter=";")
        if not existe:
            writer.writerow(["timestamp", "carimbo", "status", "detalhe"])
        writer.writerow([time.strftime("%Y-%m-%d %H:%M:%S"), f"BB_{normalizar_carimbo(carimbo)}", status, detalhe])


def carregar_status_execucao(execucao_csv: Path) -> dict[str, str]:
    if not execucao_csv.exists():
        return {}
    status: dict[str, str] = {}
    with execucao_csv.open("r", newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f, delimiter=";"):
            carimbo_bruto = str(row.get("carimbo") or "").strip()
            if not carimbo_bruto:
                continue
            status[normalizar_carimbo(carimbo_bruto)] = str(row.get("status") or "").strip()
    return status


def abrir_driver_logado():
    driver = iniciar_driver(headless=HEADLESS)
    wait = WebDriverWait(driver, 20)
    log("Abrindo login...")
    driver.get(LOGIN_URL)
    log("Fazendo login...")
    enviar_login(driver, wait, USUARIO, SENHA)
    time.sleep(1.2)
    _aguardar_sem_spinner(driver, timeout=8, min_wait=0.3)
    return driver, wait


def abrir_tela_edicao_carimbo(driver, wait, edit_url: str) -> None:
    log(f"Abrindo tela de edicao por carimbo: {edit_url}")
    base_url, _, hash_part = edit_url.partition("#")
    hash_destino = hash_part or "bpg/gestao/fatura/editaFaturaCarimbo.php"
    destinos = [
        edit_url,
        f"{base_url}#bpg/gestao/fatura/editaFaturaCarimbo.php" if base_url else edit_url,
        f"{base_url}#bpg/gestao/fatura/consultaFaturaCarimbo.php" if base_url else edit_url,
    ]

    def _form_pronto(timeout: float = 8.0) -> bool:
        limite = time.time() + timeout
        while time.time() < limite:
            try:
                _aguardar_sem_spinner(driver, timeout=4, min_wait=0.2)
            except Exception:
                pass
            try:
                campo = driver.find_elements(By.ID, "carimbo")
                botao = driver.find_elements(By.ID, "botaoCarregar")
                if campo and botao:
                    return True
            except Exception:
                pass
            time.sleep(0.25)
        return False

    # 1. Tenta acessos diretos conhecidos.
    for destino in destinos:
        try:
            driver.get(destino)
        except Exception:
            continue
        if _form_pronto():
            log("Tela de edicao por carimbo pronta.")
            return

    # 2. Fallback robusto para SPA: abre index.php e injeta o hash.
    try:
        driver.get(base_url or edit_url)
    except Exception:
        pass
    try:
        driver.execute_script("window.location.hash = arguments[0];", hash_destino)
    except Exception:
        pass

    if _form_pronto(timeout=12.0):
        log("Tela de edicao por carimbo pronta.")
        return

    # 3. Ultimo fallback: seta href completo via JS e aguarda novamente.
    try:
        driver.execute_script(
            "window.location.href = arguments[0];",
            f"{base_url}#{hash_destino}" if base_url and "#" not in edit_url else edit_url,
        )
    except Exception:
        pass

    if _form_pronto(timeout=12.0):
        log("Tela de edicao por carimbo pronta.")
        return

    raise TimeoutError("Tela de edicao por carimbo nao carregou.")


def carregar_fatura_por_carimbo(driver, wait, carimbo: str, campos_criticos: tuple[str, ...]) -> None:
    carimbo_norm = normalizar_carimbo(carimbo)
    log(f"Carregando fatura pelo carimbo BB_{carimbo_norm}...")
    time.sleep(0.2)

    preencher_input_texto(driver, wait, "carimbo", carimbo_norm, pausa_antes=0.2)
    clicar_botao(
        driver,
        wait,
        [
            (By.ID, "botaoCarregar"),
            (By.NAME, "botaoCarregar"),
            (By.CSS_SELECTOR, "button#botaoCarregar"),
            (By.CSS_SELECTOR, "input#botaoCarregar"),
            (By.CSS_SELECTOR, "button[type='submit']"),
            (By.CSS_SELECTOR, "input[type='submit']"),
        ],
        "Carregar por carimbo",
    )

    _aguardar_sem_spinner(driver, timeout=12, min_wait=0.5)

    try:
        WebDriverWait(driver, 10).until(lambda d: "editaTabFatura" in (d.current_url or ""))
    except Exception:
        url = driver.current_url or ""
        if "editaTabFatura" not in url:
            log("Forcando navegacao SPA para a tela de edicao da fatura por carimbo...")
            try:
                driver.execute_script(
                    "window.location.hash = 'bpg/gestao/fatura/editaTabFatura.php?carimbo=' + arguments[0];",
                    carimbo_norm,
                )
                _aguardar_sem_spinner(driver, timeout=12, min_wait=0.5)
            except Exception:
                pass
            url = driver.current_url or ""
        if f"carimbo={carimbo_norm}" in url and "editaTabFatura" not in url:
            log("CONSEN atualizou a URL apos o carregar; recarregando a pagina atual para concluir a navegacao...")
            driver.get(url)
            _aguardar_sem_spinner(driver, timeout=12, min_wait=0.5)

    try:
        WebDriverWait(driver, 12).until(EC.presence_of_element_located((By.ID, "btnSalvar")))
        for campo in campos_criticos:
            WebDriverWait(driver, 12).until(EC.presence_of_element_located((By.ID, campo)))
        log("Fatura carregada para edicao.")
    except Exception:
        url = driver.current_url or ""
        warn(f"Nao confirmei o botao Salvar apos carregar. URL atual: {url}")
        raise TimeoutError(f"Fatura BB_{carimbo_norm} nao abriu a tela de edicao.")


def coletar_campos_visiveis(driver) -> list[dict[str, str]]:
    script = """
        return Array.from(document.querySelectorAll('input, select, textarea')).map((el) => ({
            tag: el.tagName.toLowerCase(),
            type: el.getAttribute('type') || '',
            id: el.id || '',
            name: el.getAttribute('name') || '',
            value: el.value || '',
            text: el.tagName.toLowerCase() === 'select'
                ? Array.from(el.options).find(o => o.selected)?.textContent?.trim() || ''
                : '',
            visible: !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length)
        }));
    """
    try:
        return list(driver.execute_script(script) or [])
    except Exception:
        return []


def salvar_snapshot(driver, saida_dir: Path, carimbo: str) -> None:
    saida_dir.mkdir(parents=True, exist_ok=True)
    carimbo_norm = normalizar_carimbo(carimbo)
    html_path = saida_dir / f"BB_{carimbo_norm}_edicao.html"
    json_path = saida_dir / f"BB_{carimbo_norm}_campos.json"
    html_path.write_text(driver.page_source or "", encoding="utf-8", errors="replace")
    json_path.write_text(
        json.dumps(coletar_campos_visiveis(driver), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    log(f"Snapshot salvo: {html_path}")
    log(f"Campos salvos: {json_path}")


def localizar_input_exato(driver, wait, campo: str):
    seletores = [
        (By.ID, campo),
        (By.NAME, campo),
        (By.CSS_SELECTOR, f"input[id='{campo}'], select[id='{campo}'], textarea[id='{campo}']"),
        (By.CSS_SELECTOR, f"input[name='{campo}'], select[name='{campo}'], textarea[name='{campo}']"),
    ]
    for by, sel in seletores:
        try:
            encontrados = driver.find_elements(by, sel)
        except Exception:
            continue
        visiveis = []
        for el in encontrados:
            try:
                if el.is_displayed():
                    visiveis.append(el)
            except Exception:
                pass
        if visiveis:
            return visiveis[0]
        if encontrados:
            return encontrados[0]

    WebDriverWait(driver, 4).until(EC.presence_of_element_located((By.ID, campo)))
    return driver.find_element(By.ID, campo)


def aplicar_correcoes(
    driver,
    wait,
    carimbo: str,
    correcoes: dict[str, Any],
    ordem_campos: tuple[str, ...] = (),
) -> tuple[int, int, int]:
    carimbo_norm = normalizar_carimbo(carimbo)
    if not correcoes:
        log(f"Sem correcoes cadastradas para BB_{carimbo_norm}. Nada sera alterado.")
        return 0, 0, 0

    aplicadas = 0
    confirmadas = 0
    campos_ordenados = [campo for campo in ordem_campos if campo in correcoes]
    campos_ordenados.extend(campo for campo in correcoes if campo not in campos_ordenados)

    for campo in campos_ordenados:
        valor = correcoes.get(campo)
        campo = str(campo).strip()
        if not campo:
            continue
        log(f"[CORRECAO] {campo} <- {valor!r}")
        try:
            elemento = localizar_input_exato(driver, wait, campo)
            valor_atual = (elemento.get_attribute("value") or "").strip()
            if valor_atual == str(valor).strip():
                log(f"[CORRECAO] {campo} ja estava com o valor esperado ({valor_atual}).")
                confirmadas += 1
                continue
            ok = preencher_elemento_html(driver, elemento, valor)
            if ok:
                aplicadas += 1
                confirmadas += 1
            else:
                warn(f"[CORRECAO] Falha ao confirmar preenchimento de {campo}.")
        except Exception as exc:
            warn(f"[CORRECAO] Campo {campo} pulado: {type(exc).__name__} - {exc}")
    total = len(campos_ordenados)
    log(f"Correcoes aplicadas em BB_{carimbo_norm}: {aplicadas} | confirmadas: {confirmadas}/{total}")
    return aplicadas, confirmadas, total


def _capturar_auditoria(driver) -> tuple[str, str, str]:
    """Lê pct_diferenca, valor_diferenca e itens_divergentes da aba de auditoria.

    Retorna (pct, total, itens) como strings, vazias em caso de falha.
    - pct   : ex. '0,05%'
    - total : ex. '-0,01'
    - itens : ex. 'ICMS=R$-0,01|Total=R$-0,01'
    """
    pct = total = itens = ""
    try:
        spans = driver.find_elements(By.CSS_SELECTOR, "span.auditoria.sucesso")
        # A página normalmente tem 1 span com a % e opcionalmente um 2º com o valor R$
        if len(spans) >= 2:
            pct   = spans[0].text.strip()
            total = spans[1].text.strip()
        elif len(spans) == 1:
            pct   = spans[0].text.strip()
        if pct or total:
            log(f"[auditoria] pct={pct!r}  total={total!r}")
    except Exception as exc:
        warn(f"[auditoria] nao capturou spans: {exc}")

    try:
        tabela = driver.find_element(By.CSS_SELECTOR, "table.table-bordered")
        divs = []
        for linha in tabela.find_elements(By.TAG_NAME, "tr")[1:]:
            cols = linha.find_elements(By.TAG_NAME, "td")
            if len(cols) >= 7:
                descricao = cols[0].text.strip()
                diferenca = cols[6].text.strip()
                if diferenca and "0,00" not in diferenca:
                    divs.append(f"{descricao}={diferenca}")
        itens = "|".join(divs)
    except Exception as exc:
        warn(f"[auditoria] nao capturou tabela: {exc}")

    return pct, total, itens


def salvar_auditar_e_avancar(
    driver, wait, carimbo: str, *, rapido: bool = False
) -> tuple[str, str, str]:
    """Salva, abre auditoria, captura % diferença, fecha aba e avança.

    rapido=True: não lê o resultado da auditoria — apenas salva, clica
    auditoria e avança sem esperar a aba carregar. Ideal para correções em lote
    onde o % diferença não precisa ser registrado.

    Retorna (pct_diferenca, valor_diferenca, itens_divergentes).
    """
    carimbo_norm = normalizar_carimbo(carimbo)
    log(f"BB_{carimbo_norm}: clicando em Salvar...")
    clicar_botao_salvar(driver, wait)
    _aguardar_sem_spinner(driver, timeout=10, min_wait=0.3 if rapido else 0.5)

    aba_principal = driver.current_window_handle
    abas_antes = list(driver.window_handles)

    try:
        WebDriverWait(driver, 5 if rapido else 8).until(
            EC.presence_of_element_located((By.ID, "linkAuditoria"))
        )
    except Exception:
        time.sleep(0.5 if rapido else 1.0)

    log(f"BB_{carimbo_norm}: abrindo Auditoria...")
    clicar_link_auditoria(driver, wait)

    nova_aba = None
    # Modo rápido: espera no máximo 2 s pela nova aba; modo normal: 10 s.
    iteracoes = 8 if rapido else 40
    for _ in range(iteracoes):
        abas_agora = list(driver.window_handles)
        if len(abas_agora) > len(abas_antes):
            novas = [h for h in abas_agora if h not in abas_antes]
            if novas:
                nova_aba = novas[0]
                break
        time.sleep(0.25)

    pct = total = itens = ""

    if rapido:
        # Aguarda a requisição completar no servidor (page load), mas não lê resultado.
        if nova_aba:
            try:
                driver.switch_to.window(nova_aba)
                # Espera document.readyState == "complete" para não matar a requisição.
                WebDriverWait(driver, 10).until(
                    lambda d: d.execute_script("return document.readyState") == "complete"
                )
            except Exception:
                pass
            try:
                driver.close()
            except Exception:
                pass
            driver.switch_to.window(aba_principal)
    else:
        if nova_aba:
            driver.switch_to.window(nova_aba)
            log(f"BB_{carimbo_norm}: auditoria abriu em nova aba.")
        else:
            log(f"BB_{carimbo_norm}: auditoria nao abriu nova aba; seguindo na aba atual.")

        try:
            WebDriverWait(driver, 15).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "span.auditoria.sucesso"))
            )
            pct, total, itens = _capturar_auditoria(driver)
        except Exception:
            warn(f"BB_{carimbo_norm}: timeout aguardando tela de auditoria.")

        if nova_aba:
            try:
                driver.close()
            except Exception:
                pass
            driver.switch_to.window(aba_principal)
            log(f"BB_{carimbo_norm}: retornou da auditoria para a aba principal.")

    log(f"BB_{carimbo_norm}: clicando em Proxima Fatura...")
    clicar_botao_proxima_fatura(driver, wait)
    _aguardar_sem_spinner(driver, timeout=8, min_wait=0.2 if rapido else 0.3)

    return pct, total, itens


def carregar_lista_carimbos(args) -> list[str]:
    itens: list[str] = []
    itens.extend(args.carimbo or [])
    if getattr(args, "carimbos_arquivo", ""):
        path = Path(args.carimbos_arquivo)
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                itens.append(line)
    vistos = set()
    saida = []
    for item in itens:
        c = normalizar_carimbo(item)
        if c not in vistos:
            vistos.add(c)
            saida.append(c)
    return saida
