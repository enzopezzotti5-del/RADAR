#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Fluxo inicial CPFL / RGE
========================

MVP focado nos primeiros passos operacionais:
1. Abrir a tela de login B2C
2. Preencher email e senha
3. Clicar em entrar
4. Aceitar cookies
5. Selecionar o perfil Media e alta tensao

Este arquivo serve como base para evoluirmos o restante do fluxo CPFL/RGE
conforme os proximos passos operacionais forem sendo passados.
"""

from __future__ import annotations

import argparse
import csv
import json
import html as html_lib
import importlib.util
import logging
import random
import re
import requests
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from selenium import webdriver
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from core.downloaders.cpfl.cpfl_guard import validar_expansao_ucs


ROOT_LOCAL = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT_LOCAL))
import _venv_check  # noqa


URL_LOGIN = (
    "https://cpflb2cprd.b2clogin.com/cpflb2cprd.onmicrosoft.com/"
    "b2c_1a_signup_signin_mfa_front/oauth2/v2.0/authorize"
    "?p=B2C_1A_SIGNUP_SIGNIN_MFA_FRONT"
    "&client_id=17d5831d-6741-4670-8085-d1d34e37aec1"
    "&nonce=defaultNonce"
    "&redirect_uri=https%3A%2F%2Fwww.cpfl.com.br%2Fb2c-auth%2Freceive-token"
    "&scope=17d5831d-6741-4670-8085-d1d34e37aec1%20offline_access"
    "&response_type=code"
    "&prompt=login"
    "&response_mode=query"
)
URL_CADASTRO = "https://www.cpfl.com.br/agencia/area-cliente/cadastro"
URL_RESELECIONAR = "https://www.cpfl.com.br/agencia/area-cliente/selecionar-perfil-instalacao"
USUARIO_PADRAO = "denise.souza@acaoengenharia.com.br"
SENHA_PADRAO = "Acao@2026"

_CONTAS = {
    "denise": ("denise.souza@acaoengenharia.com.br", "Acao@2026"),
    "rge":    ("bbenergia@acaoengenharia.com.br",    "Acao*2024"),
    "bb":     ("bbenergia@acaoengenharia.com.br",    "Acao*2024"),
}
TIMEOUT = 40

SCRIPT_DIR = Path(__file__).resolve().parent
BASE_DIR = Path("//10.10.250.21/Energia/ARQUIVOS ENZO/DOWNLOAD CPFL")
LOG_DIR = BASE_DIR / "logs"
SAIDA_DIR = BASE_DIR / "saida"
DOWNLOAD_DIR = BASE_DIR
TEMP_DOWNLOAD_DIR = Path.home() / "AppData" / "Local" / "cpfl_temp"
_WORKER_ID: int = 0  # sobrescrito por --worker-id no main
INDICE_LOCAL_PATH = BASE_DIR / "indice_faturas_cpfl.csv"
INVENTARIO_PATH = BASE_DIR / "cpfl_ucs_inventario.csv"
MASTER_PY_PATH = ROOT_LOCAL.parent / "scripts" / "infra" / "indice_master.py"


def _configurar_logging() -> logging.Logger:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    logger = logging.getLogger("cpfl_rge")
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()

    fmt_console = logging.Formatter("%(asctime)s  %(message)s", datefmt="%H:%M:%S")
    fmt_file = logging.Formatter("%(asctime)s %(levelname)-8s %(message)s", datefmt="%H:%M:%S")

    h_console = logging.StreamHandler(sys.stdout)
    h_console.setFormatter(fmt_console)
    h_console.setLevel(logging.INFO)

    h_file = logging.FileHandler(LOG_DIR / f"cpfl_rge_{ts}.log", encoding="utf-8")
    h_file.setFormatter(fmt_file)
    h_file.setLevel(logging.DEBUG)

    logger.addHandler(h_console)
    logger.addHandler(h_file)
    return logger


log = _configurar_logging()


class PortalPdfError(RuntimeError):
    """Erro funcional do portal ao gerar/entregar o PDF."""


class PerfilIndisponivelError(RuntimeError):
    """Erro quando o perfil alvo nao fica acessivel no portal."""


def carregar_master():
    if not MASTER_PY_PATH.exists():
        raise FileNotFoundError(f"indice_master.py nao encontrado: {MASTER_PY_PATH}")
    spec = importlib.util.spec_from_file_location("indice_master", str(MASTER_PY_PATH))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["indice_master"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    master = mod.MasterIndice()
    log.info("Master carregado | proximo carimbo %s", master.proximo_carimbo)
    return master


def _carregar_indice_local() -> set[tuple[str, str]]:
    baixados: set[tuple[str, str]] = set()
    if not INDICE_LOCAL_PATH.exists():
        return baixados
    with open(INDICE_LOCAL_PATH, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            uc = (row.get("UC") or "").strip()
            mes_ref = (row.get("MES_REF") or "").strip()
            if uc and mes_ref:
                baixados.add((uc, mes_ref))
    return baixados


def _registrar_indice_local(
    indice_bb: str,
    uc: str,
    mes_ref: str,
    titular_id: str,
    titular_texto: str,
    perfil: str,
    arquivo: str,
) -> None:
    novo = not INDICE_LOCAL_PATH.exists()
    with open(INDICE_LOCAL_PATH, "a", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(
            f,
            fieldnames=[
                "INDICE",
                "UC",
                "MES_REF",
                "TITULAR_ID",
                "TITULAR_TEXTO",
                "PERFIL",
                "ARQUIVO",
                "DATA_DOWNLOAD",
            ],
        )
        if novo:
            w.writeheader()
        w.writerow(
            {
                "INDICE": indice_bb,
                "UC": uc.strip(),
                "MES_REF": mes_ref.strip(),
                "TITULAR_ID": titular_id.strip(),
                "TITULAR_TEXTO": titular_texto.strip(),
                "PERFIL": perfil.strip().upper(),
                "ARQUIVO": arquivo,
                "DATA_DOWNLOAD": datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
            }
        )


_MESES_PT = {
    "janeiro": "01", "fevereiro": "02", "março": "03", "abril": "04",
    "maio": "05", "junho": "06", "julho": "07", "agosto": "08",
    "setembro": "09", "outubro": "10", "novembro": "11", "dezembro": "12",
}


def _ucs_correspondem(uc_esperada: str, uc_na_pagina: str) -> bool:
    """Compara UCs ignorando zeros à esquerda e caracteres não-numéricos."""
    e = re.sub(r"\D", "", uc_esperada).lstrip("0") or "0"
    p = re.sub(r"\D", "", uc_na_pagina).lstrip("0") or "0"
    return e == p


def _normalizar_mes_ref_cpfl(valor: str) -> str:
    bruto = _texto_limpo(valor)
    m = re.search(r"\b(20\d{2})[/-](0[1-9]|1[0-2])\b", bruto)
    if m:
        return f"{m.group(2)}-{m.group(1)}"
    m = re.search(r"\b(0[1-9]|1[0-2])[/-](20\d{2})\b", bruto)
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    # "Junho de 2026" → "06-2026"
    m = re.search(r"([A-Za-záàâãéêíóôõúçÇ]+)\s+de\s+(20\d{2})", bruto, re.IGNORECASE)
    if m:
        nome = m.group(1).lower().strip()
        ano = m.group(2)
        num = _MESES_PT.get(nome)
        if num:
            return f"{num}-{ano}"
    return bruto


def _extrair_fatura_atual_segunda_via(driver: webdriver.Chrome) -> dict[str, str]:
    script = """
    const radio = document.querySelector("#ctl00_ContentPlaceHolder1_grdFaturas_ctl02_rbIDFAT")
      || document.querySelector("#ctl00_ContentPlaceHolder1_grdFaturas input[type='radio']")
      || document.querySelector("input[type='radio'][id*='rbIDFAT']");
    if (!radio) return {};
    const tr = radio.closest("tr");
    if (!tr) return {};
    const colunas = Array.from(tr.querySelectorAll("td,th"))
      .map((el) => (el.innerText || el.textContent || "").replace(/\\s+/g, " ").trim())
      .filter(Boolean);
    return {
      radio_id: radio.id || "",
      radio_name: radio.name || "",
      radio_checked: radio.checked ? "1" : "0",
      texto_linha: colunas.join(" | "),
      mes_ref: colunas[1] || "",
      vencimento: colunas[2] || "",
      tipo_debito: colunas[3] || "",
      valor: colunas[4] || "",
    };
    """
    try:
        item = driver.execute_script(script)
    except Exception:
        item = {}
    if not isinstance(item, dict):
        return {}
    return {k: _texto_limpo(str(v)) for k, v in item.items()}


def _mover_pdf_para_destino(pdf: Path, perfil: str, mes_ref: str, carimbo: str) -> Path:
    pasta_destino = DOWNLOAD_DIR / perfil.upper() / mes_ref
    pasta_destino.mkdir(parents=True, exist_ok=True)
    destino = pasta_destino / f"{carimbo}.pdf"
    if destino.exists():
        destino.unlink()
    shutil.move(str(pdf), str(destino))
    return destino


def _registrar_inventario(
    titular_id: str,
    titular_texto: str,
    perfil: str,
    ucs: list[dict],
) -> None:
    """Acumula todas as UCs encontradas no portal (independente de download)."""
    novo = not INVENTARIO_PATH.exists()
    ts = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    with open(INVENTARIO_PATH, "a", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=[
            "TITULAR_ID", "TITULAR_TEXTO", "PERFIL", "UC", "STATUS", "LINHA", "DATA_SCAN",
        ])
        if novo:
            w.writeheader()
        for item in ucs:
            w.writerow({
                "TITULAR_ID": titular_id.strip(),
                "TITULAR_TEXTO": titular_texto.strip(),
                "PERFIL": perfil.upper(),
                "UC": item.get("uc", "").strip(),
                "STATUS": item.get("status", "").strip(),
                "LINHA": _texto_limpo(item.get("linha", "")),
                "DATA_SCAN": ts,
            })


def _extrair_cnpj_do_titular(texto: str) -> str:
    m = re.search(r"\b\d{11,14}\b", _texto_limpo(texto))
    return m.group(0) if m else ""


def _priorizar_titulares(opcoes: list[dict]) -> list[dict]:
    def _score(opcao: dict) -> tuple[int, str]:
        texto = _texto_limpo(opcao.get("text", "")).upper()
        ident = _texto_limpo(opcao.get("id", ""))
        documento = _extrair_cnpj_do_titular(texto)

        if "BANCO DO BRASIL" in texto:
            prioridade = 0
        elif len(documento) == 14:
            prioridade = 1
        elif "S/A" in texto or "LTDA" in texto or "SA " in f"{texto} ":
            prioridade = 2
        elif len(documento) == 11:
            prioridade = 4
        else:
            prioridade = 3

        return (prioridade, ident or texto)

    return sorted(opcoes, key=_score)


def _pausa_humana(minimo: float = 0.4, maximo: float = 1.1) -> None:
    time.sleep(random.uniform(minimo, maximo))


def _temp_dir_para_worker() -> Path:
    """Diretório de download temporário isolado por worker (evita conflito de PDFs em paralelo)."""
    if _WORKER_ID == 0:
        return TEMP_DOWNLOAD_DIR
    return Path.home() / "AppData" / "Local" / f"cpfl_temp_{_WORKER_ID}"


def build_driver(headless: bool = False) -> webdriver.Chrome:
    import tempfile as _tempfile
    temp_dir = _temp_dir_para_worker()
    temp_dir.mkdir(parents=True, exist_ok=True)
    # Perfil Chrome isolado por processo — evita 'invalid session id' quando
    # múltiplas instâncias tentam usar o mesmo perfil padrão do Chrome.
    _profiles_root = Path.home() / "AppData" / "Local" / "cpfl_chrome_profiles"
    _profiles_root.mkdir(parents=True, exist_ok=True)
    profile_dir = Path(_tempfile.mkdtemp(prefix=f"cpfl_w{_WORKER_ID}_", dir=str(_profiles_root)))

    options = Options()
    options.add_argument(f"--user-data-dir={profile_dir}")
    options.add_argument("--window-size=1440,960")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-notifications")
    options.add_argument("--disable-popup-blocking")
    options.add_argument("--no-first-run")
    options.add_argument("--no-default-browser-check")
    options.add_argument("--remote-debugging-port=0")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    prefs = {
        "download.default_directory": str(temp_dir),
        "download.prompt_for_download": False,
        "download.directory_upgrade": True,
        "safebrowsing.enabled": True,
        "plugins.always_open_pdf_externally": True,
    }
    options.add_experimental_option("prefs", prefs)
    if headless:
        options.add_argument("--headless=new")

    try:
        driver = webdriver.Chrome(options=options)
    except Exception:
        import shutil as _shutil
        _shutil.rmtree(profile_dir, ignore_errors=True)
        raise
    try:
        driver.execute_cdp_cmd(
            "Page.setDownloadBehavior",
            {"behavior": "allow", "downloadPath": str(temp_dir)},
        )
    except Exception as exc:
        log.debug("Nao foi possivel configurar download via CDP: %s", exc)
    driver.set_page_load_timeout(90)
    driver._cpfl_profile_dir = profile_dir  # type: ignore[attr-defined]
    return driver


def _save_html(driver: webdriver.Chrome, prefixo: str) -> Path:
    SAIDA_DIR.mkdir(parents=True, exist_ok=True)
    path = SAIDA_DIR / f"{prefixo}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
    path.write_text(driver.page_source, encoding="utf-8")
    return path


def _save_screenshot(driver: webdriver.Chrome, prefixo: str) -> Path:
    SAIDA_DIR.mkdir(parents=True, exist_ok=True)
    path = SAIDA_DIR / f"{prefixo}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
    driver.save_screenshot(str(path))
    return path


def _save_texto(prefixo: str, conteudo: str, extensao: str = "html") -> Path:
    SAIDA_DIR.mkdir(parents=True, exist_ok=True)
    path = SAIDA_DIR / f"{prefixo}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.{extensao}"
    path.write_text(conteudo, encoding="utf-8")
    return path


def _coletar_resumo_tela(driver: webdriver.Chrome) -> str:
    try:
        body = driver.find_element(By.TAG_NAME, "body").text
    except Exception:
        body = ""
    linhas = [linha.strip() for linha in body.splitlines() if linha.strip()]
    trecho = "\n".join(linhas[:80])
    partes = [
        f"URL: {driver.current_url}",
        f"TITLE: {driver.title}",
        f"DIAGNOSTICO: {diagnosticar_tela_segunda_via(driver)}",
        "",
        "TRECHO DO TEXTO VISIVEL:",
        trecho,
    ]
    return "\n".join(partes).strip() + "\n"


def _snapshot_debug(driver: webdriver.Chrome, prefixo: str, incluir_resumo: bool = True) -> dict[str, Path]:
    artefatos = {
        "html": _save_html(driver, prefixo),
        "screenshot": _save_screenshot(driver, prefixo),
    }
    if incluir_resumo:
        artefatos["txt"] = _save_texto(f"{prefixo}_resumo", _coletar_resumo_tela(driver), extensao="txt")
    return artefatos


def _extrair_grade_segunda_via(driver: webdriver.Chrome) -> list[dict[str, str]]:
    script = """
    const tabelas = Array.from(document.querySelectorAll('#tbConsultaDeb table, #ctl00_ContentPlaceHolder1_grdFaturas, table'));
    const alvo = tabelas.find((tb) => tb.querySelector("input[type='radio']"));
    if (!alvo) return [];
    const linhas = Array.from(alvo.querySelectorAll('tr'));
    return linhas.map((tr, idx) => {
      const radio = tr.querySelector("input[type='radio']");
      const colunas = Array.from(tr.querySelectorAll('th,td')).map((el) => (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim()).filter(Boolean);
      return {
        indice: String(idx),
        possui_radio: radio ? '1' : '0',
        radio_id: radio ? (radio.id || '') : '',
        radio_name: radio ? (radio.name || '') : '',
        radio_value: radio ? (radio.value || '') : '',
        radio_checked: radio && radio.checked ? '1' : '0',
        texto: colunas.join(' | '),
      };
    }).filter((item) => item.possui_radio === '1' || item.texto);
    """
    try:
        resultado = driver.execute_script(script)
    except Exception:
        return []
    if not isinstance(resultado, list):
        return []
    linhas: list[dict[str, str]] = []
    for item in resultado:
        if not isinstance(item, dict):
            continue
        linhas.append({chave: _texto_limpo(str(valor)) for chave, valor in item.items()})
    return linhas


def _salvar_debug_grade_segunda_via(driver: webdriver.Chrome, prefixo: str) -> Path:
    linhas = _extrair_grade_segunda_via(driver)
    if not linhas:
        conteudo = "Nenhuma linha de grade com radio foi identificada na tela.\n"
    else:
        blocos = []
        for linha in linhas:
            blocos.append(
                " | ".join(
                    [
                        f"idx={linha.get('indice', '')}",
                        f"checked={linha.get('radio_checked', '')}",
                        f"id={linha.get('radio_id', '')}",
                        f"name={linha.get('radio_name', '')}",
                        f"value={linha.get('radio_value', '')}",
                        f"texto={linha.get('texto', '')}",
                    ]
                )
            )
        conteudo = "\n".join(blocos) + "\n"
    return _save_texto(prefixo, conteudo, extensao="txt")


def _arquivo_eh_pdf(path: Path) -> bool:
    try:
        with path.open("rb") as f:
            return f.read(5) == b"%PDF-"
    except Exception:
        return False


def _baixar_pdf_via_requests(driver: webdriver.Chrome, url_pdf: str) -> Path:
    """Baixa o PDF usando os cookies da sessão Selenium."""
    temp_dir = _temp_dir_para_worker()
    temp_dir.mkdir(parents=True, exist_ok=True)

    sess = requests.Session()
    for cookie in driver.get_cookies():
        try:
            sess.cookies.set(
                cookie.get("name", ""),
                cookie.get("value", ""),
                domain=cookie.get("domain"),
                path=cookie.get("path", "/"),
            )
        except Exception:
            continue

    headers = {
        "User-Agent": driver.execute_script("return navigator.userAgent;") or "Mozilla/5.0",
        "Referer": driver.current_url or "https://www.cpfl.com.br/",
        "Accept": "application/pdf,application/octet-stream;q=0.9,*/*;q=0.8",
    }
    log.info("MT requests direto para URL PDF: %s", url_pdf[:100])
    try:
        resp = sess.get(url_pdf, headers=headers, timeout=120, allow_redirects=True)
        content_type = (resp.headers.get("content-type") or "").lower()
        content = resp.content or b""
        log.info(
            "MT requests resposta: status=%s content-type=%s url_final=%s bytes=%s",
            resp.status_code,
            content_type or "(vazio)",
            resp.url,
            len(content),
        )
        resp.raise_for_status()

        if not content.startswith(b"%PDF-"):
            trecho = content[:300].decode("utf-8", errors="ignore").replace("\r", " ").replace("\n", " ")
            log.error("MT requests nao retornou PDF. Trecho inicial: %s", trecho)
            raise PortalPdfError(
                f"Resposta nao veio como PDF (status={resp.status_code}, content-type={content_type!r}, trecho={trecho!r})"
            )
    except Exception as exc:
        log.error("MT requests falhou ao baixar PDF: %s", exc)
        raise

    nome = f"MT_{int(time.time())}_{random.randint(1000, 9999)}.pdf"
    destino = temp_dir / nome
    destino.write_bytes(content)
    log.info("MT: PDF salvo via requests: %s", destino.name)
    return destino


def _clicar_robusto(driver: webdriver.Chrome, elemento) -> None:
    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", elemento)
    _pausa_humana(0.2, 0.5)
    try:
        elemento.click()
        return
    except Exception:
        pass
    driver.execute_script("arguments[0].click();", elemento)


def _texto_limpo(valor: str | None) -> str:
    if not valor:
        return ""
    return " ".join(str(valor).split())


def _digitar_rapido(driver: webdriver.Chrome, campo, valor: str) -> None:
    campo.send_keys(Keys.CONTROL, "a")
    campo.send_keys(Keys.DELETE)

    try:
        campo.send_keys(valor)
    except Exception:
        driver.execute_script(
            """
            arguments[0].focus();
            arguments[0].value = arguments[1];
            arguments[0].dispatchEvent(new Event('input', { bubbles: true }));
            arguments[0].dispatchEvent(new Event('change', { bubbles: true }));
            """,
            campo,
            valor,
        )

    preenchido = (campo.get_attribute("value") or "").strip()
    if preenchido != valor:
        driver.execute_script(
            """
            arguments[0].focus();
            arguments[0].value = arguments[1];
            arguments[0].dispatchEvent(new Event('input', { bubbles: true }));
            arguments[0].dispatchEvent(new Event('change', { bubbles: true }));
            """,
            campo,
            valor,
        )


def _preencher(driver: webdriver.Chrome, by: By, locator: str, valor: str, nome: str) -> None:
    campo = WebDriverWait(driver, TIMEOUT).until(EC.visibility_of_element_located((by, locator)))
    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", campo)
    _pausa_humana(0.2, 0.5)
    _digitar_rapido(driver, campo, valor)
    log.info("Campo %s preenchido.", nome)


def _preencher_primeiro(
    driver: webdriver.Chrome,
    candidatos: list[tuple[By, str]],
    valor: str,
    nome: str,
    timeout_por_candidato: int = 8,
) -> None:
    ultimo_erro = None
    for by, locator in candidatos:
        try:
            campo = WebDriverWait(driver, timeout_por_candidato).until(
                EC.presence_of_element_located((by, locator))
            )
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", campo)
            _pausa_humana(0.2, 0.5)
            _digitar_rapido(driver, campo, valor)
            log.info("Campo %s preenchido.", nome)
            return
        except Exception as exc:
            ultimo_erro = exc
            continue
    raise TimeoutException(f"Campo {nome} nao encontrado. Ultimo erro: {ultimo_erro}")


def _achar_botao_submit(driver: webdriver.Chrome):
    candidatos = [
        (By.ID, "next"),
        (By.ID, "continue"),
        (By.CSS_SELECTOR, "button[type='submit']"),
        (By.CSS_SELECTOR, "input[type='submit']"),
        (By.XPATH, "//button[contains(normalize-space(.), 'Entrar')]"),
        (By.XPATH, "//button[contains(normalize-space(.), 'Login')]"),
        (By.XPATH, "//button[contains(normalize-space(.), 'Avancar')]"),
    ]
    ultimo = None
    for by, locator in candidatos:
        try:
            ultimo = WebDriverWait(driver, 5).until(EC.element_to_be_clickable((by, locator)))
            return ultimo
        except Exception:
            continue
    raise TimeoutException("Botao de login nao encontrado na tela B2C.") from ultimo


def fazer_login(driver: webdriver.Chrome, usuario: str, senha: str) -> None:
    log.info("Abrindo login CPFL/RGE...")
    driver.get(URL_LOGIN)

    _preencher(driver, By.ID, "signInName", usuario, "e-mail")
    log.info("Aguardando campo senha...")
    _preencher_primeiro(
        driver,
        [
            (By.ID, "password"),
            (By.NAME, "Password"),
            (By.CSS_SELECTOR, "input[type='password']"),
            (By.CSS_SELECTOR, "input[placeholder='Senha']"),
            (By.XPATH, "//input[contains(@aria-label, 'Senha')]"),
        ],
        senha,
        "senha",
    )

    botao = _achar_botao_submit(driver)
    _clicar_robusto(driver, botao)
    log.info("Login enviado.")


def aguardar_pos_login(driver: webdriver.Chrome) -> None:
    def _saiu_da_b2c(drv: webdriver.Chrome) -> bool:
        url = (drv.current_url or "").lower()
        pagina = (drv.page_source or "").lower()

        if "cpfl.com.br" in url and "b2clogin.com" not in url:
            return True
        if "/agencia/area-cliente/cadastro" in url:
            return True
        if "qual perfil você deseja acessar?".lower() in pagina:
            return True
        if "média e alta tensão".lower() in pagina or "media e alta tensao" in pagina:
            return True
        if drv.find_elements(By.ID, "edit-field-1633fada-a2b6-4c70-aa41-6907653f7c41"):
            return True
        if drv.find_elements(By.ID, "edit-parceiro-negocio"):
            return True
        if drv.find_elements(By.ID, "signInName"):
            return False
        return False

    log.info("Aguardando redirecionamento apos login...")
    WebDriverWait(driver, TIMEOUT).until(_saiu_da_b2c)
    log.info("Redirecionamento apos login confirmado: %s", driver.current_url)


def aceitar_cookies(driver: webdriver.Chrome) -> bool:
    candidatos = [
        (By.ID, "onetrust-accept-btn-handler"),
        (By.CSS_SELECTOR, "#onetrust-banner-sdk #onetrust-accept-btn-handler"),
        (
            By.XPATH,
            "//button[contains(normalize-space(.), 'Aceitar') or contains(normalize-space(.), 'Accept')]",
        ),
    ]
    for by, locator in candidatos:
        try:
            botao = WebDriverWait(driver, 8).until(EC.element_to_be_clickable((by, locator)))
            _clicar_robusto(driver, botao)
            log.info("Cookies aceitos.")
            _pausa_humana(0.5, 1.0)
            return True
        except Exception:
            continue
    log.info("Banner de cookies nao apareceu.")
    return False


def _esta_na_tela_cadastro_ou_perfil(driver: webdriver.Chrome) -> bool:
    url = (driver.current_url or "").lower()
    pagina = (driver.page_source or "").lower()
    # Excluir páginas que não são cadastro mas contêm termos como "baixa tensão"
    if "agencia-virtual/pagina-inicial" in url or "conta-completa" in url:
        return False
    if "/agencia/area-cliente/cadastro" in url or "selecionar-perfil-instalacao" in url:
        return True
    if driver.find_elements(By.ID, "edit-parceiro-negocio"):
        return True
    if driver.find_elements(By.CSS_SELECTOR, ".select2-selection--single"):
        return True
    if "qual perfil voc" in pagina and "selecionar perfil" in pagina:
        return True
    if "mÃ©dia e alta tensÃ£o".lower() in pagina or "media e alta tensao" in pagina:
        return True
    if "baixa tens" in pagina or "grupo b" in pagina:
        return True
    if driver.find_elements(By.ID, "edit-button--2"):
        return True
    return False


def garantir_tela_cadastro(driver: webdriver.Chrome, perfil: str) -> None:
    if len(driver.window_handles) > 1:
        principal = driver.window_handles[0]
        for handle in list(driver.window_handles)[1:]:
            try:
                driver.switch_to.window(handle)
                driver.close()
            except Exception:
                continue
        driver.switch_to.window(principal)

    urls_navegacao = [URL_CADASTRO, URL_RESELECIONAR] if (perfil or "").lower() == "mt" else [URL_RESELECIONAR, URL_CADASTRO]
    for tentativa in range(1, 4):
        if not _esta_na_tela_cadastro_ou_perfil(driver):
            url_alvo = urls_navegacao[(tentativa - 1) % len(urls_navegacao)]
            log.info("Retornando para a tela de cadastro/perfil (%s/3) via %s...", tentativa, url_alvo)
            try:
                driver.get(url_alvo)
            except Exception:
                driver.execute_script("window.location.href = arguments[0];", url_alvo)
            _pausa_humana(2.5, 3.5)

        try:
            WebDriverWait(driver, TIMEOUT).until(_esta_na_tela_cadastro_ou_perfil)
        except Exception:
            continue

        aceitar_cookies(driver)
        if driver.find_elements(By.ID, "edit-parceiro-negocio") or driver.find_elements(By.CSS_SELECTOR, ".select2-selection--single"):
            log.info("Tela de cadastro/lista de titulares pronta.")
            return
        if not driver.find_elements(By.ID, "edit-parceiro-negocio"):
            try:
                selecionar_perfil_consumo(driver, perfil)
                WebDriverWait(driver, TIMEOUT).until(
                    lambda d: d.find_elements(By.ID, "edit-parceiro-negocio")
                    or d.find_elements(By.CSS_SELECTOR, ".select2-selection--single")
                )
            except Exception as exc_perfil:
                # Conta sem card de perfil (ex: corporativa) — vai direto para /cadastro
                log.info("Selecao de perfil nao encontrada (%s); navegando para %s.", exc_perfil, URL_CADASTRO)
                driver.get(URL_CADASTRO)
                _pausa_humana(1.5, 2.0)
                continue
        log.info("Tela de cadastro/lista de titulares pronta.")
        return

    artefatos = _snapshot_debug(driver, "cpfl_rge_falha_retorno_cadastro")
    log.error("HTML de falha ao retornar ao cadastro salvo em: %s", artefatos["html"])
    log.error("Screenshot de falha ao retornar ao cadastro salvo em: %s", artefatos["screenshot"])
    if "txt" in artefatos:
        log.error("Resumo da tela salvo em: %s", artefatos["txt"])
    raise TimeoutException("Nao foi possivel retornar para a tela de cadastro/lista de titulares.")


def _reselecionar_rapido_da_pagina_inicial(driver: webdriver.Chrome) -> bool:
    """Se estamos em pagina-inicial, clica 'Selecione outra instalacao' e aguarda
    selecionar-perfil-instalacao com as UCs do mesmo titular ja carregadas.
    Evita reload completo + nova chamada API de titular (~25s economizados por UC).
    Retorna True se conseguiu; False se precisa do caminho completo."""
    url = driver.current_url or ""
    if "agencia-virtual/pagina-inicial" not in url:
        return False
    candidatos = [
        (By.XPATH, "//a[contains(normalize-space(.), 'Selecione outra instala')]", "texto Selecione outra instalacao"),
        (By.XPATH, "//a[contains(normalize-space(.), 'outra instala')]", "outra instalacao generico"),
        (By.XPATH, "//a[contains(normalize-space(.), 'Trocar instala')]", "texto Trocar instalacao"),
        (By.XPATH, "//a[contains(@href, 'selecionar-perfil-instalacao')]", "href selecionar-perfil-instalacao"),
    ]
    for by, locator, descricao in candidatos:
        try:
            link = WebDriverWait(driver, 3).until(EC.element_to_be_clickable((by, locator)))
            _clicar_robusto(driver, link)
            log.info("Caminho rapido: '%s' clicado.", descricao)
            _pausa_humana(0.8, 1.5)
            _aguardar_instalacoes_dom(driver, timeout=15)
            log.info("selecionar-perfil-instalacao pronta via caminho rapido (titular ja selecionado).")
            return True
        except Exception as exc:
            log.debug("Caminho rapido falhou (%s): %s", descricao, exc)
            continue
    log.info("Caminho rapido indisponivel em pagina-inicial; seguindo pelo fluxo completo.")
    return False


def _obter_config_perfil(perfil: str) -> dict:
    perfil_norm = (perfil or "mt").strip().lower()
    if perfil_norm == "bt":
        return {
            "codigo": "bt",
            "label": "Baixa tensao",
            "candidatos_clickaveis": [
                (
                    By.ID,
                    "edit-field-102fc749-3b8d-4a09-b754-4234493f89c3",
                    "card exato da Baixa tensao",
                ),
                (
                    By.CSS_SELECTOR,
                    "div.tile.with-button.administrativo",
                    "tile administrativo",
                ),
                (
                    By.XPATH,
                    (
                        "//*[contains(normalize-space(.), 'Baixa tens') "
                        "or contains(normalize-space(.), 'Grupo B')]"
                        "/ancestor::div[contains(@class, 'tile')][1]"
                    ),
                    "tile pelo titulo Baixa tensao/Grupo B",
                ),
            ],
            "candidatos_fallback": [
                (
                    By.CSS_SELECTOR,
                    "input#edit-button[name='id_102fc749-3b8d-4a09-b754-4234493f89c3'][value='Entrar']",
                    "input submit exato da Baixa tensao",
                ),
                (By.ID, "edit-button", "ID edit-button"),
                (
                    By.XPATH,
                    (
                        "//*[contains(normalize-space(.), 'Baixa tens') "
                        "or contains(normalize-space(.), 'Grupo B')]"
                        "/ancestor::*[self::div or self::section or self::article or self::form][1]"
                        "//*[@id='edit-button--2' or @value='Entrar']"
                    ),
                    "botao Entrar dentro do card de Baixa tensao",
                ),
                (
                    By.XPATH,
                    (
                        "//*[contains(normalize-space(.), 'Baixa tens') "
                        "or contains(normalize-space(.), 'Grupo B')]"
                        "/ancestor::*[self::div or self::section or self::article][1]"
                        "//*[self::button or self::input][contains(normalize-space(.), 'Entrar') or @value='Entrar']"
                    ),
                    "submit no card de Baixa tensao",
                ),
            ],
        }

    return {
        "codigo": "mt",
        "label": "Media e alta tensao",
        "candidatos_clickaveis": [
            (
                By.ID,
                "edit-field-1633fada-a2b6-4c70-aa41-6907653f7c41",
                "card da Media e alta tensao",
            ),
            (
                By.CSS_SELECTOR,
                "div.tile.with-button.empresarial",
                "tile empresarial",
            ),
            (
                By.XPATH,
                (
                    "//*[contains(normalize-space(.), 'MÃ©dia e alta tensÃ£o') "
                    "or contains(normalize-space(.), 'Media e alta tensao')]"
                    "/ancestor::div[contains(@class, 'tile')][1]"
                ),
                "tile pelo titulo Media e alta tensao",
            ),
        ],
        "candidatos_fallback": [
            (
                By.CSS_SELECTOR,
                "input#edit-button--2[name='id_1633fada-a2b6-4c70-aa41-6907653f7c41'][value='Entrar']",
                "input submit exato da Media e alta tensao",
            ),
            (By.ID, "edit-button--2", "ID edit-button--2"),
            (
                By.XPATH,
                (
                    "//*[contains(normalize-space(.), 'MÃ©dia e alta tensÃ£o') "
                    "or contains(normalize-space(.), 'Media e alta tensao')]"
                    "/ancestor::*[self::div or self::section or self::article or self::form][1]"
                    "//*[@id='edit-button--2' or @value='Entrar']"
                ),
                "botao Entrar dentro do card de Media e alta tensao",
            ),
            (
                By.XPATH,
                (
                    "//*[contains(normalize-space(.), 'MÃ©dia e alta tensÃ£o') "
                    "or contains(normalize-space(.), 'Media e alta tensao')]"
                    "/ancestor::*[self::div or self::section or self::article][1]"
                    "//*[self::button or self::input][contains(normalize-space(.), 'Entrar') or @value='Entrar']"
                ),
                "submit no card de Media e alta tensao",
            ),
            (
                By.XPATH,
                "//button[@id='edit-button--2' or contains(normalize-space(.), 'Entrar')]",
                "botao Entrar da tela de perfil",
            ),
        ],
    }


def selecionar_perfil_consumo(driver: webdriver.Chrome, perfil: str = "mt") -> None:
    config = _obter_config_perfil(perfil)
    candidatos_clickaveis = config["candidatos_clickaveis"]
    candidatos_fallback = config["candidatos_fallback"]

    log.info("Aguardando card de %s...", config["label"])

    ultimo_erro = None
    for by, locator, descricao in candidatos_clickaveis:
        try:
            card = WebDriverWait(driver, 8).until(EC.presence_of_element_located((by, locator)))
            _clicar_robusto(driver, card)
            log.info("Perfil %s selecionado (%s).", config["label"], descricao)
            _pausa_humana(0.8, 1.5)
            return
        except Exception as exc:
            ultimo_erro = exc
            continue

    log.info("Card de %s nao respondeu ao clique; tentando submit interno do perfil.", config["label"])
    for by, locator, descricao in candidatos_fallback:
        try:
            botao = WebDriverWait(driver, 8).until(EC.presence_of_element_located((by, locator)))
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", botao)
            _pausa_humana(0.2, 0.5)
            try:
                _clicar_robusto(driver, botao)
            except Exception:
                driver.execute_script("arguments[0].click();", botao)
            log.info("Perfil %s selecionado (%s).", config["label"], descricao)
            _pausa_humana(0.8, 1.5)
            return
        except Exception as exc:
            ultimo_erro = exc
            continue

    # Fallback JS para telas em que o card existe, mas o Selenium nao consegue
    # interagir por sobreposicao/transicao do frontend.
    try:
        codigo = config["codigo"]
        resultado = driver.execute_script(
            """
            const codigo = arguments[0];
            const ids = codigo === "mt"
              ? ["edit-field-1633fada-a2b6-4c70-aa41-6907653f7c41", "edit-button--2"]
              : ["edit-field-102fc749-3b8d-4a09-b754-4234493f89c3", "edit-button"];
            for (const id of ids) {
              const el = document.getElementById(id);
              if (el) {
                el.scrollIntoView({block: 'center'});
                el.click();
                return "id:" + id;
              }
            }
            const tile = codigo === "mt"
              ? document.querySelector("div.tile.with-button.empresarial")
              : document.querySelector("div.tile.with-button.administrativo");
            if (tile) {
              tile.scrollIntoView({block: 'center'});
              tile.click();
              return "tile";
            }
            return "";
            """,
            codigo,
        )
        if resultado:
            log.info("Perfil %s selecionado via JS (%s).", config["label"], resultado)
            _pausa_humana(1.0, 2.0)
            return
    except Exception as exc:
        ultimo_erro = exc

    artefatos = _snapshot_debug(driver, f"cpfl_rge_falha_perfil_{config['codigo']}")
    log.error("HTML de falha ao selecionar perfil salvo em: %s", artefatos["html"])
    log.error("Screenshot de falha ao selecionar perfil salvo em: %s", artefatos["screenshot"])
    if "txt" in artefatos:
        log.error("Resumo da tela salvo em: %s", artefatos["txt"])
    raise PerfilIndisponivelError(
        f"Botao de {config['label']} nao encontrado na pagina de perfil. Ultimo erro: {ultimo_erro}"
    )


def selecionar_media_tensao(driver: webdriver.Chrome) -> None:
    selecionar_perfil_consumo(driver, "mt")
    return


def _selecionar_media_tensao_legado(driver: webdriver.Chrome) -> None:
    candidatos_clickaveis = [
        (
            By.ID,
            "edit-field-1633fada-a2b6-4c70-aa41-6907653f7c41",
            "card da Media e alta tensao",
        ),
        (
            By.CSS_SELECTOR,
            "div.tile.with-button.empresarial",
            "tile empresarial",
        ),
        (
            By.XPATH,
            (
                "//*[contains(normalize-space(.), 'Média e alta tensão') "
                "or contains(normalize-space(.), 'Media e alta tensao')]"
                "/ancestor::div[contains(@class, 'tile')][1]"
            ),
            "tile pelo titulo Media e alta tensao",
        ),
    ]

    candidatos_fallback = [
        (
            By.CSS_SELECTOR,
            "input#edit-button--2[name='id_1633fada-a2b6-4c70-aa41-6907653f7c41'][value='Entrar']",
            "input submit exato da Media e alta tensao",
        ),
        (By.ID, "edit-button--2", "ID edit-button--2"),
        (
            By.XPATH,
            (
                "//*[contains(normalize-space(.), 'Média e alta tensão') "
                "or contains(normalize-space(.), 'Media e alta tensao')]"
                "/ancestor::*[self::div or self::section or self::article or self::form][1]"
                "//*[@id='edit-button--2' or @value='Entrar']"
            ),
            "botao Entrar dentro do card de Media e alta tensao",
        ),
        (
            By.XPATH,
            (
                "//*[contains(normalize-space(.), 'Média e alta tensão') "
                "or contains(normalize-space(.), 'Media e alta tensao')]"
                "/ancestor::*[self::div or self::section or self::article][1]"
                "//*[self::button or self::input][contains(normalize-space(.), 'Entrar') or @value='Entrar']"
            ),
            "card Media e alta tensao",
        ),
        (
            By.XPATH,
            "//button[@id='edit-button--2' or contains(normalize-space(.), 'Entrar')]",
            "botao Entrar da tela de perfil",
        ),
    ]

    log.info("Aguardando card de Media e alta tensao...")

    ultimo_erro = None
    for by, locator, descricao in candidatos_clickaveis:
        try:
            card = WebDriverWait(driver, 8).until(EC.presence_of_element_located((by, locator)))
            _clicar_robusto(driver, card)
            log.info("Perfil Media e alta tensao selecionado (%s).", descricao)
            _pausa_humana(0.8, 1.5)
            return
        except Exception as exc:
            ultimo_erro = exc
            continue

    log.info("Card nao respondeu ao clique; tentando submit interno do perfil.")
    for by, locator, descricao in candidatos_fallback:
        try:
            botao = WebDriverWait(driver, 8).until(EC.presence_of_element_located((by, locator)))
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", botao)
            _pausa_humana(0.2, 0.5)
            try:
                _clicar_robusto(driver, botao)
            except Exception:
                driver.execute_script("arguments[0].click();", botao)
            log.info("Perfil Media e alta tensao selecionado (%s).", descricao)
            _pausa_humana(0.8, 1.5)
            return
        except Exception as exc:
            ultimo_erro = exc
            continue

    raise TimeoutException(
        f"Botao de Media e alta tensao nao encontrado na pagina de perfil. Ultimo erro: {ultimo_erro}"
    )


def _buscar_json_no_contexto(driver: webdriver.Chrome, url: str) -> dict:
    resultado = driver.execute_async_script(
        """
        const url = arguments[0];
        const done = arguments[arguments.length - 1];
        fetch(url, {
            credentials: 'include',
            headers: {
                'Accept': 'application/json, text/plain, */*',
                'X-Requested-With': 'XMLHttpRequest'
            }
        })
        .then(async (resp) => {
            const text = await resp.text();
            let data = null;
            try {
                data = JSON.parse(text);
            } catch (e) {}
            done({ ok: resp.ok, status: resp.status, text, data });
        })
        .catch((err) => done({ ok: false, error: String(err) }));
        """,
        url,
    )
    return resultado


def _coletar_opcoes_select2(payload) -> list[dict]:
    opcoes: list[dict] = []

    def visitar(no) -> None:
        if isinstance(no, dict):
            chaves_id = ["id", "value", "codigo", "code"]
            chaves_texto = ["text", "label", "descricao", "description", "nome", "name"]
            valor_id = next((no.get(ch) for ch in chaves_id if no.get(ch) not in (None, "")), None)
            valor_texto = next((no.get(ch) for ch in chaves_texto if no.get(ch) not in (None, "")), None)
            if valor_id is not None or valor_texto is not None:
                opcoes.append(
                    {
                        "id": _texto_limpo(valor_id),
                        "text": _texto_limpo(valor_texto or valor_id),
                    }
                )
            for valor in no.values():
                visitar(valor)
        elif isinstance(no, list):
            for item in no:
                visitar(item)

    visitar(payload)

    vistos: set[tuple[str, str]] = set()
    unicas: list[dict] = []
    for opcao in opcoes:
        chave = (opcao["id"], opcao["text"])
        if chave in vistos:
            continue
        vistos.add(chave)
        if opcao["id"] or opcao["text"]:
            unicas.append(opcao)
    return unicas


def obter_titulares(driver: webdriver.Chrome) -> list[dict]:
    campo = WebDriverWait(driver, TIMEOUT).until(
        EC.presence_of_element_located((By.ID, "edit-parceiro-negocio"))
    )
    data_url = campo.get_attribute("data-url") or ""
    if not data_url:
        log.info("Campo do titular sem data-url de consulta.")
        return []

    url = urljoin(driver.current_url, data_url)
    resposta = _buscar_json_no_contexto(driver, url)
    if not resposta.get("ok"):
        log.info("Falha ao consultar titulares via endpoint: %s", resposta)
        return []

    payload = resposta.get("data")
    if payload is None:
        trecho = _texto_limpo((resposta.get("text") or "")[:300])
        log.info("Endpoint de titulares nao retornou JSON util. Trecho: %s", trecho)
        return []

    opcoes = _priorizar_titulares(_coletar_opcoes_select2(payload))
    if opcoes:
        log.info("Titulares retornados pelo endpoint: %s", len(opcoes))
        log.info("Titulares priorizados para o lote:")
        for idx, opcao in enumerate(opcoes[:5], start=1):
            log.info("  %s. %s | %s", idx, opcao.get("id", ""), opcao.get("text", ""))
    return opcoes


def selecionar_titular(driver: webdriver.Chrome, alvo: dict | None = None) -> dict:
    log.info("Aguardando seletor do titular...")
    opcoes = obter_titulares(driver)
    if alvo is None and opcoes:
        alvo = opcoes[0]

    seletor = WebDriverWait(driver, TIMEOUT).until(
        EC.element_to_be_clickable((By.CSS_SELECTOR, ".select2-selection--single"))
    )
    _clicar_robusto(driver, seletor)
    _pausa_humana(0.5, 1.0)

    campo_busca = None
    try:
        campo_busca = WebDriverWait(driver, 6).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "input.select2-search__field"))
        )
    except Exception:
        campo_busca = None

    texto_alvo = ""
    if alvo:
        texto_alvo = alvo.get("text") or alvo.get("id") or ""
    texto_busca = texto_alvo
    cnpj_busca = ""
    if texto_alvo:
        m = re.search(r"\b\d{11,14}\b", texto_alvo)
        if m:
            cnpj_busca = m.group(0)
            texto_busca = cnpj_busca
    if texto_busca and campo_busca is not None:
        campo_busca.clear()
        campo_busca.send_keys(texto_busca)
        _pausa_humana(0.8, 1.3)

    try:
        if texto_alvo:
            xpath = (
                "//li[contains(@class, 'select2-results__option') and "
                "not(contains(@class, 'loading-results')) and "
                "not(@aria-disabled='true') and "
                f"(contains(normalize-space(.), {json.dumps(texto_alvo)}) "
                + (f"or contains(normalize-space(.), {json.dumps(cnpj_busca)})" if cnpj_busca else "")
                + ")]"
            )
            opcao = WebDriverWait(driver, 8).until(EC.element_to_be_clickable((By.XPATH, xpath)))
        else:
            opcao = WebDriverWait(driver, 8).until(
                EC.element_to_be_clickable(
                    (
                        By.XPATH,
                        "//li[contains(@class, 'select2-results__option') and "
                        "not(contains(@class, 'loading-results')) and "
                        "not(@aria-disabled='true')]",
                    )
                )
            )
        escolhido = _texto_limpo(opcao.text)
        _clicar_robusto(driver, opcao)
        log.info("Titular selecionado: %s", escolhido or texto_alvo or "primeira opcao")
        return {
            "id": alvo.get("id", "") if alvo else "",
            "text": escolhido or texto_alvo or "primeira opcao",
        }
    except Exception:
        if campo_busca is not None:
            campo_busca.send_keys(Keys.ARROW_DOWN)
            _pausa_humana(0.2, 0.4)
            campo_busca.send_keys(Keys.ENTER)
            _pausa_humana(0.6, 1.0)
            selecionado = _texto_limpo(
                driver.execute_script(
                    "return document.querySelector('.select2-selection__rendered')?.textContent || '';"
                )
            )
            if selecionado and "Digite aqui o CPF ou CNPJ" not in selecionado:
                log.info("Titular selecionado via teclado: %s", selecionado)
                return {
                    "id": alvo.get("id", "") if alvo else "",
                    "text": selecionado,
                }

        if alvo:
            script = """
            const valorId = arguments[0];
            const valorTexto = arguments[1];
            const input = document.querySelector('#edit-parceiro-negocio');
            const select = document.querySelector('select.select2-hidden-accessible');
            const rendered = document.querySelector('.select2-selection__rendered');
            if (input) {
                input.value = valorId;
                input.dispatchEvent(new Event('input', { bubbles: true }));
                input.dispatchEvent(new Event('change', { bubbles: true }));
            }
            if (select) {
                select.innerHTML = '';
                const opt = document.createElement('option');
                opt.value = valorId;
                opt.text = valorTexto;
                opt.selected = true;
                select.appendChild(opt);
                select.value = valorId;
                if (window.jQuery) {
                    window.jQuery(select).trigger('change');
                } else {
                    select.dispatchEvent(new Event('change', { bubbles: true }));
                }
            }
            if (rendered) {
                rendered.textContent = valorTexto;
                rendered.title = valorTexto;
                rendered.classList.remove('select2-selection__placeholder');
            }
            return {
                inputValue: input ? input.value : null,
                selectValue: select ? select.value : null,
                renderedText: rendered ? rendered.textContent : null,
            };
            """
            estado = driver.execute_script(script, alvo.get("id", ""), alvo.get("text", ""))
            log.info(
                "Titular preenchido via JS. input=%s | select=%s | texto=%s",
                estado.get("inputValue"),
                estado.get("selectValue"),
                _texto_limpo(estado.get("renderedText")),
            )
            return {
                "id": alvo.get("id", ""),
                "text": alvo.get("text") or alvo.get("id") or "primeira opcao",
            }
        raise


def selecionar_primeiro_titular(driver: webdriver.Chrome) -> dict:
    return selecionar_titular(driver, None)


def consultar_instalacoes_titular(driver: webdriver.Chrome, parceiro_id: str) -> str:
    form_build_id = driver.find_element(By.NAME, "form_build_id").get_attribute("value")
    form_id = driver.find_element(By.NAME, "form_id").get_attribute("value")
    url_ajax = urljoin(driver.current_url, "/agencia/area-cliente/cadastro?ajax_form=1")

    resultado = driver.execute_async_script(
        """
        const url = arguments[0];
        const parceiroId = arguments[1];
        const formBuildId = arguments[2];
        const formId = arguments[3];
        const done = arguments[arguments.length - 1];
        const body = new URLSearchParams();
        body.set('parceiro_negocio', parceiroId);
        body.set('form_build_id', formBuildId);
        body.set('form_id', formId);
        body.set('_drupal_ajax', '1');
        body.set('_triggering_element_name', 'parceiro_negocio');
        fetch(url, {
            method: 'POST',
            credentials: 'include',
            headers: {
                'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
                'Accept': 'application/json, text/javascript, */*; q=0.01',
                'X-Requested-With': 'XMLHttpRequest'
            },
            body: body.toString()
        })
        .then(async (resp) => {
            const text = await resp.text();
            let data = null;
            try {
                data = JSON.parse(text);
            } catch (e) {}
            done({ ok: resp.ok, status: resp.status, text, data });
        })
        .catch((err) => done({ ok: false, error: String(err) }));
        """,
        url_ajax,
        parceiro_id,
        form_build_id,
        form_id,
    )

    if not resultado.get("ok"):
        raise RuntimeError(f"Falha no AJAX de instalacoes: {resultado}")

    payload = resultado.get("data")
    if not isinstance(payload, list):
        trecho = _texto_limpo((resultado.get("text") or "")[:500])
        raise RuntimeError(f"Resposta AJAX inesperada para instalacoes. Trecho: {trecho}")

    fragmentos: list[str] = []
    for item in payload:
        if isinstance(item, dict) and isinstance(item.get("data"), str):
            fragmentos.append(item["data"])

    html_fragmento = "\n".join(fragmentos).strip()
    if not html_fragmento:
        raise RuntimeError("AJAX de instalacoes retornou sem fragmento HTML.")
    return html_fragmento


def aplicar_instalacoes_no_dom(driver: webdriver.Chrome, html_fragmento: str) -> None:
    driver.execute_script(
        """
        const wrapper = document.querySelector('#instalacoes-wrapper');
        if (!wrapper) {
            throw new Error('Wrapper de instalacoes nao encontrado no DOM.');
        }
        wrapper.innerHTML = arguments[0];
        """,
        html_fragmento,
    )
    _pausa_humana(0.6, 1.0)


def extrair_ucs_de_html(html_fragmento: str) -> list[dict]:
    """
    Usa bs4 para extrair UCs do fragmento HTML do portal CPFL.
    Estrutura real: <input name="instalacao" value="UC_NUM" ...>
                    <div class="bt-ativo-instalacao">Ativa</div>
    """
    soup = BeautifulSoup(html_fragmento, "html.parser")
    resultados: list[dict] = []
    vistos: set[str] = set()
    for radio in soup.find_all("input", attrs={"name": "instalacao", "type": "radio"}):
        uc = (radio.get("value") or "").strip()
        if not uc or uc in vistos:
            continue
        vistos.add(uc)
        disabled = radio.has_attr("disabled")
        radio_id = radio.get("id", "")
        label = soup.find("label", {"for": radio_id}) if radio_id else None
        texto_label = label.get_text(" ", strip=True) if label else ""
        if label:
            inativo_div = label.find("div", class_="bt-inativo-instalacao")
            status = "INATIVA" if (inativo_div or disabled) else "ATIVA"
        else:
            status = "INATIVA" if disabled else "ATIVA"
        resultados.append({"uc": uc, "status": status, "linha": texto_label})
    return resultados


def _aguardar_instalacoes_dom(driver: webdriver.Chrome, timeout: int = 20) -> None:
    """Aguarda pelo menos um radio de instalação aparecer no DOM e o spinner desaparecer."""
    WebDriverWait(driver, timeout).until(
        lambda d: d.find_elements(By.CSS_SELECTOR, "input[name='instalacao']")
    )
    # Aguarda o overlay de carregamento sumir antes de interagir com os radios
    try:
        WebDriverWait(driver, 10).until(
            lambda d: not d.find_elements(By.CSS_SELECTOR, "div.loader:not(.hide)")
        )
    except Exception:
        pass
    _pausa_humana(0.3, 0.6)


def _tem_proxima_pagina(driver: webdriver.Chrome) -> bool:
    try:
        btn = driver.find_element(By.CSS_SELECTOR, "input.next-button")
        classes = btn.get_attribute("class") or ""
        return btn.is_enabled() and "disabled" not in classes
    except Exception:
        return False


def coletar_todas_ucs_paginado(driver: webdriver.Chrome) -> list[dict]:
    """Pagina por todas as páginas de instalações e coleta UCs via bs4."""
    todas: list[dict] = []
    pagina = 1
    while True:
        html = driver.execute_script(
            "return document.querySelector('#edit-items')?.outerHTML"
            " || document.querySelector('#instalacoes-results-wrapper')?.innerHTML || '';"
        )
        ucs = extrair_ucs_de_html(html)
        todas.extend(ucs)
        log.info("Pagina %s: %s UCs | total acumulado: %s", pagina, len(ucs), len(todas))
        if not _tem_proxima_pagina(driver):
            break
        try:
            btn = driver.find_element(By.CSS_SELECTOR, "input.next-button")
            _clicar_robusto(driver, btn)
            _pausa_humana(1.5, 2.5)
            _aguardar_instalacoes_dom(driver)
        except Exception as exc:
            log.warning("Paginação interrompida: %s", exc)
            break
        pagina += 1
    return todas


def navegar_para_uc(driver: webdriver.Chrome, uc_valor: str) -> dict:
    """Busca a UC nas páginas de instalações (paginando se necessário) e seleciona o radio."""
    pagina = 1
    while True:
        radio = None
        label = None
        texto_label = ""
        try:
            for _sel in [
                f"input[name='instalacao'][value='{uc_valor}']",
                f"#instalacao-{uc_valor}",
                f"input[type='radio'][value='{uc_valor}']",
            ]:
                _encontrados = driver.find_elements(By.CSS_SELECTOR, _sel)
                if _encontrados:
                    radio = _encontrados[0]
                    break
            if radio:
                label_id = radio.get_attribute("id") or ""
                label = driver.find_element(By.CSS_SELECTOR, f"label[for='{label_id}']") if label_id else None
                try:
                    texto_label = _texto_limpo(label.text) if label else ""
                except Exception:
                    texto_label = ""
        except Exception:
            radio = None

        if radio is not None:
            # Setar checked=true via JS sem disparar eventos — clicar no label ou
            # disparar change/click aciona o AJAX Drupal que recarrega a lista e
            # desmarca o radio. A submissão do form (btn-buscar) lê radio.checked
            # diretamente do DOM, então apenas setar a propriedade já é suficiente.
            driver.execute_script("arguments[0].checked = true;", radio)
            _pausa_humana(0.3, 0.6)
            log.info("UC %s selecionada (página %s).", uc_valor, pagina)
            return {"uc": uc_valor, "texto": texto_label}

        if not _tem_proxima_pagina(driver):
            raise RuntimeError(f"UC {uc_valor} nao encontrada em nenhuma pagina de instalacoes.")
        btn = driver.find_element(By.CSS_SELECTOR, "input.next-button")
        _clicar_robusto(driver, btn)
        _pausa_humana(1.5, 2.5)
        _aguardar_instalacoes_dom(driver)
        pagina += 1


def selecionar_uc_ativa(driver: webdriver.Chrome, indice_ativo: int = 0) -> dict:
    radios = driver.find_elements(By.CSS_SELECTOR, "input[name='instalacao']")
    ativas: list[tuple[object, str, str]] = []
    for radio in radios:
        try:
            if not radio.is_enabled():
                continue
            valor = _texto_limpo(radio.get_attribute("value"))
            label = driver.find_element(By.CSS_SELECTOR, f"label[for='{radio.get_attribute('id')}']")
            texto_label = _texto_limpo(label.text)
            if "Inativa" in texto_label:
                continue
            ativas.append((radio, valor, texto_label))
        except Exception:
            continue

    if not ativas:
        raise RuntimeError("Nenhuma UC ativa selecionavel foi encontrada no DOM.")

    if indice_ativo < 0 or indice_ativo >= len(ativas):
        raise RuntimeError(
            f"Indice de UC ativa invalido: {indice_ativo}. Existem {len(ativas)} UCs ativas disponiveis."
        )

    radio, valor, texto_label = ativas[indice_ativo]
    driver.execute_script("arguments[0].checked = true;", radio)
    _pausa_humana(0.4, 0.8)
    log.info("UC ativa selecionada [%s/%s]: %s", indice_ativo + 1, len(ativas), valor)
    return {"uc": valor, "texto": texto_label}


def avancar_com_uc(driver: webdriver.Chrome, uc_valor: str = "") -> None:
    # Detecta portal pelo URL para tentar o botão certo primeiro (evita 5s de timeout desnecessário)
    url_atual = (driver.current_url or "").lower()
    novo_portal = "selecionar-perfil-instalacao" in url_atual

    if novo_portal:
        # JS direto — goto-page-btn tem classe wloader e pode não ser "clickable" via EC
        for js_expr, descricao in [
            ("document.getElementById('goto-page-btn')", "goto-page-btn JS (novo)"),
            ("document.querySelector('input.btn-avancar')", "btn-avancar JS"),
        ]:
            resultado = driver.execute_script(
                f"var el = {js_expr}; if (el) {{ el.scrollIntoView(true); el.click(); return true; }} return false;"
            )
            if resultado:
                log.info("Avanco enviado via JS click (%s).", descricao)
                _pausa_humana(1.0, 1.5)
                return
    else:
        # Portal antigo: seta radio.checked e dispara btn-buscar.click() no mesmo JS atomico.
        # btn-buscar.click() aciona o AJAX do Drupal que define o perfil ativo no servidor
        # (form.submit() nao aciona o AJAX e o servidor nao configura o perfil, causando erro SSO).
        if uc_valor:
            resultado_submit = driver.execute_script(
                """
                var uc = arguments[0];
                var radio = document.querySelector("input[name='instalacao'][value='" + uc + "']")
                         || document.querySelector("#instalacao-" + uc)
                         || document.querySelector("input[type='radio'][value='" + uc + "']");
                if (!radio) { return 'radio-nao-encontrado:' + uc; }
                radio.disabled = false;
                radio.checked = true;
                var btn = document.getElementById('btn-buscar')
                          || document.getElementById('edit-button')
                          || document.querySelector('input[type=submit]');
                if (!btn) { return 'btn-nao-encontrado'; }
                btn.click();
                return 'ok-ajax:' + radio.value;
                """,
                uc_valor
            )
        else:
            resultado_submit = driver.execute_script(
                """
                var radio = document.querySelector("input[name='instalacao']:checked");
                if (!radio) { return 'radio-nao-checado'; }
                var btn = document.getElementById('btn-buscar')
                          || document.getElementById('edit-button')
                          || document.querySelector('input[type=submit]');
                if (!btn) { return 'btn-nao-encontrado'; }
                btn.click();
                return 'ok-ajax:' + radio.value;
                """
            )
        if isinstance(resultado_submit, str) and resultado_submit.startswith("ok-ajax:"):
            log.info("Avanco via btn-buscar AJAX atomico (%s).", resultado_submit)
            _pausa_humana(2.5, 4.0)
            return
        log.info("btn-buscar AJAX atomico nao aplicavel (%s); tentando btn-buscar via EC.", resultado_submit)
        # Fallback: btn-buscar via EC
        _url_antes_ec = driver.current_url or ""
        for by, locator, descricao in [
            (By.ID, "btn-buscar", "btn-buscar (antigo)"),
            (By.ID, "edit-button", "edit-button BT"),
            (By.ID, "edit-button--2", "edit-button--2 MT"),
        ]:
            try:
                botao = WebDriverWait(driver, 3).until(EC.element_to_be_clickable((by, locator)))
                _clicar_robusto(driver, botao)
                log.info("Avanco para a UC selecionada enviado (%s).", descricao)
                # Aguarda o AJAX redirecionar para pagina-inicial ou URL diferente
                try:
                    WebDriverWait(driver, 10).until(
                        lambda d: (d.current_url or "") != _url_antes_ec
                        or "agencia-virtual" in (d.current_url or "").lower()
                    )
                except Exception:
                    pass
                _pausa_humana(1.0, 1.5)
                return
            except Exception:
                continue

    # Fallback universal: tenta todos via JS
    for js_expr, descricao in [
        ("document.getElementById('goto-page-btn')", "goto-page-btn JS fallback"),
        ("document.getElementById('btn-buscar')", "btn-buscar JS fallback"),
        ("document.querySelector('input.btn-avancar')", "btn-avancar JS fallback"),
        ("document.getElementById('edit-button')", "edit-button JS fallback"),
    ]:
        resultado = driver.execute_script(
            f"var el = {js_expr}; if (el) {{ el.scrollIntoView(true); el.click(); return true; }} return false;"
        )
        if resultado:
            log.info("Avanco enviado via JS fallback (%s).", descricao)
            _pausa_humana(1.0, 1.5)
            return

    raise TimeoutException("Botao de Avanco nao encontrado na pagina (btn-buscar / goto-page-btn).")


def _extrair_fatura_da_pagina_inicial(driver: webdriver.Chrome) -> dict[str, str]:
    """Lê mes_ref, valor, vencimento e número de instalação da home da UC (pagina-inicial)."""
    script = r"""
    const texto = document.body.innerText || "";
    const mRef = texto.match(/Referente\s+[àa]\s+([A-Za-záàâãéêíóôõúûçÁÀÂÃÉÊÍÓÔÕÚÛÇ]+\s+de\s+\d{4})/i);
    const mVal = texto.match(/R\$\s*([\d.,]+)/);
    const mVenc = texto.match(/Vencimento:\s*(\d{2}\/\d{2}\/\d{4})/);
    // Tenta extrair UC do texto da página (vários formatos possíveis)
    const mUC = texto.match(/Instala[cç][aã]o[:\s#]+(\d{6,12})/i)
             || texto.match(/N[º°]\s*(\d{6,12})/i);
    // Fallback: link conta-completa pode ter instalacao= na URL
    const linkCC = document.querySelector("a[href*='conta-completa']");
    const hrefUC = linkCC ? (linkCC.href.match(/[?&]instalacao=(\d+)/) || [])[1] || "" : "";
    return {
        mes_ref_texto: mRef ? mRef[1].trim() : "",
        valor: mVal ? mVal[1] : "",
        vencimento: mVenc ? mVenc[1] : "",
        uc_na_pagina: (mUC ? mUC[1].trim() : "") || hrefUC,
    };
    """
    try:
        item = driver.execute_script(script)
    except Exception:
        item = {}
    if not isinstance(item, dict):
        return {}
    return {k: _texto_limpo(str(v)) for k, v in item.items()}


def _baixar_pdf_via_navegacao(driver: webdriver.Chrome, url_pdf: str) -> None:
    """Fallback: navega o browser diretamente para a URL do PDF.
    O Chrome está configurado para baixar PDFs (não exibir), então o arquivo
    vai para o temp dir automaticamente sem precisar de requests/cookies manuais."""
    temp_dir = _temp_dir_para_worker()
    temp_dir.mkdir(parents=True, exist_ok=True)
    log.info("MT navegacao direta para URL PDF: %s", url_pdf[:100])
    driver.get(url_pdf)
    _pausa_humana(1.5, 2.5)


def _baixar_pdf_fluxo_mt(driver: webdriver.Chrome) -> None:
    """Fluxo MT: clica em 'Segunda via da fatura' (entenda-conta) → página de pagamento
    → localiza o link de download direto no DOM (sem precisar abrir modal)."""
    # 1. Clicar em "Segunda via da fatura" (a.entenda-conta)
    link = WebDriverWait(driver, 12).until(
        EC.element_to_be_clickable((By.CSS_SELECTOR, "a.entenda-conta[href*='debitos-segunda-via']"))
    )
    href_ec = link.get_attribute("href") or ""
    log.info("MT: clicando entenda-conta -> %s", href_ec)
    _clicar_robusto(driver, link)
    _pausa_humana(2.5, 3.5)

    # 2. Página de pagamento — abre o modal "Segunda Via" clicando no botão visível,
    #    para que o JS da página inicialize o modal antes do clique no download
    try:
        btn_modal = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, "a[data-open-modal*='segunda-via']"))
        )
        _clicar_robusto(driver, btn_modal)
        log.info("MT: modal segunda-via aberto.")
        _pausa_humana(1.0, 2.0)
    except Exception as exc:
        log.warning("MT: nao conseguiu abrir modal segunda-via: %s", exc)

    # 3. Clicar no link de download dentro do modal (agora visível — JS processa normalmente)
    candidatos_dl = [
        (By.CSS_SELECTOR, "a[id*='download-segunda-via']",                   "id download-segunda-via"),
        (By.CSS_SELECTOR, "a.gerar-protocolo[data-tipo='completa-imprimir']", "gerar-protocolo"),
        (By.XPATH, "//a[contains(normalize-space(.), 'download da 2')]",      "texto download 2a via"),
        (By.XPATH, "//a[contains(normalize-space(.), 'Fazer download')]",     "texto Fazer download"),
    ]
    for by, loc, desc in candidatos_dl:
        try:
            el = WebDriverWait(driver, 10).until(EC.element_to_be_clickable((by, loc)))
            href_dl = el.get_attribute("href") or ""
            _clicar_robusto(driver, el)
            log.info("MT: PDF acionado (%s | href=%s).", desc, href_dl[:80])
            _pausa_humana(1.5, 2.5)
            return
        except Exception as exc:
            log.debug("MT candidato %s falhou: %s", desc, exc)
            continue

    raise TimeoutException("Botao de download PDF MT nao encontrado na pagina segunda-via/pagamentos.")


def baixar_pdf_da_pagina_inicial(driver: webdriver.Chrome, perfil: str = "bt") -> None:
    """Clica no link correto para download do PDF na pagina-inicial.
    MT: usa a.entenda-conta -> pagina de pagamento -> link download.
    BT: usa a.btn.fill.visualizar-pdf (download direto)."""
    # Para perfil MT: detecta entenda-conta e usa fluxo MT.
    # Para perfil BT: tenta BT direto primeiro — entenda-conta pode existir em contas corporativas
    # mesmo para UCs BT, causando deteccao incorreta.
    if perfil != "bt":
        try:
            driver.find_element(By.CSS_SELECTOR, "a.entenda-conta[href*='debitos-segunda-via']")
            log.info("Detectado fluxo MT (entenda-conta presente) — usando fluxo segunda via.")
            _baixar_pdf_fluxo_mt(driver)
            return
        except Exception:
            pass

    # BT: clique direto no visualizar-pdf / conta-completa
    candidatos = [
        (By.CSS_SELECTOR, "a.btn.fill.visualizar-pdf", "btn fill visualizar-pdf"),
        (By.CSS_SELECTOR, "a[href*='conta-completa']",  "link conta-completa"),
        (By.XPATH, "//a[contains(normalize-space(.), 'Ver conta completa')]", "texto Ver conta completa"),
        (By.XPATH, "//a[contains(normalize-space(.), 'Download PDF')]",       "texto Download PDF"),
    ]
    ultimo_erro = None
    for by, locator, descricao in candidatos:
        try:
            link = WebDriverWait(driver, 12).until(EC.element_to_be_clickable((by, locator)))
            href = link.get_attribute("href") or ""
            _clicar_robusto(driver, link)
            log.info("BT: Download PDF acionado (%s | href=%s).", descricao, href)
            _pausa_humana(1.0, 2.0)
            return
        except Exception as exc:
            ultimo_erro = exc
            continue
    raise TimeoutException(f"Botao de Download PDF nao encontrado. Ultimo erro: {ultimo_erro}")


def abrir_pagar_a_conta(driver: webdriver.Chrome) -> None:
    candidatos = [
        (
            By.XPATH,
            "//a[contains(@class, 'entenda-conta') and contains(normalize-space(.), 'Pagar a conta')]",
            "link Pagar a conta pela classe/texto",
        ),
        (
            By.XPATH,
            "//a[contains(@href, '/debitos-segunda-via/pagamentos/') and contains(normalize-space(.), 'Pagar a conta')]",
            "link Pagar a conta pelo href",
        ),
        (
            By.XPATH,
            "//a[contains(normalize-space(.), 'Pagar a conta')]",
            "fallback texto Pagar a conta",
        ),
    ]
    ultimo_erro = None
    for by, locator, descricao in candidatos:
        try:
            link = WebDriverWait(driver, TIMEOUT).until(EC.element_to_be_clickable((by, locator)))
            _clicar_robusto(driver, link)
            log.info("Acesso a Pagar a conta acionado (%s).", descricao)
            _pausa_humana(2.0, 3.0)
            return
        except Exception as exc:
            ultimo_erro = exc
            continue
    raise TimeoutException(f"Link Pagar a conta nao encontrado. Ultimo erro: {ultimo_erro}")


def abrir_servico_uc(driver: webdriver.Chrome, fluxo_servico: str) -> tuple[str, str]:
    if fluxo_servico == "pagar-conta":
        abrir_pagar_a_conta(driver)
        return "cpfl_rge_pagar_conta", "Pagar a conta"
    if fluxo_servico == "segunda-via":
        abrir_segunda_via_fatura(driver)
        return "cpfl_rge_segunda_via", "Segunda via da fatura"
    raise ValueError(f"Fluxo de servico desconhecido: {fluxo_servico}")


def abrir_segunda_via_fatura(driver: webdriver.Chrome) -> None:
    # Para links no dropdown (ex: redirect-arame-active-profile): hover no pai para abrir o menu,
    # depois navega direto via driver.get() com o href extraido — evita o erro SSO do JS click direto.
    for js_selector, descricao_js in [
        ("a[href*='redirect-arame-active-profile']", "redirect-arame-active-profile"),
        ("a[href*='historico-de-contas-antigo']", "historico-de-contas-antigo"),
        ("a[href*='historico-contas']:not([href*='antigo'])", "historico-contas"),
    ]:
        href = driver.execute_script(
            "var a = document.querySelector(arguments[0]); return a ? a.href : null;",
            js_selector,
        )
        if href:
            # Hover no li pai para abrir o dropdown e acionar eventos de menu
            try:
                link_el = driver.find_element(By.CSS_SELECTOR, js_selector)
                pai = driver.execute_script(
                    "var el = arguments[0]; while(el && el.tagName !== 'LI' && el.tagName !== 'NAV') { el = el.parentElement; } return el;",
                    link_el,
                )
                if pai:
                    ActionChains(driver).move_to_element(pai).perform()
                    _pausa_humana(0.5, 1.0)
            except Exception:
                pass
            log.info("Acesso a Segunda via via driver.get (%s) -> %s.", descricao_js, href[:80])
            driver.get(href)
            _pausa_humana(2.0, 3.5)
            return

    candidatos = [
        # Novo portal: "Debito e segunda via" com href historico-de-contas-antigo
        (
            By.XPATH,
            "//a[contains(@href, 'historico-de-contas-antigo')]",
            "link historico-de-contas-antigo",
        ),
        # Redirect alternativo historico-contas
        (
            By.XPATH,
            "//a[contains(@href, 'historico-contas') and not(contains(@href, 'antigo'))]",
            "redirect historico-contas",
        ),
        # Novo portal: "2a via da conta" via redirect-arame-active-profile
        (
            By.XPATH,
            "//a[contains(@href, 'redirect-arame-active-profile')]",
            "redirect-arame-active-profile",
        ),
        # Texto case-insensitive: "débito e segunda via" ou "segunda via"
        (
            By.XPATH,
            "//a[contains("
            "translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZÀÁÂÃÉÊÍÓÔÕÚÇ', "
            "'abcdefghijklmnopqrstuvwxyzàáâãéêíóôõúç'), "
            "'segunda via')]",
            "link texto segunda via (case-insensitive)",
        ),
        # Layout antigo: span com label exato
        (
            By.XPATH,
            "//span[@class='label' and contains(normalize-space(.), 'Segunda via da fatura')]"
            "/ancestor::a[1]",
            "span label layout antigo",
        ),
        # Antigo: href com servico=segunda-via
        (
            By.XPATH,
            "//a[contains(@href, 'servico=segunda-via')]",
            "href servico=segunda-via (antigo)",
        ),
    ]
    ultimo_erro = None
    for by, locator, descricao in candidatos:
        try:
            link = WebDriverWait(driver, 12).until(EC.element_to_be_clickable((by, locator)))
            _clicar_robusto(driver, link)
            log.info("Acesso a Segunda via acionado (%s).", descricao)
            _pausa_humana(2.0, 3.5)
            return
        except Exception as exc:
            ultimo_erro = exc
            continue
    raise TimeoutException(f"Link de Segunda via da fatura nao encontrado. Ultimo erro: {ultimo_erro}")


def diagnosticar_tela_segunda_via(driver: webdriver.Chrome) -> str:
    url = (driver.current_url or "").lower()
    pagina = (driver.page_source or "").lower()

    if "agencia-virtual/pagina-inicial" in url:
        return "pagina_inicial_uc"
    if "/debitos-segunda-via/pagamentos/" in url or "pagar a conta" in pagina:
        return "pagamento_conta"
    if "validausuario.aspx" in url or "favor entrar seu usu" in pagina:
        return "portal_legado_login"
    if "2ª via / consulta à débito".lower() in pagina or "2a via / consulta a debito" in pagina:
        return "consulta_debito"
    if "imprimir segunda via" in pagina and "btngerarfatura" in pagina:
        return "consulta_debito"
    if "historico-de-contas" in url or "historico-contas" in url:
        return "historico_redirect"
    if "conta-completa" in url:
        return "conta_completa"
    return "desconhecida"


def _manter_navegador_aberto(driver: webdriver.Chrome, mensagem: str) -> None:
    log.info(mensagem)
    while True:
        time.sleep(1)


def abrir_contas_quitadas_se_necessario(driver: webdriver.Chrome) -> bool:
    pagina = (driver.page_source or "").lower()
    sem_aberto = (
        "não existem contas em aberto neste momento.".lower() in pagina
        or "nao existem contas em aberto neste momento." in pagina
    )
    if not sem_aberto:
        return False

    candidatos = [
        (By.ID, "ctl00_ContentPlaceHolder1_btnEXIBIRCONTASQUITADAS", "ID padrao"),
        (
            By.XPATH,
            "//input[@type='submit' and contains(@value, 'EXIBIR CONTAS QUITADAS')]",
            "submit pelo value",
        ),
        (
            By.XPATH,
            "//button[contains(normalize-space(.), 'EXIBIR CONTAS QUITADAS')]",
            "botao pelo texto",
        ),
    ]
    ultimo_erro = None
    for by, locator, descricao in candidatos:
        try:
            botao = WebDriverWait(driver, 8).until(EC.presence_of_element_located((by, locator)))
            _clicar_robusto(driver, botao)
            log.info("Tela sem contas em aberto; exibindo contas quitadas (%s).", descricao)
            _pausa_humana(2.0, 3.0)
            return True
        except Exception as exc:
            ultimo_erro = exc
            continue

    log.info("Tela sem contas em aberto, mas botao de contas quitadas nao respondeu: %s", ultimo_erro)
    return False


def selecionar_fatura_segunda_via(driver: webdriver.Chrome) -> None:
    abrir_contas_quitadas_se_necessario(driver)
    grade_path = _salvar_debug_grade_segunda_via(driver, "cpfl_rge_segunda_via_grade_antes_selecao")
    log.info("Debug da grade da 2a via salvo em: %s", grade_path)

    candidatos = [
        (
            By.ID,
            "ctl00_ContentPlaceHolder1_grdFaturas_ctl02_rbIDFAT",
            "radio exato ctl02_rbIDFAT",
        ),
        (
            By.CSS_SELECTOR,
            "#ctl00_ContentPlaceHolder1_grdFaturas_ctl02_rbIDFAT",
            "radio exato por css",
        ),
        (
            By.CSS_SELECTOR,
            "#ctl00_ContentPlaceHolder1_grdFaturas input[type='radio']",
            "radio da grade grdFaturas",
        ),
        (
            By.CSS_SELECTOR,
            "#tbConsultaDeb input[type='radio']",
            "radio da tabela tbConsultaDeb",
        ),
        (
            By.CSS_SELECTOR,
            "input[id*='grdFaturas'][id*='rbIDFAT']",
            "radio grdFaturas/rbIDFAT",
        ),
        (
            By.CSS_SELECTOR,
            "input[type='radio'][name*='rbIDFAT'], input[type='radio'][name*='grdFaturas']",
            "radio por name",
        ),
        (
            By.CSS_SELECTOR,
            "input[type='radio']",
            "fallback generico",
        ),
    ]
    ultimo_erro = None
    for by, locator, descricao in candidatos:
        try:
            radio = WebDriverWait(driver, 8).until(EC.presence_of_element_located((by, locator)))
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", radio)
            driver.execute_script(
                """
                arguments[0].checked = true;
                if (typeof CheckOtherIsCheckedByGVID === 'function') {
                    CheckOtherIsCheckedByGVID(arguments[0]);
                }
                arguments[0].dispatchEvent(new Event('click', { bubbles: true }));
                arguments[0].dispatchEvent(new Event('change', { bubbles: true }));
                """,
                radio,
            )
            try:
                radio.click()
            except Exception:
                pass

            try:
                linha = radio.find_element(By.XPATH, "./ancestor::tr[1]")
                driver.execute_script(
                    "arguments[0].style.backgroundColor = '#d9edf7'; arguments[0].scrollIntoView({block:'center'});",
                    linha,
                )
                try:
                    _clicar_robusto(driver, linha)
                except Exception:
                    pass
            except Exception:
                pass

            WebDriverWait(driver, 5).until(
                lambda d: d.execute_script(
                    "return !!(arguments[0].checked || arguments[0].getAttribute('checked'));",
                    radio,
                )
            )
            log.info(
                "Fatura da 2a via selecionada (%s | id=%s).",
                descricao,
                radio.get_attribute("id"),
            )
            _pausa_humana(0.6, 1.2)
            grade_depois = _salvar_debug_grade_segunda_via(driver, "cpfl_rge_segunda_via_grade_depois_selecao")
            log.info("Debug da grade apos selecionar fatura salvo em: %s", grade_depois)
            return
        except Exception as exc:
            ultimo_erro = exc
            continue
    artefatos = _snapshot_debug(driver, "cpfl_rge_falha_selecao_fatura")
    grade_falha = _salvar_debug_grade_segunda_via(driver, "cpfl_rge_segunda_via_grade_falha")
    log.error("HTML de falha na selecao salvo em: %s", artefatos["html"])
    log.error("Screenshot de falha na selecao salvo em: %s", artefatos["screenshot"])
    if "txt" in artefatos:
        log.error("Resumo da tela salvo em: %s", artefatos["txt"])
    log.error("Debug final da grade salvo em: %s", grade_falha)
    raise TimeoutException(f"Nao foi possivel selecionar a fatura da 2a via. Ultimo erro: {ultimo_erro}")


def imprimir_segunda_via(driver: webdriver.Chrome) -> str:
    handles_antes = set(driver.window_handles)
    grade_path = _salvar_debug_grade_segunda_via(driver, "cpfl_rge_segunda_via_grade_antes_imprimir")
    log.info("Debug da grade antes de imprimir salvo em: %s", grade_path)
    candidatos = [
        (By.ID, "ctl00_ContentPlaceHolder1_btnGERARFATURA", "ID padrao"),
        (
            By.CSS_SELECTOR,
            "input[type='submit'][name='ctl00$ContentPlaceHolder1$btnGERARFATURA']",
            "submit pelo name",
        ),
        (
            By.XPATH,
            "//input[@type='submit' and @value='Imprimir segunda via']",
            "submit pelo value",
        ),
    ]
    ultimo_erro = None
    for by, locator, descricao in candidatos:
        try:
            botao = WebDriverWait(driver, 8).until(EC.presence_of_element_located((by, locator)))
            _clicar_robusto(driver, botao)
            log.info("Botao Imprimir segunda via acionado (%s).", descricao)
            break
        except Exception as exc:
            ultimo_erro = exc
            continue
    else:
        artefatos = _snapshot_debug(driver, "cpfl_rge_falha_imprimir_segunda_via")
        log.error("HTML de falha ao imprimir salvo em: %s", artefatos["html"])
        log.error("Screenshot de falha ao imprimir salvo em: %s", artefatos["screenshot"])
        if "txt" in artefatos:
            log.error("Resumo da tela salvo em: %s", artefatos["txt"])
        raise TimeoutException(f"Botao Imprimir segunda via nao encontrado. Ultimo erro: {ultimo_erro}")

    WebDriverWait(driver, TIMEOUT).until(lambda d: len(set(d.window_handles) - handles_antes) >= 1)
    novas = list(set(driver.window_handles) - handles_antes)
    nova_janela = novas[-1]
    driver.switch_to.window(nova_janela)
    WebDriverWait(driver, TIMEOUT).until(lambda d: "gerar" in (d.current_url or "").lower())
    log.info("Nova janela da fatura aberta: %s", driver.current_url)
    _pausa_humana(4.0, 6.0)
    return driver.current_url


def esperar_arquivo_pdf_baixado(timeout: int = 180) -> Path:
    inicio = time.time()
    temp_dir = _temp_dir_para_worker()
    temp_dir.mkdir(parents=True, exist_ok=True)
    existentes = {p.resolve() for p in temp_dir.glob("*")}
    while time.time() - inicio < timeout:
        atuais = [p for p in temp_dir.glob("*") if p.is_file()]
        crdownloads = [p for p in atuais if p.suffix.lower() == ".crdownload"]
        if crdownloads:
            time.sleep(1)
            continue

        novos = [p for p in atuais if p.resolve() not in existentes]
        candidatos = sorted(novos or atuais, key=lambda p: p.stat().st_mtime, reverse=True)
        for arquivo in candidatos:
            if _arquivo_eh_pdf(arquivo):
                destino = arquivo.with_suffix(".pdf")
                if arquivo.suffix.lower() != ".pdf":
                    if destino.exists():
                        destino.unlink()
                    arquivo.rename(destino)
                    log.info("Download PDF normalizado: %s -> %s", arquivo.name, destino.name)
                    return destino
                log.info("Download PDF concluido: %s", arquivo.name)
                return arquivo
        time.sleep(1)
    raise TimeoutException("Arquivo PDF nao apareceu na pasta de downloads.")


def preparar_titular_e_instalacoes(driver: webdriver.Chrome, perfil: str = "mt") -> tuple[dict, str, list[dict]]:
    garantir_tela_cadastro(driver, perfil)

    titular = selecionar_primeiro_titular(driver)
    html_instalacoes = consultar_instalacoes_titular(driver, titular.get("id", ""))
    fragmento = _save_texto("cpfl_rge_instalacoes_ajax", html_instalacoes)
    log.info("Fragmento HTML de instalacoes salvo em: %s", fragmento)
    aplicar_instalacoes_no_dom(driver, html_instalacoes)

    ucs = extrair_ucs_de_html(html_instalacoes)
    if ucs:
        log.info("UCs encontradas para o primeiro titular (%s): %s", titular.get("text", ""), len(ucs))
        for item in ucs[:30]:
            log.info("  UC %s | %s | %s", item["uc"], item["status"], item["linha"])
    else:
        log.info("Nao foi possivel identificar UCs automaticamente no retorno AJAX.")

    return titular, html_instalacoes, ucs


def listar_titulares_disponiveis(driver: webdriver.Chrome, perfil: str) -> list[dict]:
    garantir_tela_cadastro(driver, perfil)
    return obter_titulares(driver)


def abrir_titular_e_instalacoes(driver: webdriver.Chrome, perfil: str, titular_alvo: dict) -> tuple[dict, list[dict]]:
    garantir_tela_cadastro(driver, perfil)
    titular = selecionar_titular(driver, titular_alvo)
    # Aguarda Drupal atualizar o DOM; fallback para AJAX manual se não acontecer
    try:
        _aguardar_instalacoes_dom(driver, timeout=15)
    except TimeoutException:
        log.info("DOM de instalações não atualizou automaticamente; usando AJAX manual.")
        html_inst = consultar_instalacoes_titular(driver, titular.get("id", ""))
        aplicar_instalacoes_no_dom(driver, html_inst)
        _aguardar_instalacoes_dom(driver, timeout=10)
    ucs = coletar_todas_ucs_paginado(driver)
    return titular, ucs


def processar_uma_uc_lote(
    driver: webdriver.Chrome,
    perfil: str,
    titular_alvo: dict,
    uc_valor_alvo: str,
    fluxo_servico: str,
    baixados: set[tuple[str, str]],
    master,
    parar_na_segunda_via: bool = False,
    manter_aberto: bool = False,
    mesmo_titular: bool = False,
    forcar_download: bool = False,
) -> dict:
    # Caminho rápido: se mesma sessão/titular, vai direto de pagina-inicial → selecionar outra instalação
    usou_caminho_rapido = mesmo_titular and _reselecionar_rapido_da_pagina_inicial(driver)

    if usou_caminho_rapido:
        # Titular já selecionado na sessão — reutiliza dados passados por parâmetro
        titular = {"id": titular_alvo.get("id", ""), "text": titular_alvo.get("text", "")}
    else:
        # Verifica se UC já está no DOM — evita re-selecionar o titular quando acabamos
        # de coletar a lista em abrir_titular_e_instalacoes (re-seleção do mesmo titular
        # em contas corporativas pode disparar redirecionamento para card UI ao invés de
        # atualizar #edit-items via AJAX). Verificamos apenas a presença do radio,
        # independente de URL, pois o portal pode estar em /selecionar-perfil-instalacao
        # ou /cadastro dependendo do tipo de titular.
        try:
            _na_pagina_inicial = "agencia-virtual/pagina-inicial" in (driver.current_url or "")
            _ja_no_dom = bool(
                not _na_pagina_inicial
                and (
                    driver.find_elements(By.CSS_SELECTOR, f"input[name='instalacao'][value='{uc_valor_alvo}']")
                    or driver.find_elements(By.CSS_SELECTOR, f"#instalacao-{uc_valor_alvo}")
                    or driver.find_elements(By.CSS_SELECTOR, f"input[type='radio'][value='{uc_valor_alvo}']")
                )
            )
        except Exception:
            _ja_no_dom = False

        if _ja_no_dom:
            log.info("UC %s ja no DOM em /cadastro — pulando re-selecao de titular.", uc_valor_alvo)
            titular = {"id": titular_alvo.get("id", ""), "text": titular_alvo.get("text", "")}
        else:
            garantir_tela_cadastro(driver, perfil)
            titular = selecionar_titular(driver, titular_alvo)
            try:
                _aguardar_instalacoes_dom(driver, timeout=15)
            except TimeoutException:
                log.info("DOM nao atualizou; AJAX manual para UC %s.", uc_valor_alvo)
                try:
                    html_inst = consultar_instalacoes_titular(driver, titular.get("id", ""))
                    aplicar_instalacoes_no_dom(driver, html_inst)
                    _aguardar_instalacoes_dom(driver, timeout=10)
                except Exception as _exc_ajax:
                    log.info("AJAX manual tambem falhou (%s); prosseguindo.", _exc_ajax)

    try:
        uc_info = navegar_para_uc(driver, uc_valor_alvo)
    except RuntimeError as _exc_nav:
        if "nao encontrada" not in str(_exc_nav).lower():
            raise
        # Fallback: instalacoes inativas ficam numa pagina separada
        log.info("UC %s nao encontrada na tela principal; tentando /selecionar-instalacao-inativa...", uc_valor_alvo)
        try:
            driver.get("https://www.cpfl.com.br/agencia/area-cliente/selecionar-instalacao-inativa")
            _pausa_humana(2.0, 3.0)
            _aguardar_instalacoes_dom(driver, timeout=12)
            uc_info = navegar_para_uc(driver, uc_valor_alvo)
        except Exception as _exc_inat:
            raise RuntimeError(
                f"UC {uc_valor_alvo} nao encontrada nem na tela principal nem em instalacoes-inativas: {_exc_inat}"
            ) from _exc_nav
    avancar_com_uc(driver, uc_valor=uc_info.get("uc", ""))

    # Aguarda a navegação pós-avanço concluir antes de inspecionar a URL.
    # O clique em goto-page-btn é assíncrono — sem espera, a URL ainda pode
    # ser selecionar-perfil-instalacao e o script cai no branch errado (portal antigo).
    _url_pos_avanco = driver.current_url or ""
    if "selecionar-perfil-instalacao" in _url_pos_avanco or "cadastro" in _url_pos_avanco:
        try:
            WebDriverWait(driver, 15).until(
                lambda d: "selecionar-perfil-instalacao" not in (d.current_url or "")
                and "cadastro" not in (d.current_url or "")
            )
        except Exception:
            pass

    uc = uc_info.get("uc", "")
    cnpj_titular = _extrair_cnpj_do_titular(titular.get("text", ""))
    janela_principal = driver.current_window_handle
    fatura_id_para_master = ""

    # Detecta portal geração nova (pagina-inicial) vs antigo (ASPX)
    url_atual = driver.current_url or ""
    na_pagina_inicial = "agencia-virtual/pagina-inicial" in url_atual
    na_historico_contas = (
        "historico-contas" in url_atual.lower()
        or "historico-de-contas" in url_atual.lower()
    )

    if na_pagina_inicial:
        # ── Novo portal: extrai mes_ref da home da UC ──────────────────────
        fatura_home = _extrair_fatura_da_pagina_inicial(driver)
        mes_ref_texto = fatura_home.get("mes_ref_texto", "")
        mes_ref = _normalizar_mes_ref_cpfl(mes_ref_texto)
        uc_na_pagina = fatura_home.get("uc_na_pagina", "")
        log.info("Pagina inicial UC: mes_ref=%s valor=%s vencimento=%s uc_pagina=%s",
                 mes_ref, fatura_home.get("valor"), fatura_home.get("vencimento"), uc_na_pagina)

        # Guarda anti-duplicata: se o portal redirecionou para uma UC diferente, rejeita
        if uc_na_pagina and not _ucs_correspondem(uc, uc_na_pagina):
            log.warning("Redirect detectado: esperava UC %s mas pagina mostra UC %s — ignorando.", uc, uc_na_pagina)
            return {"status": "REDIRECT_ERRADO", "titular": titular.get("text", ""), "uc": uc, "uc_na_pagina": uc_na_pagina}

        # Portal carregou mas não conseguiu obter dados da UC (sem fatura disponível)
        _pagina_texto = (driver.page_source or "").lower()
        if (
            "não foi possível obter os dados de conta atual" in _pagina_texto
            or "nao foi possivel obter os dados de conta atual" in _pagina_texto
        ):
            log.warning("Pagina-inicial sem dados para UC %s — sem fatura disponivel, pulando.", uc)
            return {"status": "SEM_DADOS_PORTAL", "titular": titular.get("text", ""), "uc": uc}

        if parar_na_segunda_via:
            log.info("Fluxo interrompido na pagina-inicial para inspecao (UC %s).", uc)
            if manter_aberto:
                _manter_navegador_aberto(driver, f"pagina-inicial UC {uc}")
            return {"status": "PARADO_INSPECAO", "titular": titular.get("text", ""), "uc": uc}

        if not forcar_download:
            if mes_ref and master and master.ja_foi_baixado(uc, mes_ref, "CPFL"):
                log.info("Ja no master: %s | %s", uc, mes_ref)
                return {"status": "JA_MASTER", "titular": titular.get("text", ""), "uc": uc, "mes_ref": mes_ref}
            if mes_ref and (uc, mes_ref) in baixados:
                log.info("Ja no indice local: %s | %s", uc, mes_ref)
                return {"status": "JA_LOCAL", "titular": titular.get("text", ""), "uc": uc, "mes_ref": mes_ref}

        try:
            baixar_pdf_da_pagina_inicial(driver, perfil=perfil)
            pdf_baixado = esperar_arquivo_pdf_baixado()
            log.info("PDF salvo em: %s", pdf_baixado)
        except PortalPdfError as exc:
            log.warning("Portal recusou PDF da UC %s: %s", uc, exc)
            return {
                "status": "PORTAL_PDF_FALHOU",
                "titular": titular.get("text", ""),
                "uc": uc,
                "mes_ref": mes_ref,
                "detalhe": str(exc),
            }
        except TimeoutException as exc:
            detalhe = str(exc)
            status_timeout = "PORTAL_TIMEOUT"
            if "pdf nao apareceu" in detalhe.lower() or "arquivo pdf nao apareceu" in detalhe.lower():
                status_timeout = "DOWNLOAD_TIMEOUT"
            log.warning("Timeout no download da UC %s: %s", uc, detalhe)
            return {
                "status": status_timeout,
                "titular": titular.get("text", ""),
                "uc": uc,
                "mes_ref": mes_ref,
                "detalhe": detalhe,
            }

    elif na_historico_contas:
        # ── Novo portal: historico-contas (segunda via sem radio buttons ASPX) ──
        _pagina_hc = (driver.page_source or "").lower()
        if (
            "você não possui débitos em aberto" in _pagina_hc
            or "voce nao possui debitos em aberto" in _pagina_hc
            or "não existem contas em aberto" in _pagina_hc
            or "nao existem contas em aberto" in _pagina_hc
        ):
            log.warning("UC %s sem debitos em aberto no historico-contas — pulando.", uc)
            return {"status": "SEM_DEBITO_ABERTO", "titular": titular.get("text", ""), "uc": uc}
        log.warning(
            "UC %s em historico-contas com conteudo desconhecido — salvando artefato para inspecao.", uc
        )
        _snapshot_debug(driver, "cpfl_rge_historico_contas_desconhecido")
        return {"status": "HISTORICO_CONTAS_MANUAL", "titular": titular.get("text", ""), "uc": uc}

    else:
        # ── Fallback: portal antigo ASPX ───────────────────────────────────
        prefixo_tela, nome_fluxo = abrir_servico_uc(driver, fluxo_servico)

        if parar_na_segunda_via:
            log.info("Fluxo interrompido apos %s para inspecao (UC %s).", nome_fluxo, uc)
            if manter_aberto:
                _manter_navegador_aberto(driver, f"{nome_fluxo} da UC {uc}")
            return {"status": "PARADO_INSPECAO", "titular": titular.get("text", ""), "uc": uc}

        fatura = _extrair_fatura_atual_segunda_via(driver)
        mes_ref = _normalizar_mes_ref_cpfl(fatura.get("mes_ref", ""))
        fatura_id_para_master = fatura.get("texto_linha", "")

        if not forcar_download:
            if mes_ref and master and master.ja_foi_baixado(uc, mes_ref, "CPFL"):
                log.info("Ja no master: %s | %s", uc, mes_ref)
                return {"status": "JA_MASTER", "titular": titular.get("text", ""), "uc": uc, "mes_ref": mes_ref}
            if mes_ref and (uc, mes_ref) in baixados:
                log.info("Ja no indice local: %s | %s", uc, mes_ref)
                return {"status": "JA_LOCAL", "titular": titular.get("text", ""), "uc": uc, "mes_ref": mes_ref}

        try:
            selecionar_fatura_segunda_via(driver)
            url_fatura = imprimir_segunda_via(driver)
            log.info("Janela da fatura carregada: %s", url_fatura)
            pdf_baixado = esperar_arquivo_pdf_baixado()
            log.info("PDF salvo em: %s", pdf_baixado)
        except PortalPdfError as exc:
            log.warning("Portal recusou PDF da UC %s: %s", uc, exc)
            return {
                "status": "PORTAL_PDF_FALHOU",
                "titular": titular.get("text", ""),
                "uc": uc,
                "mes_ref": mes_ref,
                "detalhe": str(exc),
            }
        except TimeoutException as exc:
            detalhe = str(exc)
            status_timeout = "PORTAL_TIMEOUT"
            if "pdf nao apareceu" in detalhe.lower() or "arquivo pdf nao apareceu" in detalhe.lower():
                status_timeout = "DOWNLOAD_TIMEOUT"
            log.warning("Timeout no download da UC %s: %s", uc, detalhe)
            return {
                "status": status_timeout,
                "titular": titular.get("text", ""),
                "uc": uc,
                "mes_ref": mes_ref,
                "detalhe": detalhe,
            }

    # ── Registro comum (ambos caminhos) ────────────────────────────────────
    indice_bb = master.consumir_carimbo() if master else f"BB_{2000000 + len(baixados):07d}"
    destino = _mover_pdf_para_destino(pdf_baixado, perfil, mes_ref or "sem_mes_ref", indice_bb)
    if master and mes_ref:
        master.registrar(
            indice_bb=indice_bb,
            sistema="CPFL",
            uc=uc,
            mes_ref=mes_ref,
            fatura_id=fatura_id_para_master,
            cnpj=cnpj_titular,
            estado="SAO PAULO",
            arquivo=str(destino),
            concessionaria="CPFL / RGE",
        )
    if mes_ref:
        _registrar_indice_local(
            indice_bb=indice_bb,
            uc=uc,
            mes_ref=mes_ref,
            titular_id=titular.get("id", ""),
            titular_texto=titular.get("text", ""),
            perfil=perfil,
            arquivo=str(destino),
        )
        baixados.add((uc, mes_ref))

    if driver.current_window_handle != janela_principal:
        try:
            driver.close()
            driver.switch_to.window(janela_principal)
            log.info("Janela extra fechada; retorno para janela principal.")
        except Exception as exc:
            log.info("Nao foi possivel fechar janela extra: %s", exc)

    return {
        "status": "OK",
        "titular": titular.get("text", ""),
        "uc": uc,
        "mes_ref": mes_ref,
        "indice": indice_bb,
        "arquivo": str(destino),
    }


def executar(
    usuario: str,
    senha: str,
    headless: bool,
    manter_aberto: bool,
    parar_na_segunda_via: bool,
    indice_uc_ativa: int,
    fluxo_servico: str,
    perfil: str,
    lote: bool,
    limite_titulares: int,
    limite_ucs: int,
    offset_titulares: int = 0,
    worker_id: int = 0,
    forcar_download: bool = False,
    max_ucs_por_titular: int = 368,
) -> int:
    global _WORKER_ID
    _WORKER_ID = worker_id
    driver = None
    try:
        log.info("=" * 72)
        log.info("CPFL / RGE - WORKER %s", worker_id)
        log.info("Perfil alvo: %s", "Baixa tensao" if perfil == "bt" else "Media e alta tensao")
        log.info("Destino rede: %s", BASE_DIR)
        log.info("Temp local  : %s", _temp_dir_para_worker())
        log.info("Guarda UCs : maximo %s por titular", max_ucs_por_titular)
        if offset_titulares > 0:
            log.info("Offset titulares: pulando os %s primeiros", offset_titulares)
        log.info("=" * 72)
        try:
            master = carregar_master()
        except Exception as exc:
            master = None
            log.info("Falha ao carregar master; seguindo sem indice master: %s", exc)
        baixados = _carregar_indice_local()
        log.info("Indice local CPFL: %s registros", len(baixados))
        driver = build_driver(headless=headless)
        fazer_login(driver, usuario, senha)
        aguardar_pos_login(driver)
        aceitar_cookies(driver)
        titulares = listar_titulares_disponiveis(driver, perfil)
        if not titulares:
            raise RuntimeError("Nenhum titular foi retornado para o perfil selecionado.")

        if offset_titulares > 0:
            titulares = titulares[offset_titulares:]

        if limite_titulares > 0:
            titulares = titulares[:limite_titulares]

        if not lote:
            titulares = titulares[:1]

        for idx_t, titular_alvo in enumerate(titulares, start=1):
            try:
                titular_preview, ucs_preview = abrir_titular_e_instalacoes(driver, perfil, titular_alvo)
            except Exception as exc:
                log.info("[%s/%s] Falha ao abrir titular %s: %s", idx_t, len(titulares), titular_alvo.get("text", ""), exc)
                continue

            ativas_preview = [item for item in ucs_preview if item.get("status") == "ATIVA"]
            log.info("[%s/%s] Titular %s | UCs: %s (%s ativas, %s inativas)",
                     idx_t, len(titulares), titular_preview.get("text", ""),
                     len(ucs_preview), len(ativas_preview), len(ucs_preview) - len(ativas_preview))
            validar_expansao_ucs(
                titular_id=titular_alvo.get("id", ""),
                titular_texto=titular_preview.get("text", ""),
                total_ucs=len(ucs_preview),
                max_ucs=max_ucs_por_titular,
            )
            _registrar_inventario(
                titular_id=titular_alvo.get("id", ""),
                titular_texto=titular_preview.get("text", ""),
                perfil=perfil,
                ucs=ucs_preview,
            )
            for item in ucs_preview[:30]:
                log.info("  UC %s | %s | %s", item["uc"], item["status"], item["linha"])

            if not ucs_preview:
                continue

            if not lote:
                if indice_uc_ativa < len(ativas_preview):
                    ucs_para_rodar = [ativas_preview[indice_uc_ativa]]
                else:
                    log.warning("Indice UC %s fora do range (%s ativas).", indice_uc_ativa, len(ativas_preview))
                    continue
            else:
                ucs_para_rodar = ucs_preview
                if limite_ucs > 0:
                    ucs_para_rodar = ucs_para_rodar[:limite_ucs]

            # Pré-filtro rápido: descarta sem navegar UCs já no master para o mês atual/anterior
            if master and lote:
                hoje = datetime.now()
                mes_atual = f"{hoje.month:02d}-{hoje.year}"
                mes_ant_num = hoje.month - 1 or 12
                mes_ant_ano = hoje.year if hoje.month > 1 else hoje.year - 1
                mes_anterior = f"{mes_ant_num:02d}-{mes_ant_ano}"
                meses_cand = [mes_atual, mes_anterior]
                ucs_antes = len(ucs_para_rodar)
                ucs_para_rodar = [
                    item for item in ucs_para_rodar
                    if not any(master.ja_foi_baixado(item["uc"], m, "CPFL") for m in meses_cand)
                    and not any((item["uc"], m) in baixados for m in meses_cand)
                ]
                puladas = ucs_antes - len(ucs_para_rodar)
                if puladas:
                    log.info("Pre-filtro: %s/%s UCs ja no master — puladas sem navegar. Restam: %s",
                             puladas, ucs_antes, len(ucs_para_rodar))

            for idx_local, uc_item in enumerate(ucs_para_rodar, start=1):
                uc_valor = uc_item["uc"]
                log.info("-" * 72)
                log.info(
                    "[Titular %s/%s | UC %s/%s] %s",
                    idx_t, len(titulares), idx_local, len(ucs_para_rodar), uc_valor,
                )
                log.info("-" * 72)
                try:
                    res = processar_uma_uc_lote(
                        driver=driver,
                        perfil=perfil,
                        titular_alvo=titular_alvo,
                        uc_valor_alvo=uc_valor,
                        fluxo_servico=fluxo_servico,
                        baixados=baixados,
                        master=master,
                        parar_na_segunda_via=parar_na_segunda_via,
                        manter_aberto=manter_aberto and not headless,
                        mesmo_titular=(idx_local > 1),
                        forcar_download=forcar_download,
                    )
                    log.info("Resultado UC: %s", res)
                    if not lote:
                        return 0
                except PerfilIndisponivelError as exc:
                    res = {
                        "status": "PERFIL_MT_INDISPONIVEL" if perfil == "mt" else "PERFIL_INDISPONIVEL",
                        "titular": titular_alvo.get("text", ""),
                        "uc": uc_valor,
                        "detalhe": str(exc),
                    }
                    log.warning("Resultado UC: %s", res)
                    if not lote:
                        return 1
                except Exception as exc:
                    log.error("Falha ao processar titular %s / UC %s: %s", titular_alvo.get("text", ""), uc_valor, exc)
                    try:
                        artefatos_erro_uc = _snapshot_debug(driver, "cpfl_rge_erro_uc_lote")
                        log.error("HTML de erro salvo em: %s", artefatos_erro_uc["html"])
                        log.error("Screenshot de erro salvo em: %s", artefatos_erro_uc["screenshot"])
                        if "txt" in artefatos_erro_uc:
                            log.error("Resumo da tela salvo em: %s", artefatos_erro_uc["txt"])
                    except Exception:
                        pass
                    if not lote:
                        raise
        return 0
    except Exception as exc:
        log.error("Falha no login CPFL/RGE: %s", exc)
        if driver is not None:
            try:
                artefatos_erro = _snapshot_debug(driver, "cpfl_rge_erro_login")
                log.error("HTML de erro salvo em: %s", artefatos_erro["html"])
                log.error("Screenshot de erro salvo em: %s", artefatos_erro["screenshot"])
                if "txt" in artefatos_erro:
                    log.error("Resumo da tela salvo em: %s", artefatos_erro["txt"])
            except Exception:
                pass
        return 1
    finally:
        if driver is not None and not (manter_aberto and not headless):
            try:
                driver.quit()
            except Exception:
                pass


def navegar_para_uc(driver: webdriver.Chrome, uc_valor: str) -> dict:
    """Versao reforcada: marca a UC por JS para evitar stale element no DOM dinamico."""
    pagina = 1
    while True:
        texto_label = ""
        selecionada = False
        for _ in range(3):
            try:
                radio = None
                for seletor in [
                    f"input[name='instalacao'][value='{uc_valor}']",
                    f"#instalacao-{uc_valor}",
                    f"input[type='radio'][value='{uc_valor}']",
                ]:
                    encontrados = driver.find_elements(By.CSS_SELECTOR, seletor)
                    if encontrados:
                        radio = encontrados[0]
                        break
                if radio is None:
                    break

                label_id = radio.get_attribute("id") or ""
                if label_id:
                    try:
                        label = driver.find_element(By.CSS_SELECTOR, f"label[for='{label_id}']")
                        texto_label = _texto_limpo(label.text)
                    except Exception:
                        texto_label = ""

                selecionada = bool(driver.execute_script(
                    """
                    var uc = arguments[0];
                    var radio = document.querySelector("input[name='instalacao'][value='" + uc + "']")
                             || document.querySelector("#instalacao-" + uc)
                             || document.querySelector("input[type='radio'][value='" + uc + "']");
                    if (!radio) return false;
                    radio.disabled = false;
                    radio.checked = true;
                    return true;
                    """,
                    uc_valor,
                ))
                if selecionada:
                    break
            except Exception:
                time.sleep(0.5)

        if selecionada:
            _pausa_humana(0.3, 0.6)
            log.info("UC %s selecionada (página %s).", uc_valor, pagina)
            return {"uc": uc_valor, "texto": texto_label}

        if not _tem_proxima_pagina(driver):
            raise RuntimeError(f"UC {uc_valor} nao encontrada em nenhuma pagina de instalacoes.")
        btn = driver.find_element(By.CSS_SELECTOR, "input.next-button")
        _clicar_robusto(driver, btn)
        _pausa_humana(1.5, 2.5)
        _aguardar_instalacoes_dom(driver)
        pagina += 1


def _baixar_pdf_fluxo_mt(driver: webdriver.Chrome) -> None:
    """Versao reforcada: usa href direto do PDF quando o modal do portal nao responde."""
    link = WebDriverWait(driver, 12).until(
        EC.element_to_be_clickable((By.CSS_SELECTOR, "a.entenda-conta[href*='debitos-segunda-via']"))
    )
    href_ec = link.get_attribute("href") or ""
    log.info("MT: clicando entenda-conta -> %s", href_ec)
    _clicar_robusto(driver, link)
    _pausa_humana(2.5, 3.5)

    try:
        btn_modal = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, "a[data-open-modal*='segunda-via']"))
        )
        _clicar_robusto(driver, btn_modal)
        log.info("MT: modal segunda-via aberto.")
        _pausa_humana(1.0, 2.0)
    except Exception as exc:
        log.warning("MT: nao conseguiu abrir modal segunda-via: %s", exc)

    def _hrefs_download_mt() -> list[str]:
        try:
            hrefs = driver.execute_script(
                """
                var seletores = [
                    "a.gerar-protocolo[data-tipo='completa-imprimir']",
                    "a[id*='download-segunda-via']",
                    "a[href*='/debitos-segunda-via/completa/download/pdf']"
                ];
                var out = [];
                for (const sel of seletores) {
                    var nodes = Array.from(document.querySelectorAll(sel));
                    for (const el of nodes) {
                        if (el && el.href) out.push(el.href);
                    }
                }
                return out;
                """
            )
        except Exception:
            hrefs = []
        if not isinstance(hrefs, list):
            return []
        vistos: list[str] = []
        for href in hrefs:
            href_norm = _texto_limpo(str(href))
            if href_norm and href_norm not in vistos:
                vistos.append(href_norm)
        return vistos

    def _normalizar_href_download_mt(href: str) -> str:
        href = _texto_limpo(href)
        if not href:
            return ""
        # O portal expõe links com `numConta[]`, mas na navegação direta esse
        # formato pode cair em "non-scalar value". Normalizamos para `numConta`.
        href = href.replace("numConta[]=", "numConta=")
        href = href.replace("numConta%5B%5D=", "numConta=")
        return href

    hrefs_diretos = [_normalizar_href_download_mt(h) for h in _hrefs_download_mt()]
    hrefs_diretos = [h for h in hrefs_diretos if h]
    if hrefs_diretos:
        erros_pdf: list[str] = []
        for idx, href_direto in enumerate(hrefs_diretos, start=1):
            try:
                log.info("MT: link direto do PDF localizado no DOM (%s/%s).", idx, len(hrefs_diretos))
                _baixar_pdf_via_requests(driver, href_direto)
                return
            except PortalPdfError as exc:
                erros_pdf.append(str(exc))
                log.warning("MT: portal recusou href direto (%s/%s): %s", idx, len(hrefs_diretos), exc)
                continue
        raise PortalPdfError(" | ".join(erros_pdf))

    candidatos_dl = [
        (By.CSS_SELECTOR, "a[id*='download-segunda-via']", "id download-segunda-via"),
        (By.CSS_SELECTOR, "a.gerar-protocolo[data-tipo='completa-imprimir']", "gerar-protocolo"),
        (By.XPATH, "//a[contains(normalize-space(.), 'download da 2')]", "texto download 2a via"),
        (By.XPATH, "//a[contains(normalize-space(.), 'Fazer download')]", "texto Fazer download"),
    ]
    for by, loc, desc in candidatos_dl:
        try:
            el = WebDriverWait(driver, 10).until(EC.element_to_be_clickable((by, loc)))
            href_dl = el.get_attribute("href") or ""
            _clicar_robusto(driver, el)
            log.info("MT: PDF acionado (%s | href=%s).", desc, href_dl[:80])
            _pausa_humana(1.5, 2.5)
            return
        except Exception as exc:
            log.debug("MT candidato %s falhou: %s", desc, exc)
            hrefs_diretos = [_normalizar_href_download_mt(h) for h in _hrefs_download_mt()]
            hrefs_diretos = [h for h in hrefs_diretos if h]
            if hrefs_diretos:
                log.info("MT: fallback por href direto apos falha no clique (%s).", desc)
                erros_pdf: list[str] = []
                for idx, href_direto in enumerate(hrefs_diretos, start=1):
                    try:
                        _baixar_pdf_via_requests(driver, href_direto)
                        return
                    except PortalPdfError as pdf_exc:
                        erros_pdf.append(str(pdf_exc))
                        log.warning("MT: portal recusou href fallback (%s/%s): %s", idx, len(hrefs_diretos), pdf_exc)
                        continue
                raise PortalPdfError(" | ".join(erros_pdf))
            continue

    raise TimeoutException("Botao de download PDF MT nao encontrado na pagina segunda-via/pagamentos.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fluxo inicial CPFL/RGE - teste de login.")
    parser.add_argument("--conta", choices=list(_CONTAS), default="",
                        help="Atalho de credenciais: denise (padrão CPFL), rge/bb (RGE/bbenergia)")
    parser.add_argument("--usuario", default="")
    parser.add_argument("--senha", default="")
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--manter-aberto", action="store_true")
    parser.add_argument("--parar-na-segunda-via", action="store_true")
    parser.add_argument("--indice-uc-ativa", type=int, default=0)
    parser.add_argument("--perfil", choices=["mt", "bt"], default="mt")
    parser.add_argument("--lote", action="store_true")
    parser.add_argument("--limite-titulares", type=int, default=0)
    parser.add_argument("--offset-titulares", type=int, default=0,
                        help="Pula os primeiros N titulares (para execução paralela)")
    parser.add_argument("--limite-ucs", type=int, default=0)
    parser.add_argument("--forcar-download", action="store_true",
                        help="Ignora pre-filtro master/local e forca download mesmo se ja baixado")
    parser.add_argument("--worker-id", type=int, default=0,
                        help="ID do worker (0=padrão). Cada worker usa pasta temp isolada.")
    parser.add_argument(
        "--fluxo-servico",
        choices=["segunda-via", "pagar-conta"],
        default="segunda-via",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    if args.conta:
        usuario, senha = _CONTAS[args.conta]
    else:
        usuario = args.usuario or USUARIO_PADRAO
        senha = args.senha or SENHA_PADRAO
    raise SystemExit(
        executar(
            usuario,
            senha,
            args.headless,
            args.manter_aberto,
            args.parar_na_segunda_via,
            args.indice_uc_ativa,
            args.fluxo_servico,
            args.perfil,
            args.lote,
            args.limite_titulares,
            args.limite_ucs,
            offset_titulares=args.offset_titulares,
            worker_id=args.worker_id,
            forcar_download=args.forcar_download,
        )
    )
