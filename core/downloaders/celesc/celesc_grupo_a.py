#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Fluxo inicial CELESC - Grupo A
==============================

MVP do downloader CELESC:
1. Acessa a tela de login
2. Preenche usuario e senha
3. Clica em "Entrar"
4. Seleciona "Grupo A"
5. Usa BeautifulSoup para identificar os CNPJs exibidos na tela

Neste primeiro momento o script nao baixa faturas nem grava indice master.
Ele serve para validar o fluxo de autenticacao e a leitura da lista de CNPJs.
"""

from __future__ import annotations

import argparse
import csv
import logging
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unicodedata
from datetime import datetime
from pathlib import Path

from bs4 import BeautifulSoup
try:
    from filelock import FileLock
except ImportError:
    FileLock = None
from selenium import webdriver
from selenium.common.exceptions import (
    InvalidSessionIdException,
    SessionNotCreatedException,
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait


CORE_ROOT = Path(__file__).resolve().parents[2]
ROOT_LOCAL = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT_LOCAL))
sys.path.insert(0, str(CORE_ROOT))
import _venv_check  # noqa
from indice_master import MasterIndice


URL_PORTAL = "https://conecte.celesc.com.br/"
URL_LOGIN = "https://conecte.celesc.com.br/autenticacao/login"
USUARIO_PADRAO = "bbenergia@acaoengenharia.com.br"
SENHA_PADRAO = "Ac@o*2025!"

BASE_DIR = Path(__file__).resolve().parent
LOG_DIR = BASE_DIR / "logs"
OUTPUT_DIR = BASE_DIR / "saida"
DOWNLOAD_DIR = BASE_DIR / "downloads"
TEMP_ROOT = Path(tempfile.gettempdir()) / "energia_celesc_tmp"
SERVER_DOWNLOAD_DIR = Path(r"\\10.10.250.21\Energia\ARQUIVOS ENZO\DOWNLOAD CELESC")
INDEX_LOCAL_PATH = BASE_DIR / "indice_faturas_celesc.csv"
INDEX_SERVER_PATH = SERVER_DOWNLOAD_DIR / "indice_faturas_celesc.csv"
SERVER_HISTORY_CSV_DIR = SERVER_DOWNLOAD_DIR / "_historico_csv"
TIMEOUT_PADRAO = 30
TENSAO_GRUPO_A = "MT"
INDEX_FIELDS = [
    "INDICE",
    "INSTALACAO",
    "UC",
    "ENDERECO",
    "MES_REF",
    "MES_EXIBICAO",
    "VENCIMENTO",
    "VALOR",
    "STATUS_CONTA",
    "ACAO_PORTAL",
    "CNPJ",
    "CODIGO_PARCEIRO",
    "PARCEIRO_NOME",
    "DATA_DOWNLOAD",
    "ARQUIVO",
]
class _SessaoMortaError(Exception):
    """Sinaliza que a sessao Chrome morreu no meio do processamento e precisa ser recriada.
    Carrega os resultados parciais coletados ate o momento e o indice do CNPJ onde falhou."""

    def __init__(self, indice_cnpj: int, resultado: list, resultado_faturas: list):
        super().__init__(f"Sessao morta no CNPJ indice {indice_cnpj}")
        self.indice_cnpj = indice_cnpj
        self.resultado = resultado
        self.resultado_faturas = resultado_faturas


MESES_PT = {
    "janeiro": "01",
    "fevereiro": "02",
    "marco": "03",
    "abril": "04",
    "maio": "05",
    "junho": "06",
    "julho": "07",
    "agosto": "08",
    "setembro": "09",
    "outubro": "10",
    "novembro": "11",
    "dezembro": "12",
}


def _pausa_humana(minimo: float = 0.5, maximo: float = 1.5) -> None:
    """Pausa aleatoria entre acoes para simular comportamento humano."""
    time.sleep(random.uniform(minimo, maximo))


def _configurar_logging() -> logging.Logger:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    logger = logging.getLogger("celesc_grupo_a")
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()

    fmt_console = logging.Formatter("%(asctime)s  %(message)s", datefmt="%H:%M:%S")
    fmt_file = logging.Formatter("%(asctime)s %(levelname)-8s %(message)s", datefmt="%H:%M:%S")

    h_console = logging.StreamHandler(sys.stdout)
    h_console.setFormatter(fmt_console)
    h_console.setLevel(logging.INFO)

    h_file = logging.FileHandler(LOG_DIR / f"celesc_grupo_a_{ts}.log", encoding="utf-8")
    h_file.setFormatter(fmt_file)
    h_file.setLevel(logging.DEBUG)

    logger.addHandler(h_console)
    logger.addHandler(h_file)
    return logger


log = _configurar_logging()


class IndiceLocalCelesc:
    def __init__(self, path: Path = INDEX_LOCAL_PATH):
        self.path = path
        self.lock_path = self.path.with_suffix(f"{self.path.suffix}.lock")
        self.memoria: set[tuple[str, str]] = set()
        self.proximo: int = 2_000_000
        self._carregar()

    def _file_lock(self):
        if FileLock is None:
            return _NullLockLocal()
        return FileLock(str(self.lock_path), timeout=30)

    def _criar_vazio(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w", newline="", encoding="utf-8-sig") as handle:
            csv.writer(handle).writerow(INDEX_FIELDS)

    def _carregar(self) -> None:
        with self._file_lock():
            if not self.path.exists():
                self._criar_vazio()
                log.info("Indice local CELESC criado: %s", self.path)
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

        log.info("Indice local CELESC: %s registros | proximo local=%s", len(self.memoria), self.proximo)

    def ja_baixado(self, instalacao: str, mes_ref: str) -> bool:
        return (instalacao.strip(), mes_ref.strip()) in self.memoria

    def gravar(
        self,
        indice_bb: str,
        instalacao: str,
        endereco: str,
        mes_ref: str,
        mes_exibicao: str,
        vencimento: str,
        valor: str,
        status_conta: str,
        acao_portal: str,
        cnpj: str,
        codigo_parceiro: str,
        parceiro_nome: str,
        arquivo: str,
    ) -> None:
        linha = {
            "INDICE": indice_bb,
            "INSTALACAO": instalacao,
            "UC": instalacao,
            "ENDERECO": endereco,
            "MES_REF": mes_ref,
            "MES_EXIBICAO": mes_exibicao,
            "VENCIMENTO": vencimento,
            "VALOR": valor,
            "STATUS_CONTA": status_conta,
            "ACAO_PORTAL": acao_portal,
            "CNPJ": cnpj,
            "CODIGO_PARCEIRO": codigo_parceiro,
            "PARCEIRO_NOME": parceiro_nome,
            "DATA_DOWNLOAD": datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
            "ARQUIVO": arquivo,
        }
        with self._file_lock():
            with self.path.open("a", newline="", encoding="utf-8-sig") as handle:
                writer = csv.DictWriter(handle, fieldnames=INDEX_FIELDS)
                writer.writerow(linha)
        self.memoria.add((instalacao.strip(), mes_ref.strip()))
        match = re.search(r"(\d+)$", indice_bb)
        if match:
            self.proximo = max(self.proximo, int(match.group(1)) + 1)
        try:
            SERVER_DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
            shutil.copy2(self.path, INDEX_SERVER_PATH)
        except Exception as exc:
            log.warning("Falha ao sincronizar indice CELESC no servidor: %s", exc)


class _NullLockLocal:
    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None


def carregar_master() -> MasterIndice | None:
    try:
        master = MasterIndice(scan_individual_indexes=False)
        log.info("Master carregado | proximo carimbo %s", master.proximo_carimbo)
        return master
    except Exception as exc:
        log.warning("Falha ao carregar indice_master.py: %s", exc)
        return None


def _normalizar_texto(valor: str) -> str:
    texto = "" if valor is None else str(valor)
    texto = unicodedata.normalize("NFKD", texto)
    texto = texto.encode("ASCII", "ignore").decode("ASCII")
    return " ".join(texto.split()).strip().lower()


def _texto_limpo(valor: str | None) -> str:
    return " ".join((valor or "").split()).strip()


def _slug(valor: str) -> str:
    texto = _normalizar_texto(valor)
    texto = re.sub(r"[^a-z0-9]+", "_", texto).strip("_")
    return texto or "sem_valor"


def _safe_contains_xpath(valor: str) -> str:
    if "'" not in valor:
        return f"'{valor}'"
    partes = valor.split("'")
    return "concat(" + ", \"'\", ".join(f"'{parte}'" for parte in partes) + ")"


def _find_cached_chromedriver() -> str | None:
    cache = Path.home() / ".cache" / "selenium" / "chromedriver" / "win64"
    if not cache.exists():
        return None
    try:
        result = subprocess.run(
            [
                "powershell",
                "-c",
                "(gi 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe').VersionInfo.ProductVersion",
            ],
            capture_output=True,
            text=True,
            timeout=5,
            creationflags=0x08000000,
        )
        chrome_major = result.stdout.strip().split(".")[0]
    except Exception:
        chrome_major = None

    for path in sorted(cache.iterdir(), reverse=True):
        exe = path / "chromedriver.exe"
        if exe.exists() and (not chrome_major or path.name.startswith(chrome_major + ".")):
            return str(exe)
    return None


def _limpar_perfis_temporarios_antigos(max_idade_horas: int = 12) -> None:
    if not TEMP_ROOT.exists():
        return
    limite = time.time() - (max_idade_horas * 3600)
    for path in TEMP_ROOT.glob("chrome_profile_*"):
        try:
            if path.is_dir() and path.stat().st_mtime < limite:
                shutil.rmtree(path, ignore_errors=True)
        except Exception:
            continue


def _encerrar_driver(driver: webdriver.Chrome | None) -> None:
    if driver is None:
        return
    try:
        driver.quit()
    except Exception:
        pass
    perfil_dir = getattr(driver, "_celesc_profile_dir", None)
    if perfil_dir:
        try:
            shutil.rmtree(perfil_dir, ignore_errors=True)
        except Exception:
            pass


def _deve_recriar_sessao(exc: Exception) -> bool:
    if isinstance(exc, KeyboardInterrupt):
        return False
    if isinstance(exc, (ConnectionResetError, BrokenPipeError, InvalidSessionIdException, SessionNotCreatedException)):
        return True
    if isinstance(exc, WebDriverException):
        texto = _normalizar_texto(str(exc))
        marcadores = (
            "disconnected",
            "session deleted",
            "invalid session id",
            "target frame detached",
            "chrome not reachable",
            "unable to receive message from renderer",
            "timed out receiving message from renderer",
            "net err_connection_reset",
        )
        if any(marcador in texto for marcador in marcadores):
            return True
    texto = _normalizar_texto(repr(exc))
    return "winerror 10054" in texto or "connection reset" in texto


def _deve_reiniciar_fluxo_portal(driver: webdriver.Chrome | None, exc: Exception) -> bool:
    if _deve_recriar_sessao(exc):
        return True
    if isinstance(exc, TimeoutException) and driver is not None and _esta_em_erro_generico(driver):
        return True
    texto = _normalizar_texto(str(exc))
    return "selecao de grupo a nao encontrada" in texto and driver is not None and _esta_em_erro_generico(driver)


def build_driver(headless: bool = False, download_dir: Path | None = None) -> webdriver.Chrome:
    TEMP_ROOT.mkdir(parents=True, exist_ok=True)
    _limpar_perfis_temporarios_antigos()
    download_dir = download_dir or DOWNLOAD_DIR
    download_dir.mkdir(parents=True, exist_ok=True)
    perfil_dir = Path(tempfile.mkdtemp(prefix="chrome_profile_", dir=str(TEMP_ROOT)))

    options = Options()
    options.add_argument(f"--user-data-dir={perfil_dir.resolve()}")
    options.add_argument("--window-size=1440,960")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-notifications")
    options.add_argument("--disable-popup-blocking")
    options.add_argument("--disable-background-timer-throttling")
    options.add_argument("--disable-backgrounding-occluded-windows")
    options.add_argument("--disable-renderer-backgrounding")
    options.add_argument("--no-first-run")
    options.add_argument("--no-default-browser-check")
    options.add_argument("--no-sandbox")
    options.add_argument("--remote-debugging-port=0")
    options.add_experimental_option(
        "prefs",
        {
            "download.default_directory": str(download_dir.resolve()),
            "download.prompt_for_download": False,
            "download.directory_upgrade": True,
            "plugins.always_open_pdf_externally": True,
            "safebrowsing.enabled": True,
        },
    )
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    if headless:
        options.add_argument("--headless=new")

    cached = _find_cached_chromedriver()
    try:
        if cached:
            driver = webdriver.Chrome(service=Service(cached), options=options)
        else:
            driver = webdriver.Chrome(options=options)
    except Exception:
        shutil.rmtree(perfil_dir, ignore_errors=True)
        raise

    driver.set_page_load_timeout(60)
    driver._celesc_profile_dir = perfil_dir  # type: ignore[attr-defined]
    driver._celesc_download_dir = download_dir  # type: ignore[attr-defined]

    try:
        driver.execute_cdp_cmd(
            "Page.setDownloadBehavior",
            {"behavior": "allow", "downloadPath": str(download_dir.resolve())},
        )
    except Exception:
        pass
    return driver


def _wait_clickable(driver: webdriver.Chrome, by: By, locator: str, timeout: int = TIMEOUT_PADRAO):
    return WebDriverWait(driver, timeout).until(EC.element_to_be_clickable((by, locator)))


def _wait_visible(driver: webdriver.Chrome, by: By, locator: str, timeout: int = TIMEOUT_PADRAO):
    return WebDriverWait(driver, timeout).until(EC.visibility_of_element_located((by, locator)))


def _clicar_robusto(driver: webdriver.Chrome, elemento) -> None:
    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", elemento)
    _pausa_humana(0.3, 0.8)
    try:
        elemento.click()
        return
    except Exception:
        pass
    try:
        driver.execute_script("arguments[0].click();", elemento)
        return
    except Exception:
        pass
    driver.execute_script(
        """
        const evt = new MouseEvent('click', { bubbles: true, cancelable: true, view: window });
        arguments[0].dispatchEvent(evt);
        """,
        elemento,
    )


def _texto_pagina_normalizado(driver: webdriver.Chrome) -> str:
    try:
        return _normalizar_texto(driver.find_element(By.TAG_NAME, "body").text)
    except Exception:
        return ""


def _aguardar_dom_estavel(
    driver: webdriver.Chrome,
    tentativas_iguais: int = 3,
    intervalo: float = 0.8,
    timeout: int = 12,
) -> None:
    limite = time.time() + timeout
    anterior = None
    iguais = 0
    while time.time() < limite:
        try:
            atual = (
                driver.current_url,
                len(driver.page_source or ""),
                len(driver.find_elements(By.CSS_SELECTOR, "button")),
            )
        except Exception:
            time.sleep(intervalo)
            continue
        if atual == anterior:
            iguais += 1
            if iguais >= tentativas_iguais:
                return
        else:
            anterior = atual
            iguais = 0
        time.sleep(intervalo)


def _historico_contas_renderizado(driver: webdriver.Chrome) -> bool:
    texto = _texto_pagina_normalizado(driver)
    return bool(driver.find_elements(By.CSS_SELECTOR, "celesc-bill-history-summary-section")) or bool(
        driver.find_elements(By.CSS_SELECTOR, "celesc-bill-history ui-celesc-table-row .month-selector")
    ) or (
        bool(driver.find_elements(By.CSS_SELECTOR, "celesc-bill-history"))
        and ("gerar 2" in texto or "pagar" in texto or "vencimento" in texto or "competencia" in texto)
    )


def _historico_sem_faturas_visivel(driver: webdriver.Chrome) -> bool:
    texto = _texto_pagina_normalizado(driver)
    return bool(driver.find_elements(By.CSS_SELECTOR, "celesc-empty-table-template")) or (
        "nao existem faturas disponiveis para essa instalacao" in texto
    )


def _digitar_humano(campo, valor: str) -> None:
    """Digita caractere por caractere com delay aleatorio para simular humano."""
    campo.send_keys(Keys.CONTROL, "a")
    campo.send_keys(Keys.DELETE)
    for char in valor:
        campo.send_keys(char)
        time.sleep(random.uniform(0.05, 0.18))


def _preencher_input(driver: webdriver.Chrome, locator: tuple[By, str], valor: str, nome: str) -> None:
    campo = _wait_visible(driver, locator[0], locator[1])
    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", campo)
    _pausa_humana(0.3, 0.7)
    try:
        campo.click()
    except Exception:
        try:
            driver.execute_script("arguments[0].click();", campo)
        except Exception:
            driver.execute_script("arguments[0].focus();", campo)

    try:
        _digitar_humano(campo, valor)
    except Exception:
        driver.execute_script(
            """
            arguments[0].value = arguments[1];
            arguments[0].dispatchEvent(new Event('input', { bubbles: true }));
            arguments[0].dispatchEvent(new Event('change', { bubbles: true }));
            """,
            campo,
            valor,
        )
    log.info("Campo %s preenchido.", nome)


def _localizar_input_por_rotulo(driver: webdriver.Chrome, rotulo: str, timeout: int = 10) -> tuple[By, str] | None:
    xpath = (
        "//label[contains(normalize-space(.), '{rotulo}')]"
        "/ancestor::*[contains(@class, 'input-wrapper')][1]"
        "/ancestor::*[1]//input"
    ).format(rotulo=rotulo)
    try:
        _wait_visible(driver, By.XPATH, xpath, timeout=timeout)
        return (By.XPATH, xpath)
    except Exception:
        return None


def _patch_webdriver_flag(driver: webdriver.Chrome) -> None:
    """Remove a flag navigator.webdriver via JS apos o carregamento da pagina."""
    try:
        driver.execute_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
            "window.chrome=window.chrome||{runtime:{}};"
        )
    except Exception:
        pass


def _obter_locators_login() -> tuple[list[tuple[By, str]], list[tuple[By, str]]]:
    xpath_email = (
        "//label[contains(normalize-space(.), 'E-mail')]"
        "/ancestor::*[contains(@class, 'input-wrapper')][1]"
        "/ancestor::*[1]//input"
    )
    xpath_senha = (
        "//label[contains(normalize-space(.), 'Senha')]"
        "/ancestor::*[contains(@class, 'input-wrapper')][1]"
        "/ancestor::*[1]//input"
    )
    candidatos_email = [
        (By.XPATH, xpath_email),
        (By.CSS_SELECTOR, "input[type='email']"),
        (By.CSS_SELECTOR, "input[autocomplete='username']"),
        (By.CSS_SELECTOR, "input[formcontrolname*='mail']"),
        (By.CSS_SELECTOR, "input[formcontrolname*='email']"),
        (By.XPATH, "//input[contains(@placeholder, 'mail') or contains(@aria-label, 'mail')]"),
    ]
    candidatos_senha = [
        (By.XPATH, xpath_senha),
        (By.CSS_SELECTOR, "input[type='password']"),
        (By.CSS_SELECTOR, "input[autocomplete='current-password']"),
        (By.CSS_SELECTOR, "input[formcontrolname*='senha']"),
        (By.XPATH, "//input[contains(@placeholder, 'Senha') or contains(@aria-label, 'Senha')]"),
    ]
    return (
        [locator for locator in candidatos_email if locator is not None],
        [locator for locator in candidatos_senha if locator is not None],
    )


def _campos_login_visiveis(driver: webdriver.Chrome, timeout: int = 2) -> tuple[tuple[By, str] | None, tuple[By, str] | None]:
    candidatos_email, candidatos_senha = _obter_locators_login()

    email_locator = None
    for locator in candidatos_email:
        try:
            _wait_visible(driver, locator[0], locator[1], timeout=timeout)
            email_locator = locator
            break
        except Exception:
            continue

    senha_locator = None
    for locator in candidatos_senha:
        try:
            _wait_visible(driver, locator[0], locator[1], timeout=timeout)
            senha_locator = locator
            break
        except Exception:
            continue

    return email_locator, senha_locator


def _salvar_dump_login(driver: webdriver.Chrome, prefixo: str) -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_DIR / f"{prefixo}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
    try:
        path.write_text(driver.page_source, encoding="utf-8")
    except Exception:
        pass
    return path


def _clicar_primeiro_visivel(driver: webdriver.Chrome, candidatos: list[tuple[By, str]]) -> bool:
    for by, locator in candidatos:
        try:
            elementos = driver.find_elements(by, locator)
        except Exception:
            continue
        for elemento in elementos:
            try:
                if not elemento.is_displayed():
                    continue
                try:
                    alvo = driver.execute_script(
                        """
                        const el = arguments[0];
                        return el.closest('button, a, [role="button"]') || el;
                        """,
                        elemento,
                    )
                except Exception:
                    alvo = elemento
                _clicar_robusto(driver, alvo)
                return True
            except Exception:
                continue
    return False


def _preparar_tela_login(driver: webdriver.Chrome) -> tuple[tuple[By, str], tuple[By, str]]:
    ultimo_erro = None

    for tentativa in range(1, 7):
        email_locator, senha_locator = _campos_login_visiveis(driver, timeout=2 if tentativa > 1 else 4)
        if email_locator and senha_locator:
            return email_locator, senha_locator

        candidatos_prioritarios = [
            [
                (By.CSS_SELECTOR, "ui-celesc-link.verified-user-link span.link"),
                (By.XPATH, "//*[contains(@class, 'verified-user-link')]//*[contains(normalize-space(.), 'Ja tenho o novo cadastro') or contains(normalize-space(.), 'Já tenho o novo cadastro')]"),
            ],
            [
                (By.CSS_SELECTOR, "ui-celesc-button.password-login-toggle button"),
                (By.XPATH, "//button[.//span[contains(normalize-space(.), 'Entrar com e-mail') or contains(normalize-space(.), 'Entrar com email')]]"),
            ],
            [
                (By.XPATH, "//button[.//span[contains(normalize-space(.), 'Acessar com e-mail') or contains(normalize-space(.), 'Acessar com email')]]"),
                (By.XPATH, "//button[.//span[contains(normalize-space(.), 'Continuar com e-mail') or contains(normalize-space(.), 'Continuar com email')]]"),
                (By.XPATH, "//button[.//span[contains(normalize-space(.), 'Fazer login')]]"),
            ],
        ]

        clicou = False
        for candidatos in candidatos_prioritarios:
            if _clicar_primeiro_visivel(driver, candidatos):
                clicou = True
                break

        if clicou:
            log.info("Etapa intermediaria antes do login detectada; avancando para o formulario (%s/6)...", tentativa)
            _pausa_humana(0.8, 1.4)
            try:
                _aguardar_dom_estavel(driver, timeout=8)
            except Exception as exc:
                ultimo_erro = exc
            continue

        _pausa_humana(0.8, 1.3)

    dump_path = _salvar_dump_login(driver, "celesc_login_falha")
    raise TimeoutException(
        f"Campo de e-mail nao encontrado apos as etapas intermediarias de login. HTML salvo em {dump_path}. "
        f"Ultimo erro: {ultimo_erro}"
    )


def fazer_login(driver: webdriver.Chrome, usuario: str, senha: str) -> None:
    log.info("Abrindo login CELESC...")
    _pausa_humana(1.0, 2.0)  # aguarda browser estabilizar antes de navegar
    driver.get(URL_LOGIN)
    _patch_webdriver_flag(driver)
    email_locator, senha_locator = _preparar_tela_login(driver)

    _preencher_input(driver, email_locator, usuario, "e-mail")
    _preencher_input(driver, senha_locator, senha, "senha")

    candidatos_entrar = [
        (By.XPATH, "//button[contains(@class, 'default')][.//span[contains(normalize-space(.), 'Entrar')]]"),
        (By.XPATH, "//button[.//span[contains(normalize-space(.), 'Entrar')] or contains(normalize-space(.), 'Entrar')]"),
        (By.CSS_SELECTOR, "button.default"),
    ]

    entrar = None
    for locator in candidatos_entrar:
        try:
            entrar = _wait_clickable(driver, locator[0], locator[1], timeout=10)
            texto = _normalizar_texto(entrar.text)
            if texto and "entrar" not in texto and locator != (By.CSS_SELECTOR, "button.default"):
                continue
            break
        except Exception:
            continue

    if entrar is None:
        raise TimeoutException("Botao Entrar nao encontrado.")

    _pausa_humana(0.8, 1.8)  # pausa natural entre digitar senha e clicar Entrar
    _clicar_robusto(driver, entrar)
    log.info("Login enviado.")


def fechar_modal_boas_vindas(driver: webdriver.Chrome) -> None:
    candidatos = [
        (By.XPATH, "//span[contains(normalize-space(.), 'Ja tenho o novo cadastro')]"),
        (By.XPATH, "//button[.//span[contains(normalize-space(.), 'Ja tenho o novo cadastro')]]"),
        (By.XPATH, "//*[contains(normalize-space(.), 'Ja tenho o novo cadastro')]"),
    ]

    for by, locator in candidatos:
        try:
            elemento = WebDriverWait(driver, 8).until(EC.presence_of_element_located((by, locator)))
            if elemento.is_displayed():
                _clicar_robusto(driver, elemento)
                log.info("Modal de boas-vindas fechado.")
                _pausa_humana(0.8, 1.5)
                return
        except Exception:
            continue

    log.info("Modal de boas-vindas nao apareceu.")


def _esta_em_erro_generico(driver: webdriver.Chrome) -> bool:
    try:
        texto = _normalizar_texto(driver.find_element(By.TAG_NAME, "body").text)
    except Exception:
        return False
    return "ocorreu um erro inesperado" in texto or "tente novamente mais tarde" in texto


def _recuperar_tela_apos_erro_generico(driver: webdriver.Chrome) -> bool:
    if not _esta_em_erro_generico(driver):
        return False

    log.warning("Portal CELESC caiu em tela generica de erro. Tentando retomar o fluxo...")
    candidatos = [
        (
            By.XPATH,
            (
                "//button[.//span[contains(normalize-space(.), 'Voltar para a pagina inicial')] "
                "or .//span[contains(normalize-space(.), 'Voltar para a página inicial')]]"
            ),
        ),
        (By.XPATH, "//ui-celesc-logo//button"),
        (
            By.XPATH,
            (
                "//button[contains(normalize-space(.), 'Trocar perfil')]"
                " | //li/button[contains(normalize-space(.), 'Trocar perfil')]"
            ),
        ),
    ]

    for by, locator in candidatos:
        try:
            elementos = driver.find_elements(by, locator)
            if not elementos:
                continue
            _clicar_robusto(driver, elementos[0])
            WebDriverWait(driver, TIMEOUT_PADRAO).until(
                lambda d: bool(
                    d.find_elements(
                        By.XPATH,
                        "//div[contains(@class, 'profile-card')][.//*[contains(normalize-space(.), 'Grupo A')]]",
                    )
                )
                or bool(d.find_elements(By.CSS_SELECTOR, ".pn-details-wrapper"))
                or not _esta_em_erro_generico(d)
            )
            _pausa_humana(0.8, 1.5)
            return True
        except Exception:
            continue
    return False


def selecionar_grupo_a(driver: webdriver.Chrome) -> None:
    log.info("Aguardando tela de selecao do perfil...")

    XPATH_GRUPO_A_BTN = (
        "//div[contains(@class,'profile-card')]"
        "[.//*[contains(normalize-space(.),'Grupo A')]]"
        "//button[contains(normalize-space(.),'Selecionar') or .//span[contains(normalize-space(.),'Selecionar')]]"
    )
    XPATH_PROFILE_CARD = (
        "//div[contains(@class,'profile-card')]"
        "[.//*[contains(normalize-space(.),'Grupo A')]]"
    )

    # Aguarda a pagina pos-login estabilizar: profile-card OU lista de CNPJs
    try:
        WebDriverWait(driver, TIMEOUT_PADRAO + 15).until(
            lambda d: bool(d.find_elements(By.CSS_SELECTOR, ".pn-details-wrapper"))
            or bool(d.find_elements(By.XPATH, XPATH_PROFILE_CARD))
            or _esta_em_erro_generico(d)
        )
    except TimeoutException:
        pass

    _pausa_humana(0.5, 1.0)

    # Ja esta na lista de CNPJs — Grupo A ativo
    if _esta_na_lista_cnpjs(driver):
        log.info("Grupo A ja estava ativo.")
        return

    # Tela de erro — tenta recuperar
    if _esta_em_erro_generico(driver):
        if not _recuperar_tela_apos_erro_generico(driver):
            dump_path = OUTPUT_DIR / f"celesc_escolha_perfil_falha_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
            dump_path.parent.mkdir(parents=True, exist_ok=True)
            dump_path.write_text(driver.page_source, encoding="utf-8")
            raise TimeoutException("Portal CELESC permaneceu na tela generica de erro apos o login.")

    # Clica em Grupo A
    ultimo_erro = None
    for _ in range(3):
        if driver.find_elements(By.CSS_SELECTOR, ".pn-details-wrapper"):
            log.info("Grupo A ja estava ativo.")
            return
        try:
            botao = WebDriverWait(driver, 15).until(
                EC.presence_of_element_located((By.XPATH, XPATH_GRUPO_A_BTN))
            )
            _clicar_robusto(driver, botao)
            log.info("Grupo A selecionado.")
            return
        except Exception as exc:
            ultimo_erro = exc
            _pausa_humana(1.0, 2.0)

    dump_path = OUTPUT_DIR / f"celesc_escolha_perfil_falha_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
    dump_path.parent.mkdir(parents=True, exist_ok=True)
    dump_path.write_text(driver.page_source, encoding="utf-8")
    raise TimeoutException(f"Selecao de Grupo A nao encontrada. HTML salvo em {dump_path}. Ultimo erro: {ultimo_erro}")


def aguardar_lista_cnpjs(driver: webdriver.Chrome) -> None:
    log.info("Aguardando lista de parceiros/CNPJs...")
    WebDriverWait(driver, TIMEOUT_PADRAO).until(
        lambda d: bool(re.search(r"\b\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}\b", d.page_source))
        or "parceiro de negocio" in _normalizar_texto(d.page_source)
    )
    log.info("Tela de parceiros carregada.")


def aguardar_lista_ucs(driver: webdriver.Chrome) -> None:
    log.info("Aguardando lista de UCs do CNPJ...")
    WebDriverWait(driver, TIMEOUT_PADRAO).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, ".consumer-unit-container"))
    )
    WebDriverWait(driver, TIMEOUT_PADRAO).until(
        lambda d: len(d.find_elements(By.CSS_SELECTOR, ".uc-list .edit-item-card-wrapper")) > 0
        and bool(
            d.find_elements(
                By.XPATH,
                "//button[contains(@class, 'small') and "
                "(contains(@class, 'outlined') or contains(@class, 'secondary'))]"
                "[.//span[contains(normalize-space(.), 'Selecionar unidade')]]",
            )
        )
    )
    _aguardar_dom_estavel(driver, timeout=10)
    log.info("Tela de UCs carregada.")


def aguardar_area_privada(driver: webdriver.Chrome) -> None:
    log.info("Aguardando area privada da UC...")
    WebDriverWait(driver, TIMEOUT_PADRAO + 15).until(
        lambda d: (
            "area-privada" in (d.current_url or "").lower()
            and (
                bool(d.find_elements(By.CSS_SELECTOR, "button.service-button"))
                or "historico de contas" in _texto_pagina_normalizado(d)
                or "sua conta esta pronta para ser paga" in _texto_pagina_normalizado(d)
            )
        )
        or _historico_contas_renderizado(d)
    )
    _aguardar_dom_estavel(driver, timeout=10)
    log.info("Area privada carregada.")


def _localizar_botao_historico(driver: webdriver.Chrome, timeout: int = 20):
    """Retorna o botao de Historico de contas/faturas assim que estiver presente e visivel."""
    XPATH = (
        "//button["
        "contains(normalize-space(.), 'Histórico de contas') or "
        "contains(normalize-space(.), 'Historico de contas') or "
        "contains(normalize-space(.), 'Histórico de faturas') or "
        "contains(normalize-space(.), 'Historico de faturas')"
        "]"
    )
    return WebDriverWait(driver, timeout).until(EC.presence_of_element_located((By.XPATH, XPATH)))


def abrir_historico_contas(driver: webdriver.Chrome) -> None:
    log.info("Abrindo Historico de contas...")
    aguardar_area_privada(driver)

    XPATH_BTN = (
        "//button["
        "contains(normalize-space(.), 'Histórico de contas') or "
        "contains(normalize-space(.), 'Historico de contas') or "
        "contains(normalize-space(.), 'Histórico de faturas') or "
        "contains(normalize-space(.), 'Historico de faturas')"
        "]"
    )

    ultimo_erro = None
    for tentativa in range(1, 5):
        try:
            # A partir da tentativa 2, volta para area privada (pagina pode ter mudado apos clique sem efeito)
            if tentativa > 1:
                log.info("  Retornando para area privada antes de nova tentativa...")
                aguardar_area_privada(driver)

            # Aguarda o botao estar presente no DOM
            btn = _localizar_botao_historico(driver, timeout=20)

            # Pausa extra para o Angular registrar os event listeners
            _pausa_humana(1.2, 2.0)

            # Garante que o botao ainda esta clicavel apos a pausa
            btn = WebDriverWait(driver, 10).until(EC.element_to_be_clickable((By.XPATH, XPATH_BTN)))

            _clicar_robusto(driver, btn)
            log.info("  Botao de historico clicado (tentativa %s).", tentativa)

            # Verifica se o historico realmente abriu em ate 8s
            try:
                WebDriverWait(driver, 8).until(lambda d: _historico_contas_renderizado(d))
                _aguardar_dom_estavel(driver, timeout=12)
                log.info("Historico de contas aberto.")
                return
            except TimeoutException:
                # Clique nao surtiu efeito — Angular ainda nao estava pronto
                log.warning("  Historico nao abriu apos o clique (tentativa %s). Aguardando e tentando novamente...", tentativa)
                _pausa_humana(2.0, 3.0)
                continue

        except Exception as exc:
            ultimo_erro = exc
            log.warning("  Falha ao localizar botao de historico (tentativa %s): %s", tentativa, exc)
            _pausa_humana(2.0, 3.0)

    dump_path = OUTPUT_DIR / f"celesc_area_privada_falha_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
    dump_path.parent.mkdir(parents=True, exist_ok=True)
    dump_path.write_text(driver.page_source, encoding="utf-8")
    raise TimeoutException(f"Botao de Historico de contas nao respondeu apos 4 tentativas. HTML salvo em {dump_path}. Ultimo erro: {ultimo_erro}")


def _coletar_faturas_bs4(html: str) -> list[dict[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    registros: list[dict[str, str]] = []
    contexto_node = soup.select_one("celesc-bill-history-summary-section .page-subtitle h3, celesc-bill-history-summary-section .page-subtitle")
    mes_contexto = ""
    ano_contexto = ""
    if contexto_node:
        match_ctx = re.search(r"([A-Za-zÀ-ÿ]+)\s*/\s*(\d{4})", contexto_node.get_text(" ", strip=True))
        if match_ctx:
            mes_contexto = _normalizar_texto(match_ctx.group(1))
            ano_contexto = match_ctx.group(2)

    mes_contexto_codigo = MESES_PT.get(mes_contexto, "")
    ano_corrente = int(ano_contexto) if ano_contexto.isdigit() else None
    mes_anterior_num = int(mes_contexto_codigo) if mes_contexto_codigo.isdigit() else None

    for idx, row in enumerate(soup.select("ui-celesc-table-row"), start=1):
        texto = _texto_limpo(row.get_text(" ", strip=True))

        mes_node = row.select_one(".month-selector .description p.md")
        due_node = row.select_one(".month-selector .due-date p")
        valor_node = row.select_one(".bill-amount p")
        status_node = row.select_one(".tag")
        action_node = row.select_one("button.small.default span, button.small.outlined span")

        if not due_node and not action_node:
            continue

        mes_ref = _texto_limpo(mes_node.get_text(" ", strip=True) if mes_node else "")
        vencimento = ""
        if due_node:
            match = re.search(r"(\d{2}/\d{2}/\d{4})", due_node.get_text(" ", strip=True))
            vencimento = match.group(1) if match else ""

        valor = _texto_limpo(valor_node.get_text(" ", strip=True) if valor_node else "")
        status = _texto_limpo(status_node.get_text(" ", strip=True) if status_node else "")
        acao = _texto_limpo(action_node.get_text(" ", strip=True) if action_node else "")
        ano_venc = vencimento[-4:] if len(vencimento) == 10 else ""
        acao_normalizada = _normalizar_texto(acao)
        mes_codigo = MESES_PT.get(_normalizar_texto(mes_ref), "")
        if mes_codigo.isdigit() and ano_corrente is not None:
            mes_num = int(mes_codigo)
            if mes_anterior_num is not None and mes_num > mes_anterior_num:
                ano_corrente -= 1
            mes_anterior_num = mes_num
        ano_ref = str(ano_corrente) if ano_corrente is not None else ano_venc
        mes_ref_competencia = f"{mes_codigo}-{ano_ref}" if mes_codigo and ano_ref else ""
        mes_exibicao = f"{mes_ref}/{ano_ref}" if mes_ref and ano_ref else mes_ref

        registros.append(
            {
                "ordem": str(idx),
                "mes_ref": mes_ref,
                "mes_codigo": mes_codigo,
                "mes_ref_competencia": mes_ref_competencia,
                "mes_exibicao": mes_exibicao,
                "ano_ref": ano_ref,
                "vencimento": vencimento,
                "ano_venc": ano_venc,
                "valor": valor,
                "status": status,
                "acao": acao,
                "acao_normalizada": acao_normalizada,
                "texto_normalizado": _normalizar_texto(texto),
            }
        )

    return registros


def _encontrar_botao_fatura(driver: webdriver.Chrome, fatura: dict[str, str]):
    mes_expr = _safe_contains_xpath(fatura["mes_ref"])
    venc_expr = _safe_contains_xpath(fatura["vencimento"])
    acao_expr = _safe_contains_xpath(fatura["acao"])
    xpath = (
        "//ui-celesc-table-row"
        f"[.//*[contains(normalize-space(.), {mes_expr})]]"
        f"[.//*[contains(normalize-space(.), {venc_expr})]]"
        f"//button[.//span[contains(normalize-space(.), {acao_expr})]]"
    )
    return WebDriverWait(driver, 15).until(EC.presence_of_element_located((By.XPATH, xpath)))


def _snapshot_downloads(download_dir: Path) -> set[str]:
    return {p.name for p in download_dir.glob("*") if p.is_file()}


def _esperar_novo_download(download_dir: Path, antes: set[str], timeout: int = 90) -> Path | None:
    limite = time.time() + timeout
    while time.time() < limite:
        atuais = [p for p in download_dir.glob("*") if p.is_file() and p.name not in antes]
        concluidos = [p for p in atuais if not p.name.endswith(".crdownload")]
        if concluidos:
            return max(concluidos, key=lambda p: p.stat().st_mtime)
        time.sleep(1)
    return None


def _renomear_e_copiar_pdf(pdf_path: Path, carimbo: str, mes_ref_competencia: str, tensao: str = TENSAO_GRUPO_A) -> tuple[Path, Path]:
    nome = f"{carimbo}.pdf"
    destino_local = pdf_path.with_name(nome)
    try:
        if pdf_path != destino_local:
            pdf_path.rename(destino_local)
    except Exception:
        destino_local = pdf_path

    destino_servidor = _pasta_pdf_servidor(mes_ref_competencia, tensao) / destino_local.name
    try:
        shutil.copy2(destino_local, destino_servidor)
        log.info("PDF copiado para servidor: %s", destino_servidor)
    except Exception as exc:
        log.warning("Falha ao copiar PDF para servidor (%s): %s", destino_servidor, exc)
    return destino_local, destino_servidor


def _copiar_arquivo_para_servidor(path: Path) -> None:
    SERVER_DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    SERVER_HISTORY_CSV_DIR.mkdir(parents=True, exist_ok=True)

    nome = path.name
    destino = SERVER_DOWNLOAD_DIR / nome
    destino_extra: Path | None = None

    if re.fullmatch(r"celesc_cnpjs_grupo_a_\d{8}_\d{6}\.csv", nome):
        destino = SERVER_HISTORY_CSV_DIR / nome
        destino_extra = SERVER_DOWNLOAD_DIR / "celesc_cnpjs_grupo_a_atual.csv"
    elif re.fullmatch(r"celesc_ucs_grupo_a_\d{8}_\d{6}\.csv", nome):
        destino = SERVER_HISTORY_CSV_DIR / nome
        destino_extra = SERVER_DOWNLOAD_DIR / "celesc_ucs_grupo_a_atual.csv"
    elif re.fullmatch(r"celesc_faturas_2026_\d{8}_\d{6}\.csv", nome):
        destino = SERVER_HISTORY_CSV_DIR / nome
        destino_extra = SERVER_DOWNLOAD_DIR / "celesc_faturas_2026_atual.csv"

    try:
        shutil.copy2(path, destino)
        log.info("Arquivo copiado para servidor: %s", destino)
        if destino_extra is not None:
            shutil.copy2(path, destino_extra)
            log.info("Snapshot atual atualizado: %s", destino_extra)
    except Exception as exc:
        log.warning("Falha ao copiar arquivo para servidor (%s): %s", destino, exc)


def _mes_pasta(mes_ref_competencia: str) -> str:
    return (mes_ref_competencia or "").replace("-", ".")


def _pasta_pdf_servidor(mes_ref_competencia: str, tensao: str = TENSAO_GRUPO_A) -> Path:
    pasta = SERVER_DOWNLOAD_DIR / _mes_pasta(mes_ref_competencia) / tensao
    pasta.mkdir(parents=True, exist_ok=True)
    return pasta


def baixar_faturas_2026(
    driver: webdriver.Chrome,
    master: MasterIndice | None,
    indice_local: IndiceLocalCelesc,
    parceiro_nome: str,
    codigo_parceiro: str,
    cnpj: str,
    uc: str,
    endereco: str,
    limite_faturas: int | None = None,
    meses_ref_alvo: set[str] | None = None,
    ignorar_ja_baixado: bool = False,
) -> list[dict[str, str]]:
    faturas: list[dict[str, str]] = []
    alvo: list[dict[str, str]] = []
    for tentativa_historico in range(1, 3):
        if tentativa_historico > 1:
            log.warning(
                "  Historico da UC %s veio sem faturas; reabrindo a unidade para nova tentativa (%s/2)...",
                uc,
                tentativa_historico,
            )
            if not _clicar_trocar_imovel_contexto(driver):
                raise TimeoutException(f"Nao foi possivel sair do historico para reabrir a UC {uc}.")
            _pausa_humana(0.8, 1.5)
            abrir_uc(driver, uc, endereco)
            aguardar_area_privada(driver)
            _pausa_humana(1.0, 2.0)

        abrir_historico_contas(driver)
        _pausa_humana(1.5, 2.5)
        faturas = _coletar_faturas_bs4(driver.page_source)
        alvo = [
            f
            for f in faturas
            if f.get("ano_ref") == "2026"
            and (
                "pagar" in (f.get("acao_normalizada") or "")
                or "gerar 2" in (f.get("acao_normalizada") or "")
            )
        ]
        if meses_ref_alvo:
            alvo = [f for f in alvo if (f.get("mes_ref_competencia", "") or "").strip() in meses_ref_alvo]
        if limite_faturas:
            alvo = alvo[:limite_faturas]
        if alvo or not _historico_sem_faturas_visivel(driver):
            break

    log.info("  %s fatura(s) de 2026 elegivel(is) para UC %s", len(alvo), uc)
    if not alvo:
        try:
            dump_path = OUTPUT_DIR / f"celesc_historico_sem_faturas_{_slug(uc or endereco)}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
            dump_path.write_text(driver.page_source, encoding="utf-8")
            log.warning("  Historico sem faturas elegiveis salvo em: %s", dump_path)
        except Exception:
            pass
    resultados: list[dict[str, str]] = []
    download_dir = getattr(driver, "_celesc_download_dir", DOWNLOAD_DIR)

    for fatura in alvo:
        mes_ref_competencia = fatura.get("mes_ref_competencia", "")
        if not ignorar_ja_baixado and master and master.ja_foi_baixado(uc, mes_ref_competencia, "CELESC"):
            log.info("    Ja no master: %s | %s", uc, mes_ref_competencia)
            continue
        if not ignorar_ja_baixado and indice_local.ja_baixado(uc, mes_ref_competencia):
            log.info("    Ja no indice local: %s | %s", uc, mes_ref_competencia)
            continue

        antes = _snapshot_downloads(download_dir)
        botao = _encontrar_botao_fatura(driver, fatura)
        _clicar_robusto(driver, botao)
        time.sleep(2)

        try:
            modal_btn = WebDriverWait(driver, 8).until(
                EC.presence_of_element_located(
                    (
                        By.XPATH,
                        "//button[contains(@class, 'secondary')][.//span[contains(normalize-space(.), 'Gerar 2')]]",
                    )
                )
            )
            _clicar_robusto(driver, modal_btn)
            log.info(
                "    Confirmacao de 2a via acionada: %s | %s",
                fatura.get("mes_ref"),
                fatura.get("vencimento"),
            )
        except Exception:
            log.info(
                "    Acao direta sem modal visivel: %s | %s",
                fatura.get("mes_ref"),
                fatura.get("vencimento"),
            )

        pdf = _esperar_novo_download(download_dir, antes, timeout=90)
        status_download = "ERRO"
        arquivo_final = ""
        if pdf:
            if master:
                carimbo = master.consumir_carimbo()
            else:
                carimbo = f"BB_{indice_local.proximo:07d}"
            final_path, destino_servidor = _renomear_e_copiar_pdf(
                pdf,
                carimbo,
                mes_ref_competencia=mes_ref_competencia,
                tensao=TENSAO_GRUPO_A,
            )
            status_download = "OK"
            arquivo_final = str(destino_servidor)
            if master:
                master.registrar(
                    indice_bb=carimbo,
                    sistema="CELESC",
                    uc=uc,
                    mes_ref=mes_ref_competencia,
                    fatura_id=f"{fatura.get('mes_exibicao', fatura.get('mes_ref', ''))}|{fatura.get('vencimento', '')}",
                    cnpj=cnpj,
                    estado="SANTA CATARINA",
                    arquivo=str(destino_servidor),
                    concessionaria="CELESC",
                )
            indice_local.gravar(
                indice_bb=carimbo,
                instalacao=uc,
                endereco=endereco,
                mes_ref=mes_ref_competencia,
                mes_exibicao=fatura.get("mes_exibicao", fatura.get("mes_ref", "")),
                vencimento=fatura.get("vencimento", ""),
                valor=fatura.get("valor", ""),
                status_conta=fatura.get("status", ""),
                acao_portal=fatura.get("acao", ""),
                cnpj=cnpj,
                codigo_parceiro=codigo_parceiro,
                parceiro_nome=parceiro_nome,
                arquivo=str(destino_servidor),
            )
            log.info("    PDF baixado: %s", final_path.name)
        else:
            log.warning("    Nenhum PDF detectado para %s | %s", fatura.get("mes_ref"), fatura.get("vencimento"))

        resultados.append(
            {
                **fatura,
                "indice": carimbo if pdf else "",
                "cnpj": cnpj,
                "uc": uc,
                "endereco": endereco,
                "arquivo_pdf": arquivo_final,
                "download_status": status_download,
            }
        )
        try:
            driver.find_element(By.TAG_NAME, "body").send_keys(Keys.ESCAPE)
        except Exception:
            pass
        _pausa_humana(0.8, 1.8)

    try:
        if _clicar_trocar_imovel_contexto(driver):
            _pausa_humana(0.8, 1.5)
        else:
            try:
                driver.find_element(By.TAG_NAME, "body").send_keys(Keys.ESCAPE)
            except Exception:
                pass
    except Exception as exc:
        log.warning("Falha ao sair do historico via 'Trocar imovel': %s", exc)
        try:
            driver.find_element(By.TAG_NAME, "body").send_keys(Keys.ESCAPE)
        except Exception:
            pass

    return resultados




def abrir_uc(driver: webdriver.Chrome, uc: str, endereco: str) -> None:
    xpath = (
        "//div[contains(@class, 'edit-item-card-wrapper')]"
        f"[.//*[contains(normalize-space(.), {_safe_contains_xpath(endereco)})]]"
    )
    if uc:
        xpath += f"[.//*[contains(normalize-space(.), {_safe_contains_xpath(uc)})]]"
    xpath += (
        "//button[contains(@class, 'small') and "
        "(contains(@class, 'outlined') or contains(@class, 'secondary'))]"
        "[.//span[contains(normalize-space(.), 'Selecionar unidade')]]"
        "[.//ui-celesc-icon//span[contains(normalize-space(.), 'arrow_forward')]]"
    )

    botao = WebDriverWait(driver, TIMEOUT_PADRAO).until(
        EC.element_to_be_clickable((By.XPATH, xpath))
    )
    _clicar_robusto(driver, botao)
    log.info("  UC selecionada: %s | %s", uc or "SEM_UC", endereco)


def _esta_na_lista_cnpjs(driver: webdriver.Chrome) -> bool:
    return bool(driver.find_elements(By.CSS_SELECTOR, ".pn-details-wrapper"))


def _esta_na_lista_ucs(driver: webdriver.Chrome) -> bool:
    # A URL muda para ``contrato/selecao`` antes de os componentes da lista
    # terminarem de renderizar. Considerar apenas a URL como sucesso fazia o
    # fluxo avancar cedo demais e, logo depois, expirar em aguardar_lista_ucs.
    return bool(driver.find_elements(By.CSS_SELECTOR, ".uc-list .edit-item-card-wrapper"))


def _esta_na_selecao_perfil(driver: webdriver.Chrome) -> bool:
    return bool(
        driver.find_elements(
            By.XPATH,
            "//div[contains(@class,'profile-card')][.//*[contains(normalize-space(.),'Grupo A')]]",
        )
    )



def _clicar_trocar_imovel_contexto(driver: webdriver.Chrome) -> bool:
    candidatos = [
        (
            By.XPATH,
            "//ui-celesc-link//span[contains(normalize-space(.), 'Trocar imóvel')]",
        ),
        (
            By.XPATH,
            "//ui-celesc-link//span[contains(normalize-space(.), 'Trocar imovel')]",
        ),
        (
            By.XPATH,
            "//span[contains(@class, 'link') and contains(normalize-space(.), 'Trocar imóvel')]",
        ),
        (
            By.XPATH,
            "//span[contains(@class, 'link') and contains(normalize-space(.), 'Trocar imovel')]",
        ),
    ]
    for by, locator in candidatos:
        try:
            botao = WebDriverWait(driver, 8).until(EC.element_to_be_clickable((by, locator)))
            _clicar_robusto(driver, botao)
            WebDriverWait(driver, TIMEOUT_PADRAO + 10).until(lambda d: _esta_na_lista_ucs(d))
            _aguardar_dom_estavel(driver, timeout=10)
            log.info("Trocar imovel acionado; retorno para lista de UCs concluido.")
            return True
        except Exception:
            continue
    return False



def _garantir_lista_cnpjs_via_portal_raiz(driver: webdriver.Chrome) -> None:
    log.info("Reabrindo portal para retornar ao nivel de CNPJs...")
    driver.get(URL_PORTAL)
    WebDriverWait(driver, TIMEOUT_PADRAO + 20).until(
        lambda d: _esta_na_selecao_perfil(d) or _esta_na_lista_cnpjs(d)
    )
    _aguardar_dom_estavel(driver, timeout=10)

    if _esta_na_lista_cnpjs(driver):
        log.info("Lista de CNPJs reaberta via portal raiz.")
        _pausa_humana(0.5, 1.0)
        return

    selecionar_grupo_a(driver)
    aguardar_lista_cnpjs(driver)
    _pausa_humana(1.0, 2.0)
    log.info("Retorno para lista de CNPJs concluido via Grupo A.")


def _coletar_cartoes_cnpj_bs4(html: str) -> list[dict[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    cnpj_regex = re.compile(r"\b\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}\b")

    encontrados: list[dict[str, str]] = []
    vistos: set[str] = set()
    for card in soup.select("div.pn-details-wrapper"):
        nome_node = card.select_one(".content .title p, .content ui-celesc-body-text.title p")
        cnpj_node = card.select_one(".content .document p")
        nome = _texto_limpo(nome_node.get_text(" ", strip=True) if nome_node else "")
        cnpj_texto = _texto_limpo(cnpj_node.get_text(" ", strip=True) if cnpj_node else "")
        codigo_nodes = card.select(".content ui-celesc-body-text p")
        codigo = ""
        if len(codigo_nodes) >= 3:
            codigo = _texto_limpo(codigo_nodes[2].get_text(" ", strip=True))
        match = cnpj_regex.search(cnpj_texto or card.get_text(" ", strip=True))
        if not match:
            continue
        cnpj = match.group(0)
        if cnpj in vistos:
            continue
        vistos.add(cnpj)
        encontrados.append(
            {
                "parceiro_nome": nome,
                "cnpj": cnpj,
                "codigo_parceiro": codigo,
                "texto_normalizado": _normalizar_texto(" ".join([nome, cnpj, codigo])),
            }
        )
    return encontrados


def _coletar_ucs_bs4(html: str) -> list[dict[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    encontrados: list[dict[str, str]] = []
    vistos: set[tuple[str, str]] = set()

    for card in soup.select(".uc-list .edit-item-card-wrapper"):
        textos = [
            _texto_limpo(p.get_text(" ", strip=True))
            for p in card.select(".item-card-content p")
            if _texto_limpo(p.get_text(" ", strip=True))
        ]
        if not textos:
            continue

        endereco = textos[0]
        uc = ""
        for texto in textos[1:]:
            match = re.search(r"\b\d{5,20}\b", texto)
            if match:
                uc = match.group(0)
                break

        if not uc:
            match = re.search(r"\b\d{5,20}\b", card.get_text(" ", strip=True))
            uc = match.group(0) if match else ""

        chave = (uc, endereco)
        if chave in vistos:
            continue
        vistos.add(chave)
        encontrados.append(
            {
                "uc": uc,
                "endereco": endereco,
                "texto_normalizado": _normalizar_texto(" ".join(textos)),
            }
        )
    return encontrados


def salvar_html(driver: webdriver.Chrome) -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_DIR / f"celesc_grupo_a_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
    path.write_text(driver.page_source, encoding="utf-8")
    return path


def salvar_cnpjs_csv(cnpjs: list[dict[str, str]]) -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_DIR / f"celesc_cnpjs_grupo_a_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=["parceiro_nome", "cnpj", "codigo_parceiro", "texto_normalizado"])
        writer.writeheader()
        writer.writerows(cnpjs)
    return path


def salvar_ucs_csv(linhas: list[dict[str, str]]) -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_DIR / f"celesc_ucs_grupo_a_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
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
    path = OUTPUT_DIR / f"celesc_faturas_2026_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
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


def abrir_cnpj(driver: webdriver.Chrome, cnpj: str) -> None:
    xpath = (
        "//div[contains(@class, 'pn-details-wrapper')]"
        f"[.//*[contains(normalize-space(.), '{cnpj}')]]"
        "//button[contains(@class, 'small') and contains(@class, 'default')]"
    )
    botao = WebDriverWait(driver, TIMEOUT_PADRAO).until(
        EC.element_to_be_clickable((By.XPATH, xpath))
    )
    _clicar_robusto(driver, botao)
    log.info("CNPJ selecionado: %s", cnpj)


def abrir_cnpj_e_aguardar_ucs(driver: webdriver.Chrome, cnpj: str, tentativas: int = 3) -> None:
    ultimo_erro = None
    for tentativa in range(1, tentativas + 1):
        try:
            if not _esta_na_lista_cnpjs(driver):
                _trocar_perfil_e_selecionar_grupo_a(driver)
            aguardar_lista_cnpjs(driver)
            _pausa_humana(0.8, 1.4)
            abrir_cnpj(driver, cnpj)
            aguardar_lista_ucs(driver)
            _pausa_humana(1.0, 2.0)
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
                dump_path = OUTPUT_DIR / f"celesc_lista_ucs_falha_{_slug(cnpj)}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
                dump_path.write_text(driver.page_source, encoding="utf-8")
                log.warning("HTML de falha ao abrir CNPJ salvo em: %s", dump_path)
            except Exception:
                pass
            if tentativa < tentativas:
                try:
                    _trocar_perfil_e_selecionar_grupo_a(driver)
                except Exception:
                    try:
                        driver.get(URL_PORTAL)
                        selecionar_grupo_a(driver)
                        aguardar_lista_cnpjs(driver)
                    except Exception:
                        pass
                _pausa_humana(1.5, 2.5)

    raise TimeoutException(
        f"Nao foi possivel abrir o CNPJ {cnpj} e carregar a lista de UCs apos {tentativas} tentativa(s)."
    ) from ultimo_erro


def _trocar_perfil_e_selecionar_grupo_a(driver: webdriver.Chrome) -> None:
    """Retorna para a lista de CNPJs priorizando o fluxo nativo do portal.

    Estrategia:
    1. Se ja na lista de CNPJs → nada a fazer.
    2. Se em area privada/historico → clica 'Trocar imovel' para chegar na lista de UCs.
    3. Se na lista de UCs → clica a seta de voltar para chegar na lista de CNPJs.
    4. Fallback: reabre o portal pela URL raiz.
    Evita 'Trocar perfil' (sai do perfil inteiro, desnecessario).
    """
    log.info("Retornando para a lista de CNPJs...")

    if _esta_na_lista_cnpjs(driver):
        log.info("Ja na lista de CNPJs - estado limpo.")
        _pausa_humana(0.5, 1.0)
        return

    # Passo 1: se estiver em area privada/historico, sair para a lista de UCs via 'Trocar imovel'
    if not _esta_na_lista_ucs(driver):
        try:
            if _clicar_trocar_imovel_contexto(driver):
                _pausa_humana(0.8, 1.5)
        except Exception as exc:
            log.warning("Falha ao tentar sair para a lista de UCs via 'Trocar imovel': %s", exc)

    # Passo 2: se na lista de UCs (ou em qualquer outro estado), navega direto para selecao de acesso
    log.info("Navegando para tela de selecao de acesso para reselecionar Grupo A...")
    try:
        driver.get("https://conecte.celesc.com.br/autenticacao/selecao-acesso")
        WebDriverWait(driver, TIMEOUT_PADRAO + 10).until(
            lambda d: _esta_na_selecao_perfil(d) or _esta_na_lista_cnpjs(d)
        )
        _pausa_humana(0.5, 1.0)
        if _esta_na_lista_cnpjs(driver):
            log.info("Retorno para lista de CNPJs concluido via selecao-acesso.")
            return
        if _esta_na_selecao_perfil(driver):
            selecionar_grupo_a(driver)
            aguardar_lista_cnpjs(driver)
            log.info("Retorno para lista de CNPJs concluido via Grupo A em selecao-acesso.")
            return
    except Exception as exc:
        log.warning("Falha ao navegar para selecao-acesso: %s", exc)

    # Fallback: reabre o portal pela URL raiz
    _garantir_lista_cnpjs_via_portal_raiz(driver)

    candidatos_trocar = [
        (By.XPATH, "//button[contains(normalize-space(.), 'Trocar perfil')]"),
        (By.XPATH, "//li/button[contains(normalize-space(.), 'Trocar perfil')]"),
        (By.XPATH, "//*[contains(@class,'profile')]//button[contains(normalize-space(.), 'Trocar')]"),
    ]

    clicou = False
    for by, locator in candidatos_trocar:
        try:
            btn = WebDriverWait(driver, 8).until(EC.presence_of_element_located((by, locator)))
            if btn.is_displayed():
                _clicar_robusto(driver, btn)
                clicou = True
                log.info("Botao 'Trocar perfil' clicado.")
                break
        except Exception:
            continue

    if not clicou:
        log.warning("Botao 'Trocar perfil' nao encontrado. Tentando via URL raiz do portal...")
        try:
            driver.get("https://conecte.celesc.com.br/")
        except Exception:
            pass

    # Aguarda tela de selecao de perfil OU lista de CNPJs (se Grupo A ja estava ativo)
    try:
        WebDriverWait(driver, TIMEOUT_PADRAO).until(
            lambda d: _esta_na_selecao_perfil(d) or _esta_na_lista_cnpjs(d)
        )
    except TimeoutException:
        pass

    # Se caiu direto na lista de CNPJs, esta pronto
    if _esta_na_lista_cnpjs(driver):
        log.info("Ja na lista de CNPJs — estado limpo.")
        _pausa_humana(0.5, 1.0)
        return

    # Seleciona Grupo A e aguarda lista de CNPJs
    selecionar_grupo_a(driver)
    aguardar_lista_cnpjs(driver)
    _pausa_humana(1.0, 2.0)
    log.info("Pronto na lista de CNPJs — estado limpo.")


def listar_ucs_por_cnpj(
    driver: webdriver.Chrome,
    master: MasterIndice | None,
    indice_local: IndiceLocalCelesc,
    cartoes_cnpj: list[dict[str, str]],
    limite_cnpjs: int | None = None,
    limite_ucs: int | None = None,
    baixar_faturas: bool = False,
    limite_faturas: int | None = None,
    cnpjs_alvo: set[str] | None = None,
    ucs_alvo: set[str] | None = None,
    meses_ref_alvo: set[str] | None = None,
    ignorar_ja_baixado: bool = False,
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """Itera sobre CNPJs e UCs.

    Estrategia de navegacao:
    - Fase 1 (coleta): entra no CNPJ, le as UCs, volta para lista de CNPJs via
      _trocar_perfil_e_selecionar_grupo_a (sem driver.back()).
    - Fase 2 (download): para cada UC, renavega CNPJ -> UC -> baixa -> volta para
      lista de CNPJs via _trocar_perfil_e_selecionar_grupo_a.
    Isso evita qualquer uso de driver.back() apos downloads, que e instavel no
    portal CELESC.
    """
    if cnpjs_alvo:
        cartoes_cnpj = [item for item in cartoes_cnpj if (item.get("cnpj", "") or "").strip() in cnpjs_alvo]
    selecionados = cartoes_cnpj[:limite_cnpjs] if limite_cnpjs else cartoes_cnpj
    resultado: list[dict[str, str]] = []
    resultado_faturas: list[dict[str, str]] = []

    for indice, card in enumerate(selecionados, start=1):
        cnpj = card["cnpj"]
        log.info("[%s/%s] CNPJ %s (%s)", indice, len(selecionados), cnpj, card.get("parceiro_nome", ""))

        # ── Fase 1: coletar lista de UCs ─────────────────────────────────────
        ucs: list[dict[str, str]] = []
        try:
            abrir_cnpj_e_aguardar_ucs(driver, cnpj)
            ucs = _coletar_ucs_bs4(driver.page_source)
            if not ucs:
                log.warning("  Nenhuma UC encontrada para %s", cnpj)
            else:
                log.info("  %s UC(s) encontrada(s) para %s", len(ucs), cnpj)
        except Exception as exc:
            log.error("  Erro ao coletar UCs do CNPJ %s: %s", cnpj, exc)

        if ucs_alvo:
            ucs = [item for item in ucs if (item.get("uc", "") or "").strip() in ucs_alvo]
        ucs_selecionadas = ucs[:limite_ucs] if limite_ucs else ucs
        for uc_info in ucs_selecionadas:
            resultado.append({
                "parceiro_nome": card.get("parceiro_nome", ""),
                "cnpj": cnpj,
                "codigo_parceiro": card.get("codigo_parceiro", ""),
                "uc": uc_info.get("uc", ""),
                "endereco": uc_info.get("endereco", ""),
                "texto_normalizado": uc_info.get("texto_normalizado", ""),
            })
            log.info("    UC %s | %s", uc_info.get("uc") or "SEM_UC", uc_info.get("endereco", ""))

        if not baixar_faturas or not ucs_selecionadas:
            _trocar_perfil_e_selecionar_grupo_a(driver)
            continue

        # ── Fase 2: baixar faturas UC por UC ─────────────────────────────────
        # Cada UC começa da lista de CNPJs (estado limpo garantido)
        for uc_idx, uc_info in enumerate(ucs_selecionadas, start=1):
            linha = {
                "parceiro_nome": card.get("parceiro_nome", ""),
                "cnpj": cnpj,
                "codigo_parceiro": card.get("codigo_parceiro", ""),
                "uc": uc_info.get("uc", ""),
                "endereco": uc_info.get("endereco", ""),
            }
            log.info(
                "  [UC %s/%s] Baixando %s | %s",
                uc_idx, len(ucs_selecionadas),
                linha["uc"] or "SEM_UC", linha["endereco"],
            )
            try:
                if uc_idx == 1:
                    if not _esta_na_lista_ucs(driver):
                        abrir_cnpj_e_aguardar_ucs(driver, cnpj)
                else:
                    abrir_cnpj_e_aguardar_ucs(driver, cnpj)
                abrir_uc(driver, linha["uc"], linha["endereco"])
                aguardar_area_privada(driver)
                _pausa_humana(1.0, 2.0)

                faturas_uc = baixar_faturas_2026(
                    driver,
                    master=master,
                    indice_local=indice_local,
                    parceiro_nome=linha["parceiro_nome"],
                    codigo_parceiro=linha["codigo_parceiro"],
                    cnpj=cnpj,
                    uc=linha["uc"],
                    endereco=linha["endereco"],
                    limite_faturas=limite_faturas,
                    meses_ref_alvo=meses_ref_alvo,
                    ignorar_ja_baixado=ignorar_ja_baixado,
                )
                if not faturas_uc:
                    log.info("    Nenhuma fatura 2026 para %s", linha["uc"] or linha["endereco"])
                for item in faturas_uc:
                    resultado_faturas.append({**linha, **item})

            except Exception as exc:
                log.error("    Erro na UC %s: %s", linha["uc"] or linha["endereco"], exc)

            finally:
                # Sempre volta para estado limpo independente de erro ou sucesso
                try:
                    _trocar_perfil_e_selecionar_grupo_a(driver)
                except Exception as exc_nav:
                    if _deve_recriar_sessao(exc_nav):
                        log.warning(
                            "Sessao Chrome morreu ao tentar retornar para lista de CNPJs (CNPJ indice %s). "
                            "Sinalizando para reinicio de sessao...",
                            indice,
                        )
                        raise _SessaoMortaError(indice, resultado, resultado_faturas) from exc_nav
                    log.warning("Falha ao retornar para lista de CNPJs (nao fatal): %s", exc_nav)

    return resultado, resultado_faturas


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
    driver = None
    master = carregar_master()
    indice_local = IndiceLocalCelesc()
    if master:
        try:
            indice_local.proximo = max(indice_local.proximo, int(master.proximo_carimbo.replace("BB_", "")))
        except Exception:
            pass
    try:
        ultimo_erro_login = None
        for tentativa in range(1, 4):
            try:
                driver = build_driver(headless=headless)
                fazer_login(driver, usuario, senha)
                fechar_modal_boas_vindas(driver)
                selecionar_grupo_a(driver)
                aguardar_lista_cnpjs(driver)
                _pausa_humana(1.5, 2.5)
                break
            except Exception as exc:
                ultimo_erro_login = exc
                if tentativa >= 3 or not _deve_reiniciar_fluxo_portal(driver, exc):
                    raise
                log.warning(
                    "Falha ao iniciar fluxo CELESC (%s/3): %s. Recriando sessao...",
                    tentativa,
                    exc,
                )
                _encerrar_driver(driver)
                driver = None
                time.sleep(3)
        if driver is None and ultimo_erro_login is not None:
            raise ultimo_erro_login

        html_path = salvar_html(driver)
        cnpjs = _coletar_cartoes_cnpj_bs4(driver.page_source)

        if not cnpjs:
            log.warning("Nenhum CNPJ encontrado via BS4. HTML salvo em: %s", html_path)
            return 2

        csv_path = salvar_cnpjs_csv(cnpjs)
        log.info("CNPJs encontrados: %s", len(cnpjs))
        for item in cnpjs:
            log.info(
                "  %s  |  %s  |  %s",
                item["cnpj"],
                item["parceiro_nome"] or "-",
                item["codigo_parceiro"] or "-",
            )

        # Itera com recuperacao automatica de sessao morta
        cnpjs_restantes = cnpjs[:limite_cnpjs] if limite_cnpjs else cnpjs
        linhas_ucs: list = []
        linhas_faturas: list = []
        MAX_RECRIACOES = 5

        for recriacao in range(MAX_RECRIACOES + 1):
            try:
                ucs_parcial, faturas_parcial = listar_ucs_por_cnpj(
                    driver,
                    master,
                    indice_local,
                    cnpjs_restantes,
                    limite_cnpjs=None,        # ja fatiado acima
                    limite_ucs=limite_ucs,
                    baixar_faturas=baixar_faturas,
                    limite_faturas=limite_faturas,
                    cnpjs_alvo=cnpjs_alvo,
                    ucs_alvo=ucs_alvo,
                    meses_ref_alvo=meses_ref_alvo,
                    ignorar_ja_baixado=ignorar_ja_baixado,
                )
                linhas_ucs.extend(ucs_parcial)
                linhas_faturas.extend(faturas_parcial)
                break  # concluiu sem sessao morta
            except _SessaoMortaError as exc:
                linhas_ucs.extend(exc.resultado)
                linhas_faturas.extend(exc.resultado_faturas)
                cnpjs_restantes = cnpjs_restantes[exc.indice_cnpj:]
                log.warning(
                    "Sessao morta no CNPJ indice %s. Recriando sessao e retomando (%s/%s)...",
                    exc.indice_cnpj,
                    recriacao + 1,
                    MAX_RECRIACOES,
                )
                _encerrar_driver(driver)
                driver = None
                if recriacao >= MAX_RECRIACOES:
                    log.error("Limite de recriacoes de sessao atingido. Encerrando.")
                    break
                time.sleep(5)
                for tent_login in range(1, 4):
                    try:
                        driver = build_driver(headless=headless)
                        fazer_login(driver, usuario, senha)
                        fechar_modal_boas_vindas(driver)
                        selecionar_grupo_a(driver)
                        aguardar_lista_cnpjs(driver)
                        _pausa_humana(1.5, 2.5)
                        log.info("Sessao recriada com sucesso. Retomando CNPJs restantes: %s", len(cnpjs_restantes))
                        break
                    except Exception as exc_login:
                        log.warning("Falha ao recriar sessao (%s/3): %s", tent_login, exc_login)
                        _encerrar_driver(driver)
                        driver = None
                        time.sleep(3)
                if driver is None:
                    log.error("Nao foi possivel recriar a sessao. Encerrando com resultados parciais.")
                    break
        ucs_csv_path = salvar_ucs_csv(linhas_ucs)
        _copiar_arquivo_para_servidor(csv_path)
        _copiar_arquivo_para_servidor(ucs_csv_path)

        log.info("HTML salvo em: %s", html_path)
        log.info("CSV salvo em: %s", csv_path)
        log.info("CSV UCs salvo em: %s", ucs_csv_path)
        log.info("Indice local CELESC: %s", INDEX_LOCAL_PATH)
        _copiar_arquivo_para_servidor(INDEX_LOCAL_PATH)
        if baixar_faturas:
            faturas_csv_path = salvar_faturas_csv(linhas_faturas)
            _copiar_arquivo_para_servidor(faturas_csv_path)
            log.info("CSV faturas salvo em: %s", faturas_csv_path)
            log.info("Faturas 2026 processadas: %s", len(linhas_faturas))
        return 0
    finally:
        _encerrar_driver(driver)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fluxo inicial CELESC - Grupo A")
    parser.add_argument("--usuario", default=USUARIO_PADRAO)
    parser.add_argument("--senha", default=SENHA_PADRAO)
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
