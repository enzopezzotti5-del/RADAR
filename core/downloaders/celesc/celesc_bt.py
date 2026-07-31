#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Fluxo inicial CELESC - BT
=========================

Primeira versao do downloader CELESC BT, refletindo o fluxo do MT:
1. Acessa a tela de login
2. Preenche usuario e senha
3. Clica em "Entrar"
4. Seleciona o perfil "Para Voce e Seu Negocio"
5. Lista parceiros/CNPJs e suas UCs

O fluxo de navegacao e coleta reaproveita a implementacao consolidada do MT.
"""

from __future__ import annotations

import argparse
import csv
import logging
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path


CORE_ROOT = Path(__file__).resolve().parents[2]
ROOT_LOCAL = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT_LOCAL))
sys.path.insert(0, str(CORE_ROOT))

from downloaders.celesc import celesc_grupo_a as mt


BASE_DIR = Path(__file__).resolve().parent
LOG_DIR = BASE_DIR / "logs"
OUTPUT_DIR = BASE_DIR / "saida"
DOWNLOAD_DIR = BASE_DIR / "downloads_bt"
TEMP_ROOT = BASE_DIR / ".tmp_bt_profiles"
SERVER_DOWNLOAD_DIR = mt.SERVER_DOWNLOAD_DIR
INDEX_LOCAL_PATH = BASE_DIR / "indice_faturas_celesc_bt.csv"
INDEX_SERVER_PATH = SERVER_DOWNLOAD_DIR / "indice_faturas_celesc_bt.csv"
SERVER_HISTORY_CSV_DIR = SERVER_DOWNLOAD_DIR / "_historico_csv"
TENSAO_BT = "BT"

PERFIL_BT_ROTULOS = [
    "Para Voc\u00ea e Seu Neg\u00f3cio",
    "Para voc\u00ea e seu neg\u00f3cio",
    "Para Voce e Seu Negocio",
    "Para voce e seu negocio",
    "Seu Neg\u00f3cio",
    "seu neg\u00f3cio",
    "Seu Negocio",
    "seu negocio",
]

ROTULOS_JA_TENHO_CADASTRO = [
    "J\u00e1 tenho o novo cadastro",
    "Ja tenho o novo cadastro",
]


def _configurar_logging() -> logging.Logger:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    logger = logging.getLogger("celesc_bt")
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()

    fmt_console = logging.Formatter("%(asctime)s  %(message)s", datefmt="%H:%M:%S")
    fmt_file = logging.Formatter("%(asctime)s %(levelname)-8s %(message)s", datefmt="%H:%M:%S")

    h_console = logging.StreamHandler(sys.stdout)
    h_console.setFormatter(fmt_console)
    h_console.setLevel(logging.INFO)

    h_file = logging.FileHandler(LOG_DIR / f"celesc_bt_{ts}.log", encoding="utf-8")
    h_file.setFormatter(fmt_file)
    h_file.setLevel(logging.DEBUG)

    logger.addHandler(h_console)
    logger.addHandler(h_file)
    return logger


log = _configurar_logging()


class IndiceLocalCelescBT(mt.IndiceLocalCelesc):
    def _carregar(self) -> None:
        with self._file_lock():
            if not self.path.exists():
                self._criar_vazio()
                return

            with self.path.open(newline="", encoding="utf-8-sig") as handle:
                for row in csv.DictReader(handle):
                    inst = (row.get("INSTALACAO") or row.get("UC") or "").strip()
                    mes_ref = (row.get("MES_REF") or "").strip()
                    if inst and mes_ref:
                        self.memoria.add((inst, mes_ref))
                    match = re.search(r"(\d+)$", row.get("INDICE", ""))
                    if match:
                        self.proximo = max(self.proximo, int(match.group(1)) + 1)

        log.info("Indice local CELESC BT: %s registros", len(self.memoria))


def _xpath_profile_card_bt() -> str:
    condicoes = [
        f".//*[contains(normalize-space(.), {mt._safe_contains_xpath(rotulo)})]"
        for rotulo in PERFIL_BT_ROTULOS
    ]
    filtro = " or ".join(condicoes)
    return f"(//celesc-profile-card[{filtro}] | //div[contains(@class,'profile-card')][{filtro}])"


def _xpath_botao_bt() -> str:
    condicoes = [
        f".//*[contains(normalize-space(.), {mt._safe_contains_xpath(rotulo)})]"
        for rotulo in PERFIL_BT_ROTULOS
    ]
    filtro_botao = (
        "//button["
        "(contains(@class,'small') or contains(@class,'secondary') or contains(@class,'default')) and "
        "("
        "contains(normalize-space(.), 'Selecionar') or "
        ".//span[contains(normalize-space(.), 'Selecionar')] or "
        ".//span[contains(normalize-space(.), 'arrow_right')]"
        ")]"
    )
    return " | ".join(
        [
            f"//celesc-profile-card[{' or '.join(condicoes)}]{filtro_botao}",
            f"//div[contains(@class,'profile-card')][{' or '.join(condicoes)}]{filtro_botao}",
        ]
    )


def _esta_na_selecao_perfil_bt(driver) -> bool:
    if driver.find_elements(mt.By.XPATH, _xpath_profile_card_bt()):
        return True
    return bool(
        driver.find_elements(
            mt.By.XPATH,
            "//h1[contains(normalize-space(.), 'Deseja acessar a sua conta a partir de qual perfil de usuario') "
            "or contains(normalize-space(.), 'Deseja acessar a sua conta a partir de qual perfil de usuário')]",
        )
    )


def _abrir_menu_usuario_se_necessario(driver) -> None:
    try:
        opcoes = driver.find_elements(mt.By.XPATH, "//button[contains(normalize-space(.), 'Trocar perfil')]")
        if any(item.is_displayed() for item in opcoes):
            return
    except Exception:
        pass

    candidatos = [
        (mt.By.CSS_SELECTOR, ".avatar-wrapper"),
        (mt.By.CSS_SELECTOR, "ui-celesc-avatar"),
        (mt.By.XPATH, "//span[contains(@class, 'avatar-name')]"),
    ]
    for by, locator in candidatos:
        try:
            elementos = driver.find_elements(by, locator)
        except Exception:
            continue
        for elemento in elementos:
            try:
                if not elemento.is_displayed():
                    continue
                mt._clicar_robusto(driver, elemento)
                mt._pausa_humana(0.4, 0.8)
                return
            except Exception:
                continue


def _clicar_botao_perfil_bt(driver) -> bool:
    xpath_btn = _xpath_botao_bt()
    xpath_card = _xpath_profile_card_bt()
    try:
        cartoes = driver.find_elements(mt.By.XPATH, xpath_card)
    except Exception:
        cartoes = []

    for cartao in cartoes:
        try:
            if not cartao.is_displayed():
                continue
            botoes = cartao.find_elements(
                mt.By.XPATH,
                ".//button[contains(normalize-space(.), 'Selecionar') "
                "or .//span[contains(normalize-space(.), 'Selecionar')] "
                "or .//span[contains(normalize-space(.), 'arrow_right')]]",
            )
            for botao in botoes:
                if not botao.is_displayed():
                    continue
                mt._clicar_robusto(driver, botao)
                return True
        except Exception:
            continue

    try:
        botoes = driver.find_elements(mt.By.XPATH, xpath_btn)
    except Exception:
        botoes = []
    for botao in botoes:
        try:
            if not botao.is_displayed():
                continue
            mt._clicar_robusto(driver, botao)
            return True
        except Exception:
            continue
    return False


def _esta_na_home_portal_bt(driver) -> bool:
    texto = mt._texto_pagina_normalizado(driver)
    marcadores = (
        "comece por aqui",
        "insira o seu e-mail para iniciar o acesso",
        "acesso rapido",
        "entenda sua fatura",
    )
    return any(marcador in texto for marcador in marcadores)


def _resumo_estado_bt(driver) -> str:
    try:
        url = driver.current_url or ""
    except Exception:
        url = ""
    try:
        titulo = driver.title or ""
    except Exception:
        titulo = ""
    texto = mt._texto_pagina_normalizado(driver)
    trecho = texto[:240] if texto else ""
    estados = []
    if mt._esta_na_lista_cnpjs(driver):
        estados.append("lista_cnpjs")
    if mt._esta_na_lista_ucs(driver):
        estados.append("lista_ucs")
    if _esta_na_selecao_perfil_bt(driver):
        estados.append("selecao_perfil_bt")
    if _esta_na_home_portal_bt(driver):
        estados.append("home_portal")
    if _tem_sessao_autenticada(driver):
        estados.append("sessao_autenticada")
    if _esta_em_erro_generico(driver):
        estados.append("erro_generico")
    estado = ", ".join(estados) if estados else "estado_desconhecido"
    return f"url={url} | titulo={titulo} | estados={estado} | texto={trecho}"


def _lista_ucs_vazia(driver) -> bool:
    try:
        url = (driver.current_url or "").lower()
    except Exception:
        url = ""
    if "contrato/selecao" not in url:
        return False
    texto = mt._texto_pagina_normalizado(driver)
    return "nenhum resultado encontrado" in texto or "lista vazia" in texto


def _salvar_debug_estado_bt(driver, prefixo: str) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = OUTPUT_DIR / f"{prefixo}_{ts}"
    try:
        base.with_suffix(".html").write_text(driver.page_source, encoding="utf-8")
    except Exception:
        pass
    try:
        base.with_suffix(".txt").write_text(_resumo_estado_bt(driver), encoding="utf-8")
    except Exception:
        pass


def fechar_modal_boas_vindas(driver) -> None:
    candidatos: list[tuple[str, str]] = []
    for rotulo in ROTULOS_JA_TENHO_CADASTRO:
        candidatos.extend(
            [
                (mt.By.XPATH, f"//span[contains(normalize-space(.), {mt._safe_contains_xpath(rotulo)})]"),
                (
                    mt.By.XPATH,
                    f"//button[.//span[contains(normalize-space(.), {mt._safe_contains_xpath(rotulo)})]]",
                ),
                (mt.By.XPATH, f"//*[contains(normalize-space(.), {mt._safe_contains_xpath(rotulo)})]"),
            ]
        )

    for by, locator in candidatos:
        try:
            elementos = driver.find_elements(by, locator)
            if not elementos:
                continue
            elemento = elementos[0]
            if elemento.is_displayed():
                mt._clicar_robusto(driver, elemento)
                log.info("Modal de boas-vindas fechado.")
                mt._pausa_humana(0.8, 1.5)
                return
        except Exception:
            continue


def _esta_em_erro_generico(driver) -> bool:
    try:
        texto = mt._normalizar_texto(driver.find_element(mt.By.TAG_NAME, "body").text)
    except Exception:
        return False
    marcadores = (
        "ocorreu um erro inesperado",
        "tente novamente mais tarde",
        "servico indisponivel",
        "nao foi possivel validar seu parceiro celesc",
    )
    return any(marcador in texto for marcador in marcadores)


def _fechar_modal_servico_indisponivel(driver) -> bool:
    if not _esta_em_erro_generico(driver):
        return False

    candidatos = [
        (mt.By.XPATH, "//button[contains(@class,'small') and contains(@class,'default')][.//span[contains(normalize-space(.), 'Ok')]]"),
        (mt.By.XPATH, "//button[.//span[contains(normalize-space(.), 'Ok')]]"),
        (mt.By.XPATH, "//button[contains(normalize-space(.), 'Ok')]"),
        (mt.By.XPATH, "//button[.//span[contains(normalize-space(.), 'Cancelar')]]"),
    ]
    for by, locator in candidatos:
        try:
            botao = mt.WebDriverWait(driver, 6).until(mt.EC.element_to_be_clickable((by, locator)))
            mt._clicar_robusto(driver, botao)
            log.warning("Modal de servico indisponivel detectado; tentativa de fechamento executada.")
            mt._pausa_humana(0.8, 1.5)
            return True
        except Exception:
            continue
    return False


def _resolver_modais_pos_login(driver) -> None:
    fechar_modal_boas_vindas(driver)
    _fechar_modal_servico_indisponivel(driver)


def _tem_sessao_autenticada(driver) -> bool:
    candidatos = [
        (mt.By.XPATH, "//button[contains(normalize-space(.), 'Trocar perfil')]"),
        (mt.By.XPATH, "//li/button[contains(normalize-space(.), 'Trocar perfil')]"),
        (mt.By.CSS_SELECTOR, ".avatar-wrapper .avatar-name"),
    ]
    for by, locator in candidatos:
        try:
            if driver.find_elements(by, locator):
                return True
        except Exception:
            continue
    return False


def _forcar_tela_selecao_acesso(driver) -> None:
    def _esperar_destino() -> None:
        mt.WebDriverWait(driver, mt.TIMEOUT_PADRAO + 20).until(
            lambda d: _esta_na_selecao_perfil_bt(d) or mt._esta_na_lista_cnpjs(d) or _esta_em_erro_generico(d)
        )
        mt._aguardar_dom_estavel(driver, timeout=10)
        _resolver_modais_pos_login(driver)

    candidatos_trocar = [
        (mt.By.XPATH, "//button[contains(normalize-space(.), 'Trocar perfil')]"),
        (mt.By.XPATH, "//li/button[contains(normalize-space(.), 'Trocar perfil')]"),
    ]
    _abrir_menu_usuario_se_necessario(driver)
    for by, locator in candidatos_trocar:
        try:
            botao = mt.WebDriverWait(driver, 6).until(mt.EC.element_to_be_clickable((by, locator)))
            mt._clicar_robusto(driver, botao)
            log.info("Trocar perfil acionado a partir da sessao autenticada.")
            _esperar_destino()
            return
        except Exception:
            continue

    for nome_fluxo, url in [
        ("selecao de acesso", "https://conecte.celesc.com.br/autenticacao/selecao-acesso"),
        ("portal raiz", mt.URL_PORTAL),
    ]:
        try:
            log.info("Sessao autenticada sem card de perfil; abrindo %s...", nome_fluxo)
            driver.get(url)
            _esperar_destino()
            return
        except Exception as exc:
            log.warning("Falha ao abrir %s a partir da sessao autenticada: %s", nome_fluxo, exc)

    dump_path = OUTPUT_DIR / f"celesc_bt_sessao_autenticada_sem_perfil_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
    dump_path.parent.mkdir(parents=True, exist_ok=True)
    dump_path.write_text(driver.page_source, encoding="utf-8")
    raise mt.TimeoutException(f"Sessao autenticada, mas sem card de perfil BT. HTML salvo em {dump_path}.")


def selecionar_perfil_bt(driver) -> None:
    log.info("Aguardando tela de selecao do perfil...")

    _resolver_modais_pos_login(driver)

    xpath_profile_card = _xpath_profile_card_bt()
    xpath_btn = _xpath_botao_bt()
    xpath_titulo_perfil = (
        "//h1[contains(normalize-space(.), 'Deseja acessar a sua conta a partir de qual perfil de usuario') "
        "or contains(normalize-space(.), 'Deseja acessar a sua conta a partir de qual perfil de usuário')]"
    )

    try:
        mt.WebDriverWait(driver, mt.TIMEOUT_PADRAO + 15).until(
            lambda d: bool(d.find_elements(mt.By.CSS_SELECTOR, ".pn-details-wrapper"))
            or bool(d.find_elements(mt.By.XPATH, xpath_profile_card))
            or bool(d.find_elements(mt.By.XPATH, xpath_btn))
            or bool(d.find_elements(mt.By.XPATH, xpath_titulo_perfil))
            or _esta_em_erro_generico(d)
        )
    except mt.TimeoutException:
        pass

    mt._pausa_humana(0.5, 1.0)
    _resolver_modais_pos_login(driver)

    if driver.find_elements(mt.By.XPATH, xpath_profile_card) or driver.find_elements(mt.By.XPATH, xpath_titulo_perfil):
        log.info("Tela de selecao de perfil detectada; tentando clicar no BT.")

    if not _esta_na_selecao_perfil_bt(driver) and not mt._esta_na_lista_cnpjs(driver) and _tem_sessao_autenticada(driver):
        _forcar_tela_selecao_acesso(driver)

    if mt._esta_na_lista_cnpjs(driver):
        log.info("Perfil BT ja estava ativo.")
        return

    if _esta_em_erro_generico(driver):
        if not _fechar_modal_servico_indisponivel(driver):
            dump_path = OUTPUT_DIR / f"celesc_bt_escolha_perfil_falha_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
            dump_path.parent.mkdir(parents=True, exist_ok=True)
            dump_path.write_text(driver.page_source, encoding="utf-8")
            raise mt.TimeoutException("Portal CELESC permaneceu na tela generica de erro apos o login.")
        mt._pausa_humana(1.0, 2.0)

    ultimo_erro = None
    for _ in range(3):
        _resolver_modais_pos_login(driver)
        if not _esta_na_selecao_perfil_bt(driver) and not mt._esta_na_lista_cnpjs(driver) and _tem_sessao_autenticada(driver):
            _forcar_tela_selecao_acesso(driver)
        if mt._esta_na_lista_cnpjs(driver):
            log.info("Perfil BT ja estava ativo.")
            return
        try:
            mt.WebDriverWait(driver, 15).until(
                lambda d: bool(d.find_elements(mt.By.XPATH, xpath_btn)) or bool(d.find_elements(mt.By.XPATH, xpath_profile_card))
            )
            if not _clicar_botao_perfil_bt(driver):
                raise mt.TimeoutException("Botao do perfil BT nao ficou clicavel.")
            log.info("Perfil 'Para Voce e Seu Negocio' selecionado.")
            return
        except Exception as exc:
            ultimo_erro = exc
            mt._pausa_humana(1.0, 2.0)

    dump_path = OUTPUT_DIR / f"celesc_bt_escolha_perfil_falha_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
    dump_path.parent.mkdir(parents=True, exist_ok=True)
    dump_path.write_text(driver.page_source, encoding="utf-8")
    raise mt.TimeoutException(
        f"Selecao do perfil 'Para Voce e Seu Negocio' nao encontrada. HTML salvo em {dump_path}. "
        f"Ultimo erro: {ultimo_erro}"
    )


def _garantir_lista_cnpjs_via_portal_raiz_bt(driver) -> None:
    log.info("Reabrindo portal para retornar ao nivel de CNPJs...")
    driver.get(mt.URL_PORTAL)
    mt.WebDriverWait(driver, mt.TIMEOUT_PADRAO + 20).until(
        lambda d: _esta_na_selecao_perfil_bt(d) or mt._esta_na_lista_cnpjs(d)
    )
    mt._aguardar_dom_estavel(driver, timeout=10)

    if mt._esta_na_lista_cnpjs(driver):
        log.info("Lista de CNPJs reaberta via portal raiz.")
        mt._pausa_humana(0.5, 1.0)
        return

    selecionar_perfil_bt(driver)
    mt.aguardar_lista_cnpjs(driver)
    mt._pausa_humana(1.0, 2.0)
    log.info("Retorno para lista de CNPJs concluido via BT.")


def _garantir_lista_cnpjs_bt(driver) -> None:
    if mt._esta_na_lista_cnpjs(driver):
        driver._celesc_cnpjs_url = driver.current_url  # type: ignore[attr-defined]
        mt._aguardar_dom_estavel(driver, timeout=8)
        return

    if mt._esta_na_lista_ucs(driver) or _lista_ucs_vazia(driver):
        url_cnpjs = getattr(driver, "_celesc_cnpjs_url", "")
        if url_cnpjs:
            try:
                log.info("Retornando para a URL conhecida da lista de CNPJs...")
                driver.get(url_cnpjs)
                mt.aguardar_lista_cnpjs(driver)
                mt._pausa_humana(0.8, 1.4)
                log.info("Lista de CNPJs recuperada mantendo o perfil BT.")
                return
            except Exception as exc:
                log.warning("Falha ao retornar para a URL conhecida dos CNPJs: %s", exc)

        try:
            botao_voltar = mt.WebDriverWait(driver, 8).until(
                mt.EC.element_to_be_clickable(
                    (
                        mt.By.XPATH,
                        "//ui-celesc-button[.//span[normalize-space(.)='arrow_back']]//button",
                    )
                )
            )
            mt._clicar_robusto(driver, botao_voltar)
            mt.aguardar_lista_cnpjs(driver)
            mt._pausa_humana(0.8, 1.4)
            log.info("Lista de CNPJs recuperada pelo botao nativo de voltar.")
            return
        except Exception as exc:
            log.warning("Falha ao voltar da lista de UCs para os CNPJs: %s", exc)

    if _esta_na_selecao_perfil_bt(driver):
        selecionar_perfil_bt(driver)
        mt.aguardar_lista_cnpjs(driver)
        mt._pausa_humana(0.8, 1.4)
        return

    if _tem_sessao_autenticada(driver):
        # Primeiro tenta recuperar o nivel de CNPJs mantendo o perfil BT
        # corrente. A versao anterior clicava imediatamente em "Trocar
        # perfil", embora a sessao ainda estivesse autenticada.
        try:
            log.info("Sessao autenticada; tentando reabrir a lista de CNPJs sem trocar perfil...")
            driver.get(mt.URL_PORTAL)
            mt.WebDriverWait(driver, mt.TIMEOUT_PADRAO + 10).until(
                lambda d: mt._esta_na_lista_cnpjs(d)
                or _esta_na_selecao_perfil_bt(d)
                or _esta_em_erro_generico(d)
            )
            if mt._esta_na_lista_cnpjs(driver):
                mt.aguardar_lista_cnpjs(driver)
                mt._pausa_humana(0.8, 1.4)
                log.info("Lista de CNPJs recuperada sem trocar perfil.")
                return
        except Exception as exc:
            log.warning("Falha ao reabrir a lista de CNPJs mantendo o perfil BT: %s", exc)

        # Mantem a troca de perfil somente como ultimo recurso, para os casos
        # em que o portal realmente perdeu o contexto do perfil selecionado.
        try:
            _forcar_tela_selecao_acesso(driver)
            if _esta_na_selecao_perfil_bt(driver):
                selecionar_perfil_bt(driver)
            mt.aguardar_lista_cnpjs(driver)
            mt._pausa_humana(0.8, 1.4)
            return
        except Exception as exc:
            log.warning("Falha ao reaproveitar sessao autenticada para voltar aos CNPJs: %s", exc)

    if _esta_na_home_portal_bt(driver):
        try:
            log.info("Portal caiu na tela inicial; reabrindo selecao de acesso do BT...")
            driver.get("https://conecte.celesc.com.br/autenticacao/selecao-acesso")
            mt.WebDriverWait(driver, mt.TIMEOUT_PADRAO + 10).until(
                lambda d: _esta_na_selecao_perfil_bt(d) or mt._esta_na_lista_cnpjs(d)
            )
            if _esta_na_selecao_perfil_bt(driver):
                selecionar_perfil_bt(driver)
            mt.aguardar_lista_cnpjs(driver)
            mt._pausa_humana(0.8, 1.4)
            return
        except Exception as exc:
            log.warning("Falha ao sair da tela inicial do portal para a lista de CNPJs: %s", exc)

    _garantir_lista_cnpjs_via_portal_raiz_bt(driver)


def _trocar_perfil_e_selecionar_bt(driver) -> None:
    log.info("Retornando para a lista de CNPJs...")

    if mt._esta_na_lista_cnpjs(driver):
        log.info("Ja na lista de CNPJs - estado limpo.")
        mt._pausa_humana(0.5, 1.0)
        return

    if not mt._esta_na_lista_ucs(driver) and not _lista_ucs_vazia(driver):
        try:
            if mt._clicar_trocar_imovel_contexto(driver):
                mt._pausa_humana(0.8, 1.5)
        except Exception as exc:
            log.warning("Falha ao tentar sair para a lista de UCs via 'Trocar imovel': %s", exc)

    # Recupera mantendo o perfil atual; _garantir_lista_cnpjs_bt deixa a
    # reselecao de perfil apenas como fallback.
    _garantir_lista_cnpjs_bt(driver)


def abrir_cnpj_e_aguardar_ucs_bt(driver, cnpj: str, tentativas: int = 3) -> None:
    ultimo_erro = None
    xpath = (
        "//div[contains(@class, 'pn-details-wrapper')]"
        f"[.//*[contains(normalize-space(.), '{cnpj}')]]"
        "//button[contains(@class, 'small') and contains(@class, 'default')]"
    )

    for tentativa in range(1, tentativas + 1):
        try:
            _garantir_lista_cnpjs_bt(driver)
            mt.aguardar_lista_cnpjs(driver)
            mt._aguardar_dom_estavel(driver, timeout=12)
            mt._pausa_humana(1.0, 1.8)

            botao = mt.WebDriverWait(driver, mt.TIMEOUT_PADRAO).until(
                mt.EC.element_to_be_clickable((mt.By.XPATH, xpath))
            )
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", botao)
            mt._pausa_humana(0.3, 0.7)
            mt._clicar_robusto(driver, botao)
            log.info("CNPJ selecionado: %s", cnpj)

            # Alguns CNPJs entram diretamente na area privada com uma UC ja
            # ativa, em vez de abrir a lista. Nesse caso, usa o proprio link
            # "Trocar imovel" para chegar ao nivel de UCs esperado pelo fluxo.
            mt.WebDriverWait(driver, mt.TIMEOUT_PADRAO + 5).until(
                lambda d: mt._esta_na_lista_ucs(d)
                or _lista_ucs_vazia(d)
                or "area-privada" in (d.current_url or "").lower()
                or _esta_na_selecao_perfil_bt(d)
                or _esta_na_home_portal_bt(d)
                or _esta_em_erro_generico(d)
            )
            if "area-privada" in (driver.current_url or "").lower():
                log.info("CNPJ abriu uma UC diretamente; solicitando 'Trocar imovel' para listar as UCs...")
                if not mt._clicar_trocar_imovel_contexto(driver):
                    raise mt.TimeoutException(
                        f"Area privada aberta, mas nao foi possivel acessar a lista de UCs. {_resumo_estado_bt(driver)}"
                    )
            elif _lista_ucs_vazia(driver):
                log.info("Lista de UCs carregada sem resultados para o CNPJ %s.", cnpj)
                return
            elif not mt._esta_na_lista_ucs(driver):
                raise mt.TimeoutException(
                    f"Apos clicar no CNPJ {cnpj}, o portal foi para um estado inesperado. {_resumo_estado_bt(driver)}"
                )

            mt.aguardar_lista_ucs(driver)
            mt._pausa_humana(1.0, 2.0)
            return
        except Exception as exc:
            ultimo_erro = exc
            log.warning(
                "Falha ao abrir o CNPJ %s e carregar a lista de UCs (%s/%s): %s",
                cnpj,
                tentativa,
                tentativas,
                exc,
            )
            try:
                dump_path = OUTPUT_DIR / f"celesc_lista_ucs_falha_{mt._slug(cnpj)}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
                dump_path.write_text(driver.page_source, encoding="utf-8")
                log.warning("HTML de falha ao abrir CNPJ salvo em: %s", dump_path)
                resumo_path = dump_path.with_suffix(".txt")
                resumo_path.write_text(_resumo_estado_bt(driver), encoding="utf-8")
                log.warning("Resumo de estado da falha salvo em: %s", resumo_path)
            except Exception:
                pass

            if tentativa < tentativas:
                try:
                    _garantir_lista_cnpjs_bt(driver)
                except Exception as exc_nav:
                    log.warning("Falha ao restaurar a lista de CNPJs antes da nova tentativa: %s", exc_nav)
                mt._pausa_humana(1.5, 2.5)

    raise mt.TimeoutException(
        f"Nao foi possivel abrir o CNPJ {cnpj} e carregar a lista de UCs apos {tentativas} tentativa(s)."
    ) from ultimo_erro


def salvar_html(driver) -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_DIR / f"celesc_bt_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
    path.write_text(driver.page_source, encoding="utf-8")
    return path


def salvar_cnpjs_csv(cnpjs: list[dict[str, str]]) -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_DIR / f"celesc_cnpjs_bt_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=["parceiro_nome", "cnpj", "codigo_parceiro", "texto_normalizado"])
        writer.writeheader()
        writer.writerows(cnpjs)
    return path


def salvar_ucs_csv(linhas: list[dict[str, str]]) -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_DIR / f"celesc_ucs_bt_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "parceiro_nome",
                "cnpj",
                "codigo_parceiro",
                "uc",
                "endereco",
                "texto_normalizado",
            ],
        )
        writer.writeheader()
        writer.writerows(linhas)
    return path


def salvar_faturas_csv(linhas: list[dict[str, str]]) -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_DIR / f"celesc_faturas_bt_2026_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "parceiro_nome",
                "cnpj",
                "codigo_parceiro",
                "uc",
                "endereco",
                "indice",
                "ordem",
                "mes_ref",
                "mes_codigo",
                "mes_ref_competencia",
                "mes_exibicao",
                "ano_ref",
                "vencimento",
                "ano_venc",
                "valor",
                "status",
                "acao",
                "acao_normalizada",
                "texto_normalizado",
                "arquivo_pdf",
                "download_status",
            ],
        )
        writer.writeheader()
        writer.writerows(linhas)
    return path


def _copiar_arquivo_para_servidor(path: Path) -> None:
    SERVER_DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    SERVER_HISTORY_CSV_DIR.mkdir(parents=True, exist_ok=True)

    nome = path.name
    destino = SERVER_DOWNLOAD_DIR / nome
    destino_extra: Path | None = None

    if re.fullmatch(r"celesc_cnpjs_bt_\d{8}_\d{6}\.csv", nome):
        destino = SERVER_HISTORY_CSV_DIR / nome
        destino_extra = SERVER_DOWNLOAD_DIR / "celesc_cnpjs_bt_atual.csv"
    elif re.fullmatch(r"celesc_ucs_bt_\d{8}_\d{6}\.csv", nome):
        destino = SERVER_HISTORY_CSV_DIR / nome
        destino_extra = SERVER_DOWNLOAD_DIR / "celesc_ucs_bt_atual.csv"
    elif re.fullmatch(r"celesc_faturas_bt_2026_\d{8}_\d{6}\.csv", nome):
        destino = SERVER_HISTORY_CSV_DIR / nome
        destino_extra = SERVER_DOWNLOAD_DIR / "celesc_faturas_bt_2026_atual.csv"

    try:
        shutil.copy2(path, destino)
        log.info("Arquivo copiado para servidor: %s", destino)
        if destino_extra is not None:
            shutil.copy2(path, destino_extra)
            log.info("Snapshot atual atualizado: %s", destino_extra)
    except Exception as exc:
        log.warning("Falha ao copiar arquivo para servidor (%s): %s", destino, exc)


mt.LOG_DIR = LOG_DIR
mt.OUTPUT_DIR = OUTPUT_DIR
mt.DOWNLOAD_DIR = DOWNLOAD_DIR
mt.TEMP_ROOT = TEMP_ROOT
mt.log = log
mt.TENSAO_GRUPO_A = TENSAO_BT
mt.INDEX_LOCAL_PATH = INDEX_LOCAL_PATH
mt.INDEX_SERVER_PATH = INDEX_SERVER_PATH
mt.SERVER_HISTORY_CSV_DIR = SERVER_HISTORY_CSV_DIR

mt.IndiceLocalCelesc = IndiceLocalCelescBT
mt.IndiceLocalCelesc.__init__.__defaults__ = (INDEX_LOCAL_PATH,)
mt._renomear_e_copiar_pdf.__defaults__ = (TENSAO_BT,)
mt._pasta_pdf_servidor.__defaults__ = (TENSAO_BT,)

mt.fechar_modal_boas_vindas = fechar_modal_boas_vindas
mt._esta_em_erro_generico = _esta_em_erro_generico
mt._esta_na_selecao_perfil = _esta_na_selecao_perfil_bt
mt.selecionar_grupo_a = selecionar_perfil_bt
mt._garantir_lista_cnpjs_via_portal_raiz = _garantir_lista_cnpjs_via_portal_raiz_bt
mt._trocar_perfil_e_selecionar_grupo_a = _trocar_perfil_e_selecionar_bt
mt.abrir_cnpj_e_aguardar_ucs = abrir_cnpj_e_aguardar_ucs_bt

mt.salvar_html = salvar_html
mt.salvar_cnpjs_csv = salvar_cnpjs_csv
mt.salvar_ucs_csv = salvar_ucs_csv
mt.salvar_faturas_csv = salvar_faturas_csv
mt._copiar_arquivo_para_servidor = _copiar_arquivo_para_servidor


def executar(
    usuario: str,
    senha: str,
    headless: bool,
    limite_cnpjs: int | None = None,
    limite_ucs: int | None = None,
    baixar_faturas: bool = False,
    limite_faturas: int | None = None,
    cnpjs_alvo: set[str] | None = None,
    ucs_alvo: set[str] | None = None,
    meses_ref_alvo: set[str] | None = None,
    ignorar_ja_baixado: bool = False,
) -> int:
    return mt.executar(
        usuario,
        senha,
        headless,
        limite_cnpjs=limite_cnpjs,
        limite_ucs=limite_ucs,
        baixar_faturas=baixar_faturas,
        limite_faturas=limite_faturas,
        cnpjs_alvo=cnpjs_alvo,
        ucs_alvo=ucs_alvo,
        meses_ref_alvo=meses_ref_alvo,
        ignorar_ja_baixado=ignorar_ja_baixado,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fluxo inicial CELESC - BT")
    parser.add_argument("--usuario", default=mt.USUARIO_PADRAO)
    parser.add_argument("--senha", default=mt.SENHA_PADRAO)
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--limite-cnpjs", type=int, default=None)
    parser.add_argument("--limite-ucs", type=int, default=None)
    parser.add_argument("--baixar-faturas-2026", action="store_true")
    parser.add_argument("--limite-faturas", type=int, default=None)
    parser.add_argument("--cnpjs-alvo", default="", help="Lista de CNPJs separadas por virgula.")
    parser.add_argument("--ucs-alvo", default="", help="Lista de UCs separadas por virgula.")
    parser.add_argument("--meses-ref", default="", help="Lista de referencias MM-AAAA separadas por virgula.")
    parser.add_argument("--ignorar-ja-baixado", action="store_true",
                        help="Baixa novamente mesmo que a UC/ref ja exista no master/indice local.")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    cnpjs_alvo = {item.strip() for item in str(args.cnpjs_alvo or "").split(",") if item.strip()} or None
    ucs_alvo = {item.strip() for item in str(args.ucs_alvo or "").split(",") if item.strip()} or None
    meses_ref_alvo = {item.strip() for item in str(args.meses_ref or "").split(",") if item.strip()} or None
    raise SystemExit(
        executar(
            args.usuario,
            args.senha,
            args.headless,
            limite_cnpjs=args.limite_cnpjs,
            limite_ucs=args.limite_ucs,
            baixar_faturas=args.baixar_faturas_2026,
            limite_faturas=args.limite_faturas,
            cnpjs_alvo=cnpjs_alvo,
            ucs_alvo=ucs_alvo,
            meses_ref_alvo=meses_ref_alvo,
            ignorar_ja_baixado=args.ignorar_ja_baixado,
        )
    )
